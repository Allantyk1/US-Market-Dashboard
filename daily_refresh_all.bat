@echo off
title Daily US Market Refresh - Full Pipeline
color 0B
cd /d "%~dp0"

echo ============================================================
echo  STEP 1/12: Building ticker reference map
echo ============================================================
python us_ticker_reference_generator.py

echo.
echo ============================================================
echo  STEP 2/12: Running live screener (this takes ~10-15 min)
echo ============================================================
python stock_screener_us.py

echo.
echo ============================================================
echo  STEP 3/12: Building Excel Calculator
echo ============================================================
python generate_vlookup_calculator.py

echo.
echo ============================================================
echo  STEP 4/12: Building PowerPoint Report
echo ============================================================
python generate_us_market_report.py

echo.
echo ============================================================
echo  STEP 5/12: Updating Trade Tracker (My Trades holdings + realized P/L)
echo ============================================================
python generate_trade_tracker.py

echo.
echo ============================================================
echo  STEP 6/12: Updating Portfolio Tracker (holdings synced from My Trades)
echo ============================================================
python generate_portfolio_tracker.py

echo.
echo ============================================================
echo  STEP 7/12: Publishing dashboard data (for mobile dashboard)
echo ============================================================
python generate_dashboard_data.py

echo.
echo ============================================================
echo  STEP 8/12: Logging monthly Top-20 recommendation snapshot
echo ============================================================
python log_top20_snapshot.py

echo.
echo ============================================================
echo  STEP 9/12: Building Top-20 Pick Tracker (tracker.html data)
echo ============================================================
python generate_pick_tracker.py

echo.
echo ============================================================
echo  STEP 10/12: Valuation dialog data (Top 20 + watchlist + 3M best)
echo ============================================================
python generate_valuation.py

echo.
echo ============================================================
echo  STEP 11/12: Checking alerts (sends phone notification if triggered)
echo ============================================================
python check_alerts.py

echo.
echo ============================================================
echo  STEP 12/12: Publishing to GitHub (live dashboard)
echo ============================================================
call publish_to_web.bat

echo.
echo ============================================================
echo  ALL DONE. Files ready in this folder:
echo    - Complete_US_Market_Report.xlsx        (raw screener data)
echo    - US_Market_Screener_Calculator.xlsx    (Top 20 + 2-month calculator)
echo    - US_Market_Technical_Analysis_Report.pptx
echo    - Trade_Tracker.xlsx                    (My Trades buy/sell journal + realized P/L)
echo    - Portfolio_Tracker.xlsx                (My Portfolio vs Recommended)
echo    - dashboard_data.json + index.html  (mobile dashboard - see MOBILE_SETUP.md)
echo    - pick_tracker_data.json + tracker.html (Top-20 Pick Tracker)
echo    - valuation_data.json + valuation.js    (Valuation dialog, fundamentals weekly)
echo    - Screener_Failed_Tickers.txt           (only if any tickers failed)
echo    - Live site pushed to GitHub Pages (see MOBILE_SETUP.md for your URL)
echo ============================================================
if not defined SCHEDULED_RUN pause
