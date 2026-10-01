@echo off
rem Video side of the receiver. Start fpv_rx.grc first, then this script.
setlocal
cd /d "%~dp0..\src"
set PY=%USERPROFILE%\radioconda\python.exe
if not exist "%PY%" set PY=python
"%PY%" video_rx.py %*
if errorlevel 1 pause
