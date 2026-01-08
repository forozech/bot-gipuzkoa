from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import asyncio
from datetime import timezone
import httpx
import time

import unicodedata
import re

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return s

router = Router()
import feedparser
import re
from datetime import datetime

# =========================
# EXTRACCIONES DESDE RSS
# =========================

RE_DEADLINE = re.compile(
    r"Fecha límite[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4})",
    re.IGNORECASE
)

RE_BUDGET = re.compile(
    r"Presupuesto[^0-9]*([\d\.]+,\d{2})",
    re.IGNORECASE
)


def extract_deadline(text: str):
    if not text:
        return None

    m = RE_DEADLINE.search(text)
    if not m:
        return None

    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y").date().isoformat()
    except Exception:
        return None


def extract_budget(text: str):
    if not text:
        return None

    m = RE_BUDGET.search(text)
    if not m:
        return None

    try:
        return float(
            m.group(1)
            .replace(".", "")
            .replace(",", ".")
        )
    except Exception:
        return None


def extract_entity(entry):
    # RSS suele traerlo en author o en summary
    if getattr(entry, "author", None):
        return entry.author.strip()

    if entry.get("summary"):
        m = re.search(
            r"Poder adjudicador[^:]*:\s*([^<\n]+)",
            entry.summary,
            re.IGNORECASE
        )
        if m:
            return m.group(1).strip()

    return "OTROS"


# =========================
# RSS URLs (GIPUZKOA)
# =========================

RSS_URLS = {
    ("OBR", "ABI"): "https://www.contratacion.euskadi.eus/ac70cPublicidadWar/suscribirAnuncio/suscripcionRss?p01=1&p02=3&p26=ES212&idioma=es",
    ("OBR", "CER"): "https://www.contratacion.euskadi.eus/ac70cPublicidadWar/suscribirAnuncio/suscripcionRss?p01=1&p02=4&p26=ES212&idioma=es",
    ("SERV", "ABI"): "https://www.contratacion.euskadi.eus/ac70cPublicidadWar/suscribirAnuncio/suscripcionRss?p01=2&p02=3&p26=ES212&idioma=es",
    ("SERV", "CER"): "https://www.contratacion.euskadi.eus/ac70cPublicidadWar/suscribirAnuncio/suscripcionRss?p01=2&p02=4&p26=ES212&idioma=es",
}


# =========================
# LOAD CONTRACTS DESDE RSS
# =========================

async def load_contracts(contrato, estado):
    """
    contrato: OBR | SERV | ING
    estado: ABI | PLZ | CER
    """

    # Ingeniería es subconjunto de servicios
    rss_contrato = "SERV" if contrato == "ING" else contrato

    rss_estado = "ABI" if estado == "PLZ" else estado
    rss_url = RSS_URLS[(rss_contrato, rss_estado)]

    feed = feedparser.parse(rss_url)

    items = []

    for e in feed.entries:
        summary = e.get("summary", "")

        item = {
            "id": e.get("id") or e.get("link"),
            "object": e.get("title", "").strip(),
            "entity": {
                "name": extract_entity(e)
            },
            "firstPublicationDate": (
                datetime(*e.published_parsed[:6]).date().isoformat()
                if getattr(e, "published_parsed", None)
                else None
            ),
            "deadlineDate": extract_deadline(summary),
            "budgetWithoutVAT": extract_budget(summary),
            "mainEntityOfPage": e.get("link"),
        }

        items.append(item)

    return {"items": items}


# =========================
# SAFE EDIT (evita errores Telegram)
# =========================
async def safe_edit(message, text: str, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise

# =========================
# CACHE EN MEMORIA
# =========================
CACHE = {}
CACHE_TTL = 900  # 15 minutos
SUMMARY_PAGE_SIZE = 4


def get_cache(key):
    v = CACHE.get(key)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > CACHE_TTL:
        CACHE.pop(key, None)
        return None
    return data

def set_cache(key, data):
    CACHE[key] = (time.time(), data)
    
# =========================
# AVISOS AUTOMÁTICOS ABIERTAS
# =========================
SEEN_OPEN_IDS = set()

# 👉 pon aquí TU chat (puede ser grupo o privado)
ALERT_CHAT_ID = -1003637338441  # <-- CAMBIA ESTO


# =========================
# FORMATOS
# =========================
def fmt_date(d):
    if not d:
        return "—"
    return datetime.fromisoformat(d[:10]).strftime("%d/%m/%Y")

def fmt_money(x):
    if x is None:
        return "—"
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

def get_notice_url(it):
    """
    Devuelve SIEMPRE el enlace correcto al anuncio.
    Nunca el del poder adjudicador.
    """
    # 1️⃣ enlace público del anuncio (el bueno)
    if it.get("mainEntityOfPage"):
        return it["mainEntityOfPage"]

    # 2️⃣ fallback: enlace API del notice
    self_link = ((it.get("_links") or {}).get("self") or {}).get("href")
    if self_link:
        return self_link

    return None

# =========================
# FILTRO ÁMBITO – GIPUZKOA
# =========================

GIP_MUNICIPIOS = [
    # territorio
    "GIPUZKOA",
    "GUIPUZCOA",

    # comarcas / consorcios
    "TXINGUDI",
    "ANARBE",
    "AÑARBE",
    "BIDASOA",
    "DEBABARRENA",
    "DEBAGOIENA",
    "SAN MARCOS",
    "SAN MARKOS",

    # capitales y ciudades
    "DONOSTIA",
    "SAN SEBASTIAN",
    "IRUN",
    "EIBAR",
    "HERNANI",
    "TOLOSA",
    "ZARAUTZ",
    "AZPEITIA",
    "AZKOITIA",
    "ARRASATE",
    "MONDRAGON",
    "DONOSTIA", "SAN SEBASTIAN",
    "HERNANI",
    "USURBIL",
    "LASARTE ORIA", "LASARTE-ORIA",
    "ASTIGARRAGA",
    "ERRENTERIA", "RENTERIA",
    "PASAIA", "PASAJES",
    "LEZO",
    "OIARTZUN",
    "IRUN",
    "HONDARRIBIA", "FUENTERRABIA",
    "BIDASOA",
    "TXINGUDI",
    "ZARAUTZ",
    "GETARIA",
    "ZUMAIA", "ZUMAYA",
    "AZPEITIA",
    "AZKOITIA",
    "AIZARNABAL",
    "AIA",
    "ERREZIL",
    "TOLOSA",
    "ANDOAIN",
    "ADUNA",
    "ALKIZA",
    "ALTZO",
    "AMEZKETA",
    "ANOETA",
    "BALIARRAIN",
    "BERASTEGI",
    "BIDEGOIAN", "BIDEGOYAN",
    "ELDRAIN",
    "GAZTELU",
    "HERNIALDE",
    "IKAZTEGIETA",
    "IRURA",
    "LIZARTZA",
    "OREXA",
    "ARRASATE", "MONDRAGON",
    "ARETXABALETA",
    "BERGARA",
    "ELGETA",
    "ESKORIATZA",
    "LEINTZ GATZAGA", "SALINAS DE LENIZ",
    "OÑATI", "ONATI",
    "EIBAR",
    "ELGOIBAR",
    "MENDARO",
    "DEBA",
    "MUTRIKU", "MOTRICO",
    "SORALUZE", "PLACENCIA DE LAS ARMAS",
    "BEASAIN",
    "ORDIZIA", "VILLAFRANCA DE ORIA",
    "ZEGAMA",
    "SEGURA",
    "IDIAZABAL",
    "LAZKAO",
    "OLABERRIA",
    "ZALDIBIA",
    "ATAUN",
    "GABIRIA",
]

def is_gipuzkoa(it):
    # 1️⃣ NUTS en contractingAuthority.codNUTS
    ca = it.get("contractingAuthority") or {}
    if ca.get("codNUTS") == "ES212":
        return True

    # 2️⃣ NUTS en contractingAuthority.codNUTSList
    nuts_list = ca.get("codNUTSList") or []
    if "ES212" in nuts_list:
        return True

    # 3️⃣ NUTS en entity.codNUTS (muy común en ayuntamientos)
    ent = it.get("entity") or {}
    if ent.get("codNUTS") == "ES212":
        return True

    # 4️⃣ fallback por texto (solo si NO hay NUTS)
    txt = normalize_text(
        " ".join([
            it.get("object", ""),
            ent.get("name", ""),
            ca.get("name", "")
        ])
    )
    return any(m in txt for m in GIP_MUNICIPIOS)


# =========================
# FILTRO SERVICIOS – INGENIERÍAS
# =========================

ING_POSITIVE = [
    # ingeniería general
    "INGENIER",
    "INGENIERIA",
    "ING",

    # proyectos
    "PROYECT",
    "REDACCION",
    "ESTUDIO",
    "MEMORIA",
    "CALCULO",

    # obra
    "DIRECCION OBRA",
    "DIR OBRA",
    "ASISTENCIA TECNICA",
    "ASIST TECN",
    "CONTROL OBRA",
    "SUPERVISION",

    # especialidades
    "ELECTRIC",
    "INSTALACION",
    "CLIMATIZACION",
    "SANEAMIENTO",
    "AGUA",
    "DEPURADORA",
    "ABASTECIMIENTO",
    "URBANIZACION",
    "ESTRUCTURA",
    "CARRETERA",
    "CAMINO",
]
ING_NEGATIVE = [
    "LIMPIEZA",
    "CARPINTERIA",
    "VIGILANCIA",
    "SEGURIDAD PRIVADA",
    "ATENCION",
    "CONTROL DE ACCESO",
    "GESTION",
    "EDUCATIVO",
    "SOCIAL",
    "CULTURAL",
    "ESCENICA",
    "MUSEO",
    "ALUMNADO",
]

def is_ingenieria(it):
    txt = normalize_text(it.get("object", ""))

    if not any(k in txt for k in ING_POSITIVE):
        return False

    if any(k in txt for k in ING_NEGATIVE):
        return False

    return True



def filter_en_plazo(items):
    today = datetime.utcnow().date()
    out = []

    for it in items:
        d = it.get("deadlineDate")
        if not d:
            continue
        try:
            if datetime.fromisoformat(d[:10]).date() >= today:
                out.append(it)
        except Exception:
            pass

    return out

BIG_AMOUNT = 1_000_000
ALERT_DAYS = 7

import feedparser

async def load_contracts(contrato, estado):
    key = (contrato if contrato != "ING" else "SERV", estado)
    rss_url = RSS_URLS[key]

    feed = feedparser.parse(rss_url)

    items = []
    for e in feed.entries:
        items.append({
            "id": e.id,
            "object": e.title,
            "link": e.link,
            "entity": e.get("author", ""),
            "firstPublicationDate": e.published,
            "deadlineDate": extract_deadline(e.summary),
            "budgetWithoutVAT": extract_budget(e.summary),
        })

    return {"items": items}

def apply_filters(items, contrato, estado):
    out = items

    if estado == "PLZ":
        out = filter_en_plazo(out)

    if contrato == "ING":
        out = [it for it in out if is_ingenieria(it)]

    return out


def group_and_sort(items):
    grouped = {}

    for it in items:
        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    # ordenar entidades y contratos
    entities = []
    for ent, its in grouped.items():
        its_sorted = sorted(
            its,
            key=lambda x: x.get("deadlineDate") or "9999-12-31"
        )
        entities.append((ent, its_sorted))

    return sorted(entities, key=lambda x: x[0])


# =========================
# RESUMEN (SIN LÍMITES)
# =========================
def build_summary_page(entities, kind, mode, summary_page, summary_page_size=4):
    total_pages = (len(entities) + summary_page_size - 1) // summary_page_size

    block = entities[
        summary_page * summary_page_size :
        (summary_page + 1) * summary_page_size
    ]

    today = datetime.utcnow().date()

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"🧾 **RESUMEN — {kind} — {mode}**",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for entity, items in block:
        lines.append(f"\n📜 **{entity.upper()}**")
        total = 0.0

        items_sorted = sorted(
            items,
            key=lambda x: x.get("deadlineDate") or "9999-12-31"
        )

        for it in items_sorted:
            published = fmt_date(it.get("firstPublicationDate"))
            deadline_raw = it.get("deadlineDate")
            deadline = fmt_date(deadline_raw)
            amount = it.get("budgetWithoutVAT")
            money = fmt_money(amount)

            if amount:
                total += amount

            alert = ""
            if deadline_raw:
                try:
                    d = datetime.fromisoformat(deadline_raw[:10]).date()
                    if (d - today).days <= ALERT_DAYS:
                        alert = " ❗"
                except Exception:
                    pass

            icon = "💎" if amount and amount >= BIG_AMOUNT else "💵"

            lines.append(
                f"⏰ {deadline}{alert} · {icon} {money}"
            )

        lines.append(f"🏷 TOTAL: {fmt_money(total)}")

    lines.append(
        f"\n📄 _Resumen · Página {summary_page+1}/{total_pages}_"
    )

    return "\n".join(lines), total_pages

async def get_open_contracts_today():
    today = datetime.now(pytz.timezone("Europe/Madrid")).date()

    url = (
        "https://api.euskadi.eus/procurements/contracting-notices"
        "?contract-type-id=1"
        "&contract-procedure-status-id=3"
        "&itemsOfPage=50"
        "&lang=SPANISH"
    )

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        data = r.json()

    items = data.get("items", [])
    today_items = []

    for it in items:
        pub = it.get("firstPublicationDate")
        if not pub:
            continue
        try:
            pub_date = datetime.fromisoformat(pub[:10]).date()
        except Exception:
            continue
        if pub_date == today:
            today_items.append(it)

    return today_items

async def send_open_contracts_today_short(bot):
    items = await get_open_contracts_today()

    if not items:
        return

    grouped = {}
    for it in items:
        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    lines = [
        "🆕 **NOVEDADES DE HOY (ABIERTAS)**",
        ""
    ]

    for ent in sorted(grouped):
        its = grouped[ent]
        amounts = [
            fmt_money(it.get("budgetWithoutVAT"))
            for it in its
            if it.get("budgetWithoutVAT")
        ]

        lines.append(
            f"🏛 **{ent}**: {len(its)} anuncio(s)\n"
            f"   💰 " + "; ".join(amounts)
        )

    lines.append("\n👉 Usa /novedades para ver el detalle completo")

    await bot.send_message(
        chat_id=ALERT_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

# =========================
# NORMALIZACIÓN / CONSTANTES
# =========================

# Tipos de contrato
K_OBRAS = "OBR"
K_SERV  = "SERV"
K_ING   = "ING"

# Estados
E_ABIERTAS = "ABI"
E_EN_PLAZO = "PLZ"
E_CERRADAS = "CER"

# Ámbito
A_GEN = "GEN"
A_GIP = "GIP"

# Vista
V_RES = "RES"
V_DET = "DET"

def build_header(vista, contrato, ambito, estado):
    return f"🧾 **{vista} · {contrato} · {ambito} · {estado}**"

# =========================
# TECLADOS
# =========================

def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(
        timezone=pytz.timezone("Europe/Madrid")
    )

    scheduler.add_job(
        send_open_contracts_today_short,
        CronTrigger(hour=11, minute=0),
        args=[bot],
        id="open_today_11"
    )

    scheduler.add_job(
        send_open_contracts_today_short,
        CronTrigger(hour=17, minute=0),
        args=[bot],
        id="open_today_17"
    )

    scheduler.start()

def kb_start():
    kb = InlineKeyboardBuilder()
    kb.button(text="👨‍🔧", callback_data="c:OBR")
    kb.button(text="👩‍💻", callback_data="c:SERV")
    kb.button(text="📐", callback_data="c:ING")
    kb.button(text="🚀", callback_data="reset")
    kb.adjust(3, 1)
    return kb.as_markup()

def kb_estado(contrato: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="⏱️", callback_data=f"e:{contrato}:ABI")
    kb.button(text="⏰", callback_data=f"e:{contrato}:PLZ")
    kb.button(text="🔒", callback_data=f"e:{contrato}:CER")
    kb.button(text="🏫", callback_data="home")
    kb.button(text="🚀", callback_data="reset")
    kb.adjust(3, 1)
    return kb.as_markup()

def kb_ambito(contrato: str, estado: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="🌍", callback_data=f"a:{contrato}:{estado}:GEN")
    kb.button(text="📍", callback_data=f"a:{contrato}:{estado}:GIP")
    kb.button(text="🏫", callback_data="home")
    kb.button(text="🚀", callback_data="reset")
    kb.adjust(2, 2)
    return kb.as_markup()

def kb_vista(contrato: str, estado: str, ambito: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="📋", callback_data=f"v:{contrato}:{estado}:{ambito}:RES")
    kb.button(text="🔍", callback_data=f"v:{contrato}:{estado}:{ambito}:DET")
    kb.button(text="🏫", callback_data="home")
    kb.button(text="🚀", callback_data="reset")
    kb.adjust(2, 2)
    return kb.as_markup()

def kb_detalle_nav(contrato, estado, ambito, page, total_pages):
    kb = InlineKeyboardBuilder()

    if page > 0:
        kb.button(
            text="◁",
            callback_data=f"detpage:{contrato}:{estado}:{ambito}:{page-1}"
        )
    if page < total_pages - 1:
        kb.button(
            text="▷",
            callback_data=f"detpage:{contrato}:{estado}:{ambito}:{page+1}"
        )

    # 📋 CAMBIO DE VISTA
    kb.button(
        text="📋 Resumen",
        callback_data=f"v:{contrato}:{estado}:{ambito}:RES"
    )

    kb.button(text="🏠", callback_data="home")
    kb.button(text="🚀", callback_data="reset")

    kb.adjust(2, 2)
    return kb.as_markup()


def kb_resumen_nav(contrato, estado, ambito, page, total_pages):
    kb = InlineKeyboardBuilder()

    # navegación
    if page > 0:
        kb.button(
            text="◁",
            callback_data=f"respage:{contrato}:{estado}:{ambito}:{page-1}"
        )
    if page < total_pages - 1:
        kb.button(
            text="▷",
            callback_data=f"respage:{contrato}:{estado}:{ambito}:{page+1}"
        )

    # 🔍 CAMBIO DE VISTA
    kb.button(
        text="🔍 Detalle",
        callback_data=f"v:{contrato}:{estado}:{ambito}:DET"
    )

    # acciones globales
    kb.button(text="🏠", callback_data="home")
    kb.button(text="🚀", callback_data="reset")

    kb.adjust(2, 2)
    return kb.as_markup()



# =========================
# START
# =========================
@router.message(F.text == "/start")
async def start_cmd(msg: Message):
    await msg.answer(
        "💼 **CONTRATO**",
        reply_markup=kb_start(),
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    await safe_edit(cb.message, "🏠 Menú principal:", reply_markup=kb_start())
    await cb.answer()

@router.callback_query(F.data == "reset")
async def reset(cb: CallbackQuery):
    await safe_edit(cb.message, "✅ Reset hecho:", reply_markup=kb_start())
    await cb.answer()

@router.callback_query(F.data.startswith("c:"))
async def pick_contrato(cb: CallbackQuery):
    contrato = cb.data.split(":")[1]

    await safe_edit(
        cb.message,
        f"📂 **{contrato} — ESTADO**",
        parse_mode="Markdown",
        reply_markup=kb_estado(contrato)  # ⚠️ se crea en el paso 3
    )
    await cb.answer()

@router.callback_query(F.data.startswith("e:"))
async def pick_estado(cb: CallbackQuery):
    _, contrato, estado = cb.data.split(":")

    await safe_edit(
        cb.message,
        f"📊 **{contrato} — {estado} — ÁMBITO**",
        parse_mode="Markdown",
        reply_markup=kb_ambito(contrato, estado)  # se crea en el paso 4
    )
    await cb.answer()

@router.callback_query(F.data.startswith("a:"))
async def pick_ambito(cb: CallbackQuery):
    _, contrato, estado, ambito = cb.data.split(":")

    await safe_edit(
        cb.message,
        f"📑 **{contrato} — {estado} — {ambito} — VISTA**",
        parse_mode="Markdown",
        reply_markup=kb_vista(contrato, estado, ambito)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("v:"))
async def pick_vista(cb: CallbackQuery):
    _, contrato, estado, ambito, vista = cb.data.split(":")

    header = build_header(vista, contrato, ambito, estado)

    data = await load_contracts(contrato, estado)
    items = data.get("items", [])

    items = apply_filters(items, contrato, estado, ambito)
    entities = group_and_sort(items)

    if not entities:
        await safe_edit(
            cb.message,
            f"{header}\n\nℹ️ No hay resultados.",
            parse_mode="Markdown",
            reply_markup=kb_vista(contrato, estado, ambito)
        )
        return

    # RESUMEN
    if vista == "RES":
        text, total_pages = build_summary_page(
            entities,
            contrato,
            estado,
            summary_page=0,
            summary_page_size=SUMMARY_PAGE_SIZE
        )

        await safe_edit(
            cb.message,
            text,
            parse_mode="Markdown",
            reply_markup=kb_resumen_nav(contrato, estado, ambito, 0, total_pages),
            disable_web_page_preview=True
        )
        return

    # DETALLE
    await render_page(
        cb,
        kind=contrato,
        mode=estado,
        entities=entities,
        page=0,
        page_size=2
    )

@router.callback_query(F.data.startswith("respage:"))
async def change_res_page(cb: CallbackQuery):
    _, contrato, estado, ambito, page = cb.data.split(":")
    page = int(page)

    data = await load_contracts(contrato, estado)
    items = data.get("items", [])

    items = apply_filters(items, contrato, estado, ambito)
    entities = group_and_sort(items)

    if not entities:
        await safe_edit(
            cb.message,
            "ℹ️ No hay resultados.",
            parse_mode="Markdown",
            reply_markup=kb_resumen_nav(contrato, estado, ambito, 0, 1)
        )
        return

    total_pages = (len(entities) + SUMMARY_PAGE_SIZE - 1) // SUMMARY_PAGE_SIZE

    # 🔒 CLAMP REAL (ESTO ES LA CLAVE)
    if page < 0:
        page = 0
    elif page >= total_pages:
        page = total_pages - 1

    text, _ = build_summary_page(
        entities,
        contrato,
        estado,
        summary_page=page,
        summary_page_size=SUMMARY_PAGE_SIZE
    )

    await safe_edit(
        cb.message,
        text,
        parse_mode="Markdown",
        reply_markup=kb_resumen_nav(
            contrato, estado, ambito, page, total_pages
        ),
        disable_web_page_preview=True
    )


@router.callback_query(F.data.startswith("detpage:"))
async def change_det_page(cb: CallbackQuery):
    _, contrato, estado, ambito, page = cb.data.split(":")
    page = int(page)

    data = await load_contracts(contrato, estado)
    items = data.get("items", [])

    items = apply_filters(items, contrato, estado, ambito)
    entities = group_and_sort(items)

    await render_page(
        cb,
        kind=contrato,
        mode=estado,
        entities=entities,
        page=page,
        page_size=2,
        ambito=ambito  # lo ajustamos abajo
    )

# =========================
# RENDER DETALLE
# =========================
async def render_page(cb, kind, mode, entities, page, page_size=2, ambito=None):
    is_callback = hasattr(cb, "message")
    message = cb.message if is_callback else cb

    total_pages = (len(entities) + page_size - 1) // page_size
    if total_pages <= 0:
        total_pages = 1

    if page < 0:
        page = 0
    elif page >= total_pages:
        page = total_pages - 1

    block = entities[page*page_size:(page+1)*page_size]

    lines = []
    counter = 1 + page * page_size

    for entity, items in block:
        lines.append(f"__**{entity.upper()}**__\n")

        for it in items:
            url = get_notice_url(it)
            link = f"🔗 {url}" if url else "🔗 —"

            lines.append(
                f"{counter}️⃣ {it.get('object','(Sin título)')}\n"
                f"⏱️ DESDE: {fmt_date(it.get('firstPublicationDate'))}\n"
                f"⏰🖊 HASTA: {fmt_date(it.get('deadlineDate'))}\n"
                f"💰 {fmt_money(it.get('budgetWithoutVAT'))}\n"
                f"{link}\n"
            )
            counter += 1

    # ✅ construir el texto UNA SOLA VEZ
    text = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        + f"🔍 **DETALLE — {kind} — {mode}**\n"
        + "━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lines)
        + f"\n\n📄 _Página {page+1}/{total_pages}_"
    )

    if is_callback:
        await safe_edit(
            message,
            text,
            parse_mode="Markdown",
            reply_markup=kb_detalle_nav(kind, mode, ambito, page, total_pages),
            disable_web_page_preview=True
        )
        await cb.answer()
    else:
        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=kb_detalle_nav(kind, mode, ambito, page, total_pages),
            disable_web_page_preview=True
        )

@router.message(F.text == "/chatid")
async def show_chat_id(msg: Message):
    await msg.answer(
        f"CHAT_ID = {msg.chat.id}",
        parse_mode=None
    )
RUNNING_NOVEDADES = set()

@router.message(F.text == "/novedades")
async def novedades_cmd(msg: Message):
    if msg.chat.id in RUNNING_NOVEDADES:
        return

    RUNNING_NOVEDADES.add(msg.chat.id)
    try:
        await msg.answer("🔎 Buscando novedades de hoy...")

        items = await get_open_contracts_today()

        if not items:
            await msg.answer("ℹ️ Hoy no hay nuevas licitaciones abiertas.")
            return

        grouped = {}
        for it in items:
            ent = (it.get("entity") or {}).get("name", "OTROS")
            grouped.setdefault(ent, []).append(it)

        entities = sorted(grouped.items(), key=lambda x: x[0])

        # reutiliza tu render con flechas
        await render_page(
            cb=msg,
            kind="OBR",
            mode="ABI",
            entities=entities,
            page=0,
            page_size=2
        )

    finally:
        RUNNING_NOVEDADES.discard(msg.chat.id)
