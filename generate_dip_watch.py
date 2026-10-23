"""
generate_dip_watch.py  (added 2026-10-23)

Builds dip_watch_data.json for the Look-ahead tab's "Dip watch & momentum"
section (lookahead.html, section 5) and for three scoreboard rules:

  * Dip 15%+, not at a 52-week low   (forward-tested buy rule)
  * Gap-up continuation               (forward-tested buy rule)
  * At a new 52-week low              (AVOID warning only)

Why these three (backtest 2026-10-23, 676 stocks, Oct 2021 - Oct 2026,
see dip_bounce_backtest_2026-10-23.md in the project):
  - A stock's own past bounce record barely predicts its next bounce, so it
    is shown as context only, never as a trigger.
  - Dips of 15%+ beat the market by about +1.5% over 4 weeks on average
    (coin-flip win rate); dips that are ALSO a new 52-week low had no edge.
  - A gap up of 4%+ on 2x volume that closes up (post-earnings drift) was the
    only momentum entry that beat the market over 3 months in every year
    except a flat 2026; breakouts, MACD crosses, EMA pullbacks and squeezes
    were all around zero.

Prices: shares winrate_price_cache.pkl with train_winrate_model.py (5 years,
local only, in .gitignore). A normal day only tops up the last month.

Usage:
  python generate_dip_watch.py               top up prices, build the JSON
  python generate_dip_watch.py --no-download use the cache as-is (offline)
"""
import datetime as dt
import json
import math
import os
import pickle
import sys
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
# yfinance still uses pandas' inplace=... internally; harmless, just noisy
warnings.filterwarnings("ignore", message=".*inplace keyword.*")

DASHBOARD_FILE = "dashboard_data.json"
PICK_FILE = "pick_tracker_data.json"
CACHE_FILE = "winrate_price_cache.pkl"      # shared with train_winrate_model.py
OUT_FILE = "dip_watch_data.json"

HISTORY_PERIOD = "5y"
BATCH_SIZE = 50
BATCH_COOLDOWN = 8

DIP = 0.15            # close at least 15% under its 20-session high
LOOK = 20             # sessions in the "recent high" window
HORIZON = 20          # outcome window (4 weeks)
LOW_WIN = 252         # 52-week low/high window
GAP_MIN = 0.04        # open at least 4% above the prior close
GAP_VOL = 2.0         # volume at least 2x the 50-day average
GAP_HORIZON = 60      # gap-up edge measured over 3 months
FLAG_SESSIONS = 70    # daily flags kept for the scoreboard replay
STATS_SINCE = "2023-10-01"
MIN_PRICE = 5.0       # penny stocks are left out of the lists and rules (splits, junk moves)


# ---------------------------------------------------------------- helpers
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else round(v, 4)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def us_eastern_now():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return dt.datetime.utcnow() - dt.timedelta(hours=4)


# ---------------------------------------------------------------- prices (same cache as train_winrate_model.py)
def _extract_one(raw, ticker):
    if raw is None or raw.empty:
        return None
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        if ticker in cols.get_level_values(0):
            sub = raw[ticker]
        elif ticker in cols.get_level_values(1):
            sub = raw.xs(ticker, axis=1, level=1)
        else:
            return None
    else:
        sub = raw
    need = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in sub.columns for c in need):
        return None
    sub = sub[need].copy().dropna(subset=["Close"])
    if sub.empty:
        return None
    idx = pd.to_datetime(sub.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    sub.index = idx.normalize()
    sub = sub[~sub.index.duplicated(keep="last")].sort_index()
    return sub.astype(float)


def _drop_unfinished_session(df):
    """Never treat a still-open session's live price as a close (2026-09-12 lesson)."""
    if df is None or df.empty:
        return df
    now_et = us_eastern_now()
    if df.index[-1].date() == now_et.date() and now_et.hour < 17:
        return df.iloc[:-1]
    return df


def download_batches(tickers, period):
    try:
        import yfinance as yf
    except ImportError:
        print("[STOP] yfinance is not installed. Run:  pip install yfinance")
        sys.exit(1)
    # Importing yfinance re-enables its own deprecation warnings (it puts a
    # 'default' filter in front of ours), so silence pandas' inplace notices again
    # AFTER the import. Harmless notices from inside yfinance; other warnings still show.
    warnings.filterwarnings("ignore", message=".*inplace keyword.*")
    out = {}
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        print(f"  downloading {i + 1}-{i + len(batch)} of {len(tickers)} ({period}) ...", flush=True)
        raw = None
        for attempt in range(3):
            try:
                raw = yf.download(batch, period=period, interval="1d", group_by="ticker",
                                  auto_adjust=True, threads=False, progress=False)
                break
            except Exception as e:
                print(f"    retry {attempt + 1}/3 after error: {e}")
                time.sleep(BATCH_COOLDOWN * (attempt + 1))
        for t in batch:
            sub = _extract_one(raw, t)
            if sub is not None and len(sub):
                out[t] = sub
        if i + BATCH_SIZE < len(tickers):
            time.sleep(BATCH_COOLDOWN)
    return out


def update_cache(tickers, allow_download=True):
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "rb") as f:
            cache = pickle.load(f)
    if not allow_download:
        print(f"[INFO] --no-download: using cached prices for {len(cache)} tickers")
        return cache
    today = pd.Timestamp(dt.date.today())
    full, top_up = [], []
    for t in tickers:
        df = cache.get(t)
        if df is None or df.empty or (today - df.index[-1]).days > 20:
            full.append(t)
        else:
            top_up.append(t)
    if full:
        print(f"[DATA] Full {HISTORY_PERIOD} download for {len(full)} tickers")
        for t, df in download_batches(full, HISTORY_PERIOD).items():
            cache[t] = df
    if top_up:
        print(f"[DATA] Topping up the last month for {len(top_up)} tickers")
        for t, new in download_batches(top_up, "1mo").items():
            old = cache.get(t)
            merged = pd.concat([old, new]) if old is not None else new
            cache[t] = merged[~merged.index.duplicated(keep="last")].sort_index()
    cutoff = today - pd.Timedelta(days=365 * 6)
    for t in list(cache):
        df = _drop_unfinished_session(cache[t])
        cache[t] = df[df.index >= cutoff]
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(cache, f)
    os.replace(tmp, CACHE_FILE)
    return cache


# ---------------------------------------------------------------- analysis
def dip_events(c, mk):
    """Every 15%+ dip off the 20-session high, with a 20-session cooldown.
    Outcome fields are None while the 4-week window is still open."""
    v = c.values
    idx = c.index
    hi = c.rolling(LOOK, min_periods=LOOK).max().values
    lo_prev = c.shift(1).rolling(LOW_WIN, min_periods=120).min().values
    n = len(v)
    out = []
    i = LOOK
    while i < n:
        if v[i] / hi[i] - 1 <= -DIP:
            done = i + HORIZON < n
            e = {"d": idx[i], "newlow": bool(not np.isnan(lo_prev[i]) and v[i] <= lo_prev[i]),
                 "done_d": idx[i + HORIZON] if done else None}
            if done:
                j = i + HORIZON
                win = v[i + 1:j + 1]
                e["fwd"] = v[j] / v[i] - 1
                e["ex"] = e["fwd"] - (mk.loc[idx[j]] / mk.loc[idx[i]] - 1)
                e["rec"] = bool((win >= v[i] + 0.5 * (hi[i] - v[i])).any())
                e["slide"] = bool((win <= v[i] * 0.90).any())
            out.append(e)
            i += HORIZON
        else:
            i += 1
    return out


def summarize(events):
    ev = [e for e in events if e.get("done_d") is not None]
    if not ev:
        return {"n": 0}
    return {"n": len(ev),
            "rec": float(np.mean([e["rec"] for e in ev])),
            "slide": float(np.mean([e["slide"] for e in ev])),
            "ex": float(np.mean([e["ex"] for e in ev])),
            "win": float(np.mean([e["ex"] > 0 for e in ev]))}


def main():
    allow = "--no-download" not in sys.argv
    dash = load_json(DASHBOARD_FILE, {}) or {}
    rows = {r["ticker"]: r for r in dash.get("all_tickers", []) if r.get("ticker")}
    if not rows:
        print(f"[STOP] {DASHBOARD_FILE} has no tickers - run generate_dashboard_data.py first.")
        sys.exit(1)
    tickers = sorted(rows)
    cache = update_cache(tickers, allow)
    have = [t for t in tickers if t in cache and len(cache[t]) > LOOK + 5]
    missing = [t for t in tickers if t not in have]
    if missing:
        print(f"[WARN] No usable price history for {len(missing)} tickers (e.g. {', '.join(missing[:8])})")
    if len(have) < 50:
        print("[STOP] Too few tickers with prices - not writing dip_watch_data.json")
        sys.exit(1)

    C = pd.DataFrame({t: cache[t]["Close"] for t in have}).sort_index()
    O = pd.DataFrame({t: cache[t]["Open"] for t in have}).reindex(C.index)
    V = pd.DataFrame({t: cache[t]["Volume"] for t in have}).reindex(C.index)
    # keep only sessions where most stocks traded (drops odd half-filled dates)
    C = C[C.notna().mean(axis=1) >= 0.5]
    O, V = O.reindex(C.index), V.reindex(C.index)
    mk = (1 + C.pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod()
    as_of = C.index[-1].strftime("%Y-%m-%d")

    # ---- daily flags for the scoreboard replay
    hi20 = C.rolling(LOOK, min_periods=LOOK).max()
    lo52p = C.shift(1).rolling(LOW_WIN, min_periods=120).min()
    hi52 = C.rolling(LOW_WIN, min_periods=120).max()
    dd = C / hi20 - 1
    newlow = C <= lo52p
    liquid = C >= MIN_PRICE
    dipf = (dd <= -DIP) & ~newlow & liquid
    volr = V / V.shift(1).rolling(50, min_periods=30).mean()
    gapr = O / C.shift(1) - 1
    gapf = (gapr >= GAP_MIN) & (volr >= GAP_VOL) & (C > C.shift(1)) & liquid
    sma200 = C.rolling(200, min_periods=150).mean()
    sma50 = C.rolling(50, min_periods=40).mean()

    last = C.index[-FLAG_SESSIONS:]
    flag_dates = [d.strftime("%Y-%m-%d") for d in last]

    def fstr(frame, t):
        s = frame[t].reindex(last)
        return "".join("1" if bool(x) else "0" for x in s.fillna(False))

    flags = {}
    for t in have:
        f = {"dip": fstr(dipf, t), "gap": fstr(gapf, t), "low": fstr(newlow & liquid, t)}
        f = {k: v for k, v in f.items() if "1" in v}
        if f:
            flags[t] = f

    # ---- per-stock bounce history + universe test (refreshed every run)
    per, all_ev = {}, []
    for t in have:
        c = C[t].dropna()
        if len(c) < 200:
            continue
        ev = dip_events(c, mk)
        per[t] = summarize(ev)
        for e in ev:
            e["t"] = t
        all_ev.extend(ev)
    recent = [e for e in all_ev if e["d"] >= pd.Timestamp(STATS_SINCE)]
    stats = {
        "since": STATS_SINCE,
        "all": summarize(recent),
        "newlow": summarize([e for e in recent if e["newlow"]]),
        "notlow": summarize([e for e in recent if not e["newlow"]]),
    }
    # bounce-record buckets (only dips whose earlier history was already known)
    by_t = {}
    for e in sorted(all_ev, key=lambda e: e["d"]):
        by_t.setdefault(e["t"], []).append(e)
    buckets = {"low": [], "mid": [], "high": []}
    for t, evs in by_t.items():
        for k, e in enumerate(evs):
            if e["d"] < pd.Timestamp(STATS_SINCE) or e.get("done_d") is None:
                continue
            prior = [p for p in evs[:k] if p.get("done_d") is not None and p["done_d"] < e["d"]]
            if len(prior) < 3:
                continue
            s = np.mean([p["rec"] for p in prior])
            buckets["low" if s <= 1 / 3 else "high" if s >= 2 / 3 else "mid"].append(e)
    stats["by_record"] = {k: summarize(v) for k, v in buckets.items()}

    # gap-up continuation, 3-month edge by year (20-session cooldown per stock)
    f3 = C.shift(-GAP_HORIZON) / C - 1
    ex3 = f3.sub(mk.shift(-GAP_HORIZON) / mk - 1, axis=0)
    gx = []
    G = gapf.values
    for j, t in enumerate(C.columns):
        lastk = -999
        for i in np.flatnonzero(G[:, j]):
            if i - lastk >= 20:
                lastk = i
                val = ex3.iat[i, j]
                if not np.isnan(val):
                    gx.append((C.index[i].year, float(val)))
    years = sorted({y for y, _ in gx})
    by_year = {str(y): float(np.mean([v for yy, v in gx if yy == y])) for y in years}
    stats["gap"] = {"n": len(gx), "ex": float(np.mean([v for _, v in gx])) if gx else None,
                    "win": float(np.mean([v > 0 for _, v in gx])) if gx else None,
                    "by_year": by_year, "horizon": GAP_HORIZON}

    # ---- today's lists
    i_last = len(C) - 1
    dips, gaps = [], []
    for t in have:
        c = C[t]
        px = c.iat[i_last]
        if np.isnan(px) or px < MIN_PRICE:
            continue
        r = rows.get(t, {})
        led = r.get("last_earnings_date")
        d_now = dd[t].iat[i_last]
        if not np.isnan(d_now) and d_now <= -DIP:
            win = c.iloc[-LOOK:]
            pk_i = int(np.nanargmax(win.values))
            pk_date = win.index[pk_i]
            chg = c.pct_change(fill_method=None).loc[pk_date:].iloc[1:]
            big = float(chg.min()) if len(chg) else None
            big_d = chg.idxmin().strftime("%Y-%m-%d") if len(chg) else None
            post_earn = False
            if led and big_d:
                post_earn = abs((pd.Timestamp(big_d) - pd.Timestamp(led)).days) <= 4
            dips.append({
                "t": t, "px": px, "hi20": hi20[t].iat[i_last], "dd": d_now,
                "dd52": px / hi52[t].iat[i_last] - 1 if not np.isnan(hi52[t].iat[i_last]) else None,
                "low52": bool(newlow[t].iat[i_last]),
                "days": len(c.loc[pk_date:]) - 1,
                "big": big, "big_d": big_d, "post_earn": post_earn,
                # a one-day fall of 45%+ is usually a split/spin-off adjustment, not a real dip
                "suspect": bool(big is not None and big <= -0.45),
                "uptrend": bool(c.loc[pk_date] > sma200[t].loc[pk_date]) if not np.isnan(sma200[t].loc[pk_date]) else None,
                "hist": per.get(t, {"n": 0}),
            })
        for k in range(max(0, i_last - 9), i_last + 1):
            if bool(gapf[t].iat[k]):
                d = C.index[k]
                post_earn = bool(led) and abs((d - pd.Timestamp(led)).days) <= 4
                gaps.append({
                    "t": t, "d": d.strftime("%Y-%m-%d"), "sessions_ago": i_last - k,
                    "gap": gapr[t].iat[k], "volx": volr[t].iat[k], "day": c.iat[k] / c.iat[k - 1] - 1,
                    "since": px / c.iat[k] - 1, "px": px,
                    "uptrend": bool(px > sma50[t].iat[i_last] > sma200[t].iat[i_last]) if not np.isnan(sma200[t].iat[i_last]) else None,
                    "post_earn": post_earn,
                })
    dips.sort(key=lambda r: (r["suspect"], r["low52"], r["dd"]))
    gaps.sort(key=lambda r: (r["sessions_ago"], -r["gap"]))

    pick = load_json(PICK_FILE, {}) or {}
    out = {
        "version": 1,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "as_of": as_of,
        "tracker_session": pick.get("as_of_session"),
        "params": {"min_price": MIN_PRICE, "dip": DIP, "look": LOOK, "horizon": HORIZON, "gap_min": GAP_MIN, "gap_vol": GAP_VOL},
        "tickers_priced": len(have),
        "stats": stats,
        "dates": flag_dates,
        "flags": flags,
        "dips": dips,
        "gaps": gaps,
    }
    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean(out), f, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, OUT_FILE)
    note = ""
    if pick.get("as_of_session") and pick["as_of_session"] != as_of:
        note = f"  [WARN] prices end {as_of} but the tracker is on {pick['as_of_session']}"
    print(f"[OK] {OUT_FILE}: {len(dips)} dips, {len(gaps)} gap-ups, as of {as_of}, "
          f"{len(have)} stocks priced.{note}")


if __name__ == "__main__":
    main()
