"""
Dashboard Data Publisher
------------------------------------------------------------------
Consolidates Top20 recommendations + your actual portfolio holdings into
one compact JSON file (dashboard_data.json) - small enough to load fast
on mobile, and everything the index.html needs in one place.

Run this AFTER generate_vlookup_calculator.py and generate_portfolio_tracker.py
(it reads their outputs). Already wired into daily_refresh_all.bat.
"""

import json
import os
import math
import datetime

JSON_FILE = "top20_calculator_data.json"
PORTFOLIO_FILE = "Portfolio_Tracker.xlsx"
REAL_DATA_FILE = "Complete_US_Market_Report.xlsx"
OUTPUT_FILE = "dashboard_data.json"
PUBLIC_OUTPUT_FILE = "public_data.json"


def _safe_float(v):
    try:
        if v in (None, "N/A", "", "nan"):
            return None
        f = float(v)
        # NaN != NaN is always True in Python, so an equality-based check
        # (like the one above) can never catch a real floating-point NaN -
        # explicit isnan/isinf checks are required, or a literal NaN/Infinity
        # token leaks straight into the JSON output, which browsers reject
        # as invalid (only null is valid JSON for "no value", not NaN).
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_str(v, default):
    """Like _safe_float's NaN guard, but for text columns such as
    'KDJ Action'/'KDJ Cross'. pandas Series.get(key, default) only falls
    back to `default` when the COLUMN is missing - a present column with a
    blank/NaN CELL (e.g. a stale row from before KDJ was added, never
    re-run through stock_screener_us.py) comes back as float('nan'), not
    the default. That NaN survives into the JSON as null (see sanitize()
    below), and null matches none of index.html's exact-string KDJ chip
    filters (=== 'Buy Zone', etc.) - so the ticker silently drops out of
    every chip count while still counting toward the ALL_TICKERS total,
    which is why the six KDJ categories didn't sum to 668."""
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return default
    return s


def compute_suggested_entry(current_price, lower_bb):
    """Same formula used in generate_vlookup_calculator.py - the raw
    screener output (Complete_US_Market_Report.xlsx) never has a
    'Suggested Entry Price' column, that's a derived value computed
    downstream. All Tickers needs to compute it here too, rather than
    reading a column that doesn't actually exist in the source file."""
    cp, lb = _safe_float(current_price), _safe_float(lower_bb)
    if cp is None:
        return None
    if lb is None:
        return round(cp * 0.995, 2)
    dist = cp - lb
    return round(min(cp * 0.995, lb + dist * 0.35), 2)


RISK_ATR_MULT = 3.0   # chosen by backtest_stop_loss.py (2026-10-06): loosest
                      # ATR stop that still roughly halves 15%+ losses while
                      # rarely selling a stock that later recovers.


def compute_risk_line(entry, atr):
    """3 x ATR 'risk line' below the suggested entry. A risk guide for
    position sizing, NOT a sell signal: the backtest showed stops cut big
    losses but slightly lower the average return, and cannot protect
    against overnight gaps (e.g. earnings). Returns (line, pct, per_1000)."""
    e, a = _safe_float(entry), _safe_float(atr)
    if e is None or a is None or e <= 0 or a <= 0:
        return None, None, None
    line = e - RISK_ATR_MULT * a
    if line <= 0:
        return None, None, None
    pct = (e - line) / e
    return round(line, 2), round(pct, 4), round(pct * 1000)


PRICE_CACHE_FILE = "winrate_price_cache.pkl"   # built by train_winrate_model.py
CACHE_MAX_AGE_DAYS = 10                        # ignore cache series older than this
_price_cache = None


def fallback_atr(ticker):
    """ATR(14) for a ticker whose report row has no 'ATR (14)' value yet
    (rows written before the screener had ATR, or tickers that resume mode
    skipped). Uses the same Wilder ATR as stock_screener_us.py /
    backtest_stop_loss.py, computed from winrate_price_cache.pkl.
    Returns (atr, atr_pct) or (None, None)."""
    global _price_cache
    if _price_cache is None:
        _price_cache = {}
        if os.path.exists(PRICE_CACHE_FILE):
            try:
                import pickle
                with open(PRICE_CACHE_FILE, "rb") as f:
                    _price_cache = pickle.load(f)
            except Exception as e:
                print(f"[WARN] could not read {PRICE_CACHE_FILE}: {e}")
    df = _price_cache.get(ticker) if ticker else None
    if df is None or len(df) < 20:
        return None, None
    try:
        import pandas as pd
        df = df.dropna(subset=["High", "Low", "Close"])
        last = pd.Timestamp(df.index[-1]).tz_localize(None) if getattr(df.index[-1], "tzinfo", None) else pd.Timestamp(df.index[-1])
        if (pd.Timestamp.now() - last).days > CACHE_MAX_AGE_DAYS:
            return None, None
        c, h, l = df["Close"], df["High"], df["Low"]
        prev = c.shift(1)
        tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
        atr = _safe_float(tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1])
        close = _safe_float(c.iloc[-1])
        if atr is None or not close:
            return None, None
        return round(atr, 4), round(atr / close * 100, 2)
    except Exception as e:
        print(f"[WARN] fallback ATR failed for {ticker}: {e}")
        return None, None


def load_all_tickers():
    """Full ticker universe with ALL the calculator's fields - not trimmed.
    The dashboard shows a compact summary by default and lets you tap a
    card to expand the rest, so the full richness of the calculator is
    actually available on mobile, just organized for a small screen."""
    if not os.path.exists(REAL_DATA_FILE):
        return []
    import pandas as pd
    df = pd.read_excel(REAL_DATA_FILE)
    tickers = []
    n_fallback = 0
    for _, r in df.iterrows():
        entry = compute_suggested_entry(r.get("Current Price"), r.get("Support (Lower BB)"))
        atr14, atr_pct = _safe_float(r.get("ATR (14)")), _safe_float(r.get("ATR %"))
        if atr14 is None:
            atr14, atr_pct = fallback_atr(r.get("Ticker"))
            if atr14 is not None:
                n_fallback += 1
        risk_line, risk_pct, risk_per_1000 = compute_risk_line(entry, atr14)
        tickers.append({
            "ticker": r.get("Ticker"),
            "company": r.get("Name", r.get("Ticker")),
            "category": r.get("Category"),
            "signal": r.get("Signal"),
            "matrix_score": r.get("Matrix Score"),
            "current_price": _safe_float(r.get("Current Price")),
            "suggested_entry": entry,
            # 3 x ATR risk line below the suggested entry (risk guide, not a
            # sell signal) - see compute_risk_line(). None for rows from
            # before ATR was added until the screener re-processes them.
            "atr14": atr14,
            "atr_pct": atr_pct,
            "risk_line": risk_line,
            "risk_pct": risk_pct,
            "risk_per_1000": risk_per_1000,
            "high_52w_price": _safe_float(r.get("52W High Price")),
            "high_52w_drop_pct": r.get("52W High Drop %"),
            "ema20": _safe_float(r.get("EMA20")),
            "ema40": _safe_float(r.get("EMA40")),
            "support_lower_bb": _safe_float(r.get("Support (Lower BB)")),
            "middle_bb": _safe_float(r.get("Middle BB")),
            "resistance_upper_bb": _safe_float(r.get("Resistance (Upper BB)")),
            "vwap": _safe_float(r.get("VWAP (20d)")),
            "vwap_upper2": _safe_float(r.get("VWAP +2SD")),
            "vwap_lower2": _safe_float(r.get("VWAP -2SD")),
            "vwap_sd_distance": _safe_float(r.get("VWAP Distance (SD)")),
            "rsi": _safe_float(r.get("RSI")),
            "macd_line": _safe_float(r.get("MACD Line")),
            "macd_signal": _safe_float(r.get("MACD Signal")),
            "macd_histogram": _safe_float(r.get("MACD Histogram")),
            "macd_trend": r.get("MACD Histogram Trend", "N/A"),
            # KDJ Action - entry/exit TIMING badge, informational only,
            # separate from matrix_score/signal above. See
            # determine_kdj_action() in stock_screener_us.py.
            "kdj_k": _safe_float(r.get("KDJ K")),
            "kdj_d": _safe_float(r.get("KDJ D")),
            "kdj_j": _safe_float(r.get("KDJ J")),
            "kdj_cross": _safe_str(r.get("KDJ Cross"), "NONE"),
            "kdj_action": _safe_str(r.get("KDJ Action"), "Waiting"),
            "current_vol": r.get("Current Vol"),
            "vol_200sma": r.get("Vol 200SMA"),
            "volume_strength": r.get("Volume Driven Strength"),
            "rr_ratio": _safe_float(r.get("R/R Ratio")),
            "rr_note": r.get("R/R Ratio Note"),
            "intrinsic_value": _safe_float(r.get("Intrinsic (Fair) Value")),
            "margin_of_safety": r.get("Margin of Safety"),
            "analyst_target": _safe_float(r.get("Analyst Target Price")),
            "analyst_upside_pct": r.get("Analyst Upside %"),
            "put_call_ratio": _safe_float(r.get("Put/Call OI Ratio")),
            "put_wall_price": _safe_float(r.get("Whale Put Wall Price (Floor)")),
            "put_wall_oi": r.get("Put Wall Open Interest Volume"),
            "call_wall_price": _safe_float(r.get("Whale Call Wall Price (Ceiling)")),
            "call_wall_oi": r.get("Call Wall Open Interest Volume"),
            "walls_crossed": bool(r.get("Walls Crossed (Low Confidence)")),
        })
    with_risk = sum(1 for t in tickers if t["risk_line"] is not None)
    print(f"[INFO] Risk line: {with_risk}/{len(tickers)} tickers "
          f"({n_fallback} ATR values filled from {PRICE_CACHE_FILE})")
    return tickers


def load_top20():
    if not os.path.exists(JSON_FILE):
        return [], False
    with open(JSON_FILE) as f:
        data = json.load(f)
    return data.get("rows", []), data.get("is_real", False)


def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    import openpyxl
    wb = openpyxl.load_workbook(PORTFOLIO_FILE, data_only=True)
    ws = wb["My Portfolio"]
    headers = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
    holdings = []
    for r in range(2, ws.max_row + 1):
        ticker = ws.cell(row=r, column=headers["Ticker"]).value
        if not (ticker and isinstance(ticker, str) and len(ticker) <= 10 and " " not in ticker):
            continue  # skip the note row / blanks, same guard as generate_portfolio_tracker.py
        shares = _safe_float(ws.cell(row=r, column=headers["Shares Held"]).value) or 0
        if shares <= 0:
            continue  # not actually held
        holdings.append({
            "ticker": ticker,
            "company": ws.cell(row=r, column=headers["Company"]).value,
            "shares": shares,
            "avg_cost": _safe_float(ws.cell(row=r, column=headers["Average Cost"]).value),
            "current_price": _safe_float(ws.cell(row=r, column=headers["Current Price"]).value),
            "market_value": _safe_float(ws.cell(row=r, column=headers["Market Value"]).value),
            "gain_dollar": _safe_float(ws.cell(row=r, column=headers["Unrealized Gain/Loss ($)"]).value),
            "gain_pct": _safe_float(ws.cell(row=r, column=headers["Unrealized Gain/Loss (%)"]).value),
            "weight_pct": _safe_float(ws.cell(row=r, column=headers["Portfolio Weight (%)"]).value),
            "suggested_entry": _safe_float(ws.cell(row=r, column=headers["This Month Suggested Entry"]).value),
            "analyst_target": _safe_float(ws.cell(row=r, column=headers["Analyst Target Price"]).value),
            "macd_trend": ws.cell(row=r, column=headers["MACD Histogram Trend"]).value,
        })
    return holdings


def load_options():
    """Open option positions (LEAPs etc.) from the 'My Options' tab that
    generate_portfolio_tracker.py maintains. Private dashboard only - never
    copied into public_data.json. Added 2026-10-02."""
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    import openpyxl
    wb = openpyxl.load_workbook(PORTFOLIO_FILE, data_only=True)
    if "My Options" not in wb.sheetnames:
        return []
    ws = wb["My Options"]
    h = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
    get = lambda r, name: ws.cell(row=r, column=h[name]).value if name in h else None
    out = []
    for r in range(2, ws.max_row + 1):
        und = get(r, "Underlying")
        if not (und and isinstance(und, str) and len(und.strip()) <= 10 and " " not in und.strip()):
            continue
        contracts = _safe_float(get(r, "Contracts")) or 0
        strike = _safe_float(get(r, "Strike"))
        if contracts <= 0 or strike is None:
            continue  # closed, or not filled in properly yet
        exp = get(r, "Expiry")
        if not isinstance(exp, (datetime.date, datetime.datetime)) and exp:
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%b %d %Y", "%b %d, %Y", "%d-%m-%Y", "%Y/%m/%d"):
                try:
                    exp = datetime.datetime.strptime(str(exp).strip(), fmt)
                    break
                except ValueError:
                    pass
        exp_str = exp.strftime("%Y-%m-%d") if isinstance(exp, (datetime.date, datetime.datetime)) else (str(exp) if exp else None)
        cp = str(get(r, "Call/Put") or "").strip().upper()[:1]
        if cp not in ("C", "P") or not exp:
            continue  # row has an input problem - the Price Source column in Excel says what
        out.append({
            "underlying": und.strip().upper(),
            "type": "CALL" if cp == "C" else ("PUT" if cp == "P" else "?"),
            "strike": strike,
            "expiry": exp_str,
            "contracts": contracts,
            "premium_paid": _safe_float(get(r, "Premium Paid (per share)")),
            "option_price": _safe_float(get(r, "Option Price (now)")),
            "price_source": get(r, "Price Source"),
            "cost_basis": _safe_float(get(r, "Cost Basis")),
            "market_value": _safe_float(get(r, "Market Value")),
            "gain_dollar": _safe_float(get(r, "Unrealized P/L ($)")),
            "gain_pct": _safe_float(get(r, "Unrealized P/L (%)")),
            "dte": _safe_float(get(r, "Days to Expiry")),
            "breakeven": _safe_float(get(r, "Breakeven Price")),
            "vs_breakeven_pct": _safe_float(get(r, "Stock vs Breakeven (%)")),
            "stock_price": _safe_float(get(r, "Stock Price")),
            "iv": _safe_float(get(r, "Implied Volatility")),
            "notes": get(r, "Notes"),
        })
    return out


def main():
    top20_rows, is_real = load_top20()
    holdings = load_portfolio()
    options = load_options()
    all_tickers = load_all_tickers()
    held_tickers = {h["ticker"] for h in holdings}

    top20 = []
    for r in top20_rows:
        top20.append({
            "ticker": r["Ticker"],
            "company": r.get("Name", r["Ticker"]),
            "signal": r.get("Signal"),
            "matrix_score": r.get("Matrix Score"),
            "current_price": _safe_float(r.get("Current Price")),
            "suggested_entry": _safe_float(r.get("This Month Entry Price")),
            "analyst_target": _safe_float(r.get("Analyst Target Price")),
            "macd_trend": r.get("MACD Histogram Trend", "N/A"),
            "shares_needed": _safe_float(r.get("Shares Needed (This Month)")),
            "capital_required": _safe_float(r.get("Capital Required (This Month)")),
            "already_held": r["Ticker"] in held_tickers,
        })

    stock_value = sum(h["market_value"] for h in holdings if h["market_value"])
    stock_cost = sum((h["avg_cost"] or 0) * h["shares"] for h in holdings)
    # Options: if a contract had no quote this run, count it at cost rather
    # than as $0 so the headline total doesn't fake a 100% loss.
    options_cost = sum(o["cost_basis"] or 0 for o in options)
    options_value = sum(o["market_value"] if o["market_value"] is not None else (o["cost_basis"] or 0)
                        for o in options)
    total_value = stock_value + options_value
    total_cost = stock_cost + options_cost
    total_gain = total_value - total_cost if total_value else 0

    payload = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "is_real_data": is_real,
        "portfolio": {
            "total_value": round(total_value, 2),
            "total_cost": round(total_cost, 2),
            "total_gain": round(total_gain, 2),
            "total_gain_pct": round(total_gain / total_cost, 4) if total_cost else None,
            "holdings": holdings,
            "stock_value": round(stock_value, 2),
            "options_value": round(options_value, 2),
            "options_cost": round(options_cost, 2),
            "options": options,
        },
        "top20": top20,
        "all_tickers": all_tickers,
    }

    # Safety net: recursively replace any NaN/Infinity anywhere in the
    # payload with null. This is a backstop, not the primary fix - the
    # primary fix is _safe_float() actually catching these at the source -
    # but this guarantees the file can NEVER contain invalid JSON even if a
    # future field gets added without going through _safe_float.
    def sanitize(obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize(v) for v in obj]
        return obj

    payload = sanitize(payload)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"[SUCCESS] Wrote {OUTPUT_FILE} - {len(holdings)} holdings, {len(options)} option positions, {len(top20)} recommended picks, "
          f"{len(all_tickers)} total tickers")

    # --- Public version: genuinely no portfolio data, not just hidden in
    # the UI. Even 'already_held' is stripped from top20, since it would
    # otherwise leak which tickers you hold indirectly. This file is safe
    # to put behind a Cloudflare Access bypass rule and share with anyone -
    # there is nothing personal in it to find, even by viewing raw JSON. ---
    public_top20 = [{k: v for k, v in r.items() if k != "already_held"} for r in payload["top20"]]
    public_payload = {
        "generated_at": payload["generated_at"],
        "is_real_data": payload["is_real_data"],
        "top20": public_top20,
        "all_tickers": payload["all_tickers"],
    }
    with open(PUBLIC_OUTPUT_FILE, "w") as f:
        json.dump(public_payload, f, indent=2)
    print(f"[SUCCESS] Wrote {PUBLIC_OUTPUT_FILE} (no portfolio data) - safe to share publicly")


if __name__ == "__main__":
    main()
