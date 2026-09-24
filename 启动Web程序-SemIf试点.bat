@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe scripts\start_semif_pilot.py --interface web
if errorlevel 1 pause
