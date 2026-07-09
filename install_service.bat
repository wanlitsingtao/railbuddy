@echo off
REM RailBuddy Service Manager - launches install_service.py with admin rights
REM Usage: install_service.bat [install^|uninstall^|start^|stop^|restart^|status]

set ACTION=%1
if "%ACTION%"=="" set ACTION=install

set PYTHON=%~dp0venv\Scripts\python.exe
set SCRIPT=%~dp0install_service.py

REM Check if Python exists
if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    echo Please run install.bat first to create the virtual environment.
    pause
    exit /b 1
)

REM Check if script exists
if not exist "%SCRIPT%" (
    echo [ERROR] Script not found: %SCRIPT%
    pause
    exit /b 1
)

REM Request admin privileges
net session >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%PYTHON%\" \"%SCRIPT%\" %ACTION% & pause' -Verb RunAs"
    exit /b 0
)

REM Already admin, run directly
"%PYTHON%" "%SCRIPT%" %ACTION%
pause
