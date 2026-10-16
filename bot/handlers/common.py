from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bot.config import config
from bot.database.models import User, Order
from bot.keyboards.inline import get_main_menu, get_rules_kb
from bot.services.tron import generate_tron_wallet

common_router = Router()

async def check_user_unread_chats(session: AsyncSession, user_id: int) -> bool:
    stmt = (
        select(Order.id)
        .where(
            Order.user_id == user_id,
            Order.user_unread == True
        )
        .limit(1)
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none() is not None

SHOP_RULES_TEXT = (
    "📜 <b>ПРАВИЛА И РЕГЛАМЕНТ РАБОТЫ МАГАЗИНА</b>\n\n"
    "Чтобы продолжить пользоваться ботом, необходимо подтвердить согласие с регламентом:\n\n"
    "1️⃣ <b>Время на проверку аккаунта — 24 часа:</b>\n"
    "• С момента покупки запускается гарантийный холд на 24 часа.\n"
    "• Если аккаунт оказался нерабочим, и вы сообщили об этом <b>после истечения 24 часов</b> — средства возвращены НЕ будут.\n\n"
    "2️⃣ <b>Разделение обращений: «Диспут» vs «Вопрос по заказу»:</b>\n"
    "• <b>«⚠️ Открыть диспут»</b> — используйте ТОЛЬКО при неработоспособности аккаунта (невалид, блок до момента покупки, несовпадение данных). Заказ замораживается до решения.\n"
    "• <b>«❓ Задать вопрос»</b> — используйте для консультаций по входу, прокси, юзер-агенту или формату.\n\n"
    "3️⃣ <b>Регламент ответа в диспуте (SLA 24 часа):</b>\n"
    "• Для обеих сторон (Клиент и Магазин) действует строгое правило: <b>время на ответ — 24 часа</b>.\n"
    "• Если <b>клиент не отвечает в течение 24 часов</b> — диспут закрывается в пользу магазина, возврат отклоняется.\n"
    "• Если <b>магазин (поддержка) не отвечает в течение 24 часов</b> — диспут автоматически одобряется, средства возвращаются на ваш баланс.\n\n"
    "4️⃣ <b>Доказательства и этика общения:</b>\n"
    "• При открытии диспута обязательно предоставьте описание проблемы и скриншот первой попытки входа.\n"
    "• <b>Запрещено:</b> обманывать, фальсифицировать данные, оскорблять и проявлять неуважение. Нарушение правил влечет отказ в возврате и бан."
)

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
            tron_private_key=tron_key,
            rules_accepted=False,
        )
        session.add(user)
        await session.commit()

    # Обязательное принятие правил перед использованием
    if not user.rules_accepted:
        await message.answer(
            SHOP_RULES_TEXT,
            reply_markup=get_rules_kb()
        )
        return

    is_admin = (tg_id == config.admin_id)
    has_unread = await check_user_unread_chats(session, tg_id)
    text = (
        f"👋 Здравствуйте, <b>{message.from_user.first_name}</b>!\n\n"
        "Добро пожаловать в наш автоматизированный магазин.\n"
        "Выберите раздел в меню ниже:"
    )
    await message.answer(text, reply_markup=get_main_menu(is_admin, has_unread))

@common_router.callback_query(F.data == "accept_rules")
async def accept_rules(callback: CallbackQuery, session: AsyncSession):
    await callback.answer("✅ Правила успешно приняты!", show_alert=True)
    user = await session.scalar(select(User).where(User.tg_id == callback.from_user.id))
    if user:
        user.rules_accepted = True
        user.rules_accepted_at = datetime.utcnow()
        await session.commit()

    is_admin = (callback.from_user.id == config.admin_id)
    has_unread = await check_user_unread_chats(session, callback.from_user.id)
    text = (
        f"👋 Здравствуйте, <b>{callback.from_user.first_name}</b>!\n\n"
        "Добро пожаловать в наш автоматизированный магазин.\n"
        "Выберите раздел в меню ниже:"
    )
    await callback.message.edit_text(text, reply_markup=get_main_menu(is_admin, has_unread))

@common_router.callback_query(F.data == "view_rules")
async def view_rules_handler(callback: CallbackQuery):
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    await callback.message.edit_text(SHOP_RULES_TEXT, reply_markup=builder.as_markup())

@common_router.callback_query(F.data == "main_menu")
async def back_to_main_menu(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    user = await session.scalar(select(User).where(User.tg_id == callback.from_user.id))
    if user and not user.rules_accepted:
        await callback.message.edit_text(SHOP_RULES_TEXT, reply_markup=get_rules_kb())
        return

    is_admin = (callback.from_user.id == config.admin_id)
    has_unread = await check_user_unread_chats(session, callback.from_user.id)
    await callback.message.edit_text(
        "Главное меню магазина:",
        reply_markup=get_main_menu(is_admin, has_unread)
    )
