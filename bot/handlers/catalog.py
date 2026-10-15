from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from bot.database.models import Category, Product, User
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

    await callback.message.edit_text(
        "📦 <b>Выберите интересующий товар:</b>",
        reply_markup=get_products_kb(products, callback_data.id)
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

    text = (
        f"🏷 <b>{product.name}</b>\n\n"
        f"📝 <b>Описание:</b>\n{product.description}\n\n"
        f"💵 <b>Цена:</b> ${product.price:.2f}"
    )
    await callback.message.edit_text(text, reply_markup=get_product_card_kb(product))

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

    if float(user.balance) < float(product.price):
        await callback.message.answer(
            f"❌ <b>Недостаточно средств!</b>\n\n"
            f"Стоимость товара: ${product.price:.2f}\n"
            f"Ваш баланс: ${float(user.balance):.2f}\n\n"
            f"Пополните счет через раздел 👤 <b>Профиль</b>."
        )
        return

    user.balance = float(user.balance) - float(product.price)
    await session.commit()

    await callback.message.answer(
        f"🎉 <b>Покупка успешно совершена!</b>\n\n"
        f"Товар: <b>{product.name}</b>\n"
        f"Списано: <b>${product.price:.2f}</b>\n\n"
        f"📦 <b>Ваш товар / ключ:</b>\n"
        f"<code>{product.content}</code>"
    )
