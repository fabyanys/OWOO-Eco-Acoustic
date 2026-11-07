@echo off
title OWOO Eco-Acoustic LoRaWAN Command Center
cd /d "%~dp0"
echo ====================================================================
echo   OWOO Eco-Acoustic LoRaWAN Monitoring Server (Local Preview)
echo   Buka Browser: http://127.0.0.1:5000
echo ====================================================================
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py app.py
) else (
    python app.py
)
pause
