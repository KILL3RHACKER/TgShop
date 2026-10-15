from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot

from bot.database.base import async_session
from bot.config import config

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session

_bot: Bot | None = None

def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=config.bot_token.get_secret_value())
    return _bot

async def close_bot() -> None:
    global _bot
    if _bot is not None:
        try:
            await _bot.session.close()
        except Exception:
            pass
        _bot = None
