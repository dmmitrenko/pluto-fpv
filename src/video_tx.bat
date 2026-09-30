@echo off
rem Video side of the transmitter. Start fpv_tx.grc first, then this script.
rem Arguments are passed on, for example: video_tx.bat --list
setlocal
cd /d "%~dp0"
set PY=%USERPROFILE%\radioconda\python.exe
if not exist "%PY%" set PY=python
"%PY%" video_tx.py %*
if errorlevel 1 pause
