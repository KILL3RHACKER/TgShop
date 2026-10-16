import math
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

from bot.config import config
from bot.database.models import Order, OrderStatus, DisputeMessage, User, OrderReview
from bot.keyboards.callbacks import (
    OrderCD,
    OpenDisputeCD,
    ReplyDisputeCD,
    ResolveDisputeCD,
    AskQuestionCD,
    MyOrdersTabCD,
    StartReviewCD,
    RateOrderCD,
    SkipReviewCD,
)
from bot.keyboards.inline import (
    get_my_orders_tabs_kb,
    get_order_detail_kb,
    get_dispute_admin_kb,
    get_review_rating_kb,
)

disputes_router = Router()

class DisputeStates(StatesGroup):
    waiting_client_dispute = State()
    waiting_client_question = State()
    waiting_admin_reply = State()
    waiting_review_comment = State()

# --- Вкладки в «Мои заказы» ---

@disputes_router.callback_query(F.data == "my_orders")
async def show_my_orders_default(callback: CallbackQuery, session: AsyncSession):
    await render_orders_tab(callback, session, current_tab="attention")

@disputes_router.callback_query(MyOrdersTabCD.filter())
async def show_my_orders_tab(
    callback: CallbackQuery,
    callback_data: MyOrdersTabCD,
    session: AsyncSession
):
    await render_orders_tab(callback, session, current_tab=callback_data.tab)

async def render_orders_tab(callback: CallbackQuery, session: AsyncSession, current_tab: str):
    await callback.answer()
    user_id = callback.from_user.id

    # 1. Заказы, ожидающие внимания: HOLD, DISPUTED, QUESTION
    att_stmt = (
        select(Order)
        .where(
            Order.user_id == user_id,
            Order.status.in_([OrderStatus.HOLD, OrderStatus.DISPUTED, OrderStatus.QUESTION])
        )
        .order_by(Order.created_at.desc())
    )
    att_res = await session.execute(att_stmt)
    attention_orders = list(att_res.scalars().all())

    # 2. Завершенные заказы: COMPLETED, REFUNDED
    comp_stmt = (
        select(Order)
        .where(
            Order.user_id == user_id,
            Order.status.in_([OrderStatus.COMPLETED, OrderStatus.REFUNDED])
        )
        .order_by(Order.created_at.desc())
    )
    comp_res = await session.execute(comp_stmt)
    completed_orders = list(comp_res.scalars().all())

    displayed_orders = attention_orders if current_tab == "attention" else completed_orders
    tab_title = "⏳ Ожидают внимания" if current_tab == "attention" else "✅ Завершенные"
    tab_desc = (
        "Заказы на 24-часовой гарантии, активные диспуты и вопросы."
        if current_tab == "attention"
        else "Завершенные заказы. Вы можете оставить отзыв с оценкой от 1 до 5 звезд."
    )

    text = (
        f"📦 <b>Мои заказы &rarr; {tab_title}</b>\n\n"
        f"{tab_desc}\n\n"
        f"<i>Выберите заказ для просмотра деталей:</i>"
    )

    kb = get_my_orders_tabs_kb(
        current_tab=current_tab,
        orders=displayed_orders,
        attention_count=len(attention_orders),
        completed_count=len(completed_orders)
    )

    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)

# --- Карточка заказа ---

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

    # Формирование статуса и SLA таймеров
    if is_hold:
        remaining = max(timedelta(0), order.hold_until - now)
        hours, rem = divmod(int(remaining.total_seconds()), 3600)
        minutes = rem // 60
        status_text = f"⏳ <b>В холде на проверке</b> (осталось {hours}ч {minutes}м)"
    elif order.status == OrderStatus.DISPUTED:
        sla_info = ""
        if order.dispute_deadline:
            rem_sla = max(timedelta(0), order.dispute_deadline - now)
            h, r = divmod(int(rem_sla.total_seconds()), 3600)
            m = r // 60
            if order.last_reply_by == "admin":
                sla_info = f"\n⏱ <b>Очередь ответа: Покупатель</b> (осталось {h}ч {m}м, иначе закрытие спора)"
            else:
                sla_info = f"\n⏱ <b>Очередь ответа: Магазин</b> (осталось {h}ч {m}м, иначе авто-возврат средств)"
        status_text = f"🚨 <b>Открыт диспут</b>{sla_info}"
    elif order.status == OrderStatus.QUESTION:
        status_text = "❓ <b>Задан вопрос по заказу</b> (ожидается консультация)"
    elif order.status == OrderStatus.REFUNDED:
        status_text = "↩️ <b>Возврат средств</b> (деньги возвращены на баланс)"
    elif order.status == OrderStatus.COMPLETED:
        status_text = "✅ <b>Завершен</b> (холд закрыт)"
    else:
        status_text = order.status.value

    acc_data = order.account.data if order.account else "—"
    prod_name = order.product.name if order.product else "—"

    text = (
        f"📋 <b>Заказ {order.order_code}</b>\n\n"
        f"🏷 Товар: <b>{prod_name}</b>\n"
        f"💵 Сумма: <b>${order.amount:.2f}</b>\n"
        f"📦 Аккаунт: <code>{acc_data}</code>\n"
        f"📅 Дата покупки: {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📊 Статус: {status_text}\n"
    )

    if order.messages:
        text += f"\n💬 <b>Сообщений в диалоге:</b> {len(order.messages)}"

    # Информация об отзыве
    has_review = order.review is not None
    if has_review:
        rev = order.review
        dispute_tag = " <i>[После диспута]</i>" if rev.is_after_dispute else ""
        text += (
            f"\n\n⭐️ <b>Ваш отзыв:</b> {rev.rating}/5{dispute_tag}\n"
            f"💬 <i>\"{rev.comment or 'Без текста'}\"</i>"
        )

    await callback.message.edit_text(
        text,
        reply_markup=get_order_detail_kb(order, can_dispute=can_dispute, has_review=has_review)
    )

# --- 1. Открытие диспута (Kraken SLA 24h) ---

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

    await state.set_state(DisputeStates.waiting_client_dispute)
    await state.update_data(order_id=order.id)

    prompt = (
        f"⚠️ <b>ЧАТ ДИСПУТА ПО ЗАКАЗУ {order.order_code}</b>\n\n"
        "Опишите проблему с аккаунтом и прикрепите доказательства (скриншот первой попытки входа).\n\n"
        "⚖️ <b>Регламент Kraken SLA:</b>\n"
        "• На ответ у каждой стороны есть ровно 24 часа.\n"
        "• Если магазин не ответит за 24ч — средства автоматически вернутся вам.\n"
        "• Если вы не ответите за 24ч — спор закроется в пользу магазина.\n\n"
        "<i>Отправьте сообщение текстом или со скриншотом:</i>"
    )
    await callback.message.answer(prompt)

@disputes_router.message(DisputeStates.waiting_client_dispute)
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

    now = datetime.utcnow()
    order.status = OrderStatus.DISPUTED
    order.has_dispute = True
    if not order.disputed_at:
        order.disputed_at = now

    # Обновление SLA таймеров
    order.last_reply_by = "client"
    order.last_reply_at = now
    order.dispute_deadline = now + timedelta(hours=24)

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
        f"✅ <b>Сообщение отправлено в диспут {order.order_code}!</b>\n\n"
        f"Магазин уведомлен. Время на ответ поддержки — 24 часа."
    )

    # Уведомление администратора
    admin_text = (
        f"🚨 <b>ДИСПУТ ПО ЗАКАЗУ {order.order_code}</b>\n\n"
        f"👤 Покупатель: @{message.from_user.username or '—'} (<code>{order.user_id}</code>)\n"
        f"💵 Сумма: <b>${order.amount:.2f} USDT</b>\n"
        f"📦 Товар: <code>{order.account.data if order.account else '—'}</code>\n"
        f"⏱ <b>SLA: до {order.dispute_deadline.strftime('%d.%m %H:%M UTC')} (24h)</b>\n\n"
        f"💬 <b>Сообщение:</b>\n{text or '<i>[Только скриншот]</i>'}"
    )

    kb = get_dispute_admin_kb(order.id)
    try:
        if photo_id:
            await bot.send_photo(chat_id=config.admin_id, photo=photo_id, caption=admin_text, reply_markup=kb)
        else:
            await bot.send_message(chat_id=config.admin_id, text=admin_text, reply_markup=kb)
    except Exception:
        pass

# --- 2. Задать вопрос по заказу ---

@disputes_router.callback_query(AskQuestionCD.filter())
async def start_ask_question(
    callback: CallbackQuery,
    callback_data: AskQuestionCD,
    state: FSMContext,
    session: AsyncSession
):
    await callback.answer()
    order = await session.get(Order, callback_data.order_id)
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    await state.set_state(DisputeStates.waiting_client_question)
    await state.update_data(order_id=order.id)

    prompt = (
        f"❓ <b>ВОПРОС ПО ЗАКАЗУ {order.order_code}</b>\n\n"
        f"Напишите ваш вопрос (консультация по входу, прокси, юзер-агенту или формату данных).\n\n"
        f"<i>Примечание: обращение с вопросом не останавливает гарантийный холд заказа.</i>\n\n"
        f"Отправьте ваш вопрос сообщением:"
    )
    await callback.message.answer(prompt)

@disputes_router.message(DisputeStates.waiting_client_question)
async def process_client_question_message(
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
        await message.answer("Заказ не найден.")
        return

    text = message.text or message.caption or ""
    photo_id = message.photo[-1].file_id if message.photo else None

    if not text and not photo_id:
        await message.answer("Пожалуйста, отправьте текст вопроса:")
        return

    now = datetime.utcnow()
    if order.status == OrderStatus.HOLD:
        order.status = OrderStatus.QUESTION

    order.last_reply_by = "client"
    order.last_reply_at = now

    disp_msg = DisputeMessage(
        order_id=order.id,
        sender_id=message.from_user.id,
        sender_role="client",
        text=f"[ВОПРОС] {text}",
        photo_file_id=photo_id,
    )
    session.add(disp_msg)
    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>Ваш вопрос по заказу {order.order_code} отправлен поддержке!</b>\n"
        f"Ответ поступит в этот чат."
    )

    admin_text = (
        f"❓ <b>ВОПРОС ПО ЗАКАЗУ {order.order_code}</b>\n\n"
        f"👤 Покупатель: @{message.from_user.username or '—'} (<code>{order.user_id}</code>)\n"
        f"💵 Сумма: <b>${order.amount:.2f} USDT</b>\n"
        f"📦 Товар: <code>{order.account.data if order.account else '—'}</code>\n\n"
        f"💬 <b>Вопрос покупателя:</b>\n{text}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить на вопрос", callback_data=ReplyDisputeCD(order_id=order.id))
    builder.adjust(1)

    try:
        if photo_id:
            await bot.send_photo(chat_id=config.admin_id, photo=photo_id, caption=admin_text, reply_markup=builder.as_markup())
        else:
            await bot.send_message(chat_id=config.admin_id, text=admin_text, reply_markup=builder.as_markup())
    except Exception:
        pass

# --- 3. Ответ администратора в чат ---

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
        f"✍️ <b>Введите ответ покупателю по заказу #{callback_data.order_id}:</b>"
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

    now = datetime.utcnow()
    order.last_reply_by = "admin"
    order.last_reply_at = now
    if order.status == OrderStatus.DISPUTED:
        order.dispute_deadline = now + timedelta(hours=24)

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

    await message.answer(f"✅ Ответ отправлен покупателю по заказу {order.order_code}.")

    # Уведомление клиента с возможностью продолжить диалог
    client_notice = (
        f"👨‍💼 <b>Ответ поддержки по заказу {order.order_code}:</b>\n\n"
        f"{text or '<i>[Вложение]</i>'}\n\n"
        f"<i>У вас есть 24 часа на ответ в случае продолжения спора.</i>"
    )

    builder = InlineKeyboardBuilder()
    if order.status == OrderStatus.DISPUTED:
        builder.button(text="💬 Ответить в диспут", callback_data=OpenDisputeCD(order_id=order.id))
    else:
        builder.button(text="💬 Ответить на сообщение", callback_data=AskQuestionCD(order_id=order.id))
    builder.adjust(1)

    try:
        if photo_id:
            await bot.send_photo(chat_id=order.user_id, photo=photo_id, caption=client_notice, reply_markup=builder.as_markup())
        else:
            await bot.send_message(chat_id=order.user_id, text=client_notice, reply_markup=builder.as_markup())
    except Exception:
        pass

# --- 4. Решение диспута (возврат / закрытие в пользу магазина) ---

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
            f"✅ <b>Диспут {order.order_code} решен в пользу покупателя.</b>\n"
            f"Сумма ${order.amount:.2f} возвращена на баланс покупателя @{user.username if user else ''}."
        )

        try:
            builder = InlineKeyboardBuilder()
            builder.button(text="⭐️ Оставить отзыв", callback_data=StartReviewCD(order_id=order.id))
            builder.adjust(1)

            await bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"🎉 <b>Диспут по заказу {order.order_code} решен в вашу пользу!</b>\n\n"
                    f"Средства в размере <b>${order.amount:.2f} USDT</b> возвращены на ваш баланс магазина.\n\n"
                    f"Вы также можете оценить заказ и работу поддержки:"
                ),
                reply_markup=builder.as_markup()
            )
        except Exception:
            pass

    elif callback_data.action == "reject":
        order.status = OrderStatus.COMPLETED
        order.resolved_at = datetime.utcnow()
        await session.commit()

        await callback.message.edit_text(
            f"❌ <b>Диспут {order.order_code} закрыт в пользу магазина.</b>\n"
            f"Средства разморожены."
        )

        try:
            builder = InlineKeyboardBuilder()
            builder.button(text="⭐️ Оставить отзыв", callback_data=StartReviewCD(order_id=order.id))
            builder.adjust(1)

            await bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"❌ <b>Диспут по заказу {order.order_code} закрыт администратором.</b>\n\n"
                    f"Решение принято в пользу магазина. Средства не подлежат возврату."
                ),
                reply_markup=builder.as_markup()
            )
        except Exception:
            pass

# --- 5. Рейтинг и отзывы (1-5 звезд, опциональный комментарий, метка [После диспута]) ---

@disputes_router.callback_query(StartReviewCD.filter())
async def start_order_review(
    callback: CallbackQuery,
    callback_data: StartReviewCD,
    session: AsyncSession
):
    await callback.answer()
    order = await session.get(Order, callback_data.order_id)
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    if order.review:
        await callback.answer("Вы уже оставили отзыв по этому заказу.", show_alert=True)
        return

    text = (
        f"⭐️ <b>ОТЗЫВ ПО ЗАКАЗУ {order.order_code}</b>\n\n"
        f"Пожалуйста, оцените качество товара от 1 до 5 звезд:"
    )
    await callback.message.answer(text, reply_markup=get_review_rating_kb(order.id))

@disputes_router.callback_query(RateOrderCD.filter())
async def process_rate_order(
    callback: CallbackQuery,
    callback_data: RateOrderCD,
    state: FSMContext,
    session: AsyncSession
):
    await callback.answer()
    order = await session.get(Order, callback_data.order_id)
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    if order.review:
        await callback.answer("Отзыв уже оставлен.", show_alert=True)
        return

    is_after_disp = bool(order.has_dispute or order.disputed_at)
    review = OrderReview(
        order_id=order.id,
        user_id=order.user_id,
        product_id=order.product_id,
        rating=callback_data.rating,
        comment=None,
        is_after_dispute=is_after_disp,
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)

    await state.set_state(DisputeStates.waiting_review_comment)
    await state.update_data(order_id=order.id, review_id=review.id)

    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить комментарий", callback_data=SkipReviewCD(order_id=order.id))
    builder.adjust(1)

    text = (
        f"⭐️ <b>Вы поставили {callback_data.rating} из 5!</b>\n\n"
        f"Напишите краткий комментарий к отзыву текстом (или нажмите кнопку ниже, чтобы пропустить):"
    )
    await callback.message.answer(text, reply_markup=builder.as_markup())

@disputes_router.message(DisputeStates.waiting_review_comment)
async def process_review_comment(
    message: Message,
    state: FSMContext,
    session: AsyncSession
):
    data = await state.get_data()
    review_id = data.get("review_id")
    order_id = data.get("order_id")
    await state.clear()

    if review_id:
        review = await session.get(OrderReview, review_id)
        if review:
            review.comment = message.text.strip()[:1000] if message.text else None
            await session.commit()

    order = await session.get(Order, order_id) if order_id else None
    disp_tag = " [После диспута]" if (order and (order.has_dispute or order.disputed_at)) else ""

    await message.answer(
        f"🎉 <b>Спасибо за ваш отзыв{disp_tag}!</b>\n\n"
        f"Ваша оценка успешно сохранена и поможет другим покупателям."
    )

@disputes_router.callback_query(SkipReviewCD.filter())
async def skip_review_comment(
    callback: CallbackQuery,
    callback_data: SkipReviewCD,
    state: FSMContext,
    session: AsyncSession
):
    await callback.answer("Отзыв сохранен без комментария!")
    await state.clear()
    await callback.message.edit_text("🎉 <b>Спасибо за вашу оценку!</b> Отзыв сохранен.")
