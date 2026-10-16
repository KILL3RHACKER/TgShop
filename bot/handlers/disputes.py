from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bot.config import config
from bot.database.models import Order, OrderStatus, DisputeMessage, User
from bot.keyboards.callbacks import (
    OrderCD,
    OpenDisputeCD,
    ReplyDisputeCD,
    ResolveDisputeCD,
)
from bot.keyboards.inline import (
    get_my_orders_kb,
    get_order_detail_kb,
    get_dispute_admin_kb,
)

disputes_router = Router()

class DisputeStates(StatesGroup):
    waiting_client_message = State()
    waiting_admin_reply = State()

@disputes_router.callback_query(F.data == "my_orders")
async def show_my_orders(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    result = await session.execute(
        select(Order)
        .where(Order.user_id == callback.from_user.id)
        .order_by(Order.created_at.desc())
        .limit(10)
    )
    orders = list(result.scalars().all())

    if not orders:
        await callback.message.edit_text(
            "У вас пока нет покупок.",
            reply_markup=get_my_orders_kb([])
        )
        return

    text = "📦 <b>Ваши последние заказы:</b>\n\nВыберите заказ для просмотра или открытия диспута:"
    await callback.message.edit_text(text, reply_markup=get_my_orders_kb(orders))

@disputes_router.callback_query(OrderCD.filter())
async def show_order_detail(
    callback: CallbackQuery,
    callback_data: OrderCD,
    session: AsyncSession
):
    await callback.answer()
    order = await session.get(Order, callback_data.id)
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    now = datetime.utcnow()
    is_hold = order.status == OrderStatus.HOLD
    can_dispute = is_hold and order.hold_until > now

    if is_hold:
        remaining = order.hold_until - now
        hours, remainder = divmod(int(remaining.total_seconds()), 3600)
        minutes = remainder // 60
        status_text = f"⏳ <b>В холде на проверке</b> (осталось {hours}ч {minutes}м)"
    elif order.status == OrderStatus.DISPUTED:
        status_text = "🚨 <b>Открыт диспут</b> (на рассмотрении поддержки)"
    elif order.status == OrderStatus.COMPLETED:
        status_text = "✅ <b>Завершен</b> (проверка пройдена, холд снят)"
    elif order.status == OrderStatus.REFUNDED:
        status_text = "↩️ <b>Возврат средств</b> (деньги возвращены на баланс)"
    else:
        status_text = order.status.value

    acc_data = order.account.data if order.account else "—"
    prod_name = order.product.name if order.product else "—"

    text = (
        f"📋 <b>Заказ #ORDER-{order.id}</b>\n\n"
        f"🏷 Товар: <b>{prod_name}</b>\n"
        f"💵 Сумма: <b>${order.amount:.2f}</b>\n"
        f"📦 Аккаунт: <code>{acc_data}</code>\n"
        f"📅 Дата покупки: {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📊 Статус: {status_text}\n"
    )

    if order.messages:
        text += f"\n💬 <b>Сообщений в споре:</b> {len(order.messages)}"

    await callback.message.edit_text(
        text,
        reply_markup=get_order_detail_kb(order, can_dispute=can_dispute)
    )

@disputes_router.callback_query(OpenDisputeCD.filter())
async def start_open_dispute(
    callback: CallbackQuery,
    callback_data: OpenDisputeCD,
    state: FSMContext,
    session: AsyncSession
):
    await callback.answer()
    order = await session.get(Order, callback_data.order_id)
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    now = datetime.utcnow()
    if order.status == OrderStatus.COMPLETED or (order.status == OrderStatus.HOLD and order.hold_until <= now):
        await callback.answer("❌ Время на открытие диспута (24 часа) истекло.", show_alert=True)
        return

    await state.set_state(DisputeStates.waiting_client_message)
    await state.update_data(order_id=order.id)

    prompt = (
        f"⚠️ <b>Чат спора по заказу #ORDER-{order.id}</b>\n\n"
        f"Напишите сообщение с описанием проблемы и прикрепите доказательства (текст или скриншот ошибки).\n\n"
        f"<i>Сообщение будет мгновенно передано администрации магазина.</i>"
    )
    await callback.message.answer(prompt)

@disputes_router.message(DisputeStates.waiting_client_message)
async def process_client_dispute_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot
):
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.clear()
        return

    order = await session.get(Order, order_id)
    if not order:
        await state.clear()
        await message.answer("Ошибка: заказ не найден.")
        return

    text = message.text or message.caption or ""
    photo_id = message.photo[-1].file_id if message.photo else None

    if not text and not photo_id:
        await message.answer("Пожалуйста, отправьте текст или скриншот:")
        return

    # Update order to DISPUTED if it was in HOLD
    if order.status == OrderStatus.HOLD:
        order.status = OrderStatus.DISPUTED
        order.disputed_at = datetime.utcnow()

    # Save dispute message
    disp_msg = DisputeMessage(
        order_id=order.id,
        sender_id=message.from_user.id,
        sender_role="client",
        text=text,
        photo_file_id=photo_id,
    )
    session.add(disp_msg)
    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>Сообщение передано в диспут #ORDER-{order.id}!</b>\n\n"
        f"Администратор уведомлен и рассмотрит доказательства. Вы получите ответ здесь в боте."
    )

    # Notify admin with action buttons
    admin_text = (
        f"🚨 <b>ДИСПУТ ПО ЗАКАЗУ #ORDER-{order.id}</b>\n\n"
        f"👤 Покупатель: @{message.from_user.username or '—'} (<code>{order.user_id}</code>)\n"
        f"💵 Сумма: <b>${order.amount:.2f} USDT</b>\n"
        f"📦 Товар: <code>{order.account.data if order.account else '—'}</code>\n\n"
        f"💬 <b>Сообщение покупателя:</b>\n{text or '<i>[Только скриншот]</i>'}"
    )

    kb = get_dispute_admin_kb(order.id)
    try:
        if photo_id:
            await bot.send_photo(
                chat_id=config.admin_id,
                photo=photo_id,
                caption=admin_text,
                reply_markup=kb
            )
        else:
            await bot.send_message(
                chat_id=config.admin_id,
                text=admin_text,
                reply_markup=kb
            )
    except Exception:
        pass

@disputes_router.callback_query(ReplyDisputeCD.filter())
async def admin_start_reply(
    callback: CallbackQuery,
    callback_data: ReplyDisputeCD,
    state: FSMContext
):
    if callback.from_user.id != config.admin_id:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    await callback.answer()
    await state.set_state(DisputeStates.waiting_admin_reply)
    await state.update_data(order_id=callback_data.order_id)
    await callback.message.answer(
        f"✍️ <b>Введите ответ покупателю по заказу #ORDER-{callback_data.order_id}:</b>"
    )

@disputes_router.message(DisputeStates.waiting_admin_reply)
async def process_admin_reply(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot
):
    if message.from_user.id != config.admin_id:
        return

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.clear()
        return

    order = await session.get(Order, order_id)
    if not order:
        await state.clear()
        await message.answer("Заказ не найден.")
        return

    text = message.text or message.caption or ""
    photo_id = message.photo[-1].file_id if message.photo else None

    # Save dispute message
    disp_msg = DisputeMessage(
        order_id=order.id,
        sender_id=message.from_user.id,
        sender_role="admin",
        text=text,
        photo_file_id=photo_id,
    )
    session.add(disp_msg)
    await session.commit()
    await state.clear()

    await message.answer(f"✅ Ответ отправлен покупателю по заказу #ORDER-{order.id}.")

    # Send to client
    client_notice = (
        f"👨‍💼 <b>Ответ поддержки по спору #ORDER-{order.id}:</b>\n\n"
        f"{text or '<i>[Вложение]</i>'}"
    )
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="💬 Ответить в спор", callback_data=OpenDisputeCD(order_id=order.id))
        kb.adjust(1)

        if photo_id:
            await bot.send_photo(
                chat_id=order.user_id,
                photo=photo_id,
                caption=client_notice,
                reply_markup=kb.as_markup()
            )
        else:
            await bot.send_message(
                chat_id=order.user_id,
                text=client_notice,
                reply_markup=kb.as_markup()
            )
    except Exception:
        pass

@disputes_router.callback_query(ResolveDisputeCD.filter())
async def resolve_dispute(
    callback: CallbackQuery,
    callback_data: ResolveDisputeCD,
    session: AsyncSession,
    bot: Bot
):
    if callback.from_user.id != config.admin_id:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    await callback.answer()
    order = await session.get(Order, callback_data.order_id)
    if not order:
        await callback.message.edit_text("Заказ не найден.")
        return

    user = await session.scalar(select(User).where(User.tg_id == order.user_id))

    if callback_data.action == "refund":
        order.status = OrderStatus.REFUNDED
        order.resolved_at = datetime.utcnow()
        if user:
            user.balance = float(user.balance) + float(order.amount)
        await session.commit()

        await callback.message.edit_text(
            f"✅ <b>Диспут #ORDER-{order.id} решен в пользу покупателя.</b>\n"
            f"Сумма ${order.amount:.2f} возвращена на баланс покупателя @{user.username if user else ''}."
        )

        try:
            await bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"🎉 <b>Диспут по заказу #ORDER-{order.id} решен в вашу пользу!</b>\n\n"
                    f"Средства в размере <b>${order.amount:.2f} USDT</b> возвращены на ваш баланс магазина."
                )
            )
        except Exception:
            pass

    elif callback_data.action == "reject":
        order.status = OrderStatus.COMPLETED
        order.resolved_at = datetime.utcnow()
        await session.commit()

        await callback.message.edit_text(
            f"❌ <b>Диспут #ORDER-{order.id} закрыт в пользу магазина.</b>\n"
            f"Средства окончательно разморожены."
        )

        try:
            await bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"❌ <b>Диспут по заказу #ORDER-{order.id} закрыт администратором.</b>\n\n"
                    f"Решение принято в пользу магазина. Средства не подлежат возврату."
                )
            )
        except Exception:
            pass
