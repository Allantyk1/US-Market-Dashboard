"""
Scorecard summary - plain-English recap of graded Top-20 picks.
------------------------------------------------------------------
Reads recommendation_scorecard.json (written by evaluate_recommendations.py)
and prints, for each graded monthly batch:
  - how many picks went up vs how much they made on average
  - average size of a win vs average size of a loss
  - the biggest winners and the worst losers
  - a guard-rail callout when "how often right" and "how much made"
    point in opposite directions, or when losers fell 15%+

Run as the last step of monthly_model_review.bat (or on its own):
    python scorecard_summary.py
Output: printed summary + scorecard_summary.md (same folder).
Added 2026-10-24.
"""

import json
import os
import statistics as stats
import datetime

SCORECARD_FILE = "recommendation_scorecard.json"
OUT_MD = "scorecard_summary.md"
BIG_MOVE_PCT = 15.0     # a pick moving this much (either way) is called out
TOP_N = 3               # winners / losers listed per batch

GUARD_RAIL = (
    "What you earn depends on HOW MUCH you win compared with HOW MUCH you lose, "
    "not just how often you are right. A batch can be right more often and still "
    "lose money (a few big losers), or be right less often and still make money "
    "(a few big winners). Cutting losers early - before they fall 15-20% - is what "
    "protects the average. That is what the 3x ATR stop-loss line is for."
)


def fmt(x):
    return f"{x:+.1f}%"


def summarise(rows):
    rets = [r["return_30d_pct"] for r in rows]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    alphas = [r["alpha_30d_pct"] for r in rows if r.get("alpha_30d_pct") is not None]
    s = {
        "n": len(rets),
        "up_pct": 100.0 * len(wins) / len(rets),
        "avg": stats.mean(rets),
        "n_win": len(wins),
        "n_loss": len(losses),
        "avg_win": stats.mean(wins) if wins else 0.0,
        "avg_loss": stats.mean(losses) if losses else 0.0,
        "beat_pct": (100.0 * sum(1 for a in alphas if a > 0) / len(alphas)) if alphas else None,
    }
    s["payoff"] = (s["avg_win"] / abs(s["avg_loss"])) if s["avg_loss"] else None
    ranked = sorted(rows, key=lambda r: r["return_30d_pct"], reverse=True)
    s["top"] = [r for r in ranked[:TOP_N] if r["return_30d_pct"] > 0]
    s["worst"] = [r for r in reversed(ranked[-TOP_N:]) if r["return_30d_pct"] <= 0]
    s["big_losers"] = [r for r in rows if r["return_30d_pct"] <= -BIG_MOVE_PCT]
    s["big_winners"] = [r for r in rows if r["return_30d_pct"] >= BIG_MOVE_PCT]
    return s


def callouts(label, s):
    out = []
    if s["up_pct"] >= 50 and s["avg"] < 0:
        out.append(f"{label}: RIGHT MORE OFTEN BUT LOST MONEY - {s['up_pct']:.0f}% went up, "
                   f"yet the average was {fmt(s['avg'])} because losses "
                   f"(avg {fmt(s['avg_loss'])}) were bigger than wins (avg {fmt(s['avg_win'])}).")
    elif s["up_pct"] < 50 and s["avg"] > 0:
        out.append(f"{label}: RIGHT LESS OFTEN BUT MADE MONEY - only {s['up_pct']:.0f}% went up, "
                   f"yet the average was {fmt(s['avg'])} because a few big winners "
                   f"(avg win {fmt(s['avg_win'])}) outweighed the losers (avg {fmt(s['avg_loss'])}).")
    if s["big_losers"]:
        names = ", ".join(f"{r['ticker']} {fmt(r['return_30d_pct'])}" for r in
                          sorted(s["big_losers"], key=lambda r: r["return_30d_pct"]))
        out.append(f"{label}: {len(s['big_losers'])} pick(s) fell {BIG_MOVE_PCT:.0f}%+ ({names}) "
                   f"- a stop-loss would have capped these.")
    return out


def batch_lines(label, s):
    payoff = f"{s['payoff']:.1f}x" if s["payoff"] is not None else "n/a"
    beat = f"{s['beat_pct']:.0f}%" if s["beat_pct"] is not None else "n/a"
    lines = [
        f"{label}: {s['n']} picks | went up {s['up_pct']:.0f}% ({s['n_win']} up / {s['n_loss']} down) "
        f"| avg 30d return {fmt(s['avg'])} | beat market {beat}",
        f"    avg win {fmt(s['avg_win'])}  vs  avg loss {fmt(s['avg_loss'])}  "
        f"(win size / loss size = {payoff})",
    ]
    if s["top"]:
        lines.append("    Big winners : " + ", ".join(f"{r['ticker']} {fmt(r['return_30d_pct'])}" for r in s["top"]))
    if s["worst"]:
        lines.append("    Worst losers: " + ", ".join(f"{r['ticker']} {fmt(r['return_30d_pct'])}" for r in s["worst"]))
    return lines


def main():
    if not os.path.exists(SCORECARD_FILE):
        print(f"[SKIP] {SCORECARD_FILE} not found - no graded picks yet.")
        return
    with open(SCORECARD_FILE) as f:
        data = json.load(f)
    rows = data if isinstance(data, list) else data.get("rows", [])
    graded = [r for r in rows if r.get("return_30d_pct") is not None]
    pending = sorted({r.get("month") for r in rows if r.get("return_30d_pct") is None})
    if not graded:
        print("[SKIP] No picks have reached 30 days yet.")
        return

    months = sorted({r["month"] for r in graded})
    report, alerts = [], []
    for m in months:
        s = summarise([r for r in graded if r["month"] == m])
        report += batch_lines(m, s)
        alerts += callouts(m, s)
    overall = summarise(graded)
    report += batch_lines("ALL", overall)

    if overall["big_winners"]:
        stars = ", ".join(f"{r['ticker']} ({r['month']}) {fmt(r['return_30d_pct'])}" for r in
                          sorted(overall["big_winners"], key=lambda r: r["return_30d_pct"], reverse=True))
        alerts.insert(0, f"BIG WINNERS ({BIG_MOVE_PCT:.0f}%+ in 30 days): {stars}")

    today = datetime.date.today().isoformat()
    print()
    print("=" * 78)
    print(f" SCORECARD SUMMARY ({today}) - graded Top-20 picks, 30-day results")
    print("=" * 78)
    for line in report:
        print(line)
    if pending:
        print(f"\nStill pending (under 30 days): {', '.join(pending)}")
    if alerts:
        print("\n--- CALL-OUTS ---")
        for a in alerts:
            print(" * " + a)
    print("\n--- GUARD RAIL ---")
    print(" " + GUARD_RAIL)
    print("=" * 78)

    md = [f"# Scorecard Summary ({today})", "",
          "30-day results of graded Top-20 picks. Generated by scorecard_summary.py.", "",
          "| Batch | Picks | Went up | Avg 30d return | Avg win | Avg loss | Beat market |",
          "|---|---|---|---|---|---|---|"]
    for m in months + ["ALL"]:
        s = overall if m == "ALL" else summarise([r for r in graded if r["month"] == m])
        beat = f"{s['beat_pct']:.0f}%" if s["beat_pct"] is not None else "n/a"
        md.append(f"| {m} | {s['n']} | {s['up_pct']:.0f}% | {fmt(s['avg'])} | "
                  f"{fmt(s['avg_win'])} | {fmt(s['avg_loss'])} | {beat} |")
    md += ["", "## Biggest movers", "", "| Batch | Big winners | Worst losers |", "|---|---|---|"]
    for m in months:
        s = summarise([r for r in graded if r["month"] == m])
        w = ", ".join(f"{r['ticker']} {fmt(r['return_30d_pct'])}" for r in s["top"]) or "-"
        l = ", ".join(f"{r['ticker']} {fmt(r['return_30d_pct'])}" for r in s["worst"]) or "-"
        md.append(f"| {m} | {w} | {l} |")
    if pending:
        md += ["", f"Still pending (under 30 days): {', '.join(pending)}"]
    if alerts:
        md += ["", "## Call-outs", ""] + [f"- {a}" for a in alerts]
    md += ["", "## Guard rail", "", f"> {GUARD_RAIL}", "", "Not financial advice."]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
