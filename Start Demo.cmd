@echo off
cd /d "%~dp0"
if exist "%~dp0portable\AprilTagStudio.exe" (
    start "" "%~dp0portable\AprilTagStudio.exe" --demo
    exit /b 0
)
echo Portable application missing. See README.md.
pause
