from urllib.parse import quote
from fastapi import APIRouter, Request, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import TopUpTicket, TicketStatus, User
from bot.services.ticket import approve_ticket, reject_ticket
from web.auth import AdminUser
from web.dependencies import get_db, get_bot

router = APIRouter(prefix="/tickets", tags=["tickets"])

def get_templates():
    from web.main import templates
    return templates

@router.get("", response_class=HTMLResponse)
async def list_tickets(
    request: Request,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    status_filter: str = "all",
    msg: str | None = None,
    msg_type: str = "success",
):
    stmt = select(TopUpTicket).order_by(desc(TopUpTicket.created_at))

    if status_filter != "all":
        try:
            target_status = TicketStatus(status_filter)
            stmt = stmt.where(TopUpTicket.status == target_status)
        except ValueError:
            pass

    tickets_res = await session.execute(stmt)
    tickets = list(tickets_res.scalars().all())

    # Map user data
    user_ids = [t.user_id for t in tickets]
    users_map = {}
    if user_ids:
        users_res = await session.execute(select(User).where(User.tg_id.in_(user_ids)))
        users_map = {u.tg_id: u for u in users_res.scalars().all()}

    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="tickets.html",
        context={
            "admin": admin,
            "active_tab": "tickets",
            "tickets": tickets,
            "users_map": users_map,
            "status_filter": status_filter,
            "msg": msg,
            "msg_type": msg_type,
        }
    )

@router.post("/{ticket_id}/approve")
async def approve_ticket_endpoint(
    ticket_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
):
    bot = get_bot()
    success, message = await approve_ticket(session, ticket_id, bot=bot)
    msg_type = "success" if success else "error"
    return RedirectResponse(
        url=f"/tickets?msg={quote(message)}&msg_type={msg_type}",
        status_code=status.HTTP_303_SEE_OTHER,
    )

@router.post("/{ticket_id}/reject")
async def reject_ticket_endpoint(
    ticket_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
):
    bot = get_bot()
    success, message = await reject_ticket(session, ticket_id, bot=bot)
    msg_type = "warning" if success else "error"
    return RedirectResponse(
        url=f"/tickets?msg={quote(message)}&msg_type={msg_type}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
