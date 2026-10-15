from aiogram.filters.callback_data import CallbackData

class CategoryCD(CallbackData, prefix="cat"):
    id: int

class ProductCD(CallbackData, prefix="prod"):
    id: int

class BuyProductCD(CallbackData, prefix="buy"):
    id: int

class TicketActionCD(CallbackData, prefix="tck"):
    id: int
    action: str  # "approve" | "reject"
