@echo off
setlocal
title Kalman Traffic Test

:menu
echo(
echo Kalman Traffic Test
echo http://127.0.0.1:8000/
echo(
echo   1  Start
echo   2  Refresh
echo   3  Stop
echo   4  Stop and exit
echo(
choice /c 1234 /n /m "Choose 1-4: "
set "selected=%errorlevel%"
set "action="
if "%selected%"=="1" set "action=start"
if "%selected%"=="2" set "action=refresh"
if "%selected%"=="3" set "action=stop"
if "%selected%"=="4" set "action=stop"
if not defined action exit /b 1
echo(
powershell.exe -NoLogo -ExecutionPolicy RemoteSigned -File "%~dp0preview.ps1" %action%
if "%selected%"=="4" exit /b %errorlevel%
goto menu
