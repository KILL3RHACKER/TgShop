from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup
from bot.keyboards.callbacks import CategoryCD, ProductCD, BuyProductCD, TicketActionCD
from bot.database.models import Category, Product

def get_main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 Каталог", callback_data="catalog")
    builder.button(text="👤 Профиль", callback_data="profile")
    if is_admin:
        builder.button(text="⚙️ Админ панель", callback_data="admin_menu")
    builder.adjust(2, 1)
    return builder.as_markup()

def get_categories_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(text=f"📁 {cat.name}", callback_data=CategoryCD(id=cat.id))
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_products_kb(products: list[Product], category_id: int, counts: dict[int, int] | None = None) -> InlineKeyboardMarkup:
    counts = counts or {}
    builder = InlineKeyboardBuilder()
    for prod in products:
        count = counts.get(prod.id, 0)
        if count > 0:
            text = f"{prod.name} ({count} шт.) — ${prod.price:.2f}"
        else:
            text = f"{prod.name} (Нет в наличии) — ${prod.price:.2f}"
        builder.button(text=text, callback_data=ProductCD(id=prod.id))
    builder.button(text="◀️ Назад к категориям", callback_data="catalog")
    builder.adjust(1)
    return builder.as_markup()

def get_product_card_kb(product: Product, in_stock: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if in_stock:
        builder.button(text=f"💳 Купить за ${product.price:.2f}", callback_data=BuyProductCD(id=product.id))
    else:
        builder.button(text="❌ Нет в наличии", callback_data="out_of_stock")
    builder.button(text="◀️ Назад к товарам", callback_data=CategoryCD(id=product.category_id))
    builder.adjust(1)
    return builder.as_markup()

def get_profile_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Пополнить баланс", callback_data="topup")
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_ticket_admin_kb(ticket_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=TicketActionCD(id=ticket_id, action="approve"))
    builder.button(text="❌ Отклонить", callback_data=TicketActionCD(id=ticket_id, action="reject"))
    builder.adjust(2)
    return builder.as_markup()
