from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, Product, Account, TopUpTicket, TicketStatus, Category
from web.auth import AdminUser
from web.dependencies import get_db

router = APIRouter(tags=["dashboard"])

def get_templates():
    from web.main import templates
    return templates

@router.get("/", response_class=HTMLResponse)
async def dashboard_view(
    request: Request,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db)
):
    # Total Users
    total_users = await session.scalar(select(func.count(User.id))) or 0

    # Total Sold Accounts
    sold_items = await session.scalar(
        select(func.count(Account.id)).where(Account.is_sold == True)
    ) or 0

    # Available Stock
    available_stock = await session.scalar(
        select(func.count(Account.id)).where(Account.is_sold == False)
    ) or 0

    # Total Revenue (sum of product prices for sold accounts)
    revenue_res = await session.scalar(
        select(func.coalesce(func.sum(Product.price), 0.0))
        .select_from(Account)
        .join(Product, Account.product_id == Product.id)
        .where(Account.is_sold == True)
    )
    total_revenue = float(revenue_res or 0.0)

    # Pending Tickets
    pending_tickets = await session.scalar(
        select(func.count(TopUpTicket.id)).where(TopUpTicket.status == TicketStatus.PENDING)
    ) or 0

    # Recent Tickets (limit 6)
    recent_tickets_res = await session.execute(
        select(TopUpTicket)
        .order_by(desc(TopUpTicket.created_at))
        .limit(6)
    )
    recent_tickets = list(recent_tickets_res.scalars().all())

    # Get users map for recent tickets
    user_ids = [t.user_id for t in recent_tickets]
    users_map = {}
    if user_ids:
        users_res = await session.execute(select(User).where(User.tg_id.in_(user_ids)))
        users_map = {u.tg_id: u for u in users_res.scalars().all()}

    # Recent Sold Accounts (limit 6)
    recent_sold_res = await session.execute(
        select(Account)
        .where(Account.is_sold == True)
        .order_by(desc(Account.sold_at))
        .limit(6)
    )
    recent_sold = list(recent_sold_res.scalars().all())

    # Products with stock counts
    products_res = await session.execute(select(Product).order_by(Product.name))
    all_products = list(products_res.scalars().all())

    counts_res = await session.execute(
        select(Account.product_id, func.count(Account.id))
        .where(Account.is_sold == False)
        .group_by(Account.product_id)
    )
    stock_counts = dict(counts_res.all())

    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "admin": admin,
            "active_tab": "dashboard",
            "total_users": total_users,
            "total_revenue": total_revenue,
            "available_stock": available_stock,
            "sold_items": sold_items,
            "pending_tickets": pending_tickets,
            "recent_tickets": recent_tickets,
            "users_map": users_map,
            "recent_sold": recent_sold,
            "products": all_products,
            "stock_counts": stock_counts,
        }
    )
