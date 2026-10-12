"""
backtest_box_momentum.py
========================
Answers: "After a run of GREEN boxes, how many WHITE boxes in a row signal real
upward momentum?"  -- using years of daily prices, not just the tracker's 45 sessions.

Box rule (same idea as tracker.html section 2):
    weekly avg  = average of the previous 5 closes (today excluded)
    GREEN box   = close <  weekly avg   (stock closed below its weekly average)
    WHITE box   = close >= weekly avg
  NOTE: the dashboard uses the 5-session average of the daily *Suggested Entry*.
  That history only exists since the tracker started logging, so this script uses
  the 5-session average *close* as a stand-in. Same pattern, longer history.

What it measures
  For every stock and every day, it reads the newest run of boxes:
    * "k white after g green"  -> the k-th white day in a row, right after g+ green days
    * "green now"              -> still closing below the weekly average (the dip-buy case)
    * "Discount"               -> more green than white boxes in the last 20 sessions
  Then it looks forward 5, 10 and 20 sessions from that day's close and records
  hit rate (% up), average and median return, and worst drop.
  Every result is compared with the BASELINE = all days of the same stocks.

Outputs (written next to this script)
  box_momentum_report.md    readable report with the suggested Rebound range
  box_momentum_report.json  same numbers, machine-readable
  box_momentum_prices.pkl   price cache, so re-runs don't re-download

Usage (from the US_Market_Screener_Package folder)
  python backtest_box_momentum.py                       # tickers from pick_tracker_data.json, 3 years
  python backtest_box_momentum.py --years 5             # longer history
  python backtest_box_momentum.py --tickers AAPL MSFT NVDA
  python backtest_box_momentum.py --min-green 3         # require 3+ green days before the white run
  python backtest_box_momentum.py --refresh             # ignore the cache and re-download
  python backtest_box_momentum.py --from-history        # quick offline test on history/*.json (~90 days only)

Not investment advice. Past patterns do not guarantee future results.
"""
import argparse, glob, json, math, os, pickle, sys, time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
HORIZONS = (5, 10, 20)          # sessions forward = 1, 2, 4 weeks
MAX_K = 10                      # white-day counts reported individually (10 = "10+")
MIN_N = 100                     # fewest events before a bucket is trusted
MIN_STOCKS = 20                 # fewest different stocks before a bucket is trusted


# ----------------------------------------------------------------------------- data
def load_tickers(args):
    if args.tickers:
        return sorted({t.upper() for t in args.tickers})
    p = os.path.join(HERE, "pick_tracker_data.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        ts = sorted(d.get("watch") or d.get("tickers") or {})
        if ts:
            return ts
    sys.exit("No tickers: pass --tickers or run from the folder that has pick_tracker_data.json")


def download_prices(tickers, years, refresh):
    """Daily closes, one column per ticker. Cached; resumes if interrupted."""
    import yfinance as yf
    cache = os.path.join(HERE, "box_momentum_prices.pkl")
    store = {}
    if os.path.exists(cache) and not refresh:
        with open(cache, "rb") as f:
            store = pickle.load(f)
        if store.get("years") != years:
            store = {}
    closes = store.get("closes", {})
    todo = [t for t in tickers if t not in closes]
    print(f"{len(tickers)} tickers, {len(closes)} cached, {len(todo)} to download ({years}y each)")
    failed = []
    for i, t in enumerate(todo, 1):
        try:
            h = yf.Ticker(t.replace(".", "-")).history(period=f"{years}y", auto_adjust=True, timeout=15)
            s = h["Close"].dropna() if h is not None and not h.empty else None
            if s is not None and len(s) > 60:
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                closes[t] = s
            else:
                failed.append(t)
        except Exception as e:                      # keep going; report at the end
            failed.append(t)
            print(f"  {t}: {e}")
        time.sleep(0.5)                             # same pacing as stock_screener_us.py
        if i % 50 == 0:
            print(f"  {i}/{len(todo)} downloaded; cooling down 8s")
            with open(cache, "wb") as f:
                pickle.dump({"years": years, "closes": closes}, f)
            time.sleep(8)
    with open(cache, "wb") as f:
        pickle.dump({"years": years, "closes": closes}, f)
    if failed:
        print(f"  no data for {len(failed)}: {' '.join(failed[:30])}{' ...' if len(failed) > 30 else ''}")
    return {t: closes[t] for t in tickers if t in closes}


def prices_from_history():
    """Offline smoke test: ~90 sessions per ticker from history/*.json (no year in labels)."""
    out = {}
    for p in glob.glob(os.path.join(HERE, "history", "*.json")):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        px = [x for x in (d.get("price") or []) if x is not None]
        if len(px) > 40:
            out[d.get("ticker") or os.path.basename(p)[:-5]] = pd.Series(px, index=pd.RangeIndex(len(px)), dtype=float)
    return out


# ----------------------------------------------------------------------------- events
def scan(close: pd.Series, min_green: int):
    """Yield (state, day_index, year) for one stock, plus forward returns."""
    c = close.to_numpy(dtype=float)
    n = len(c)
    avg = pd.Series(c).rolling(5).mean().shift(1).to_numpy()      # previous 5 closes
    green = np.where(np.isnan(avg), np.nan, (c < avg).astype(float))
    fwd = {h: np.full(n, np.nan) for h in HORIZONS}
    for h in HORIZONS:
        fwd[h][: n - h] = c[h:] / c[: n - h] - 1
    # worst close within the next 20 sessions, vs today's close
    worst = np.full(n, np.nan)
    if n > 20:
        win = np.lib.stride_tricks.sliding_window_view(c[1:], 20)   # rows: c[i+1 .. i+20]
        worst[: len(win)] = win.min(axis=1) / c[: len(win)] - 1
    years = (close.index.year.to_numpy() if hasattr(close.index, "year") else np.zeros(n, int))

    wrun = grun = 0          # current white run, and the green run right before it
    gnow = 0                 # current green run
    last20 = []
    for i in range(n):
        g = green[i]
        if np.isnan(g):
            wrun = grun = gnow = 0
            last20 = []
            continue
        if g == 1:
            gnow += 1
            wrun = 0
        else:
            if wrun == 0:            # first white day: remember the green run it ended
                grun = gnow
            wrun += 1
            gnow = 0
        last20 = (last20 + [g])[-20:]
        if np.isnan(fwd[HORIZONS[0]][i]):
            continue
        states = ["ALL"]
        if g == 1:
            states.append("green now")
        elif grun >= min_green:
            states.append(f"white{min(wrun, MAX_K)}")
        else:
            states.append("white, no green run before")
        if len(last20) == 20 and sum(last20) > 10:
            states.append("Discount (green > white, 20d)")
        yield states, i, int(years[i]), {h: fwd[h][i] for h in HORIZONS}, worst[i]


def run(prices, min_green):
    rows = []
    for t, s in prices.items():
        s = s[~s.index.duplicated()].sort_index() if hasattr(s.index, "duplicated") else s
        for states, i, yr, f, w in scan(s, min_green):
            for st in states:
                rows.append((st, t, yr, f[5], f[10], f[20], w))
    df = pd.DataFrame(rows, columns=["state", "t", "year", "r5", "r10", "r20", "worst20"])
    return df


def summarise(df):
    base = df[df.state == "ALL"]
    out = {}
    for st, g in df.groupby("state"):
        d = {"n": int(len(g)), "tickers": int(g.t.nunique())}
        for h in HORIZONS:
            r = g[f"r{h}"].dropna()
            b = base[f"r{h}"].dropna()
            if len(r) == 0:
                continue
            excess = r.mean() - b.mean()
            # Stock-by-stock test: each stock's own excess (pattern days vs that stock's normal days),
            # then a t-stat across stocks. Overlapping days of one stock can't fake a signal this way.
            per = (g.groupby("t")[f"r{h}"].mean() - base.groupby("t")[f"r{h}"].mean()).dropna()
            se = per.std(ddof=1) / math.sqrt(len(per)) if len(per) >= MIN_STOCKS else float("nan")
            d[f"{h}d"] = {
                "n": int(len(r)),
                "hit": float((r > 0).mean()),
                "hit_base": float((b > 0).mean()),
                "avg": float(r.mean()),
                "median": float(r.median()),
                "excess_avg": float(excess),
                "t": float(per.mean() / se) if se and se > 0 else None,
                "stocks_beating_own_baseline": float((per > 0).mean()) if len(per) else None,
            }
        wd = g.worst20.dropna()
        d["avg_worst_20d"] = float(wd.mean()) if len(wd) else None
        # stability: excess 10d return by calendar year
        yr = {}
        for y, gy in g.groupby("year"):
            by = base[base.year == y]
            if len(gy) >= 30 and len(by):
                yr[str(y)] = float(gy.r10.mean() - by.r10.mean())
        d["excess_10d_by_year"] = yr
        out[st] = d
    return out


def suggest_range(summ):
    """Shortest-to-longest white counts that beat the baseline at 10d AND 20d, with enough events."""
    good = []
    for k in range(1, MAX_K):      # MAX_K is the open-ended 'N+' bucket, not a rebound count
        s = summ.get(f"white{k}")
        if not s or s.get("10d", {}).get("n", 0) < MIN_N:
            continue
        a, b = s.get("10d", {}), s.get("20d", {})
        years = list(s.get("excess_10d_by_year", {}).values())
        stable = (sum(v > 0 for v in years) >= max(1, math.ceil(len(years) * 0.6))) if years else True
        # strict on purpose: 9 white counts x 3 horizons are tested, so a single t of 2 can be luck
        if (a.get("excess_avg", 0) > 0 and b.get("excess_avg", 0) > 0 and (a.get("t") or 0) >= 2.5
                and (b.get("t") or 0) >= 2 and (a.get("stocks_beating_own_baseline") or 0) >= 0.55 and stable):
            good.append(k)
    if not good:
        return None
    # longest run of consecutive good k's, starting from the smallest
    best, cur = [good[0]], [good[0]]
    for k in good[1:]:
        cur = cur + [k] if k == cur[-1] + 1 else [k]
        if len(cur) > len(best):
            best = cur
    return {"min_white": best[0], "max_white": best[-1], "all_good": good}


# ----------------------------------------------------------------------------- report
def pct(x, d=1):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x*100:+.{d}f}%"


def write_report(summ, sug, meta):
    order = ["ALL", "green now", "Discount (green > white, 20d)"] + [f"white{k}" for k in range(1, MAX_K + 1)] + ["white, no green run before"]
    label = lambda s: (f"{s[5:]}{'+' if s == f'white{MAX_K}' else ''} white after {meta['min_green']}+ green" if s.startswith("white") and s[5:].isdigit() else s)
    L = [f"# Box momentum backtest", "",
         f"Run {meta['run_at']} · {meta['tickers']} stocks · {meta['source']} · weekly avg = previous 5 closes · "
         f"white run counted after {meta['min_green']}+ green days.", "",
         "**Hit** = share of cases up after N sessions (baseline in brackets). **Excess** = average return minus the "
         "baseline average (all days of the same stocks). **t** is tested stock by stock (each stock vs its own "
         f"normal days); above 2 means the edge shows up across many stocks and is unlikely to be luck. Buckets with "
         f"fewer than {MIN_N} cases or {MIN_STOCKS} stocks are not trusted.", ""]
    if sug:
        L += [f"## Suggested Rebound range: **{sug['min_white']} to {sug['max_white']} white days** after {meta['min_green']}+ green",
              "", f"White counts that beat the baseline at both 10 and 20 sessions, with stock-by-stock t ≥ 2.5 at 10 sessions and ≥ 2 at 20, at least 55% of stocks beating their own normal days, at least {MIN_N} cases and a "
              f"positive excess in most years: {', '.join(map(str, sug['all_good']))}.", ""]
    else:
        L += ["## Suggested Rebound range: **none found**", "",
              "No white-day count beat the baseline reliably at both 10 and 20 sessions. Treat the Rebound tag as unproven.", ""]
    hdr = "| Pattern on the day | Cases | " + " | ".join(f"{h}d hit (base) | {h}d excess | t" for h in HORIZONS) + " | Avg worst drop, next 20d |"
    L += ["## Results", "", hdr, "|" + "---|" * (3 + 3 * len(HORIZONS)) ]
    for st in order:
        s = summ.get(st)
        if not s:
            continue
        cells = []
        for h in HORIZONS:
            x = s.get(f"{h}d")
            cells += ["–", "–", "–"] if not x else [f"{x['hit']*100:.0f}% ({x['hit_base']*100:.0f}%)", pct(x["excess_avg"], 2), "–" if x["t"] is None else f"{x['t']:.1f}"]
        L.append(f"| {label(st)} | {s['n']:,} | " + " | ".join(cells) + f" | {pct(s['avg_worst_20d'])} |")
    L += ["", "## Is it stable year to year? (10-session excess return)", "", "| Pattern | " + " | ".join(meta["years"]) + " |", "|---|" + "---|" * len(meta["years"])]
    for st in order:
        s = summ.get(st)
        if s and st != "ALL":
            L.append(f"| {label(st)} | " + " | ".join(pct(s["excess_10d_by_year"].get(y), 2) for y in meta["years"]) + " |")
    L += ["", "Not investment advice. Past patterns do not guarantee future results."]
    with open(os.path.join(HERE, "box_momentum_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(HERE, "box_momentum_report.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "suggested_rebound": sug, "results": summ}, f, indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--min-green", type=int, default=2, help="green days required before the white run (default 2)")
    ap.add_argument("--refresh", action="store_true", help="re-download instead of using the cache")
    ap.add_argument("--from-history", action="store_true", help="offline test on history/*.json")
    args = ap.parse_args()

    if args.from_history:
        prices, source = prices_from_history(), "history/*.json (~90 sessions, TEST ONLY)"
    else:
        prices, source = download_prices(load_tickers(args), args.years, args.refresh), f"yfinance {args.years}y daily"
    if not prices:
        sys.exit("No price data.")
    print(f"Scanning {len(prices)} stocks ...")
    df = run(prices, args.min_green)
    summ = summarise(df)
    sug = suggest_range(summ)
    years = sorted({str(y) for y in df.year.unique() if y})
    meta = {"run_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "tickers": len(prices), "source": source,
            "min_green": args.min_green, "horizons": HORIZONS, "years": years}
    write_report(summ, sug, meta)

    # console summary
    print(f"\n{'pattern':34}{'cases':>9}{'10d hit':>9}{'base':>7}{'10d excess':>12}{'t':>6}{'20d excess':>12}")
    for st in ["ALL", "green now", "Discount (green > white, 20d)"] + [f"white{k}" for k in range(1, MAX_K + 1)] + ["white, no green run before"]:
        s = summ.get(st)
        if not s or "10d" not in s:
            continue
        a, b = s["10d"], s.get("20d", {})
        name = f"{st[5:]}{'+' if st == f'white{MAX_K}' else ''} white after {args.min_green}+ green" if st.startswith("white") and st[5:].isdigit() else st
        print(f"{name:34}{s['n']:>9,}{a['hit']*100:>8.0f}%{a['hit_base']*100:>6.0f}%{pct(a['excess_avg'],2):>12}{(a['t'] or 0):>6.1f}{pct(b.get('excess_avg'),2):>12}")
    print("\nSuggested Rebound range:", f"{sug['min_white']} to {sug['max_white']} white days" if sug else "none found")
    print("Report: box_momentum_report.md")


if __name__ == "__main__":
    main()
