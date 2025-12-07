@echo off
title T-Watch Health Monitor
color 0B

echo ===========================================
echo       T-Watch Health Monitor Launcher
echo ===========================================
echo.

REM --- CHECK PYTHON ---
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo [ERROR] Python not found!
    echo Please install Python 3.8 or higher:
    echo https://www.python.org/downloads/
    pause
    exit /b
)

echo [ OK ] Python detected.
echo.

REM --- INSTALL DEPENDENCIES ---
echo Checking dependencies...
python -m pip install --upgrade pip >nul

IF EXIST requirements.txt (
    pip install -r requirements.txt >nul 2>&1
    echo [ OK ] requirements.txt installed.
) ELSE (
    echo [WARNING] requirements.txt not found.
)

echo Installing reportlab...
pip install reportlab >nul 2>&1

IF %ERRORLEVEL%==0 (
    echo [ OK ] reportlab installed.
) ELSE (
    echo [ERROR] Failed to install reportlab.
)

echo Installing Werkzeug...
pip install Werkzeug >nul 2>&1

IF %ERRORLEVEL%==0 (
    echo [ OK ] Werkzeug installed.
) ELSE (
    echo [ERROR] Failed to install Werkzeug.
)

echo Installing Waitress...
pip install waitress >nul 2>&1

IF %ERRORLEVEL%==0 (
    echo [ OK ] Waitress installed.
) ELSE (
    echo [ERROR] Failed to install Waitress.
)

echo.

REM ======================================================
REM = CREATE TWO SHORTCUTS USING BUILT-IN TEMP VBS SCRIPT =
REM ======================================================

echo Creating Desktop shortcuts...

REM --- Create Dashboard Shortcut ---
echo Set oWS = WScript.CreateObject("WScript.Shell") > temp_shortcut.vbs
echo strDesktop = oWS.SpecialFolders("Desktop") >> temp_shortcut.vbs
echo Set oLink = oWS.CreateShortcut(strDesktop ^& "\T-Watch Dashboard.lnk") >> temp_shortcut.vbs
echo oLink.TargetPath = "http://127.0.0.1:5000/login" >> temp_shortcut.vbs
echo oLink.IconLocation = "%~dp0static\icon.ico" >> temp_shortcut.vbs
echo oLink.Description = "Open T-Watch Dashboard" >> temp_shortcut.vbs
echo oLink.Save >> temp_shortcut.vbs

cscript //nologo temp_shortcut.vbs

REM --- Create Server Shortcut ---
echo Set oWS = WScript.CreateObject("WScript.Shell") > temp_shortcut2.vbs
echo strDesktop = oWS.SpecialFolders("Desktop") >> temp_shortcut2.vbs
echo Set oLink = oWS.CreateShortcut(strDesktop ^& "\Start T-Watch Server.lnk") >> temp_shortcut2.vbs
echo oLink.TargetPath = "%~dp0StartServer.bat" >> temp_shortcut2.vbs
echo oLink.WorkingDirectory = "%~dp0" >> temp_shortcut2.vbs
echo oLink.IconLocation = "%~dp0static\icon.ico" >> temp_shortcut2.vbs
echo oLink.Description = "Start T-Watch Flask Server" >> temp_shortcut2.vbs
echo oLink.Save >> temp_shortcut2.vbs

cscript //nologo temp_shortcut2.vbs
del temp_shortcut.vbs
del temp_shortcut2.vbs

echo [ OK ] Shortcuts created.
echo.

echo Starting local server on port 5000...
echo Starting Python server...

python server.py

pause