@echo off
:: This is what Task Scheduler points to. It exists ONLY to suppress the
:: "pause" at the end of daily_refresh_all.bat, which would otherwise hang
:: forever with nobody there to press a key. All actual pipeline logic
:: lives in daily_refresh_all.bat - kept in exactly one place so the two
:: can never drift out of sync again.
set SCHEDULED_RUN=1
cd /d "%~dp0"
call daily_refresh_all.bat
