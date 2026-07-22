@echo off
setlocal

cd /d "%~dp0"
set "PROJECT_PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%PROJECT_PYTHON%" (
    echo [ERROR] Virtual environment not found: .venv
    echo Run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -e ".[test,research]"
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] Configuration file .env was not found.
    echo Copy .env.example to .env and configure the required API keys.
    pause
    exit /b 1
)

echo Starting Web application at http://localhost:8000 ...
start "Robot Intelligence Web" cmd /k ""%PROJECT_PYTHON%" -m uvicorn app.main:app --reload"
timeout /t 2 /nobreak >nul
start "" http://localhost:8000

echo.
echo Startup command sent successfully.
echo Web: http://localhost:8000
echo API docs: http://localhost:8000/docs
echo Close the service window to stop the project.

endlocal
