from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from sqlalchemy.orm import Session
from .db import SessionLocal

class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        db: Session = SessionLocal()
        try:
            data["db"] = db  # <-- esto permite usar "db" en tus handlers
            return await handler(event, data)
        finally:
            db.close()
