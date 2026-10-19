from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from bot.database.models import User
from bot.keyboards.inline import get_profile_kb
from bot.services.reputation import get_user_reputation

profile_router = Router()

@profile_router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    tg_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    
    if not user:
        await callback.answer("Пользователь не найден. Перезапустите бота с помощью /start.", show_alert=True)
        return

    if not user.rules_accepted:
        from bot.handlers.common import SHOP_RULES_TEXT
        from bot.keyboards.inline import get_rules_kb
        await callback.message.edit_text(SHOP_RULES_TEXT, reply_markup=get_rules_kb())
        return

    # Подсчет приглашенных пользователей
    ref_count = await session.scalar(
        select(func.count(User.id)).where(User.referrer_id == tg_id)
    )

    rep = await get_user_reputation(session, tg_id)
    rules_date_str = user.rules_accepted_at.strftime("%d.%m.%Y %H:%M") if user.rules_accepted_at else "Подтверждены"

    bot_info = await callback.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"

    text = (
        f"<b>👤 Профиль резидента ({user.tg_id})</b>\n\n"
        f"💳 Баланс: <b>${float(user.balance):.2f}</b>\n"
        f"🛡 Траст площадки: <b>{rep['trust_label']}</b>\n"
        f"📜 Регламент: <b>✅ Согласие подтверждено</b> ({rules_date_str})\n\n"
        f"📊 <b>История сделок:</b>\n"
        f"• Успешных покупок: <b>{rep['total_orders']} шт.</b> (${rep['total_spent']:.2f})\n"
        f"• Диспуты: <b>{rep['disputes_count']}</b> (Возвратов: {rep['refunded_count']}, В пользу шопа: {rep['rejected_count']})\n"
        f"• Отзывов: <b>{rep['reviews_count']} шт.</b> (⭐️ Рейтинг: <b>{rep['avg_rating']:.1f}</b>)\n\n"
        f"💎 <b>Персональный TRX/USDT (TRC-20) адрес:</b>\n"
        f"<code>{user.tron_address}</code>\n\n"
        f"👥 <b>Реферальная программа:</b>\n"
        f"Приглашено партнеров: <b>{ref_count or 0} чел.</b>\n"
        f"Ваша ссылка:\n<code>{ref_link}</code>"
    )

    await callback.message.edit_text(text, reply_markup=get_profile_kb())
