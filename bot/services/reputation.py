from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.database.models import Order, OrderStatus, OrderReview

async def get_user_reputation(session: AsyncSession, user_id: int) -> dict:
    """
    Рассчитывает репутацию покупателя для Администратора при рассмотрении споров:
    - Всего заказов и сумма покупок ($)
    - История диспутов (всего, возвратов, закрыто в пользу магазина)
    - Оценки в отзывах и средний рейтинг
    - Уровень доверия (🟢 Высокий, 🟡 Средний, 🔴 Подозрительный)
    """
    orders_res = await session.execute(
        select(Order).where(Order.user_id == user_id)
    )
    user_orders = list(orders_res.scalars().all())
    total_orders = len(user_orders)
    total_spent = sum(float(o.amount) for o in user_orders if o.status in (OrderStatus.HOLD, OrderStatus.COMPLETED))

    disputes_count = sum(1 for o in user_orders if o.has_dispute or o.disputed_at or o.status in (OrderStatus.DISPUTED, OrderStatus.REFUNDED))
    refunded_count = sum(1 for o in user_orders if o.status == OrderStatus.REFUNDED)
    rejected_count = sum(1 for o in user_orders if (o.has_dispute or o.disputed_at) and o.status == OrderStatus.COMPLETED)

    rev_res = await session.execute(
        select(OrderReview).where(OrderReview.user_id == user_id)
    )
    reviews = list(rev_res.scalars().all())
    avg_rating = (sum(r.rating for r in reviews) / len(reviews)) if reviews else 5.0
    reviews_count = len(reviews)

    if disputes_count >= 3 and refunded_count >= 2:
        trust_label = "🔴 Подозрительный (частые споры)"
    elif total_orders >= 5 and disputes_count <= 1:
        trust_label = "🟢 Высокий (постоянный клиент)"
    elif total_orders >= 2:
        trust_label = "🟡 Средний (проверенный покупатель)"
    else:
        trust_label = "⚪️ Новый покупатель"

    return {
        "total_orders": total_orders,
        "total_spent": total_spent,
        "disputes_count": disputes_count,
        "refunded_count": refunded_count,
        "rejected_count": rejected_count,
        "reviews_count": reviews_count,
        "avg_rating": avg_rating,
        "trust_label": trust_label,
    }

def format_reputation_text(rep: dict, username: str | None = None, user_id: int | None = None) -> str:
    user_str = f"@{username}" if username else str(user_id or "—")
    return (
        f"📊 <b>ДОСЬЕ РЕПУТАЦИИ КЛИЕНТА ({user_str})</b>\n"
        f"• Уровень доверия: <b>{rep['trust_label']}</b>\n"
        f"• Всего заказов: <b>{rep['total_orders']} шт.</b> на сумму <b>${rep['total_spent']:.2f}</b>\n"
        f"• Диспуты: <b>{rep['disputes_count']}</b> (Возвратов: {rep['refunded_count']}, В пользу шопа: {rep['rejected_count']})\n"
        f"• Отзывы: <b>{rep['reviews_count']} шт.</b> (Средняя оценка: ⭐️ <b>{rep['avg_rating']:.1f}</b>)"
    )
