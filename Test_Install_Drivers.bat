@echo off
:: Request Admin Privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :RunAdmin
) else (
    echo Requesting Administrator Privileges...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:RunAdmin
echo Saving Audio State...
powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0bundled_drivers\RestoreAudio.ps1" -Action Save

echo Installing VB-Audio Virtual Cable...
"%~dp0bundled_drivers\vbcable\VBCABLE_Setup_x64.exe" -i -h

echo Restoring Audio State...
powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0bundled_drivers\RestoreAudio.ps1" -Action Restore

echo Registering OBS Virtual Camera...
regsvr32.exe /s "%~dp0bundled_drivers\obs_vcam\bin\64bit\obs-virtualcam-module64.dll"

echo =========================================
echo Installation Complete!
echo Press any key to close this window.
echo =========================================
pause >nul
