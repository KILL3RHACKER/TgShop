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
    if user_id == config.admin_id:
        stmt = select(Order.id).where(Order.admin_unread == True).limit(1)
    else:
        caller = await session.scalar(select(User).where(User.tg_id == user_id))
        if caller and caller.role in ("moderator", "admin"):
            stmt = select(Order.id).where(Order.admin_unread == True).limit(1)
        else:
            stmt = select(Order.id).where(Order.user_id == user_id, Order.user_unread == True).limit(1)
    res = await session.execute(stmt)
    return res.scalar_one_or_none() is not None

SHOP_RULES_TEXT = (
    "📜 <b>РЕГЛАМЕНТ И ПРАВИЛА ПЛОЩАДКИ (KRAKEN ESCROW)</b>\n\n"
    "Для доступа к магазину каждый участник обязан подтвердить безоговорочное согласие со следующими правилами:\n\n"
    "1️⃣ <b>Гарантийный холд (Escrow 24 часа):</b>\n"
    "• С момента оплаты средства замораживаются в гарантийном холде ровно на 24 часа.\n"
    "• Это время отведено покупателю на проверку работоспособности и соответствия товара.\n"
    "• По истечении 24 часов сделка закрывается автоматически, средства поступают магазину, претензии не принимаются.\n\n"
    "2️⃣ <b>Разграничение обращений («Диспут» vs «Вопрос»):</b>\n"
    "• <b>«⚠️ Открыть диспут»</b> — запускается ТОЛЬКО при объективной неработоспособности (невалид при первой попытке входа, бан до момента покупки, несоответствие заявленным параметрам).\n"
    "• <b>«❓ Задать вопрос»</b> — консультации по формату данных, подбору прокси, юзер-агенту или настройке софта без блокировки средств.\n\n"
    "3️⃣ <b>Тайм-ауты и жесткий SLA (24 часа на ответ):</b>\n"
    "• На каждый ответ в открытом диспуте обеим сторонам (Клиент и Магазин) дается ровно <b>24 часа</b>.\n"
    "• <b>Клиент не ответил за 24 часа</b> — автоматическое закрытие спора в пользу магазина, возврат аннулируется.\n"
    "• <b>Магазин не ответил за 24 часа</b> — автоматический безусловный возврат полной суммы на баланс покупателя.\n\n"
    "4️⃣ <b>Доказательства и технические стандарты:</b>\n"
    "• К открытию диспута обязательно прикрепляется детальное описание проблемы и полный скриншот первой попытки входа.\n"
    "• Вход должен осуществляться строго через чистые резидентские/мобильные прокси под штат аккаунта.\n"
    "• Любая смена учетных данных (пароль, почта) или передача третьим лицам до решения спора аннулирует гарантию.\n\n"
    "5️⃣ <b>Антифрод и блокировка (Blacklist):</b>\n"
    "• Фальсификация доказательств, подделка скриншотов, оскорбления и шантаж влекут немедленный бан аккаунта с аннулированием баланса и внесением в ЧС площадки.\n\n"
    "6️⃣ <b>Окончательность решений Арбитража:</b>\n"
    "• В случае неразрешимого спора подключается Администратор (Арбитр площадки). Вердикт Арбитража на основе доказательств и репутации сторон является окончательным и пересмотру не подлежит."
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
