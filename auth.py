import os
import httpx
import jwt
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from fastapi import Request

GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SECRET_KEY           = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
BASE_URL             = os.environ.get("BASE_URL", "http://localhost:8080")

ALGORITHM           = "HS256"
TOKEN_EXPIRE_DAYS   = 30
COOKIE_NAME         = "drafiti_session"

GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _redirect_uri() -> str:
    return f"{BASE_URL}/auth/callback"


def google_auth_url_build() -> str:
    params = urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  _redirect_uri(),
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "offline",
        "prompt":        "select_account",
    })
    return f"{GOOGLE_AUTH_URL}?{params}"


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


def create_session_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_session_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except Exception:
        return None


def get_token_from_request(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)


def is_secure() -> bool:
    return BASE_URL.startswith("https://")
