# Win-Rate Model Report (2026-10-06)

**Status: win: NO EDGE OVER RSI RULE YET (experimental) | beat: NO EDGE OVER RSI RULE YET (experimental)**  |  Graded live cohorts: 2 (need 3+)  |  Training data: 2021-10-04 to 2026-10-05, 668 tickers, 165,140 samples

> How to read this: AUC measures how well a score ranks winners above losers (0.50 = coin flip, 0.55 is a useful edge for 30-day stock moves, above 0.60 would be unusually strong). **Same-day AUC** compares stocks against each other on the same day - that is what choosing a Top 20 does, so it is the number the model is judged on. Overall AUC also rewards guessing whether the whole market goes up. Every number below comes from quarters the model had NOT seen when it was trained.

## Up after ~30 days (return > 0)

Chosen model: **gbm**. Beats the RSI-bucket rule on unseen quarters: **NO**.

| Score used to rank | Same-day AUC | Overall AUC | Top-20 hit rate | Top-20 avg 30d return |
|---|---|---|---|---|
| RSI-bucket rule (what refine_matrix_score.py uses) | 0.513 | 0.514 | 54.1% | +1.59% |
| Model: logistic | 0.513 | 0.528 | 56.1% | +1.74% |
| Model: gbm (chosen) | 0.520 | 0.512 | 58.2% | +2.03% |
| Whole universe (no selection) | 0.500 | 0.500 | 52.9% | +1.63% |

Per quarter (test period only):

| Quarter | Market | Base rate | RSI rule same-day AUC | gbm same-day AUC | gbm Top-20 hit |
|---|---|---|---|---|---|
| 2024Q4 | SPY 1-month return avg +2.2% | 49.6% | 0.518 | 0.501 | 50.4% |
| 2025Q1 | SPY 1-month return avg -2.0% | 34.5% | 0.499 | 0.535 | 39.2% |
| 2025Q2 | SPY 1-month return avg +2.7% | 72.3% | 0.515 | 0.545 | 75.8% |
| 2025Q3 | SPY 1-month return avg +3.1% | 55.4% | 0.513 | 0.507 | 64.2% |
| 2025Q4 | SPY 1-month return avg +1.8% | 55.8% | 0.512 | 0.557 | 67.3% |
| 2026Q1 | SPY 1-month return avg -1.1% | 49.5% | 0.518 | 0.526 | 51.7% |
| 2026Q2 | SPY 1-month return avg +4.3% | 59.0% | 0.515 | 0.549 | 71.7% |
| 2026Q3 | SPY 1-month return avg +1.7% | 47.0% | 0.514 | 0.438 | 45.6% |

Calibration (does the probability mean what it says?):

| Predicted | Actual | n |
|---|---|---|
| 47.8% | 50.5% | 12,802 |
| 50.8% | 49.5% | 12,802 |
| 52.5% | 48.4% | 12,800 |
| 54.9% | 57.7% | 12,801 |
| 59.5% | 59.4% | 12,802 |

What the model leans on most (drop in AUC when the feature is scrambled):

- Market: SPY vs 200-day SMA: +0.0646
- Market: SPY 1-month return: +0.0406
- Market: SPY volatility: +0.0330
- Volatility (ATR %): +0.0133
- Distance from 52-week high: +0.0071
- 3-month return: +0.0031
- MACD histogram slope: +0.0027
- Volume vs 200-day avg: +0.0020
- RSI(14): +0.0018
- Bollinger position: +0.0017

## Beat SPY/QQQ/DIA average after ~30 days

Chosen model: **gbm**. Beats the RSI-bucket rule on unseen quarters: **NO**.

| Score used to rank | Same-day AUC | Overall AUC | Top-20 hit rate | Top-20 avg 30d return |
|---|---|---|---|---|
| RSI-bucket rule (what refine_matrix_score.py uses) | 0.511 | 0.510 | 46.8% | +1.85% |
| Model: logistic | 0.505 | 0.502 | 48.3% | +2.38% |
| Model: gbm (chosen) | 0.518 | 0.497 | 50.5% | +4.24% |
| Whole universe (no selection) | 0.500 | 0.500 | 45.9% | +1.63% |

Per quarter (test period only):

| Quarter | Market | Base rate | RSI rule same-day AUC | gbm same-day AUC | gbm Top-20 hit |
|---|---|---|---|---|---|
| 2024Q4 | SPY 1-month return avg +2.2% | 45.3% | 0.511 | 0.551 | 54.6% |
| 2025Q1 | SPY 1-month return avg -2.0% | 50.0% | 0.522 | 0.411 | 34.2% |
| 2025Q2 | SPY 1-month return avg +2.7% | 43.7% | 0.518 | 0.561 | 50.0% |
| 2025Q3 | SPY 1-month return avg +3.1% | 43.2% | 0.497 | 0.490 | 53.5% |
| 2025Q4 | SPY 1-month return avg +1.8% | 49.7% | 0.504 | 0.520 | 46.2% |
| 2026Q1 | SPY 1-month return avg -1.1% | 47.3% | 0.520 | 0.504 | 49.2% |
| 2026Q2 | SPY 1-month return avg +4.3% | 45.0% | 0.519 | 0.527 | 54.2% |
| 2026Q3 | SPY 1-month return avg +1.7% | 43.3% | 0.494 | 0.576 | 62.2% |

Calibration (does the probability mean what it says?):

| Predicted | Actual | n |
|---|---|---|
| 45.5% | 44.8% | 12,802 |
| 45.9% | 46.8% | 12,801 |
| 46.0% | 45.2% | 12,801 |
| 46.2% | 46.0% | 12,801 |
| 46.5% | 47.3% | 12,802 |

What the model leans on most (drop in AUC when the feature is scrambled):

- Market: SPY vs 200-day SMA: +0.0491
- Market: SPY volatility: +0.0405
- Market: SPY 1-month return: +0.0344
- Volatility (ATR %): +0.0241
- 3-month return: +0.0110
- Distance from 200-day SMA: +0.0071
- Distance from 52-week high: +0.0066
- Dollar volume (liquidity): +0.0059
- Share price level: +0.0024
- MACD histogram slope: +0.0021

## Logged Top-20 picks, scored as of their entry date

40 graded picks. Model's average predicted chance of being up: 55.3%; actually up: 47.5%. Model AUC on these picks: 0.520; Matrix Score AUC: 0.573. With this few picks these numbers are noise, not evidence - they become meaningful after a few more cohorts.

Picks the model rated in the top half: 45.0% were up; bottom half: 50.0%.

| Cohort | Ticker | Matrix Score | Model win prob | 30d return | Up? |
|---|---|---|---|---|---|
| 2026-08 | ADI | 3 | 53.1% | +3.76% | yes |
| 2026-08 | AFL | 3 | 52.5% | -0.85% | no |
| 2026-08 | AMKR | 3 | 55.1% | +2.80% | yes |
| 2026-08 | AVGO | 3 | 52.7% | -0.19% | no |
| 2026-08 | CBOE | 3 | 52.9% | -7.46% | no |
| 2026-08 | COHR | 3 | 55.5% | +10.86% | yes |
| 2026-08 | DIOD | 3 | 56.2% | -0.01% | no |
| 2026-08 | GL | 3 | 52.1% | +0.78% | yes |
| 2026-08 | KEYS | 4 | 53.1% | +8.06% | yes |
| 2026-08 | LOW | 3 | 53.8% | -12.13% | no |
| 2026-08 | NBIS | 3 | 56.4% | +5.77% | yes |
| 2026-08 | NTES | 3 | 53.4% | -1.56% | no |
| 2026-08 | ONTO | 3 | 56.6% | -7.65% | no |
| 2026-08 | RMBS | 3 | 55.0% | +5.22% | yes |
| 2026-08 | SITM | 3 | 55.6% | +6.60% | yes |
| 2026-08 | STLD | 4 | 53.0% | +4.77% | yes |
| 2026-08 | STX | 5 | 56.5% | +3.19% | yes |
| 2026-08 | TRV | 3 | 53.1% | +2.17% | yes |
| 2026-08 | TYL | 3 | 54.4% | -4.50% | no |
| 2026-08 | WYNN | 5 | 53.6% | -16.35% | no |
| 2026-09 | APTV | 2 | 56.5% | -5.67% | no |
| 2026-09 | AVL | 2 | 60.5% | +1.61% | yes |
| 2026-09 | AXON | 2 | 56.1% | -23.48% | no |
| 2026-09 | BAX | 2 | 55.4% | -5.28% | no |
| 2026-09 | BIDU | 2 | 56.4% | -8.99% | no |
| 2026-09 | CLX | 2 | 55.4% | -13.02% | no |
| 2026-09 | COO | 2 | 56.0% | -19.63% | no |
| 2026-09 | CPB | 3 | 55.4% | -12.79% | no |
| 2026-09 | CRUS | 2 | 58.2% | +7.27% | yes |
| 2026-09 | GEHC | 2 | 55.7% | -5.71% | no |
| 2026-09 | HPE | 2 | 55.4% | +25.89% | yes |
| 2026-09 | HSY | 2 | 55.8% | -8.03% | no |
| 2026-09 | MDB | 2 | 56.0% | -6.56% | no |
| 2026-09 | NET | 2 | 56.0% | +26.35% | yes |
| 2026-09 | ON | 2 | 56.3% | +16.67% | yes |
| 2026-09 | PYPL | 2 | 56.2% | -4.00% | no |
| 2026-09 | QLYS | 2 | 55.0% | +13.95% | yes |
| 2026-09 | RARE | 2 | 57.8% | -2.36% | no |
| 2026-09 | RPD | 2 | 58.3% | +7.76% | yes |
| 2026-09 | TENB | 2 | 56.3% | +11.66% | yes |
| 2026-10 | AKAM | 3 | 52.0% | pending |  |
| 2026-10 | APTV | 4 | 52.3% | pending |  |
| 2026-10 | CMI | 4 | 52.8% | pending |  |
| 2026-10 | COO | 3 | 53.5% | pending |  |
| 2026-10 | DECK | 3 | 52.5% | pending |  |
| 2026-10 | FAST | 4 | 51.2% | pending |  |
| 2026-10 | HOOD | 3 | 52.1% | pending |  |
| 2026-10 | HRL | 4 | 52.3% | pending |  |
| 2026-10 | J | 3 | 51.9% | pending |  |
| 2026-10 | LULU | 3 | 52.1% | pending |  |
| 2026-10 | MDT | 4 | 51.7% | pending |  |
| 2026-10 | NCLH | 3 | 52.5% | pending |  |
| 2026-10 | NVR | 3 | 52.1% | pending |  |
| 2026-10 | PCG | 4 | 53.5% | pending |  |
| 2026-10 | PHM | 3 | 52.5% | pending |  |
| 2026-10 | PSKY | 3 | 51.1% | pending |  |
| 2026-10 | UDR | 4 | 52.2% | pending |  |
| 2026-10 | VIPS | 3 | 51.7% | pending |  |
| 2026-10 | WETO | 4 | 54.9% | pending |  |
| 2026-10 | XLV | 4 | 51.1% | pending |  |

## What to do with this

- Show `win_prob_30d_pct` next to the Matrix Score as a second opinion; don't let it override the score until status is TRUSTED.
- If the chosen model does NOT beat the RSI rule, the honest conclusion is that these indicators carry little extra 30-day edge beyond what the rule captures.
- The 30-day returns here are measured over 21 trading days, which can differ slightly from the scorecard's calendar-day window.
- Not financial advice.