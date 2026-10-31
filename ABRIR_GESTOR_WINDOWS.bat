@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist "dados\empresas.sqlite3" (
 echo Banco de dados nao encontrado. Execute INICIAR_WINDOWS.bat ou importe um CSV primeiro.
 pause
 exit /b 1
)
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo ===================================================
echo CSA PROSPECCAO - GESTOR COMERCIAL LOCAL - ETAPA 6
echo ===================================================
echo O navegador deve abrir em http://127.0.0.1:8785
echo Para encerrar, pressione Ctrl+C nesta janela.
echo O PAINEL NAO ENVIA EMAIL. LOTES EXIGEM CONFIRMACAO EXPLICITA.
echo.
%PY% gestor.py iniciar --porta 8785
if errorlevel 1 goto erro
pause
exit /b 0
:erro
echo Falha ao iniciar. Confirme o Python e a disponibilidade da porta 8785.
pause
exit /b 1
