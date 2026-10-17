@echo off
:: Pushes index.html, manifest.json, dashboard_data.json (your private
:: dashboard) AND public.html, public_data.json (the shareable version,
:: with zero portfolio data) to your GitHub repo, so Cloudflare can
:: auto-redeploy both.
::
:: ONE-TIME SETUP REQUIRED FIRST - see MOBILE_SETUP.md before running this.
:: This assumes a git repo already exists in this folder (or a
:: subfolder you've pointed GH_PAGES_DIR at) and is connected to GitHub.

setlocal
set GH_PAGES_DIR=.

:: Unattended (Task Scheduler) runs have nobody to type a GitHub username/
:: password. Without these, a missing/expired credential makes git sit at
:: "Username for 'https://github.com':" forever. With them, git fails fast
:: and the [FAILED] message below is printed instead. Manual runs are left
:: interactive so you can still enter the token by hand when setting up.
if defined SCHEDULED_RUN (
    set GIT_TERMINAL_PROMPT=0
    set GCM_INTERACTIVE=never
)

:: Scheduled runs happen in a background session where Git Credential Manager
:: can't show its sign-in pop-up, so git would fall back to asking for a
:: username nobody can type. GITAUTH makes pull/push (this script only) skip
:: GCM and read the saved token from C:\Users\Allan\.git-credentials instead.
:: That file is created the first time you run this script BY HAND and enter
:: Allantyk1 + your GitHub token (PAT) when asked. Added 2026-10-01.
set GITAUTH=-c credential.helper= -c credential.helper=store

cd /d "%~dp0%GH_PAGES_DIR%"

echo Publishing dashboard to GitHub Pages...
git add index.html manifest.json dashboard_data.json public.html public_data.json history recommendation_log.json
:: These two only exist once evaluate_recommendations.py has actually run
:: (first cohort matures ~30 days after being logged) - git add fails hard
:: on a missing pathspec and aborts the WHOLE command, which silently broke
:: every file above too, so they're guarded here instead of listed directly.
if exist recommendation_scorecard.json git add recommendation_scorecard.json
if exist recommendation_scorecard.csv git add recommendation_scorecard.csv
:: Win-rate model files (added 2026-10-01). Guarded with 'if exist' for the
:: same reason as above. winrate_model.pkl and winrate_price_cache.pkl are
:: deliberately NOT pushed - they're large, local-only, and in .gitignore.
if exist .gitignore git add .gitignore
if exist train_winrate_model.py git add train_winrate_model.py
if exist monthly_model_review.bat git add monthly_model_review.bat
if exist winrate_predictions.json git add winrate_predictions.json
if exist winrate_model_report.md git add winrate_model_report.md
:: Pick Tracker (added 2026-10-03) - tracker.html page, its data, the daily
:: snapshot log it builds on, and the script itself. Guarded like the rest.
if exist tracker.html git add tracker.html
if exist pick_tracker_data.json git add pick_tracker_data.json
if exist top20_daily_log.json git add top20_daily_log.json
if exist generate_pick_tracker.py git add generate_pick_tracker.py
:: Screener code itself (added 2026-10-15) so fixes to it are backed up
:: on GitHub with each daily publish, not only when pushed by hand.
if exist stock_screener_us.py git add stock_screener_us.py
:: Valuation dialog (added 2026-10-15) - the shared dialog script, its data
:: and the script that builds it. valuation_cache.json is local-only (raw
:: Yahoo fundamentals) and is not pushed. Guarded like the rest.
if exist valuation.js git add valuation.js
if exist valuation_data.json git add valuation_data.json
if exist generate_valuation.py git add generate_valuation.py
:: Look-ahead Strategy tab (added 2026-10-17). Reads the JSON files above,
:: no data file of its own. Linked from index.html only (it shows portfolio).
if exist lookahead.html git add lookahead.html
git commit -m "Daily dashboard update %date% %time%"

echo.
echo Syncing with GitHub before pushing (git pull) so this can't get rejected...
:: Committing first, THEN pulling, means the merge lands on a clean working
:: tree - pulling before the commit above can fail with "local changes would
:: be overwritten" if today's regenerated files differ from what's on GitHub.
git %GITAUTH% pull --no-edit
if not %ERRORLEVEL%==0 (
    echo.
    echo [FAILED] Git pull failed - a merge conflict, or GitHub sign-in failed.
    echo Run "git status"
    echo here to see what's conflicted, resolve it by hand, then run
    echo "git push" yourself once it's resolved.
    :: Guarded the same way as daily_refresh_all.bat's trailing pause - an
    :: unattended Task Scheduler run has nobody to press a key, so this used
    :: to hang forever and block every later scheduled run behind it.
    if not defined SCHEDULED_RUN pause
    exit /b 1
)

echo.
git %GITAUTH% push

if %ERRORLEVEL%==0 (
    echo.
    echo [SUCCESS] Dashboard published. Check MOBILE_SETUP.md for your live URL.
) else (
    echo.
    echo [FAILED] Git push failed. If it mentions authentication / terminal
    echo prompts disabled, the saved GitHub token is missing or expired - run
    echo publish_to_web.bat by hand once and paste a fresh token as the password.
    echo Otherwise see MOBILE_SETUP.md to check your setup,
    echo or run "git status" / "git remote -v" here to diagnose.
)
:: Same guard - this one fires even on a successful push, which is what was
:: leaving the Task Scheduler instance stuck "Running" every day even
:: though the publish itself had already finished.
if not defined SCHEDULED_RUN pause
