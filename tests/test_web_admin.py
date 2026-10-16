import io
import sys
import zipfile
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from bot.config import config
from bot.database.models import Base, User, Category, Subcategory, Product, Account, TopUpTicket, TicketStatus, Order, OrderStatus
from web.main import app
from web.dependencies import get_db

def test_full_flow():
    # Setup in-memory SQLite DB
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    test_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with test_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async def init():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from bot.database.seed import seed_data
        async with test_session() as session:
            await seed_data(session)

            # Create a user with balance
            u = User(
                tg_id=777888999,
                username="test_buyer",
                balance=100.0,
                tron_address="TTestAddressXYZ",
                tron_private_key="testprivkey"
            )
            session.add(u)

            # Create a pending ticket
            t = TopUpTicket(
                user_id=777888999,
                amount=25.0,
                status=TicketStatus.PENDING,
                expires_at=datetime.utcnow() + timedelta(minutes=config.ticket_expire_minutes)
            )
            session.add(t)

            # Create an order with 24h hold
            order = Order(
                user_id=777888999,
                product_id=1,
                amount=3.50,
                status=OrderStatus.HOLD,
                hold_until=datetime.utcnow() + timedelta(hours=24)
            )
            session.add(order)
            await session.commit()

    asyncio.run(init())

    client = TestClient(app, follow_redirects=False)

    # 1. Unauthenticated redirect to login
    resp = client.get("/")
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]

    # 2. Login POST success
    resp = client.post(
        "/login",
        data={"username": config.admin_web_user, "password": config.admin_web_password, "next": "/"}
    )
    assert resp.status_code == 303
    auth_cookie = resp.cookies["tg_admin_session"]
    client.cookies.set("tg_admin_session", auth_cookie)

    # 3. Dashboard view
    resp = client.get("/")
    assert resp.status_code == 200
    assert "TG Shop" in resp.text

    # 4. Products view (3-tier catalog)
    resp = client.get("/products")
    assert resp.status_code == 200
    assert "Главные категории" in resp.text
    assert "Подкатегории" in resp.text
    assert "Список товаров" in resp.text

    # 5. Accounts view
    resp = client.get("/accounts")
    assert resp.status_code == 200
    assert "JOHN DOE" in resp.text or "MICHAEL SMITH" in resp.text

    # 6. Upload ZIP Accounts: supports any date format, normalized to DD.MM
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("test_zip.txt", "CA | zip user 1 | 11/11:tail\nNY, zip user 2, 12.12 extra\n")
    zip_buf.seek(0)

    resp = client.post(
        "/accounts/upload-archive",
        data={"product_id": 1, "upload_mode": "specific_product"},
        files={"archive_file": ("accounts.zip", zip_buf, "application/zip")}
    )
    assert resp.status_code == 303

    resp = client.get("/accounts?q=ZIP USER")
    assert resp.status_code == 200
    assert "ZIP USER 1" in resp.text
    assert "ZIP USER 2" in resp.text
    assert "11.11" in resp.text
    assert "12.12" in resp.text

    # 7. Delete by list: matches DD.MM
    resp = client.post(
        "/accounts/delete-list",
        data={
            "product_id": 1,
            "list_data": "CA | ZIP USER 1 | 11.11\nNY | ZIP USER 2 | 12.12"
        }
    )
    assert resp.status_code == 303

    resp = client.get("/accounts?q=ZIP USER")
    assert "ZIP USER 1" not in resp.text
    assert "ZIP USER 2" not in resp.text

    # 8. Tickets view & Approve
    resp = client.get("/tickets")
    assert resp.status_code == 200
    assert "777888999" in resp.text

    resp = client.post("/tickets/1/approve")
    assert resp.status_code == 303

    resp = client.get("/tickets")
    assert "Одобрен" in resp.text

    # 9. Orders view & Dispute
    resp = client.get("/orders")
    assert resp.status_code == 200
    assert "-0001" in resp.text or "Диспут" in resp.text

    print("ALL WEB ADMIN 3-TIER AND ARCHIVE UPLOAD TESTS PASSED!")

if __name__ == "__main__":
    test_full_flow()
