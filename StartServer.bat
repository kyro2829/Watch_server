@echo off
REM StartServer.bat — place this in the same folder as server.py

REM Change to script directory (works no matter where the shortcut points)
cd /d "%~dp0"

REM If virtualenv exists in project folder named "venv", activate it automatically
if exist "%~dp0venv\Scripts\activate.bat" (
    call "%~dp0venv\Scripts\activate.bat"
)

REM Prefer 'python' if available, else try 'py'
where python >nul 2>nul
if %errorlevel%==0 (
    set PYEXEC=python
) else (
    set PYEXEC=py
)

echo ===========================================
echo   Starting T-Watch server with %PYEXEC%
echo   Working directory: %cd%
echo ===========================================
echo.

REM Start server (use waitress if included in server.py)
%PYEXEC% server.py

echo.
echo Server stopped. Press any key to close...
pause >nul
