import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy import select

from bot.config import config
from bot.database.base import init_db, async_session
from bot.database.seed import seed_data
from bot.database.models import Order, OrderStatus
from bot.middlewares.db import DbSessionMiddleware
from bot.handlers.common import common_router
from bot.handlers.catalog import catalog_router
from bot.handlers.profile import profile_router
from bot.handlers.topup import topup_router
from bot.handlers.admin import admin_router
from bot.handlers.disputes import disputes_router
from web.services import sync_all_accounts_age_tiers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def hold_release_worker(session_pool) -> None:
    """Фоновый воркер: авто-разморозка заказов, у которых истек 24-часовой холд без открытия спора."""
    while True:
        try:
            await asyncio.sleep(60)
            async with session_pool() as session:
                now = datetime.utcnow()
                stmt = select(Order).where(Order.status == OrderStatus.HOLD, Order.hold_until <= now)
                res = await session.execute(stmt)
                expired_orders = list(res.scalars().all())
                for order in expired_orders:
                    order.status = OrderStatus.COMPLETED
                    order.resolved_at = now
                if expired_orders:
                    await session.commit()
                    logger.info(f"Released 24h hold for {len(expired_orders)} completed orders.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in hold_release_worker: {e}")

async def age_tier_sync_worker(session_pool) -> None:
    """Фоновый воркер: авто-сортировка и перевод аккаунтов в старшие тиры (15+, 30+) по мере взросления."""
    while True:
        try:
            async with session_pool() as session:
                migrated = await sync_all_accounts_age_tiers(session)
                if migrated > 0:
                    logger.info(f"Age tier sync: {migrated} accounts migrated to older tier.")
            await asyncio.sleep(3600)  # Check every hour
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in age_tier_sync_worker: {e}")
            await asyncio.sleep(60)

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
        admin_router,
        disputes_router
    )

    # Фоновые задачи: разморозка 24ч холда и авто-синхронизация тиров отлёжки
    hold_task = asyncio.create_task(hold_release_worker(async_session))
    age_sync_task = asyncio.create_task(age_tier_sync_worker(async_session))

    logger.info("Bot starting...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        hold_task.cancel()
        age_sync_task.cancel()
        await bot.session.close()
        logger.info("Bot stopped.")

if __name__ == "__main__":
    asyncio.run(main())
