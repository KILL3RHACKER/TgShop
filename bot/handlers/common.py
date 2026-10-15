from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, CommandObject
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bot.config import config
from bot.database.models import User
from bot.keyboards.inline import get_main_menu
from bot.services.tron import generate_tron_wallet

common_router = Router()

@common_router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, session: AsyncSession):
    tg_id = message.from_user.id
    user = await session.scalar(select(User).where(User.tg_id == tg_id))

    if not user:
        referrer_id = None
        if command.args and command.args.startswith("ref_"):
            try:
                candidate_id = int(command.args.split("_")[1])
                if candidate_id != tg_id:
                    referrer_id = candidate_id
            except ValueError:
                pass

        tron_address, tron_key = generate_tron_wallet()
        user = User(
            tg_id=tg_id,
            username=message.from_user.username,
            referrer_id=referrer_id,
            tron_address=tron_address,
            tron_private_key=tron_key
        )
        session.add(user)
        await session.commit()

    is_admin = (tg_id == config.admin_id)
    text = (
        f"👋 Здравствуйте, <b>{message.from_user.first_name}</b>!\n\n"
        "Добро пожаловать в наш автоматизированный магазин.\n"
        "Выберите раздел в меню ниже:"
    )
    await message.answer(text, reply_markup=get_main_menu(is_admin))

@common_router.callback_query(F.data == "main_menu")
async def back_to_main_menu(callback: CallbackQuery):
    await callback.answer()
    is_admin = (callback.from_user.id == config.admin_id)
    await callback.message.edit_text(
        "Главное меню магазина:",
        reply_markup=get_main_menu(is_admin)
    )
