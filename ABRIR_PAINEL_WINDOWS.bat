@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo CSA Prospect Ultra - painel local
%PY% painel.py --porta 8785
if errorlevel 1 echo ERRO ao iniciar. Confira Python 3.11+ e porta 8785.
pause
