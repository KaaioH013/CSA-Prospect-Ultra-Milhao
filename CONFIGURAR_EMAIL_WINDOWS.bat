@echo off
setlocal
cd /d "%~dp0"
if not exist "email_config.ini" copy /Y "email_config.exemplo.ini" "email_config.ini" >nul
echo Edite o email do remetente e as configuracoes do provedor.
echo Nunca grave senha de conta nem senha de aplicativo neste arquivo.
notepad "email_config.ini"
