from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from bot.database.models import Category, Product, User, Account
from bot.keyboards.callbacks import CategoryCD, ProductCD, BuyProductCD
from bot.keyboards.inline import get_categories_kb, get_products_kb, get_product_card_kb

catalog_router = Router()

@catalog_router.callback_query(F.data == "catalog")
async def show_categories(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    result = await session.execute(select(Category))
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
async def show_products_in_category(
    callback: CallbackQuery,
    callback_data: CategoryCD,
    session: AsyncSession
):
    await callback.answer()
    result = await session.execute(
        select(Product).where(Product.category_id == callback_data.id)
    )
    products = list(result.scalars().all())

    if not products:
        await callback.answer("В этой категории пока нет товаров.", show_alert=True)
        return

    product_ids = [p.id for p in products]
    counts_res = await session.execute(
        select(Account.product_id, func.count(Account.id))
        .where(Account.product_id.in_(product_ids), Account.is_sold == False)
        .group_by(Account.product_id)
    )
    counts = dict(counts_res.all())

    await callback.message.edit_text(
        "📦 <b>Выберите интересующий товар:</b>",
        reply_markup=get_products_kb(products, callback_data.id, counts)
    )

@catalog_router.callback_query(ProductCD.filter())
async def show_product_detail(
    callback: CallbackQuery,
    callback_data: ProductCD,
    session: AsyncSession
):
    await callback.answer()
    product = await session.get(Product, callback_data.id)
    if not product:
        await callback.answer("Товар не найден.", show_alert=True)
        return

    stock_count = await session.scalar(
        select(func.count(Account.id)).where(
            Account.product_id == product.id,
            Account.is_sold == False
        )
    ) or 0

    stock_text = f"{stock_count} шт." if stock_count > 0 else "Нет в наличии"

    text = (
        f"🏷 <b>{product.name}</b>\n\n"
        f"📝 <b>Описание:</b>\n{product.description}\n\n"
        f"💵 <b>Цена:</b> ${product.price:.2f}\n"
        f"📦 <b>В наличии:</b> {stock_text}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_product_card_kb(product, in_stock=(stock_count > 0))
    )

@catalog_router.callback_query(F.data == "out_of_stock")
async def out_of_stock_action(callback: CallbackQuery):
    await callback.answer("❌ Товар временно закончился на складе.", show_alert=True)

@catalog_router.callback_query(BuyProductCD.filter())
async def buy_product(
    callback: CallbackQuery,
    callback_data: BuyProductCD,
    session: AsyncSession
):
    await callback.answer()
    product = await session.get(Product, callback_data.id)
    user = await session.scalar(select(User).where(User.tg_id == callback.from_user.id))

    if not product or not user:
        await callback.answer("Ошибка покупки.", show_alert=True)
        return

    # Check database dialect for FOR UPDATE support
    stmt = (
        select(Account)
        .where(Account.product_id == product.id, Account.is_sold == False)
        .order_by(Account.id.asc())
        .limit(1)
    )
    bind = session.bind or session.get_bind()
    if bind and getattr(bind.dialect, "name", "") != "sqlite":
        stmt = stmt.with_for_update(skip_locked=True)

    account = await session.scalar(stmt)
    if not account:
        await callback.answer("❌ Товар временно закончился на складе.", show_alert=True)
        return

    if float(user.balance) < float(product.price):
        await callback.message.answer(
            f"❌ <b>Недостаточно средств!</b>\n\n"
            f"Стоимость товара: ${product.price:.2f}\n"
            f"Ваш баланс: ${float(user.balance):.2f}\n\n"
            f"Пополните счет через раздел 👤 <b>Профиль</b>."
        )
        return

    user.balance = float(user.balance) - float(product.price)
    account.is_sold = True
    account.sold_to_user_id = user.tg_id
    account.sold_at = datetime.utcnow()

    await session.commit()

    account_display = account.data if account.data else f"{account.name}, {account.date_info}"

    await callback.message.answer(
        f"🎉 <b>Покупка успешно совершена!</b>\n\n"
        f"Товар: <b>{product.name}</b>\n"
        f"Списано: <b>${product.price:.2f}</b>\n\n"
        f"📦 <b>Ваш аккаунт:</b>\n"
        f"<code>{account_display}</code>"
    )
