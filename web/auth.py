import base64
import secrets
from typing import Annotated
from fastapi import Request, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from bot.config import config

COOKIE_NAME = "tg_admin_session"
MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days

serializer = URLSafeTimedSerializer(config.web_secret_key)

class NotAuthenticatedException(Exception):
    def __init__(self, redirect_url: str = "/login"):
        self.redirect_url = redirect_url

def create_session_token(username: str) -> str:
    return serializer.dumps({"username": username})

def verify_session_token(token: str) -> str | None:
    try:
        data = serializer.loads(token, max_age=MAX_AGE_SECONDS)
        return data.get("username")
    except (BadSignature, SignatureExpired, Exception):
        return None

def verify_basic_auth(auth_header: str | None) -> str | None:
    if not auth_header or not auth_header.startswith("Basic "):
        return None
    try:
        encoded = auth_header[6:].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
        if (
            secrets.compare_digest(username, config.admin_web_user)
            and secrets.compare_digest(password, config.admin_web_password)
        ):
            return username
    except Exception:
        return None
    return None

async def get_current_admin(request: Request) -> str:
    # 1. Check signed cookie
    token = request.cookies.get(COOKIE_NAME)
    if token:
        username = verify_session_token(token)
        if username and secrets.compare_digest(username, config.admin_web_user):
            return username

    # 2. Check HTTP Basic Auth header
    auth_header = request.headers.get("Authorization")
    basic_user = verify_basic_auth(auth_header)
    if basic_user:
        return basic_user

    # If API / JSON request, raise 401
    accept = request.headers.get("accept", "")
    if "application/json" in accept or request.url.path.startswith("/api/"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    # For browser HTML requests, redirect to login
    raise NotAuthenticatedException(redirect_url="/login")

AdminUser = Annotated[str, Depends(get_current_admin)]
