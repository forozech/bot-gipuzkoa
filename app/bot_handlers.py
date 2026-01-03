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


from sqlalchemy.orm import Session
import httpx
import time

from .models import Notice
from .updater import get_meta

router = Router()

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
CACHE_TTL = 300  # 5 minutos
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

BIG_AMOUNT = 1_000_000
ALERT_DAYS = 7

# =========================
# RESUMEN (SIN LÍMITES)
# =========================
def build_summary_page(entities, summary_page, summary_page_size=4):
    total_pages = (len(entities) + summary_page_size - 1) // summary_page_size

    block = entities[
        summary_page * summary_page_size :
        (summary_page + 1) * summary_page_size
    ]

    today = datetime.utcnow().date()

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        "🧾 **RESUMEN**",
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
        return  # automático silencioso si no hay novedades

    grouped = {}
    for it in items:
        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    lines = [
        "🆕 **NOVEDADES DE HOY (ABIERTAS)**",
        ""
    ]

    for ent, its in grouped.items():
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
# TECLADOS
# =========================

def setup_scheduler(bot):
    scheduler = AsyncIOScheduler(
        timezone=pytz.timezone("Europe/Madrid")
    )

    scheduler.add_job(
        check_new_open_contracts,
        CronTrigger(hour=11, minute=0),
        args=[bot],
        id="open_alert_11"
    )

    scheduler.add_job(
        check_new_open_contracts,
        CronTrigger(hour=17, minute=0),
        args=[bot],
        id="open_alert_17"
    )

    scheduler.add_job(
        check_open_contracts_today,
        CronTrigger(hour=11, minute=0),
        args=[bot],
        id="open_today_11"
    )

    scheduler.add_job(
        check_open_contracts_today,
        CronTrigger(hour=17, minute=0),
        args=[bot],
        id="open_today_17"
    )

    scheduler.start()

def kb_start():
    kb = InlineKeyboardBuilder()
    kb.button(text="👨‍🔧", callback_data="pick:OBRAS")
    kb.button(text="👩‍💻", callback_data="pick:ING")
    kb.button(text="🚀", callback_data="reset")
    kb.adjust(2, 1)
    return kb.as_markup()

def kb_mode(kind: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="⏱️", callback_data=f"mode:{kind}:OPEN")
    kb.button(text="⏰", callback_data=f"mode:{kind}:CLOSED")
    kb.button(text="🏫", callback_data="home")
    kb.button(text="🚀", callback_data="reset")
    kb.adjust(2, 2)
    return kb.as_markup()

def kb_view(kind: str, mode: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="📋", callback_data=f"view:{kind}:{mode}:SUMMARY")
    kb.button(text="🔍", callback_data=f"view:{kind}:{mode}:DETAIL")
    kb.button(text="🏫", callback_data="home")
    kb.adjust(2, 1)
    return kb.as_markup()

def kb_pages(kind: str, mode: str, page: int, total_pages: int):
    kb = InlineKeyboardBuilder()

    if page > 0:
        kb.button(text="◁", callback_data=f"page:{kind}:{mode}:{page-1}")

    if page < total_pages - 1:
        kb.button(text="▷", callback_data=f"page:{kind}:{mode}:{page+1}")

    kb.button(text="🚀", callback_data="home")
    kb.adjust(2, 1)
    return kb.as_markup()

def kb_summary_pages(kind, mode, page, total_pages):
    kb = InlineKeyboardBuilder()

    if page > 0:
        kb.button(text="◁", callback_data=f"summary:{kind}:{mode}:{page-1}")
    if page < total_pages - 1:
        kb.button(text="▷", callback_data=f"summary:{kind}:{mode}:{page+1}")

    kb.button(text="🔍", callback_data=f"view:{kind}:{mode}:DETAIL")
    kb.button(text="🏠", callback_data="home")
    kb.adjust(2, 1)

    return kb.as_markup()


# =========================
# START
# =========================
@router.message(F.text == "/start")
async def start_cmd(msg: Message):
    await msg.answer("🍀OFERTAS", reply_markup=kb_start())

@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    await safe_edit(cb.message, "🏠 Menú principal:", reply_markup=kb_start())
    await cb.answer()

@router.callback_query(F.data == "reset")
async def reset(cb: CallbackQuery):
    await safe_edit(cb.message, "✅ Reset hecho:", reply_markup=kb_start())
    await cb.answer()

@router.callback_query(F.data.startswith("pick:"))
async def pick_kind(cb: CallbackQuery):
    kind = cb.data.split(":")[1]
    await safe_edit(
        cb.message,
        f"👨‍🔧**{kind}**",
        reply_markup=kb_mode(kind),
        parse_mode="Markdown"
    )
    await cb.answer()

# =========================
# MODO
# =========================
@router.callback_query(F.data.startswith("mode:"))
async def show_mode(cb: CallbackQuery):
    _, kind, mode = cb.data.split(":")

    await safe_edit(
        cb.message,
        f"🔎 **{kind} · {mode}**\n\nElige vista:",
        reply_markup=kb_view(kind, mode),
        parse_mode="Markdown"
    )
    await cb.answer()


# =========================
# VISTAS
# =========================

@router.callback_query(F.data.startswith("summary:"))
async def change_summary_page(cb: CallbackQuery):
    _, kind, mode, page = cb.data.split(":")
    page = int(page)

    contract_type_id = 1 if kind == "OBRAS" else 2
    status_id = 3 if mode == "OPEN" else 4

    cache_key = f"{mode}:{contract_type_id}"
    data = get_cache(cache_key)

    if not data:
        await cb.answer("Cache caducada", show_alert=True)
        return

    grouped = {}
    for it in data.get("items", []):
        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    entities = sorted(grouped.items(), key=lambda x: x[0])

    text, total_pages = build_summary_page(
        entities,
        summary_page=page,
        summary_page_size=SUMMARY_PAGE_SIZE
    )

    await safe_edit(
        cb.message,
        f"📋 **RESUMEN {kind} · {mode}**\n\n{text}",
        parse_mode="Markdown",
        reply_markup=kb_summary_pages(kind, mode, page, total_pages),
        disable_web_page_preview=True
    )
    await cb.answer()

@router.callback_query(F.data.startswith("view:"))
async def show_view(cb: CallbackQuery):
    _, kind, mode, view = cb.data.split(":")

    contract_type_id = 1 if kind == "OBRAS" else 2
    status_id = 3 if mode == "OPEN" else 4

    cache_key = f"{mode}:{contract_type_id}"
    data = get_cache(cache_key)

    if not data:
        url = (
            "https://api.euskadi.eus/procurements/contracting-notices"
            f"?contract-type-id={contract_type_id}"
            f"&contract-procedure-status-id={status_id}"
            "&itemsOfPage=50"
            "&lang=SPANISH"
        )
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            data = r.json()
        set_cache(cache_key, data)

    items = data.get("items", [])
    grouped = {}

    for it in items:
        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    entities = sorted(grouped.items(), key=lambda x: x[0])

    # 📋 RESUMEN
    if view == "SUMMARY":
        text, total_pages = build_summary_page(
            entities,
            summary_page=0,
            summary_page_size=SUMMARY_PAGE_SIZE
        )

        await safe_edit(
            cb.message,
            f"📋 **RESUMEN {kind} · {mode}**\n\n{text}",
            parse_mode="Markdown",
            reply_markup=kb_summary_pages(kind, mode, 0, total_pages),
            disable_web_page_preview=True
        )
        await cb.answer()
        return

       
    # 🔍 DETALLE  ← ESTO FALTABA
    await render_page(cb, kind, mode, entities, page=0, page_size=2)


# =========================
# RENDER DETALLE
# =========================
async def render_page(cb, kind, mode, entities, page, page_size=2):
    total_pages = (len(entities) + page_size - 1) // page_size
    block = entities[page*page_size:(page+1)*page_size]

    lines = []
    counter = 1 + page * page_size

    for entity, items in block:
        lines.append(f"__**{entity.upper()}**__\n")

        for it in items:
            lines.append(
                f"{counter}️⃣ {it.get('object','(Sin título)')}\n"
                f"⏱️ DESDE: {fmt_date(it.get('firstPublicationDate'))}\n"
                f"⏰🖊 HASTA: {fmt_date(it.get('deadlineDate'))}\n"
                f"💰 {fmt_money(it.get('budgetWithoutVAT'))}\n"
                f"🔗 {it.get('mainEntityOfPage','—')}\n"
            )
            counter += 1

    text = (
        f"🔍 **DETALLE {kind} · {mode}**\n"
        f"📄 Pág. {page+1}/{total_pages}\n\n"
        + "\n".join(lines)
    )

    await safe_edit(
        cb.message,
        text,
        parse_mode="Markdown",
        reply_markup=kb_pages(kind, mode, page, total_pages),
        disable_web_page_preview=True
    )
    await cb.answer()

# =========================
# PAGINACIÓN
# =========================
@router.callback_query(F.data.startswith("page:"))
async def change_page(cb: CallbackQuery):
    _, kind, mode, page = cb.data.split(":")
    page = int(page)

    contract_type_id = 1 if kind == "OBRAS" else 2
    status_id = 3 if mode == "OPEN" else 4

    cache_key = f"{mode}:{contract_type_id}"
    data = get_cache(cache_key)

    if not data:
        await cb.answer("Recarga ABIERTAS", show_alert=True)
        return

    items = data["items"]
    grouped = {}

    for it in items:
        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    entities = sorted(grouped.items(), key=lambda x: x[0])

    # 👇 AQUÍ ESTABA EL ERROR
    await render_page(cb, kind, mode, entities, page, page_size=2)

from aiogram.types import Message

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
            kind="OBRAS",
            mode="OPEN",
            entities=entities,
            page=0,
            page_size=2
        )

    finally:
        RUNNING_NOVEDADES.discard(msg.chat.id)
