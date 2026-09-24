@echo off
rem Sara Wallet installer - double-click to install or update.
rem This file only starts Install-Sara.ps1 (in the same folder); open that
rem file in Notepad to read exactly what the installer does.
title Sara installer
if not exist "%~dp0Install-Sara.ps1" (
  echo Install-Sara.ps1 was not found next to this file.
  echo Right-click the downloaded zip, choose "Extract All...", and run this
  echo file again from the extracted folder.
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Sara.ps1" %*
set "SARA_RC=%ERRORLEVEL%"
echo.
if not "%SARA_RC%"=="0" echo The installer stopped with an error. Nothing further was changed.
pause
exit /b %SARA_RC%
