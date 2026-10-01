@echo off
REM ------------------------------------------------------------------
REM Monthly model review: grade cohorts -> RSI refinement -> retrain win-rate model
REM Run from the screener folder (same place as dashboard_data.json).
REM Safe to run any day; it only grades cohorts that are 30+ days old.
REM ------------------------------------------------------------------
cd /d "%~dp0"

echo.
echo [1/3] Grading matured Top-20 cohorts (evaluate_recommendations.py, using history\)
python -c "import evaluate_recommendations as e; e.HISTORY_DIR = 'history'; e.main()"
if errorlevel 1 (
    echo [FAILED] evaluate_recommendations.py - stopping.
    goto :end
)

echo.
echo [2/3] RSI-bucket refinement (refine_matrix_score.py)
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
echo [3/3] Retraining win-rate model (train_winrate_model.py)
python train_winrate_model.py
if errorlevel 1 (
    echo [FAILED] train_winrate_model.py
    goto :end
)

echo.
echo [DONE] Read winrate_model_report.md and recommendation_scorecard.csv
echo        Then git add / commit / push recommendation_scorecard.json recommendation_scorecard.csv winrate_predictions.json

:end
if not defined SCHEDULED_RUN pause
