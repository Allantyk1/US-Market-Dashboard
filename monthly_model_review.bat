@echo off
REM Hide pandas/yfinance deprecation warnings (harmless noise, added 2026-10-24)
set PYTHONWARNINGS=ignore
REM ------------------------------------------------------------------
REM Monthly model review: grade cohorts -> RSI refinement -> retrain win-rate model -> scorecard summary
REM Run from the screener folder (same place as dashboard_data.json).
REM Safe to run any day; it only grades cohorts that are 30+ days old.
REM ------------------------------------------------------------------
cd /d "%~dp0"

echo.
echo [1/4] Grading matured Top-20 cohorts (evaluate_recommendations.py, using history\)
python -c "import evaluate_recommendations as e; e.HISTORY_DIR = 'history'; e.main()"
if errorlevel 1 (
    echo [FAILED] evaluate_recommendations.py - stopping.
    goto :end
)

echo.
echo [2/4] RSI-bucket refinement (refine_matrix_score.py)
if exist refine_matrix_score.py (
    if exist indicator_backtest_report.json (
        python refine_matrix_score.py
    ) else (
        echo [SKIP] indicator_backtest_report.json not in this folder
    )
) else (
    echo [SKIP] refine_matrix_score.py not in this folder
)

echo.
echo [3/4] Retraining win-rate model (train_winrate_model.py)
python train_winrate_model.py
if errorlevel 1 (
    echo [FAILED] train_winrate_model.py
    goto :end
)

echo.
echo [4/4] Scorecard summary - winners, losers and guard rail (scorecard_summary.py)
if exist scorecard_summary.py (
    python scorecard_summary.py
) else (
    echo [SKIP] scorecard_summary.py not in this folder
)
echo.
echo [DONE] Details: winrate_model_report.md, scorecard_summary.md, recommendation_scorecard.csv
echo        These upload to GitHub automatically with the next publish_to_web.bat run.

:end
if not defined SCHEDULED_RUN pause
