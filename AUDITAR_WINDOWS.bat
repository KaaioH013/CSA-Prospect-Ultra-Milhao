@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist "dados\empresas.sqlite3" (
  echo Banco de sites nao encontrado. Execute INICIAR_WINDOWS.bat primeiro.
  pause
  exit /b 1
)
where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
)
echo CSA PROSPECCAO - ETAPA 2 - AUDITORIAS
%PY% auditor.py auditar --limite 100 --workers 8
if errorlevel 1 goto erro
%PY% auditor.py exportar
if errorlevel 1 goto erro
%PY% auditor.py resumo
if errorlevel 1 goto erro
echo.
echo CONCLUIDO: arquivo saida\auditorias.csv
pause
exit /b 0
:erro
echo Erro na auditoria. O trabalho concluido foi salvo no SQLite.
pause
exit /b 1
