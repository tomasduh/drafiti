from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
import pdfplumber
import re
import io
from typing import Optional

app = FastAPI(title="Drafiti")

CATEGORY_RULES = [
    ("Pagos",            ["PAGO X PSE", "COBRO PRIMA SEGURO"]),
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
]

# ── Regexes ──────────────────────────────────────────────────────────────────

# Full: date comp desc tasa cuota val saldo_corte cargos_mes saldo_dif
FULL_RE = re.compile(
    r"^(\d{8})\s+(\d{6})\s+(.+?)\s+"
    r"(\d*\.\d{2}|\*+)\s+(\d{2} DE \d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s*$"
)
# tasa + cuota + single amount (payment rows)
MEDIUM_RE = re.compile(
    r"^(\d{8})\s+(\d{6})\s+(.+?)\s+"
    r"(\d*\.\d{2}|\*+)\s+(\d{2} DE \d{2}|\*+)\s+"
    r"(-?\s*[\d,]+\.\d{2}|\*+)\s*$"
)
# Single trailing amount (refunds / credits with no column info)
SHORT_RE = re.compile(
    r"^(\d{8})\s+(\d{6})\s+(.+?)\s+(-?\s*[\d,]+\.\d{2})\s*$"
)

# ── Helpers ───────────────────────────────────────────────────────────────────

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
    """Return (actual, total) or (None, None)."""
    m = re.match(r"^(\d{2}) DE (\d{2})$", s.strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# ── Statement summary ─────────────────────────────────────────────────────────

def parse_summary(page1_text: str) -> dict:
    # Header = everything before the first transaction line
    header_end = re.search(r"^\d{8}\s+\d{6}", page1_text, re.MULTILINE)
    header = page1_text[: header_end.start()] if header_end else page1_text[:800]

    summary: dict = {}

    # Dates in DD-MM-YYYY format
    dates = re.findall(r"\b(\d{2}-\d{2}-\d{4})\b", header)
    if dates:
        summary["fecha_corte"]  = dates[0]
        summary["fecha_limite"] = dates[-1] if len(dates) > 1 else dates[0]

    # Amounts that appear AFTER the last date (pago_total, pago_minimo)
    if "fecha_limite" in summary:
        pos   = header.rfind(summary["fecha_limite"])
        after = header[pos:]
        large = [
            float(a.replace(",", ""))
            for a in re.findall(r"([\d,]{4,}\.\d{2})", after)
            if float(a.replace(",", "")) > 1000
        ]
        if len(large) >= 2:
            summary["saldo_total"]  = large[0]
            summary["pago_minimo"]  = large[1]
        elif len(large) == 1:
            summary["saldo_total"]  = large[0]

    # Pagos y abonos: largest amount that starts with 6,xxx,xxx (this statement specific)
    # More generally: find the "pagos" amount in the summary block
    pagos_candidates = [
        float(a.replace(",", ""))
        for a in re.findall(r"([\d,]{4,}\.\d{2})", header)
        if float(a.replace(",", "")) > 100_000
    ]
    # The pagos amount is typically the one that reduces the balance —
    # we can infer it: saldo_total ≈ saldo_anterior + facturacion - pagos + intereses
    # For display we expose the raw figure from the text: look for it after the
    # intereses lines (small numbers) and before saldo_total.
    # Simpler: use all large amounts; pagos is typically position 7 in the list.
    all_large = [
        float(a.replace(",", ""))
        for a in re.findall(r"([\d,]{4,}\.\d{2})", header)
        if float(a.replace(",", "")) > 10_000
    ]
    # Known order for BBVA: cupo_total, cupo_disp, saldo_ant, facturacion,
    #   cupo_disp, [interest], pagos, saldo_total, pago_total, pago_minimo
    if len(all_large) >= 7:
        summary["pagos_abonos"] = all_large[6]

    return summary


# ── Transaction parser ────────────────────────────────────────────────────────

def _parse_line(line: str) -> Optional[dict]:
    m = FULL_RE.match(line)
    if m:
        tasa, cuota_str = m.group(4), m.group(5)
        if re.match(r"^\*+$", tasa) and re.match(r"^\*+$", cuota_str):
            return None  # supplementary foreign-currency row
        valor = clean_amount(m.group(6))
        if valor is None:
            return None
        cuota_act, cuota_tot = parse_cuota(cuota_str)
        return _build(
            date_raw     = m.group(1),
            desc         = m.group(3),
            amount       = valor,
            saldo_corte  = clean_amount(m.group(7)),
            cargos_mes   = clean_amount(m.group(8)),
            saldo_dif    = clean_amount(m.group(9)),
            cuota_act    = cuota_act,
            cuota_tot    = cuota_tot,
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
        # debt columns
        "saldo_corte": saldo_corte,
        "cargos_mes":  cargos_mes,
        "saldo_dif":   saldo_dif,
        "cuota_act":   cuota_act,
        "cuota_tot":   cuota_tot,
    }


# ── Main extraction ───────────────────────────────────────────────────────────

def extract_all(pdf_bytes: bytes) -> dict:
    txns: list  = []
    seen: set   = set()
    summary: dict = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if i == 0:
                summary = parse_summary(text)
            for line in text.split("\n"):
                txn = _parse_line(line.strip())
                if txn is None:
                    continue
                key = (txn["date"], txn["description"], txn["amount"])
                if key in seen:
                    continue
                seen.add(key)
                txns.append(txn)

    return {"transactions": txns, "summary": summary}


# ── API ───────────────────────────────────────────────────────────────────────

@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
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


app.mount("/", StaticFiles(directory="static", html=True), name="static")
