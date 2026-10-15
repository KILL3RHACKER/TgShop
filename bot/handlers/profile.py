from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from bot.database.models import User
from bot.keyboards.inline import get_profile_kb

profile_router = Router()

@profile_router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    tg_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    
    if not user:
        await callback.answer("Пользователь не найден. Перезапустите бота с помощью /start.", show_alert=True)
        return

    # Подсчет приглашенных пользователей
    ref_count = await session.scalar(
        select(func.count(User.id)).where(User.referrer_id == tg_id)
    )

    bot_info = await callback.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"

    text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"🆔 Telegram ID: <code>{user.tg_id}</code>\n"
        f"💰 Баланс: <b>${float(user.balance):.2f}</b>\n\n"
        f"💎 <b>Ваш персональный TRX/USDT (TRC-20) адрес:</b>\n"
        f"<code>{user.tron_address}</code>\n\n"
        f"👥 <b>Реферальная система:</b>\n"
        f"Приглашено партнеров: <b>{ref_count or 0}</b>\n"
        f"Ваша ссылка:\n<code>{ref_link}</code>"
    )

    await callback.message.edit_text(text, reply_markup=get_profile_kb())
