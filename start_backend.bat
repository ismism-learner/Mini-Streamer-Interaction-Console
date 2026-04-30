@echo off
chcp 65001 >nul
title xiaozhubo-backend

cd /d "C:\Users\haokun\Documents\trae_projects\小主播互动机"

echo ========================================
echo    xiaozhubo backend server
echo ========================================
echo.

python -m backend.main

echo.
echo [error] backend exited unexpectedly.
pause