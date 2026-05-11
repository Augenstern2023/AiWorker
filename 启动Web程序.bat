@echo off

:: Change to the script directory
cd /d %~dp0

:: Activate virtual environment
call .venv\Scripts\activate.bat

:: Run the main program
python WebGUI.py

:: Pause to view output (optional, comment out if not needed)
pause