# Win-Rate Model Report (2026-10-24)

**Status: win: NO EDGE OVER RSI RULE YET (experimental) | beat: NO EDGE OVER RSI RULE YET (experimental)**  |  Graded live cohorts: 2 (need 3+)  |  Training data: 2021-10-01 to 2026-10-23, 671 tickers, 167,661 samples

> How to read this: AUC measures how well a score ranks winners above losers (0.50 = coin flip, 0.55 is a useful edge for 30-day stock moves, above 0.60 would be unusually strong). **Same-day AUC** compares stocks against each other on the same day - that is what choosing a Top 20 does, so it is the number the model is judged on. Overall AUC also rewards guessing whether the whole market goes up. Every number below comes from quarters the model had NOT seen when it was trained.

## Up after ~30 days (return > 0)

Chosen model: **gbm**. Beats the RSI-bucket rule on unseen quarters: **NO**.

| Score used to rank | Same-day AUC | Overall AUC | Top-20 hit rate | Top-20 avg 30d return |
|---|---|---|---|---|
| RSI-bucket rule (what refine_matrix_score.py uses) | 0.510 | 0.518 | 53.3% | +2.49% |
| Model: logistic | 0.508 | 0.528 | 55.6% | +1.45% |
| Model: gbm (chosen) | 0.511 | 0.488 | 56.7% | +0.88% |
| Whole universe (no selection) | 0.500 | 0.500 | 53.2% | +1.66% |

Per quarter (test period only):

| Quarter | Market | Base rate | RSI rule same-day AUC | gbm same-day AUC | gbm Top-20 hit |
|---|---|---|---|---|---|
| 2024Q4 | SPY 1-month return avg +2.2% | 51.4% | 0.505 | 0.458 | 46.5% |
| 2025Q1 | SPY 1-month return avg -1.4% | 35.8% | 0.489 | 0.510 | 40.4% |
| 2025Q2 | SPY 1-month return avg +2.3% | 72.8% | 0.518 | 0.529 | 73.8% |
| 2025Q3 | SPY 1-month return avg +3.0% | 57.3% | 0.496 | 0.494 | 69.6% |
| 2025Q4 | SPY 1-month return avg +1.4% | 54.9% | 0.503 | 0.519 | 65.4% |
| 2026Q1 | SPY 1-month return avg -1.6% | 50.0% | 0.535 | 0.525 | 46.7% |
| 2026Q2 | SPY 1-month return avg +4.3% | 55.7% | 0.513 | 0.554 | 60.8% |
| 2026Q3 | SPY 1-month return avg +1.1% | 47.4% | 0.519 | 0.495 | 50.0% |

Calibration (does the probability mean what it says?):

| Predicted | Actual | n |
|---|---|---|
| 44.6% | 44.5% | 13,251 |
| 50.1% | 53.9% | 13,250 |
| 52.9% | 54.6% | 13,250 |
| 55.8% | 52.2% | 13,250 |
| 62.7% | 60.8% | 13,250 |

What the model leans on most (drop in AUC when the feature is scrambled):

- Market: SPY volatility: +0.0576
- Market: SPY 1-month return: +0.0385
- Market: SPY vs 200-day SMA: +0.0370
- Volatility (ATR %): +0.0256
- Share price level: +0.0028
- 3-month return: +0.0025
- Distance from 52-week high: +0.0020
- Volume vs 200-day avg: +0.0017
- RSI(14): +0.0014
- Dollar volume (liquidity): +0.0013

## Beat SPY/QQQ/DIA average after ~30 days

Chosen model: **logistic**. Beats the RSI-bucket rule on unseen quarters: **NO**.

| Score used to rank | Same-day AUC | Overall AUC | Top-20 hit rate | Top-20 avg 30d return |
|---|---|---|---|---|
| RSI-bucket rule (what refine_matrix_score.py uses) | 0.511 | 0.510 | 45.6% | +2.00% |
| Model: logistic (chosen) | 0.495 | 0.491 | 47.3% | +1.86% |
| Model: gbm | 0.494 | 0.477 | 46.4% | +2.27% |
| Whole universe (no selection) | 0.500 | 0.500 | 45.9% | +1.66% |

Per quarter (test period only):

| Quarter | Market | Base rate | RSI rule same-day AUC | logistic same-day AUC | logistic Top-20 hit |
|---|---|---|---|---|---|
| 2024Q4 | SPY 1-month return avg +2.2% | 44.4% | 0.507 | 0.448 | 43.5% |
| 2025Q1 | SPY 1-month return avg -1.4% | 51.3% | 0.524 | 0.625 | 62.5% |
| 2025Q2 | SPY 1-month return avg +2.3% | 44.5% | 0.509 | 0.396 | 37.5% |
| 2025Q3 | SPY 1-month return avg +3.0% | 43.4% | 0.499 | 0.438 | 44.6% |
| 2025Q4 | SPY 1-month return avg +1.4% | 46.8% | 0.511 | 0.475 | 45.4% |
| 2026Q1 | SPY 1-month return avg -1.6% | 48.8% | 0.516 | 0.539 | 46.7% |
| 2026Q2 | SPY 1-month return avg +4.3% | 45.2% | 0.520 | 0.518 | 50.4% |
| 2026Q3 | SPY 1-month return avg +1.1% | 42.8% | 0.501 | 0.518 | 47.5% |

Calibration (does the probability mean what it says?):

| Predicted | Actual | n |
|---|---|---|
| 44.5% | 43.1% | 13,251 |
| 45.4% | 46.4% | 13,250 |
| 45.9% | 47.6% | 13,250 |
| 46.3% | 46.4% | 13,250 |
| 47.3% | 46.0% | 13,250 |

What the model leans on most (drop in AUC when the feature is scrambled):

- Distance from EMA20: +0.0254
- Distance from 200-day SMA: +0.0193
- Distance from 52-week high: +0.0193
- 5-day return: +0.0107
- Market: SPY vs 200-day SMA: +0.0097
- 3-month return: +0.0095
- KDJ D: +0.0091
- 1-month return: +0.0086
- Volume vs 200-day avg: +0.0073
- Share price level: +0.0072

## Logged Top-20 picks, scored as of their entry date

40 graded picks. Model's average predicted chance of being up: 58.9%; actually up: 47.5%. Model AUC on these picks: 0.665; Matrix Score AUC: 0.573. With this few picks these numbers are noise, not evidence - they become meaningful after a few more cohorts.

Picks the model rated in the top half: 60.0% were up; bottom half: 35.0%.

| Cohort | Ticker | Matrix Score | Model win prob | 30d return | Up? |
|---|---|---|---|---|---|
| 2026-08 | ADI | 3 | 55.8% | +3.76% | yes |
| 2026-08 | AFL | 3 | 53.2% | -0.85% | no |
| 2026-08 | AMKR | 3 | 59.9% | +2.80% | yes |
| 2026-08 | AVGO | 3 | 57.0% | -0.19% | no |
| 2026-08 | CBOE | 3 | 53.5% | -7.46% | no |
| 2026-08 | COHR | 3 | 61.6% | +10.86% | yes |
| 2026-08 | DIOD | 3 | 60.3% | -0.01% | no |
| 2026-08 | GL | 3 | 54.0% | +0.78% | yes |
| 2026-08 | KEYS | 4 | 55.7% | +8.06% | yes |
| 2026-08 | LOW | 3 | 53.5% | -12.13% | no |
| 2026-08 | NBIS | 3 | 61.5% | +5.77% | yes |
| 2026-08 | NTES | 3 | 53.4% | -1.56% | no |
| 2026-08 | ONTO | 3 | 61.4% | -7.65% | no |
| 2026-08 | RMBS | 3 | 59.8% | +5.22% | yes |
| 2026-08 | SITM | 3 | 61.9% | +6.60% | yes |
| 2026-08 | STLD | 4 | 55.7% | +4.77% | yes |
| 2026-08 | STX | 5 | 62.2% | +3.19% | yes |
| 2026-08 | TRV | 3 | 54.9% | +2.17% | yes |
| 2026-08 | TYL | 3 | 56.4% | -4.50% | no |
| 2026-08 | WYNN | 5 | 53.2% | -16.35% | no |
| 2026-09 | APTV | 2 | 60.0% | -5.67% | no |
| 2026-09 | AVL | 2 | 64.0% | +1.61% | yes |
| 2026-09 | AXON | 2 | 60.8% | -23.48% | no |
| 2026-09 | BAX | 2 | 59.2% | -5.28% | no |
| 2026-09 | BIDU | 2 | 59.9% | -8.99% | no |
| 2026-09 | CLX | 2 | 58.6% | -13.02% | no |
| 2026-09 | COO | 2 | 59.4% | -19.63% | no |
| 2026-09 | CPB | 3 | 59.8% | -12.79% | no |
| 2026-09 | CRUS | 2 | 59.6% | +7.27% | yes |
| 2026-09 | GEHC | 2 | 58.8% | -5.76% | no |
| 2026-09 | HPE | 2 | 58.4% | +25.89% | yes |
| 2026-09 | HSY | 2 | 59.0% | -8.03% | no |
| 2026-09 | MDB | 2 | 61.1% | -6.56% | no |
| 2026-09 | NET | 2 | 60.2% | +26.35% | yes |
| 2026-09 | ON | 2 | 61.0% | +16.67% | yes |
| 2026-09 | PYPL | 2 | 59.4% | -4.00% | no |
| 2026-09 | QLYS | 2 | 60.0% | +13.95% | yes |
| 2026-09 | RARE | 2 | 63.2% | -2.36% | no |
| 2026-09 | RPD | 2 | 64.1% | +7.76% | yes |
| 2026-09 | TENB | 2 | 64.4% | +11.66% | yes |
| 2026-10 | AKAM | 3 | 50.7% | pending |  |
| 2026-10 | APTV | 4 | 50.5% | pending |  |
| 2026-10 | CMI | 4 | 51.1% | pending |  |
| 2026-10 | COO | 3 | 50.1% | pending |  |
| 2026-10 | DECK | 3 | 50.0% | pending |  |
| 2026-10 | FAST | 4 | 50.1% | pending |  |
| 2026-10 | HOOD | 3 | 51.1% | pending |  |
| 2026-10 | HRL | 4 | 50.3% | pending |  |
| 2026-10 | J | 3 | 51.0% | pending |  |
| 2026-10 | LULU | 3 | 50.1% | pending |  |
| 2026-10 | MDT | 4 | 50.8% | pending |  |
| 2026-10 | NCLH | 3 | 50.0% | pending |  |
| 2026-10 | NVR | 3 | 51.6% | pending |  |
| 2026-10 | PCG | 4 | 50.4% | pending |  |
| 2026-10 | PHM | 3 | 50.4% | pending |  |
| 2026-10 | PSKY | 3 | 50.5% | pending |  |
| 2026-10 | UDR | 4 | 49.7% | pending |  |
| 2026-10 | VIPS | 3 | 51.2% | pending |  |
| 2026-10 | WETO | 4 | 55.5% | pending |  |
| 2026-10 | XLV | 4 | 49.9% | pending |  |

## What to do with this

- Show `win_prob_30d_pct` next to the Matrix Score as a second opinion; don't let it override the score until status is TRUSTED.
- If the chosen model does NOT beat the RSI rule, the honest conclusion is that these indicators carry little extra 30-day edge beyond what the rule captures.
- The 30-day returns here are measured over 21 trading days, which can differ slightly from the scorecard's calendar-day window.
- Not financial advice.