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
from bot.database.models import Order, OrderStatus, User
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

async def hold_and_dispute_sla_worker(session_pool, bot: Bot) -> None:
    """
    Фоновый воркер:
    1. Авто-закрытие заказов после 24ч холда (если не открыт диспут).
    2. Kraken SLA авто-резолв диспутов при отсутствии ответа одной из сторон более 24 часов:
       - Если клиент молчит 24ч -> закрытие в пользу магазина.
       - Если магазин молчит 24ч -> авто-возврат средств покупателю.
    """
    while True:
        try:
            await asyncio.sleep(60)
            async with session_pool() as session:
                now = datetime.utcnow()

                # 1. Авто-закрытие заказов после 24h холда
                stmt_hold = select(Order).where(Order.status == OrderStatus.HOLD, Order.hold_until <= now)
                res_hold = await session.execute(stmt_hold)
                expired_holds = list(res_hold.scalars().all())
                for order in expired_holds:
                    order.status = OrderStatus.COMPLETED
                    order.resolved_at = now
                if expired_holds:
                    await session.commit()
                    logger.info(f"Released 24h hold for {len(expired_holds)} completed orders.")

                # 2. Проверка 24h SLA таймеров открытых диспутов
                stmt_disp = select(Order).where(
                    Order.status == OrderStatus.DISPUTED,
                    Order.dispute_deadline.is_not(None),
                    Order.dispute_deadline <= now
                )
                res_disp = await session.execute(stmt_disp)
                expired_disputes = list(res_disp.scalars().all())

                for disp in expired_disputes:
                    user = await session.scalar(select(User).where(User.tg_id == disp.user_id))
                    
                    if disp.last_reply_by == "admin":
                        # Клиент не ответил за 24 часа -> закрытие в пользу магазина
                        disp.status = OrderStatus.COMPLETED
                        disp.resolved_at = now
                        await session.commit()
                        logger.info(f"Dispute {disp.order_code} auto-closed in store's favor (client SLA expired).")
                        try:
                            await bot.send_message(
                                disp.user_id,
                                f"⌛️ <b>Диспут {disp.order_code} закрыт.</b>\n\n"
                                f"Вы не предоставили ответ в течение 24 часов. "
                                f"Регламентное время истекло, спор закрыт в пользу магазина."
                            )
                            await bot.send_message(
                                config.admin_id,
                                f"⌛️ <b>Диспут {disp.order_code} автоматически закрыт.</b>\n"
                                f"Покупатель не ответил в течение 24 часов."
                            )
                        except Exception:
                            pass

                    elif disp.last_reply_by == "client":
                        # Магазин не ответил за 24 часа -> авто-возврат покупателю
                        disp.status = OrderStatus.REFUNDED
                        disp.resolved_at = now
                        if user:
                            user.balance = float(user.balance) + float(disp.amount)
                        await session.commit()
                        logger.info(f"Dispute {disp.order_code} auto-refunded to buyer (store SLA expired).")
                        try:
                            await bot.send_message(
                                disp.user_id,
                                f"🎉 <b>Диспут {disp.order_code} решен в вашу пользу!</b>\n\n"
                                f"Магазин не предоставил ответ в течение 24 часов. "
                                f"Средства в размере <b>${disp.amount:.2f} USDT</b> автоматически возвращены на ваш баланс!"
                            )
                            await bot.send_message(
                                config.admin_id,
                                f"⚠️ <b>Внимание: Диспут {disp.order_code} автоматически одобрен!</b>\n"
                                f"Сумма ${disp.amount:.2f} возвращена покупателю из-за отсутствия ответа поддержки в течение 24 часов."
                            )
                        except Exception:
                            pass

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in hold_and_dispute_sla_worker: {e}")

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

    # Фоновые задачи: разморозка 24ч холда, SLA авто-резолв диспутов и авто-синхронизация тиров
    hold_task = asyncio.create_task(hold_and_dispute_sla_worker(async_session, bot))
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
