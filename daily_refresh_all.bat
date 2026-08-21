@echo off
title Daily US Market Refresh - Full Pipeline
color 0B
cd /d "%~dp0"

echo ============================================================
echo  STEP 1/10: Building ticker reference map
echo ============================================================
python us_ticker_reference_generator.py

echo.
echo ============================================================
echo  STEP 2/10: Running live screener (this takes ~10-15 min)
echo ============================================================
python stock_screener_us.py

echo.
echo ============================================================
echo  STEP 3/10: Building Excel Calculator
echo ============================================================
python generate_vlookup_calculator.py

echo.
echo ============================================================
echo  STEP 4/10: Building PowerPoint Report
echo ============================================================
python generate_us_market_report.py

echo.
echo ============================================================
echo  STEP 5/10: Updating Trade Tracker (Latest Price + manual tickers)
echo ============================================================
python generate_trade_tracker.py

echo.
echo ============================================================
echo  STEP 6/10: Updating Portfolio Tracker (My Portfolio vs Recommended)
echo ============================================================
python generate_portfolio_tracker.py

echo.
echo ============================================================
echo  STEP 7/10: Publishing dashboard data (for mobile dashboard)
echo ============================================================
python generate_dashboard_data.py

echo.
echo ============================================================
echo  STEP 8/10: Logging monthly Top-20 recommendation snapshot
echo ============================================================
python log_top20_snapshot.py

echo.
echo ============================================================
echo  STEP 9/10: Checking alerts (sends phone notification if triggered)
echo ============================================================
python check_alerts.py

echo.
echo ============================================================
echo  STEP 10/10: Publishing to GitHub (live dashboard)
echo ============================================================
call publish_to_web.bat

echo.
echo ============================================================
echo  ALL DONE. Files ready in this folder:
echo    - Complete_US_Market_Report.xlsx        (raw screener data)
echo    - US_Market_Screener_Calculator.xlsx    (Top 20 + 2-month calculator)
echo    - US_Market_Technical_Analysis_Report.pptx
echo    - Trade_Tracker.xlsx                    (recommended vs actual log)
echo    - Portfolio_Tracker.xlsx                (My Portfolio vs Recommended)
echo    - dashboard_data.json + index.html  (mobile dashboard - see MOBILE_SETUP.md)
echo    - Screener_Failed_Tickers.txt           (only if any tickers failed)
echo    - Live site pushed to GitHub Pages (see MOBILE_SETUP.md for your URL)
echo ============================================================
if not defined SCHEDULED_RUN pause
