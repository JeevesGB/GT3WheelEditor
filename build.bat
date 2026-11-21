@echo off
setlocal
cd /d "%~dp0"

set APP_NAME=GT3WheelPainter

echo === Installing dependencies ===
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo === Cleaning previous build ===
if exist _pyi_work rmdir /s /q _pyi_work
if exist BUILD rmdir /s /q BUILD
if exist %APP_NAME%.spec del /q %APP_NAME%.spec

echo === Building ===
python -m PyInstaller --noconfirm --clean --windowed ^
  --distpath BUILD ^
  --workpath _pyi_work ^
  --name %APP_NAME% ^
  --icon "assets\icon.ico" ^
  --add-data "assets;assets" ^
  --add-data "ui\win98.qss;ui" ^
  --add-data "ui\icons;ui\icons" ^
  --collect-submodules OpenGL ^
  --hidden-import OpenGL.platform.win32 ^
  main.py
if errorlevel 1 goto :fail

rem Remove intermediate files, keep only BUILD
if exist _pyi_work rmdir /s /q _pyi_work
if exist %APP_NAME%.spec del /q %APP_NAME%.spec

echo.
echo Build complete: BUILD\%APP_NAME%\%APP_NAME%.exe
pause
exit /b 0

:fail
echo.
echo BUILD FAILED
pause
exit /b 1