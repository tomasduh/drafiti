from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, text
import pdfplumber
import re
import io
import os
import json
import hashlib
import asyncio
import uuid as _uuid
import logging
from datetime import datetime, timezone
from typing import Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from db import init_db, get_db, SessionLocal, User, HistoryEntry, LearnedRule, CustomCategory, RevokedToken
import auth

logger = logging.getLogger(__name__)

_RAW_ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL", "")
_RAW_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
if auth.is_secure() and (not _RAW_ADMIN_EMAIL or not _RAW_ADMIN_USERNAME):
    raise RuntimeError("ADMIN_EMAIL y ADMIN_USERNAME deben estar configurados en producción")
ADMIN_EMAIL    = _RAW_ADMIN_EMAIL    or "tomasduh421@gmail.com"
ADMIN_USERNAME = _RAW_ADMIN_USERNAME or "tomasduh"

# ── Sanitización ──────────────────────────────────────────────────────────────
_HEX_RE  = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME_RE = re.compile(r"^[\w\sáéíóúñÁÉÍÓÚÑ&\-]{1,40}$")

# ── Normalización de merchant key ──────────────────────────────────────────────
_MK_TLD     = re.compile(r'\.(COM|NET|ORG|CO|IO|APP)\b', re.I)
_MK_DIGITS  = re.compile(r'\b\d{3,}\b')
_MK_SPECIAL = re.compile(r'[*#@]')
_MK_NOISE   = {
    "CO", "COL", "BOGOTA", "BOGOTÁ", "MEDELLIN", "MEDELLÍN",
    "CALI", "BARRANQUILLA", "CARTAGENA", "SAS", "LTDA", "SA",
    "DE", "EL", "LA", "LOS", "LAS",
}

def _merchant_key(desc: str) -> str:
    s = _MK_TLD.sub("", desc.upper().strip())
    s = _MK_SPECIAL.sub(" ", s)
    s = _MK_DIGITS.sub("", s)
    s = s.replace(".", " ")
    tokens = [t for t in s.split() if len(t) > 1 and t not in _MK_NOISE]
    return " ".join(tokens)

def sanitize_text(s: str, max_len: int = 255) -> str:
    return re.sub(r"[<>\"']", "", str(s))[:max_len]

# ── Rate limiting ─────────────────────────────────────────────────────────────
def _extract_key(request: Request) -> str:
    token = auth.get_token_from_request(request)
    if token:
        user_id, _ = auth.decode_session_token(token)
        if user_id:
            return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"

limiter = Limiter(key_func=get_remote_address)

# ── Startup ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _migrate_db()
    await _ensure_admin()
    await _cleanup_revoked_tokens()
    yield


async def _migrate_db():
    from db import engine as _engine
    is_pg = "postgresql" in str(_engine.url)
    async with _engine.begin() as conn:
        if is_pg:
            # PostgreSQL: IF NOT EXISTS is safe and idempotent
            await conn.execute(text(
                "ALTER TABLE history_entries ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64)"
            ))
        else:
            # SQLite: check if column exists first to avoid error
            rows = await conn.execute(text("PRAGMA table_info(history_entries)"))
            cols = {row[1] for row in rows}
            if "file_hash" not in cols:
                await conn.execute(text(
                    "ALTER TABLE history_entries ADD COLUMN file_hash VARCHAR(64)"
                ))
    logger.info("_migrate_db: OK")


async def _ensure_admin():
    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
        if not result.scalar_one_or_none():
            db.add(User(username=ADMIN_USERNAME, email=ADMIN_EMAIL, is_admin=True))
            await db.commit()


async def _cleanup_revoked_tokens():
    async with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        await db.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
        await db.commit()


app = FastAPI(title="Drafiti", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Security headers ──────────────────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"]   = "nosniff"
    response.headers["X-Frame-Options"]          = "DENY"
    response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]       = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"]  = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://lh3.googleusercontent.com; "
        "connect-src 'self' https://accounts.google.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self' https://accounts.google.com"
    )
    if auth.is_secure():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── CSRF check ────────────────────────────────────────────────────────────────
@app.middleware("http")
async def csrf_check(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get("x-drafiti-csrf") != "1":
            return JSONResponse({"detail": "Solicitud no válida"}, status_code=403)
    return await call_next(request)

# ── Auth dependency ───────────────────────────────────────────────────────────

async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    token = auth.get_token_from_request(request)
    if not token:
        raise HTTPException(401, "No autenticado")
    user_id, jti = auth.decode_session_token(token)
    if not user_id:
        raise HTTPException(401, "Sesión inválida")
    if jti:
        rev = await db.execute(select(RevokedToken).where(RevokedToken.jti == jti))
        if rev.scalar_one_or_none():
            raise HTTPException(401, "Sesión cerrada")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "Usuario no encontrado")
    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Solo administradores")
    return user

# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")


@app.get("/auth/login")
async def auth_login(remember: str = "0"):
    url, state_value = auth.google_auth_url_build(remember=(remember == "1"))
    response = RedirectResponse(url)
    response.set_cookie(
        auth.STATE_COOKIE_NAME, state_value,
        httponly=True,
        secure=auth.is_secure(),
        samesite="lax",
        max_age=600,
    )
    return response


@app.get("/auth/callback")
@limiter.limit("20/hour")
async def auth_callback(
    request: Request,
    code: str = "",
    error: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    state_cookie = request.cookies.get(auth.STATE_COOKIE_NAME, "")
    valid, remember_me = auth.validate_state(state, state_cookie)

    if not valid or error or not code:
        response = RedirectResponse("/login?error=oauth")
        response.delete_cookie(auth.STATE_COOKIE_NAME)
        return response

    try:
        tokens   = await auth.exchange_code(code)
        userinfo = await auth.get_userinfo(tokens["access_token"])
    except Exception:
        response = RedirectResponse("/login?error=oauth")
        response.delete_cookie(auth.STATE_COOKIE_NAME)
        return response

    email      = userinfo.get("email", "")
    google_sub = userinfo.get("sub", "")

    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user   = result.scalar_one_or_none()

    if not user:
        result = await db.execute(select(User).where(User.email == email))
        user   = result.scalar_one_or_none()
        if user:
            user.google_sub = google_sub
            await db.commit()

    if not user:
        response = RedirectResponse("/login?error=not_registered")
        response.delete_cookie(auth.STATE_COOKIE_NAME)
        return response

    token, _jti  = auth.create_session_token(user.id, remember_me)
    response = RedirectResponse("/")
    response.set_cookie(
        auth.COOKIE_NAME, token,
        httponly=True,
        secure=auth.is_secure(),
        samesite="strict",
        max_age=auth.token_max_age(remember_me),
    )
    response.delete_cookie(auth.STATE_COOKIE_NAME)
    return response


@app.post("/auth/logout")
async def auth_logout(request: Request, db: AsyncSession = Depends(get_db)):
    token = auth.get_token_from_request(request)
    if token:
        _uid, jti = auth.decode_session_token(token)
        if jti:
            expires_at = auth.get_token_expiry(token)
            if expires_at:
                db.add(RevokedToken(jti=jti, expires_at=expires_at))
                await db.commit()
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME, samesite="strict")
    return response


_DEV_ONLY = os.environ.get("DEV_ONLY", "").lower() == "true"

@app.get("/auth/dev-login")
async def auth_dev_login(db: AsyncSession = Depends(get_db)):
    if not _DEV_ONLY or auth.is_secure():
        raise HTTPException(404, "Not found")
    result = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(500, "Admin user not found")
    token, _jti = auth.create_session_token(user.id)
    response = RedirectResponse("/")
    response.set_cookie(
        auth.COOKIE_NAME, token,
        httponly=True,
        secure=False,
        samesite="strict",
        max_age=auth.TOKEN_EXPIRE_HOURS_SHORT * 3600,
    )
    return response

# ── User info ─────────────────────────────────────────────────────────────────

@app.get("/api/me")
async def api_me(user: User = Depends(get_current_user)):
    return {
        "id":       user.id,
        "username": user.username,
        "email":    user.email,
        "is_admin": user.is_admin,
    }

# ── History ───────────────────────────────────────────────────────────────────

@app.get("/api/history")
async def api_get_history(
    user: User = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
):
    result  = await db.execute(
        select(HistoryEntry)
        .where(HistoryEntry.user_id == user.id)
        .order_by(HistoryEntry.sort_key.desc())
    )
    entries = result.scalars().all()
    return [
        {
            "id":           e.id,
            "filename":     e.filename,
            "fecha_corte":  e.fecha_corte,
            "uploadedAt":   e.uploaded_at,
            "total":        e.total,
            "summary":      e.summary_json,
            "transactions": e.transactions_json,
        }
        for e in entries
    ]


@app.post("/api/history")
async def api_save_history(
    request: Request,
    user:    User         = Depends(get_current_user),
    db:      AsyncSession = Depends(get_db),
):
    body      = await request.json()
    file_hash = body.get("file_hash") or None
    new_txns  = body.get("transactions", [])

    entry_id = str(_uuid.uuid4())
    sort_key = int(datetime.now(timezone.utc).timestamp() * 1000)
    entry    = HistoryEntry(
        id                = entry_id,
        user_id           = user.id,
        filename          = sanitize_text(body.get("filename", ""), 255),
        fecha_corte       = body.get("fecha_corte"),
        uploaded_at       = datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        total             = body.get("total", 0),
        summary_json      = body.get("summary", {}),
        transactions_json = new_txns,
        sort_key          = sort_key,
    )
    db.add(entry)

    # Keep max 20 entries per user
    result      = await db.execute(
        select(HistoryEntry)
        .where(HistoryEntry.user_id == user.id)
        .order_by(HistoryEntry.sort_key.desc())
    )
    all_entries = result.scalars().all()
    if len(all_entries) >= 20:
        for old in all_entries[19:]:
            await db.delete(old)

    await db.commit()
    return {"ok": True, "id": entry_id}


@app.put("/api/history/{entry_id}")
async def api_update_history(
    entry_id: str,
    request:  Request,
    user:     User         = Depends(get_current_user),
    db:       AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HistoryEntry)
        .where(HistoryEntry.id == entry_id, HistoryEntry.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Extracto no encontrado")
    body = await request.json()
    if "transactions" in body:
        entry.transactions_json = body["transactions"]
    if "total" in body:
        entry.total = body["total"]
    await db.commit()
    return {"ok": True}


@app.delete("/api/history/{entry_id}")
async def api_delete_history(
    entry_id: str,
    user:     User         = Depends(get_current_user),
    db:       AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(HistoryEntry)
        .where(HistoryEntry.id == entry_id, HistoryEntry.user_id == user.id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Extracto no encontrado")
    await db.delete(entry)
    await db.commit()
    return {"ok": True}

# ── Learned rules ─────────────────────────────────────────────────────────────

@app.get("/api/rules")
async def api_get_rules(
    user: User         = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
):
    result = await db.execute(select(LearnedRule).where(LearnedRule.user_id == user.id))
    rules  = result.scalars().all()
    return {r.description_key: r.categories_json for r in rules}


@app.put("/api/rules")
async def api_save_rule(
    request: Request,
    user:    User         = Depends(get_current_user),
    db:      AsyncSession = Depends(get_db),
):
    body = await request.json()
    key  = _merchant_key(body["key"])
    cats = body["categories"]

    result = await db.execute(
        select(LearnedRule)
        .where(LearnedRule.user_id == user.id, LearnedRule.description_key == key)
    )
    rule = result.scalar_one_or_none()
    if rule:
        rule.categories_json = cats
    else:
        db.add(LearnedRule(user_id=user.id, description_key=key, categories_json=cats))
    await db.commit()
    return {"ok": True}

# ── Custom categories ─────────────────────────────────────────────────────────

@app.get("/api/categories")
async def api_get_categories(
    user: User         = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CustomCategory).where(CustomCategory.user_id == user.id)
    )
    cats = result.scalars().all()
    return [
        {"name": c.name, "icon": c.icon, "bg": c.bg, "text": c.text, "custom": True}
        for c in cats
    ]


@app.post("/api/categories")
async def api_create_category(
    request: Request,
    user:    User         = Depends(get_current_user),
    db:      AsyncSession = Depends(get_db),
):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    icon = str(body.get("icon", "")).strip()
    bg   = str(body.get("bg", "")).strip()
    text = str(body.get("text", "")).strip()

    if not _NAME_RE.match(name):
        raise HTTPException(400, "Nombre de categoría inválido")
    if not _HEX_RE.match(bg) or not _HEX_RE.match(text):
        raise HTTPException(400, "Color inválido (debe ser #RRGGBB)")
    if len(icon) > 10:
        raise HTTPException(400, "Ícono inválido")

    result = await db.execute(
        select(CustomCategory)
        .where(CustomCategory.user_id == user.id, CustomCategory.name == name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(400, "Ya existe esa categoría")
    db.add(CustomCategory(user_id=user.id, name=name, icon=icon, bg=bg, text=text))
    await db.commit()
    return {"ok": True}


@app.delete("/api/categories/{name}")
async def api_delete_category(
    name: str,
    user: User         = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CustomCategory)
        .where(CustomCategory.user_id == user.id, CustomCategory.name == name)
    )
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(404, "Categoría no encontrada")
    await db.delete(cat)
    await db.commit()
    return {"ok": True}

# ── Admin ─────────────────────────────────────────────────────────────────────

@app.get("/api/admin/users")
async def api_admin_list_users(
    admin: User         = Depends(get_admin_user),
    db:    AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User))
    users  = result.scalars().all()
    return [
        {
            "id":         u.id,
            "username":   u.username,
            "email":      u.email,
            "is_admin":   u.is_admin,
            "created_at": u.created_at.isoformat(),
            "has_google": bool(u.google_sub),
        }
        for u in users
    ]


@app.post("/api/admin/users")
async def api_admin_create_user(
    request: Request,
    admin:   User         = Depends(get_admin_user),
    db:      AsyncSession = Depends(get_db),
):
    body     = await request.json()
    username = body["username"].strip()
    email    = body["email"].strip().lower()

    result = await db.execute(
        select(User).where((User.email == email) | (User.username == username))
    )
    if result.scalar_one_or_none():
        raise HTTPException(400, "Ya existe un usuario con ese email o username")

    user = User(username=username, email=email, is_admin=body.get("is_admin", False))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"id": user.id, "username": user.username, "email": user.email}


@app.delete("/api/admin/users/{user_id}")
async def api_admin_delete_user(
    user_id: int,
    admin:   User         = Depends(get_admin_user),
    db:      AsyncSession = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(400, "No puedes eliminarte a ti mismo")
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Usuario no encontrado")
    await db.delete(user)
    await db.commit()
    return {"ok": True}

# ── PDF extraction ────────────────────────────────────────────────────────────

CATEGORY_RULES = [
    ("Pagos",            ["PAGO X PSE", "COBRO PRIMA SEGURO", "PAGO A",
                          "IMPTO GOBIERNO", "PAGO CREDITO SUC", "PAGO PSE",
                          "ABONO SUCURSAL"]),
    ("Fitness",          ["FITNESS24", "ZONAFIT", "ZONABIKER"]),
    ("Gasolina",         ["EDS EL BUENO", "EDS LA FLORA", "EDS BUENO", "EDS "]),
    ("Digital",          ["STEAM", "TEBEX", "NETFLIX", "EBANX", "MERCADO PAGO",
                          "MERCADOPAGO", "APPLE.COM", "CLAUDE.AI", "PAGSEGURO"]),
    ("Servicios",        ["MOVISTAR", "SIMIT VIAS", "DIR TRANSITO",
                          "BOLD*PLAN", "BOLD*PLANIFICACION", "SEGURO", "SERVICIOS WEB",
                          "MANEJO TARJETA DEB", "COMISION TRASLADO", "IVA COMIS TRASLADO",
                          "COMISION AVANCE", "INTERESES CORRIENTES", "PAYU", "CHOCONATO"]),
    ("Comida",           ["RAPPI", "HAMBURGUES", "SANDWICH", "PANINI", "RAMEN",
                          "CREPES", "WAFFLES", "STARBUCKS", "HORNO DE LENA",
                          "OXXO", "PLAY SHOTS", "LICORERA", "ALISON GUEVARA", "OBLEAS"]),
    ("Mercado",          ["ALMACENES EXITO", "EXITO BUCARAMANGA", "EXITO ORIENTA",
                          "TIENDAS ARA", "TIENDA D1", "PRICESMART", "MERCAGABY", "SURTIDORA",
                          "SUPERM MAS POR MENOS"]),
    ("Salidas",          ["CINE COLOMBIA", "MULTIPLEX", "PARQUEADERO"]),
    ("Compras",          ["PEPE GANGA", "FALABELLA", "BCS CARACOLI", "BMSUB"]),
    ("Centro Comercial", ["CEN CIAL"]),
    # Cuentas de débito / ahorros
    ("Pagos",            ["PAGO DE TARJETA DE CREDITO", "PAGO DE TARJETA"]),
    ("Ingresos",         ["RECIBISTE", "PAGO DE PROV", "ABONO INTERESES", "PAGO INTERBANC",
                          "ABONO POR INTERESES", "AVANCE A CTA TC"]),
    ("Efectivo",         ["RETIRO EN CAJERO", "RETIRO CAJERO", "AVANCE SUCURSAL"]),
    ("Ahorro",           ["ABRISTE UN CDT"]),
    ("Transferencias",   ["ENVIASTE A", "TRASLADO VIRTUAL", "TRANSF A",
                          "ENVIO POR BRE-B", "TRANSFERENCIA"]),
    ("Servicios",        ["CARGO POR IMPUESTO", "COBRO PORTAFOLIO", "CUOTA DE MANEJO"]),
]

FULL_RE = re.compile(
    r"^(\d{8})\s+(\d{6})\s+(.+?)\s+"
    r"(\d*\.\d{2}|\*+)\s+(\d{2} DE \d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s*$"
)
MEDIUM_RE = re.compile(
    r"^(\d{8})\s+(\d{6})\s+(.+?)\s+"
    r"(\d*\.\d{2}|\*+)\s+(\d{2} DE \d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s*$"
)
SHORT_RE = re.compile(
    r"^(\d{8})\s+(\d{6})\s+(.+?)\s+(-?\s*[\d,]+\.\d{2})\s*$"
)


def categorize(desc: str) -> str:
    d = desc.upper()
    for cat, kws in CATEGORY_RULES:
        for kw in kws:
            if kw.upper() in d:
                return cat
    return "Otros"


def parse_date(s: str) -> str:
    return f"{s[:2]}/{s[2:4]}/{s[4:]}" if re.match(r"^\d{8}$", s) else s


def clean_amount(s) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip().replace(" ", "").replace(",", "")
    if not s or re.match(r"^\*+$", s) or s == "00.00":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_cuota(s: str) -> tuple[Optional[int], Optional[int]]:
    m = re.match(r"^(\d{2}) DE (\d{2})$", s.strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def parse_summary(page1_text: str) -> dict:
    header_end = re.search(r"^\d{8}\s+\d{6}", page1_text, re.MULTILINE)
    header = page1_text[: header_end.start()] if header_end else page1_text[:800]
    summary: dict = {}

    dates = re.findall(r"\b(\d{2}-\d{2}-\d{4})\b", header)
    if dates:
        summary["fecha_corte"]  = dates[0]
        summary["fecha_limite"] = dates[-1] if len(dates) > 1 else dates[0]

    if "fecha_limite" in summary:
        pos   = header.rfind(summary["fecha_limite"])
        after = header[pos:]
        large = [
            float(a.replace(",", ""))
            for a in re.findall(r"([\d,]{4,}\.\d{2})", after)
            if float(a.replace(",", "")) > 1000
        ]
        if len(large) >= 2:
            summary["saldo_total"] = large[0]
            summary["pago_minimo"] = large[1]
        elif len(large) == 1:
            summary["saldo_total"] = large[0]

    all_large = [
        float(a.replace(",", ""))
        for a in re.findall(r"([\d,]{4,}\.\d{2})", header)
        if float(a.replace(",", "")) > 10_000
    ]
    if len(all_large) >= 7:
        summary["pagos_abonos"] = all_large[6]

    return summary


def _parse_line(line: str) -> Optional[dict]:
    m = FULL_RE.match(line)
    if m:
        tasa, cuota_str = m.group(4), m.group(5)
        if re.match(r"^\*+$", tasa) and re.match(r"^\*+$", cuota_str):
            return None
        valor = clean_amount(m.group(6))
        if valor is None:
            return None
        cuota_act, cuota_tot = parse_cuota(cuota_str)
        return _build(
            date_raw    = m.group(1),
            desc        = m.group(3),
            amount      = valor,
            saldo_corte = clean_amount(m.group(7)),
            cargos_mes  = clean_amount(m.group(8)),
            saldo_dif   = clean_amount(m.group(9)),
            cuota_act   = cuota_act,
            cuota_tot   = cuota_tot,
        )

    m = MEDIUM_RE.match(line)
    if m:
        valor = clean_amount(m.group(6))
        if valor is None:
            return None
        cuota_act, cuota_tot = parse_cuota(m.group(5))
        return _build(m.group(1), m.group(3), valor,
                      cuota_act=cuota_act, cuota_tot=cuota_tot)

    m = SHORT_RE.match(line)
    if m:
        valor = clean_amount(m.group(4))
        if valor is None:
            return None
        return _build(m.group(1), m.group(3), valor)

    return None


def _build(
    date_raw: str, desc: str, amount: float,
    saldo_corte: Optional[float] = None,
    cargos_mes:  Optional[float] = None,
    saldo_dif:   Optional[float] = None,
    cuota_act:   Optional[int]   = None,
    cuota_tot:   Optional[int]   = None,
) -> dict:
    return {
        "date":        parse_date(date_raw),
        "description": desc.strip(),
        "amount":      amount,
        "category":    categorize(desc),
        "saldo_corte": saldo_corte,
        "cargos_mes":  cargos_mes,
        "saldo_dif":   saldo_dif,
        "cuota_act":   cuota_act,
        "cuota_tot":   cuota_tot,
    }


# ── Debit account parser (Nu Cuenta de Ahorros) ───────────────────────────────

DEBIT_MONTH_MAP = {
    "ene": "01", "feb": "02", "mar": "03", "abr": "04",
    "may": "05", "jun": "06", "jul": "07", "ago": "08",
    "sep": "09", "oct": "10", "nov": "11", "dic": "12",
}

DEBIT_RE = re.compile(
    r"^(\d{2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\s+"
    r"(.+?)\s+([+-]\$[\d.,]+)\s*$",
    re.IGNORECASE,
)


def _detect_type(text: str) -> str:
    """Returns statement format: 'debito_nu', 'debito_bancolombia', 'debito_davivienda', 'credito_bancolombia', or 'credito'."""
    if "Nu Financiera" in text or "Cuenta Nu" in text:
        return "debito_nu"
    if "ESTADO DE CUENTA" in text and "CUENTA DE AHORROS" in text:
        return "debito_bancolombia"
    if "SALDO CIERRE MES ANTERIOR" in text or "CUENTA DE AHORROS LIBRETON" in text:
        return "debito_davivienda"
    if "Deuda a la fecha de corte:" in text or "Cupo total:" in text:
        return "credito_bancolombia"
    return "credito"


def _parse_cop_amount(s: str) -> Optional[float]:
    """Parse Colombian-format amount: +$1.234.567,89 or -$60.000,00"""
    s = s.strip()
    negative = s.startswith("-")
    s = re.sub(r"[+\-$\s]", "", s)
    s = s.replace(".", "").replace(",", ".")
    try:
        val = float(s)
        return -val if negative else val
    except ValueError:
        return None


def parse_debit_summary(page1_text: str) -> dict:
    summary: dict = {}

    m = re.search(r"(\d{2})\s*-\s*(\d{2})\s+([A-Za-z]{3})\s+(\d{4})", page1_text)
    if m:
        summary["periodo"] = f"{m.group(1)}-{m.group(2)} {m.group(3).upper()} {m.group(4)}"
        summary["year"]    = m.group(4)

    def find_cop(pattern: str) -> Optional[float]:
        fm = re.search(pattern, page1_text, re.IGNORECASE | re.DOTALL)
        return _parse_cop_amount(fm.group(1)) if fm else None

    amt = find_cop(r"inicio del mes\s+(\$[\d.,]+)")
    if amt is not None: summary["saldo_inicial"] = amt

    amt = find_cop(r"entr.{0,15}cuenta\s+(\+\$[\d.,]+)")
    if amt is not None: summary["total_entradas"] = abs(amt)

    amt = find_cop(r"sali.{0,15}cuenta\s+(-\$[\d.,]+)")
    if amt is not None: summary["total_salidas"] = abs(amt)

    amt = find_cop(r"Rendimiento total de tu cuenta\s+(\+\$[\d.,]+)")
    if amt is not None: summary["rendimientos"] = abs(amt)

    amt = find_cop(r"final del mes\s+(\$[\d.,]+)")
    if amt is not None: summary["saldo_final"] = amt

    return summary


def _parse_debit_line(line: str, year: str = "2026") -> Optional[dict]:
    m = DEBIT_RE.match(line.strip())
    if not m:
        return None
    day, month_str, desc, amount_str = m.group(1), m.group(2), m.group(3), m.group(4)
    month_num = DEBIT_MONTH_MAP.get(month_str.lower(), "01")
    amount    = _parse_cop_amount(amount_str)
    if amount is None:
        return None
    return {
        "date":        f"{day}/{month_num}/{year}",
        "description": desc.strip(),
        "amount":      amount,
        "category":    categorize(desc),
        "saldo_corte": None,
        "cargos_mes":  None,
        "saldo_dif":   None,
        "cuota_act":   None,
        "cuota_tot":   None,
    }


# ── Bancolombia savings account parser ────────────────────────────────────────

BANCOLOMBIA_TXN_RE = re.compile(
    r"^(\d{2}/\d{2})\s+(.+?)\s+(-?(?:[\d,]+)?\.\d{1,2})\s+(-?(?:[\d,]+)?\.\d{1,2})\s*$"
)


def parse_bancolombia_summary(text: str) -> dict:
    summary: dict = {}

    # Period: "DESDE: 2025/12/31 HASTA: 2026/03/31"
    m = re.search(r"DESDE:\s*(\d{4})/(\d{2})/(\d{2})\s+HASTA:\s*(\d{4})/(\d{2})/(\d{2})", text)
    if m:
        summary["desde_year"]  = m.group(1)
        summary["desde_month"] = int(m.group(2))
        summary["hasta_year"]  = m.group(4)
        summary["hasta_month"] = int(m.group(5))
        summary["periodo"]     = f"{m.group(3)}/{m.group(2)}/{m.group(1)} - {m.group(6)}/{m.group(5)}/{m.group(4)}"

    def find_bc_amount(label: str) -> Optional[float]:
        fm = re.search(label + r"\s*\$?\s*(\.?\d[\d,]*\.?\d*)", text)
        if not fm:
            return None
        raw = fm.group(1).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return None

    amt = find_bc_amount(r"SALDO ANTERIOR")
    if amt is not None: summary["saldo_inicial"] = amt

    amt = find_bc_amount(r"TOTAL ABONOS")
    if amt is not None: summary["total_entradas"] = amt

    amt = find_bc_amount(r"TOTAL CARGOS")
    if amt is not None: summary["total_salidas"] = amt

    amt = find_bc_amount(r"SALDO ACTUAL")
    if amt is not None: summary["saldo_final"] = amt

    amt = find_bc_amount(r"VALOR INTERESES PAGADOS")
    if amt is not None: summary["rendimientos"] = amt

    return summary


def _parse_bancolombia_line(line: str, desde_year: str, desde_month: int,
                             hasta_year: str, hasta_month: int) -> Optional[dict]:
    m = BANCOLOMBIA_TXN_RE.match(line.strip())
    if not m:
        return None
    date_raw, desc, valor_str, _saldo = m.group(1), m.group(2), m.group(3), m.group(4)
    day, month_str = date_raw.split("/")
    month = int(month_str)
    year  = hasta_year if month <= hasta_month else desde_year
    amount_raw = valor_str.replace(",", "")
    try:
        amount = float(amount_raw)
    except ValueError:
        return None
    return {
        "date":        f"{day}/{month_str}/{year}",
        "description": desc.strip(),
        "amount":      amount,
        "category":    categorize(desc),
        "saldo_corte": None,
        "cargos_mes":  None,
        "saldo_dif":   None,
        "cuota_act":   None,
        "cuota_tot":   None,
    }


# ── Bancolombia credit card parser ────────────────────────────────────────────

# 633777 23/03/2026 APPLE.COM/BILL $ 900,00 1/36 $ 25,00 1,9110 % 25,5026 % $ 875,00
BC_CRED_FULL_RE = re.compile(
    r"^(\d{6})\s+(\d{2}/\d{2}/\d{4})\s+(.+?)\s+"
    r"\$\s*(-?[\d.]+,\d{2})\s+"
    r"(\d+)/(\d+)\s+"
    r"\$\s*([\d.]+,\d{2})\s+"
    r"[\d,]+\s*%\s+[\d,]+\s*%\s+"
    r"\$\s*([\d.]+,\d{2})\s*$"
)

# 156269 17/03/2026 ABONO SUCURSAL VIRTUAL $ -300.000,00 $ -300.000,00 $ 0,00
BC_CRED_SIMPLE_RE = re.compile(
    r"^(\d{6})\s+(\d{2}/\d{2}/\d{4})\s+(.+?)\s+"
    r"\$\s*(-?[\d.]+,\d{2})\s+"
    r"\$\s*(-?[\d.]+,\d{2})\s+"
    r"\$\s*([\d.]+,\d{2})\s*$"
)

# 30/03/2026 INTERESES CORRIENTES $ 59.231,46 $ 59.231,46 $ 0,00
BC_CRED_NOAUTH_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+"
    r"\$\s*([\d.]+,\d{2})\s+"
    r"\$\s*([\d.]+,\d{2})\s+"
    r"\$\s*([\d.]+,\d{2})\s*$"
)

_BC_MONTH_MAP = {
    "ene": "01", "feb": "02", "mar": "03", "abr": "04",
    "may": "05", "jun": "06", "jul": "07", "ago": "08",
    "sep": "09", "oct": "10", "nov": "11", "dic": "12",
}


def _undecuple(s: str) -> str:
    """Collapse triple-encoded PDF text: 'TTTaaa' -> 'Ta'."""
    result = []
    i = 0
    while i < len(s):
        result.append(s[i])
        if i + 2 < len(s) and s[i] == s[i + 1] == s[i + 2]:
            i += 3
        else:
            i += 1
    return "".join(result)


def parse_bc_credit_summary(page1_text: str) -> dict:
    summary: dict = {}

    m = re.search(
        r"(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\.?"
        r"\s*-\s*(\d{1,2})\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\.?"
        r"\s+(\d{4})",
        page1_text, re.IGNORECASE,
    )
    if m:
        end_day   = m.group(3).zfill(2)
        end_month = _BC_MONTH_MAP.get(m.group(4).lower(), "01")
        summary["fecha_corte"] = f"{end_day}/{end_month}/{m.group(5)}"

    m = re.search(r"Cupo total:\s*\$\s*([\d.,]+)", page1_text)
    if m:
        amt = _parse_cop_amount(m.group(1))
        if amt is not None:
            summary["cupo_total"] = amt

    triple_amounts = re.findall(r"\${1,3}\s*((?:\d{3}|[.,]{3})+)", page1_text)
    decoded = [_parse_cop_amount(_undecuple(ta)) for ta in triple_amounts]
    decoded = [v for v in decoded if v is not None and v > 100]
    # deduplicate preserving order (saldo_total can appear twice in the layout)
    seen_vals: list = []
    for v in decoded:
        if v not in seen_vals:
            seen_vals.append(v)
    if seen_vals:
        summary["saldo_total"] = seen_vals[0]
    if len(seen_vals) >= 2:
        summary["pago_minimo"] = seen_vals[1]

    return summary


def _parse_bc_credit_line(line: str) -> Optional[dict]:
    m = BC_CRED_FULL_RE.match(line)
    if m:
        amount = _parse_cop_amount(m.group(4))
        if amount is None:
            return None
        return {
            "date":        m.group(2),
            "description": m.group(3).strip(),
            "amount":      amount,
            "category":    categorize(m.group(3)),
            "saldo_corte": None,
            "cargos_mes":  None,
            "saldo_dif":   _parse_cop_amount(m.group(8)),
            "cuota_act":   int(m.group(5)),
            "cuota_tot":   int(m.group(6)),
        }

    m = BC_CRED_SIMPLE_RE.match(line)
    if m:
        amount = _parse_cop_amount(m.group(4))
        if amount is None:
            return None
        return {
            "date":        m.group(2),
            "description": m.group(3).strip(),
            "amount":      amount,
            "category":    categorize(m.group(3)),
            "saldo_corte": None,
            "cargos_mes":  None,
            "saldo_dif":   None,
            "cuota_act":   None,
            "cuota_tot":   None,
        }

    m = BC_CRED_NOAUTH_RE.match(line)
    if m:
        amount = _parse_cop_amount(m.group(3))
        if amount is None:
            return None
        return {
            "date":        m.group(1),
            "description": m.group(2).strip(),
            "amount":      amount,
            "category":    categorize(m.group(2)),
            "saldo_corte": None,
            "cargos_mes":  None,
            "saldo_dif":   None,
            "cuota_act":   None,
            "cuota_tot":   None,
        }

    return None


# ── Davivienda savings account parser ────────────────────────────────────────

# 1316 28-02-2026 02-03-2026 AVANCE A CTA TC 40428044273826 150,000.00 3,558,419.59
DAVI_TXN_RE = re.compile(
    r"^(\d{4})\s+(\d{2}-\d{2}-\d{4})\s+(\d{2}-\d{2}-\d{4})\s+(.+?)\s+"
    r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$"
)

# 31-03-2026 31-03-2026 ABONO POR INTERESES DE CUENTA 2.00 3,100,706.86
DAVI_NONUM_RE = re.compile(
    r"^(\d{2}-\d{2}-\d{4})\s+(\d{2}-\d{2}-\d{4})\s+(.+?)\s+"
    r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$"
)


def _parse_us_amount(s: str) -> Optional[float]:
    try:
        return float(str(s).strip().replace(",", ""))
    except ValueError:
        return None


def parse_davivienda_summary(text: str) -> dict:
    summary: dict = {}

    m = re.search(r"DESDE:\s*(\d{2})-(\d{2})-(\d{4})\s+HASTA:\s*(\d{2})-(\d{2})-(\d{4})", text)
    if m:
        summary["desde_year"]  = m.group(3)
        summary["desde_month"] = int(m.group(2))
        summary["hasta_year"]  = m.group(6)
        summary["hasta_month"] = int(m.group(5))
        summary["periodo"]     = f"{m.group(1)}/{m.group(2)}/{m.group(3)} - {m.group(4)}/{m.group(5)}/{m.group(6)}"

    def find_us(label: str) -> Optional[float]:
        fm = re.search(label + r"\s+(?:\d+\s+)?([\d,]+\.\d{2})", text)
        return _parse_us_amount(fm.group(1)) if fm else None

    amt = find_us(r"SALDO CIERRE MES ANTERIOR")
    if amt is not None: summary["saldo_inicial"] = amt

    amt = find_us(r"\+ ABONOS")
    if amt is not None: summary["total_entradas"] = amt

    amt = find_us(r"\+ INTERESES RECIBIDOS")
    if amt is not None: summary["rendimientos"] = amt

    amt = find_us(r"- CARGOS")
    if amt is not None: summary["total_salidas"] = amt

    amt = find_us(r"SALDO FINAL")
    if amt is not None: summary["saldo_final"] = amt

    return summary


def _parse_davivienda_lines(text: str, prev_balance: list) -> list[dict]:
    """Parse Davivienda transaction lines. prev_balance is [float] (mutable) for cross-page continuity."""
    txns = []
    for line in text.split("\n"):
        line = line.strip()
        m = DAVI_TXN_RE.match(line)
        if m:
            date_raw, desc, amount_str, bal_str = m.group(2), m.group(4), m.group(5), m.group(6)
        else:
            m = DAVI_NONUM_RE.match(line)
            if not m:
                continue
            date_raw, desc, amount_str, bal_str = m.group(1), m.group(3), m.group(4), m.group(5)

        amount  = _parse_us_amount(amount_str)
        balance = _parse_us_amount(bal_str)
        if amount is None or balance is None:
            continue

        if prev_balance[0] is not None:
            sign = 1 if balance > prev_balance[0] else -1
        else:
            sign = -1
        prev_balance[0] = balance

        d, mo, y = date_raw.split("-")
        txns.append({
            "date":        f"{d}/{mo}/{y}",
            "description": desc.strip(),
            "amount":      sign * amount,
            "category":    categorize(desc),
            "saldo_corte": None,
            "cargos_mes":  None,
            "saldo_dif":   None,
            "cuota_act":   None,
            "cuota_tot":   None,
        })
    return txns


def extract_all(pdf_bytes: bytes, password: str = "") -> dict:
    txns: list = []
    seen: set  = set()
    summary: dict = {}
    fmt = "credito"
    text_read = False  # True once we've successfully read page text

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes), password=password) as pdf:
            first_text = pdf.pages[0].extract_text() or "" if pdf.pages else ""
            # Empty text on an existing page = still encrypted
            if not first_text and pdf.pages:
                raise ValueError("password_required")

            text_read = True
            fmt = _detect_type(first_text)

            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if i == 0:
                    if fmt == "debito_nu":
                        summary = parse_debit_summary(text)
                    elif fmt == "debito_bancolombia":
                        summary = parse_bancolombia_summary(text)
                    elif fmt == "debito_davivienda":
                        summary = parse_davivienda_summary(text)
                        davi_prev_balance = [summary.get("saldo_inicial")]
                    elif fmt == "credito_bancolombia":
                        summary = parse_bc_credit_summary(text)
                    else:
                        summary = parse_summary(text)

                if fmt == "debito_nu":
                    year = summary.get("year", "2026")
                    for line in text.split("\n"):
                        txn = _parse_debit_line(line.strip(), year)
                        if txn is None: continue
                        key = (txn["date"], txn["description"], txn["amount"])
                        if key in seen: continue
                        seen.add(key); txns.append(txn)

                elif fmt == "debito_bancolombia":
                    dy = summary.get("desde_year", "2026")
                    dm = summary.get("desde_month", 1)
                    hy = summary.get("hasta_year",  "2026")
                    hm = summary.get("hasta_month", 12)
                    for line in text.split("\n"):
                        txn = _parse_bancolombia_line(line.strip(), dy, dm, hy, hm)
                        if txn is None: continue
                        key = (txn["date"], txn["description"], txn["amount"])
                        if key in seen: continue
                        seen.add(key); txns.append(txn)

                elif fmt == "credito_bancolombia":
                    for line in text.split("\n"):
                        txn = _parse_bc_credit_line(line.strip())
                        if txn is None: continue
                        key = (txn["date"], txn["description"], txn["amount"])
                        if key in seen: continue
                        seen.add(key); txns.append(txn)

                elif fmt == "debito_davivienda":
                    for txn in _parse_davivienda_lines(text, davi_prev_balance):
                        key = (txn["date"], txn["description"], txn["amount"])
                        if key in seen: continue
                        seen.add(key); txns.append(txn)

                else:
                    for line in text.split("\n"):
                        txn = _parse_line(line.strip())
                        if txn is None: continue
                        key = (txn["date"], txn["description"], txn["amount"])
                        if key in seen: continue
                        seen.add(key); txns.append(txn)

    except ValueError:
        raise  # password_required — let api_extract handle it
    except Exception as e:
        if not text_read:
            # Failed before reading any text → almost certainly a password issue
            raise ValueError("password_required") from e
        raise  # Unexpected error during parsing — surface it normally

    statement_type = "credito" if fmt in ("credito", "credito_bancolombia") else "debito"
    return {
        "transactions":   txns,
        "summary":        {**summary, "statement_type": statement_type},
        "statement_type": statement_type,
    }


@app.post("/api/extract")
@limiter.limit("50/day", key_func=_extract_key)
async def api_extract(
    request:  Request,
    file:     UploadFile    = File(...),
    password: Optional[str] = Form(None),
    user:     User          = Depends(get_current_user),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Solo se aceptan archivos PDF")
    content = await file.read()
    if not content.startswith(b"%PDF-"):
        raise HTTPException(400, "El archivo no es un PDF válido")
    file_hash = hashlib.sha256(content).hexdigest()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(extract_all, content, password or ""),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(408, "El PDF tardó demasiado. Intenta con un archivo más pequeño.")
    except ValueError as e:
        if "password_required" in str(e):
            raise HTTPException(422, "password_required")
        logger.exception("PDF extraction ValueError")
        raise HTTPException(500, "No se pudo procesar el PDF")
    except Exception:
        logger.exception("PDF extraction error")
        raise HTTPException(500, "No se pudo procesar el PDF")
    if not result["transactions"]:
        raise HTTPException(400, "No se encontraron transacciones en el PDF")
    result["file_hash"] = file_hash
    return result


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/admin")
async def admin_page():
    return FileResponse("static/admin.html")


# ── Static files (must be last) ───────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")
