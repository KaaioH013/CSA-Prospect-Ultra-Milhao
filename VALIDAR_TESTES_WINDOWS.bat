@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo TESTES AUTOMATIZADOS OFFLINE DA CSA PROSPECCAO
echo NENHUM EMAIL SERA ENVIADO
%PY% -m unittest discover -s tests -v
if errorlevel 1 (
  echo FALHA. Verifique o erro acima.
  pause
  exit /b 1
)
echo TODOS OS TESTES LOCAIS PASSARAM
pause
