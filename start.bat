@echo off
echo ================================================================
echo  SOLO PARA DESARROLLO LOCAL — NUNCA USAR EN PRODUCCION
echo ================================================================
echo.

REM Verificar que no estamos en prod por accidente
if defined PRODUCTION (
  echo ERROR: Variable PRODUCTION detectada. Este script es solo para dev.
  exit /b 1
)

echo Instalando dependencias...
pip install -r requirements.txt -q
echo.

REM ── Variables de entorno para desarrollo local ────────────────────────────
REM Crea un proyecto en https://console.cloud.google.com → APIs → OAuth 2.0
REM y agrega http://localhost:8080/auth/callback como URI autorizado.

set BASE_URL=http://localhost:8080
set GOOGLE_CLIENT_ID=TU_CLIENT_ID_AQUI
set GOOGLE_CLIENT_SECRET=TU_CLIENT_SECRET_AQUI

REM SECRET_KEY: mínimo 32 caracteres. Genera uno con: python -c "import secrets; print(secrets.token_hex(32))"
set SECRET_KEY=cambia-esto-por-una-clave-larga-y-aleatoria-de-32-chars

REM ADMIN_EMAIL / ADMIN_USERNAME: correo y nombre del usuario administrador inicial
set ADMIN_EMAIL=tu_email@gmail.com
set ADMIN_USERNAME=tu_username

REM DEV_ONLY: permite el endpoint /auth/dev-login solo en desarrollo
set DEV_ONLY=true

echo Iniciando Drafiti en http://localhost:8080
echo Presiona Ctrl+C para detener.
echo.
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
