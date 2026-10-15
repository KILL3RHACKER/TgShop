import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage

from bot.config import config
from bot.database.base import init_db, async_session
from bot.database.seed import seed_data
from bot.middlewares.db import DbSessionMiddleware
from bot.handlers.common import common_router
from bot.handlers.catalog import catalog_router
from bot.handlers.profile import profile_router
from bot.handlers.topup import topup_router
from bot.handlers.admin import admin_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def main() -> None:
    # Инициализация БД и сид данных
    await init_db()
    async with async_session() as session:
        await seed_data(session)

    # Хранилище FSM в Redis
    storage = RedisStorage.from_url(config.redis_url)

    bot = Bot(
        token=config.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=storage)

    # Middleware сессий БД
    dp.update.middleware(DbSessionMiddleware(async_session))

    # Регистрация роутеров
    dp.include_routers(
        common_router,
        catalog_router,
        profile_router,
        topup_router,
        admin_router
    )

    logger.info("Bot starting...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    asyncio.run(main())
