import math
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from bot.config import config
from bot.database.models import Category, Subcategory, Product, Account, User, Order, OrderStatus
from bot.keyboards.callbacks import (
    CategoryCD,
    SubcategoryCD,
    ProductCD,
    AccountCD,
    BuyAccountCD,
    FastBuyCD,
    OpenDisputeCD,
)
from bot.keyboards.inline import (
    get_categories_kb,
    get_subcategories_kb,
    get_products_kb,
    get_accounts_kb,
    get_account_card_kb,
    get_order_detail_kb,
    get_order_delivery_kb,
    get_rules_kb,
)
from bot.handlers.common import SHOP_RULES_TEXT
from bot.services.account_zip import generate_account_zip

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
    user = await session.scalar(select(User).where(User.tg_id == callback.from_user.id))
    if user and not user.rules_accepted:
        await callback.message.edit_text(SHOP_RULES_TEXT, reply_markup=get_rules_kb())
        return

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
        select(Product.subcategory_id, func.count(Account.id))
        .join(Account, Account.product_id == Product.id)
        .where(Product.subcategory_id.in_(subcat_ids), Account.is_sold == False)
        .group_by(Product.subcategory_id)
    )
    counts = dict(counts_res.all())

    await callback.message.edit_text(
        f"📁 <b>{category.name}</b>\n\nВыберите подкатегорию:",
        reply_markup=get_subcategories_kb(subcategories, callback_data.id, counts)
    )

@catalog_router.callback_query(SubcategoryCD.filter())
async def show_products(
    callback: CallbackQuery,
    callback_data: SubcategoryCD,
    session: AsyncSession
):
    await callback.answer()
    subcategory = await session.get(Subcategory, callback_data.id)
    if not subcategory:
        await callback.answer("Подкатегория не найдена.", show_alert=True)
        return

    category = await session.get(Category, subcategory.category_id)

    # Fetch products in this subcategory
    prods_res = await session.execute(
        select(Product)
        .where(Product.subcategory_id == subcategory.id)
        .order_by(Product.name)
    )
    products = list(prods_res.scalars().all())

    if not products:
        await callback.answer("В этой подкатегории пока нет товаров.", show_alert=True)
        return

    # Если в подкатегории ровно 1 товар (тир), сразу переходим к витрине товаров (аккаунтов)!
    if len(products) == 1:
        return await render_accounts_showcase(
            callback=callback,
            product=products[0],
            page=1,
            session=session,
            back_category_id=subcategory.category_id
        )

    prod_ids = [p.id for p in products]
    counts_res = await session.execute(
        select(Account.product_id, func.count(Account.id))
        .where(Account.product_id.in_(prod_ids), Account.is_sold == False)
        .group_by(Account.product_id)
    )
    counts = dict(counts_res.all())

    await callback.message.edit_text(
        f"📂 <b>{subcategory.name}</b>\nКатегория: {category.name if category else '—'}\n\nВыберите нужный товар:",
        reply_markup=get_products_kb(products, subcategory.category_id, counts)
    )

@catalog_router.callback_query(ProductCD.filter())
async def show_accounts_in_product(
    callback: CallbackQuery,
    callback_data: ProductCD,
    session: AsyncSession
):
    await callback.answer()
    product = await session.get(Product, callback_data.id)
    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    subcat = product.subcategory
    back_cat_id = subcat.category_id if subcat else None

    await render_accounts_showcase(
        callback=callback,
        product=product,
        page=callback_data.page,
        session=session,
        back_category_id=back_cat_id
    )

async def render_accounts_showcase(
    callback: CallbackQuery,
    product: Product,
    page: int,
    session: AsyncSession,
    back_category_id: int | None = None
):
    # Total unsold accounts
    total_count = await session.scalar(
        select(func.count(Account.id)).where(
            Account.product_id == product.id,
            Account.is_sold == False
        )
    ) or 0

    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE

    # Sorting: свежие на первой странице (ORDER BY created_at DESC, id DESC)
    accounts_res = await session.execute(
        select(Account)
        .where(
            Account.product_id == product.id,
            Account.is_sold == False
        )
        .order_by(Account.created_at.desc(), Account.id.desc())
        .offset(offset)
        .limit(PAGE_SIZE)
    )
    accounts = list(accounts_res.scalars().all())

    desc_text = f"\n📝 <i>{product.description}</i>\n" if product.description else ""
    stock_text = f"<b>{total_count} шт.</b>" if total_count > 0 else "<b>Нет в наличии</b>"

    text = (
        f"🏷 <b>Товар: {product.name}</b>\n"
        f"{desc_text}\n"
        f"💵 <b>Цена:</b> ${product.price:.2f}\n"
        f"📦 <b>В наличии на складе:</b> {stock_text}\n\n"
        f"👇 <i>Нажмите «Купить любой свежий» для моментальной покупки или выберите конкретный аккаунт:</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_accounts_kb(
            accounts=accounts,
            product=product,
            page=page,
            total_pages=total_pages,
            total_count=total_count,
            back_category_id=back_category_id
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

    product = await session.get(Product, account.product_id)
    created_str = account.created_at.strftime("%d.%m.%Y %H:%M") if account.created_at else "—"

    age_str = f"{account.age_days} дн." if account.registration_date else "—"
    text = (
        f"🏷 <b>Товар:</b> <code>{account.data}</code>\n"
        f"🏛 <b>Штат (ST):</b> <code>{account.state}</code>\n"
        f"👤 <b>ФИО:</b> <code>{account.name}</code>\n"
        f"📅 <b>Дата:</b> <code>{account.date_info}</code> (Отлёжка: {age_str})\n"
        f"📁 <b>Категория:</b> {product.name}\n"
        f"💵 <b>Цена:</b> ${product.price:.2f}\n"
        f"📊 <b>Статус:</b> {'🟢 В наличии' if not account.is_sold else '🔴 Продан'}"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_account_card_kb(account, product)
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

    product = await session.get(Product, account.product_id)
    price = float(product.price)

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
        account_data=account.data,
        product_id=product.id,
        amount=price,
        status=OrderStatus.HOLD,
        hold_until=hold_until,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)

    hold_str = hold_until.strftime("%d.%m.%Y %H:%M UTC")
    success_text = (
        f"🎉 <b>Успешная покупка заказа {order.order_code}!</b>\n\n"
        f"🏷 <b>Товар:</b> {product.name}\n"
        f"💵 <b>Списано:</b> ${price:.2f}\n"
        f"💰 <b>Остаток баланса:</b> ${float(user.balance):.2f}\n\n"
        f"📦 <b>Данные аккаунта:</b>\n"
        f"<code>{account.data}</code>\n\n"
        f"⏳ <b>Гарантия на проверку: 24 часа</b> (до {hold_str}).\n"
        f"Аккаунт сформирован в ZIP-архиве выше."
    )

    zip_bytes, zip_filename = generate_account_zip(order, account, product)
    zip_doc = BufferedInputFile(zip_bytes, filename=zip_filename)

    await callback.message.answer_document(
        document=zip_doc,
        caption=success_text,
        reply_markup=get_order_delivery_kb(order)
    )

    # Notify admin
    try:
        admin_alert = (
            f"💰 <b>Новый заказ {order.order_code} на ${price:.2f}!</b>\n\n"
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

    if not user.rules_accepted:
        await callback.message.answer(SHOP_RULES_TEXT, reply_markup=get_rules_kb())
        return

    product = await session.get(Product, callback_data.product_id)
    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    price = float(product.price)
    if float(user.balance) < price:
        await callback.answer(
            f"❌ Недостаточно средств на балансе. Цена: ${price:.2f}, Ваш баланс: ${float(user.balance):.2f}.",
            show_alert=True
        )
        return

    # Pick the freshest available account atomically
    stmt = (
        select(Account)
        .where(Account.product_id == product.id, Account.is_sold == False)
        .order_by(Account.created_at.desc(), Account.id.desc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    result = await session.execute(stmt)
    account = result.scalar_one_or_none()

    if not account:
        await callback.answer("❌ К сожалению, все аккаунты этого товара раскуплены.", show_alert=True)
        return

    user.balance = float(user.balance) - price
    account.is_sold = True
    account.sold_to_user_id = user.tg_id
    account.sold_at = datetime.utcnow()

    hold_until = datetime.utcnow() + timedelta(hours=24)
    order = Order(
        user_id=user.tg_id,
        account_id=account.id,
        account_data=account.data,
        product_id=product.id,
        amount=price,
        status=OrderStatus.HOLD,
        hold_until=hold_until,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)

    hold_str = hold_until.strftime("%d.%m.%Y %H:%M UTC")
    success_text = (
        f"⚡️ <b>Быстрая покупка {order.order_code}!</b>\n\n"
        f"🏷 <b>Товар:</b> {product.name}\n"
        f"💵 <b>Списано:</b> ${price:.2f}\n"
        f"💰 <b>Остаток баланса:</b> ${float(user.balance):.2f}\n\n"
        f"📦 <b>Ваш свежий аккаунт:</b>\n"
        f"<code>{account.data}</code>\n\n"
        f"⏳ <b>Гарантия на проверку: 24 часа</b> (до {hold_str}).\n"
        f"Архив с полными данными прикреплен выше."
    )

    zip_bytes, zip_filename = generate_account_zip(order, account, product)
    zip_doc = BufferedInputFile(zip_bytes, filename=zip_filename)

    await callback.message.answer_document(
        document=zip_doc,
        caption=success_text,
        reply_markup=get_order_delivery_kb(order)
    )

    try:
        admin_alert = (
            f"⚡️ <b>Быстрая покупка {order.order_code} на ${price:.2f}!</b>\n\n"
            f"Покупатель: @{user.username or '—'} (<code>{user.tg_id}</code>)\n"
            f"Товар: <code>{account.data}</code>\n"
            f"Статус: ⏳ <b>Заморожено на 24 часа</b> (до {hold_str})"
        )
        await bot.send_message(config.admin_id, admin_alert)
    except Exception:
        pass
