@echo off
title PitchBattle - Voice Arena Server
cd /d "%~dp0"
echo ========================================================
echo   PITCH BATTLE - Voice Arena & Multiplayer Karaoke
echo ========================================================
echo Starting FastAPI Web & WebSocket Server...
echo.
python app.py
pause
