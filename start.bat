@echo off
echo Instalando dependencias...
pip install -r requirements.txt -q
echo.
echo Iniciando Drafiti en http://localhost:8000
echo Presiona Ctrl+C para detener.
echo.
uvicorn main:app --reload
