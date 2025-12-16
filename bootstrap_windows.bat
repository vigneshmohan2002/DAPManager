@echo off
echo DAP Manager Windows Bootstrap
echo =============================

REM Check python availability
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

if not exist config.json (
    echo [INFO] config.json not found. Creating from Windows template...
    if exist config-win.json (
        copy config-win.json config.json
        echo [OK] Created config.json
    ) else (
        echo [ERROR] config-win.json not found!
        echo Please ensure you have config-win.json in this directory.
        pause
        exit /b 1
    )
) else (
    echo [INFO] config.json already exists. Using existing configuration.
)

REM Check for sldl.exe
if not exist sldl.exe (
    echo [WARNING] sldl.exe not found in root directory!
    echo Please download slsk-batchdl executable and rename it to sldl.exe
    echo or update config.json to point to the correct path.
)

echo.
echo [INFO] Starting Web Server...
python web_server.py
pause
