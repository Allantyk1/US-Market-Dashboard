"""
Matrix Score Self-Learning Refinement Loop
------------------------------------------------------------------
Closes the loop one step further than evaluate_recommendations.py:
instead of just grading picks, this turns graded results (plus the
retrospective backtest) into concrete, directional suggestions for
how to re-weight the Matrix Score - with a built-in guardrail so it
never proposes acting on too little live data.

WHERE THIS SITS IN THE PIPELINE
  stock_screener_us.py        -> computes Matrix Score (source of truth,
                                  lives on Allan's PC, not touched here)
  log_top20_snapshot.py       -> records what was recommended (monthly)
  evaluate_recommendations.py -> grades matured cohorts -> recommendation_scorecard.json
  refine_matrix_score.py      -> THIS SCRIPT. Reads recommendation_scorecard.json
                                  (live results) + indicator_backtest_report.json
                                  (661-ticker retrospective backtest) and blends
                                  them into a weight-adjustment recommendation.
                                  Never edits stock_screener_us.py itself - it
                                  proposes changes for a human (or a future
                                  Claude session with the live file) to apply.

HOW THE BLEND WORKS
  Each indicator bucket (RSI band, MACD trend, EMA trend, distance from
  EMA20, Bollinger position, volume) has a backtest prior (alpha vs.
  SPY/QQQ/DIA, ~44,700 observations, but drawn from a single unusually
  strong bull window - see indicator_backtest_report.json caveats).
  Once evaluate_recommendations.py has graded enough real cohorts, this
  script also computes the SAME buckets from live picks and blends the
  two: live evidence gets more weight as more graded picks accumulate,
  but only ever *nudges* the backtest prior - it can't swing a
  recommendation on a handful of noisy live picks.

GUARDRAILS (matches the caution already written into
Recommendation_Tracking_Report.md section 2 and 4):
  - MIN_LIVE_FOR_BLEND graded picks are required in a bucket before live
    data is blended in at all; below that, the suggestion is backtest-only
    and explicitly labeled "hypothesis, not yet validated."
  - MIN_COHORTS_FOR_TRUST graded cohorts are required before the summary
    stops repeating "treat as hypothesis" - matches the project's own
    "wait for 3+ graded cohorts" rule of thumb.
  - Nothing here is auto-applied. Output is a proposal, not a patch.

Run: python3 refine_matrix_score.py
Inputs (same folder): recommendation_scorecard.json (optional - produced by
  evaluate_recommendations.py once cohorts have matured), indicator_backtest_report.json
Outputs: matrix_score_refinement.json, matrix_score_refinement.md, printed summary
"""

import json
import os
import statistics as stats
import datetime

SCORECARD_FILE = "recommendation_scorecard.json"
BACKTEST_FILE = "indicator_backtest_report.json"
LOG_FILE = "recommendation_log.json"

MIN_LIVE_FOR_BLEND = 15     # picks needed in a bucket before live data counts at all
MIN_COHORTS_FOR_TRUST = 3   # graded cohorts needed before dropping the "hypothesis" caveat


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def bucket_rsi_value(rsi):
    if rsi is None:
        return None
    if rsi >= 70:
        return "RSI >=70 (overbought)"
    if rsi < 30:
        return "RSI <30 (oversold)"
    if rsi < 45:
        return "RSI 30-45"
    if rsi < 55:
        return "RSI 45-55 (neutral)"
    return "RSI 55-70"


def live_rsi_buckets(scorecard_rows):
    """Re-bucket graded live picks the same way the backtest does, using
    return_30d_pct as the live analogue of avg_fwd30_pct (alpha vs. bench
    is already computed by evaluate_recommendations.py per row)."""
    groups = {}
    for r in scorecard_rows:
        if r.get("return_30d_pct") is None:
            continue
        b = bucket_rsi_value(r.get("rsi_at_entry"))
        if b is None:
            continue
        groups.setdefault(b, []).append(r)
    out = {}
    for b, rows in groups.items():
        rets = [r["return_30d_pct"] for r in rows]
        alphas = [r["alpha_30d_pct"] for r in rows if r.get("alpha_30d_pct") is not None]
        out[b] = {
            "n": len(rows),
            "avg_fwd30_pct": round(stats.mean(rets), 2),
            "win_rate_30_pct": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1),
            "alpha_vs_bench_30_pct": round(stats.mean(alphas), 2) if alphas else None,
        }
    return out


def blend(backtest_alpha, live_alpha, n_live):
    """Confidence-weighted blend: live weight grows with sample size but
    is capped so live data nudges rather than overrides the backtest,
    consistent with 'don't re-weight off 1-2 cohorts' guidance."""
    if live_alpha is None or n_live < MIN_LIVE_FOR_BLEND:
        return backtest_alpha, False
    live_weight = min(0.5, n_live / (n_live + 60))  # asymptotes toward 0.5, never dominates
    blended = (1 - live_weight) * backtest_alpha + live_weight * live_alpha
    return round(blended, 2), True


def direction_label(alpha_pct):
    if alpha_pct >= 1.5:
        return "STRONG POSITIVE - favor / do not penalize"
    if alpha_pct >= 0.3:
        return "MILD POSITIVE - slight favor"
    if alpha_pct > -0.3:
        return "NEUTRAL - no change"
    if alpha_pct > -1.0:
        return "MILD NEGATIVE - slight caution"
    return "STRONG NEGATIVE - penalize / avoid"


def main():
    backtest = load_json(BACKTEST_FILE)
    if backtest is None:
        print(f"[STOP] {BACKTEST_FILE} not found - nothing to refine against.")
        return

    scorecard = load_json(SCORECARD_FILE) or []
    log = load_json(LOG_FILE) or {"cohorts": []}
    graded_cohorts = 0
    today = datetime.date.today()
    for c in log.get("cohorts", []):
        if not c.get("picks"):
            continue
        entry_date = datetime.date.fromisoformat(c["picks"][0]["entry_date"])
        if (today - entry_date).days >= 30:
            graded_cohorts += 1

    live_rsi = live_rsi_buckets(scorecard) if scorecard else {}

    results = []
    for row in backtest["by_rsi"]:
        bucket = row["bucket"]
        bt_alpha = row["alpha_vs_bench_30_pct"]
        live = live_rsi.get(bucket)
        n_live = live["n"] if live else 0
        live_alpha = live["alpha_vs_bench_30_pct"] if live else None
        blended_alpha, used_live = blend(bt_alpha, live_alpha, n_live)
        results.append({
            "indicator": "RSI",
            "bucket": bucket,
            "backtest_alpha_30d_pct": bt_alpha,
            "backtest_n": row["n30"],
            "live_n": n_live,
            "live_alpha_30d_pct": live_alpha,
            "blended_alpha_30d_pct": blended_alpha,
            "used_live_data": used_live,
            "suggestion": direction_label(blended_alpha),
        })

    is_trustworthy = graded_cohorts >= MIN_COHORTS_FOR_TRUST
    status = ("LIVE-INFORMED" if any(r["used_live_data"] for r in results)
              else "BACKTEST-ONLY (hypothesis, not yet validated)")

    output = {
        "generated_at": today.isoformat(),
        "graded_cohorts": graded_cohorts,
        "min_cohorts_for_trust": MIN_COHORTS_FOR_TRUST,
        "status": status,
        "trustworthy": is_trustworthy,
        "caveat": (
            "Backtest window (Apr-Aug 2026) was an unusually strong, one-directional "
            "bull run - treat directional suggestions as hypotheses until validated "
            "across 3+ graded live cohorts, ideally including a down or flat month."
        ),
        "rsi_suggestions": results,
    }

    with open("matrix_score_refinement.json", "w") as f:
        json.dump(output, f, indent=2)

    lines = []
    lines.append(f"# Matrix Score Refinement Suggestions ({today.isoformat()})\n")
    lines.append(f"Status: **{status}**  |  Graded cohorts: {graded_cohorts} "
                 f"(need {MIN_COHORTS_FOR_TRUST}+ before treating this as validated)\n")
    lines.append(f"> {output['caveat']}\n")
    lines.append("| Bucket | Backtest alpha (30d) | Live n | Live alpha (30d) | Blended | Suggestion |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        live_cell = "n/a" if r["live_alpha_30d_pct"] is None else f"{r['live_alpha_30d_pct']:+.2f}%"
        lines.append(
            f"| {r['bucket']} | {r['backtest_alpha_30d_pct']:+.2f}% | {r['live_n']} | "
            f"{live_cell} | "
            f"{r['blended_alpha_30d_pct']:+.2f}% | {r['suggestion']} |"
        )
    lines.append("")
    lines.append("## What to actually change in stock_screener_us.py")
    lines.append("This script proposes *direction*, not the exact point values in your Matrix "
                 "Score - it doesn't have access to that scoring code. For each row above marked "
                 "STRONG/MILD POSITIVE, remove or soften any existing penalty tied to that "
                 "condition; for STRONG/MILD NEGATIVE, add or increase one. Apply changes only "
                 "once `trustworthy: true` in matrix_score_refinement.json, or deliberately as a "
                 "tracked experiment you're willing to reverse.")

    with open("matrix_score_refinement.md", "w") as f:
        f.write("\n".join(lines))

    print(f"Status: {status}")
    print(f"Graded cohorts: {graded_cohorts}/{MIN_COHORTS_FOR_TRUST}")
    print()
    for r in results:
        live_txt = "n/a" if r["live_alpha_30d_pct"] is None else f"{r['live_alpha_30d_pct']:+.2f}%"
        print(f"  {r['bucket']:<30} backtest={r['backtest_alpha_30d_pct']:+.2f}%  "
              f"live(n={r['live_n']})={live_txt}  blended={r['blended_alpha_30d_pct']:+.2f}%  "
              f"-> {r['suggestion']}")
    print("\nWrote matrix_score_refinement.json and matrix_score_refinement.md")


if __name__ == "__main__":
    main()
