@echo off
setlocal EnableExtensions
rem ------------------------------------------------------------------
rem  FB-MultiPier directional load mirroring - launcher
rem  Mirrors wind (WS*, WL*), braking (BR*), water (WA*) and temperature (TU*) loads;
rem  all other loads are kept as-is.
rem  Usage:  double-click, or drag the base .in file onto this .bat,
rem          or:  "Mirror Loads.bat" "model.in" "model.out"
rem  The three mirrored files (X, Y, XY) and their reports are written
rem  next to the base .in file.
rem ------------------------------------------------------------------
set "SCRIPT=%~dp0FBMP-MirrorLoads.ps1"
if not exist "%SCRIPT%" echo Cannot find FBMP-MirrorLoads.ps1 next to this launcher. & pause & exit /b 1

set "INFILE=%~1"
if "%INFILE%"=="" set /p "INFILE=Base FB-MultiPier .in file (drag it here, then Enter): "
set "INFILE=%INFILE:"=%"
if not exist "%INFILE%" echo File not found: "%INFILE%" & pause & exit /b 1

set "OUTFILE=%~2"
if "%OUTFILE%"=="" if exist "%INFILE:~0,-3%.out" set "OUTFILE=%INFILE:~0,-3%.out"
if "%OUTFILE%"=="" set /p "OUTFILE=.out file from a run of that same model (drag it here, then Enter): "
set "OUTFILE=%OUTFILE:"=%"
if not exist "%OUTFILE%" echo File not found: "%OUTFILE%" & pause & exit /b 1

echo.
echo Optional: p-multiplier lists calibrated for the mirrored directions.
echo   Give an existing .in file (for example the mirrored-foundation model
echo   for that direction) or press Enter to keep the base model's lists.
set "PMX="
set "PMY="
set /p "PMX=  .in file supplying the X list (used for Mirror X and XY): "
set /p "PMY=  .in file supplying the Y list (used for Mirror Y and XY): "

set "ARGS="
if not "%PMX%"=="" set "PMX=%PMX:"=%"
if not "%PMY%"=="" set "PMY=%PMY:"=%"
if not "%PMX%"=="" set ARGS=%ARGS% -PMultXFile "%PMX%"
if not "%PMY%"=="" set ARGS=%ARGS% -PMultYFile "%PMY%"

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -InFile "%INFILE%" -OutFile "%OUTFILE%" %ARGS%
echo.
pause
