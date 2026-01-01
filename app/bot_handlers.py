from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy.orm import Session
from datetime import datetime
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
from datetime import datetime, timedelta

BIG_AMOUNT = 1_000_000
ALERT_DAYS = 7


def build_summary(entities):
    today = datetime.utcnow().date()

    lines = [
        "🟢🟢🟢 **ABIERTAS — RESUMEN RÁPIDO** 🟢🟢🟢\n"
    ]

    for entity, items in entities:
        items_sorted = sorted(
            items,
            key=lambda x: x.get("deadlineDate") or "9999-12-31"
        )

        total_entity = 0.0
        lines.append(f"🏛️ __**{entity.upper()}**__")

        for it in items_sorted:
            published = fmt_date(it.get("firstPublicationDate"))
            deadline_raw = it.get("deadlineDate")
            deadline = fmt_date(deadline_raw)

            amount = it.get("budgetWithoutVAT")
            money = fmt_money(amount)

            if amount:
                total_entity += amount

            alert = ""
            if deadline_raw:
                try:
                    d = datetime.fromisoformat(deadline_raw[:10]).date()
                    if (d - today).days <= ALERT_DAYS:
                        alert = " 🔴🔔"
                except:
                    pass

            money_icon = "💎💰" if amount and amount >= BIG_AMOUNT else "💰"

            lines.append(
                f"📅 {published}  ⏰ {deadline}{alert}\n"
                f"{money_icon} {money}"
            )

        lines.append(f"📊 **Total entidad:** {fmt_money(total_entity)}\n")

    lines.append("━━━━━━━━━━━━━━━━━━\n👇 **DETALLE DE ANUNCIOS** 👇\n")

    return "\n".join(lines)


# =========================
# TECLADOS
# =========================
def kb_start():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏗️ OBRAS", callback_data="pick:OBRAS")
    kb.button(text="🧠 ING (Servicios)", callback_data="pick:ING")
    kb.button(text="🔁 RESET", callback_data="reset")
    kb.adjust(2, 1)
    return kb.as_markup()


def kb_mode(kind: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="🟢 ABIERTAS", callback_data=f"mode:{kind}:OPEN")
    kb.button(text="🔴 CERRADAS", callback_data=f"mode:{kind}:CLOSED")
    kb.button(text="🏠 INICIO", callback_data="home")
    kb.button(text="🔁 RESET", callback_data="reset")
    kb.adjust(2, 2)
    return kb.as_markup()


def kb_pages(kind, page, total_pages):
    kb = InlineKeyboardBuilder()
    if page > 0:
        kb.button(text="⬅️", callback_data=f"page:{kind}:{page-1}")
    if page < total_pages - 1:
        kb.button(text="➡️", callback_data=f"page:{kind}:{page+1}")
    kb.button(text="🏠 INICIO", callback_data="home")
    kb.adjust(2, 1)
    return kb.as_markup()


# =========================
# START
# =========================
@router.message(F.text == "/start")
async def start_cmd(msg: Message):
    await msg.answer(
        "👋 ¡Hola! Bot de licitaciones Euskadi.\n\nElige:",
        reply_markup=kb_start()
    )


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
        f"Has elegido **{kind}**.\n\nSelecciona modo:",
        reply_markup=kb_mode(kind),
        parse_mode="Markdown"
    )
    await cb.answer()


# =========================
# ABIERTAS → API DIRECTA
# =========================
@router.callback_query(F.data.startswith("mode:"))
async def show_mode(cb: CallbackQuery):
    _, kind, mode = cb.data.split(":")
    if mode != "OPEN":
        return

    contract_type_id = 1 if kind == "OBRAS" else 2
    cache_key = f"open:{contract_type_id}"
    data = get_cache(cache_key)

    if not data:
        url = (
            "https://api.euskadi.eus/procurements/contracting-notices"
            f"?contract-type-id={contract_type_id}"
            "&contract-procedure-status-id=3"
            "&itemsOfPage=50"
            "&lang=SPANISH"
        )
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            data = r.json()
        set_cache(cache_key, data)

    items = data.get("items", [])

    # Agrupar por entidad
    grouped = {}
    for it in items:
        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    entities = sorted(grouped.items(), key=lambda x: x[0])
    await render_page(cb, kind, entities, page=0)


# =========================
# RENDER + PAGINACIÓN
# =========================
async def render_page(cb, kind, entities, page, page_size=3):
    total_pages = (len(entities) + page_size - 1) // page_size
    block = entities[page*page_size:(page+1)*page_size]

    lines = []
    counter = 1 + page * 50

    for entity, items in block:
        lines.append(f"__**{entity.upper()}**__\n")

        for it in items:
            lines.append(
                f"{counter}️⃣ {it.get('object','(Sin título)')}\n"
                f"📅 Publicado: {fmt_date(it.get('firstPublicationDate'))}\n"
                f"⏰ Límite: {fmt_date(it.get('deadlineDate'))}\n"
                f"💶 {fmt_money(it.get('budgetWithoutVAT'))}\n"
                f"🔗 {it.get('mainEntityOfPage','—')}\n"
            )
            counter += 1

    text = (
        f"🟢 **{kind} ABIERTAS**\n"
        f"📄 Página {page+1}/{total_pages}\n\n"
        + "\n".join(lines)
    )

    await safe_edit(
        cb.message,
        text,
        parse_mode="Markdown",
        reply_markup=kb_pages(kind, page, total_pages),
        disable_web_page_preview=True
    )
    await cb.answer()


# =========================
# FLECHAS ⬅️ ➡️
# =========================
@router.callback_query(F.data.startswith("page:"))
async def change_page(cb: CallbackQuery):
    _, kind, page = cb.data.split(":")
    page = int(page)

    contract_type_id = 1 if kind == "OBRAS" else 2
    data = get_cache(f"open:{contract_type_id}")

    if not data:
        await cb.answer("Recarga ABIERTAS", show_alert=True)
        return

    items = data["items"]
    grouped = {}
    for it in items:
        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    entities = sorted(grouped.items(), key=lambda x: x[0])
    await render_page(cb, kind, entities, page)
