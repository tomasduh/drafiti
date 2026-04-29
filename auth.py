import os
import uuid
import secrets
import logging
import httpx
import jwt
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from fastapi import Request

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
BASE_URL             = os.environ.get("BASE_URL", "http://localhost:8080")

_raw_secret = os.environ.get("SECRET_KEY", "")
if not _raw_secret:
    if BASE_URL.startswith("https://"):
        raise RuntimeError("SECRET_KEY debe estar configurado en producción")
    _raw_secret = "dev-insecure-do-not-use-in-production-replace-me!!"
    logger.warning("SECRET_KEY no configurado — usando clave de desarrollo insegura")
if len(_raw_secret) < 32:
    raise RuntimeError("SECRET_KEY debe tener al menos 32 caracteres")
SECRET_KEY = _raw_secret

ALGORITHM                = "HS256"
TOKEN_EXPIRE_HOURS_SHORT = 24         # sesión sin "recordarme"
TOKEN_EXPIRE_DAYS_LONG   = 30         # sesión con "recordarme"
COOKIE_NAME              = "drafiti_session"
STATE_COOKIE_NAME        = "drafiti_oauth_state"

GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _redirect_uri() -> str:
    return f"{BASE_URL}/auth/callback"


def google_auth_url_build(remember: bool = False) -> tuple[str, str]:
    """Construye la URL de OAuth. Retorna (url, state_cookie_value).
    Guarda state_cookie_value en una cookie HttpOnly antes de redirigir."""
    nonce = secrets.token_urlsafe(32)
    state_value = f"{nonce}:{'1' if remember else '0'}"
    params = urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  _redirect_uri(),
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "offline",
        "prompt":        "select_account",
        "state":         nonce,
    })
    return f"{GOOGLE_AUTH_URL}?{params}", state_value


def validate_state(google_state: str, state_cookie: str) -> tuple[bool, bool]:
    """Valida el parámetro state. Retorna (válido, remember_me)."""
    if not state_cookie or not google_state:
        return False, False
    parts = state_cookie.split(":", 1)
    if len(parts) != 2:
        return False, False
    nonce, remember_flag = parts
    if not secrets.compare_digest(nonce, google_state):
        return False, False
    return True, remember_flag == "1"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(GOOGLE_TOKEN_URL, data={
            "code":          code,
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  _redirect_uri(),
            "grant_type":    "authorization_code",
        })
        r.raise_for_status()
        return r.json()


async def get_userinfo(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        r.raise_for_status()
        return r.json()


def create_session_token(user_id: int, remember_me: bool = False) -> tuple[str, str]:
    """Retorna (token_jwt, jti)."""
    jti = str(uuid.uuid4())
    if remember_me:
        expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS_LONG)
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS_SHORT)
    token = jwt.encode(
        {"sub": str(user_id), "exp": expire, "jti": jti},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return token, jti


def decode_session_token(token: str) -> tuple[int | None, str | None]:
    """Retorna (user_id, jti) o (None, None) si el token es inválido."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"]), payload.get("jti")
    except Exception:
        return None, None


def get_token_expiry(token: str) -> datetime | None:
    """Extrae la fecha de expiración del JWT."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    except Exception:
        return None


def get_token_from_request(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)


def is_secure() -> bool:
    return BASE_URL.startswith("https://")


def token_max_age(remember_me: bool) -> int:
    if remember_me:
        return TOKEN_EXPIRE_DAYS_LONG * 86400
    return TOKEN_EXPIRE_HOURS_SHORT * 3600
