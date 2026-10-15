import logging
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import config
from bot.database.models import TopUpTicket, TicketStatus, User

logger = logging.getLogger(__name__)

async def approve_ticket(session: AsyncSession, ticket_id: int, bot: Bot | None = None) -> tuple[bool, str]:
    ticket = await session.get(TopUpTicket, ticket_id)
    if not ticket or ticket.status != TicketStatus.PENDING:
        return False, f"Тикет #{ticket_id} уже обработан или не найден."

    user = await session.scalar(select(User).where(User.tg_id == ticket.user_id))
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

    if bot:
        try:
            await bot.send_message(
                ticket.user_id,
                f"✅ <b>Ваш баланс успешно пополнен на ${ticket.amount:.2f} USDT!</b>"
            )
        except Exception as e:
            logger.warning("Failed to notify user %s: %s", ticket.user_id, e)

    return True, f"Тикет #{ticket_id} успешно подтвержден. Баланс пополнен на ${ticket.amount:.2f}."

async def reject_ticket(session: AsyncSession, ticket_id: int, bot: Bot | None = None) -> tuple[bool, str]:
    ticket = await session.get(TopUpTicket, ticket_id)
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
