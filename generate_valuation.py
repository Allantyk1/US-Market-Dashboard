"""
Valuation Builder (added 2026-10-15)
------------------------------------------------------------------
Builds valuation_data.json for the "Valuation" dialog on index.html and
tracker.html. Ten fair-value methods per stock plus a blended fair value,
a simple quality grade and a key-metrics grid, in the spirit of the
StockOracle "Intrinsic Value" page, computed from yfinance data.

WHICH STOCKS (about 50, not the whole 680 universe, to keep Yahoo calls low):
  1. Today's Top 20                       (dashboard_data.json  -> top20)
  2. Your Custom Watchlist                (dashboard_data.json  -> category)
  3. Top 20 best performers, past 3 months (pick_tracker_data.json) - the same
     ranking as tracker.html section 1 with its defaults: Top-20 picks,
     3-month window, ranked by % gain from day 1 to the latest close.
  4. Your own extra tickers, one per line in valuation_extra.txt (added
     2026-10-15). They can be outside the 680-stock screener: those have no
     card, so open them by typing the ticker in the dashboard search box.
  ETFs are listed but not valued (no earnings / cash flow to discount).

REFRESH: fundamentals (statements, estimates, 5y monthly prices) are cached
in valuation_cache.json and only re-fetched when older than REFRESH_DAYS
(weekly), or for a stock that is new to the list. Prices, premium/discount
and the P/E-type metrics are recomputed EVERY run from today's close in
dashboard_data.json, so the dialog always matches the cards.
  python generate_valuation.py           normal run (weekly fundamentals)
  python generate_valuation.py --force   re-fetch every stock's fundamentals

Run AFTER generate_dashboard_data.py and generate_pick_tracker.py.
Wired into daily_refresh_all.bat as STEP 10/12.

NOT FINANCIAL ADVICE: every method below depends on assumptions (growth,
discount rate, historical multiples). Treat the spread of methods as a
range, not a price target.
"""

import json
import math
import os
import sys
import time
import datetime
import statistics

import yfinance as yf
import warnings; warnings.filterwarnings("ignore", message=".*inplace keyword.*")  # yfinance re-enables pandas inplace notices on import

DASHBOARD_FILE = "dashboard_data.json"
PICK_FILE = "pick_tracker_data.json"
CACHE_FILE = "valuation_cache.json"
OUTPUT_FILE = "valuation_data.json"
EXTRA_FILE = "valuation_extra.txt"
EXTRA_TEMPLATE = """# Extra tickers for the Valuation dialog - one per line, e.g. NVDA
# Lines starting with # are ignored. Tickers outside the screener have no
# card: type the ticker in the dashboard search box to open its valuation.
# New tickers are fetched on the next run of generate_valuation.py.
"""

REFRESH_DAYS = 7          # fundamentals re-fetched weekly
BEST_N = 20               # top N best performers from the tracker
BEST_MONTHS = 3           # tracker pick window
CALL_DELAY = 0.5          # seconds between Yahoo calls (same as screener)
COOLDOWN_EVERY = 50       # tickers
COOLDOWN_SECS = 8

# ---- valuation assumptions (shown in the dialog's "Assumptions" note) ----
GROWTH_CAP = 0.30         # years 1-5 growth capped at 30%/yr
GROWTH_FLOOR = 0.0        # negative growth is treated as 0%
LONG_TERM_GROWTH = 0.04   # years 11-20 (never above the years 6-10 rate)
TERMINAL_GROWTH = 0.03    # perpetual growth for the DFCF-Terminal method
RISK_FREE = 0.0425        # discount rate = risk-free + beta x equity premium
EQUITY_PREMIUM = 0.05
DISCOUNT_MIN, DISCOUNT_MAX = 0.07, 0.12
PEG_FAIR = 1.0            # Peter Lynch rule: fair P/E = growth %
PSG_FAIR = 0.2            # fair P/S = 0.2 x revenue growth %
GROWTH_PCT_CAP = 50       # PEG / PSG growth % capped at 50
MAX_MULTIPLE = {"pe": 150, "ps": 60, "pb": 60}   # drop outlier years

METHODS = [
    ("dcf20",  "Discounted Cash Flow 20-yr",       "DCF-20"),
    ("dfcf20", "Discounted Free Cash Flow 20-yr",  "DFCF-20"),
    ("dni20",  "Discounted Net Income 20-yr",      "DNI-20"),
    ("dfcft",  "Discounted FCF + Terminal",        "DFCF-Terminal"),
    ("ps",     "Mean Price to Sales",              "P/S"),
    ("pe",     "Mean Price to Earnings",           "P/E"),
    ("pb",     "Mean Price to Book",               "P/B"),
    ("psg",    "Price to Sales Growth",            "PSG"),
    ("peg",    "Price to Earnings Growth",         "PEG"),
]


# ---------------------------------------------------------------- helpers
def num(v):
    """float or None - catches None, 'N/A', NaN and Infinity (a bare NaN
    in the JSON breaks the browser's JSON.parse, see ways-of-working)."""
    try:
        if v is None:
            return None
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def sanitize(o):
    if isinstance(o, dict):
        return {k: sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [sanitize(v) for v in o]
    if isinstance(o, float):
        return num(o)
    return o


def r2(v, d=2):
    v = num(v)
    return None if v is None else round(v, d)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def months_back(date_str, months):
    """Same as tracker.html cutoff(): calendar months back from a date."""
    d = datetime.date.fromisoformat(date_str)
    m = d.month - months
    y = d.year
    while m <= 0:
        m += 12
        y -= 1
    day = d.day
    while True:
        try:
            return datetime.date(y, m, day).isoformat()
        except ValueError:
            day -= 1  # e.g. 31 Jul -> 30 Apr


# ---------------------------------------------------------------- universe
def best_performers(pick, n=BEST_N, months=BEST_MONTHS):
    """tracker.html section 1 (Top-20 picks, 3-month window, % to date):
    each stock's FIRST Top-20 entry in the window is day 1; gain is from
    that day's close to the latest close."""
    cut = months_back(pick["as_of_session"], months)
    first = {}
    for e in pick.get("events", []):
        if e.get("src") != "top20" or e.get("d", "") < cut:
            continue
        t = e["t"]
        if t not in first or e["d"] < first[t]["d"]:
            first[t] = e
    rows = []
    for t, e in first.items():
        rp, lc = num(e.get("rp")), num(e.get("lc"))
        if rp and lc is not None and rp > 0:
            rows.append({"t": t, "pct": round(lc / rp - 1, 4), "d": e["d"],
                         "rp": rp, "lc": lc})
    rows.sort(key=lambda r: -r["pct"])
    for i, r in enumerate(rows[:n]):
        r["rank"] = i + 1
    return rows[:n], cut


def read_extra():
    """valuation_extra.txt -> list of tickers. Creates a template on first run."""
    if not os.path.exists(EXTRA_FILE):
        with open(EXTRA_FILE, "w", encoding="utf-8") as fh:
            fh.write(EXTRA_TEMPLATE)
        return []
    out = []
    with open(EXTRA_FILE, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            t = line.split("#", 1)[0].strip().upper().replace(".", "-")  # BRK.B -> BRK-B (Yahoo)
            if t and t not in out:
                out.append(t)
    return out


def fresh_price(symbol):
    """Latest close for a ticker the screener doesn't cover (so it isn't in
    dashboard_data.json). One cheap call per run, only for those tickers."""
    try:
        h = yf.Ticker(symbol).history(period="5d", interval="1d")
        time.sleep(CALL_DELAY)
        closes = [num(c) for c in h["Close"]]
        closes = [c for c in closes if c]
        return closes[-1] if closes else None
    except Exception:
        return None


# ---------------------------------------------------------------- fetch
def _row(df, *names):
    """Annual statement row as a list of (date, value), newest first."""
    if df is None or getattr(df, "empty", True):
        return []
    for nm in names:
        if nm in df.index:
            out = []
            for col, v in df.loc[nm].items():
                v = num(v)
                if v is not None:
                    out.append((str(col)[:10], v))
            if out:
                return out
    return []


def _est(df, period, col):
    try:
        if df is None or df.empty or period not in df.index or col not in df.columns:
            return None
        return num(df.loc[period, col])
    except Exception:
        return None


def fetch_fundamentals(symbol):
    """All Yahoo calls for one stock. Returns a compact JSON-able dict."""
    tk = yf.Ticker(symbol)
    info = tk.info or {}
    time.sleep(CALL_DELAY)
    if info.get("quoteType") == "ETF":
        return {"etf": True, "co": info.get("shortName")}

    def safe(attr):
        try:
            v = getattr(tk, attr)
            time.sleep(CALL_DELAY)
            return v
        except Exception:
            return None

    inc = safe("income_stmt")
    cf = safe("cashflow")
    bs = safe("balance_sheet")
    ge = safe("growth_estimates")
    re_ = safe("revenue_estimate")
    try:
        hist = tk.history(period="6y", interval="1mo", auto_adjust=False)
        time.sleep(CALL_DELAY)
        monthly = [(str(i)[:10], num(c)) for i, c in hist["Close"].items() if num(c)]
    except Exception:
        monthly = []

    return {
        "etf": False,
        "co": info.get("shortName") or info.get("longName"),
        # e.g. SK Hynix ADR: statements in KRW, price in USD -> not comparable
        "cur": info.get("currency"), "fin_cur": info.get("financialCurrency"),
        "info": {k: num(info.get(k)) for k in (
            "currentPrice", "trailingEps", "forwardEps", "bookValue", "sharesOutstanding",
            "totalRevenue", "netIncomeToCommon", "operatingCashflow", "freeCashflow",
            "totalCash", "totalDebt", "beta", "returnOnEquity", "grossMargins",
            "debtToEquity", "dividendRate", "earningsGrowth", "revenueGrowth",
            "impliedSharesOutstanding")},
        "annual": {
            "revenue": _row(inc, "Total Revenue"),
            "net_income": _row(inc, "Net Income Common Stockholders", "Net Income"),
            "eps": _row(inc, "Diluted EPS", "Basic EPS"),
            "shares": _row(inc, "Diluted Average Shares", "Basic Average Shares"),
            "ebit": _row(inc, "EBIT", "Operating Income"),
            "tax_rate": _row(inc, "Tax Rate For Calcs"),
            "ocf": _row(cf, "Operating Cash Flow"),
            "fcf": _row(cf, "Free Cash Flow"),
            "equity": _row(bs, "Common Stock Equity", "Stockholders Equity"),
            "invested_capital": _row(bs, "Invested Capital"),
        },
        "est": {
            "eps_5y": _est(ge, "+5y", "stockTrend") if _est(ge, "+5y", "stockTrend") is not None else _est(ge, "LTG", "stockTrend"),
            "eps_1y": _est(ge, "+1y", "stockTrend"),
            "eps_0y": _est(ge, "0y", "stockTrend"),
            "rev_1y": _est(re_, "+1y", "growth"),
            "rev_0y": _est(re_, "0y", "growth"),
        },
        "monthly": monthly,
    }


# ---------------------------------------------------------------- valuation
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def growth_inputs(f):
    """Years 1-5 growth (g1) and where it came from."""
    e, i = f["est"], f["info"]
    if e.get("eps_5y") is not None:
        g, src = e["eps_5y"], "analyst 5-yr EPS growth"
    elif e.get("eps_0y") is not None and e.get("eps_1y") is not None:
        g, src = (e["eps_0y"] + e["eps_1y"]) / 2, "analyst EPS growth, this + next year"
    elif e.get("eps_1y") is not None:
        g, src = e["eps_1y"], "analyst EPS growth, next year"
    else:
        rev = [v for _, v in f["annual"]["revenue"]]
        if len(rev) >= 3 and rev[-1] > 0 and rev[0] > 0:
            yrs = len(rev) - 1
            g, src = (rev[0] / rev[-1]) ** (1 / yrs) - 1, f"{yrs}-yr revenue CAGR"
        elif i.get("revenueGrowth") is not None:
            g, src = i["revenueGrowth"], "revenue growth (latest quarter YoY)"
        else:
            return None, None, "no growth data"
    raw = g
    g1 = clamp(g, GROWTH_FLOOR, GROWTH_CAP)
    return g1, raw, src


def discount_rate(beta):
    b = beta if beta is not None and beta > 0 else 1.0
    return clamp(RISK_FREE + b * EQUITY_PREMIUM, DISCOUNT_MIN, DISCOUNT_MAX)


def pv_20yr(base, g1, g2, g3, r):
    """20 years: 1-5 at g1, 6-10 at g2, 11-20 at g3, discounted at r."""
    total, cf = 0.0, base
    for yr in range(1, 21):
        g = g1 if yr <= 5 else g2 if yr <= 10 else g3
        cf *= 1 + g
        total += cf / (1 + r) ** yr
    return total


def pv_terminal(base, g1, g2, r, tg=TERMINAL_GROWTH):
    """10 years (1-5 g1, 6-10 g2) + Gordon terminal value at year 10."""
    total, cf = 0.0, base
    for yr in range(1, 11):
        cf *= 1 + (g1 if yr <= 5 else g2)
        total += cf / (1 + r) ** yr
    tv = cf * (1 + tg) / (r - tg)
    return total + tv / (1 + r) ** 10


def price_at(monthly, date_str):
    """Last monthly close on or before a fiscal year-end date."""
    best = None
    for d, c in monthly:
        if d <= date_str:
            best = c
    return best


def mean_multiple(f, metric_key, kind):
    """Average historical multiple over the annual statements Yahoo has
    (usually 4 fiscal years): price at year end / per-share metric."""
    shares = dict(f["annual"]["shares"])
    vals = []
    for d, v in f["annual"][metric_key]:
        sh = shares.get(d) if kind != "pe" else 1
        if kind != "pe" and not sh:
            continue
        per_share = v if kind == "pe" else v / sh
        px = price_at(f["monthly"], d)
        if not px or per_share <= 0:
            continue
        m = px / per_share
        if 0 < m <= MAX_MULTIPLE[kind]:
            vals.append(m)
    return (sum(vals) / len(vals), len(vals)) if len(vals) >= 2 else (None, len(vals))


def latest(series):
    return series[0][1] if series else None


def value_stock(f, price):
    i, a = f["info"], f["annual"]
    shares = i.get("sharesOutstanding") or i.get("impliedSharesOutstanding") or latest(a["shares"])
    methods, notes = {}, {}
    g1, g_raw, g_src = growth_inputs(f)
    beta = i.get("beta")
    r = discount_rate(beta)
    g2 = g1 / 2 if g1 is not None else None
    g3 = min(LONG_TERM_GROWTH, g2) if g2 is not None else None
    net_cash = (i.get("totalCash") or 0) - (i.get("totalDebt") or 0)

    # -- 1-4: discounted cash-flow family (TTM base, + cash - debt) --
    bases = {
        "dcf20": (i.get("operatingCashflow") or latest(a["ocf"]), "operating cash flow (TTM)"),
        "dfcf20": (i.get("freeCashflow") or latest(a["fcf"]), "free cash flow (TTM)"),
        "dni20": (i.get("netIncomeToCommon") or latest(a["net_income"]), "net income (TTM)"),
        "dfcft": (i.get("freeCashflow") or latest(a["fcf"]), "free cash flow (TTM)"),
    }
    for k, (base, label) in bases.items():
        if g1 is None or not shares:
            notes[k] = "no growth estimate" if g1 is None else "no share count"
            continue
        if not base or base <= 0:
            notes[k] = f"{label} is negative or missing"
            continue
        pv = pv_terminal(base, g1, g2, r) if k == "dfcft" else pv_20yr(base, g1, g2, g3, r)
        methods[k] = (pv + net_cash) / shares
        notes[k] = f"{label}, plus cash minus debt"

    # -- 5-7: mean historical multiples x today's per-share figure --
    ttm_eps = i.get("trailingEps")
    sales_ps = (i.get("totalRevenue") / shares) if i.get("totalRevenue") and shares else None
    bvps = i.get("bookValue")
    for k, metric, kind, current, label in (
        ("ps", "revenue", "ps", sales_ps, "sales per share"),
        ("pe", "eps", "pe", ttm_eps, "EPS (TTM)"),
        ("pb", "equity", "pb", bvps, "book value per share"),
    ):
        m, n = mean_multiple(f, metric, kind)
        if m is None:
            notes[k] = f"only {n} usable year(s) of history"
        elif not current or current <= 0:
            notes[k] = f"{label} is negative or missing"
        else:
            methods[k] = m * current
            notes[k] = f"{n}-yr average {kind.upper()} {m:.1f}x x {label}"

    # -- 8: PSG (revenue growth) --
    rev_g = f["est"].get("rev_1y")
    if rev_g is None:
        rev_g = f["est"].get("rev_0y")
    if rev_g is None:
        rev_g = i.get("revenueGrowth")
    if sales_ps and rev_g and rev_g > 0:
        gp = min(rev_g * 100, GROWTH_PCT_CAP)
        methods["psg"] = sales_ps * gp * PSG_FAIR
        notes["psg"] = f"fair P/S = {PSG_FAIR} x {gp:.0f}% revenue growth"
    else:
        notes["psg"] = "no positive revenue growth estimate"

    # -- 9: PEG = 1 --
    if ttm_eps and ttm_eps > 0 and g_raw and g_raw > 0:
        gp = min(g_raw * 100, GROWTH_PCT_CAP)
        methods["peg"] = ttm_eps * gp * PEG_FAIR
        notes["peg"] = f"fair P/E = {gp:.0f} (PEG {PEG_FAIR:g} x growth %)"
    else:
        notes["peg"] = "EPS or growth is negative or missing"

    vals = [v for v in methods.values() if num(v) and v > 0]
    fair = statistics.median(vals) if vals else None

    out_methods = []
    for k, label, short in METHODS:
        v = num(methods.get(k))
        out_methods.append({
            "k": k, "label": label, "short": short,
            "value": r2(v) if v and v > 0 else None,
            "prem": r2((price - v) / v, 4) if v and v > 0 and price else None,
            "note": notes.get(k, ""),
        })

    # -- quality grade (a simple 5-point check, not a moat rating) --
    roe = i.get("returnOnEquity")
    ebit, tax, ic = latest(a["ebit"]), latest(a["tax_rate"]), latest(a["invested_capital"])
    roic = ebit * (1 - (tax if tax is not None else 0.21)) / ic if ebit and ic and ic > 0 else None
    fcf_hist = [v for _, v in a["fcf"]][:4]
    de = i.get("debtToEquity")
    gm = i.get("grossMargins")
    checks = [
        ("ROE above 15%", roe is not None and roe > 0.15),
        ("ROIC above 12%", roic is not None and roic > 0.12),
        ("Gross margin above 40%", gm is not None and gm > 0.40),
        ("Positive free cash flow every year", len(fcf_hist) >= 3 and all(v > 0 for v in fcf_hist)),
        ("Debt below equity", de is not None and de < 100),
    ]
    score = sum(1 for _, ok in checks if ok)
    grade = "Strong" if score >= 4 else "Moderate" if score >= 2 else "Weak"

    fwd_eps = i.get("forwardEps")
    pe = price / ttm_eps if price and ttm_eps and ttm_eps > 0 else None
    fcf_ttm = i.get("freeCashflow") or latest(a["fcf"])
    metrics = {
        "pe": r2(pe),
        "fwd_pe": r2(price / fwd_eps) if price and fwd_eps and fwd_eps > 0 else None,
        "peg": r2(pe / (g_raw * 100)) if pe and g_raw and g_raw > 0 else None,
        "roe": r2(roe, 4),
        "roic": r2(roic, 4),
        "fcf_yield": r2(fcf_ttm / (shares * price), 4) if fcf_ttm and shares and price else None,
        "eps_growth_1y": r2(f["est"].get("eps_1y"), 4),
        "eps_growth_5y": r2(f["est"].get("eps_5y"), 4),
        "div_yield": r2((i.get("dividendRate") or 0) / price, 4) if price else None,
        "shares": r2(latest(a["shares"]) or shares, 0),
        "gross_margin": r2(gm, 4),
    }

    return {
        "fair": r2(fair),
        "n_methods": len(vals),
        # StockOracle convention: % undervalued = (fair - price) / fair
        "mos": r2((fair - price) / fair, 4) if fair and price else None,
        "methods": out_methods,
        "quality": {"grade": grade, "score": score,
                    "checks": [{"label": l, "ok": ok} for l, ok in checks]},
        "metrics": metrics,
        "assume": {"g1": r2(g1, 4), "g1_raw": r2(g_raw, 4), "g1_src": g_src,
                   "g2": r2(g2, 4), "g3": r2(g3, 4), "disc": r2(r, 4),
                   "beta": r2(beta), "net_cash_ps": r2(net_cash / shares) if shares else None},
    }


# ---------------------------------------------------------------- main
def main():
    force = "--force" in sys.argv
    # Explicit pre-checks: a missing input must fail loudly, not quietly
    # publish an empty file (the git-add silent-failure lesson).
    if not os.path.exists(DASHBOARD_FILE):
        print(f"[ERROR] {DASHBOARD_FILE} not found - run generate_dashboard_data.py first.")
        sys.exit(1)
    dash = load_json(DASHBOARD_FILE)
    all_t = {t["ticker"]: t for t in dash.get("all_tickers", [])}
    top20 = [r["ticker"] for r in dash.get("top20", [])]
    watch = [t for t, r in all_t.items() if r.get("category") == "Custom Watchlist"]

    best, cut = [], None
    if os.path.exists(PICK_FILE):
        best, cut = best_performers(load_json(PICK_FILE))
    else:
        print(f"[WARN] {PICK_FILE} not found - the 3-month best list is skipped this run.")

    extra = read_extra()
    universe = list(dict.fromkeys(top20 + watch + [b["t"] for b in best] + extra))
    print(f"Valuation list: {len(universe)} stocks "
          f"(Top 20: {len(top20)}, watchlist: {len(watch)}, 3M best: {len(best)}, "
          f"your extras: {len(extra)} from {EXTRA_FILE})")

    cache = load_json(CACHE_FILE) if os.path.exists(CACHE_FILE) else {}
    today = datetime.date.today()
    fetched = failed = 0
    for n, sym in enumerate(universe):
        c = cache.get(sym)
        age = (today - datetime.date.fromisoformat(c["fetched"])).days if c else None
        is_etf = all_t.get(sym, {}).get("category") == "Major ETF"
        if is_etf and not c:
            cache[sym] = {"fetched": today.isoformat(), "fund": {"etf": True}}
            continue
        # cache entries from before the currency check (2026-10-15) are refetched once
        old_format = c and not c["fund"].get("etf") and "cur" not in c["fund"]
        if not force and c and age is not None and age < REFRESH_DAYS and not old_format:
            continue
        try:
            print(f"  fetching {sym} ({n + 1}/{len(universe)})")
            cache[sym] = {"fetched": today.isoformat(), "fund": fetch_fundamentals(sym)}
            fetched += 1
        except Exception as e:
            failed += 1
            print(f"  [WARN] {sym}: {e} - keeping the previous cached data if any")
        if fetched and fetched % COOLDOWN_EVERY == 0:
            time.sleep(COOLDOWN_SECS)

    with open(CACHE_FILE, "w", encoding="utf-8") as fh:
        json.dump(sanitize(cache), fh)

    best_map = {b["t"]: b for b in best}
    out = {}
    for sym in universe:
        rec = all_t.get(sym, {})
        c = cache.get(sym)
        lists = [k for k, ok in (("top20", sym in top20), ("watchlist", sym in watch),
                                 ("best3m", sym in best_map), ("extra", sym in extra)) if ok]
        base = {"co": rec.get("company"), "lists": lists,
                "best3m": best_map.get(sym)}
        if not c:
            out[sym] = {**base, "error": "Yahoo data not available yet"}
            continue
        f = c["fund"]
        price = num(rec.get("current_price"))
        if price is None and not f.get("etf"):
            price = fresh_price(sym)  # not in the screener: get today's close
        if price is None:
            price = num((f.get("info") or {}).get("currentPrice"))
        price = r2(price)
        base["in_screener"] = sym in all_t
        base["co"] = base["co"] or f.get("co")
        base.update({"price": price, "fetched": c["fetched"]})
        if f.get("etf"):
            out[sym] = {**base, "etf": True}
            continue
        if f.get("fin_cur") and f.get("cur") and f["fin_cur"] != f["cur"]:
            out[sym] = {**base, "error": f"Its accounts are reported in {f['fin_cur']} while the price is in {f['cur']}, "
                                         f"so the methods can't be compared with the price. Skipped"}
            continue
        try:
            out[sym] = {**base, **value_stock(f, price)}
            fv = out[sym].get("fair")
            if fv and price and (fv / price > 10 or fv / price < 0.1):
                out[sym]["warn"] = ("Fair value is more than 10x away from the price. That usually means a reverse "
                                    "split, a currency or share-count mismatch, or a penny stock. Check before relying on it.")
        except Exception as e:
            out[sym] = {**base, "error": f"could not value: {e}"}

    data = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "price_date": dash.get("generated_at"),
        "lists": {"top20": top20, "watchlist": watch, "best3m": best, "extra": extra,
                  "best3m_from": cut, "best3m_months": BEST_MONTHS},
        "assumptions": {
            "growth_cap": GROWTH_CAP, "long_term_growth": LONG_TERM_GROWTH,
            "terminal_growth": TERMINAL_GROWTH, "risk_free": RISK_FREE,
            "equity_premium": EQUITY_PREMIUM, "discount_min": DISCOUNT_MIN,
            "discount_max": DISCOUNT_MAX, "peg_fair": PEG_FAIR, "psg_fair": PSG_FAIR,
            "refresh_days": REFRESH_DAYS,
        },
        "tickers": out,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(sanitize(data), fh, separators=(",", ":"), allow_nan=False)
    valued = sum(1 for v in out.values() if v.get("fair"))
    print(f"[SUCCESS] {OUTPUT_FILE}: {valued}/{len(out)} stocks valued "
          f"({fetched} re-fetched, {failed} failed, rest from the weekly cache)")


if __name__ == "__main__":
    main()
