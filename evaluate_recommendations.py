"""
Recommendation Effectiveness Evaluator
------------------------------------------------------------------
Reads recommendation_log.json (built by log_top20_snapshot.py) and grades
every cohort that has had time to mature, using the price history already
cached in history/*.json (no live market data call needed, as long as this
is run within the ~90-trading-day rolling window the dashboard keeps per
ticker).

For each pick, reports:
  - actual price change at +30 and +60 calendar days (when available)
  - whether it beat the SPY/QQQ/DIA average over the same window (alpha)
  - whether it hit its analyst target
Then rolls this up into a scorecard: hit rate, average return, average
alpha, broken out by matrix_score / RSI band / MACD trend at entry - so you
can see which flavour of "Top 20 pick" has actually been working.

Run: python3 evaluate_recommendations.py
Outputs: recommendation_scorecard.json, recommendation_scorecard.csv,
and prints a human-readable summary.
"""

import json
import os
import csv
import datetime
import statistics as stats

LOG_FILE = "recommendation_log.json"
HISTORY_DIR = "history"
BENCHMARKS = ["SPY", "QQQ", "DIA"]

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def label_to_date(label, near_date):
    """History labels look like 'Apr 13' with no year. Pick whichever
    nearby year makes the resulting date closest to `near_date`, since a
    cohort logged in December could reference January labels from the
    following year."""
    mon_str, day_str = label.split()
    month = MONTHS[mon_str]
    day = int(day_str)
    best = None
    for year in (near_date.year - 1, near_date.year, near_date.year + 1):
        try:
            d = datetime.date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((d - near_date).days) < abs((best - near_date).days):
            best = d
    return best


def load_history_cache():
    cache = {}
    if not os.path.isdir(HISTORY_DIR):
        return cache
    for fname in os.listdir(HISTORY_DIR):
        if fname.endswith(".json"):
            with open(os.path.join(HISTORY_DIR, fname)) as f:
                d = json.load(f)
            cache[d["ticker"]] = d
    return cache


def price_at_offset(hist, entry_date, calendar_days):
    """Find the closest trading-day price at entry_date + calendar_days."""
    if not hist or not hist.get("labels"):
        return None, None
    target = entry_date + datetime.timedelta(days=calendar_days)
    dated_prices = []
    for i, label in enumerate(hist["labels"]):
        d = label_to_date(label, entry_date)
        p = hist["price"][i] if i < len(hist["price"]) else None
        if d and p is not None:
            dated_prices.append((d, p))
    if not dated_prices:
        return None, None
    # need at least one point on/after target, and today must have reached it
    on_or_after = [dp for dp in dated_prices if dp[0] >= target]
    if not on_or_after:
        return None, None
    on_or_after.sort(key=lambda x: x[0])
    return on_or_after[0][1], on_or_after[0][0]


def entry_price_from_history(hist, entry_date):
    p, d = price_at_offset(hist, entry_date, 0)
    return p


def bucket_rsi(rsi):
    if rsi is None:
        return "N/A"
    if rsi < 30:
        return "<30"
    if rsi < 45:
        return "30-45"
    if rsi < 55:
        return "45-55"
    if rsi < 70:
        return "55-70"
    return ">=70"


def main():
    if not os.path.exists(LOG_FILE):
        print(f"[INFO] No {LOG_FILE} yet - nothing to evaluate. Run log_top20_snapshot.py "
              f"at least once, then check back after 30-60 days.")
        return

    with open(LOG_FILE) as f:
        log = json.load(f)

    history = load_history_cache()
    if not history:
        print(f"[WARN] No history data found in {HISTORY_DIR}/ - cannot compute forward returns.")
        return

    bench_hist = {b: history[b] for b in BENCHMARKS if b in history}
    today = datetime.date.today()

    rows = []
    for cohort in log["cohorts"]:
        entry_date = datetime.date.fromisoformat(cohort["picks"][0]["entry_date"]) if cohort["picks"] else None
        if entry_date is None:
            continue
        days_elapsed = (today - entry_date).days

        for pick in cohort["picks"]:
            ticker = pick["ticker"]
            hist = history.get(ticker)
            entry_price = pick.get("entry_price")

            def eval_window(n_days):
                if days_elapsed < n_days or hist is None:
                    return None
                fwd_price, fwd_date = price_at_offset(hist, entry_date, n_days)
                if fwd_price is None or not entry_price:
                    return None
                ret = fwd_price / entry_price - 1
                bench_rets = []
                for b, bh in bench_hist.items():
                    b_entry = entry_price_from_history(bh, entry_date)
                    b_fwd, _ = price_at_offset(bh, entry_date, n_days)
                    if b_entry and b_fwd:
                        bench_rets.append(b_fwd / b_entry - 1)
                alpha = ret - stats.mean(bench_rets) if bench_rets else None
                return {"price": fwd_price, "date": fwd_date.isoformat() if fwd_date else None,
                        "return_pct": round(ret * 100, 2),
                        "alpha_pct": round(alpha * 100, 2) if alpha is not None else None}

            r30 = eval_window(30)
            r60 = eval_window(60)
            hit_target = None
            if r30 and pick.get("analyst_target") and entry_price:
                hit_target = r30["price"] >= pick["analyst_target"]

            rows.append({
                "month": cohort["month"],
                "ticker": ticker,
                "entry_date": pick["entry_date"],
                "entry_price": entry_price,
                "matrix_score": pick.get("matrix_score"),
                "rsi_at_entry": pick.get("rsi"),
                "rsi_bucket": bucket_rsi(pick.get("rsi")),
                "macd_trend_at_entry": pick.get("macd_trend"),
                "days_elapsed": days_elapsed,
                "return_30d_pct": r30["return_pct"] if r30 else None,
                "alpha_30d_pct": r30["alpha_pct"] if r30 else None,
                "return_60d_pct": r60["return_pct"] if r60 else None,
                "alpha_60d_pct": r60["alpha_pct"] if r60 else None,
            })

    if not rows:
        print("[INFO] No cohorts old enough to grade yet (need >=30 days since a logged snapshot).")
        return

    with open("recommendation_scorecard.json", "w") as f:
        json.dump(rows, f, indent=2)

    fieldnames = list(rows[0].keys())
    with open("recommendation_scorecard.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Graded {len(rows)} picks across {len(log['cohorts'])} cohort(s).\n")

    def summarize(key_getter, label):
        groups = {}
        for r in rows:
            k = key_getter(r)
            if r["return_30d_pct"] is not None:
                groups.setdefault(k, []).append(r)
        print(f"--- By {label} (30-day window) ---")
        for k, items in sorted(groups.items(), key=lambda kv: -stats.mean(x["return_30d_pct"] for x in kv[1])):
            rets = [x["return_30d_pct"] for x in items]
            alphas = [x["alpha_30d_pct"] for x in items if x["alpha_30d_pct"] is not None]
            win = sum(1 for x in rets if x > 0) / len(rets) * 100
            print(f"  {str(k):<20} n={len(items):>3}  avg_return={stats.mean(rets):>6.2f}%  "
                  f"win_rate={win:>5.1f}%  avg_alpha={(stats.mean(alphas) if alphas else float('nan')):>6.2f}%")
        print()

    summarize(lambda r: r["matrix_score"], "matrix_score")
    summarize(lambda r: r["rsi_bucket"], "RSI bucket at entry")
    summarize(lambda r: r["macd_trend_at_entry"], "MACD trend at entry")

    overall_30 = [r["return_30d_pct"] for r in rows if r["return_30d_pct"] is not None]
    overall_alpha_30 = [r["alpha_30d_pct"] for r in rows if r["alpha_30d_pct"] is not None]
    if overall_30:
        win = sum(1 for x in overall_30 if x > 0) / len(overall_30) * 100
        print(f"OVERALL 30-day: n={len(overall_30)}  avg_return={stats.mean(overall_30):.2f}%  "
              f"win_rate={win:.1f}%  avg_alpha={(stats.mean(overall_alpha_30) if overall_alpha_30 else float('nan')):.2f}%")

    print("\nWrote recommendation_scorecard.json and recommendation_scorecard.csv")


if __name__ == "__main__":
    main()
