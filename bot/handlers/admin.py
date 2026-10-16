from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from bot.config import config
from bot.database.models import User, TopUpTicket, TicketStatus, Order, OrderStatus
from bot.keyboards.callbacks import TicketActionCD, OrderCD
from bot.keyboards.inline import get_dispute_admin_kb
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
    dispute_count = await session.scalar(
        select(func.count(Order.id)).where(Order.status == OrderStatus.DISPUTED)
    ) or 0
    hold_count = await session.scalar(
        select(func.count(Order.id)).where(Order.status == OrderStatus.HOLD)
    ) or 0

    hold_sum = await session.scalar(
        select(func.coalesce(func.sum(Order.amount), 0.0)).where(Order.status == OrderStatus.HOLD)
    ) or 0.0
    completed_sum = await session.scalar(
        select(func.coalesce(func.sum(Order.amount), 0.0)).where(Order.status == OrderStatus.COMPLETED)
    ) or 0.0

    builder = InlineKeyboardBuilder()
    builder.button(text=f"📋 Тикеты пополнения ({pending_tickets})", callback_data="admin_pending_tickets")
    builder.button(text=f"🚨 Активные диспуты ({dispute_count})", callback_data="admin_disputes")
    builder.button(text=f"⏳ Заказы в холде ({hold_count})", callback_data="admin_hold_orders")
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    builder.adjust(1)

    text = (
        "⚙️ <b>Панель администратора</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"⏳ Ожидают пополнения (тикеты): <b>{pending_tickets}</b>\n"
        f"🚨 Открытых диспутов: <b>{dispute_count}</b>\n"
        f"🔒 Заказов в холде 24ч: <b>{hold_count}</b> (${float(hold_sum):.2f})\n"
        f"💰 Завершенных продаж (разморожено): <b>${float(completed_sum):.2f}</b>"
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

@admin_router.callback_query(F.data == "admin_disputes")
async def list_disputed_orders(callback: CallbackQuery, session: AsyncSession):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer()

    result = await session.execute(
        select(Order).where(Order.status == OrderStatus.DISPUTED).order_by(Order.disputed_at.desc())
    )
    orders = list(result.scalars().all())

    builder = InlineKeyboardBuilder()
    if not orders:
        builder.button(text="◀️ Назад", callback_data="admin_menu")
        await callback.message.edit_text("Нет активных споров.", reply_markup=builder.as_markup())
        return

    for o in orders:
        builder.button(
            text=f"🚨 Спор #{o.id} (${o.amount:.2f})",
            callback_data=f"admin_view_dsp:{o.id}"
        )
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text("Выберите спор для рассмотрения:", reply_markup=builder.as_markup())

@admin_router.callback_query(F.data.startswith("admin_view_dsp:"))
async def view_admin_dispute(callback: CallbackQuery, session: AsyncSession):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer()

    order_id = int(callback.data.split(":")[1])
    order = await session.get(Order, order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    user = await session.scalar(select(User).where(User.tg_id == order.user_id))

    history = ""
    for m in order.messages[-5:]:
        sender_label = "Клиент" if m.sender_role == "client" else "Админ"
        history += f"• <b>{sender_label}:</b> {m.text}\n"

    text = (
        f"🚨 <b>Диспут по заказу {order.order_code}</b>\n\n"
        f"👤 Покупатель: @{user.username if user else '—'} (<code>{order.user_id}</code>)\n"
        f"💵 Сумма: <b>${order.amount:.2f} USDT</b>\n"
        f"📦 Товар: <code>{order.account.data if order.account else '—'}</code>\n\n"
        f"💬 <b>История сообщений:</b>\n{history or 'Нет сообщений'}"
    )

    kb = get_dispute_admin_kb(order.id)
    await callback.message.edit_text(text, reply_markup=kb)

@admin_router.callback_query(F.data == "admin_hold_orders")
async def list_hold_orders(callback: CallbackQuery, session: AsyncSession):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer()

    result = await session.execute(
        select(Order).where(Order.status == OrderStatus.HOLD).order_by(Order.hold_until.asc()).limit(15)
    )
    orders = list(result.scalars().all())

    builder = InlineKeyboardBuilder()
    if not orders:
        builder.button(text="◀️ Назад", callback_data="admin_menu")
        await callback.message.edit_text("Нет заказов в холде.", reply_markup=builder.as_markup())
        return

    now = datetime.utcnow()
    text = "🔒 <b>Заказы в 24-часовом холде:</b>\n\n"
    for o in orders:
        remaining = o.hold_until - now
        hours, remainder = divmod(max(0, int(remaining.total_seconds())), 3600)
        minutes = remainder // 60
        text += f"• <b>{o.order_code}</b>: ${o.amount:.2f} — осталось {hours}ч {minutes}м\n"

    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
