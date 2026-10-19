import asyncio
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bot.config import config
from bot.database.base import async_session
from bot.database.models import User, TopUpTicket, TicketStatus
from bot.keyboards.inline import get_ticket_admin_kb

topup_router = Router()

class TopUpStates(StatesGroup):
    amount = State()

async def expire_ticket_timer(ticket_id: int, bot: Bot):
    await asyncio.sleep(config.ticket_expire_minutes * 60)
    async with async_session() as session:
        ticket = await session.get(TopUpTicket, ticket_id)
        if ticket and ticket.status == TicketStatus.PENDING:
            ticket.status = TicketStatus.EXPIRED
            await session.commit()
            try:
                await bot.send_message(
                    ticket.user_id,
                    f"⌛ <b>Время ожидания оплаты по тикету #{ticket.id} истекло ({config.ticket_expire_minutes} мин).</b>\n"
                    f"Тикет автоматически закрыт. Если вы уже перевели средства, обратитесь в поддержку."
                )
            except Exception:
                pass

@topup_router.callback_query(F.data == "topup")
async def start_topup(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(TopUpStates.amount)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Отмена", callback_data="profile")
    
    await callback.message.edit_text(
        "💵 <b>Пополнение баланса</b>\n\n"
        "Введите желаемую сумму пополнения в USDT (например: <code>15.5</code>):",
        reply_markup=kb.as_markup()
    )

@topup_router.message(TopUpStates.amount)
async def process_topup_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot
):
    text_val = message.text.replace(",", ".").strip()
    try:
        amount = float(text_val)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите корректное положительное число (например: <code>20.0</code>):")
        return

    await state.clear()
    user = await session.scalar(select(User).where(User.tg_id == message.from_user.id))

    expires_at = datetime.utcnow() + timedelta(minutes=config.ticket_expire_minutes)
    ticket = TopUpTicket(
        user_id=user.tg_id,
        amount=amount,
        status=TicketStatus.PENDING,
        expires_at=expires_at
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    user_info = (
        f"🧾 <b>Тикет пополнения #{ticket.id} сформирован</b>\n\n"
        f"Сумма к оплате: <b>${amount:.2f} USDT</b> (или TRX по курсу)\n"
        f"Адрес для перевода:\n<code>{user.tron_address}</code>\n\n"
        f"⏱ <b>Время на оплату: {config.ticket_expire_minutes} минут.</b>\n"
        f"После отправки транзакции администратор подтвердит поступление."
    )
    await message.answer(user_info)

    # Оповещение администратора
    admin_msg = (
        f"🔔 <b>Новая заявка на пополнение #{ticket.id}</b>\n\n"
        f"👤 Пользователь: @{message.from_user.username or 'без_username'} (<code>{user.tg_id}</code>)\n"
        f"💰 Сумма: <b>${amount:.2f}</b>\n"
        f"🌐 TRC20 адрес: <code>{user.tron_address}</code>\n"
        f"⏳ Срок: {config.ticket_expire_minutes} минут"
    )
    try:
        await bot.send_message(
            config.admin_id,
            admin_msg,
            reply_markup=get_ticket_admin_kb(ticket.id)
        )
    except Exception:
        pass

    # Запуск таймера экспирации
    asyncio.create_task(expire_ticket_timer(ticket.id, bot))
