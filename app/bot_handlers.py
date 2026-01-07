from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from datetime import datetime, date
import pytz
import httpx
import time

router = Router()

# =========================================================
# CONFIG
# =========================================================

ALERT_CHAT_ID = -1003637338441
TIMEZONE = pytz.timezone("Europe/Madrid")

CACHE = {}
CACHE_TTL = 900  # 15 minutos

PAGE_SIZE = 2
SUMMARY_PAGE_SIZE = 4

# =========================================================
# KEYWORDS
# =========================================================

GIP_KEYWORDS = [
    "gipuzkoa","guipuzcoa","donostia","san sebastián","san sebastian",
    "irun","eibar","zarautz","tolosa","beasain","bergara","errenteria",
    "mondragón","arrasate","mutriku","ondarroa","azpeitia","azkoitia",
    "hondarribia","lezo","pasaia","oiartzun","urnieta","lasarte",
    "andoain","ordizia","legazpi","zumarraga","elgoibar",
    "txingudi","añarbe","anarbre","añarbe"
]

ING_KEYWORDS = [
    "ingeniería","ingenieria","ingeniería civil","arquitectura",
    "proyecto","dirección de obra","direccion de obra",
    "asistencia técnica","asistencia tecnica","redacción","redaccion"
]

# =========================================================
# HELPERS
# =========================================================

def fmt_date(d):
    if not d:
        return "—"
    return datetime.fromisoformat(d[:10]).strftime("%d/%m/%Y")

def fmt_money(x):
    if x is None:
        return "—"
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

async def safe_edit(message, text, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise

# =========================================================
# CACHE
# =========================================================

def get_cache(key):
    v = CACHE.get(key)
    if not v:
        return None
    if time.time() - v["ts"] > CACHE_TTL:
        CACHE.pop(key, None)
        return None
    return v

def set_cache(key, entities):
    CACHE[key] = {
        "entities": entities,
        "ts": time.time()
    }

# =========================================================
# API
# =========================================================

async def fetch_contracts(kind, mode):
    contract_type = 1 if kind == "OBRAS" else 2
    status = 3 if mode in ("OPEN", "PLAZO") else 4

    url = (
        "https://api.euskadi.eus/procurements/contracting-notices"
        f"?contract-type-id={contract_type}"
        f"&contract-procedure-status-id={status}"
        "&itemsOfPage=50&lang=SPANISH"
    )

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        return r.json().get("items", [])

# =========================================================
# FILTERS
# =========================================================

def apply_filters(items, kind, mode, scope):
    today = date.today()
    grouped = {}

    for it in items:
        title = (it.get("object") or "").lower()
        deadline = it.get("deadlineDate")

        if mode == "PLAZO" and deadline:
            if datetime.fromisoformat(deadline[:10]).date() < today:
                continue

        if scope == "GIP":
            if not any(k in title for k in GIP_KEYWORDS):
                continue

        if kind == "ING":
            if not any(k in title for k in ING_KEYWORDS):
                continue

        ent = (it.get("entity") or {}).get("name", "OTROS")
        grouped.setdefault(ent, []).append(it)

    entities = sorted(grouped.items(), key=lambda x: x[0])
    for _, items in entities:
        items.sort(key=lambda x: x.get("deadlineDate") or "9999-12-31")

    return entities

# =========================================================
# RENDER
# =========================================================

async def render_page(cb, kind, mode, scope, entities, page):
    is_cb = hasattr(cb, "message")
    msg = cb.message if is_cb else cb

    total_pages = (len(entities) + PAGE_SIZE - 1) // PAGE_SIZE
    block = entities[page*PAGE_SIZE:(page+1)*PAGE_SIZE]

    lines = [f"🔍 **DET — {kind} — {mode} — {scope}**\n"]

    for ent, items in block:
        lines.append(f"__**{ent.upper()}**__\n")
        for it in items:
            lines.append(
                f"• {it.get('object','(Sin título)')}\n"
                f"⏰ {fmt_date(it.get('deadlineDate'))} · 💰 {fmt_money(it.get('budgetWithoutVAT'))}\n"
            )

    lines.append(f"_Pág. {page+1}/{total_pages}_")

    kb = InlineKeyboardBuilder()
    if page > 0:
        kb.button(text="◁", callback_data=f"page:{kind}:{mode}:{scope}:{page-1}")
    if page < total_pages - 1:
        kb.button(text="▷", callback_data=f"page:{kind}:{mode}:{scope}:{page+1}")
    kb.button(text="🔄 RESET", callback_data="reset")
    kb.adjust(2, 1)

    if is_cb:
        await safe_edit(msg, "\n".join(lines), parse_mode="Markdown", reply_markup=kb.as_markup())
        await cb.answer()
    else:
        await msg.answer("\n".join(lines), parse_mode="Markdown", reply_markup=kb.as_markup())

# =========================================================
# KEYBOARDS
# =========================================================

def kb_start():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏗️ OBRAS", callback_data="pick:OBRAS")
    kb.button(text="🛠️ SERV", callback_data="pick:SERV")
    kb.button(text="🧠 ING", callback_data="pick:ING")
    kb.button(text="🔄 RESET", callback_data="reset")
    kb.adjust(2, 1)
    return kb.as_markup()

def kb_mode(kind):
    kb = InlineKeyboardBuilder()
    kb.button(text="📂 ABIERTAS", callback_data=f"mode:{kind}:OPEN")
    kb.button(text="⏳ EN PLAZO", callback_data=f"mode:{kind}:PLAZO")
    kb.button(text="🔒 CERRADAS", callback_data=f"mode:{kind}:CLOSED")
    kb.button(text="⬅️ ATRÁS", callback_data="home")
    kb.adjust(2, 2)
    return kb.as_markup()

def kb_scope(kind, mode):
    kb = InlineKeyboardBuilder()
    kb.button(text="🌍 GEN", callback_data=f"scope:{kind}:{mode}:GEN")
    kb.button(text="📍 GIP", callback_data=f"scope:{kind}:{mode}:GIP")
    kb.button(text="⬅️ ATRÁS", callback_data=f"pick:{kind}")
    kb.adjust(2, 1)
    return kb.as_markup()

def kb_view(kind, mode, scope):
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 RES", callback_data=f"view:{kind}:{mode}:{scope}:RES")
    kb.button(text="🔍 DET", callback_data=f"view:{kind}:{mode}:{scope}:DET")
    kb.button(text="⬅️ ATRÁS", callback_data=f"scope:{kind}:{mode}")
    kb.adjust(2, 1)
    return kb.as_markup()

# =========================================================
# HANDLERS
# =========================================================

@router.message(F.text == "/start")
async def start(msg: Message):
    await msg.answer("💼 CONTRATO", reply_markup=kb_start())

@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    await safe_edit(cb.message, "💼 CONTRATO", reply_markup=kb_start())
    await cb.answer()

@router.callback_query(F.data == "reset")
async def reset(cb: CallbackQuery):
    CACHE.clear()
    await safe_edit(cb.message, "✅ Sesión reiniciada", reply_markup=kb_start())
    await cb.answer()

@router.callback_query(F.data.startswith("pick:"))
async def pick(cb: CallbackQuery):
    kind = cb.data.split(":")[1]
    await safe_edit(cb.message, f"💼 {kind}", reply_markup=kb_mode(kind))
    await cb.answer()

@router.callback_query(F.data.startswith("mode:"))
async def mode(cb: CallbackQuery):
    _, kind, mode = cb.data.split(":")
    await safe_edit(cb.message, f"{kind} — {mode}", reply_markup=kb_scope(kind, mode))
    await cb.answer()

@router.callback_query(F.data.startswith("scope:"))
async def scope(cb: CallbackQuery):
    _, kind, mode, scope = cb.data.split(":")
    await safe_edit(cb.message, f"{kind} — {mode} — {scope}", reply_markup=kb_view(kind, mode, scope))
    await cb.answer()

@router.callback_query(F.data.startswith("view:"))
async def view(cb: CallbackQuery):
    _, kind, mode, scope, view = cb.data.split(":")

    key = f"{kind}:{mode}:{scope}"
    cached = get_cache(key)

    if cached:
        entities = cached["entities"]
    else:
        items = await fetch_contracts(kind, mode)
        entities = apply_filters(items, kind, mode, scope)
        set_cache(key, entities)

    if view == "DET":
        await render_page(cb, kind, mode, scope, entities, 0)
    else:
        lines = [f"📋 **RES — {kind} — {mode} — {scope}**\n"]
        for ent, items in entities[:SUMMARY_PAGE_SIZE]:
            total = sum(i.get("budgetWithoutVAT") or 0 for i in items)
            lines.append(f"🏛 {ent} · {fmt_money(total)}")
        lines.append("_Pág. 1/1_")
        await safe_edit(cb.message, "\n".join(lines), parse_mode="Markdown")
        await cb.answer()

@router.callback_query(F.data.startswith("page:"))
async def page(cb: CallbackQuery):
    _, kind, mode, scope, page = cb.data.split(":")
    page = int(page)

    key = f"{kind}:{mode}:{scope}"
    data = get_cache(key)
    if not data:
        await cb.answer("Cache caducada", show_alert=True)
        return

    await render_page(cb, kind, mode, scope, data["entities"], page)

# =========================================================
# SCHEDULER
# =========================================================

async def send_open_contracts_today_short(bot):
    items = await fetch_contracts("OBRAS", "OPEN")
    entities = apply_filters(items, "OBRAS", "OPEN", "GEN")

    if not entities:
        return

    lines = ["🆕 **NOVEDADES HOY — OBRAS**\n"]
    for ent, its in entities:
        amounts = [fmt_money(i.get("budgetWithoutVAT")) for i in its if i.get("budgetWithoutVAT")]
        lines.append(f"🏛 {ent}: {len(its)} · " + "; ".join(amounts))

    await bot.send_message(
        chat_id=ALERT_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown"
    )

def setup_scheduler(bot):
    sch = AsyncIOScheduler(timezone=TIMEZONE)
    sch.add_job(send_open_contracts_today_short, CronTrigger(hour=11, minute=0), args=[bot])
    sch.add_job(send_open_contracts_today_short, CronTrigger(hour=17, minute=0), args=[bot])
    sch.start()
