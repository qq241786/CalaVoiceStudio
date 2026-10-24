@echo off
rem CalaVoiceStudio launcher: prefers the packaged exe, otherwise a Python with PySide6.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
endlocal
