# -*- coding: utf-8 -*-
import io
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import zipfile
from starlette.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from bot.config import config
from bot.database.models import Base, Category, Product, Account, TopUpTicket, TicketStatus, User
from bot.database.seed import seed_data
from web.main import app
from web.dependencies import get_db

# Setup test DB
test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

async def override_get_db():
    async with TestSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db

def test_full_flow():
    import asyncio

    async def init():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with TestSessionLocal() as session:
            await seed_data(session)
            # Create a test user
            u = User(
                tg_id=777888999,
                username="test_buyer",
                balance=50.0,
                tron_address="TTestTronBuyer1234567890",
                tron_private_key="privkeybuyer"
            )
            session.add(u)
            from datetime import datetime, timedelta
            # Create a pending ticket
            t = TopUpTicket(
                user_id=777888999,
                amount=25.0,
                status=TicketStatus.PENDING,
                expires_at=datetime.utcnow() + timedelta(minutes=config.ticket_expire_minutes)
            )
            session.add(t)
            await session.commit()

    asyncio.run(init())

    client = TestClient(app, follow_redirects=False)

    # 1. Unauthenticated redirect to login
    resp = client.get("/")
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]

    # 2. Login page GET
    resp = client.get("/login")
    assert resp.status_code == 200
    assert 'action="/login"' in resp.text
    assert 'name="username"' in resp.text

    # 3. Login POST failure
    resp = client.post("/login", data={"username": "wrong", "password": "bad", "next": "/"})
    assert resp.status_code == 401

    # 4. Login POST success
    resp = client.post(
        "/login",
        data={"username": config.admin_web_user, "password": config.admin_web_password, "next": "/"}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "tg_admin_session" in resp.cookies

    # Now use authenticated client with cookies
    auth_cookie = resp.cookies["tg_admin_session"]
    client.cookies.set("tg_admin_session", auth_cookie)

    # 5. Dashboard view
    resp = client.get("/")
    assert resp.status_code == 200
    assert "TG Shop" in resp.text

    # 6. Accounts view
    resp = client.get("/accounts")
    assert resp.status_code == 200
    assert "JOHN DOE" in resp.text

    # 7. Upload Paste Accounts
    resp = client.post(
        "/accounts/upload-paste",
        data={
            "product_id": 1,
            "paste_text": "TEST PASTE 1, 10/10\nTEST PASTE 2, 20/20"
        }
    )
    assert resp.status_code == 303

    resp = client.get("/accounts?q=TEST PASTE")
    assert resp.status_code == 200
    assert "TEST PASTE 1" in resp.text
    assert "TEST PASTE 2" in resp.text

    # 8. Upload ZIP Accounts
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("test_zip.txt", "ZIP USER 1, 11/11\nZIP USER 2, 12/12\n")
    zip_buf.seek(0)

    resp = client.post(
        "/accounts/upload-zip",
        data={"product_id": 1},
        files={"zip_file": ("accounts.zip", zip_buf, "application/zip")}
    )
    assert resp.status_code == 303

    resp = client.get("/accounts?q=ZIP USER")
    assert resp.status_code == 200
    assert "ZIP USER 1" in resp.text
    assert "ZIP USER 2" in resp.text

    # 9. Delete by list
    resp = client.post(
        "/accounts/delete-by-list",
        data={
            "product_id": 1,
            "delete_text": "ZIP USER 1, 11/11\nZIP USER 2, 12/12"
        }
    )
    assert resp.status_code == 303

    resp = client.get("/accounts?q=ZIP USER")
    assert resp.status_code == 200
    assert "ZIP USER 1" not in resp.text

    # 10. Tickets view & Approve
    resp = client.get("/tickets")
    assert resp.status_code == 200
    assert "#1" in resp.text or "#" in resp.text

    resp = client.post("/tickets/1/approve")
    assert resp.status_code == 303

    # Check user balance increased by 25.0 -> 50.0 + 25.0 = 75.0
    resp = client.get("/users?q=777888999")
    assert resp.status_code == 200
    assert "$75.00" in resp.text

    # 11. Adjust balance
    resp = client.post("/users/777888999/adjust-balance", data={"action": "add", "amount": 10.0})
    assert resp.status_code == 303

    resp = client.get("/users?q=777888999")
    assert resp.status_code == 200
    assert "$85.00" in resp.text

    # 12. Products & Categories CRUD
    resp = client.get("/products")
    assert resp.status_code == 200
    assert "products" in resp.text.lower() or "категории" in resp.text.lower()

    # Create category
    resp = client.post("/products/categories/create", data={"name": "New Test Cat"})
    assert resp.status_code == 303

    # Create product
    resp = client.post(
        "/products/create",
        data={
            "category_id": 1,
            "name": "Super VPN Pro",
            "price": 12.50,
            "description": "Fastest VPN ever",
            "content": "vless://super-key"
        }
    )
    assert resp.status_code == 303

    resp = client.get("/products")
    assert resp.status_code == 200
    assert "Super VPN Pro" in resp.text
    assert "$12.50" in resp.text

    # 13. Logout
    resp = client.get("/logout")
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]

    print("[OK] All web integration tests passed successfully!")

if __name__ == "__main__":
    test_full_flow()
