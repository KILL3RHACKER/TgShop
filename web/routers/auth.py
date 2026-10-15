from fastapi import APIRouter, Request, Form, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import secrets

from bot.config import config
from web.auth import COOKIE_NAME, MAX_AGE_SECONDS, create_session_token, verify_session_token

router = APIRouter(tags=["auth"])

def get_templates():
    from web.main import templates
    return templates

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    # If already logged in, redirect to next or home
    token = request.cookies.get(COOKIE_NAME)
    if token:
        username = verify_session_token(token)
        if username and secrets.compare_digest(username, config.admin_web_user):
            return RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)

    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next": next, "error": None}
    )

@router.post("/login", response_class=HTMLResponse)
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/")
):
    templates = get_templates()
    if not (
        secrets.compare_digest(username.strip(), config.admin_web_user)
        and secrets.compare_digest(password.strip(), config.admin_web_password)
    ):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next": next,
                "error": "Неверный логин или пароль",
                "username": username,
            },
            status_code=status.HTTP_401_UNAUTHORIZED
        )

    redirect_url = next if next and next.startswith("/") else "/"
    response = RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    token = create_session_token(username.strip())
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # Can work on HTTP locally
    )
    return response

@router.get("/logout")
@router.post("/logout")
async def logout_action():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response
