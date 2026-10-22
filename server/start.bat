@echo off
chcp 65001 >nul
title Maimen Xinshi Demo
set "NODE=C:\Program Files\nodejs\node.exe"
if not exist "%NODE%" set "NODE=node"
echo Starting local demo server on http://localhost:8765 ...
start "" "http://localhost:8765/?demo=1"
"%NODE%" "%~dp0server.js"
pause
