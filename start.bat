@echo off
chcp 65001 >nul
title xiaozhubo

echo ========================================
echo    starting xiaozhubo assistant...
echo ========================================
echo.

cd /d "C:\Users\haokun\Documents\trae_projects\小主播互动机"

echo [config] threshold: 50 chars (test mode)
echo [config] model: Qwen/Qwen3.6-27B
echo [config] port: 8765
echo [config] frontend: http://127.0.0.1:8765
echo.

echo [start] starting backend...
echo.

python -m backend.main

pause