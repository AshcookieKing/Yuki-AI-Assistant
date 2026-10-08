@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist desktop\node_modules\electron\dist\electron.exe (
  echo Устанавливаю оболочку фигурки...
  pushd desktop & call npm install --no-audit --no-fund & popd
)
start "" desktop\node_modules\electron\dist\electron.exe desktop
