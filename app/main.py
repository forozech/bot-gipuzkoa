import asyncio
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .db import engine, SessionLocal
from .models import Base
from .updater import refresh_all
from .bot_handlers import router

app = FastAPI()

Base.metadata.create_all(bind=engine)

bot = Bot(token=settings.TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)

scheduler = AsyncIOScheduler(timezone=settings.TZ)

@app.get("/health")
def health():
    return {"ok": True}

async def scheduled_refresh():
    db = SessionLocal()
    try:
        await refresh_all(db)
    finally:
        db.close()

@app.on_event("startup")
async def startup():
    scheduler.add_job(scheduled_refresh, CronTrigger(hour=11, minute=0))
    scheduler.add_job(scheduled_refresh, CronTrigger(hour=17, minute=0))
    scheduler.start()
    asyncio.create_task(dp.start_polling(bot))
