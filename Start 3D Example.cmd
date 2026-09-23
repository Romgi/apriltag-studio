@echo off
cd /d "%~dp0"
if exist "%~dp0portable\AprilTagStudio.exe" (
    start "" "%~dp0portable\AprilTagStudio.exe" --pose-example
    exit /b 0
)
echo Portable application missing. Keep the portable folder beside this launcher.
pause
