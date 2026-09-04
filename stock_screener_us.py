import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
import json
import datetime

OUTPUT_NAME = "Complete_US_Market_Report.xlsx"
FAILED_LOG = "Screener_Failed_Tickers.txt"
HISTORY_DIR = "history"
HISTORY_DAYS = 90  # trading days of chart history kept per ticker

# Rolling trading-day window for the daily VWAP + standard-deviation bands.
# NOTE: this is a DAILY/rolling VWAP (volume-weighted average price over the
# last N trading days), not a true intraday session VWAP - the pipeline only
# refreshes once a day, so a session VWAP would be meaningless by the time
# anyone looks at it. 20 trading days (~1 month) matches the existing
# Bollinger Band window for consistency; change this if you want a
# shorter/longer lookback.
VWAP_WINDOW = 20

def read_tickers_from_reference(ref_file="US_Ticker_Reference_Map.xlsx"):
    if not os.path.exists(ref_file):
        print(f"Error: Master directory map '{ref_file}' not found! "
              f"Run us_ticker_reference_generator.py first.")
        return []
    try:
        df_ref = pd.read_excel(ref_file)
        if 'Ticker' in df_ref.columns:
            tickers = df_ref['Ticker'].dropna().astype(str).tolist()
            return [t.strip().upper() for t in tickers if t.strip()]
        else:
            tickers = df_ref.iloc[:, 0].dropna().astype(str).tolist()
            return [t.strip().upper() for t in tickers if t.strip()]
    except Exception as e:
        print(f"Error reading reference sheet mapping: {e}")
        return []

def read_category_map(ref_file="US_Ticker_Reference_Map.xlsx"):
    """Ticker -> Category (S&P 500 / Nasdaq Extended / Major ETF / Custom Watchlist)"""
    try:
        df_ref = pd.read_excel(ref_file)
        if 'Ticker' in df_ref.columns and 'Category' in df_ref.columns:
            return dict(zip(df_ref['Ticker'].astype(str).str.upper(), df_ref['Category']))
    except Exception:
        pass
    return {}

def calculate_indicators(df):
    if len(df) < 45: return df
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA40'] = df['Close'].ewm(span=40, adjust=False).mean()
    df['Middle_BB'] = df['Close'].rolling(window=20).mean()
    std_dev = df['Close'].rolling(window=20).std()
    df['Upper_BB'] = df['Middle_BB'] + (2 * std_dev)
    df['Lower_BB'] = df['Middle_BB'] - (2 * std_dev)

    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-9)
    df['RSI'] = 100 - (100 / (1 + rs))

    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Histogram'] = df['MACD'] - df['MACD_Signal']  # the "bar chart" under MACD -
    # shrinking bars (even while still positive/above zero) signal weakening
    # momentum, often before the price itself turns down

    vol_window = min(200, len(df))
    df['Vol_200SMA'] = df['Volume'].rolling(window=vol_window).mean()

    # Rolling daily VWAP + standard-deviation bands (volume-weighted, over
    # VWAP_WINDOW trading days). Typical Price = (H+L+C)/3, the standard
    # approximation for VWAP when only daily OHLCV bars are available
    # (matches how most charting platforms compute it off daily data).
    #
    # Var_w = E_w[TP^2] - (E_w[TP])^2 is the volume-weighted-variance
    # identity - lets this run as vectorized rolling sums instead of a much
    # slower per-row rolling().apply().
    vwap_window = min(VWAP_WINDOW, len(df))
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    tp_vol = typical_price * df['Volume']
    roll_vol = df['Volume'].rolling(window=vwap_window).sum()
    roll_tpv = tp_vol.rolling(window=vwap_window).sum()
    df['VWAP'] = roll_tpv / roll_vol
    roll_tp2v = ((typical_price ** 2) * df['Volume']).rolling(window=vwap_window).sum()
    vwap_variance = (roll_tp2v / roll_vol) - (df['VWAP'] ** 2)
    df['VWAP_SD'] = np.sqrt(vwap_variance.clip(lower=0))  # clip guards tiny
    # negative values from floating-point rounding in the variance identity
    df['VWAP_Upper1'] = df['VWAP'] + df['VWAP_SD']
    df['VWAP_Lower1'] = df['VWAP'] - df['VWAP_SD']
    df['VWAP_Upper2'] = df['VWAP'] + 2 * df['VWAP_SD']
    df['VWAP_Lower2'] = df['VWAP'] - 2 * df['VWAP_SD']
    return df

def save_ticker_history(symbol, hist):
    """Writes a compact per-ticker JSON of recent daily indicator values for
    dashboard charting (MACD panel, Price/EMA panel, RSI panel, Volume panel).
    Called with the SAME `hist` DataFrame already computed in evaluate_ticker -
    no extra yfinance calls, just persisting data that was already being
    fetched and then discarded after grabbing the latest row."""
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        tail = hist.tail(HISTORY_DAYS).copy()

        def col(name, decimals=2):
            if name not in tail.columns:
                return []
            return [None if pd.isna(v) else round(float(v), decimals) for v in tail[name]]

        payload = {
            "ticker": symbol,
            # NOTE: uses %b + int(day) rather than "%b %-d" - the %-d flag to
            # strip a leading zero from the day is a Linux/Mac-only strftime
            # extension. Windows' underlying C library doesn't support it and
            # raises "ValueError: Invalid format string" for every ticker.
            "labels": [f"{d.strftime('%b')} {d.day}" if hasattr(d, "strftime") else str(d) for d in tail.index],
            "price": col("Close"),
            "ema20": col("EMA20"),
            "ema40": col("EMA40"),
            "middle_bb": col("Middle_BB"),
            "upper_bb": col("Upper_BB"),
            "lower_bb": col("Lower_BB"),
            "rsi": col("RSI", 1),
            "macd": col("MACD", 3),
            "signal": col("MACD_Signal", 3),
            "histogram": col("MACD_Histogram", 3),
            "volume": [None if pd.isna(v) else round(float(v) / 1e6, 2) for v in tail["Volume"]] if "Volume" in tail.columns else [],
            "vol_200sma": [None if pd.isna(v) else round(float(v) / 1e6, 2) for v in tail["Vol_200SMA"]] if "Vol_200SMA" in tail.columns else [],
            "vwap": col("VWAP"),
            "vwap_upper1": col("VWAP_Upper1"),
            "vwap_lower1": col("VWAP_Lower1"),
            "vwap_upper2": col("VWAP_Upper2"),
            "vwap_lower2": col("VWAP_Lower2"),
        }
        with open(os.path.join(HISTORY_DIR, f"{symbol}.json"), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        # Never let a history-write failure break the main scoring pipeline
        print(f"  [WARNING] Could not save chart history for {symbol}: {e}", flush=True)

def get_institutional_options_walls(ticker_obj):
    try:
        options_dates = ticker_obj.options
        if not options_dates: return "N/A", "N/A", 0, "N/A", 0
        chain = ticker_obj.option_chain(options_dates[0])
        puts, calls = chain.puts, chain.calls

        total_put_oi = puts['openInterest'].sum()
        total_call_oi = calls['openInterest'].sum()
        pcr_oi = round(total_put_oi / (total_call_oi + 1e-9), 2)

        max_put_idx = puts['openInterest'].idxmax()
        major_put_wall = puts.loc[max_put_idx, 'strike']
        put_wall_oi = puts.loc[max_put_idx, 'openInterest']

        max_call_idx = calls['openInterest'].idxmax()
        major_call_wall = calls.loc[max_call_idx, 'strike']
        call_wall_oi = calls.loc[max_call_idx, 'openInterest']

        return pcr_oi, float(major_put_wall), int(put_wall_oi), float(major_call_wall), int(call_wall_oi)
    except Exception: return "N/A", "N/A", 0, "N/A", 0

def calculate_dcf(ticker_obj, info, current_price):
    try:
        if info.get('quoteType') == 'ETF': return "N/A", "ETF"
        fcf = info.get('freeCashFlow', None)
        shares = info.get('sharesOutstanding', None)
        if not fcf or fcf <= 0:
            try:
                cashflow_sheet = ticker_obj.cashflow
                if not cashflow_sheet.empty and 'Free Cash Flow' in cashflow_sheet.index:
                    fcf = float(cashflow_sheet.loc['Free Cash Flow'].iloc[0])
            except Exception: pass
        if not fcf or not shares or fcf <= 0 or shares <= 0: return "N/A", "No Data"

        growth_rate, discount_rate, terminal_growth = 0.08, 0.10, 0.02
        proj_fcf = [fcf * ((1 + growth_rate) ** i) for i in range(1, 6)]
        dcf_val = sum([val / ((1 + discount_rate) ** idx) for idx, val in enumerate(proj_fcf, start=1)])
        terminal_value = (proj_fcf[-1] * (1 + terminal_growth)) / (discount_rate - terminal_growth)
        discounted_tv = terminal_value / ((1 + discount_rate) ** 5)

        intrinsic_per_share = (dcf_val + discounted_tv) / shares
        margin_of_safety = ((intrinsic_per_share - current_price) / intrinsic_per_share) * 100
        return round(intrinsic_per_share, 2), f"{round(margin_of_safety, 1)}%"
    except Exception: return "N/A", "Error"

def fetch_history_with_retry(ticker_obj, retries=3, base_delay=2.0):
    """Yahoo Finance rate-limits/blocks aggressively past a few hundred rapid
    requests. Retry with backoff instead of giving up on the first failure."""
    last_err = None
    for attempt in range(retries):
        try:
            hist = ticker_obj.history(period="2y", timeout=10)
            if not hist.empty:
                return hist, None
            last_err = "empty history returned"
        except Exception as e:
            last_err = str(e)
        if attempt < retries - 1:
            time.sleep(base_delay * (attempt + 1))  # 2s, 4s, ...
    return pd.DataFrame(), last_err

def evaluate_ticker(symbol, category_map=None):
    """Returns (data_dict_or_None, failure_reason_or_None)."""
    ticker = yf.Ticker(symbol)
    hist, err = fetch_history_with_retry(ticker)
    if hist.empty:
        return None, err or "insufficient history (0 rows, need 45+)"

    # Drop any trailing row(s) where Close is NaN - this happens when the
    # most recent trading day is still in progress (Yahoo shows a live-
    # forming candle with Open/High/Low ticking but no Close until the
    # session actually ends). Without this, evaluate_ticker() would grab
    # an incomplete "today" row via iloc[-1] and fail almost every ticker,
    # every time the script runs before/during market hours.
    _rows_before_dropna = len(hist)
    _last_raw_date = hist.index[-1].date() if _rows_before_dropna else None
    hist = hist.dropna(subset=['Close'])
    if len(hist) < _rows_before_dropna:
        _last_complete_date = hist.index[-1].date() if len(hist) else "N/A"
        print(f"[DIAG] {symbol}: dropped {_rows_before_dropna - len(hist)} row(s) with NaN Close. "
              f"Most recent raw date from Yahoo was {_last_raw_date}, "
              f"most recent COMPLETE (usable) date is {_last_complete_date}.")
    if hist.empty or len(hist) < 45:
        return None, err or f"insufficient COMPLETE history ({len(hist)} rows, need 45+)"

    hist = calculate_indicators(hist)
    save_ticker_history(symbol, hist)
    last_row = hist.iloc[-1]
    current_price = float(last_row['Close'])
    lookback_window = min(252, len(hist))
    high_52wk = float(hist['High'].iloc[-lookback_window:].max())

    if np.isnan(current_price) or np.isnan(high_52wk):
        return None, "NaN price data"
    try: info = ticker.get_info()
    except Exception: info = {}

    stock_name = info.get('shortName', symbol)
    pct_from_high = ((current_price - high_52wk) / high_52wk) * 100

    lower_bb = last_row.get('Lower_BB', np.nan)
    middle_bb = last_row.get('Middle_BB', np.nan)
    upper_bb = last_row.get('Upper_BB', np.nan)
    rsi_val = last_row.get('RSI', np.nan)
    macd_line = last_row.get('MACD', np.nan)
    macd_sig = last_row.get('MACD_Signal', np.nan)
    vol_sma = last_row.get('Vol_200SMA', np.nan)

    # MACD Histogram (the "momentum bars") and its trend - a shrinking
    # histogram, even while still positive, often signals fading momentum
    # BEFORE the price itself turns down. Compares today's bar against 3
    # trading days ago (not just yesterday) to filter out single-day noise.
    vwap = last_row.get('VWAP', np.nan)
    vwap_sd = last_row.get('VWAP_SD', np.nan)
    vwap_upper2 = last_row.get('VWAP_Upper2', np.nan)
    vwap_lower2 = last_row.get('VWAP_Lower2', np.nan)
    # Signed distance from VWAP in standard-deviation units - e.g. +2.1 means
    # "2.1 SD above VWAP, extremely overbought"; -2.1 means "extremely
    # oversold." This is the single number the dashboard badge and the
    # calculator both key off of.
    vwap_sd_distance = ((current_price - vwap) / vwap_sd
                         if not np.isnan(vwap) and not np.isnan(vwap_sd) and vwap_sd > 0
                         else np.nan)

    macd_hist_now = last_row.get('MACD_Histogram', np.nan)
    hist_lookback = 3
    macd_hist_trend = "N/A"
    macd_hist_prior = np.nan
    if not np.isnan(macd_hist_now) and len(hist) > hist_lookback:
        macd_hist_prior = hist['MACD_Histogram'].iloc[-1 - hist_lookback]
        if not np.isnan(macd_hist_prior):
            diff = macd_hist_now - macd_hist_prior
            # Threshold scales with the prior bar's size so it's not overly
            # sensitive on penny-difference noise for low-priced/low-volatility names
            threshold = abs(macd_hist_prior) * 0.10 + 0.005
            if diff > threshold:
                macd_hist_trend = "RISING"
            elif diff < -threshold:
                macd_hist_trend = "FALLING"
            else:
                macd_hist_trend = "FLAT"

    dist_to_lower = current_price - lower_bb if not np.isnan(lower_bb) else 0
    dist_to_upper = upper_bb - current_price if not np.isnan(upper_bb) else 0
    # If price has already dropped below support (dist_to_lower <= 0), the
    # "risk" side of the ratio is no longer meaningful - dividing by a
    # near-zero or negative number produces a huge, nonsensical value (e.g.
    # -151.83) rather than something honest. Report N/A instead: the
    # underlying reality is "support has already broken," which a single
    # number can't represent cleanly anyway. rr_note explains WHY it's N/A,
    # since "missing data" and "support already broken" are very different
    # situations worth telling apart on the dashboard.
    have_bb_data = upper_bb and lower_bb and not np.isnan(upper_bb) and not np.isnan(lower_bb)
    if have_bb_data and dist_to_lower > 0:
        risk_reward = round(dist_to_upper / dist_to_lower, 2)
        rr_note = None
    elif have_bb_data:
        risk_reward = "N/A"
        rr_note = "Support already broken - price is below its support level"
    else:
        risk_reward = "N/A"
        rr_note = "Missing Bollinger Band data"

    score = 0
    if not np.isnan(last_row.get('EMA20', np.nan)) and not np.isnan(last_row.get('EMA40', np.nan)):
        if last_row['EMA20'] > last_row['EMA40']: score += 1
        else: score -= 1
    if not np.isnan(middle_bb):
        if current_price < middle_bb: score += 1
        else: score -= 1
    if not np.isnan(rsi_val):
        if rsi_val < 35: score += 1
        elif rsi_val > 65: score -= 1
    if not np.isnan(macd_line) and not np.isnan(macd_sig):
        if macd_line > macd_sig: score += 1
        else: score -= 1
    if not np.isnan(vol_sma):
        if last_row['Volume'] > vol_sma: score += 1
        else: score -= 1
    if pct_from_high < -15: score += 1
    elif pct_from_high > -3: score -= 1

    signal = "BUY" if score >= 2 else ("SELL" if score <= -2 else "WATCHLIST")
    intrinsic_val, margin_safety = calculate_dcf(ticker, info, current_price)
    pcr_ratio, whale_put_wall, put_wall_oi, whale_call_wall, call_wall_oi = get_institutional_options_walls(ticker)
    # The put wall (highest-OI put strike) and call wall (highest-OI call
    # strike) are picked completely independently - nothing forces the put
    # wall to sit below the call wall. For thinly-traded options names, this
    # can genuinely flip (put wall above call wall), which isn't wrong data,
    # but IS worth flagging as low-confidence rather than looking like an
    # unexplained bug when someone sees "Floor" above "Ceiling."
    walls_crossed = (isinstance(whale_put_wall, (int, float)) and isinstance(whale_call_wall, (int, float))
                     and whale_put_wall > whale_call_wall)

    # Analyst target price - Wall Street's mean 12-month price target, from
    # the same info dict already fetched above (no extra API call needed)
    analyst_target = info.get('targetMeanPrice')
    num_analysts = info.get('numberOfAnalystOpinions')
    if analyst_target and isinstance(analyst_target, (int, float)):
        analyst_target = round(analyst_target, 2)
        analyst_upside_pct = f"{round((analyst_target - current_price) / current_price * 100, 1)}%"
    else:
        analyst_target, analyst_upside_pct = "N/A", "N/A"

    category = (category_map or {}).get(symbol, "N/A")

    return {
        "Ticker": symbol, "Category": category, "Name": stock_name[:20],
        "Current Price": round(current_price, 2),
        "52W High Price": round(high_52wk, 2), "52W High Drop %": f"{round(pct_from_high, 1)}%",
        "EMA20": round(last_row['EMA20'], 2) if not np.isnan(last_row.get('EMA20', np.nan)) else "N/A",
        "EMA40": round(last_row['EMA40'], 2) if not np.isnan(last_row.get('EMA40', np.nan)) else "N/A",
        "Support (Lower BB)": round(lower_bb, 2) if not np.isnan(lower_bb) else "N/A",
        "Middle BB": round(middle_bb, 2) if not np.isnan(middle_bb) else "N/A",
        "Resistance (Upper BB)": round(upper_bb, 2) if not np.isnan(upper_bb) else "N/A",
        "VWAP (20d)": round(vwap, 2) if not np.isnan(vwap) else "N/A",
        "VWAP +2SD": round(vwap_upper2, 2) if not np.isnan(vwap_upper2) else "N/A",
        "VWAP -2SD": round(vwap_lower2, 2) if not np.isnan(vwap_lower2) else "N/A",
        "VWAP Distance (SD)": round(vwap_sd_distance, 2) if not np.isnan(vwap_sd_distance) else "N/A",
        "RSI": round(rsi_val, 1) if not np.isnan(rsi_val) else "N/A",
        "MACD Line": round(macd_line, 3) if not np.isnan(macd_line) else "N/A",
        "MACD Signal": round(macd_sig, 3) if not np.isnan(macd_sig) else "N/A",
        "MACD Histogram": round(macd_hist_now, 3) if not np.isnan(macd_hist_now) else "N/A",
        "MACD Histogram Trend": macd_hist_trend,
        "Current Vol": int(last_row['Volume']),
        "Vol 200SMA": int(vol_sma) if not np.isnan(vol_sma) else "N/A",
        "Volume Driven Strength": "HIGH" if (not np.isnan(vol_sma) and last_row['Volume'] > vol_sma) else "LOW",
        "R/R Ratio": risk_reward, "R/R Ratio Note": rr_note, "Intrinsic (Fair) Value": intrinsic_val, "Margin of Safety": margin_safety,
        "Put/Call OI Ratio": pcr_ratio, "Whale Put Wall Price (Floor)": whale_put_wall,
        "Put Wall Open Interest Volume": put_wall_oi, "Whale Call Wall Price (Ceiling)": whale_call_wall,
        "Walls Crossed (Low Confidence)": walls_crossed,
        "Call Wall Open Interest Volume": call_wall_oi, "Matrix Score": score, "Signal": signal,
        "Analyst Target Price": analyst_target, "Analyst Upside %": analyst_upside_pct,
        "Num Analyst Opinions": num_analysts if num_analysts else "N/A",
        "Last Updated": datetime.date.today().isoformat(),
    }, None

def load_existing_results():
    """Resume support: if a report already exists AND was last written TODAY,
    skip tickers already in it so a re-run only chases down what's still
    missing (e.g. after a rate-limit wall cut a run short earlier today).

    Critically: if the existing file is from a PREVIOUS day, it's stale -
    we do a full fresh fetch instead of reusing yesterday's (or last week's)
    prices forever. Without this check, any ticker that succeeded even once
    would never be re-fetched again on any future day, silently going stale."""
    if not os.path.exists(OUTPUT_NAME):
        return None
    try:
        mtime = datetime.date.fromtimestamp(os.path.getmtime(OUTPUT_NAME))
        if mtime != datetime.date.today():
            print(f"[NOTE] Existing '{OUTPUT_NAME}' is from {mtime}, not today - "
                  f"treating as stale and re-fetching everything fresh (not resuming).")
            return None
        df = pd.read_excel(OUTPUT_NAME)
        if 'Ticker' in df.columns and len(df) > 0:
            print(f"[NOTE] Existing '{OUTPUT_NAME}' is from today - resuming "
                  f"(only fetching tickers not already done today).")
            return df
    except Exception:
        pass
    return None

def preflight_check():
    """Tests ONE well-known, always-liquid ticker (AAPL) before touching the
    other 633 - if this fails, it's a broken yfinance/environment issue, not
    a data problem, and there's no point waiting 15 minutes to find that out
    the hard way. Prints diagnostics to help pinpoint the actual cause."""
    print("=" * 60)
    print(" PRE-FLIGHT CHECK: testing yfinance against AAPL first...")
    print("=" * 60)
    try:
        print(f"yfinance version: {yf.__version__}")
    except Exception:
        print("yfinance version: could not determine")

    try:
        test = yf.Ticker("AAPL")
        hist = test.history(period="5d", timeout=10)
        if hist.empty:
            print("[PRE-FLIGHT FAILED] AAPL returned an EMPTY history. "
                  "This is an environment/library issue, not a data issue - "
                  "even AAPL should always have data.")
            return False
        # Drop any trailing row where Close is NaN - this is normal and
        # expected if the most recent trading day hasn't closed yet (Yahoo
        # shows a live-forming candle with no Close until the session ends).
        # It does NOT mean anything is broken.
        complete_hist = hist.dropna(subset=['Close'])
        if complete_hist.empty:
            print(f"[PRE-FLIGHT FAILED] All {len(hist)} rows for AAPL are missing Close "
                  f"prices, including older/closed trading days. That's NOT just an "
                  f"in-progress-day thing - something is actually broken.")
            print("Try, in order:")
            print("  1. pip install --upgrade yfinance")
            print("  2. pip install --upgrade curl_cffi")
            print("  3. Delete the yfinance cache folder (Windows: "
                  "%USERPROFILE%\\AppData\\Local\\py-yfinance\\) and retry")
            return False
        last_close = complete_hist['Close'].iloc[-1]
        if len(complete_hist) < len(hist):
            print(f"[NOTE] {len(hist) - len(complete_hist)} trailing row(s) dropped - "
                  f"in-progress trading day(s) with no Close yet. Normal, not an error.")
        print(f"[PRE-FLIGHT OK] AAPL last close: ${last_close:.2f} ({len(complete_hist)} "
              f"complete rows). yfinance is working correctly - proceeding with full scan.")
        print("=" * 60)
        return True
    except Exception as e:
        print(f"[PRE-FLIGHT FAILED] AAPL fetch raised an exception: {e}")
        print("This points to a connectivity, library version, or authentication issue.")
        return False


def main():
    if not preflight_check():
        print("\n[CANCELLED] Pre-flight check failed - fix the yfinance/environment "
              "issue above before running the full 634-ticker scan (no point waiting "
              "15 minutes just to see the same failure 633 times).")
        return

    tickers = read_tickers_from_reference()
    if not tickers:
        print("[CANCELLED] Ticker extraction array returned empty. Verify your reference maps.")
        return
    category_map = read_category_map()

    existing_df = load_existing_results()
    already_done = set()
    if existing_df is not None:
        already_done = set(existing_df['Ticker'].astype(str).str.upper())
        print(f"Found existing '{OUTPUT_NAME}' with {len(already_done)} tickers already done - "
              f"resuming, will only fetch what's missing.", flush=True)

    tickers_to_process = [t for t in tickers if t not in already_done]
    print(f"Loaded {len(tickers)} US assets from reference map "
          f"({len(already_done)} already done, {len(tickers_to_process)} remaining). "
          f"Beginning broad sweep...", flush=True)

    results = []
    failed = []

    for idx, ticker in enumerate(tickers_to_process, 1):
        if idx % 25 == 0 or idx == 1 or idx == len(tickers_to_process):
            print(f"Dynamic Scan Matrix: [{idx}/{len(tickers_to_process)}] processed... "
                  f"({len(failed)} failed so far)", flush=True)
        try:
            data, err = evaluate_ticker(ticker, category_map)
            if data:
                results.append(data)
            else:
                failed.append((ticker, err or "unknown"))
        except Exception as e:
            failed.append((ticker, f"unexpected error: {e}"))

        time.sleep(0.5)
        # Extra cooldown every 50 tickers to avoid tripping Yahoo's rate limiter
        if idx % 50 == 0:
            print("  ...pausing 8s to avoid rate limiting...", flush=True)
            time.sleep(8)

    # Combine with any existing results (resume mode)
    all_results = (existing_df.to_dict('records') if existing_df is not None else []) + results
    if not all_results:
        print("[FAILED] No tickers succeeded this run. See failure log below.")
    else:
        df_final = pd.DataFrame(all_results).drop_duplicates(subset=['Ticker'], keep='last') \
                                             .sort_values(by="Matrix Score", ascending=False)
        try:
            df_final.to_excel(OUTPUT_NAME, index=False)
            print(f"\n[SUCCESS] {len(df_final)} total tickers in report. Saved to: {OUTPUT_NAME}")
        except PermissionError:
            ts = time.strftime("%Y%m%d-%H%M%S")
            df_final.to_excel(f"Complete_US_Market_Report_{ts}.xlsx", index=False)

        # Staleness summary - some tickers may be carrying over an OLDER
        # successful fetch if they've been failing on recent runs. This
        # makes that visible immediately instead of silently persisting.
        if "Last Updated" in df_final.columns:
            today_str = datetime.date.today().isoformat()
            fresh_count = (df_final["Last Updated"] == today_str).sum()
            stale_count = len(df_final) - fresh_count
            print(f"[INFO] {fresh_count} ticker(s) updated today, {stale_count} carrying over "
                  f"an older price (check the 'Last Updated' column in {OUTPUT_NAME} - "
                  f"or 'Screener_Failed_Tickers.txt' for why they're not refreshing).")

        # Archive a timestamped copy every run - a verifiable paper trail so
        # you (or I) can always check exactly what data existed at exactly
        # what time, in case anything ever looks stale or wrong again.
        try:
            os.makedirs("Report_History", exist_ok=True)
            archive_name = f"Report_History/Complete_US_Market_Report_{time.strftime('%Y-%m-%d_%H%M')}.xlsx"
            df_final.to_excel(archive_name, index=False)
            print(f"[SUCCESS] Archived a timestamped copy to: {archive_name}")
        except Exception as e:
            print(f"[WARNING] Could not write archive copy: {e}")

    if failed:
        print(f"\n[WARNING] {len(failed)} tickers failed this run. See '{FAILED_LOG}' for details.")
        with open(FAILED_LOG, "w") as f:
            f.write(f"{len(failed)} tickers failed - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("Re-run stock_screener_us.py to retry ONLY these (resume mode skips completed ones).\n\n")
            for t, reason in failed:
                f.write(f"{t}: {reason}\n")
        print("Tip: just run this script again - resume mode will only chase the failed ones.")

if __name__ == '__main__': main()
