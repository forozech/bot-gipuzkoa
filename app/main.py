import asyncio
import logging
import os

from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.types import Update

from .bot_handlers import router, setup_scheduler
from .middlewares import DBSessionMiddleware


import asyncio
from fastapi import FastAPI
import uvicorn

health_app = FastAPI()

@health_app.get("/health")
async def health():
    return {"status": "ok"}

async def start_health_server():
    config = uvicorn.Config(
        health_app,
        host="0.0.0.0",
        port=8000,
        log_level="warning"
    )
    server = uvicorn.Server(config)
    await server.serve()

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
        parse_mode=None
    )
)

dp = Dispatcher()
dp.include_router(router)

# middleware DB (SIN parámetros)
dp.update.middleware(DBSessionMiddleware())


# =========================
# WEBHOOK CONFIG
# =========================
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://bot-gipuzkoa.onrender.com{WEBHOOK_PATH}"


@app.post(WEBHOOK_PATH)
async def telegram_webhook(update: dict):
    telegram_update = Update.model_validate(update)
    await dp.feed_update(bot, telegram_update)
    return {"ok": True}
    
@app.head("/")
async def root_head():
    return {}

# =========================
# STARTUP / SHUTDOWN
# =========================
@app.on_event("startup")
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL)

    setup_scheduler(bot)

    logging.info("🚀 Bot iniciado con webhook")
    logging.info("⏰ Avisos automáticos activos (11:00 y 17:00)")


@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
    logging.info("🛑 Bot detenido")

async def start_health_server():
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="warning"
    )
    server = uvicorn.Server(config)
    await server.serve()

