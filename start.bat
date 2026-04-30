@echo off
chcp 65001 >nul
title xiaozhubo

cd /d "C:\Users\haokun\Documents\trae_projects\小主播互动机"

echo ========================================
echo    xiaozhubo assistant launcher
echo ========================================
echo.
echo [config] threshold: 50 chars (test mode)
echo [config] model: Qwen/Qwen3.6-27B
echo [config] port: 8765
echo.

echo [1/2] starting backend (new window)...
start "xiaozhubo-backend" cmd /k python -m backend.main

echo.
echo [2/2] starting overlay frontend...
python frontend_overlay.py

echo.
pause