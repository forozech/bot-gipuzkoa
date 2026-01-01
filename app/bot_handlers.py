from aiogram.exceptions import TelegramBadRequest

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

@router.callback_query(F.data.startswith("mode:"))
async def show_mode(cb: CallbackQuery, db: Session):
    _, kind, mode = cb.data.split(":")
    last_update = get_meta(db, "last_update_human", "—")

    contract_type_id = 1 if kind == "OBRAS" else 2

    # =========================
    # ABIERTAS (OBRAS o ING) → API DIRECTA
    # =========================
    if mode == "OPEN":
        if contract_type_id == 1:  # OBRAS
            url = (
                "https://api.euskadi.eus/procurements/contracting-notices"
                "?contract-type-id=1"
                "&contract-procedure-status-id=3"
                "&orderBy=lastPublicationDate"
                "&orderType=DESC"
                "&currentPage=1"
                "&itemsOfPage=50"
                "&lang=SPANISH"
            )
        else:  # ING
            url = (
                "https://api.euskadi.eus/procurements/contracting-notices"
                "?contract-type-id=2"
                "&contract-procedure-status-id=3"
                "&orderBy=lastPublicationDate"
                "&orderType=DESC"
                "&currentPage=1"
                "&itemsOfPage=50"
                "&lang=SPANISH"
            )

        import httpx
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            data = r.json()

        items = data.get("items", [])
        analyzed = len(items)
        items = items[:10]
        cumplen = len(items)

        lines = []
        for it in items:
            title = it.get("object") or "(Sin título)"
            org = it.get("contractingAuthority", {}).get("name", "—")
            last_date = it.get("lastPublicationDate") or it.get("firstPublicationDate") or "—"
            url_item = it.get("mainEntityOfPage") or "—"

            lines.append(
                f"🏷️ **{title}**\n"
                f"🏛️ {org}\n"
                f"📅 `{last_date}`\n"
                f"🔗 {url_item}"
            )

        text = (
            f"🟢 **{kind} ABIERTAS**\n"
            f"📌 Anuncios encontrados: **{analyzed}** | Mostrando: **{cumplen}**\n\n"
            + ("\n\n———\n\n".join(lines) if lines else "No hay resultados en este momento.")
        )

        await cb.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=kb_mode(kind),
            disable_web_page_preview=True
        )
        await cb.answer()
        return

    # =========================
    # CERRADAS (OBRAS o ING) → BD
    # =========================
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
            baja_pct = (budget - award) / budget * 100.0
            baja = f"{baja_pct:.2f}%".replace(".", ",")

        last_date = n.last_publication_date or n.first_publication_date or "—"
        plazo = best.months_contract_duration
        plazo_txt = f"{plazo} meses" if plazo is not None else "—"

        results.append(
            f"🏷️ **{n.object or '(Sin título)'}**\n"
            f"🏛️ {n.contracting_authority_name or '—'}\n"
            f"📅 `{last_date}`\n"
            f"⏳ Plazo: **{plazo_txt}**\n"
            f"💶 Inicial s/IVA: **{format_money(budget)}**\n"
            f"✅ Contrato s/IVA: **{format_money(award)}**\n"
            f"📉 Baja: **{baja}**\n"
            f"🔗 {n.main_entity_of_page or best.main_entity_of_page or '—'}"
        )

        if len(results) >= 10:
            break

    text = (
        f"🔴 **{kind} CERRADAS**\n"
        f"🕒 Última actualización BD: `{last_update}`\n"
        f"📌 Anuncios analizados: **{analyzed}** | Cumplen filtro: **{len(results)}**\n\n"
        + ("\n\n———\n\n".join(results) if results else "No he encontrado cerradas con datos completos.")
    )

    await cb.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_mode(kind))
    await cb.answer()
