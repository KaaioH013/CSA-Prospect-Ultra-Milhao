@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
%PY% configurar_ia.py
if errorlevel 1 (
 echo Houve uma falha. O banco e os dados anteriores continuam preservados.
 pause
 exit /b 1
)
echo.
echo Diagnosticos salvos em saida\diagnosticos.csv para revisao humana.
pause
