@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe scripts\start_semif_pilot.py --interface desktop
if errorlevel 1 pause
