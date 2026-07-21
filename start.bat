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

echo Starting API at http://localhost:8000 ...
start "Robot Agent API" cmd /k ""%PROJECT_PYTHON%" -m uvicorn app.main:app --reload"

echo Starting dashboard at http://localhost:8501 ...
start "Robot Agent Dashboard" cmd /k ""%PROJECT_PYTHON%" -m streamlit run dashboard.py"

echo.
echo Startup commands sent successfully.
echo Dashboard: http://localhost:8501
echo API docs: http://localhost:8000/docs
echo Close both service windows to stop the project.

endlocal
