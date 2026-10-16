from aiogram.filters.callback_data import CallbackData

class CategoryCD(CallbackData, prefix="cat"):
    id: int

class SubcategoryCD(CallbackData, prefix="subcat"):
    id: int

class ProductCD(CallbackData, prefix="prod"):
    id: int
    page: int = 1

class AccountCD(CallbackData, prefix="acc"):
    id: int

class BuyAccountCD(CallbackData, prefix="buy_acc"):
    id: int

class FastBuyCD(CallbackData, prefix="fast_buy"):
    product_id: int

class TicketActionCD(CallbackData, prefix="tck"):
    id: int
    action: str  # "approve" | "reject"

class OrderCD(CallbackData, prefix="ord"):
    id: int

class OpenDisputeCD(CallbackData, prefix="dsp_open"):
    order_id: int

class ReplyDisputeCD(CallbackData, prefix="dsp_reply"):
    order_id: int

class ResolveDisputeCD(CallbackData, prefix="dsp_res"):
    order_id: int
    action: str  # "refund" | "reject"
