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

class AskQuestionCD(CallbackData, prefix="ord_q"):
    order_id: int

class MyOrdersTabCD(CallbackData, prefix="ord_tab"):
    tab: str  # "attention" | "completed"
    page: int = 1

class StartReviewCD(CallbackData, prefix="ord_rev_start"):
    order_id: int

class RateOrderCD(CallbackData, prefix="ord_rate"):
    order_id: int
    rating: int  # 1..5

class SkipReviewCD(CallbackData, prefix="ord_rev_skip"):
    order_id: int

class ChatViewCD(CallbackData, prefix="cht_v"):
    order_id: int

class ChatReplyCD(CallbackData, prefix="cht_r"):
    order_id: int

class AdminReplaceSelectCD(CallbackData, prefix="adm_repl_sel"):
    order_id: int
    page: int = 1

class AdminConfirmReplaceCD(CallbackData, prefix="adm_repl_cfm"):
    order_id: int
    account_id: int
