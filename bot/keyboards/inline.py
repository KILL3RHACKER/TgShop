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
    AskQuestionCD,
    MyOrdersTabCD,
    StartReviewCD,
    RateOrderCD,
    SkipReviewCD,
    ChatViewCD,
    ChatReplyCD,
    DeleteChatCD,
    AdminReplaceSelectCD,
    AdminConfirmReplaceCD,
)
from bot.database.models import Category, Subcategory, Product, Account, Order, OrderStatus

def get_rules_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ознакомлен и согласен с правилами", callback_data="accept_rules")
    builder.adjust(1)
    return builder.as_markup()

def get_main_menu(is_admin: bool = False, has_unread_chats: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 Каталог", callback_data="catalog")
    builder.button(text="👤 Профиль", callback_data="profile")
    chats_btn_text = "⚪️ 💬 Чаты" if has_unread_chats else "💬 Чаты"
    builder.button(text=chats_btn_text, callback_data="my_chats")
    builder.button(text="📦 Мои заказы", callback_data="my_orders")
    builder.button(text="📜 Правила", callback_data="view_rules")
    if is_admin:
        builder.button(text="⚙️ Админ панель", callback_data="admin_menu")
    builder.adjust(2, 2, 1, 1 if is_admin else 0)
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
    counts: dict[int, int] | None = None,
    prices: dict[int, float] | None = None
) -> InlineKeyboardMarkup:
    counts = counts or {}
    prices = prices or {}
    builder = InlineKeyboardBuilder()
    for subcat in subcategories:
        count = counts.get(subcat.id, 0)
        price = prices.get(subcat.id)
        if count > 0:
            price_str = f" — ${price:.2f}" if price is not None else ""
            stock_text = f"({count} шт.){price_str}"
        else:
            stock_text = "(0 шт.)"
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
    total_count: int = 0,
    back_category_id: int | None = None,
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

    if back_category_id:
        builder.button(text="◀️ Назад к подкатегориям", callback_data=CategoryCD(id=back_category_id))
    else:
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
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_ticket_admin_kb(ticket_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=TicketActionCD(id=ticket_id, action="approve"))
    builder.button(text="❌ Отклонить", callback_data=TicketActionCD(id=ticket_id, action="reject"))
    builder.adjust(2)
    return builder.as_markup()

def get_order_delivery_kb(order: Order) -> InlineKeyboardMarkup:
    """Кнопки в чеке после покупки под отправленным ZIP-архивом"""
    builder = InlineKeyboardBuilder()
    builder.button(text="⚠️ Открыть диспут", callback_data=OpenDisputeCD(order_id=order.id))
    builder.button(text="❓ Задать вопрос", callback_data=AskQuestionCD(order_id=order.id))
    builder.button(text="⭐️ Оставить отзыв", callback_data=StartReviewCD(order_id=order.id))
    builder.adjust(2, 1)
    return builder.as_markup()

def get_my_orders_tabs_kb(
    current_tab: str,
    orders: list[Order],
    attention_count: int,
    completed_count: int
) -> InlineKeyboardMarkup:
    """Вкладки «Ожидают внимания» и «Завершенные»"""
    builder = InlineKeyboardBuilder()
    
    # 1. Табы переключения
    tab_att_text = f"⏳ Ожидают внимания ({attention_count})" + (" •" if current_tab == "attention" else "")
    tab_comp_text = f"✅ Завершенные ({completed_count})" + (" •" if current_tab == "completed" else "")
    
    builder.button(text=tab_att_text, callback_data=MyOrdersTabCD(tab="attention"))
    builder.button(text=tab_comp_text, callback_data=MyOrdersTabCD(tab="completed"))

    # 2. Список заказов текущей вкладки
    for o in orders:
        if o.status == OrderStatus.HOLD:
            status_badge = "⏳ Холд"
        elif o.status == OrderStatus.DISPUTED:
            status_badge = "🚨 Диспут"
        elif o.status == OrderStatus.QUESTION:
            status_badge = "❓ Вопрос"
        elif o.status == OrderStatus.REFUNDED:
            status_badge = "↩️ Возврат"
        else:
            status_badge = "✅ Закрыт"

        review_badge = " ⭐️" if o.review else ""
        prod_title = o.product.name if o.product else "Аккаунт"
        btn_text = f"{o.order_code} | {prod_title} [${o.amount:.2f}] ({status_badge}){review_badge}"
        builder.button(text=btn_text, callback_data=OrderCD(id=o.id))

    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    
    adjust_pattern = [2]
    for _ in orders:
        adjust_pattern.append(1)
    adjust_pattern.append(1)
    builder.adjust(*adjust_pattern)
    return builder.as_markup()

def get_order_detail_kb(order: Order, can_dispute: bool = True, has_review: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if order.status == OrderStatus.HOLD and can_dispute:
        builder.button(text="⚠️ Открыть диспут", callback_data=OpenDisputeCD(order_id=order.id))
        builder.button(text="❓ Задать вопрос", callback_data=AskQuestionCD(order_id=order.id))
    elif order.status == OrderStatus.DISPUTED:
        builder.button(text="💬 Чат диспута (SLA 24h)", callback_data=OpenDisputeCD(order_id=order.id))
    elif order.status == OrderStatus.QUESTION:
        builder.button(text="💬 Чат вопроса по заказу", callback_data=AskQuestionCD(order_id=order.id))

    # Возможность оставить отзыв (1-5 звезд)
    if not has_review:
        builder.button(text="⭐️ Оставить отзыв", callback_data=StartReviewCD(order_id=order.id))

    builder.button(text="◀️ Назад к заказам", callback_data="my_orders")
    builder.adjust(1)
    return builder.as_markup()

def get_review_rating_kb(order_id: int) -> InlineKeyboardMarkup:
    """Выбор рейтинга от 1 до 5 звезд"""
    builder = InlineKeyboardBuilder()
    for star in range(1, 6):
        builder.button(text=f"⭐️ {star}", callback_data=RateOrderCD(order_id=order_id, rating=star))
    builder.button(text="Пропустить отзыв", callback_data=SkipReviewCD(order_id=order_id))
    builder.adjust(5, 1)
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
    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_dispute_admin_kb(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить в чат спора", callback_data=ReplyDisputeCD(order_id=order_id))
    builder.button(text="🔄 Выдать замену со склада", callback_data=AdminReplaceSelectCD(order_id=order_id, page=1))
    builder.button(text="✅ Вернуть покупателю", callback_data=ResolveDisputeCD(order_id=order_id, action="refund"))
    builder.button(text="❌ Закрыть спор", callback_data=ResolveDisputeCD(order_id=order_id, action="reject"))
    builder.adjust(1, 1, 2)
    return builder.as_markup()

def get_chats_list_kb(orders: list[Order]) -> InlineKeyboardMarkup:
    """Список чатов пользователя по диспутам и вопросам"""
    builder = InlineKeyboardBuilder()
    for o in orders:
        # Белый кружок ТОЛЬКО если есть непрочитанные сообщения покупателем (user_unread == True)
        prefix = "⚪️ " if getattr(o, "user_unread", False) else ""
        kind = "Диспут" if (o.status == OrderStatus.DISPUTED or o.has_dispute) else "Вопрос"
        btn_text = f"{prefix}{kind} по заказу {o.order_code}"
        builder.button(text=btn_text, callback_data=ChatViewCD(order_id=o.id))

    builder.button(text="◀️ Главное меню", callback_data="main_menu")
    adjust_pattern = [1] * len(orders) + [1]
    builder.adjust(*adjust_pattern)
    return builder.as_markup()

def get_chat_view_kb(order: Order) -> InlineKeyboardMarkup:
    """Кнопки внутри конкретного чата"""
    builder = InlineKeyboardBuilder()
    if order.status == OrderStatus.HOLD and not order.has_dispute:
        builder.button(text="⚠️ Открыть диспут", callback_data=OpenDisputeCD(order_id=order.id))
    builder.button(text="🗑 Удалить чат", callback_data=DeleteChatCD(order_id=order.id))
    builder.button(text="◀️ Назад к чатам", callback_data="my_chats")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_replace_accounts_kb(
    order: Order,
    accounts: list[Account],
    page: int = 1,
    page_size: int = 6
) -> InlineKeyboardMarkup:
    """Список аккаунтов со склада для выдачи замены администратором"""
    builder = InlineKeyboardBuilder()
    total_items = len(accounts)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    start = (page - 1) * page_size
    end = start + page_size
    current_page_accounts = accounts[start:end]

    for acc in current_page_accounts:
        # Показываем краткую строку аккаунта
        acc_label = f"🔄 {acc.state} | {acc.name} | {acc.date_info}"
        builder.button(
            text=acc_label,
            callback_data=AdminConfirmReplaceCD(order_id=order.id, account_id=acc.id)
        )

    # Пагинация
    nav_buttons = []
    if page > 1:
        nav_buttons.append(("◀️ Назад", AdminReplaceSelectCD(order_id=order.id, page=page - 1)))
    if page < total_pages:
        nav_buttons.append(("Вперед ▶️", AdminReplaceSelectCD(order_id=order.id, page=page + 1)))

    for text, cd in nav_buttons:
        builder.button(text=text, callback_data=cd)

    builder.button(text="◀️ Отмена (к заказу)", callback_data=OrderCD(id=order.id))

    adjust_pattern = [1] * len(current_page_accounts)
    if nav_buttons:
        adjust_pattern.append(len(nav_buttons))
    adjust_pattern.append(1)
    builder.adjust(*adjust_pattern)
    return builder.as_markup()
