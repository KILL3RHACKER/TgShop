import math
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from bot.config import config
from bot.database.models import Category, Subcategory, Account, User, Order, OrderStatus
from bot.keyboards.callbacks import (
    CategoryCD,
    SubcategoryCD,
    AccountCD,
    BuyAccountCD,
    FastBuyCD,
    OpenDisputeCD,
)
from bot.keyboards.inline import (
    get_categories_kb,
    get_subcategories_kb,
    get_accounts_kb,
    get_account_card_kb,
    get_order_detail_kb,
)

catalog_router = Router()
PAGE_SIZE = 6

@catalog_router.callback_query(F.data == "noop")
async def noop_action(callback: CallbackQuery):
    await callback.answer()

@catalog_router.callback_query(F.data == "out_of_stock")
async def out_of_stock_action(callback: CallbackQuery):
    await callback.answer("❌ Товар временно закончился на складе.", show_alert=True)

@catalog_router.callback_query(F.data == "catalog")
async def show_categories(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    result = await session.execute(select(Category).order_by(Category.name))
    categories = list(result.scalars().all())

    if not categories:
        await callback.message.edit_text(
            "Каталог товаров временно пуст.",
            reply_markup=get_categories_kb([])
        )
        return

    await callback.message.edit_text(
        "🗂 <b>Выберите категорию:</b>",
        reply_markup=get_categories_kb(categories)
    )

@catalog_router.callback_query(CategoryCD.filter())
async def show_subcategories(
    callback: CallbackQuery,
    callback_data: CategoryCD,
    session: AsyncSession
):
    await callback.answer()
    category = await session.get(Category, callback_data.id)
    if not category:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    result = await session.execute(
        select(Subcategory)
        .where(Subcategory.category_id == callback_data.id)
        .order_by(Subcategory.name)
    )
    subcategories = list(result.scalars().all())

    if not subcategories:
        await callback.answer("В этой категории пока нет подкатегорий.", show_alert=True)
        return

    subcat_ids = [s.id for s in subcategories]
    counts_res = await session.execute(
        select(Account.subcategory_id, func.count(Account.id))
        .where(Account.subcategory_id.in_(subcat_ids), Account.is_sold == False)
        .group_by(Account.subcategory_id)
    )
    counts = dict(counts_res.all())

    await callback.message.edit_text(
        f"📁 <b>{category.name}</b>\n\nВыберите подкатегорию:",
        reply_markup=get_subcategories_kb(subcategories, callback_data.id, counts)
    )

@catalog_router.callback_query(SubcategoryCD.filter())
async def show_accounts_in_subcategory(
    callback: CallbackQuery,
    callback_data: SubcategoryCD,
    session: AsyncSession
):
    await callback.answer()
    subcategory = await session.get(Subcategory, callback_data.id)
    if not subcategory:
        await callback.answer("Подкатегория не найдена.", show_alert=True)
        return

    # Total unsold accounts
    total_count = await session.scalar(
        select(func.count(Account.id)).where(
            Account.subcategory_id == subcategory.id,
            Account.is_sold == False
        )
    ) or 0

    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    page = max(1, min(callback_data.page, total_pages))
    offset = (page - 1) * PAGE_SIZE

    # Sorting: свежие на первой странице (ORDER BY created_at DESC, id DESC)
    accounts_res = await session.execute(
        select(Account)
        .where(
            Account.subcategory_id == subcategory.id,
            Account.is_sold == False
        )
        .order_by(Account.created_at.desc(), Account.id.desc())
        .offset(offset)
        .limit(PAGE_SIZE)
    )
    accounts = list(accounts_res.scalars().all())

    desc_text = f"\n📝 <i>{subcategory.description}</i>\n" if subcategory.description else ""
    stock_text = f"<b>{total_count} шт.</b>" if total_count > 0 else "<b>Нет в наличии</b>"

    text = (
        f"📂 <b>{subcategory.name}</b>\n"
        f"{desc_text}\n"
        f"💵 <b>Цена:</b> ${subcategory.price:.2f}\n"
        f"📦 <b>В наличии:</b> {stock_text}\n\n"
        f"👇 <i>Выберите аккаунт из списка ниже (свежие первыми) или нажмите быструю покупку:</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_accounts_kb(
            accounts=accounts,
            subcategory=subcategory,
            page=page,
            total_pages=total_pages,
            total_count=total_count
        )
    )

@catalog_router.callback_query(AccountCD.filter())
async def show_account_card(
    callback: CallbackQuery,
    callback_data: AccountCD,
    session: AsyncSession
):
    await callback.answer()
    account = await session.get(Account, callback_data.id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    subcategory = await session.get(Subcategory, account.subcategory_id)
    created_str = account.created_at.strftime("%d.%m.%Y %H:%M") if account.created_at else "—"

    text = (
        f"🏷 <b>Товар:</b> <code>{account.data}</code>\n"
        f"📂 <b>Подкатегория:</b> {subcategory.name}\n"
        f"💵 <b>Цена:</b> ${subcategory.price:.2f}\n"
        f"📅 <b>Добавлен:</b> {created_str}\n"
        f"📊 <b>Статус:</b> {'🟢 В наличии' if not account.is_sold else '🔴 Продан'}"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_account_card_kb(account, subcategory)
    )

@catalog_router.callback_query(BuyAccountCD.filter())
async def buy_specific_account(
    callback: CallbackQuery,
    callback_data: BuyAccountCD,
    session: AsyncSession,
    bot: Bot
):
    await callback.answer()
    user = await session.scalar(select(User).where(User.tg_id == callback.from_user.id))
    if not user:
        await callback.answer("Ошибка: пользователь не найден.", show_alert=True)
        return

    # Atomic lock on the specific account
    stmt = (
        select(Account)
        .where(Account.id == callback_data.id, Account.is_sold == False)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    account = result.scalar_one_or_none()

    if not account:
        await callback.answer("❌ Этот аккаунт уже был куплен другим пользователем.", show_alert=True)
        return

    subcategory = await session.get(Subcategory, account.subcategory_id)
    price = float(subcategory.price)

    if float(user.balance) < price:
        await callback.answer(
            f"❌ Недостаточно средств на балансе. Цена: ${price:.2f}, Ваш баланс: ${float(user.balance):.2f}.",
            show_alert=True
        )
        return

    # Deduct and mark sold
    user.balance = float(user.balance) - price
    account.is_sold = True
    account.sold_to_user_id = user.tg_id
    account.sold_at = datetime.utcnow()

    # Create Order with 24-hour hold / escrow
    hold_until = datetime.utcnow() + timedelta(hours=24)
    order = Order(
        user_id=user.tg_id,
        account_id=account.id,
        subcategory_id=subcategory.id,
        amount=price,
        status=OrderStatus.HOLD,
        hold_until=hold_until,
    )
    session.add(order)
    await session.commit()

    hold_str = hold_until.strftime("%d.%m.%Y %H:%M UTC")
    success_text = (
        f"🎉 <b>Успешная покупка заказа #ORDER-{order.id}!</b>\n\n"
        f"📂 <b>Подкатегория:</b> {subcategory.name}\n"
        f"💵 <b>Списано:</b> ${price:.2f}\n"
        f"💰 <b>Остаток баланса:</b> ${float(user.balance):.2f}\n\n"
        f"📦 <b>Данные аккаунта:</b>\n"
        f"<code>{account.data}</code>\n\n"
        f"⏳ <b>Гарантия на проверку: 24 часа</b> (до {hold_str}).\n"
        f"Средства заморожены на 24ч. В случае проблем вы можете открыть диспут по кнопке ниже."
    )

    await callback.message.answer(
        success_text,
        reply_markup=get_order_detail_kb(order, can_dispute=True)
    )

    # Notify admin about new hold order
    try:
        admin_alert = (
            f"💰 <b>Новый заказ #ORDER-{order.id} на ${price:.2f}!</b>\n\n"
            f"Покупатель: @{user.username or '—'} (<code>{user.tg_id}</code>)\n"
            f"Товар: <code>{account.data}</code>\n"
            f"Статус: ⏳ <b>Заморожено на 24 часа</b> (до {hold_str})"
        )
        await bot.send_message(config.admin_id, admin_alert)
    except Exception:
        pass

@catalog_router.callback_query(FastBuyCD.filter())
async def buy_fast_freshest_account(
    callback: CallbackQuery,
    callback_data: FastBuyCD,
    session: AsyncSession,
    bot: Bot
):
    await callback.answer()
    user = await session.scalar(select(User).where(User.tg_id == callback.from_user.id))
    if not user:
        await callback.answer("Ошибка: пользователь не найден.", show_alert=True)
        return

    subcategory = await session.get(Subcategory, callback_data.subcategory_id)
    if not subcategory:
        await callback.answer("Подкатегория не найдена.", show_alert=True)
        return

    price = float(subcategory.price)
    if float(user.balance) < price:
        await callback.answer(
            f"❌ Недостаточно средств на балансе. Цена: ${price:.2f}, Ваш баланс: ${float(user.balance):.2f}.",
            show_alert=True
        )
        return

    # Pick the freshest available account atomically
    stmt = (
        select(Account)
        .where(Account.subcategory_id == subcategory.id, Account.is_sold == False)
        .order_by(Account.created_at.desc(), Account.id.desc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    result = await session.execute(stmt)
    account = result.scalar_one_or_none()

    if not account:
        await callback.answer("❌ На складе больше нет доступных аккаунтов.", show_alert=True)
        return

    user.balance = float(user.balance) - price
    account.is_sold = True
    account.sold_to_user_id = user.tg_id
    account.sold_at = datetime.utcnow()

    # Create Order with 24-hour hold
    hold_until = datetime.utcnow() + timedelta(hours=24)
    order = Order(
        user_id=user.tg_id,
        account_id=account.id,
        subcategory_id=subcategory.id,
        amount=price,
        status=OrderStatus.HOLD,
        hold_until=hold_until,
    )
    session.add(order)
    await session.commit()

    hold_str = hold_until.strftime("%d.%m.%Y %H:%M UTC")
    success_text = (
        f"⚡️ <b>Быстрая покупка заказа #ORDER-{order.id} успешна!</b>\n\n"
        f"📂 <b>Подкатегория:</b> {subcategory.name}\n"
        f"💵 <b>Списано:</b> ${price:.2f}\n"
        f"💰 <b>Остаток баланса:</b> ${float(user.balance):.2f}\n\n"
        f"📦 <b>Данные аккаунта:</b>\n"
        f"<code>{account.data}</code>\n\n"
        f"⏳ <b>Гарантия на проверку: 24 часа</b> (до {hold_str}).\n"
        f"Средства заморожены на 24ч. В случае проблем вы можете открыть диспут по кнопке ниже."
    )

    await callback.message.answer(
        success_text,
        reply_markup=get_order_detail_kb(order, can_dispute=True)
    )

    try:
        admin_alert = (
            f"💰 <b>Новый заказ #ORDER-{order.id} на ${price:.2f}!</b>\n\n"
            f"Покупатель: @{user.username or '—'} (<code>{user.tg_id}</code>)\n"
            f"Товар: <code>{account.data}</code>\n"
            f"Статус: ⏳ <b>Заморожено на 24 часа</b> (до {hold_str})"
        )
        await bot.send_message(config.admin_id, admin_alert)
    except Exception:
        pass
