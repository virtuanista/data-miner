@echo off
title DataMiner
cd /d "%~dp0"
rem Abre la aplicacion de escritorio sin dejar una consola abierta. Para la version en navegador: dm web
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m dataminer app %*
) else (
  ".venv\Scripts\python.exe" -m dataminer app %*
  if errorlevel 1 pause
)
