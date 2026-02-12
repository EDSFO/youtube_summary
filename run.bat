@echo off
REM ============================================
REM Script para executar o Sistema de Resumos
REM ============================================

REM Verificar se .env existe
if not exist .env (
    echo ❌ Arquivo .env não encontrado!
    echo.
    echo Copie .env.example para .env e configure suas API Keys:
    echo   copy .env.example .env
    echo.
    pause
    exit /b 1
)

echo 🚀 Executando Sistema de Resumos YouTube...
echo.

REM Ativar ambiente virtual se existir
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Instalar dependências se necessário
pip install -q -r requirements.txt

REM Executar o script principal
python main.py

echo.
pause
