from aiogram.exceptions import TelegramBadRequest
import time
from datetime import datetime
import httpx

async def safe_edit(message, text: str, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from .models import Notice
from .updater import get_meta

router = Router()

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

@router.message(F.text == "/start")
async def start_cmd(msg: Message):
    await msg.answer(
        "👋 ¡Hola! Soy tu bot de licitaciones.\n\n"
        "Elige qué quieres ver:",
        reply_markup=kb_start()
    )

@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    await safe_edit(cb.message, "🏠 Menú principal:", reply_markup=kb_start())
    await cb.answer()

@router.callback_query(F.data == "reset")
async def reset(cb: CallbackQuery):
    await safe_edit(cb.message, "✅ Reset hecho. Volvemos al inicio:", reply_markup=kb_start())
    await cb.answer()

@router.callback_query(F.data.startswith("pick:"))
async def pick_kind(cb: CallbackQuery, db: Session):
    kind = cb.data.split(":")[1]
    await cb.message.edit_text(
        f"Perfecto 😄 Has elegido: **{kind}**\n\nAhora elige el modo:",
        reply_markup=kb_mode(kind),
        parse_mode="Markdown"
    )
    await cb.answer()

def format_money(x):
    if x is None:
        return "—"
    try:
        return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return str(x)
# =========================
# CACHE SIMPLE EN MEMORIA
# =========================
CACHE = {}
CACHE_TTL = 120  # segundos


def get_cache(key):
    item = CACHE.get(key)
    if not item:
        return None
    ts, data = item
    if time.time() - ts > CACHE_TTL:
        CACHE.pop(key, None)
        return None
    return data


def set_cache(key, data):
    CACHE[key] = (time.time(), data)


# =========================
# FORMATEADORES
# =========================
def fmt_date(x):
    if not x:
        return "—"
    try:
        return datetime.fromisoformat(x[:10]).strftime("%d/%m/%Y")
    except Exception:
        return x


def fmt_money(x):
    if x is None:
        return "—"
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

def kb_pagination(kind, mode, page, total_pages):
    kb = InlineKeyboardBuilder()

    if page > 0:
        kb.button(text="⬅️", callback_data=f"page:{kind}:{mode}:{page-1}")

    if page < total_pages - 1:
        kb.button(text="➡️", callback_data=f"page:{kind}:{mode}:{page+1}")

    kb.button(text="🏠 INICIO", callback_data="home")
    kb.adjust(2, 1)
    return kb.as_markup()


@router.callback_query(F.data.startswith("mode:"))
async def show_mode(cb: CallbackQuery, db: Session):
    _, kind, mode = cb.data.split(":")
    contract_type_id = 1 if kind == "OBRAS" else 2

    # =========================
    # ABIERTAS → API + CACHE
    # =========================
    if mode == "OPEN":
        cache_key = f"open:{contract_type_id}"
        data = get_cache(cache_key)

        if not data:
            url = (
                "https://api.euskadi.eus/procurements/contracting-notices"
                f"?contract-type-id={contract_type_id}"
                "&contract-procedure-status-id=3"
                "&orderBy=lastPublicationDate"
                "&orderType=DESC"
                "&currentPage=1"
                "&itemsOfPage=30"
                "&lang=SPANISH"
            )

            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url)
                data = r.json()

            set_cache(cache_key, data)

        items = data.get("items", [])
        page_size = 5
        page = 0

        await render_open_page(cb, kind, items, page, page_size)
        return

    # =========================
    # CERRADAS → BD (SIN TOCAR)
    # =========================
    last_update = get_meta(db, "last_update_human", "—")

    q = db.query(Notice).filter(
        Notice.contract_type_id == contract_type_id,
        Notice.procedure_status_id != 3
    ).order_by(Notice.last_publication_date.desc())

    analyzed = q.count()
    notices = q.limit(50).all()

    results = []
    for n in notices:
        best = None
        for c in n.contracts:
            if c.award_amount_without_vat is not None:
                best = c
                break
        if not best:
            continue

        budget = n.budget_without_vat
        award = best.award_amount_without_vat
        baja = "—"
        if budget and award is not None and budget > 0:
            baja = f"{((budget - award) / budget * 100):.2f}%".replace(".", ",")

        results.append(
            f"🏷️ **{n.object or '(Sin título)'}**\n"
            f"🏛️ {n.contracting_authority_name or '—'}\n"
            f"📅 `{n.last_publication_date or '—'}`\n"
            f"💶 {fmt_money(budget)} → {fmt_money(award)}\n"
            f"📉 Baja: **{baja}**\n"
            f"🔗 {n.main_entity_of_page or '—'}"
        )

        if len(results) >= 10:
            break

    text = (
        f"🔴 **{kind} CERRADAS**\n"
        f"🕒 Última actualización BD: `{last_update}`\n\n"
        + ("\n\n———\n\n".join(results) if results else "No hay resultados.")
    )

    await safe_edit(cb.message, text, parse_mode="Markdown", reply_markup=kb_mode(kind))
    await cb.answer()

async def render_open_page(cb, kind, items, page, page_size):
    total = len(items)
    total_pages = (total + page_size - 1) // page_size

    start = page * page_size
    end = start + page_size
    page_items = items[start:end]

    lines = []
    for idx, it in enumerate(page_items, start=start + 1):
        title = it.get("object") or "(Sin título)"
        org = it.get("contractingAuthority", {}).get("name", "—")
        created = fmt_date(it.get("firstPublicationDate"))
        deadline = fmt_date(it.get("tenderSubmissionDeadline"))
        budget = fmt_money(it.get("budget", {}).get("amountWithoutVat"))
        url = it.get("mainEntityOfPage") or "—"

        lines.append(
            f"{idx}️⃣ **{title}**\n"
            f"🏛️ {org}\n"
            f"🆕 Publicado: **{created}**\n"
            f"⏰ Límite: **{deadline}**\n"
            f"💶 Presupuesto: **{budget}**\n"
            f"🔗 {url}"
        )

    text = (
        f"🟢 **{kind} ABIERTAS**\n"
        f"📌 Total: **{total}** | Página **{page + 1}/{total_pages}**\n\n"
        + "\n\n———\n\n".join(lines)
    )

    await safe_edit(
        cb.message,
        text,
        parse_mode="Markdown",
        reply_markup=kb_pagination(kind, "OPEN", page, total_pages),
        disable_web_page_preview=True
    )
    await cb.answer()
