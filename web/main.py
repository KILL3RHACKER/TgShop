from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot.database.base import init_db
from web.auth import NotAuthenticatedException
from web.dependencies import close_bot
from web.routers import (
    auth_router,
    dashboard_router,
    accounts_router,
    tickets_router,
    products_router,
    users_router,
    orders_router,
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Template custom filters
def filter_datetime(val, fmt: str = "%d.%m.%Y %H:%M"):
    if not val:
        return "—"
    try:
        return val.strftime(fmt)
    except Exception:
        return str(val)

def filter_currency(val):
    try:
        return f"${float(val):.2f}"
    except Exception:
        return "$0.00"

templates.env.filters["dt"] = filter_datetime
templates.env.filters["currency"] = filter_currency

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database schema is initialized on startup
    await init_db()
    yield
    # Cleanup background telegram bot session on shutdown
    await close_bot()

app = FastAPI(
    title="TG Shop Admin Control Panel",
    description="Control Panel for Telegram Shop Bot inventory, tickets and products",
    version="1.0.0",
    lifespan=lifespan,
)

# Static files (create directory if doesn't exist)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Auth exception handler: redirects browser to login
@app.exception_handler(NotAuthenticatedException)
async def not_authenticated_handler(request: Request, exc: NotAuthenticatedException):
    return RedirectResponse(
        url=f"{exc.redirect_url}?next={request.url.path}",
        status_code=303
    )

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# Include routers
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(accounts_router)
app.include_router(tickets_router)
app.include_router(products_router)
app.include_router(users_router)
app.include_router(orders_router)
