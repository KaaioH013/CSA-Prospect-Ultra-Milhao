@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3 nao encontrado. Instale Python 3.11 ou superior e reinicie.
    echo https://www.python.org/downloads/windows/
    pause
    exit /b 1
  )
  set "PY=python"
)
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente Python isolado...
  %PY% -m venv .venv
  if errorlevel 1 goto erro
)
echo Instalando/verificando dependencia...
.venv\Scripts\python.exe -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 goto erro
echo.
echo ATENCAO: o download inicial da base Sudeste tem aproximadamente 823 MB.
echo Os dados publicos serao salvos no seu PC. Nenhum servico pago sera usado.
echo.
choice /C SN /M "Deseja iniciar o download e a extracao agora?"
if errorlevel 2 exit /b 0
.venv\Scripts\python.exe pipeline.py baixar --regiao sudeste
if errorlevel 1 goto erro
.venv\Scripts\python.exe pipeline.py extrair --arquivo dados\sudeste-latest.osm.pbf
if errorlevel 1 goto erro
.venv\Scripts\python.exe pipeline.py exportar
if errorlevel 1 goto erro
.venv\Scripts\python.exe pipeline.py resumo
if errorlevel 1 goto erro
echo.
echo CONCLUIDO: abra a pasta saida e o arquivo sites_unicos.csv.
pause
exit /b 0
:erro
echo.
echo O processo parou por erro. Veja a mensagem acima.
echo Dados ja gravados permanecem salvos para retomada.
pause
exit /b 1
