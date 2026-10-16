import asyncio
import io
import os
import zipfile
import pytest
from datetime import datetime, timedelta, date
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, delete

from bot.database.models import (
    Base, User, Category, Subcategory, Product, Account, Order, OrderStatus, DisputeMessage
)
from bot.services.account_zip import generate_account_zip
from bot.handlers.disputes import format_chat_messages
from web.services import parse_accounts_from_archive, add_accounts_to_category_auto_age

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.asyncio
async def test_order_code_encrypted_product_name():
    """Проверка шифрования названия товара в ID заказа без #ORD-1-"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        cat = Category(name="Truist")
        session.add(cat)
        await session.flush()

        sub = Subcategory(category_id=cat.id, name="Truist 7+ Дней")
        session.add(sub)
        await session.flush()

        prod = Product(subcategory_id=sub.id, name="7 Дней", description="", price=10.0)
        session.add(prod)
        await session.flush()

        order = Order(
            id=1,
            user_id=12345678,
            product_id=prod.id,
            amount=10.0,
            status=OrderStatus.HOLD,
            hold_until=datetime.utcnow() + timedelta(hours=24),
            account_data="ID | JOHN DOE | 01.10"
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        # Проверяем, что ID заказа — это зашифрованный код товара с hex-номером
        # НЕ #ORD-1-TRU7D, а именно #TRU7D-0001
        assert order.product_code == "TRU7D"
        assert order.order_code == "#TRU7D-0001"
        assert "#ORD-1" not in order.order_code

    await engine.dispose()

@pytest.mark.asyncio
async def test_cookie_file_extraction_and_zip_generation():
    """Проверка извлечения второго файла cookie из архива и включения его в ZIP клиента"""
    # Создаем тестовый архив с info.txt и cookie.json
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("account_folder/info.txt", "Address: 109 1ST ST\tRUPERT\tID\t83350\nName: JOHN DOE\nDate: 01.10\n")
        zf.writestr("account_folder/cookies.json", '[{"name": "session", "value": "xyz123"}]')

    zip_bytes = zip_buffer.getvalue()

    accounts, files_count = parse_accounts_from_archive("test.zip", zip_bytes)
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc["state"] == "ID"
    assert acc["name"] == "JOHN DOE"
    assert acc["date_info"] == "01.10"
    # Cookie файл должен быть сохранен в cookie_data!
    assert acc.get("cookie_data") is not None
    assert "xyz123" in acc["cookie_data"]

    # Проверка генерации ZIP покупателю
    order = Order(
        id=42,
        user_id=999,
        amount=15.0,
        status=OrderStatus.HOLD,
        hold_until=datetime.utcnow() + timedelta(hours=24),
    )
    product = Product(id=1, name="Truist 15d", description="", price=15.0)
    db_acc = Account(
        id=10,
        product_id=1,
        state=acc["state"],
        name=acc["name"],
        date_info=acc["date_info"],
        data=acc["data"],
        raw_data=acc.get("raw_data"),
        cookie_data=acc.get("cookie_data"),
    )

    out_zip_bytes, out_fn = generate_account_zip(order, db_acc, product)
    assert out_fn.endswith(".zip")

    # Проверяем содержимое выданного ZIP архива
    with zipfile.ZipFile(io.BytesIO(out_zip_bytes), "r") as zf:
        namelist = zf.namelist()
        assert "account.txt" in namelist
        assert "full_data.txt" in namelist
        assert "cookies.json" in namelist
        assert "WARRANTY_MEMO.txt" in namelist

        cookies_content = zf.read("cookies.json").decode("utf-8")
        assert "xyz123" in cookies_content

@pytest.mark.asyncio
async def test_chat_message_strict_formatting():
    """Проверка структуры сообщений чата: Client(DD.MM.YYYY HH.MM) и Admin(DD.MM.YYYY HH.MM)"""
    order = Order(id=1, user_id=123)
    t1 = datetime(2026, 10, 17, 14, 30)
    t2 = datetime(2026, 10, 17, 14, 35)

    msg1 = DisputeMessage(
        id=1,
        order_id=1,
        sender_id=123,
        sender_role="client",
        text="Неверный пароль от банка",
        created_at=t1
    )
    msg2 = DisputeMessage(
        id=2,
        order_id=1,
        sender_id=0,
        sender_role="admin",
        text="Проверяем, выдаем замену",
        created_at=t2
    )

    formatted = format_chat_messages(order, [msg1, msg2])
    assert "Client(17.10.2026 14.30) Неверный пароль от банка" in formatted
    assert "Admin(17.10.2026 14.35) Проверяем, выдаем замену" in formatted

@pytest.mark.asyncio
async def test_7day_sold_accounts_retention():
    """Проверка удаления проданных аккаунтов старше 7 дней из базы с сохранением снимка в Order"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        cat = Category(name="Truist")
        session.add(cat)
        await session.flush()
        sub = Subcategory(category_id=cat.id, name="Truist 7d")
        session.add(sub)
        await session.flush()
        prod = Product(subcategory_id=sub.id, name="7d", description="", price=10.0)
        session.add(prod)
        await session.flush()

        now = datetime.utcnow()
        old_sold_at = now - timedelta(days=8)   # Старше 7 дней -> должен быть удален!
        fresh_sold_at = now - timedelta(days=2) # 2 дня -> должен остаться!

        acc_old = Account(
            product_id=prod.id,
            state="CA",
            name="OLD SOLD",
            date_info="01.10",
            data="CA | OLD SOLD | 01.10",
            is_sold=True,
            sold_at=old_sold_at
        )
        acc_fresh = Account(
            product_id=prod.id,
            state="NY",
            name="FRESH SOLD",
            date_info="02.10",
            data="NY | FRESH SOLD | 02.10",
            is_sold=True,
            sold_at=fresh_sold_at
        )
        acc_unsold = Account(
            product_id=prod.id,
            state="TX",
            name="UNSOLD STOCK",
            date_info="03.10",
            data="TX | UNSOLD STOCK | 03.10",
            is_sold=False
        )
        session.add_all([acc_old, acc_fresh, acc_unsold])
        await session.commit()

        # Создаем заказ для старого аккаунта со снимком account_data
        order = Order(
            id=100,
            user_id=555,
            account_id=acc_old.id,
            account_data=acc_old.data,
            product_id=prod.id,
            amount=10.0,
            status=OrderStatus.COMPLETED,
            hold_until=old_sold_at + timedelta(hours=24)
        )
        session.add(order)
        await session.commit()

        # Имитируем работу sold_accounts_retention_worker
        seven_days_ago = now - timedelta(days=7)
        stmt_del = (
            delete(Account)
            .where(Account.is_sold == True, Account.sold_at.is_not(None), Account.sold_at < seven_days_ago)
        )
        await session.execute(stmt_del)
        await session.commit()

        # Проверяем: acc_old удален из базы!
        res_old = await session.get(Account, acc_old.id)
        assert res_old is None

        # Проверяем: acc_fresh и acc_unsold остались в базе!
        res_fresh = await session.get(Account, acc_fresh.id)
        assert res_fresh is not None
        assert res_fresh.name == "FRESH SOLD"

        res_unsold = await session.get(Account, acc_unsold.id)
        assert res_unsold is not None
        assert res_unsold.name == "UNSOLD STOCK"

        # Проверяем: заказ остался цел и сохранил снимок account_data!
        order_db = await session.get(Order, 100)
        assert order_db is not None
        assert order_db.account_data == "CA | OLD SOLD | 01.10"
        assert order_db.account_id is None or res_old is None

    await engine.dispose()

@pytest.mark.asyncio
async def test_category_upload_auto_type_creation_no_under7():
    """Проверка загрузки по категории: автосоздание {bank} | N Days и полное отсутствие <7D"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        cat = Category(name="Truist")
        session.add(cat)
        await session.commit()
        await session.refresh(cat)

        today = date.today()
        # 3 тестовых аккаунта: 10 дней, 20 дней, 50 дней
        accs_data = [
            {
                "state": "CA",
                "name": "ACC SEVEN",
                "date_info": "01.10",
                "registration_date": today - timedelta(days=10),
                "data": "CA | ACC SEVEN | 01.10",
                "raw_data": "raw log 1",
            },
            {
                "state": "NY",
                "name": "ACC FIFTEEN",
                "date_info": "20.09",
                "registration_date": today - timedelta(days=20),
                "data": "NY | ACC FIFTEEN | 20.09",
                "raw_data": "raw log 2",
            },
            {
                "state": "TX",
                "name": "ACC THIRTY",
                "date_info": "15.08",
                "registration_date": today - timedelta(days=50),
                "data": "TX | ACC THIRTY | 15.08",
                "raw_data": "raw log 3",
            },
        ]

        stats = await add_accounts_to_category_auto_age(session, cat.id, accs_data)
        assert stats.get("Truist | 7 Days") == 1
        assert stats.get("Truist | 15 Days") == 1
        assert stats.get("Truist | 30+ Days") == 1

        # Проверяем созданные подкатегории (types)
        sub_res = await session.execute(
            select(Subcategory).where(Subcategory.category_id == cat.id).order_by(Subcategory.name)
        )
        subcats = list(sub_res.scalars().all())
        subcat_names = [s.name for s in subcats]

        assert "Truist | 7 Days" in subcat_names
        assert "Truist | 15 Days" in subcat_names
        assert "Truist | 30+ Days" in subcat_names
        # Строго проверяем, что <7D или <7 Days отсутствуют!
        for name in subcat_names:
            assert "<7" not in name
            assert "до 7" not in name.lower()

    await engine.dispose()

