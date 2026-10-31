@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo ==============================================
echo VALIDACAO DO EMAIL - CSA PROSPECCAO
 echo ==============================================
echo 1 - Verificar conexao SMTP e IMAP (sem envio)
echo 2 - Enviar UM email de teste para sua propria conta
echo 3 - Confirmar recebimento da mensagem pela INBOX
echo 0 - Sair
choice /C 1230 /M "Opcao"
if errorlevel 4 exit /b 0
if errorlevel 3 goto conferir
if errorlevel 2 goto enviar
if errorlevel 1 goto conexao
:conexao
%PY% validar_email.py conexao
goto fim
:enviar
%PY% validar_email.py enviar-teste
goto fim
:conferir
%PY% validar_email.py conferir
goto fim
:fim
if errorlevel 1 echo Validacao incompleta. Confira a mensagem acima.
pause
