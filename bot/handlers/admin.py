from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from bot.config import config
from bot.database.models import User, TopUpTicket, TicketStatus, Order, OrderStatus
from bot.keyboards.callbacks import TicketActionCD, OrderCD
from bot.keyboards.inline import get_dispute_admin_kb
from bot.services.ticket import approve_ticket, reject_ticket
from bot.services.tron import is_valid_tron_address, send_tron_transaction, sweep_all_wallets

admin_router = Router()

class AdminWithdrawStates(StatesGroup):
    waiting_address = State()
    waiting_amount = State()

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

    admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    admin_balance = float(admin_user.balance) if admin_user else 0.0

    builder = InlineKeyboardBuilder()
    builder.button(text=f"📋 Тикеты пополнения ({pending_tickets})", callback_data="admin_pending_tickets")
    builder.button(text=f"🚨 Активные диспуты ({dispute_count})", callback_data="admin_disputes")
    builder.button(text=f"⏳ Заказы в холде ({hold_count})", callback_data="admin_hold_orders")
    builder.button(text="💸 Вывести средства", callback_data="admin_withdraw")
    builder.button(text="🔄 On-chain Sweep", callback_data="admin_sweep")
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    builder.adjust(1)

    text = (
        "⚙️ <b>Панель администратора</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"⏳ Ожидают пополнения (тикеты): <b>{pending_tickets}</b>\n"
        f"🚨 Открытых диспутов: <b>{dispute_count}</b>\n"
        f"🔒 Заказов в холде 24ч: <b>{hold_count}</b> (${float(hold_sum):.2f})\n"
        f"💰 Завершенных продаж: <b>${float(completed_sum):.2f}</b>\n"
        f"💳 <b>Внутренний баланс магазина:</b> <b>${admin_balance:.2f}</b>"
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

@admin_router.message(Command("set_mod"))
async def set_moderator_cmd(message: Message, session: AsyncSession):
    if not is_admin_check(message.from_user.id):
        return
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/set_mod {tg_id}</code>")
        return
    target_tg_id = int(parts[1])
    user = await session.scalar(select(User).where(User.tg_id == target_tg_id))
    if not user:
        await message.answer(f"Пользователь с TG ID {target_tg_id} не найден в базе бота.")
        return
    user.role = "moderator"
    await session.commit()
    await message.answer(f"✅ Пользователю @{user.username or '—'} (<code>{target_tg_id}</code>) присвоена роль <b>Модератор</b>.")

@admin_router.message(Command("del_mod"))
async def del_moderator_cmd(message: Message, session: AsyncSession):
    if not is_admin_check(message.from_user.id):
        return
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/del_mod {tg_id}</code>")
        return
    target_tg_id = int(parts[1])
    user = await session.scalar(select(User).where(User.tg_id == target_tg_id))
    if not user:
        await message.answer(f"Пользователь с TG ID {target_tg_id} не найден.")
        return
    user.role = "buyer"
    await session.commit()
    await message.answer(f"✅ Роль пользователя @{user.username or '—'} (<code>{target_tg_id}</code>) сброшена до <b>Покупатель</b>.")

@admin_router.message(Command("mods"))
async def list_moderators_cmd(message: Message, session: AsyncSession):
    if not is_admin_check(message.from_user.id):
        return
    res = await session.execute(select(User).where(User.role == "moderator"))
    mods = list(res.scalars().all())
    if not mods:
        await message.answer("В системе пока нет назначенных модераторов.\nНазначить: <code>/set_mod {tg_id}</code>")
        return
    lines = [f"• @{m.username or '—'} (<code>{m.tg_id}</code>)" for m in mods]
    await message.answer("🛡 <b>Список модераторов магазина:</b>\n\n" + "\n".join(lines))

@admin_router.callback_query(F.data == "admin_sweep")
async def process_admin_sweep(callback: CallbackQuery, session: AsyncSession):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text("⏳ <i>Выполняется опрос кошельков и on-chain перевод средств...</i>")

    res = await sweep_all_wallets(session)

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ В админ панель", callback_data="admin_menu")

    if not res.get("success"):
        await callback.message.edit_text(
            f"⚠️ <b>Ошибка sweep:</b>\n{res.get('message')}",
            reply_markup=builder.as_markup()
        )
        return

    msg = (
        f"🔄 <b>Результат On-chain Sweep:</b>\n\n"
        f"🔍 Проверено кошельков: <b>{res['checked_count']}</b>\n"
        f"⚡️ Выполнено переводов: <b>{res['swept_count']}</b>\n"
        f"💰 Собрано: <b>{res['total_trx']:.4f} TRX</b>\n"
        f"🎯 Кошелек зачисления: <code>{res['destination']}</code>\n"
    )
    if res.get("transactions"):
        msg += "\n<b>Транзакции:</b>\n"
        for t in res["transactions"][:5]:
            msg += f"• Юзер <code>{t['user_id']}</code>: {t['amount']:.2f} TRX (<code>{t['tx'][:12]}...</code>)\n"

    await callback.message.edit_text(msg, reply_markup=builder.as_markup())

@admin_router.callback_query(F.data == "admin_withdraw")
async def start_admin_withdraw(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not is_admin_check(callback.from_user.id):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return
    await callback.answer()

    admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    balance = float(admin_user.balance) if admin_user else 0.0

    if balance <= 0:
        await callback.answer("❌ На балансе магазина нет доступных средств для вывода.", show_alert=True)
        return

    await state.set_state(AdminWithdrawStates.waiting_address)
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Отмена", callback_data="admin_menu")

    await callback.message.edit_text(
        f"💸 <b>Вывод средств из магазина</b>\n\n"
        f"Доступный баланс: <b>${balance:.2f}</b>\n\n"
        f"Введите целевой <b>TRON адрес</b> (начинается на 'T', 34 символа):",
        reply_markup=builder.as_markup()
    )

@admin_router.message(AdminWithdrawStates.waiting_address)
async def process_withdraw_address(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin_check(message.from_user.id):
        return

    addr = message.text.strip()
    if not is_valid_tron_address(addr):
        await message.answer("⚠️ Некорректный адрес Tron! Адрес должен начинаться на 'T' и состоять из 34 символов.\nПопробуйте ещё раз:")
        return

    await state.update_data(withdraw_address=addr)
    await state.set_state(AdminWithdrawStates.waiting_amount)

    admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
    balance = float(admin_user.balance) if admin_user else 0.0

    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Отмена", callback_data="admin_menu")

    await message.answer(
        f"Адрес принят: <code>{addr}</code>\n\n"
        f"Введите сумму для вывода в USD (максимум: <b>${balance:.2f}</b>):",
        reply_markup=builder.as_markup()
    )

@admin_router.message(AdminWithdrawStates.waiting_amount)
async def process_withdraw_amount(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin_check(message.from_user.id):
        return

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

    # Списываем баланс
    admin_user.balance = balance - amount
    await session.commit()

    tx_info = ""
    if config.admin_tron_private_key and config.admin_tron_address:
        # shortcut: курс 1 USD = N TRX для вывода; при отсутствии оракула отправляется эквивалент TRX
        ok, tx_id = await send_tron_transaction(
            config.admin_tron_address,
            config.admin_tron_private_key,
            target_addr,
            amount_trx=amount
        )
        if ok:
            tx_info = f"\n⚡️ <b>On-chain TxID:</b> <code>{tx_id}</code>"
        else:
            tx_info = f"\n⚠️ On-chain перевод не удался: {tx_id}. Списание выполнено во внутреннем балансе."

    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ В админ панель", callback_data="admin_menu")

    await message.answer(
        f"✅ <b>Средства успешно выведены!</b>\n\n"
        f"💵 Сумма: <b>${amount:.2f}</b>\n"
        f"🎯 Получатель: <code>{target_addr}</code>\n"
        f"💰 Остаток баланса: <b>${float(admin_user.balance):.2f}</b>"
        f"{tx_info}",
        reply_markup=builder.as_markup()
    )
