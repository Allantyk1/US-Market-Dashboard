"""
Win-Rate Model (machine-learning layer on top of the Matrix Score)
------------------------------------------------------------------
Learns, from price history alone, the probability that a stock will be
(a) UP after ~30 days ("win", same definition as the scorecard's win rate)
and (b) AHEAD of the SPY/QQQ/DIA average after ~30 days ("beat").

It does NOT replace the Matrix Score. It adds a second opinion - a
calibrated probability - that you can show next to it, and it only claims
to be trustworthy once it has beaten a simple baseline on data it never saw
AND there are 3+ graded live cohorts.

WHERE THIS SITS IN THE PIPELINE
  stock_screener_us.py        -> Matrix Score (unchanged)
  log_top20_snapshot.py       -> logs the monthly Top 20
  evaluate_recommendations.py -> grades matured cohorts
  refine_matrix_score.py      -> rule-based RSI-bucket suggestions
  train_winrate_model.py      -> THIS SCRIPT (monthly retrain + daily predict)

HOW IT AVOIDS FOOLING ITSELF
  - Trains on ~5 years of daily data for the whole screener universe, so it
    sees the 2022 bear market and choppy periods, not just the Apr-Aug 2026
    bull run the existing backtest came from.
  - Walk-forward testing: for each of the last 8 calendar quarters it trains
    ONLY on data that ended 35+ days before that quarter starts (so no
    30-day outcome overlaps the test period), then scores that quarter.
  - Compares against the same RSI-bucket rule refine_matrix_score.py uses.
    If the model can't beat that on unseen quarters, the report says so.
  - Probabilities are calibrated (Platt scaling) on the walk-forward predictions,
    so "62%" means roughly 62% of such setups were up 30 days later.
  - Each logged Top-20 pick is scored with a model trained only on data
    from before its entry date, so you can see how the model would have
    rated those picks at the time.
  - Graded live picks get extra training weight that grows as more cohorts
    are graded (capped at 5x), so live results count for more over time
    without letting a handful of picks dominate.

RUN (from the screener folder, same place as dashboard_data.json)
  python train_winrate_model.py                 monthly: refresh data, retrain, test, predict
  python train_winrate_model.py --predict-only  daily: refresh last month of data, score with saved model
  python train_winrate_model.py --no-download   use the price cache as-is (offline / re-run)

ONE-TIME SETUP
  pip install scikit-learn yfinance pandas numpy
  Add these to .gitignore (large/local-only):  winrate_price_cache.pkl  winrate_model.pkl

OUTPUTS
  winrate_predictions.json   probability per ticker (for the dashboard), plus today's Top 20
  winrate_model_report.md    human-readable results (walk-forward test, live picks, features)
  winrate_model_report.json  same, machine-readable
  winrate_model.pkl          saved model used by --predict-only
  winrate_price_cache.pkl    local price cache (first run downloads ~5y; later runs top up)
"""

import argparse
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

try:
    from sklearn.base import BaseEstimator, TransformerMixin
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.inspection import permutation_importance
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
except ImportError:
    print("[STOP] scikit-learn is not installed. Run:  pip install scikit-learn")
    sys.exit(1)

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------- settings
DASHBOARD_FILE = "dashboard_data.json"
LOG_FILE = "recommendation_log.json"
CACHE_FILE = "winrate_price_cache.pkl"
MODEL_FILE = "winrate_model.pkl"
PRED_FILE = "winrate_predictions.json"
REPORT_JSON = "winrate_model_report.json"
REPORT_MD = "winrate_model_report.md"

BENCHMARKS = ["SPY", "QQQ", "DIA"]
HISTORY_PERIOD = "5y"        # first download; includes the 2022 bear market
HORIZON = 21                 # trading days ~= 30 calendar days (scorecard window)
SAMPLE_EVERY = 5             # use every 5th trading day for training (less overlap)
EMBARGO_DAYS = 35            # gap between train end and test start (calendar days)
N_TEST_QUARTERS = 8          # walk-forward test quarters
TOP_N = 20                   # "Top 20" used for lift checks
BATCH_SIZE = 50              # yfinance tickers per request
BATCH_COOLDOWN = 8           # seconds between batches (matches the screener's 8s cooldown)

MIN_COHORTS_FOR_TRUST = 3    # same rule as refine_matrix_score.py
MIN_AUC_EDGE = 0.01          # must beat the RSI-bucket rule by this much on unseen quarters
MAX_LIVE_WEIGHT = 5.0

TARGETS = {
    "win": "Up after ~30 days (return > 0)",
    "beat": "Beat SPY/QQQ/DIA average after ~30 days",
}

FEATURES = [
    "rsi14", "macd_hist_pct", "macd_hist_slope_pct",
    "dist_ema20_pct", "ema20_vs_ema40_pct",
    "bb_pos", "bb_width_pct", "vol_ratio_200",
    "kdj_k", "kdj_d", "kdj_j",
    "ret_5d", "ret_21d", "ret_63d",
    "atr_pct", "dist_52w_high_pct", "dist_sma200_pct",
    "log_price", "log_dollar_vol",
    "spy_ret_21d", "spy_dist_sma200_pct", "spy_vol_21d",
]

FEATURE_LABELS = {
    "rsi14": "RSI(14)", "macd_hist_pct": "MACD histogram", "macd_hist_slope_pct": "MACD histogram slope",
    "dist_ema20_pct": "Distance from EMA20", "ema20_vs_ema40_pct": "EMA20 vs EMA40 trend",
    "bb_pos": "Bollinger position", "bb_width_pct": "Bollinger width", "vol_ratio_200": "Volume vs 200-day avg",
    "kdj_k": "KDJ K", "kdj_d": "KDJ D", "kdj_j": "KDJ J",
    "ret_5d": "5-day return", "ret_21d": "1-month return", "ret_63d": "3-month return",
    "atr_pct": "Volatility (ATR %)", "dist_52w_high_pct": "Distance from 52-week high",
    "dist_sma200_pct": "Distance from 200-day SMA", "log_price": "Share price level",
    "log_dollar_vol": "Dollar volume (liquidity)", "spy_ret_21d": "Market: SPY 1-month return",
    "spy_dist_sma200_pct": "Market: SPY vs 200-day SMA", "spy_vol_21d": "Market: SPY volatility",
}


# ---------------------------------------------------------------- helpers
class Winsorizer(BaseEstimator, TransformerMixin):
    """Clip each column to its training 1st/99th percentile (tames outliers for the linear model)."""

    def __init__(self, lower=0.01, upper=0.99):
        self.lower = lower
        self.upper = upper

    def fit(self, X, y=None):
        X = np.asarray(X, dtype=float)
        self.lo_ = np.nanquantile(X, self.lower, axis=0)
        self.hi_ = np.nanquantile(X, self.upper, axis=0)
        return self

    def transform(self, X):
        return np.clip(np.asarray(X, dtype=float), self.lo_, self.hi_)


class PlattCalibrator:
    """Maps raw model scores to probabilities with a 1-feature logistic fit
    (smoother and harder to overfit than isotonic when the edge is small)."""

    def fit(self, raw, y):
        raw = np.asarray(raw, dtype=float)
        x = np.log(np.clip(raw, 1e-4, 1 - 1e-4) / (1 - np.clip(raw, 1e-4, 1 - 1e-4))).reshape(-1, 1)
        self.lr_ = LogisticRegression(C=1.0).fit(x, np.asarray(y).astype(int))
        return self

    def predict(self, raw):
        raw = np.asarray(raw, dtype=float)
        x = np.log(np.clip(raw, 1e-4, 1 - 1e-4) / (1 - np.clip(raw, 1e-4, 1 - 1e-4))).reshape(-1, 1)
        return self.lr_.predict_proba(x)[:, 1]


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clean_json(obj):
    """Replace NaN/inf with None - browsers' JSON.parse rejects bare NaN."""
    if isinstance(obj, dict):
        return {k: clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, dt.date)):
        return obj.isoformat()[:10]
    return obj


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean_json(obj), f, indent=2, allow_nan=False)


def us_eastern_now():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # No tz database (Windows without the tzdata package) - EDT approximation
        return dt.datetime.utcnow() - dt.timedelta(hours=4)


# ---------------------------------------------------------------- universe + prices
def load_universe(log):
    tickers = set()
    dash = load_json(DASHBOARD_FILE, {}) or {}
    for key in ("all_tickers", "top20"):
        for r in dash.get(key, []) or []:
            if r.get("ticker"):
                tickers.add(str(r["ticker"]).strip())
    if not tickers and os.path.isdir("history"):
        for fn in os.listdir("history"):
            if fn.endswith(".json"):
                tickers.add(fn[:-5])
    for c in log.get("cohorts", []):
        for p in c.get("picks", []):
            if p.get("ticker"):
                tickers.add(p["ticker"])
    tickers.update(BENCHMARKS)
    return sorted(tickers)


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
    sub = sub[need].copy()
    sub = sub.dropna(subset=["Close"])
    if sub.empty:
        return None
    idx = pd.to_datetime(sub.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    sub.index = idx.normalize()
    sub = sub[~sub.index.duplicated(keep="last")].sort_index()
    return sub.astype(float)


def _drop_unfinished_session(df):
    """Same lesson as the 2026-09-12 intraday fix: never treat a still-open
    session's live price as a close."""
    if df is None or df.empty:
        return df
    now_et = us_eastern_now()
    last = df.index[-1].date()
    if last == now_et.date() and now_et.hour < 17:
        return df.iloc[:-1]
    return df


def download_batches(tickers, period):
    try:
        import yfinance as yf
    except ImportError:
        print("[STOP] yfinance is not installed. Run:  pip install yfinance")
        sys.exit(1)
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
        print(f"[DATA] Full {HISTORY_PERIOD} download for {len(full)} tickers (first run takes a while)")
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

    with open(CACHE_FILE, "wb") as f:
        pickle.dump(cache, f)
    missing = [t for t in tickers if t not in cache]
    if missing:
        print(f"[WARN] No price data for {len(missing)} tickers (e.g. {', '.join(missing[:8])})")
    return cache


# ---------------------------------------------------------------- features
def compute_features(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    f = pd.DataFrame(index=df.index)

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    f["rsi14"] = (100 - 100 / (1 + rs)).where(loss > 0, 100.0)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    f["macd_hist_pct"] = hist / c * 100
    f["macd_hist_slope_pct"] = (hist - hist.shift(3)) / c * 100

    ema20 = c.ewm(span=20, adjust=False).mean()
    ema40 = c.ewm(span=40, adjust=False).mean()
    f["dist_ema20_pct"] = (c / ema20 - 1) * 100
    f["ema20_vs_ema40_pct"] = (ema20 / ema40 - 1) * 100

    sma20 = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    upper, lower = sma20 + 2 * sd20, sma20 - 2 * sd20
    f["bb_pos"] = (c - lower) / (upper - lower).replace(0, np.nan)
    f["bb_width_pct"] = (upper - lower) / sma20 * 100

    f["vol_ratio_200"] = v / v.rolling(200, min_periods=100).mean().replace(0, np.nan)

    low9, high9 = l.rolling(9).min(), h.rolling(9).max()
    rsv = ((c - low9) / (high9 - low9).replace(0, np.nan) * 100).fillna(50)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    f["kdj_k"], f["kdj_d"], f["kdj_j"] = k, d, 3 * k - 2 * d
    f.loc[f.index[:9], ["kdj_k", "kdj_d", "kdj_j"]] = np.nan

    f["ret_5d"] = c.pct_change(5) * 100
    f["ret_21d"] = c.pct_change(21) * 100
    f["ret_63d"] = c.pct_change(63) * 100

    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    f["atr_pct"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / c * 100
    f["dist_52w_high_pct"] = (c / c.rolling(252, min_periods=120).max() - 1) * 100
    f["dist_sma200_pct"] = (c / c.rolling(200, min_periods=150).mean() - 1) * 100
    f["log_price"] = np.log10(c.clip(lower=0.01))
    f["log_dollar_vol"] = np.log10((c * v).rolling(20).mean().clip(lower=1))

    f["close"] = c
    f["fwd_ret"] = c.shift(-HORIZON) / c - 1
    return f


def regime_features(cache):
    spy = cache["SPY"]["Close"]
    r = pd.DataFrame(index=spy.index)
    r["spy_ret_21d"] = spy.pct_change(21) * 100
    r["spy_dist_sma200_pct"] = (spy / spy.rolling(200, min_periods=150).mean() - 1) * 100
    r["spy_vol_21d"] = spy.pct_change().rolling(21).std() * math.sqrt(252) * 100
    fwds = [cache[b]["Close"].shift(-HORIZON) / cache[b]["Close"] - 1 for b in BENCHMARKS if b in cache]
    r["bench_fwd"] = pd.concat(fwds, axis=1).mean(axis=1, skipna=False) if fwds else np.nan
    return r


def live_pick_keys(log):
    keys = {}
    for c in log.get("cohorts", []):
        for p in c.get("picks", []):
            try:
                d = pd.Timestamp(p["entry_date"])
            except Exception:
                continue
            keys.setdefault(p["ticker"], []).append({
                "entry_date": d, "month": c.get("month"),
                "matrix_score": p.get("matrix_score"), "signal": p.get("signal"),
            })
    return keys


def build_dataset(cache, log):
    if "SPY" not in cache:
        print("[STOP] SPY price history missing - needed for market-regime features and the calendar.")
        sys.exit(1)
    regime = regime_features(cache)
    cal = regime.index
    sample_dates = set(cal[::-1][::SAMPLE_EVERY])
    picks = live_pick_keys(log)

    train_parts, latest_parts, live_rows = [], [], []
    for t, df in cache.items():
        if df is None or len(df) < 260:
            continue
        f = compute_features(df).join(regime, how="left")
        f["ticker"] = t
        latest_parts.append(f.iloc[[-1]])
        train_parts.append(f[f.index.isin(sample_dates)])
        for p in picks.get(t, []):
            pos = f.index.searchsorted(p["entry_date"], side="right") - 1
            if pos >= 0:
                row = f.iloc[[pos]].copy()
                row["pick_month"] = p["month"]
                row["pick_entry_date"] = p["entry_date"]
                row["pick_matrix_score"] = p["matrix_score"]
                live_rows.append(row)

    def finish(parts):
        if not parts:
            return pd.DataFrame()
        d = pd.concat(parts)
        d.index.name = "date"
        d = d.reset_index()
        d["win"] = np.where(d["fwd_ret"].notna(), (d["fwd_ret"] > 0).astype(float), np.nan)
        d["beat"] = np.where(d["fwd_ret"].notna() & d["bench_fwd"].notna(),
                             (d["fwd_ret"] > d["bench_fwd"]).astype(float), np.nan)
        return d

    return finish(train_parts), finish(latest_parts), finish(live_rows)


# ---------------------------------------------------------------- modelling
def make_model(name):
    if name == "logistic":
        return Pipeline([
            ("winsor", Winsorizer()),
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(C=0.1, max_iter=3000)),
        ])
    return HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=250, min_samples_leaf=300,
        l2_regularization=1.0, random_state=42)


MODEL_NAMES = ["logistic", "gbm"]


def fit_model(name, X, y, w=None):
    m = make_model(name)
    if w is None:
        m.fit(X, y)
    elif name == "logistic":
        m.fit(X, y, clf__sample_weight=w)
    else:
        m.fit(X, y, sample_weight=w)
    return m


def rsi_bucket(rsi):
    b = pd.cut(rsi, bins=[-np.inf, 30, 45, 55, 70, np.inf], right=False,
               labels=["<30", "30-45", "45-55", "55-70", ">=70"])
    return b.astype(str)


def rsi_baseline(train, test, target):
    """The rule refine_matrix_score.py uses: win rate per RSI bucket."""
    rates = train.groupby(rsi_bucket(train["rsi14"]))[target].mean()
    overall = train[target].mean()
    return rsi_bucket(test["rsi14"]).map(rates).fillna(overall).astype(float).values


def safe_auc(y, p):
    try:
        return roc_auc_score(y, p) if len(np.unique(y)) == 2 else np.nan
    except ValueError:
        return np.nan


def xs_auc(df, score_col, target):
    """Average same-day AUC: how well the score ranks stocks against each other on a given
    day - which is exactly what picking a Top 20 does. Ignores market-wide ups and downs."""
    vals = [safe_auc(g[target], g[score_col]) for _, g in df.groupby("date") if len(g) >= 30]
    vals = [v for v in vals if not np.isnan(v)]
    return float(np.mean(vals)) if vals else np.nan


def top_n_stats(df, score_col, target):
    """Each test date: take the Top-N by score; average their hit rate and 30d return."""
    hits, rets = [], []
    for _, g in df.groupby("date"):
        if len(g) < TOP_N * 2:
            continue
        top = g.nlargest(TOP_N, score_col)
        hits.append(top[target].mean())
        rets.append(top["fwd_ret"].mean())
    if not hits:
        return np.nan, np.nan
    return float(np.mean(hits)), float(np.mean(rets) * 100)


def walk_forward(data, target):
    lab = data[data[target].notna()].reset_index(drop=True)
    last = lab["date"].max()
    quarters = pd.period_range(end=last.to_period("Q"), periods=N_TEST_QUARTERS, freq="Q")
    folds, oof = [], []
    for q in quarters:
        start, end = q.start_time, q.end_time
        test = lab[(lab["date"] >= start) & (lab["date"] <= end)].copy()
        train = lab[lab["date"] < start - pd.Timedelta(days=EMBARGO_DAYS)]
        if len(test) < 500 or len(train) < 5000:
            continue
        fold = {"quarter": str(q), "n_train": len(train), "n_test": len(test),
                "base_rate": float(test[target].mean())}
        spy_q = test.groupby("date")["spy_ret_21d"].first()
        fold["market_note"] = "SPY 1-month return avg {:+.1f}%".format(spy_q.mean()) if len(spy_q) else ""
        test["p_baseline"] = rsi_baseline(train, test, target)
        fold["baseline_auc"] = safe_auc(test[target], test["p_baseline"])
        fold["baseline_xs_auc"] = xs_auc(test, "p_baseline", target)
        fold["baseline_top_hit"], fold["baseline_top_ret"] = top_n_stats(test, "p_baseline", target)
        for name in MODEL_NAMES:
            m = fit_model(name, train[FEATURES], train[target])
            test[f"p_{name}"] = m.predict_proba(test[FEATURES])[:, 1]
            fold[f"{name}_auc"] = safe_auc(test[target], test[f"p_{name}"])
            fold[f"{name}_xs_auc"] = xs_auc(test, f"p_{name}", target)
            fold[f"{name}_brier"] = float(brier_score_loss(test[target], test[f"p_{name}"]))
            fold[f"{name}_top_hit"], fold[f"{name}_top_ret"] = top_n_stats(test, f"p_{name}", target)
        fold["all_ret"] = float(test["fwd_ret"].mean() * 100)
        folds.append(fold)
        oof.append(test[["date", "ticker", target, "fwd_ret"] + [f"p_{n}" for n in MODEL_NAMES] + ["p_baseline"]])
        print(f"  {target:<4} {q}: same-day AUC  RSI rule {fold['baseline_xs_auc']:.3f} | "
              + " | ".join(f"{n} {fold[n + '_xs_auc']:.3f}" for n in MODEL_NAMES), flush=True)
    return folds, (pd.concat(oof) if oof else pd.DataFrame())


def summarize_folds(folds):
    s = {}
    for key in ["baseline"] + MODEL_NAMES:
        s[key] = {
            "mean_auc": float(np.nanmean([f[f"{key}_auc"] for f in folds])),
            "mean_xs_auc": float(np.nanmean([f[f"{key}_xs_auc"] for f in folds])),
            "mean_top_hit": float(np.nanmean([f[f"{key}_top_hit"] for f in folds])),
            "mean_top_ret": float(np.nanmean([f[f"{key}_top_ret"] for f in folds])),
        }
    s["all_mean_ret"] = float(np.nanmean([f["all_ret"] for f in folds]))
    s["base_rate"] = float(np.nanmean([f["base_rate"] for f in folds]))
    return s


def calibration_table(y, p, bins=5):
    df = pd.DataFrame({"y": y, "p": p}).dropna()
    if df.empty:
        return []
    df["bin"] = pd.qcut(df["p"], q=bins, duplicates="drop")
    out = []
    for b, g in df.groupby("bin", observed=True):
        out.append({"predicted_pct": round(g["p"].mean() * 100, 1),
                    "actual_pct": round(g["y"].mean() * 100, 1), "n": int(len(g))})
    return out


def graded_cohort_count(log):
    today = dt.date.today()
    n = 0
    for c in log.get("cohorts", []):
        if c.get("picks"):
            try:
                if (today - dt.date.fromisoformat(c["picks"][0]["entry_date"])).days >= 30:
                    n += 1
            except Exception:
                pass
    return n


def evaluate_live_picks(lab, live, oof, target, model_name):
    """Score each logged pick with a model trained only on data from before its entry."""
    rows = []
    if live.empty:
        return rows
    for month, g in live.groupby("pick_month"):
        entry = pd.Timestamp(g["pick_entry_date"].iloc[0])
        cutoff = entry - pd.Timedelta(days=EMBARGO_DAYS)
        train = lab[lab["date"] < cutoff]
        if len(train) < 5000:
            continue
        m = fit_model(model_name, train[FEATURES], train[target])
        raw = m.predict_proba(g[FEATURES])[:, 1]
        prior = oof[oof["date"] < cutoff] if not oof.empty else pd.DataFrame()
        if len(prior) >= 2000:
            iso = PlattCalibrator().fit(prior[f"p_{model_name}"], prior[target])
            prob = iso.predict(raw)
        else:
            prob = raw
        for (_, r), pr in zip(g.iterrows(), prob):
            outcome = r[target]
            rows.append({
                "month": month, "ticker": r["ticker"], "entry_date": entry,
                "matrix_score": r.get("pick_matrix_score"),
                "prob_pct": round(float(pr) * 100, 1),
                "return_30d_pct": None if pd.isna(r["fwd_ret"]) else round(r["fwd_ret"] * 100, 2),
                "outcome": None if pd.isna(outcome) else int(outcome),
            })
    return rows


def live_summary(rows):
    graded = [r for r in rows if r["outcome"] is not None]
    if not graded:
        return {"n_graded": 0}
    df = pd.DataFrame(graded)
    s = {"n_graded": int(len(df)), "actual_rate_pct": round(df["outcome"].mean() * 100, 1),
         "avg_prob_pct": round(df["prob_pct"].mean(), 1),
         "brier": round(float(brier_score_loss(df["outcome"], df["prob_pct"] / 100)), 3),
         "model_auc": safe_auc(df["outcome"], df["prob_pct"])}
    if df["matrix_score"].notna().sum() > 2:
        s["matrix_score_auc"] = safe_auc(df["outcome"], df["matrix_score"].astype(float))
    half = df["prob_pct"].median()
    hi, lo = df[df["prob_pct"] >= half], df[df["prob_pct"] < half]
    if len(hi) and len(lo):
        s["high_half_rate_pct"] = round(hi["outcome"].mean() * 100, 1)
        s["low_half_rate_pct"] = round(lo["outcome"].mean() * 100, 1)
    return s


def feature_importance(model, lab, target):
    recent = lab[lab["date"] >= lab["date"].max() - pd.Timedelta(days=365)]
    sample = recent.sample(min(len(recent), 15000), random_state=1)
    if sample[target].nunique() < 2:
        return []
    r = permutation_importance(model, sample[FEATURES], sample[target], scoring="roc_auc",
                               n_repeats=3, random_state=1, n_jobs=1)
    order = np.argsort(-r.importances_mean)
    return [{"feature": FEATURES[i], "label": FEATURE_LABELS.get(FEATURES[i], FEATURES[i]),
             "auc_drop": round(float(r.importances_mean[i]), 4)} for i in order[:10]]


# ---------------------------------------------------------------- predict
def predict_latest(bundle, latest):
    if latest.empty:
        return []
    as_of = latest["date"].max()
    latest = latest.copy()
    latest["stale"] = latest["date"] < as_of - pd.Timedelta(days=4)
    for target, info in bundle["targets"].items():
        raw = info["model"].predict_proba(latest[FEATURES])[:, 1]
        cal = info.get("calibrator")
        latest[f"{target}_prob"] = cal.predict(raw) if cal is not None else raw
    dash = load_json(DASHBOARD_FILE, {}) or {}
    info_by_t = {r["ticker"]: r for r in dash.get("all_tickers", []) or [] if r.get("ticker")}
    out = []
    for _, r in latest.sort_values("win_prob", ascending=False).iterrows():
        d = info_by_t.get(r["ticker"], {})
        out.append({
            "ticker": r["ticker"],
            "win_prob_30d_pct": round(float(r["win_prob"]) * 100, 1),
            "beat_bench_prob_30d_pct": round(float(r["beat_prob"]) * 100, 1),
            "price_date": r["date"],
            "stale_data": bool(r["stale"]),
            "matrix_score": d.get("matrix_score"),
            "signal": d.get("signal"),
        })
    return out


def write_predictions(bundle, preds):
    dash = load_json(DASHBOARD_FILE, {}) or {}
    top20 = [r.get("ticker") for r in dash.get("top20", []) or []]
    by_t = {p["ticker"]: p for p in preds}
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of_price_date": max((p["price_date"] for p in preds), default=None),
        "model_trained_at": bundle["trained_at"],
        "model_data_end": bundle["data_end"],
        "trustworthy": bundle["trustworthy"],
        "status": bundle["status"],
        "caveat": ("Experimental. Probabilities come from a model trained on ~5 years of price data "
                   "and tested walk-forward. Treat as a second opinion next to the Matrix Score, "
                   "not as a signal on its own. Not financial advice."),
        "top20": [by_t[t] for t in top20 if t in by_t],
        "predictions": preds,
    }
    write_json(PRED_FILE, payload)
    print(f"[OK] Wrote {PRED_FILE} ({len(preds)} tickers, as of {str(payload['as_of_price_date'])[:10]})")


# ---------------------------------------------------------------- report
def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.{nd}f}%"


def num(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def write_report(report):
    L = []
    L.append(f"# Win-Rate Model Report ({report['generated_at'][:10]})\n")
    L.append(f"**Status: {report['status']}**  |  Graded live cohorts: {report['graded_cohorts']} "
             f"(need {MIN_COHORTS_FOR_TRUST}+)  |  Training data: {report['data_start']} to "
             f"{report['data_end']}, {report['n_tickers']} tickers, {report['n_rows']:,} samples\n")
    L.append("> How to read this: AUC measures how well a score ranks winners above losers "
             "(0.50 = coin flip, 0.55 is a useful edge for 30-day stock moves, above 0.60 would be "
             "unusually strong). **Same-day AUC** compares stocks against each other on the same day "
             "- that is what choosing a Top 20 does, so it is the number the model is judged on. "
             "Overall AUC also rewards guessing whether the whole market goes up. Every number below "
             "comes from quarters the model had NOT seen when it was trained.\n")
    for target, t in report["targets"].items():
        s = t["summary"]
        ch = t["chosen_model"]
        L.append(f"## {TARGETS[target]}\n")
        L.append(f"Chosen model: **{ch}**. Beats the RSI-bucket rule on unseen quarters: "
                 f"**{'YES' if t['beats_baseline'] else 'NO'}**.\n")
        L.append("| Score used to rank | Same-day AUC | Overall AUC | Top-20 hit rate | Top-20 avg 30d return |")
        L.append("|---|---|---|---|---|")
        L.append(f"| RSI-bucket rule (what refine_matrix_score.py uses) | {num(s['baseline']['mean_xs_auc'])} | {num(s['baseline']['mean_auc'])} | "
                 f"{pct(s['baseline']['mean_top_hit'])} | {s['baseline']['mean_top_ret']:+.2f}% |")
        for n in MODEL_NAMES:
            mark = " (chosen)" if n == ch else ""
            L.append(f"| Model: {n}{mark} | {num(s[n]['mean_xs_auc'])} | {num(s[n]['mean_auc'])} | {pct(s[n]['mean_top_hit'])} | "
                     f"{s[n]['mean_top_ret']:+.2f}% |")
        L.append(f"| Whole universe (no selection) | 0.500 | 0.500 | {pct(s['base_rate'])} | {s['all_mean_ret']:+.2f}% |")
        L.append("")
        L.append("Per quarter (test period only):\n")
        L.append(f"| Quarter | Market | Base rate | RSI rule same-day AUC | {ch} same-day AUC | {ch} Top-20 hit |")
        L.append("|---|---|---|---|---|---|")
        for f in t["folds"]:
            L.append(f"| {f['quarter']} | {f['market_note']} | {pct(f['base_rate'])} | "
                     f"{num(f['baseline_xs_auc'])} | {num(f[ch + '_xs_auc'])} | {pct(f[ch + '_top_hit'])} |")
        L.append("")
        if t["calibration"]:
            L.append("Calibration (does the probability mean what it says?):\n")
            L.append("| Predicted | Actual | n |")
            L.append("|---|---|---|")
            for c in t["calibration"]:
                L.append(f"| {c['predicted_pct']}% | {c['actual_pct']}% | {c['n']:,} |")
            L.append("")
        if t["importance"]:
            L.append("What the model leans on most (drop in AUC when the feature is scrambled):\n")
            for imp in t["importance"]:
                L.append(f"- {imp['label']}: {imp['auc_drop']:+.4f}")
            L.append("")

    L.append("## Logged Top-20 picks, scored as of their entry date\n")
    lw = report["live"]
    s = lw["summary"]
    if s.get("n_graded"):
        L.append(f"{s['n_graded']} graded picks. Model's average predicted chance of being up: "
                 f"{s['avg_prob_pct']}%; actually up: {s['actual_rate_pct']}%. "
                 f"Model AUC on these picks: {num(s.get('model_auc'))}; Matrix Score AUC: "
                 f"{num(s.get('matrix_score_auc'))}. With this few picks these numbers are noise, "
                 f"not evidence - they become meaningful after a few more cohorts.\n")
        if "high_half_rate_pct" in s:
            L.append(f"Picks the model rated in the top half: {s['high_half_rate_pct']}% were up; "
                     f"bottom half: {s['low_half_rate_pct']}%.\n")
    else:
        L.append("No logged picks are old enough to grade yet.\n")
    if lw["rows"]:
        L.append("| Cohort | Ticker | Matrix Score | Model win prob | 30d return | Up? |")
        L.append("|---|---|---|---|---|---|")
        for r in lw["rows"]:
            ret = "pending" if r["return_30d_pct"] is None else f"{r['return_30d_pct']:+.2f}%"
            up = "" if r["outcome"] is None else ("yes" if r["outcome"] else "no")
            L.append(f"| {r['month']} | {r['ticker']} | {r['matrix_score']} | {r['prob_pct']}% | {ret} | {up} |")
        L.append("")
    L.append("## What to do with this\n")
    L.append("- Show `win_prob_30d_pct` next to the Matrix Score as a second opinion; don't let it "
             "override the score until status is TRUSTED.")
    L.append("- If the chosen model does NOT beat the RSI rule, the honest conclusion is that these "
             "indicators carry little extra 30-day edge beyond what the rule captures.")
    L.append("- The 30-day returns here are measured over 21 trading days, which can differ "
             "slightly from the scorecard's calendar-day window.")
    L.append("- Not financial advice.")
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Train / apply the win-rate model")
    ap.add_argument("--predict-only", action="store_true", help="use the saved model, skip training")
    ap.add_argument("--no-download", action="store_true", help="use cached prices only")
    args = ap.parse_args()

    log = load_json(LOG_FILE, {"cohorts": []}) or {"cohorts": []}
    tickers = load_universe(log)
    print(f"[INFO] Universe: {len(tickers)} tickers")
    cache = update_cache(tickers, allow_download=not args.no_download)
    if not cache:
        print("[STOP] No price data available.")
        return

    data, latest, live = build_dataset(cache, log)
    print(f"[INFO] Samples: {len(data):,} rows | latest snapshot: {len(latest)} tickers | "
          f"logged picks matched: {len(live)}")

    if args.predict_only:
        if not os.path.exists(MODEL_FILE):
            print(f"[STOP] {MODEL_FILE} not found - run once without --predict-only to train it.")
            return
        with open(MODEL_FILE, "rb") as f:
            bundle = pickle.load(f)
        write_predictions(bundle, predict_latest(bundle, latest))
        return

    graded = graded_cohort_count(log)
    n_graded_picks = int(live["win"].notna().sum()) if not live.empty else 0
    live_weight = min(MAX_LIVE_WEIGHT, 1.0 + n_graded_picks / 50.0)

    report = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "data_start": data["date"].min(), "data_end": data["date"].max(),
        "n_tickers": int(data["ticker"].nunique()), "n_rows": int(len(data)),
        "graded_cohorts": graded, "live_pick_weight": live_weight, "targets": {},
    }
    bundle = {"trained_at": report["generated_at"], "data_end": report["data_end"],
              "features": FEATURES, "targets": {}}

    print("[TRAIN] Walk-forward test (each quarter scored by a model that never saw it)")
    for target in TARGETS:
        folds, oof = walk_forward(data, target)
        if not folds:
            print(f"[STOP] Not enough history for walk-forward testing on '{target}'.")
            return
        s = summarize_folds(folds)
        chosen = max(MODEL_NAMES, key=lambda n: s[n]["mean_xs_auc"])
        wins_vs_rule = sum(1 for f in folds if f[f"{chosen}_top_hit"] > f["baseline_top_hit"])
        beats = (s[chosen]["mean_xs_auc"] - s["baseline"]["mean_xs_auc"] >= MIN_AUC_EDGE
                 and wins_vs_rule >= len(folds) / 2)

        iso = PlattCalibrator().fit(oof[f"p_{chosen}"], oof[target])

        lab = data[data[target].notna()].reset_index(drop=True)
        weights = np.ones(len(lab))
        if not live.empty:
            live_lab = live[live[target].notna()]
            if len(live_lab):
                lab = pd.concat([lab, live_lab[lab.columns.intersection(live_lab.columns)]], ignore_index=True)
                weights = np.concatenate([weights, np.full(len(live_lab), live_weight)])
        print(f"[TRAIN] Final {chosen} model for '{target}' on {len(lab):,} rows")
        final = fit_model(chosen, lab[FEATURES], lab[target], weights)

        live_rows = evaluate_live_picks(data[data[target].notna()].reset_index(drop=True),
                                        live, oof, target, chosen)
        report["targets"][target] = {
            "chosen_model": chosen, "beats_baseline": bool(beats), "summary": s, "folds": folds,
            "calibration": calibration_table(oof[target], iso.predict(oof[f"p_{chosen}"])),
            "importance": feature_importance(final, lab, target),
        }
        if target == "win":
            report["live"] = {"rows": live_rows, "summary": live_summary(live_rows)}
        bundle["targets"][target] = {"model": final, "calibrator": iso, "chosen": chosen}

    def target_status(beats):
        if beats and graded >= MIN_COHORTS_FOR_TRUST:
            return "TRUSTED"
        if beats:
            return "PROMISING (beats RSI rule on unseen data; waiting for 3+ graded cohorts)"
        return "NO EDGE OVER RSI RULE YET (experimental)"

    per_target = {t: target_status(report["targets"][t]["beats_baseline"]) for t in TARGETS}
    trusted = {t: per_target[t] == "TRUSTED" for t in TARGETS}
    status = " | ".join(f"{t}: {per_target[t]}" for t in TARGETS)
    report["status"], report["trustworthy"] = status, trusted
    bundle["status"], bundle["trustworthy"] = status, trusted

    with open(MODEL_FILE, "wb") as f:
        pickle.dump(bundle, f)
    write_json(REPORT_JSON, report)
    write_report(clean_json(report))
    write_predictions(bundle, predict_latest(bundle, latest))

    print(f"\nStatus: {status}")
    for target, t in report["targets"].items():
        s, ch = t["summary"], t["chosen_model"]
        print(f"  {target:<4}: {ch} same-day AUC {s[ch]['mean_xs_auc']:.3f} vs RSI rule {s['baseline']['mean_xs_auc']:.3f} | "
              f"Top-20 hit {s[ch]['mean_top_hit'] * 100:.1f}% vs rule {s['baseline']['mean_top_hit'] * 100:.1f}% "
              f"vs universe {s['base_rate'] * 100:.1f}%")
    print(f"Wrote {REPORT_MD}, {REPORT_JSON}, {MODEL_FILE}, {PRED_FILE}")


if __name__ == "__main__":
    main()
