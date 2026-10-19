import logging
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import config
from bot.database.models import TopUpTicket, TicketStatus, User

logger = logging.getLogger(__name__)

async def approve_ticket(session: AsyncSession, ticket_id: int, bot: Bot | None = None) -> tuple[bool, str]:
    stmt = select(TopUpTicket).where(TopUpTicket.id == ticket_id).with_for_update()
    ticket = (await session.execute(stmt)).scalar_one_or_none()
    if not ticket or ticket.status != TicketStatus.PENDING:
        return False, f"Тикет #{ticket_id} уже обработан или не найден."

    user_stmt = select(User).where(User.tg_id == ticket.user_id).with_for_update()
    user = (await session.execute(user_stmt)).scalar_one_or_none()
    if not user:
        return False, f"Пользователь с ID {ticket.user_id} не найден."

    ticket.status = TicketStatus.APPROVED
    user.balance = float(user.balance) + float(ticket.amount)

    # Referral bonus
    if user.referrer_id:
        referrer = await session.scalar(select(User).where(User.tg_id == user.referrer_id))
        if referrer:
            bonus = float(ticket.amount) * (config.referral_percent / 100.0)
            referrer.balance = float(referrer.balance) + bonus
            if bot:
                try:
                    await bot.send_message(
                        referrer.tg_id,
                        f"🎁 <b>Реферальное вознаграждение!</b>\n"
                        f"Вам начислено <b>${bonus:.2f}</b> ({config.referral_percent}%) от пополнения реферала."
                    )
                except Exception as e:
                    logger.warning("Failed to notify referrer %s: %s", referrer.tg_id, e)

    await session.commit()

    # Фоновый sweep средств с кошелька юзера на мастер-кошелек админа (если настроен)
    target_addr = config.admin_tron_address
    if target_addr and user.tron_address and user.tron_private_key:
        try:
            import asyncio
            from bot.services.tron import sweep_wallet
            asyncio.create_task(sweep_wallet(user.tron_address, user.tron_private_key, target_addr))
        except Exception as e:
            logger.warning(f"Failed to schedule background sweep: {e}")

    if bot:
        try:
            await bot.send_message(
                ticket.user_id,
                f"✅ <b>Ваш баланс успешно пополнен на ${ticket.amount:.2f} USDT!</b>"
            )
        except Exception as e:
            logger.warning("Failed to notify user %s: %s", ticket.user_id, e)

    return True, f"Тикет #{ticket_id} успешно подтвержден. Баланс пополнен на ${ticket.amount:.2f}."

async def credit_admin_balance(session: AsyncSession, amount: float) -> None:
    """
    Зачисляет сумму от завершенных заказов на внутренний баланс администратора.
    """
    if amount <= 0:
        return
    admin = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    if not admin:
        from bot.services.tron import generate_tron_wallet
        t_addr, t_key = generate_tron_wallet()
        admin = User(
            tg_id=config.admin_id,
            role="admin",
            rules_accepted=True,
            tron_address=t_addr,
            tron_private_key=t_key,
            balance=0.0
        )
        session.add(admin)
        await session.flush()
    admin.balance = float(admin.balance) + float(amount)

async def reject_ticket(session: AsyncSession, ticket_id: int, bot: Bot | None = None) -> tuple[bool, str]:
    stmt = select(TopUpTicket).where(TopUpTicket.id == ticket_id).with_for_update()
    ticket = (await session.execute(stmt)).scalar_one_or_none()
    if not ticket or ticket.status != TicketStatus.PENDING:
        return False, f"Тикет #{ticket_id} уже обработан или не найден."

    ticket.status = TicketStatus.REJECTED
    await session.commit()

    if bot:
        try:
            await bot.send_message(
                ticket.user_id,
                f"❌ <b>Ваш тикет на пополнение #{ticket.id} был отклонен администратором.</b>"
            )
        except Exception as e:
            logger.warning("Failed to notify user %s: %s", ticket.user_id, e)

    return True, f"Тикет #{ticket_id} отклонен."
