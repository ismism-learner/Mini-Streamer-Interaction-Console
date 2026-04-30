@echo off
chcp 65001 >nul
title xiaozhubo-overlay

cd /d "C:\Users\haokun\Documents\trae_projects\小主播互动机"

echo ========================================
echo    xiaozhubo overlay frontend
echo ========================================
echo.
echo Make sure the backend is running first!
echo (double-click start_backend.bat)
echo.

python frontend_overlay.py

echo.
echo [error] overlay exited unexpectedly.
pause