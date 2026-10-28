"""
Earnings Calendar (cached)
------------------------------------------------------------------
Adds the next (and most recent) earnings date to every ticker on the
dashboard. Stocks often gap down after a report, so the card shows how many
days until the next one - and, for a few days after, that it just reported.

Why a separate cache instead of fetching inside stock_screener_us.py:
  - Earnings dates change rarely, so re-asking Yahoo for ~670 tickers every
    day would add ~6-10 minutes to the run for no new information.
  - The cache (earnings_cache.json) is refreshed only when an entry is
    stale, so after the first run a typical day re-fetches a few dozen
    tickers at most.

Refresh rules for each ticker (see _needs_refresh):
  - never fetched                          -> fetch
  - next date has passed                   -> fetch (it becomes "last")
  - next date within 14 days, cache > 1d   -> fetch (dates get confirmed/moved)
  - otherwise cache older than 7 days      -> fetch
  - no date found (ETFs etc.)              -> retry after 14 days

Keep-previous guard (added 2026-10-17): if Yahoo answers with NO upcoming
date for a ticker whose cached date is still in the future, the cached date
is kept (marked "kept": true) and re-checked next run. On 2026-10-17 Yahoo
returned nothing for every ticker reporting in the next two weeks, which
wiped 243 known dates (INTC, SOFI, HOOD, GOOGL, AMZN ...) from the dashboard.
A real date change always arrives as a different date, never as a blank.

Rate limiting matches the screener: 0.5s per ticker, 8s pause every 50.
Imported by generate_dashboard_data.py; can also be run on its own:
    python earnings_calendar.py            (refresh cache for the report's tickers)
"""

import datetime
import json
import os
import time

CACHE_FILE = "earnings_cache.json"
STALE_DAYS = 7          # normal re-check interval
NEAR_DAYS = 14          # inside this window, re-check daily
NEAR_STALE_DAYS = 1
EMPTY_RETRY_DAYS = 14   # ETFs / tickers with no earnings date
RECENT_DAYS = 5         # show "reported Xd ago" for this many days after


def _today():
    return datetime.date.today()


def _to_date(v):
    """Accepts date, datetime, pandas Timestamp or ISO string -> date/None."""
    if v is None:
        return None
    try:
        if hasattr(v, "date") and callable(v.date):
            return v.date()
        if isinstance(v, datetime.date):
            return v
        return datetime.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _iso(d):
    return d.isoformat() if d else None


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] could not read {CACHE_FILE} ({e}) - starting a fresh cache")
        return {}


def save_cache(cache):
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    os.replace(tmp, CACHE_FILE)   # atomic: a crash never leaves a half-written cache


def _needs_refresh(entry, today):
    if not entry:
        return True
    fetched = _to_date(entry.get("fetched"))
    if fetched is None:
        return True
    age = (today - fetched).days
    nxt = _to_date(entry.get("next"))
    if nxt is None:
        return age >= EMPTY_RETRY_DAYS
    if nxt < today:
        return True
    if (nxt - today).days <= NEAR_DAYS:
        return age >= NEAR_STALE_DAYS
    return age >= STALE_DAYS


def fetch_one(symbol):
    """Return (next_date, is_estimate, last_date) from Yahoo via yfinance.
    Handles both the newer dict-style Ticker.calendar and the older
    DataFrame style. Any failure returns (None, False, None)."""
    import yfinance as yf
    import warnings; warnings.filterwarnings("ignore", message=".*inplace keyword.*")  # yfinance re-enables pandas inplace notices on import
    t = yf.Ticker(symbol)
    today = _today()
    dates = []
    try:
        cal = t.calendar
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date") or []
            raw = raw if isinstance(raw, (list, tuple)) else [raw]
            dates = [_to_date(x) for x in raw]
        elif cal is not None and hasattr(cal, "empty") and not cal.empty:
            # old yfinance: DataFrame with 'Earnings Date' as a row label
            if "Earnings Date" in cal.index:
                dates = [_to_date(x) for x in cal.loc["Earnings Date"].tolist()]
    except Exception:
        dates = []
    dates = sorted(d for d in dates if d)
    nxt = next((d for d in dates if d >= today), None)
    # Yahoo gives a 2-date range when the company hasn't confirmed the day yet
    is_estimate = len(dates) > 1 and dates[0] != dates[-1]

    last = None
    try:
        ed = t.get_earnings_dates(limit=8)
        if ed is not None and not ed.empty:
            past = sorted(_to_date(x) for x in ed.index if _to_date(x) and _to_date(x) < today)
            last = past[-1] if past else None
            if nxt is None:
                fut = sorted(_to_date(x) for x in ed.index if _to_date(x) and _to_date(x) >= today)
                nxt = fut[0] if fut else None
    except Exception:
        pass   # get_earnings_dates scrapes a different page and fails more often - optional
    return nxt, is_estimate, last


def refresh(symbols, max_fetch=None):
    """Bring the cache up to date for `symbols`. Returns the cache dict.
    Saves progress every 50 tickers so an interrupted run isn't wasted."""
    cache = load_cache()
    today = _today()
    todo = [s for s in symbols if s and _needs_refresh(cache.get(s), today)]
    if max_fetch:
        todo = todo[:max_fetch]
    print(f"[INFO] Earnings calendar: {len(symbols) - len(todo)} cached, {len(todo)} to fetch")
    ok = failed = kept = 0
    for n, sym in enumerate(todo, 1):
        prev = cache.get(sym) or {}
        try:
            nxt, est, last = fetch_one(sym)
        except Exception as e:
            print(f"[WARN] earnings fetch failed for {sym}: {e}")
            failed += 1
            continue   # keep whatever was cached before
        old_next = _to_date(prev.get("next"))
        # Yahoo glitch guard: a blank answer never erases a future date we already know
        keep = nxt is None and old_next is not None and old_next >= today
        if keep:
            nxt, est = old_next, bool(prev.get("estimate"))
            kept += 1
        # If the previously cached "next" date has now passed, that IS the
        # last report - keep it even if Yahoo's history page didn't load.
        if last is None and old_next and old_next < today:
            last = old_next
        if last is None:
            last = _to_date(prev.get("last"))
        cache[sym] = {"next": _iso(nxt), "estimate": bool(est), "last": _iso(last),
                      "fetched": _iso(today)}
        if keep:
            cache[sym]["kept"] = True   # Yahoo gave no date; previous date kept, re-checked next run
        ok += 1
        time.sleep(0.5)
        if n % 50 == 0:
            save_cache(cache)
            print(f"[INFO]   earnings {n}/{len(todo)} ...")
            time.sleep(8)
    save_cache(cache)
    with_date = sum(1 for s in symbols if (cache.get(s) or {}).get("next"))
    print(f"[SUCCESS] Earnings calendar: fetched {ok}, failed {failed}; "
          f"{with_date}/{len(symbols)} tickers have an upcoming date")
    if kept:
        print(f"[WARN] Yahoo returned no earnings date for {kept} ticker(s) that had one - "
              f"kept the previous dates (Yahoo glitch guard)")
    return cache


def earnings_fields(symbol, cache, today=None):
    """Fields merged into each ticker record in dashboard_data.json."""
    today = today or _today()
    e = cache.get(symbol) or {}
    nxt, last = _to_date(e.get("next")), _to_date(e.get("last"))
    if nxt and nxt < today:          # cache not refreshed yet but date passed
        last, nxt = nxt, None
    days_since = (today - last).days if last else None
    return {
        "earnings_date": _iso(nxt),
        "earnings_days": (nxt - today).days if nxt else None,
        "earnings_estimate": bool(e.get("estimate")) if nxt else False,
        "last_earnings_date": _iso(last),
        "days_since_earnings": days_since if (days_since is not None and days_since <= RECENT_DAYS) else None,
    }


if __name__ == "__main__":
    import pandas as pd
    df = pd.read_excel("Complete_US_Market_Report.xlsx")
    refresh([str(s) for s in df["Ticker"].dropna().unique()])
