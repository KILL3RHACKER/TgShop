from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Order, OrderStatus, DisputeMessage, User
from web.auth import AdminUser
from web.dependencies import get_db, get_bot

router = APIRouter(prefix="/orders", tags=["orders"])

def get_templates():
    from web.main import templates
    return templates

@router.get("", response_class=HTMLResponse)
async def list_orders(
    request: Request,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    status_filter: str = "all",
    q: str = "",
    msg: str | None = None,
    msg_type: str = "success",
):
    conditions = []
    if status_filter != "all":
        try:
            target_status = OrderStatus(status_filter)
            conditions.append(Order.status == target_status)
        except ValueError:
            pass

    search = q.strip()
    if search:
        if search.isdigit():
            conditions.append(or_(Order.id == int(search), Order.user_id == int(search)))
        else:
            # Match on username or account data
            user_ids_res = await session.execute(
                select(User.tg_id).where(User.username.ilike(f"%{search}%"))
            )
            matching_user_ids = list(user_ids_res.scalars().all())
            conditions.append(Order.user_id.in_(matching_user_ids))

    stmt = select(Order).order_by(desc(Order.created_at))
    if conditions:
        stmt = stmt.where(*conditions)

    orders_res = await session.execute(stmt)
    orders = list(orders_res.scalars().all())

    # Metrics
    total_orders = await session.scalar(select(func.count(Order.id))) or 0
    hold_orders = await session.scalar(
        select(func.count(Order.id)).where(Order.status == OrderStatus.HOLD)
    ) or 0
    dispute_orders = await session.scalar(
        select(func.count(Order.id)).where(Order.status == OrderStatus.DISPUTED)
    ) or 0
    hold_sum = await session.scalar(
        select(func.coalesce(func.sum(Order.amount), 0.0)).where(Order.status == OrderStatus.HOLD)
    ) or 0.0

    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={
            "admin": admin,
            "active_tab": "orders",
            "orders": orders,
            "now": datetime.utcnow(),
            "status_filter": status_filter,
            "q": search,
            "total_orders": total_orders,
            "hold_orders": hold_orders,
            "dispute_orders": dispute_orders,
            "hold_sum": hold_sum,
            "msg": msg,
            "msg_type": msg_type,
        }
    )

@router.post("/{order_id}/message")
async def send_dispute_message(
    order_id: int,
    admin: AdminUser,
    text: str = Form(...),
    session: AsyncSession = Depends(get_db),
    bot = Depends(get_bot),
):
    order = await session.get(Order, order_id)
    if not order:
        msg = quote("Заказ не найден")
        return RedirectResponse(url=f"/orders?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    clean_text = text.strip()
    if not clean_text:
        msg = quote("Текст сообщения не может быть пустым")
        return RedirectResponse(url=f"/orders?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    disp_msg = DisputeMessage(
        order_id=order.id,
        sender_id=admin.tg_id,
        sender_role="admin",
        text=clean_text,
    )
    session.add(disp_msg)
    await session.commit()

    # Send telegram message to client
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from bot.keyboards.callbacks import OpenDisputeCD
        kb = InlineKeyboardBuilder()
        kb.button(text="💬 Ответить в спор", callback_data=OpenDisputeCD(order_id=order.id))
        kb.adjust(1)

        await bot.send_message(
            chat_id=order.user_id,
            text=(
                f"👨‍💼 <b>Сообщение от администрации по заказу #ORDER-{order.id}:</b>\n\n"
                f"{clean_text}"
            ),
            reply_markup=kb.as_markup()
        )
    except Exception:
        pass

    msg = quote(f"Ответ отправлен покупателю по заказу #{order.id}")
    return RedirectResponse(url=f"/orders?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/{order_id}/refund")
async def refund_order(
    order_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    bot = Depends(get_bot),
):
    order = await session.get(Order, order_id)
    if not order:
        msg = quote("Заказ не найден")
        return RedirectResponse(url=f"/orders?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    if order.status == OrderStatus.REFUNDED:
        msg = quote("По этому заказу уже был произведен возврат")
        return RedirectResponse(url=f"/orders?msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

    order.status = OrderStatus.REFUNDED
    order.resolved_at = datetime.utcnow()

    user = await session.scalar(select(User).where(User.tg_id == order.user_id))
    if user:
        user.balance = float(user.balance) + float(order.amount)

    await session.commit()

    # Notify user in telegram
    try:
        await bot.send_message(
            chat_id=order.user_id,
            text=(
                f"🎉 <b>Диспут по заказу #ORDER-{order.id} решен в вашу пользу!</b>\n\n"
                f"Средства в размере <b>${order.amount:.2f} USDT</b> возвращены на ваш баланс магазина."
            )
        )
    except Exception:
        pass

    msg = quote(f"Возврат по заказу #{order.id} на сумму ${order.amount:.2f} успешно выполнен")
    return RedirectResponse(url=f"/orders?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/{order_id}/reject")
async def reject_dispute(
    order_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    bot = Depends(get_bot),
):
    order = await session.get(Order, order_id)
    if not order:
        msg = quote("Заказ не найден")
        return RedirectResponse(url=f"/orders?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    order.status = OrderStatus.COMPLETED
    order.resolved_at = datetime.utcnow()
    await session.commit()

    try:
        await bot.send_message(
            chat_id=order.user_id,
            text=(
                f"❌ <b>Диспут по заказу #ORDER-{order.id} закрыт администрацией.</b>\n\n"
                f"Решение принято в пользу магазина. Средства не подлежат возврату."
            )
        )
    except Exception:
        pass

    msg = quote(f"Спор по заказу #{order.id} закрыт в пользу магазина")
    return RedirectResponse(url=f"/orders?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)
