@echo off
echo Instalando dependencias...
pip install -r requirements.txt -q
echo.

REM ── Variables de entorno para desarrollo local ────────────────────────────
REM Crea un proyecto en https://console.cloud.google.com → APIs → OAuth 2.0
REM y agrega http://localhost:8080/auth/callback como URI autorizado.
set BASE_URL=http://localhost:8080
set GOOGLE_CLIENT_ID=TU_CLIENT_ID_AQUI
set GOOGLE_CLIENT_SECRET=TU_CLIENT_SECRET_AQUI
set SECRET_KEY=una-clave-secreta-local-cualquiera

echo Iniciando Drafiti en http://localhost:8080
echo Presiona Ctrl+C para detener.
echo.
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
