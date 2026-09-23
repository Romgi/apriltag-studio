@echo off
cd /d "%~dp0"
if exist "%~dp0portable\AprilTagStudio.exe" (
    start "" "%~dp0portable\AprilTagStudio.exe"
    exit /b 0
)
echo Portable application missing. Keep the portable folder beside this launcher.
echo See README.md to run from source or build the application.
pause
