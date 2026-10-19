import asyncio
import io
import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, TelegramObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import config
from bot.database.models import (
    Account,
    Category,
    DisputeMessage,
    Order,
    OrderStatus,
    Product,
    Subcategory,
    TicketStatus,
    TopUpTicket,
    User,
)
from bot.services.account_zip import generate_account_zip
from bot.services.reputation import format_reputation_text, get_user_reputation
from bot.services.ticket import approve_ticket, credit_admin_balance, reject_ticket
from bot.services.tron import is_valid_tron_address, send_tron_transaction, sweep_all_wallets
from web.services import (
    ParsedAccount,
    add_accounts_to_category_auto_age,
    parse_accounts_from_archive,
    parse_accounts_from_extracted_dir,
    parse_accounts_from_text,
)

logger = logging.getLogger(__name__)

admin_bot_router = Router(name="admin_bot")

ACCESS_DENIED_TEXT = "Доступ запрещен. Это закрытый терминал администрации."


class AdminBotStates(StatesGroup):
    waiting_upload_file = State()
    waiting_upload_category = State()
    waiting_dispute_reply = State()
    waiting_withdraw_address = State()
    waiting_withdraw_amount = State()
    waiting_broadcast_text = State()


class AdminAuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        event_from_user = data.get("event_from_user")
        if not event_from_user:
            return await handler(event, data)

        user_id = event_from_user.id
        session: AsyncSession | None = data.get("session")

        is_allowed = False
        if user_id == config.admin_id:
            is_allowed = True
        elif session:
            user = await session.scalar(select(User).where(User.tg_id == user_id))
            if user and not user.is_banned and user.role in ("admin", "moderator"):
                is_allowed = True
        else:
            from bot.database.base import async_session

            async with async_session() as s:
                user = await s.scalar(select(User).where(User.tg_id == user_id))
                if user and not user.is_banned and user.role in ("admin", "moderator"):
                    is_allowed = True

        if not is_allowed:
            if isinstance(event, CallbackQuery):
                await event.answer(ACCESS_DENIED_TEXT, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(ACCESS_DENIED_TEXT)
            return

        return await handler(event, data)


admin_bot_router.message.middleware(AdminAuthMiddleware())
admin_bot_router.callback_query.middleware(AdminAuthMiddleware())


def get_target_shop_bot(bot: Bot, shop_bot: Bot | None = None) -> Bot:
    return shop_bot or bot


async def ensure_admin_user(session: AsyncSession, tg_id: int, username: str | None = None) -> User:
    admin = await session.scalar(select(User).where(User.tg_id == tg_id))
    if not admin:
        from bot.services.tron import generate_tron_wallet

        t_addr, t_key = generate_tron_wallet()
        admin = User(
            tg_id=tg_id,
            username=username,
            role="admin",
            rules_accepted=True,
            tron_address=t_addr,
            tron_private_key=t_key,
            balance=0.0,
        )
        session.add(admin)
        await session.commit()
    elif admin.role != "admin" and tg_id == config.admin_id:
        admin.role = "admin"
        await session.commit()
    return admin


def serialize_account_dict(acc: ParsedAccount) -> dict:
    d = dict(acc)
    if isinstance(d.get("registration_date"), (date, datetime)):
        d["registration_date"] = d["registration_date"].isoformat()
    return d


def deserialize_account_dict(d: dict) -> ParsedAccount:
    res = dict(d)
    reg_val = res.get("registration_date")
    if reg_val and isinstance(reg_val, str):
        try:
            res["registration_date"] = date.fromisoformat(reg_val)
        except Exception:
            res["registration_date"] = None
    return res  # type: ignore


async def build_main_menu(session: AsyncSession) -> tuple[str, any]:
    pending_tickets = (
        await session.scalar(select(func.count(TopUpTicket.id)).where(TopUpTicket.status == TicketStatus.PENDING))
    ) or 0
    active_appeals = (
        await session.scalar(
            select(func.count(Order.id)).where(Order.status.in_([OrderStatus.DISPUTED, OrderStatus.QUESTION]))
        )
    ) or 0

    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="ab_stats")
    builder.button(text=f"📋 Тикеты ({pending_tickets})", callback_data="ab_tickets")
    builder.button(text=f"🚨 Диспуты ({active_appeals})", callback_data="ab_disputes")
    builder.button(text="📦 Склад / Загрузка", callback_data="ab_stock")
    builder.button(text="💳 Кошелек и Вывод", callback_data="ab_wallet")
    builder.button(text="👥 Управление / Рассылка", callback_data="ab_manage")
    builder.adjust(2, 2, 2)

    text = (
        "🤖 <b>Админ-терминал магазина TG Shop Bot</b>\n\n"
        "Добро пожаловать в закрытый терминал администрации.\n"
        "Выберите раздел для работы:"
    )
    return text, builder.as_markup()


# --- Главное меню и /start ---


@admin_bot_router.message(Command("start", "menu", "admin", "help"))
async def cmd_admin_start(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    await ensure_admin_user(session, message.from_user.id, message.from_user.username)
    text, kb = await build_main_menu(session)
    await message.answer(text, reply_markup=kb)


@admin_bot_router.callback_query(F.data == "ab_main")
async def cb_admin_main(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    await callback.answer()
    text, kb = await build_main_menu(session)
    await callback.message.edit_text(text, reply_markup=kb)


# --- 1. 📊 Статистика ---


@admin_bot_router.callback_query(F.data == "ab_stats")
async def cb_admin_stats(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    total_users = (await session.scalar(select(func.count(User.id)))) or 0
    active_buyers = (
        await session.scalar(select(func.count(User.id)).where(User.role == "buyer", User.is_banned == False))
    ) or 0
    banned_users = (await session.scalar(select(func.count(User.id)).where(User.is_banned == True))) or 0

    completed_sum = (
        await session.scalar(
            select(func.coalesce(func.sum(Order.amount), 0.0)).where(Order.status == OrderStatus.COMPLETED)
        )
    ) or 0.0
    completed_orders = (
        await session.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.COMPLETED))
    ) or 0

    hold_sum = (
        await session.scalar(
            select(func.coalesce(func.sum(Order.amount), 0.0)).where(
                Order.status.in_([OrderStatus.HOLD, OrderStatus.QUESTION])
            )
        )
    ) or 0.0
    hold_orders = (
        await session.scalar(
            select(func.count(Order.id)).where(Order.status.in_([OrderStatus.HOLD, OrderStatus.QUESTION]))
        )
    ) or 0

    admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    admin_balance = float(admin_user.balance) if admin_user else 0.0

    pending_tickets = (
        await session.scalar(select(func.count(TopUpTicket.id)).where(TopUpTicket.status == TicketStatus.PENDING))
    ) or 0
    dispute_count = (
        await session.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.DISPUTED))
    ) or 0
    question_count = (
        await session.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.QUESTION))
    ) or 0
    stock_count = (await session.scalar(select(func.count(Account.id)).where(Account.is_sold == False))) or 0

    text = (
        "📊 <b>Сводная статистика магазина</b>\n\n"
        "👥 <b>Пользователи:</b>\n"
        f"• Всего в базе: <b>{total_users}</b>\n"
        f"• Активных покупателей: <b>{active_buyers}</b>\n"
        f"• Заблокировано: <b>{banned_users}</b>\n\n"
        "💰 <b>Финансы:</b>\n"
        f"• Баланс магазина: <b>${admin_balance:.2f} USDT</b>\n"
        f"• Выручка от продаж: <b>${float(completed_sum):.2f} USDT</b> ({completed_orders} заказов)\n"
        f"• Заказов в холде (24ч): <b>${float(hold_sum):.2f} USDT</b> ({hold_orders} заказов)\n\n"
        "📋 <b>Операционная деятельность:</b>\n"
        f"• Тикеты пополнения (ожидают): <b>{pending_tickets}</b>\n"
        f"• Открытых диспутов: <b>{dispute_count}</b>\n"
        f"• Вопросов по заказам: <b>{question_count}</b>\n"
        f"• Аккаунтов на складе: <b>{stock_count} шт.</b>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="ab_stats")
    builder.button(text="◀️ Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


# --- 2. 📋 Тикеты пополнения ---


@admin_bot_router.callback_query(F.data == "ab_tickets")
async def cb_admin_tickets(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    stmt = select(TopUpTicket).where(TopUpTicket.status == TicketStatus.PENDING).order_by(TopUpTicket.created_at.desc())
    tickets = list((await session.execute(stmt)).scalars().all())

    builder = InlineKeyboardBuilder()
    if not tickets:
        builder.button(text="◀️ Главное меню", callback_data="ab_main")
        await callback.message.edit_text(
            "📋 <b>Тикеты пополнения</b>\n\nНет ожидающих проверки тикетов.",
            reply_markup=builder.as_markup(),
        )
        return

    for t in tickets:
        builder.button(text=f"Тикет #{t.id} — ${float(t.amount):.2f}", callback_data=f"ab_tck:{t.id}")
    builder.button(text="◀️ Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await callback.message.edit_text(
        f"📋 <b>Ожидающие тикеты пополнения ({len(tickets)} шт.):</b>\nВыберите тикет для обработки:",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.callback_query(F.data.startswith("ab_tck:"))
async def cb_admin_view_ticket(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])
    ticket = await session.get(TopUpTicket, ticket_id)
    if not ticket:
        await callback.message.edit_text(
            "Тикет не найден.",
            reply_markup=InlineKeyboardBuilder().button(text="◀️ К списку", callback_data="ab_tickets").as_markup(),
        )
        return

    user = await session.scalar(select(User).where(User.tg_id == ticket.user_id))

    text = (
        f"📋 <b>Тикет на пополнение #{ticket.id}</b>\n\n"
        f"👤 Покупатель: @{user.username if user else '—'} (<code>{ticket.user_id}</code>)\n"
        f"💵 Сумма: <b>${float(ticket.amount):.2f} USDT</b>\n"
        f"📍 TRON кошелек: <code>{user.tron_address if user else 'N/A'}</code>\n"
        f"⏰ Создан: {ticket.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"⏳ Статус: <b>{ticket.status.value.upper()}</b>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"ab_tck_act:{ticket.id}:approve")
    builder.button(text="❌ Отклонить", callback_data=f"ab_tck_act:{ticket.id}:reject")
    builder.button(text="◀️ К списку тикетов", callback_data="ab_tickets")
    builder.adjust(2, 1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@admin_bot_router.callback_query(F.data.startswith("ab_tck_act:"))
async def cb_admin_ticket_action(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    shop_bot: Bot | None = None,
):
    await callback.answer()
    parts = callback.data.split(":")
    ticket_id = int(parts[1])
    action = parts[2]

    target_bot = get_target_shop_bot(bot, shop_bot)

    if action == "approve":
        success, msg = await approve_ticket(session, ticket_id, bot=target_bot)
        prefix = "✅" if success else "⚠️"
    else:
        success, msg = await reject_ticket(session, ticket_id, bot=target_bot)
        prefix = "❌" if success else "⚠️"

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ К списку тикетов", callback_data="ab_tickets")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await callback.message.edit_text(f"{prefix} <b>{msg}</b>", reply_markup=builder.as_markup())


# --- 3. 🚨 Диспуты и Вопросы ---


@admin_bot_router.callback_query(F.data == "ab_disputes")
async def cb_admin_disputes(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    stmt = (
        select(Order)
        .where(Order.status.in_([OrderStatus.DISPUTED, OrderStatus.QUESTION]))
        .order_by(Order.last_reply_at.desc().nullslast(), Order.created_at.desc())
    )
    appeals = list((await session.execute(stmt)).scalars().all())

    builder = InlineKeyboardBuilder()
    if not appeals:
        builder.button(text="◀️ Главное меню", callback_data="ab_main")
        await callback.message.edit_text(
            "🚨 <b>Диспуты и Вопросы</b>\n\nНет активных обращений от покупателей.",
            reply_markup=builder.as_markup(),
        )
        return

    for o in appeals:
        icon = "🚨 Диспут" if o.status == OrderStatus.DISPUTED else "❓ Вопрос"
        builder.button(
            text=f"{icon} {o.order_code} (${float(o.amount):.2f})",
            callback_data=f"ab_disp_view:{o.id}",
        )
    builder.button(text="◀️ Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await callback.message.edit_text(
        f"🚨 <b>Активные обращения покупателей ({len(appeals)} шт.):</b>\nВыберите для рассмотрения:",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.callback_query(F.data.startswith("ab_disp_view:"))
async def cb_admin_view_dispute(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    order_id = int(callback.data.split(":")[1])
    order = await session.get(Order, order_id)
    if not order:
        await callback.message.edit_text(
            "Заказ не найден.",
            reply_markup=InlineKeyboardBuilder().button(text="◀️ Назад", callback_data="ab_disputes").as_markup(),
        )
        return

    user = await session.scalar(select(User).where(User.tg_id == order.user_id))
    rep = await get_user_reputation(session, order.user_id)
    rep_text = format_reputation_text(rep, username=user.username if user else None, user_id=order.user_id)

    history_lines = []
    for m in order.messages[-6:]:
        sender = "👤 Клиент" if m.sender_role == "client" else "🛡 Поддержка"
        history_lines.append(f"• <b>{sender}:</b> {m.text}")
    history_str = "\n".join(history_lines) if history_lines else "<i>Сообщений пока нет</i>"

    account_info = order.account_data or (order.account.data if order.account else "—")
    appeal_type = "Диспут" if order.status == OrderStatus.DISPUTED else "Вопрос по заказу"

    text = (
        f"🚨 <b>{appeal_type} по заказу {order.order_code}</b>\n\n"
        f"👤 Покупатель: @{user.username if user else '—'} (<code>{order.user_id}</code>)\n"
        f"💵 Сумма: <b>${float(order.amount):.2f} USDT</b>\n"
        f"📦 Товар: <code>{account_info}</code>\n"
        f"⏳ Статус: <b>{order.status.value.upper()}</b>\n\n"
        f"{rep_text}\n\n"
        f"💬 <b>История сообщений:</b>\n{history_str}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Ответить клиенту", callback_data=f"ab_disp_reply:{order.id}")
    builder.button(text="🔄 Выдать замену", callback_data=f"ab_disp_replace:{order.id}")
    builder.button(text="✅ Возврат", callback_data=f"ab_disp_resolve:{order.id}:refund")
    builder.button(text="❌ В пользу шопа", callback_data=f"ab_disp_resolve:{order.id}:reject")
    builder.button(text="⛔️ Забанить фродера", callback_data=f"ab_disp_ban:{order.id}")
    builder.button(text="◀️ К списку обращений", callback_data="ab_disputes")
    builder.adjust(2, 2, 1, 1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@admin_bot_router.callback_query(F.data.startswith("ab_disp_reply:"))
async def cb_admin_reply_dispute(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    order_id = int(callback.data.split(":")[1])
    order = await session.get(Order, order_id)
    if not order:
        await callback.message.edit_text("Заказ не найден.")
        return

    await state.set_state(AdminBotStates.waiting_dispute_reply)
    await state.update_data(order_id=order.id)

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Отмена", callback_data=f"ab_disp_view:{order.id}")

    await callback.message.edit_text(
        f"✏️ <b>Ответ клиенту по заказу {order.order_code}</b>\n\n"
        "Отправьте текст сообщения (или фото с комментарием) для покупателя:",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.message(AdminBotStates.waiting_dispute_reply)
async def process_admin_dispute_reply(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    shop_bot: Bot | None = None,
):
    data = await state.get_data()
    order_id = data.get("order_id")
    await state.clear()

    order = await session.get(Order, order_id)
    if not order:
        await message.answer("Заказ не найден.")
        return

    text_msg = message.text or message.caption or ""
    photo_id = message.photo[-1].file_id if message.photo else None

    if not text_msg and not photo_id:
        await message.answer("⚠️ Сообщение не может быть пустым.")
        return

    disp_msg = DisputeMessage(
        order_id=order.id,
        sender_id=message.from_user.id,
        sender_role="admin",
        text=text_msg or "Фотография от поддержки",
        photo_file_id=photo_id,
    )
    session.add(disp_msg)

    now = datetime.utcnow()
    order.last_reply_by = "admin"
    order.last_reply_at = now
    order.user_unread = True
    order.dispute_deadline = now + timedelta(hours=24)
    await session.commit()

    target_bot = get_target_shop_bot(bot, shop_bot)
    try:
        if photo_id:
            await target_bot.send_photo(
                chat_id=order.user_id,
                photo=photo_id,
                caption=f"💬 <b>Ответ поддержки по заказу {order.order_code}:</b>\n\n{text_msg}",
            )
        else:
            await target_bot.send_message(
                chat_id=order.user_id,
                text=f"💬 <b>Ответ поддержки по заказу {order.order_code}:</b>\n\n{text_msg}",
            )
    except Exception as e:
        logger.warning(f"Failed to notify buyer {order.user_id}: {e}")

    builder = InlineKeyboardBuilder()
    builder.button(text="👀 Просмотреть обращение", callback_data=f"ab_disp_view:{order.id}")
    builder.button(text="◀️ К списку обращений", callback_data="ab_disputes")
    builder.adjust(1)

    await message.answer("✅ <b>Ваш ответ успешно отправлен покупателю!</b>", reply_markup=builder.as_markup())


@admin_bot_router.callback_query(F.data.startswith("ab_disp_replace:"))
async def cb_admin_dispute_replace_select(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    order_id = int(callback.data.split(":")[1])
    order = await session.get(Order, order_id)
    if not order:
        await callback.message.edit_text("Заказ не найден.")
        return

    accounts: List[Account] = []
    if order.product_id:
        stmt = (
            select(Account)
            .where(Account.is_sold == False, Account.product_id == order.product_id)
            .order_by(Account.created_at.desc())
            .limit(10)
        )
        accounts = list((await session.execute(stmt)).scalars().all())

    if not accounts:
        stmt_any = select(Account).where(Account.is_sold == False).order_by(Account.created_at.desc()).limit(10)
        accounts = list((await session.execute(stmt_any)).scalars().all())

    builder = InlineKeyboardBuilder()
    if not accounts:
        builder.button(text="◀️ Назад", callback_data=f"ab_disp_view:{order.id}")
        await callback.message.edit_text(
            f"❌ На складе нет свободных аккаунтов для замены по заказу {order.order_code}.",
            reply_markup=builder.as_markup(),
        )
        return

    for acc in accounts:
        builder.button(
            text=f"📦 #{acc.id} {acc.name} ({acc.date_info})",
            callback_data=f"ab_disp_do_replace:{order.id}:{acc.id}",
        )
    builder.button(text="◀️ Отмена", callback_data=f"ab_disp_view:{order.id}")
    builder.adjust(1)

    await callback.message.edit_text(
        f"🔄 <b>Выбор аккаунта для замены по заказу {order.order_code}:</b>\n\n"
        "Выберите подходящий аккаунт из складского наличия для выдачи клиенту:",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.callback_query(F.data.startswith("ab_disp_do_replace:"))
async def cb_admin_dispute_do_replace(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    shop_bot: Bot | None = None,
):
    await callback.answer()
    parts = callback.data.split(":")
    order_id = int(parts[1])
    account_id = int(parts[2])

    order = await session.get(Order, order_id)
    account = await session.get(Account, account_id)
    if not order or not account or account.is_sold:
        await callback.message.edit_text(
            "❌ Ошибка: заказ не найден либо выбранный аккаунт уже продан.",
            reply_markup=InlineKeyboardBuilder().button(text="◀️ Назад", callback_data="ab_disputes").as_markup(),
        )
        return

    now = datetime.utcnow()
    account.is_sold = True
    account.sold_to_user_id = order.user_id
    account.sold_at = now

    order.account_id = account.id
    order.account_data = account.data
    order.status = OrderStatus.COMPLETED
    order.resolved_at = now
    order.last_reply_by = "admin"
    order.last_reply_at = now
    await credit_admin_balance(session, float(order.amount))

    disp_msg = DisputeMessage(
        order_id=order.id,
        sender_id=callback.from_user.id,
        sender_role="admin",
        text=f"Администратор предоставил замену по заказу: {account.data}",
    )
    session.add(disp_msg)
    await session.commit()

    product = order.product or Product(name="Замена по заказу", price=order.amount)
    zip_bytes, zip_filename = generate_account_zip(order, account, product)
    zip_file = BufferedInputFile(zip_bytes, filename=zip_filename)

    target_bot = get_target_shop_bot(bot, shop_bot)
    try:
        await target_bot.send_document(
            chat_id=order.user_id,
            document=zip_file,
            caption=(
                f"🔄 <b>Вам выдана замена по заказу {order.order_code}!</b>\n\n"
                f"📦 <b>Новый аккаунт:</b> <code>{account.data}</code>\n\n"
                "Архив с логом и cookies прикреплен выше. Обращение закрыто."
            ),
        )
    except Exception as e:
        logger.error(f"Failed to deliver replacement zip to buyer {order.user_id}: {e}")

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ К списку обращений", callback_data="ab_disputes")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await callback.message.edit_text(
        f"✅ <b>Замена по заказу {order.order_code} успешно выдана!</b>\n\n"
        f"Выданный аккаунт: <code>{account.data}</code>\n"
        "Архив доставлен покупателю, заказ переведен в статус выполненных.",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.callback_query(F.data.startswith("ab_disp_resolve:"))
async def cb_admin_dispute_resolve(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    shop_bot: Bot | None = None,
):
    await callback.answer()
    parts = callback.data.split(":")
    order_id = int(parts[1])
    action = parts[2]

    order = await session.get(Order, order_id)
    if not order:
        await callback.message.edit_text("Заказ не найден.")
        return

    user = await session.scalar(select(User).where(User.tg_id == order.user_id))
    now = datetime.utcnow()
    target_bot = get_target_shop_bot(bot, shop_bot)

    if action == "refund":
        order.status = OrderStatus.REFUNDED
        order.resolved_at = now
        if user:
            user.balance = float(user.balance) + float(order.amount)
        session.add(
            DisputeMessage(
                order_id=order.id,
                sender_id=callback.from_user.id,
                sender_role="admin",
                text=f"Администратор одобрил полный возврат средств (${float(order.amount):.2f}).",
            )
        )
        await session.commit()

        try:
            await target_bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"🎉 <b>Диспут по заказу {order.order_code} решен в вашу пользу!</b>\n\n"
                    f"Средства в размере <b>${float(order.amount):.2f} USDT</b> возвращены на ваш баланс магазина."
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to notify buyer {order.user_id}: {e}")

        res_text = (
            f"✅ <b>Возврат средств по заказу {order.order_code} выполнен!</b>\n\n"
            f"Сумма: <b>${float(order.amount):.2f} USDT</b>\n"
            f"Покупатель: @{user.username if user else '—'} (<code>{order.user_id}</code>)\n"
            "Диспут успешно закрыт."
        )

    else:
        order.status = OrderStatus.COMPLETED
        order.resolved_at = now
        await credit_admin_balance(session, float(order.amount))
        session.add(
            DisputeMessage(
                order_id=order.id,
                sender_id=callback.from_user.id,
                sender_role="admin",
                text="Администратор рассмотрел спор и закрыл его в пользу магазина.",
            )
        )
        await session.commit()

        try:
            await target_bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"❌ <b>Диспут по заказу {order.order_code} закрыт Администратором.</b>\n\n"
                    "После всестороннего изучения материалов спора решение вынесено в пользу магазина."
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to notify buyer {order.user_id}: {e}")

        res_text = (
            f"❌ <b>Диспут {order.order_code} закрыт в пользу магазина!</b>\n\n"
            f"Сумма: <b>${float(order.amount):.2f} USDT</b> переведена в кассу магазина."
        )

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ К списку обращений", callback_data="ab_disputes")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await callback.message.edit_text(res_text, reply_markup=builder.as_markup())


@admin_bot_router.callback_query(F.data.startswith("ab_disp_ban:"))
async def cb_admin_dispute_ban(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    shop_bot: Bot | None = None,
):
    await callback.answer()
    order_id = int(callback.data.split(":")[1])
    order = await session.get(Order, order_id)
    if not order:
        await callback.message.edit_text("Заказ не найден.")
        return

    user = await session.scalar(select(User).where(User.tg_id == order.user_id))
    now = datetime.utcnow()

    if user:
        user.is_banned = True
        user.banned_reason = f"Мошенничество по заказу {order.order_code}"

    order.status = OrderStatus.COMPLETED
    order.resolved_at = now
    await credit_admin_balance(session, float(order.amount))

    session.add(
        DisputeMessage(
            order_id=order.id,
            sender_id=callback.from_user.id,
            sender_role="admin",
            text="Покупатель заблокирован Администратором за мошенничество.",
        )
    )
    await session.commit()

    target_bot = get_target_shop_bot(bot, shop_bot)
    try:
        await target_bot.send_message(
            chat_id=order.user_id,
            text="⛔️ <b>Ваш аккаунт заблокирован службой безопасности за нарушение регламента площадки.</b>",
        )
    except Exception:
        pass

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ К списку обращений", callback_data="ab_disputes")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await callback.message.edit_text(
        f"⛔️ <b>Покупатель @{user.username if user else '—'} (<code>{order.user_id}</code>) заблокирован за фрод!</b>\n\n"
        f"Заказ {order.order_code} закрыт в пользу магазина, средства зачислены в кассу.",
        reply_markup=builder.as_markup(),
    )


# --- 4. 📦 Загрузка товаров / Склад ---


@admin_bot_router.callback_query(F.data == "ab_stock")
async def cb_admin_stock(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    categories = list((await session.execute(select(Category).order_by(Category.name.asc()))).scalars().all())

    if not categories:
        text = "📦 <b>Склад пуст.</b> В базе нет созданных категорий."
    else:
        stock_lines = []
        grand_total = 0
        for cat in categories:
            prods_res = await session.execute(
                select(Product)
                .join(Subcategory, Product.subcategory_id == Subcategory.id)
                .where(Subcategory.category_id == cat.id)
            )
            products = list(prods_res.scalars().all())
            prod_ids = [p.id for p in products]

            if not prod_ids:
                stock_lines.append(f"🏦 <b>{cat.name}:</b> <i>нет товаров</i>")
                continue

            accs_res = await session.execute(
                select(Account.product_id, func.count(Account.id))
                .where(Account.product_id.in_(prod_ids), Account.is_sold == False)
                .group_by(Account.product_id)
            )
            counts = dict(accs_res.all())
            cat_total = sum(counts.values())
            grand_total += cat_total

            t7 = sum(
                cnt
                for pid, cnt in counts.items()
                if any(p.id == pid and "7" in p.name and "15" not in p.name for p in products)
            )
            t15 = sum(cnt for pid, cnt in counts.items() if any(p.id == pid and "15" in p.name for p in products))
            t30 = sum(cnt for pid, cnt in counts.items() if any(p.id == pid and "30" in p.name for p in products))

            stock_lines.append(
                f"🏦 <b>{cat.name}:</b>\n"
                f"  • 7 Days: <b>{t7} шт.</b>\n"
                f"  • 15 Days: <b>{t15} шт.</b>\n"
                f"  • 30+ Days: <b>{t30} шт.</b>\n"
                f"  <i>Всего по банку: {cat_total} шт.</i>"
            )

        text = (
            "📦 <b>Складские остатки товаров</b>\n\n"
            + "\n\n".join(stock_lines)
            + f"\n\n📊 <b>Всего товара в наличии: {grand_total} шт.</b>"
        )

    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Загрузить товар", callback_data="ab_upload_select_cat")
    builder.button(text="🔄 Обновить", callback_data="ab_stock")
    builder.button(text="◀️ Главное меню", callback_data="ab_main")
    builder.adjust(2, 1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@admin_bot_router.callback_query(F.data == "ab_upload_select_cat")
async def cb_admin_upload_select_cat(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    categories = list((await session.execute(select(Category).order_by(Category.name.asc()))).scalars().all())

    builder = InlineKeyboardBuilder()
    if not categories:
        builder.button(text="◀️ Назад", callback_data="ab_stock")
        await callback.message.edit_text(
            "⚠️ В системе пока нет ни одной категории для загрузки.",
            reply_markup=builder.as_markup(),
        )
        return

    for c in categories:
        builder.button(text=f"🏦 {c.name}", callback_data=f"ab_upload_cat:{c.id}")
    builder.button(text="◀️ Отмена", callback_data="ab_stock")
    builder.adjust(1)

    await callback.message.edit_text(
        "📁 <b>Выберите категорию банка для загрузки товаров:</b>",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.callback_query(F.data.startswith("ab_upload_cat:"))
async def cb_admin_upload_cat_chosen(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    cat_id = int(callback.data.split(":")[1])
    cat = await session.get(Category, cat_id)
    if not cat:
        await callback.message.edit_text("Категория не найдена.")
        return

    await state.set_state(AdminBotStates.waiting_upload_file)
    await state.update_data(upload_category_id=cat.id)

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Отмена", callback_data="ab_stock")

    await callback.message.edit_text(
        f"📥 <b>Категория: {cat.name}</b>\n\n"
        "Отправьте <b>.zip</b> архив или <b>.txt</b> документ со строками/логами аккаунтов прямо в этот чат:",
        reply_markup=builder.as_markup(),
    )


async def _parse_upload_bytes(filename: str, raw_bytes: bytes) -> List[ParsedAccount]:
    fn_lower = filename.lower()
    if fn_lower.endswith(".zip") or fn_lower.endswith(".rar"):
        accounts, _ = parse_accounts_from_archive(filename, raw_bytes)
        return accounts

    # Handle .txt, .log or plain text
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = os.path.join(tmpdir, filename or "accounts.txt")
        with open(temp_path, "wb") as f:
            f.write(raw_bytes)
        accounts, _ = parse_accounts_from_extracted_dir(tmpdir)
        if accounts:
            return accounts

    # Direct fallback for line decoding without premature dedup
    from web.services import parse_account_line
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            lines_acc = [p for l in text.splitlines() if (p := parse_account_line(l))]
            if lines_acc:
                return lines_acc
        except UnicodeDecodeError:
            continue
    return []


async def _import_accounts_and_report(
    session: AsyncSession,
    category_id: int,
    parsed_accounts: List[ParsedAccount],
) -> str:
    total_candidates = len(parsed_accounts)
    stats = await add_accounts_to_category_auto_age(session, category_id, parsed_accounts)
    total_imported = sum(stats.values())
    duplicates_skipped = total_candidates - total_imported

    t7 = sum(cnt for name, cnt in stats.items() if "7" in name and "15" not in name)
    t15 = sum(cnt for name, cnt in stats.items() if "15" in name)
    t30 = sum(cnt for name, cnt in stats.items() if "30" in name)

    category = await session.get(Category, category_id)
    cat_name = category.name if category else "Категория"

    report = (
        f"✅ <b>Загружено {total_imported} аккаунтов в категорию {cat_name}:</b>\n"
        f"• 7 Days: {t7} шт.\n"
        f"• 15 Days: {t15} шт.\n"
        f"• 30+ Days: {t30} шт.\n"
        f"Дубликатов по SSN пропущено: {duplicates_skipped}"
    )
    return report


@admin_bot_router.message(F.document)
async def process_document_upload(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    doc = message.document
    filename = doc.file_name or "accounts.txt"
    fn_lower = filename.lower()

    valid_exts = (".zip", ".rar", ".txt", ".log")
    if not any(fn_lower.endswith(ext) for ext in valid_exts):
        await message.answer("⚠️ Неподдерживаемый формат файла. Отправьте <b>.zip</b>, <b>.rar</b> или <b>.txt</b> документ.")
        return

    status_msg = await message.answer("⏳ <i>Загрузка и парсинг файла...</i>")

    file_io = io.BytesIO()
    await bot.download(doc, destination=file_io)
    raw_bytes = file_io.getvalue()

    try:
        parsed_accounts = await _parse_upload_bytes(filename, raw_bytes)
    except Exception as e:
        logger.error(f"Error parsing uploaded file {filename}: {e}")
        await status_msg.edit_text(f"⚠️ Ошибка при чтении файла: {e}")
        return

    if not parsed_accounts:
        await status_msg.edit_text("⚠️ В файле не обнаружено валидных строк или логов аккаунтов.")
        return

    state_data = await state.get_data()
    cat_id = state_data.get("upload_category_id")

    if cat_id:
        await state.clear()
        report = await _import_accounts_and_report(session, cat_id, parsed_accounts)
        builder = InlineKeyboardBuilder()
        builder.button(text="📦 Просмотр остатков", callback_data="ab_stock")
        builder.button(text="📥 Загрузить ещё", callback_data="ab_upload_select_cat")
        builder.button(text="🏠 Главное меню", callback_data="ab_main")
        builder.adjust(1)
        await status_msg.edit_text(report, reply_markup=builder.as_markup())
    else:
        # User uploaded file directly without prior category selection
        categories = list((await session.execute(select(Category).order_by(Category.name.asc()))).scalars().all())
        if not categories:
            await status_msg.edit_text("⚠️ В базе нет доступных категорий. Создайте категорию перед загрузкой.")
            return

        if len(categories) == 1:
            report = await _import_accounts_and_report(session, categories[0].id, parsed_accounts)
            builder = InlineKeyboardBuilder()
            builder.button(text="📦 Просмотр остатков", callback_data="ab_stock")
            builder.button(text="🏠 Главное меню", callback_data="ab_main")
            builder.adjust(1)
            await status_msg.edit_text(report, reply_markup=builder.as_markup())
        else:
            serialized = [serialize_account_dict(a) for a in parsed_accounts]
            await state.set_state(AdminBotStates.waiting_upload_category)
            await state.update_data(pending_parsed_accounts=serialized)

            builder = InlineKeyboardBuilder()
            for c in categories:
                builder.button(text=f"🏦 {c.name}", callback_data=f"ab_upload_save_cat:{c.id}")
            builder.button(text="◀️ Отмена", callback_data="ab_stock")
            builder.adjust(1)

            await status_msg.edit_text(
                f"📦 Файл <code>{filename}</code> успешно прочитан (найдено: <b>{len(parsed_accounts)}</b> аккаунтов).\n\n"
                "📁 <b>Выберите категорию для распределения:</b>",
                reply_markup=builder.as_markup(),
            )


@admin_bot_router.callback_query(F.data.startswith("ab_upload_save_cat:"))
async def cb_admin_upload_save_cat(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    cat_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    raw_list = data.get("pending_parsed_accounts", [])
    await state.clear()

    if not raw_list:
        await callback.message.edit_text("Данные для загрузки устарели или не найдены.")
        return

    parsed_accounts = [deserialize_account_dict(d) for d in raw_list]
    report = await _import_accounts_and_report(session, cat_id, parsed_accounts)

    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Просмотр остатков", callback_data="ab_stock")
    builder.button(text="📥 Загрузить ещё", callback_data="ab_upload_select_cat")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await callback.message.edit_text(report, reply_markup=builder.as_markup())


@admin_bot_router.message(AdminBotStates.waiting_upload_file, F.text)
async def process_text_upload(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    cat_id = data.get("upload_category_id")
    await state.clear()

    if not cat_id:
        await message.answer("⚠️ Категория не выбрана.")
        return

    from web.services import parse_account_line
    parsed_accounts = [p for l in message.text.splitlines() if (p := parse_account_line(l))]
    if not parsed_accounts:
        await message.answer("⚠️ Не удалось распознать аккаунты из введенного текста.")
        return

    report = await _import_accounts_and_report(session, cat_id, parsed_accounts)
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Просмотр остатков", callback_data="ab_stock")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await message.answer(report, reply_markup=builder.as_markup())


# --- 5. 💳 Кошелек и Вывод ---


@admin_bot_router.callback_query(F.data == "ab_wallet")
async def cb_admin_wallet(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    balance = float(admin_user.balance) if admin_user else 0.0

    master_addr = config.admin_tron_address or (admin_user.tron_address if admin_user else "Не настроен")

    text = (
        "💳 <b>Кошелек и Вывод средств магазина</b>\n\n"
        f"💰 <b>Внутренний баланс:</b> <b>${balance:.2f} USDT</b>\n"
        f"🎯 <b>Мастер TRON адрес:</b> <code>{master_addr}</code>\n"
        f"🌐 <b>Сеть:</b> <code>{config.tron_network}</code>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 On-chain Sweep", callback_data="ab_sweep")
    builder.button(text="💸 Вывести средства", callback_data="ab_withdraw")
    builder.button(text="◀️ Главное меню", callback_data="ab_main")
    builder.adjust(2, 1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@admin_bot_router.callback_query(F.data == "ab_sweep")
async def cb_admin_sweep(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    await callback.message.edit_text("⏳ <i>Выполняется опрос кошельков покупателей и on-chain перевод TRX...</i>")

    res = await sweep_all_wallets(session)

    builder = InlineKeyboardBuilder()
    builder.button(text="💳 В кошелек", callback_data="ab_wallet")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    if not res.get("success"):
        await callback.message.edit_text(
            f"⚠️ <b>Ошибка On-chain Sweep:</b>\n{res.get('message')}",
            reply_markup=builder.as_markup(),
        )
        return

    msg = (
        "🔄 <b>Результат On-chain Sweep:</b>\n\n"
        f"🔍 Проверено кошельков: <b>{res['checked_count']}</b>\n"
        f"⚡️ Выполнено переводов: <b>{res['swept_count']}</b>\n"
        f"💰 Собрано средств: <b>{res['total_trx']:.4f} TRX</b>\n"
        f"🎯 Адрес зачисления: <code>{res['destination']}</code>\n"
    )
    if res.get("transactions"):
        msg += "\n<b>Транзакции:</b>\n"
        for t in res["transactions"][:5]:
            msg += f"• Юзер <code>{t['user_id']}</code>: {t['amount']:.2f} TRX (<code>{t['tx'][:12]}...</code>)\n"

    await callback.message.edit_text(msg, reply_markup=builder.as_markup())


@admin_bot_router.callback_query(F.data == "ab_withdraw")
async def cb_admin_start_withdraw(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    await callback.answer()

    admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    balance = float(admin_user.balance) if admin_user else 0.0

    if balance <= 0:
        await callback.answer("❌ На балансе магазина нет доступных средств для вывода.", show_alert=True)
        return

    await state.set_state(AdminBotStates.waiting_withdraw_address)
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Отмена", callback_data="ab_wallet")

    await callback.message.edit_text(
        f"💸 <b>Вывод средств из магазина</b>\n\n"
        f"Доступный баланс: <b>${balance:.2f}</b>\n\n"
        "Введите целевой <b>TRON адрес</b> (начинается на 'T', 34 символа):",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.message(AdminBotStates.waiting_withdraw_address)
async def process_withdraw_address(message: Message, state: FSMContext, session: AsyncSession):
    addr = message.text.strip()
    if not is_valid_tron_address(addr):
        await message.answer("⚠️ Некорректный адрес Tron! Он должен начинаться на 'T' и состоять из 34 символов.\nПопробуйте ещё раз:")
        return

    await state.update_data(withdraw_address=addr)
    await state.set_state(AdminBotStates.waiting_withdraw_amount)

    admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    balance = float(admin_user.balance) if admin_user else 0.0

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Отмена", callback_data="ab_wallet")

    await message.answer(
        f"Адрес принят: <code>{addr}</code>\n\n"
        f"Введите сумму для вывода в USD (максимум: <b>${balance:.2f}</b>):",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.message(AdminBotStates.waiting_withdraw_amount)
async def process_withdraw_amount(message: Message, state: FSMContext, session: AsyncSession):
    text_val = message.text.replace(",", ".").strip()
    try:
        amount = float(text_val)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите корректное положительное число (например: <code>50.0</code>):")
        return

    admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    balance = float(admin_user.balance) if admin_user else 0.0

    if amount > balance:
        await message.answer(f"❌ Сумма превышает доступный баланс (${balance:.2f}). Введите меньшую сумму:")
        return

    data = await state.get_data()
    target_addr = data.get("withdraw_address")
    await state.clear()

    admin_user.balance = balance - amount
    await session.commit()

    tx_info = ""
    if config.admin_tron_private_key and config.admin_tron_address:
        # shortcut: отправка эквивалента суммы в TRX при отсутствии оракула
        ok, tx_id = await send_tron_transaction(
            config.admin_tron_address,
            config.admin_tron_private_key,
            target_addr,
            amount_trx=amount,
        )
        if ok:
            tx_info = f"\n⚡️ <b>On-chain TxID:</b> <code>{tx_id}</code>"
        else:
            tx_info = f"\n⚠️ On-chain перевод отклонен: {tx_id}. Списание выполнено во внутреннем балансе."

    builder = InlineKeyboardBuilder()
    builder.button(text="💳 В кошелек", callback_data="ab_wallet")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await message.answer(
        f"✅ <b>Средства успешно выведены!</b>\n\n"
        f"💵 Сумма: <b>${amount:.2f}</b>\n"
        f"🎯 Получатель: <code>{target_addr}</code>\n"
        f"💰 Остаток баланса: <b>${float(admin_user.balance):.2f}</b>"
        f"{tx_info}",
        reply_markup=builder.as_markup(),
    )


# --- 6. 👥 Управление и Рассылка ---


@admin_bot_router.callback_query(F.data == "ab_manage")
async def cb_admin_manage(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()

    mods_count = (await session.scalar(select(func.count(User.id)).where(User.role == "moderator"))) or 0
    users_count = (await session.scalar(select(func.count(User.id)))) or 0

    text = (
        "👥 <b>Управление персоналом и Рассылка</b>\n\n"
        f"🛡 Назначенных модераторов: <b>{mods_count}</b>\n"
        f"👥 Всего пользователей: <b>{users_count}</b>\n\n"
        "<b>Команды управления:</b>\n"
        "• <code>/set_mod &lt;tg_id&gt;</code> — назначить модератора\n"
        "• <code>/del_mod &lt;tg_id&gt;</code> — снять модератора\n"
        "• <code>/mods</code> — список действующих модераторов\n"
        "• <code>/ban &lt;tg_id&gt; &lt;причина&gt;</code> — заблокировать пользователя\n"
        "• <code>/unban &lt;tg_id&gt;</code> — разблокировать пользователя\n"
        "• <code>/broadcast &lt;текст&gt;</code> — рассылка покупателям через основного бота"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🛡 Список модераторов", callback_data="ab_mods_list")
    builder.button(text="📢 Начать рассылку", callback_data="ab_start_broadcast")
    builder.button(text="◀️ Главное меню", callback_data="ab_main")
    builder.adjust(2, 1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@admin_bot_router.callback_query(F.data == "ab_mods_list")
async def cb_admin_mods_list(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    mods = list((await session.execute(select(User).where(User.role == "moderator"))).scalars().all())

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data="ab_manage")

    if not mods:
        await callback.message.edit_text(
            "🛡 В системе пока нет назначенных модераторов.\n\nНазначить: <code>/set_mod &lt;tg_id&gt;</code>",
            reply_markup=builder.as_markup(),
        )
        return

    lines = [f"• @{m.username or '—'} (<code>{m.tg_id}</code>)" for m in mods]
    await callback.message.edit_text(
        "🛡 <b>Список действующих модераторов:</b>\n\n" + "\n".join(lines),
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.callback_query(F.data == "ab_start_broadcast")
async def cb_admin_start_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminBotStates.waiting_broadcast_text)

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Отмена", callback_data="ab_manage")

    await callback.message.edit_text(
        "📢 <b>Рассылка покупателям</b>\n\n"
        "Отправьте текст сообщения, которое будет разослано всем покупателям через основного бота:",
        reply_markup=builder.as_markup(),
    )


async def _execute_broadcast(
    session: AsyncSession,
    text: str,
    target_bot: Bot,
    reply_to_message: Message,
) -> None:
    stmt = select(User.tg_id).where(User.is_banned == False, User.tg_id != config.admin_id)
    uids = list((await session.execute(stmt)).scalars().all())

    if not uids:
        await reply_to_message.answer("👥 В базе данных пока нет пользователей для рассылки.")
        return

    status_msg = await reply_to_message.answer(f"⏳ <i>Выполняется рассылка на {len(uids)} получателей...</i>")
    sent, failed = 0, 0

    for uid in uids:
        try:
            await target_bot.send_message(chat_id=uid, text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    builder = InlineKeyboardBuilder()
    builder.button(text="👥 В управление", callback_data="ab_manage")
    builder.button(text="🏠 Главное меню", callback_data="ab_main")
    builder.adjust(1)

    await status_msg.edit_text(
        f"📢 <b>Рассылка завершена!</b>\n\n"
        f"✅ Успешно доставлено: <b>{sent}</b>\n"
        f"❌ Ошибок (бот заблокирован): <b>{failed}</b>",
        reply_markup=builder.as_markup(),
    )


@admin_bot_router.message(AdminBotStates.waiting_broadcast_text)
async def process_broadcast_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    shop_bot: Bot | None = None,
):
    await state.clear()
    text = message.text or message.caption or ""
    if not text.strip():
        await message.answer("⚠️ Текст рассылки не может быть пустым.")
        return

    target_bot = get_target_shop_bot(bot, shop_bot)
    await _execute_broadcast(session, text, target_bot, message)


@admin_bot_router.message(Command("broadcast"))
async def cmd_admin_broadcast(
    message: Message,
    session: AsyncSession,
    bot: Bot,
    shop_bot: Bot | None = None,
):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Формат: <code>/broadcast &lt;текст сообщения&gt;</code>")
        return

    target_bot = get_target_shop_bot(bot, shop_bot)
    await _execute_broadcast(session, parts[1].strip(), target_bot, message)


@admin_bot_router.message(Command("set_mod"))
async def cmd_set_moderator(message: Message, session: AsyncSession):
    is_superadmin = (message.from_user.id == config.admin_id) or (
        (await session.scalar(select(User).where(User.tg_id == message.from_user.id)))
        and (await session.scalar(select(User).where(User.tg_id == message.from_user.id))).role == "admin"
    )
    if not is_superadmin:
        await message.answer("❌ Только Главный Администратор может назначать модераторов.")
        return

    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/set_mod &lt;tg_id&gt;</code>")
        return

    target_tg_id = int(parts[1])
    user = await session.scalar(select(User).where(User.tg_id == target_tg_id))
    if not user:
        await message.answer(f"Пользователь с TG ID {target_tg_id} не найден в базе данных.")
        return

    user.role = "moderator"
    await session.commit()
    await message.answer(f"✅ Пользователю @{user.username or '—'} (<code>{target_tg_id}</code>) присвоена роль <b>Модератор</b>.")


@admin_bot_router.message(Command("del_mod"))
async def cmd_del_moderator(message: Message, session: AsyncSession):
    is_superadmin = (message.from_user.id == config.admin_id) or (
        (await session.scalar(select(User).where(User.tg_id == message.from_user.id)))
        and (await session.scalar(select(User).where(User.tg_id == message.from_user.id))).role == "admin"
    )
    if not is_superadmin:
        await message.answer("❌ Только Главный Администратор может снимать модераторов.")
        return

    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/del_mod &lt;tg_id&gt;</code>")
        return

    target_tg_id = int(parts[1])
    user = await session.scalar(select(User).where(User.tg_id == target_tg_id))
    if not user:
        await message.answer(f"Пользователь с TG ID {target_tg_id} не найден.")
        return

    user.role = "buyer"
    await session.commit()
    await message.answer(f"✅ Роль пользователя @{user.username or '—'} (<code>{target_tg_id}</code>) сброшена до <b>Покупатель</b>.")


@admin_bot_router.message(Command("mods"))
async def cmd_list_moderators(message: Message, session: AsyncSession):
    mods = list((await session.execute(select(User).where(User.role == "moderator"))).scalars().all())
    if not mods:
        await message.answer("🛡 В системе пока нет назначенных модераторов.\nНазначить: <code>/set_mod &lt;tg_id&gt;</code>")
        return
    lines = [f"• @{m.username or '—'} (<code>{m.tg_id}</code>)" for m in mods]
    await message.answer("🛡 <b>Список модераторов магазина:</b>\n\n" + "\n".join(lines))


@admin_bot_router.message(Command("ban"))
async def cmd_ban_user(message: Message, session: AsyncSession):
    parts = message.text.strip().split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/ban &lt;tg_id&gt; &lt;причина&gt;</code>")
        return

    target_tg_id = int(parts[1])
    reason = parts[2].strip() if len(parts) > 2 else "Нарушение регламента площадки"

    user = await session.scalar(select(User).where(User.tg_id == target_tg_id))
    if not user:
        from bot.services.tron import generate_tron_wallet

        t_addr, t_key = generate_tron_wallet()
        user = User(
            tg_id=target_tg_id,
            role="buyer",
            is_banned=True,
            banned_reason=reason,
            tron_address=t_addr,
            tron_private_key=t_key,
        )
        session.add(user)
    else:
        user.is_banned = True
        user.banned_reason = reason

    await session.commit()
    await message.answer(f"⛔️ Пользователь @{user.username or '—'} (<code>{target_tg_id}</code>) <b>заблокирован</b>.\nПричина: <i>{reason}</i>")


@admin_bot_router.message(Command("unban"))
async def cmd_unban_user(message: Message, session: AsyncSession):
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/unban &lt;tg_id&gt;</code>")
        return

    target_tg_id = int(parts[1])
    user = await session.scalar(select(User).where(User.tg_id == target_tg_id))
    if not user:
        await message.answer(f"Пользователь с TG ID {target_tg_id} не найден в базе.")
        return

    user.is_banned = False
    user.banned_reason = None
    await session.commit()
    await message.answer(f"✅ Пользователь @{user.username or '—'} (<code>{target_tg_id}</code>) <b>разблокирован</b>.")
