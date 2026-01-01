from typing import Callable, Awaitable, Dict, Any
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from sqlalchemy.orm import Session

from .database import SessionLocal


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        db: Session = SessionLocal()
        try:
            data["db"] = db
            return await handler(event, data)
        finally:
            db.close()
