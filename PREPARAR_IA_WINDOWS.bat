@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
echo CSA PROSPECCAO ETAPA 3 - PREPARAR IA LOCAL
where ollama >nul 2>nul
if errorlevel 1 (
 echo Ollama nao encontrado. Instale pelo site oficial: https://ollama.com/download/windows
 echo Feche e abra esta janela depois da instalacao.
 pause
 exit /b 1
)
set /p ACEITA=Baixar o modelo qwen3:1.7b (aprox. 1,4 GB) agora? [S/N]: 
if /I not "%ACEITA%"=="S" (
 echo Download cancelado. Nenhuma alteracao realizada.
 pause
 exit /b 0
)
ollama pull qwen3:1.7b
if errorlevel 1 (
 echo Falha no download. Confira a conexao e execute novamente.
 pause
 exit /b 1
)
echo Modelo instalado. Agora execute DIAGNOSTICAR_IA_WINDOWS.bat.
pause
