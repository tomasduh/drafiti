from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import pdfplumber
import re
import io
import os
from typing import Optional

from db import init_db, get_db, SessionLocal, User, HistoryEntry, LearnedRule, CustomCategory
import auth

ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL",    "tomasduh421@gmail.com")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "tomasduh")

# ── Startup ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _ensure_admin()
    yield


async def _ensure_admin():
    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
        if not result.scalar_one_or_none():
            db.add(User(username=ADMIN_USERNAME, email=ADMIN_EMAIL, is_admin=True))
            await db.commit()


app = FastAPI(title="Drafiti", lifespan=lifespan)

# ── Auth dependency ───────────────────────────────────────────────────────────

async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    token = auth.get_token_from_request(request)
    if not token:
        raise HTTPException(401, "No autenticado")
    user_id = auth.decode_session_token(token)
    if not user_id:
        raise HTTPException(401, "Sesión inválida")
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
async def auth_login():
    return RedirectResponse(auth.google_auth_url_build())


@app.get("/auth/callback")
async def auth_callback(code: str = "", error: str = "", db: AsyncSession = Depends(get_db)):
    if error or not code:
        return RedirectResponse("/login?error=oauth")
    try:
        tokens   = await auth.exchange_code(code)
        userinfo = await auth.get_userinfo(tokens["access_token"])
    except Exception:
        return RedirectResponse("/login?error=oauth")

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
        return RedirectResponse("/login?error=not_registered")

    token    = auth.create_session_token(user.id)
    response = RedirectResponse("/")
    response.set_cookie(
        auth.COOKIE_NAME, token,
        httponly=True,
        secure=auth.is_secure(),
        samesite="lax",
        max_age=auth.TOKEN_EXPIRE_DAYS * 86400,
    )
    return response


@app.post("/auth/logout")
async def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME)
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
            "id":          e.id,
            "filename":    e.filename,
            "fecha_corte": e.fecha_corte,
            "uploadedAt":  e.uploaded_at,
            "total":       e.total,
            "summary":     e.summary_json,
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
    body  = await request.json()
    entry = HistoryEntry(
        id               = body["id"],
        user_id          = user.id,
        filename         = body["filename"],
        fecha_corte      = body.get("fecha_corte"),
        uploaded_at      = body["uploadedAt"],
        total            = body["total"],
        summary_json     = body.get("summary", {}),
        transactions_json= body["transactions"],
        sort_key         = int(body["id"]),
    )
    db.add(entry)

    # Keep max 20 entries
    result   = await db.execute(
        select(HistoryEntry)
        .where(HistoryEntry.user_id == user.id)
        .order_by(HistoryEntry.sort_key.desc())
    )
    all_entries = result.scalars().all()
    if len(all_entries) >= 20:
        for old in all_entries[19:]:
            await db.delete(old)

    await db.commit()
    return {"ok": True}


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
    key  = body["key"].upper().strip()
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
    name = body["name"]
    result = await db.execute(
        select(CustomCategory)
        .where(CustomCategory.user_id == user.id, CustomCategory.name == name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(400, "Ya existe esa categoría")
    db.add(CustomCategory(
        user_id=user.id, name=name,
        icon=body["icon"], bg=body["bg"], text=body["text"]
    ))
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
    ("Pagos",            ["PAGO X PSE", "COBRO PRIMA SEGURO", "PAGO A"]),
    ("Fitness",          ["FITNESS24", "ZONAFIT", "ZONABIKER"]),
    ("Gasolina",         ["EDS EL BUENO", "EDS LA FLORA", "EDS BUENO"]),
    ("Digital",          ["STEAM", "TEBEX", "NETFLIX", "EBANX", "MERCADO PAGO"]),
    ("Servicios",        ["MOVISTAR", "SIMIT VIAS", "DIR TRANSITO",
                          "BOLD*PLAN", "BOLD*PLANIFICACION", "SEGURO", "SERVICIOS WEB"]),
    ("Comida",           ["RAPPI", "HAMBURGUES", "SANDWICH", "PANINI", "RAMEN",
                          "CREPES", "WAFFLES", "STARBUCKS", "HORNO DE LENA",
                          "OXXO", "PLAY SHOTS", "LICORERA", "ALISON GUEVARA"]),
    ("Mercado",          ["ALMACENES EXITO", "EXITO BUCARAMANGA", "EXITO ORIENTA",
                          "TIENDAS ARA", "TIENDA D1", "PRICESMART", "MERCAGABY", "SURTIDORA"]),
    ("Salidas",          ["CINE COLOMBIA", "MULTIPLEX", "PARQUEADERO"]),
    ("Compras",          ["PEPE GANGA", "FALABELLA", "BCS CARACOLI", "BMSUB"]),
    ("Centro Comercial", ["CEN CIAL"]),
    # Débito (cuenta corriente / ahorros)
    ("Ingresos",         ["RECIBISTE"]),
    ("Efectivo",         ["RETIRO EN CAJERO", "RETIRO CAJERO"]),
    ("Ahorro",           ["ABRISTE UN CDT"]),
    ("Transferencias",   ["ENVIASTE A"]),
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
    """Returns 'debito' for Nu debit account statements, 'credito' otherwise."""
    if "Nu Financiera" in text or "Cuenta Nu" in text:
        return "debito"
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


def extract_all(pdf_bytes: bytes) -> dict:
    txns:    list = []
    seen:    set  = set()
    summary: dict = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        first_text     = pdf.pages[0].extract_text() or "" if pdf.pages else ""
        statement_type = _detect_type(first_text)

        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if i == 0:
                summary = (
                    parse_debit_summary(text)
                    if statement_type == "debito"
                    else parse_summary(text)
                )
            year = summary.get("year", "2026")
            for line in text.split("\n"):
                txn = (
                    _parse_debit_line(line.strip(), year)
                    if statement_type == "debito"
                    else _parse_line(line.strip())
                )
                if txn is None:
                    continue
                key = (txn["date"], txn["description"], txn["amount"])
                if key in seen:
                    continue
                seen.add(key)
                txns.append(txn)

    return {
        "transactions":   txns,
        "summary":        {**summary, "statement_type": statement_type},
        "statement_type": statement_type,
    }


@app.post("/api/extract")
async def api_extract(
    file: UploadFile = File(...),
    user: User       = Depends(get_current_user),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Solo se aceptan archivos PDF")
    content = await file.read()
    try:
        result = extract_all(content)
    except Exception as e:
        raise HTTPException(500, f"Error procesando el PDF: {e}")
    if not result["transactions"]:
        raise HTTPException(400, "No se encontraron transacciones en el PDF")
    return result


@app.get("/admin")
async def admin_page():
    return FileResponse("static/admin.html")


# ── Static files (must be last) ───────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")
