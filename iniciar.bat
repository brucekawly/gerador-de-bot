@echo off
REM Gerador de BOT - Windows: instala na primeira vez e abre o app.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo == Primeira execucao: criando ambiente Python ==
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Python 3.10+ nao encontrado. Instale em https://www.python.org/downloads/
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Falha ao instalar dependencias. Veja a mensagem acima.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m playwright install chromium firefox
)

start "" ".venv\Scripts\pythonw.exe" main.py
