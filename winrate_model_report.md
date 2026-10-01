# Win-Rate Model Report (2026-10-01)

**Status: win: NO EDGE OVER RSI RULE YET (experimental) | beat: PROMISING (beats RSI rule on unseen data; waiting for 3+ graded cohorts)**  |  Graded live cohorts: 1 (need 3+)  |  Training data: 2021-10-05 to 2026-09-29, 665 tickers, 163,728 samples

> How to read this: AUC measures how well a score ranks winners above losers (0.50 = coin flip, 0.55 is a useful edge for 30-day stock moves, above 0.60 would be unusually strong). **Same-day AUC** compares stocks against each other on the same day - that is what choosing a Top 20 does, so it is the number the model is judged on. Overall AUC also rewards guessing whether the whole market goes up. Every number below comes from quarters the model had NOT seen when it was trained.

## Up after ~30 days (return > 0)

Chosen model: **gbm**. Beats the RSI-bucket rule on unseen quarters: **NO**.

| Score used to rank | Same-day AUC | Overall AUC | Top-20 hit rate | Top-20 avg 30d return |
|---|---|---|---|---|
| RSI-bucket rule (what refine_matrix_score.py uses) | 0.507 | 0.509 | 53.8% | +2.04% |
| Model: logistic | 0.506 | 0.504 | 56.6% | +1.83% |
| Model: gbm (chosen) | 0.512 | 0.498 | 56.0% | +1.28% |
| Whole universe (no selection) | 0.500 | 0.500 | 53.4% | +1.75% |

Per quarter (test period only):

| Quarter | Market | Base rate | RSI rule same-day AUC | gbm same-day AUC | gbm Top-20 hit |
|---|---|---|---|---|---|
| 2024Q4 | SPY 1-month return avg +2.6% | 48.7% | 0.512 | 0.492 | 44.2% |
| 2025Q1 | SPY 1-month return avg -2.0% | 40.2% | 0.499 | 0.502 | 40.4% |
| 2025Q2 | SPY 1-month return avg +1.9% | 71.1% | 0.508 | 0.546 | 76.5% |
| 2025Q3 | SPY 1-month return avg +3.0% | 56.5% | 0.507 | 0.503 | 64.6% |
| 2025Q4 | SPY 1-month return avg +1.2% | 55.5% | 0.509 | 0.531 | 61.7% |
| 2026Q1 | SPY 1-month return avg -0.6% | 49.9% | 0.500 | 0.530 | 49.6% |
| 2026Q2 | SPY 1-month return avg +5.0% | 57.3% | 0.512 | 0.524 | 57.9% |
| 2026Q3 | SPY 1-month return avg +2.0% | 48.3% | 0.509 | 0.467 | 53.1% |

Calibration (does the probability mean what it says?):

| Predicted | Actual | n |
|---|---|---|
| 49.9% | 52.7% | 12,611 |
| 52.3% | 53.5% | 12,611 |
| 53.6% | 51.5% | 12,611 |
| 55.0% | 50.2% | 12,611 |
| 58.5% | 61.4% | 12,611 |

What the model leans on most (drop in AUC when the feature is scrambled):

- Market: SPY vs 200-day SMA: +0.0509
- Market: SPY volatility: +0.0443
- Market: SPY 1-month return: +0.0276
- Volatility (ATR %): +0.0190
- 3-month return: +0.0047
- Share price level: +0.0036
- Distance from 52-week high: +0.0029
- MACD histogram slope: +0.0024
- RSI(14): +0.0022
- MACD histogram: +0.0008

## Beat SPY/QQQ/DIA average after ~30 days

Chosen model: **gbm**. Beats the RSI-bucket rule on unseen quarters: **YES**.

| Score used to rank | Same-day AUC | Overall AUC | Top-20 hit rate | Top-20 avg 30d return |
|---|---|---|---|---|
| RSI-bucket rule (what refine_matrix_score.py uses) | 0.512 | 0.513 | 43.3% | +1.35% |
| Model: logistic | 0.497 | 0.484 | 47.7% | +2.35% |
| Model: gbm (chosen) | 0.523 | 0.493 | 51.3% | +4.12% |
| Whole universe (no selection) | 0.500 | 0.500 | 46.0% | +1.75% |

Per quarter (test period only):

| Quarter | Market | Base rate | RSI rule same-day AUC | gbm same-day AUC | gbm Top-20 hit |
|---|---|---|---|---|---|
| 2024Q4 | SPY 1-month return avg +2.6% | 43.2% | 0.513 | 0.550 | 53.3% |
| 2025Q1 | SPY 1-month return avg -2.0% | 50.4% | 0.526 | 0.423 | 33.3% |
| 2025Q2 | SPY 1-month return avg +1.9% | 44.9% | 0.505 | 0.575 | 60.0% |
| 2025Q3 | SPY 1-month return avg +3.0% | 43.2% | 0.515 | 0.516 | 57.3% |
| 2025Q4 | SPY 1-month return avg +1.2% | 48.0% | 0.500 | 0.542 | 51.3% |
| 2026Q1 | SPY 1-month return avg -0.6% | 47.4% | 0.520 | 0.520 | 60.0% |
| 2026Q2 | SPY 1-month return avg +5.0% | 46.3% | 0.518 | 0.508 | 42.1% |
| 2026Q3 | SPY 1-month return avg +2.0% | 44.8% | 0.503 | 0.552 | 53.1% |

Calibration (does the probability mean what it says?):

| Predicted | Actual | n |
|---|---|---|
| 45.6% | 44.6% | 12,611 |
| 45.9% | 45.6% | 12,611 |
| 46.1% | 46.5% | 12,611 |
| 46.2% | 47.2% | 12,611 |
| 46.5% | 46.4% | 12,611 |

What the model leans on most (drop in AUC when the feature is scrambled):

- Market: SPY vs 200-day SMA: +0.0430
- Market: SPY 1-month return: +0.0333
- Market: SPY volatility: +0.0300
- Volatility (ATR %): +0.0177
- Distance from 200-day SMA: +0.0112
- MACD histogram slope: +0.0081
- Distance from 52-week high: +0.0069
- RSI(14): +0.0053
- Share price level: +0.0038
- 3-month return: +0.0038

## Logged Top-20 picks, scored as of their entry date

20 graded picks. Model's average predicted chance of being up: 56.0%; actually up: 55.0%. Model AUC on these picks: 0.677; Matrix Score AUC: 0.571. With this few picks these numbers are noise, not evidence - they become meaningful after a few more cohorts.

Picks the model rated in the top half: 72.7% were up; bottom half: 33.3%.

| Cohort | Ticker | Matrix Score | Model win prob | 30d return | Up? |
|---|---|---|---|---|---|
| 2026-08 | ADI | 3 | 55.5% | +3.76% | yes |
| 2026-08 | AFL | 3 | 54.5% | -0.85% | no |
| 2026-08 | AMKR | 3 | 57.1% | +2.80% | yes |
| 2026-08 | AVGO | 3 | 54.9% | -0.19% | no |
| 2026-08 | CBOE | 3 | 54.4% | -7.46% | no |
| 2026-08 | COHR | 3 | 57.3% | +10.86% | yes |
| 2026-08 | DIOD | 3 | 57.9% | -0.01% | no |
| 2026-08 | GL | 3 | 54.4% | +0.98% | yes |
| 2026-08 | KEYS | 4 | 55.6% | +8.06% | yes |
| 2026-08 | LOW | 3 | 55.3% | -12.13% | no |
| 2026-08 | NBIS | 3 | 58.0% | +5.77% | yes |
| 2026-08 | NTES | 3 | 54.8% | -1.56% | no |
| 2026-08 | ONTO | 3 | 58.0% | -7.65% | no |
| 2026-08 | RMBS | 3 | 56.4% | +5.22% | yes |
| 2026-08 | SITM | 3 | 57.5% | +6.60% | yes |
| 2026-08 | STLD | 4 | 55.2% | +5.02% | yes |
| 2026-08 | STX | 5 | 58.3% | +3.19% | yes |
| 2026-08 | TRV | 3 | 54.8% | +2.17% | yes |
| 2026-08 | TYL | 3 | 55.5% | -4.50% | no |
| 2026-08 | WYNN | 5 | 55.1% | -16.35% | no |
| 2026-09 | APTV | 2 | 56.4% | pending |  |
| 2026-09 | AVL | 2 | 58.5% | pending |  |
| 2026-09 | AXON | 2 | 56.1% | pending |  |
| 2026-09 | BAX | 2 | 55.5% | pending |  |
| 2026-09 | BIDU | 2 | 56.4% | pending |  |
| 2026-09 | CLX | 2 | 56.1% | pending |  |
| 2026-09 | COO | 2 | 56.1% | pending |  |
| 2026-09 | CPB | 3 | 56.1% | pending |  |
| 2026-09 | CRUS | 2 | 56.4% | pending |  |
| 2026-09 | GEHC | 2 | 56.1% | pending |  |
| 2026-09 | HPE | 2 | 55.5% | pending |  |
| 2026-09 | HSY | 2 | 55.8% | pending |  |
| 2026-09 | MDB | 2 | 55.8% | pending |  |
| 2026-09 | NET | 2 | 56.2% | pending |  |
| 2026-09 | ON | 2 | 56.5% | pending |  |
| 2026-09 | PYPL | 2 | 56.8% | pending |  |
| 2026-09 | QLYS | 2 | 56.0% | pending |  |
| 2026-09 | RARE | 2 | 57.9% | pending |  |
| 2026-09 | RPD | 2 | 58.6% | pending |  |
| 2026-09 | TENB | 2 | 57.2% | pending |  |

## What to do with this

- Show `win_prob_30d_pct` next to the Matrix Score as a second opinion; don't let it override the score until status is TRUSTED.
- If the chosen model does NOT beat the RSI rule, the honest conclusion is that these indicators carry little extra 30-day edge beyond what the rule captures.
- The 30-day returns here are measured over 21 trading days, which can differ slightly from the scorecard's calendar-day window.
- Not financial advice.