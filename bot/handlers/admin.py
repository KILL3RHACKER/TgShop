from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from bot.config import config
from bot.database.models import User, TopUpTicket, TicketStatus, Category, Product
from bot.keyboards.callbacks import TicketActionCD
from bot.services.ticket import approve_ticket, reject_ticket

admin_router = Router()

def is_admin_check(tg_id: int) -> bool:
    return tg_id == config.admin_id

@admin_router.callback_query(F.data == "admin_menu")
async def show_admin_panel(callback: CallbackQuery, session: AsyncSession):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer()

    total_users = await session.scalar(select(func.count(User.id))) or 0
    pending_tickets = await session.scalar(
        select(func.count(TopUpTicket.id)).where(TopUpTicket.status == TicketStatus.PENDING)
    ) or 0

    builder = InlineKeyboardBuilder()
    builder.button(text=f"📋 Активные тикеты ({pending_tickets})", callback_data="admin_pending_tickets")
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    builder.adjust(1)

    text = (
        "⚙️ <b>Панель администратора</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"⏳ Ожидают проверки тикетов: <b>{pending_tickets}</b>"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@admin_router.callback_query(F.data == "admin_pending_tickets")
async def list_pending_tickets(callback: CallbackQuery, session: AsyncSession):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer()

    result = await session.execute(
        select(TopUpTicket).where(TopUpTicket.status == TicketStatus.PENDING).order_by(TopUpTicket.created_at.desc())
    )
    tickets = list(result.scalars().all())

    if not tickets:
        builder = InlineKeyboardBuilder()
        builder.button(text="◀️ Назад", callback_data="admin_menu")
        await callback.message.edit_text("Нет активных тикетов на проверку.", reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    for t in tickets:
        builder.button(
            text=f"Тикет #{t.id} — ${t.amount:.2f}",
            callback_data=f"admin_view_tck:{t.id}"
        )
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.adjust(1)

    await callback.message.edit_text("Выберите тикет для просмотра:", reply_markup=builder.as_markup())

@admin_router.callback_query(F.data.startswith("admin_view_tck:"))
async def view_single_ticket(callback: CallbackQuery, session: AsyncSession):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer()

    ticket_id = int(callback.data.split(":")[1])
    ticket = await session.get(TopUpTicket, ticket_id)
    if not ticket:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    user = await session.scalar(select(User).where(User.tg_id == ticket.user_id))

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=TicketActionCD(id=ticket.id, action="approve"))
    builder.button(text="❌ Отклонить", callback_data=TicketActionCD(id=ticket.id, action="reject"))
    builder.button(text="◀️ К списку тикетов", callback_data="admin_pending_tickets")
    builder.adjust(2, 1)

    text = (
        f"📄 <b>Тикет #{ticket.id}</b>\n\n"
        f"Пользователь: <code>{ticket.user_id}</code> (@{user.username if user else '?'})\n"
        f"Сумма: <b>${ticket.amount:.2f}</b>\n"
        f"Адрес: <code>{user.tron_address if user else 'N/A'}</code>\n"
        f"Создан: {ticket.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Статус: <b>{ticket.status.value}</b>"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@admin_router.callback_query(TicketActionCD.filter())
async def process_ticket_action(
    callback: CallbackQuery,
    callback_data: TicketActionCD,
    session: AsyncSession,
    bot: Bot
):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    await callback.answer()
    if callback_data.action == "approve":
        success, msg = await approve_ticket(session, callback_data.id, bot=bot)
        if success:
            await callback.message.edit_text(f"✅ <b>{msg}</b>")
        else:
            await callback.message.edit_text(f"⚠️ {msg}")
    elif callback_data.action == "reject":
        success, msg = await reject_ticket(session, callback_data.id, bot=bot)
        if success:
            await callback.message.edit_text(f"❌ <b>{msg}</b>")
        else:
            await callback.message.edit_text(f"⚠️ {msg}")
