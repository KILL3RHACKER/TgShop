from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup
from bot.keyboards.callbacks import (
    CategoryCD,
    SubcategoryCD,
    ProductCD,
    AccountCD,
    BuyAccountCD,
    FastBuyCD,
    TicketActionCD,
    OrderCD,
    OpenDisputeCD,
    ReplyDisputeCD,
    ResolveDisputeCD,
)
from bot.database.models import Category, Subcategory, Product, Account, Order, OrderStatus

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

def get_subcategories_kb(
    subcategories: list[Subcategory],
    category_id: int,
    counts: dict[int, int] | None = None
) -> InlineKeyboardMarkup:
    counts = counts or {}
    builder = InlineKeyboardBuilder()
    for subcat in subcategories:
        count = counts.get(subcat.id, 0)
        stock_text = f"({count} тов.)" if count > 0 else ""
        builder.button(
            text=f"📂 {subcat.name} {stock_text}",
            callback_data=SubcategoryCD(id=subcat.id)
        )
    builder.button(text="◀️ Назад к категориям", callback_data="catalog")
    builder.adjust(1)
    return builder.as_markup()

def get_products_kb(
    products: list[Product],
    category_id: int,
    counts: dict[int, int] | None = None
) -> InlineKeyboardMarkup:
    counts = counts or {}
    builder = InlineKeyboardBuilder()
    for prod in products:
        count = counts.get(prod.id, 0)
        stock_text = f"({count} шт.)" if count > 0 else "(Нет в наличии)"
        builder.button(
            text=f"🏷 {prod.name} {stock_text} — ${prod.price:.2f}",
            callback_data=ProductCD(id=prod.id, page=1)
        )
    builder.button(text="◀️ Назад к подкатегориям", callback_data=CategoryCD(id=category_id))
    builder.adjust(1)
    return builder.as_markup()

def get_accounts_kb(
    accounts: list[Account],
    product: Product,
    page: int = 1,
    total_pages: int = 1,
    total_count: int = 0
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Fast buy freshest account
    if total_count > 0:
        builder.button(
            text=f"⚡️ Купить любой свежий (${product.price:.2f})",
            callback_data=FastBuyCD(product_id=product.id)
        )

    # Individual accounts (Товары: свежие на первой странице)
    for acc in accounts:
        display_name = acc.data or f"{acc.name}, {acc.date_info}"
        builder.button(
            text=f"👤 {display_name}",
            callback_data=AccountCD(id=acc.id)
        )

    # Pagination controls
    nav_buttons = []
    if page > 1:
        nav_buttons.append(("◀️ Назад", ProductCD(id=product.id, page=page - 1)))
    nav_buttons.append((f"📄 {page}/{max(total_pages, 1)}", "noop"))
    if page < total_pages:
        nav_buttons.append(("Вперед ▶️", ProductCD(id=product.id, page=page + 1)))

    for text, cb in nav_buttons:
        if cb == "noop":
            builder.button(text=text, callback_data="noop")
        else:
            builder.button(text=text, callback_data=cb)

    builder.button(text="◀️ Назад к товарам", callback_data=SubcategoryCD(id=product.subcategory_id))

    adjust_pattern = []
    if total_count > 0:
        adjust_pattern.append(1)
    for _ in accounts:
        adjust_pattern.append(1)
    adjust_pattern.append(len(nav_buttons))
    adjust_pattern.append(1)

    builder.adjust(*adjust_pattern)
    return builder.as_markup()

def get_account_card_kb(account: Account, product: Product, page: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not account.is_sold:
        builder.button(text=f"💳 Купить за ${product.price:.2f}", callback_data=BuyAccountCD(id=account.id))
    else:
        builder.button(text="❌ Уже продан", callback_data="out_of_stock")
    builder.button(text="◀️ Назад к списку аккаунтов", callback_data=ProductCD(id=product.id, page=page))
    builder.adjust(1)
    return builder.as_markup()

def get_profile_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Пополнить баланс", callback_data="topup")
    builder.button(text="📦 Мои заказы / Диспуты", callback_data="my_orders")
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_ticket_admin_kb(ticket_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=TicketActionCD(id=ticket_id, action="approve"))
    builder.button(text="❌ Отклонить", callback_data=TicketActionCD(id=ticket_id, action="reject"))
    builder.adjust(2)
    return builder.as_markup()

def get_my_orders_kb(orders: list[Order]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for o in orders:
        status_icon = "⏳ Холд" if o.status == OrderStatus.HOLD else ("🚨 Диспут" if o.status == OrderStatus.DISPUTED else ("✅ Завершен" if o.status == OrderStatus.COMPLETED else "↩️ Возврат"))
        sub_name = o.subcategory.name if o.subcategory else "Товар"
        builder.button(
            text=f"#{o.id} | {sub_name} (${o.amount:.2f}) [{status_icon}]",
            callback_data=OrderCD(id=o.id)
        )
    builder.button(text="◀️ Назад в профиль", callback_data="profile")
    builder.adjust(1)
    return builder.as_markup()

def get_order_detail_kb(order: Order, can_dispute: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if order.status == OrderStatus.HOLD and can_dispute:
        builder.button(text="⚠️ Открыть диспут", callback_data=OpenDisputeCD(order_id=order.id))
    elif order.status == OrderStatus.DISPUTED:
        builder.button(text="💬 Чат спора", callback_data=OpenDisputeCD(order_id=order.id))
    builder.button(text="◀️ Назад к заказам", callback_data="my_orders")
    builder.adjust(1)
    return builder.as_markup()

def get_dispute_admin_kb(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить в чат спора", callback_data=ReplyDisputeCD(order_id=order_id))
    builder.button(text="✅ Вернуть покупателю", callback_data=ResolveDisputeCD(order_id=order_id, action="refund"))
    builder.button(text="❌ Закрыть спор (в пользу магазина)", callback_data=ResolveDisputeCD(order_id=order_id, action="reject"))
    builder.adjust(1, 2)
    return builder.as_markup()
