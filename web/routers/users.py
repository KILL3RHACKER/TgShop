from urllib.parse import quote
from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from web.auth import AdminUser
from web.dependencies import get_db

router = APIRouter(prefix="/users", tags=["users"])

def get_templates():
    from web.main import templates
    return templates

@router.get("", response_class=HTMLResponse)
async def list_users(
    request: Request,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    msg: str | None = None,
    msg_type: str = "success",
):
    page = max(1, page)
    offset = (page - 1) * page_size

    conditions = []
    search = q.strip()
    if search:
        search_pattern = f"%{search}%"
        tg_id_val = None
        try:
            tg_id_val = int(search)
        except ValueError:
            pass

        if tg_id_val is not None:
            conditions.append(or_(User.tg_id == tg_id_val, User.username.ilike(search_pattern)))
        else:
            clean_search = search.lstrip("@")
            conditions.append(
                or_(
                    User.username.ilike(f"%{clean_search}%"),
                    User.tron_address.ilike(search_pattern),
                )
            )

    # Total matching
    count_stmt = select(func.count(User.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total_matching = await session.scalar(count_stmt) or 0

    # Total stats
    total_users = await session.scalar(select(func.count(User.id))) or 0
    total_balance = await session.scalar(select(func.coalesce(func.sum(User.balance), 0.0))) or 0.0

    # Paginated query
    stmt = select(User).order_by(desc(User.created_at)).offset(offset).limit(page_size)
    if conditions:
        stmt = stmt.where(*conditions)

    users_res = await session.execute(stmt)
    users = list(users_res.scalars().all())

    total_pages = max(1, (total_matching + page_size - 1) // page_size)

    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={
            "admin": admin,
            "active_tab": "users",
            "users": users,
            "q": search,
            "page": page,
            "page_size": page_size,
            "total_matching": total_matching,
            "total_pages": total_pages,
            "total_users": total_users,
            "total_balance": float(total_balance),
            "msg": msg,
            "msg_type": msg_type,
        }
    )

@router.post("/{tg_id}/adjust-balance")
async def adjust_user_balance(
    tg_id: int,
    admin: AdminUser,
    amount: float = Form(...),
    action: str = Form("add"),  # "add" or "set"
    session: AsyncSession = Depends(get_db),
):
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    if not user:
        msg = quote("Пользователь не найден")
        return RedirectResponse(url=f"/users?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    if action == "set":
        user.balance = max(0.0, amount)
    else:
        user.balance = max(0.0, float(user.balance) + amount)

    await session.commit()
    msg = quote(f"Баланс пользователя {tg_id} обновлен: ${float(user.balance):.2f}")
    return RedirectResponse(url=f"/users?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)
