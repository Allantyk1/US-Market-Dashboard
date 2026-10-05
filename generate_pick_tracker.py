"""
Pick Tracker - data builder (Top 20 + every BUY signal across the universe)
------------------------------------------------------------------
Run right after generate_dashboard_data.py (STEP 9/11 in daily_refresh_all.bat).

What it does every day:
  1. Appends today's snapshot to top20_daily_log.json: the Top 20, plus every
     ticker's close, Suggested Entry, Signal and Matrix Score.
     First run (or an old-format log): back-fills from the git history of
     dashboard_data.json, so you start with ~2 months of daily snapshots.
  2. Builds pick_tracker_data.json, which tracker.html reads:
       - tickers   : name / category / today's signal for every ticker
       - watch     : last 45 sessions of close + Suggested Entry for every ticker
       - events    : every NEW Top-20 entry and every NEW BUY signal, with the
                     forward close at week 1..13, best close per horizon, and
                     return to date (for the best/worst leaderboard)
       - baseline  : the same hit-rate stats for every ticker on every day
                     (the "whole market" comparison line)

Session dating: the pipeline runs in Malaysia time, but every number refers to
the last *completed* US session. A run before 4pm New York time is dated to the
previous trading day - same rule as the screener's intraday guard.

Run: python generate_pick_tracker.py
"""

import json
import os
import subprocess
import datetime as dt

DASHBOARD_FILE = "dashboard_data.json"
HISTORY_DIR = "history"
LOG_FILE = "top20_daily_log.json"
OUT_FILE = "pick_tracker_data.json"

WATCH_SESSIONS = 40        # sessions of history per stock in the below-weekly-average section (8 weeks)
REENTRY_GAP = 5            # a ticker counts as a NEW pick if absent this many logged sessions
HORIZON_WEEKS = 13
SESSIONS_PER_WEEK = 5
TRACK_KEEP_SESSIONS = 140  # older sessions keep prices only for tickers picked (Top 20 / new BUY) within this window
TIERS = (0.0, 0.05, 0.10, 0.15, 0.20)
SIG_CODE = {"BUY": "B", "WATCHLIST": "W", "SELL": "S"}
MYT_OFFSET = 8             # Malaysia, UTC+8, no DST

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


# ---------------------------------------------------------------- dates
def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def ny_offset(utc_dt):
    """-4 during US daylight time (2nd Sun Mar 2am -> 1st Sun Nov 2am), else -5.
    Hand-rolled so it works on Windows without the tzdata package."""
    y = utc_dt.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(7))   # 2am EST = 07:00 UTC
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(6))    # 2am EDT = 06:00 UTC
    return -4 if start <= utc_dt < end else -5


def session_date_for(local_myt):
    """Malaysia wall-clock timestamp -> date of last completed US session (pre-holiday snap)."""
    utc = local_myt - dt.timedelta(hours=MYT_OFFSET)
    ny = utc + dt.timedelta(hours=ny_offset(utc))
    d = ny.date()
    if ny.time() < dt.time(16, 0):
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def labels_to_dates(labels, anchor):
    """'May 27' style labels (no year) -> ISO dates. Walks backwards from anchor."""
    out = [None] * len(labels)
    year = anchor.year
    prev_month = None
    for i in range(len(labels) - 1, -1, -1):
        mon, day = labels[i].split()
        m = MONTHS[mon[:3]]
        if prev_month is not None and m > prev_month:
            year -= 1
        d = dt.date(year, m, int(day))
        if d > anchor:            # first label later than anchor -> belongs to prior year
            year -= 1
            d = dt.date(year, m, int(day))
        out[i] = d.isoformat()
        prev_month = m
    return out


# ---------------------------------------------------------------- price history
_hist_cache = {}


def load_history(ticker, anchor):
    if ticker in _hist_cache:
        return _hist_cache[ticker]
    path = os.path.join(HISTORY_DIR, f"{ticker}.json")
    series = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                h = json.load(f)
            dates = labels_to_dates(h.get("labels", []), anchor)
            for d, p in zip(dates, h.get("price", [])):
                if isinstance(p, (int, float)) and p == p:   # drop None / NaN
                    series[d] = float(p)
        except Exception as e:
            print(f"  [WARN] could not read {path}: {e}")
    _hist_cache[ticker] = series
    return series


def build_calendar(anchor):
    """Union of trading dates found in the history files of a few liquid tickers."""
    cal = set()
    for t in ("SPY", "QQQ", "DIA", "AAPL", "MSFT"):
        cal.update(load_history(t, anchor).keys())
    if not cal and os.path.isdir(HISTORY_DIR):
        for fn in os.listdir(HISTORY_DIR)[:20]:
            cal.update(load_history(fn[:-5], anchor).keys())
    return sorted(cal)


def snap_to_calendar(d_iso, calendar):
    """Latest trading date <= d_iso (handles US holidays)."""
    best = None
    for c in calendar:
        if c <= d_iso:
            best = c
        else:
            break
    return best or d_iso


# ---------------------------------------------------------------- snapshot extraction
def snapshot_from_dashboard(dash):
    """Return (top20_rows, prices{ticker:[close, suggested_entry, signal_code, matrix_score]})."""
    top = []
    for r in dash.get("top20", []) or []:
        top.append({
            "ticker": r.get("ticker"),
            "company": r.get("company"),
            "price": r.get("current_price"),
            "suggested_entry": r.get("suggested_entry"),
            "matrix_score": r.get("matrix_score"),
            "signal": r.get("signal"),
        })
    prices = {}
    for r in dash.get("all_tickers", []) or []:
        t = r.get("ticker")
        if t:
            prices[t] = [r.get("current_price"), r.get("suggested_entry"),
                         SIG_CODE.get(r.get("signal"), ""), r.get("matrix_score")]
    for r in top:   # in case a top20 ticker is missing from all_tickers
        prices.setdefault(r["ticker"], [r["price"], r["suggested_entry"],
                                        SIG_CODE.get(r.get("signal"), ""), r.get("matrix_score")])
    return top, prices


def parse_generated_at(dash):
    g = dash.get("generated_at")
    try:
        return dt.datetime.fromisoformat(g)
    except Exception:
        return None


def sig_of(day, t):
    v = day["prices"].get(t)
    return v[2] if v and len(v) > 2 else ""


def tracked_set(log_days, upto_iso):
    """Tickers in the Top 20 or with a BUY signal within the last TRACK_KEEP_SESSIONS sessions."""
    keys = sorted(k for k in log_days if k <= upto_iso)[-TRACK_KEEP_SESSIONS:]
    s = set()
    for k in keys:
        s.update(r["ticker"] for r in log_days[k]["top20"])
        s.update(t for t, v in log_days[k]["prices"].items() if len(v) > 2 and v[2] == "B")
    return s


def log_needs_upgrade(log):
    """True when the log was written by the first version (no signal per ticker)."""
    for day in list(log.get("days", {}).values())[-5:]:
        for v in day.get("prices", {}).values():
            return len(v) < 4
    return False


def backfill_from_git(log, calendar):
    """Rebuild daily snapshots from every committed dashboard_data.json."""
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--format=%H", "--", DASHBOARD_FILE],
            capture_output=True, text=True, check=True).stdout.split()
    except Exception as e:
        print(f"  [INFO] git history not available ({e})")
        return 0
    added = 0
    for sha in out:
        try:
            raw = subprocess.run(["git", "show", f"{sha}:{DASHBOARD_FILE}"],
                                 capture_output=True, text=True, check=True,
                                 encoding="utf-8").stdout
            dash = json.loads(raw)
        except Exception:
            continue
        if not dash.get("is_real_data", True) or not dash.get("top20"):
            continue
        ga = parse_generated_at(dash)
        if ga is None:
            continue
        sd = snap_to_calendar(session_date_for(ga).isoformat(), calendar)
        top, prices = snapshot_from_dashboard(dash)
        prev = log["days"].get(sd)
        if prev and len(prices) < 0.5 * len(prev.get("prices", {})):
            continue   # a partial commit (e.g. empty all_tickers) must not overwrite a full one
        # later commits on the same session overwrite earlier ones (last run wins)
        log["days"][sd] = {"generated_at": dash.get("generated_at"), "source": f"git:{sha[:8]}",
                           "top20": top, "prices": prices}
        added += 1
    return added


def r4(x):
    return round(x, 4) if isinstance(x, (int, float)) else None


# ---------------------------------------------------------------- main build
def main():
    if not os.path.exists(DASHBOARD_FILE):
        print(f"[SKIP] {DASHBOARD_FILE} not found - run generate_dashboard_data.py first")
        return
    with open(DASHBOARD_FILE, encoding="utf-8") as f:
        dash = json.load(f)

    ga = parse_generated_at(dash) or dt.datetime.now()
    anchor = ga.date()
    calendar = build_calendar(anchor)
    if not calendar:
        print(f"[SKIP] no price history found in {HISTORY_DIR}/")
        return

    # ---- 1. daily log ------------------------------------------------------
    log = {"days": {}}
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding="utf-8") as f:
            log = json.load(f)
    if not log.get("days") or log_needs_upgrade(log):
        fresh = {"days": {}}
        n = backfill_from_git(fresh, calendar)
        if n:
            # keep any old-format sessions git could not rebuild
            for k, v in log.get("days", {}).items():
                fresh["days"].setdefault(k, v)
            log = fresh
        print(f"  Back-filled {n} snapshots from git history -> {len(log['days'])} sessions")

    today_session = snap_to_calendar(session_date_for(ga).isoformat(), calendar)
    if dash.get("is_real_data", True) and dash.get("top20") and dash.get("all_tickers"):
        top, prices = snapshot_from_dashboard(dash)
        log["days"][today_session] = {"generated_at": dash.get("generated_at"), "source": "daily",
                                      "top20": top, "prices": prices}
    log["days"] = dict(sorted(log["days"].items()))
    # keep the log small: older sessions only keep tickers that were picked recently
    keys = list(log["days"].keys())
    keep_full = set(keys[-(WATCH_SESSIONS + 10):])
    tracked = tracked_set(log["days"], keys[-1]) if keys else set()
    for k in keys:
        if k not in keep_full:
            pr = log["days"][k]["prices"]
            log["days"][k]["prices"] = {t: v for t, v in pr.items() if t in tracked}
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, separators=(",", ":"))

    days = log["days"]
    logged = list(days.keys())
    cal_idx = {d: i for i, d in enumerate(calendar)}
    last_cal = calendar[-1]

    def close_on(ticker, d):
        p = load_history(ticker, anchor).get(d)
        if p is None and d in days:
            v = days[d]["prices"].get(ticker)
            if v and isinstance(v[0], (int, float)):
                p = float(v[0])
        return p

    def sugg_on(ticker, d):
        if d in days:
            v = days[d]["prices"].get(ticker)
            if v and len(v) > 1 and isinstance(v[1], (int, float)):
                return float(v[1])
        return None

    # ---- ticker directory -----------------------------------------------------
    tickers = {}
    for d in logged:
        for r in days[d]["top20"]:
            tickers.setdefault(r["ticker"], {"co": r.get("company")})
    today_top = {r["ticker"] for r in days.get(today_session, {}).get("top20", [])}
    for r in dash.get("all_tickers", []) or []:
        t = r.get("ticker")
        if not t:
            continue
        tickers[t] = {"co": r.get("company"), "cat": r.get("category"),
                      "sig": SIG_CODE.get(r.get("signal"), ""), "ms": r.get("matrix_score"),
                      "px": r.get("current_price"), "se": r.get("suggested_entry"),
                      "t20": 1 if t in today_top else 0}

    # ---- section: entry watch for every ticker (compact arrays) -------------------
    window = calendar[-(WATCH_SESSIONS + SESSIONS_PER_WEEK):]   # extra 5 for the first averages
    top_by_day = {d: {r["ticker"] for r in days[d]["top20"]} for d in logged}
    watch = {}
    dash_tick = {r.get("ticker") for r in dash.get("all_tickers", [])}
    for t in tickers:
        if t not in dash_tick and t not in today_top:
            continue
        c = [r4(close_on(t, d)) for d in window]
        if all(v is None for v in c):
            continue
        watch[t] = {
            "c": c,
            "se": [r4(sugg_on(t, d)) for d in window],
            "t20": "".join("1" if d in top_by_day and t in top_by_day[d] else ("0" if d in days else "-") for d in window),
            "sig": "".join((sig_of(days[d], t) or "-") if d in days else "-" for d in window),
        }

    # ---- forward-return helper ------------------------------------------------
    def forward(t, d, rec):
        i0 = cal_idx[d]
        fc, fp = [], []
        best = None
        for w in range(1, HORIZON_WEEKS + 1):
            lo = i0 + (w - 1) * SESSIONS_PER_WEEK + 1
            hi = i0 + w * SESSIONS_PER_WEEK
            if hi >= len(calendar):
                fc.append(None); fp.append(None)
                continue
            for k in range(lo, hi + 1):
                p = close_on(t, calendar[k])
                if p is not None:
                    best = p if best is None else max(best, p)
            fc.append(r4(close_on(t, calendar[hi])))
            fp.append(r4(best))
        # to date: best and worst close since the recommendation
        since = [close_on(t, calendar[k]) for k in range(i0 + 1, len(calendar))]
        since = [p for p in since if p is not None]
        # flag a one-day move of 50%+ either way: often a split, reverse split or spin-off
        # that Yahoo's unadjusted close has not corrected, rather than a real gain/loss
        jump = None
        prev = rec
        for k in range(i0 + 1, len(calendar)):
            p = close_on(t, calendar[k])
            if p is None:
                continue
            if prev and (p / prev <= 0.5 or p / prev >= 2.0):
                jump = calendar[k]
            prev = p
        return fc, fp, (r4(max(since)) if since else None), (r4(min(since)) if since else None), jump

    # ---- events: new Top-20 entries and new BUY signals -------------------------
    events = []

    def add_event(src, t, d, fallback_price, ms):
        if d not in cal_idx:
            return
        rec = close_on(t, d) or fallback_price
        if not isinstance(rec, (int, float)) or rec <= 0:
            return
        fc, fp, hi, lo, jump = forward(t, d, rec)
        ev = {"t": t, "src": src, "d": d, "rp": r4(float(rec)), "ms": ms,
              "lc": r4(close_on(t, last_cal)), "hi": hi, "lo": lo, "fc": fc, "fp": fp}
        if jump:
            ev["jump"] = jump
        events.append(ev)

    last_top, last_buy = {}, {}
    for li, d in enumerate(logged):
        for r in days[d]["top20"]:
            t = r["ticker"]
            prev = last_top.get(t)
            last_top[t] = li
            if prev is None or (li - prev) > REENTRY_GAP:
                add_event("top20", t, d, r.get("price"), r.get("matrix_score"))
        for t, v in days[d]["prices"].items():
            if len(v) < 3 or v[2] != "B":
                continue
            prev = last_buy.get(t)
            last_buy[t] = li
            if prev is None or (li - prev) > REENTRY_GAP:
                add_event("buy", t, d, v[0], v[3] if len(v) > 3 else None)

    # ---- baseline: every ticker on every logged day ------------------------------
    universe = sorted({t for d in logged[-(WATCH_SESSIONS + 10):] for t in days[d]["prices"]})
    baseline = {}
    for mode in ("close", "peak"):
        acc = [[0, [0] * len(TIERS), 0.0] for _ in range(HORIZON_WEEKS)]
        baseline[mode] = acc
    for d in logged:
        if d not in cal_idx:
            continue
        i0 = cal_idx[d]
        for t in universe:
            p0 = close_on(t, d)
            if not p0:
                continue
            best = None
            for w in range(HORIZON_WEEKS):
                hi = i0 + (w + 1) * SESSIONS_PER_WEEK
                if hi >= len(calendar):
                    break
                for k in range(i0 + w * SESSIONS_PER_WEEK + 1, hi + 1):
                    p = close_on(t, calendar[k])
                    if p is not None:
                        best = p if best is None else max(best, p)
                pc = close_on(t, calendar[hi])
                for mode, p in (("close", pc), ("peak", best)):
                    if p is None:
                        continue
                    r = p / p0 - 1
                    a = baseline[mode][w]
                    a[0] += 1
                    a[2] += r
                    for j, k in enumerate(TIERS):
                        if (r > 0) if k == 0 else (r >= k):
                            a[1][j] += 1
    base_out = {m: [{"n": a[0], "rates": [round(x / a[0], 4) if a[0] else None for x in a[1]],
                     "avg1k": round(1000 * a[2] / a[0], 2) if a[0] else None} for a in acc]
                for m, acc in baseline.items()}

    out = {
        "version": 2,
        "generated_at": dash.get("generated_at"),
        "as_of_session": today_session,
        "last_price_date": last_cal,
        "first_logged_session": logged[0] if logged else None,
        "logged_sessions": len(logged),
        "settings": {"watch_sessions": WATCH_SESSIONS, "reentry_gap": REENTRY_GAP,
                     "horizon_weeks": HORIZON_WEEKS, "sessions_per_week": SESSIONS_PER_WEEK},
        "watch_dates": window,
        "tickers": tickers,
        "watch": watch,
        "events": events,
        "baseline": base_out,
        "baseline_note": f"{len(universe)} tickers x {len(logged)} sessions",
    }

    def _clean(o):   # NaN-proof (strict JSON.parse in browsers rejects NaN)
        if isinstance(o, float) and o != o:
            return None
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        return o

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, separators=(",", ":"))

    n_top = sum(e["src"] == "top20" for e in events)
    print(f"[SUCCESS] {OUT_FILE}: session {today_session}, {len(logged)} logged sessions, "
          f"{n_top} Top-20 picks + {len(events) - n_top} BUY-signal picks, "
          f"{len(watch)} tickers in entry watch, baseline {out['baseline_note']}")


if __name__ == "__main__":
    main()
