import asyncio
import logging
import os

from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from .bot_handlers import router
from .middlewares import DBSessionMiddleware


# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO)


# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="Bot Gipuzkoa")


@app.get("/")
async def root():
    """
    Endpoint obligatorio para Render / UptimeRobot
    """
    return {
        "status": "ok",
        "service": "bot-gipuzkoa",
        "bot": "running"
    }


# =========================
# TELEGRAM BOT
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN no definido")

from aiogram.client.default import DefaultBotProperties

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.MARKDOWN
    )
)


dp = Dispatcher()
dp.include_router(router)

# middleware DB (SIN parámetros)
dp.update.middleware(DBSessionMiddleware())


# =========================
# STARTUP / SHUTDOWN
# =========================
@app.on_event("startup")
async def on_startup():
    asyncio.create_task(start_bot())
    logging.info("🚀 Bot iniciado")


async def start_bot():
    try:
        await dp.start_polling(bot)
    except Exception:
        logging.exception("Error en polling del bot")


@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
    logging.info("🛑 Bot detenido")
