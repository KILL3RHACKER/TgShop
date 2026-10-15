"""Routers for Web Admin Panel."""
from web.routers.auth import router as auth_router
from web.routers.dashboard import router as dashboard_router
from web.routers.accounts import router as accounts_router
from web.routers.tickets import router as tickets_router
from web.routers.products import router as products_router
from web.routers.users import router as users_router

__all__ = [
    "auth_router",
    "dashboard_router",
    "accounts_router",
    "tickets_router",
    "products_router",
    "users_router",
]
