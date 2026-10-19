from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from bot.database.models import User

BAN_REJECT_MESSAGE = "⛔️ Ваш аккаунт заблокирован службой безопасности за нарушение регламента площадки."

async def _reject_banned(event: TelegramObject) -> None:
    try:
        if isinstance(event, Update):
            if event.callback_query:
                await event.callback_query.answer(BAN_REJECT_MESSAGE, show_alert=True)
            elif event.message:
                await event.message.answer(BAN_REJECT_MESSAGE)
        elif isinstance(event, CallbackQuery):
            await event.answer(BAN_REJECT_MESSAGE, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(BAN_REJECT_MESSAGE)
    except Exception:
        pass

class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_pool: async_sessionmaker[AsyncSession]):
        super().__init__()
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with self.session_pool() as session:
            data["session"] = session

            event_from_user = data.get("event_from_user")
            if event_from_user:
                user = await session.scalar(select(User).where(User.tg_id == event_from_user.id))
                if user and user.is_banned:
                    await _reject_banned(event)
                    return

            return await handler(event, data)

class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        event_from_user = data.get("event_from_user")
        if session and event_from_user:
            user = await session.scalar(select(User).where(User.tg_id == event_from_user.id))
            if user and user.is_banned:
                await _reject_banned(event)
                return
        return await handler(event, data)
