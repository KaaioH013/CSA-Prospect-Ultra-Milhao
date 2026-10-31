@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist "dados\empresas.sqlite3" (
 echo Banco nao encontrado. Rode INICIAR_WINDOWS.bat primeiro ou importe um CSV.
 pause
 exit /b 1
)
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo CSA PROSPECCAO - ETAPA 4 - CONTATOS PUBLICOS
%PY% contatos.py coletar --limite 100 --workers 4 --pausa 1.0
if errorlevel 1 goto erro
%PY% contatos.py exportar
if errorlevel 1 goto erro
%PY% contatos.py resumo
if errorlevel 1 goto erro
echo Concluido: saida\contatos_para_revisao.csv
pause
exit /b 0
:erro
echo Houve erro. Registros ja processados foram mantidos.
pause
exit /b 1
