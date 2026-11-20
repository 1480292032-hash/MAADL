@echo off
chcp 65001 >nul
title MAADL 网页配置
cd /d "%~dp0"
start "" http://127.0.0.1:8512
python webui\server.py
pause
