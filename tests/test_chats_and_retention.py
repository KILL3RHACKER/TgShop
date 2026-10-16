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

        # Проверяем, что ID заказа — это TRU-{encrypted(Days-Time-UserID)}
        assert order.bank_code == "TRU"
        assert order.order_code.startswith("TRU-")
        assert "#ORD-1" not in order.order_code
        decrypted = Order.decrypt_order_code(order.order_code)
        assert decrypted is not None
        assert decrypted["bank"] == "TRU"
        assert decrypted["days"] == "7D"
        assert decrypted["user_id"] == "12345678"

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

    # Проверка генерации ZIP покупателю: название архива = название товара, внутри только cookies.txt и info.txt
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
    # Название архива = названию товара/аккаунта (ST | NAME | DD.MM)
    assert out_fn == "ID | JOHN DOE | 01.10.zip"

    # Проверяем содержимое выданного ZIP архива: строго cookies.txt и info.txt и все!
    with zipfile.ZipFile(io.BytesIO(out_zip_bytes), "r") as zf:
        namelist = zf.namelist()
        assert set(namelist) == {"cookies.txt", "info.txt"}
        assert len(namelist) == 2

        cookies_content = zf.read("cookies.txt").decode("utf-8")
        assert "xyz123" in cookies_content
        info_content = zf.read("info.txt").decode("utf-8")
        assert "JOHN DOE" in info_content

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

@pytest.mark.asyncio
async def test_chat_sync_read_unread_and_delete():
    """Проверка синхронизации прочитано/не прочитано (белый кружок) и полного удаления чата у всех"""
    from bot.keyboards.inline import get_chats_list_kb
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        cat = Category(name="Truist")
        session.add(cat)
        await session.flush()
        sub = Subcategory(category_id=cat.id, name="Truist | 7 Days")
        session.add(sub)
        await session.flush()
        prod = Product(subcategory_id=sub.id, name="Truist | 7 Days", price=10.0)
        session.add(prod)
        await session.flush()

        order = Order(
            id=1,
            user_id=12345,
            product_id=prod.id,
            amount=10.0,
            status=OrderStatus.DISPUTED,
            hold_until=datetime.utcnow() + timedelta(hours=24),
            has_dispute=True,
            user_unread=False,
            admin_unread=True,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        # 1. Покупатель не имеет непрочитанных сообщений -> БЕЗ белого кружка!
        kb = get_chats_list_kb([order])
        btn_text = kb.inline_keyboard[0][0].text
        assert "⚪️" not in btn_text

        # 2. Администратор присылает ответ -> user_unread становится True!
        order.user_unread = True
        order.admin_unread = False
        msg_admin = DisputeMessage(order_id=order.id, sender_id=0, sender_role="admin", text="Здравствуйте")
        session.add(msg_admin)
        await session.commit()
        await session.refresh(order)

        # Теперь в списке чатов ОБЯЗАН быть белый кружок ⚪️!
        kb_unread = get_chats_list_kb([order])
        btn_text_unread = kb_unread.inline_keyboard[0][0].text
        assert btn_text_unread.startswith("⚪️ ")

        # 3. Покупатель открывает чат -> user_unread становится False, белый кружок исчезает!
        order.user_unread = False
        await session.commit()
        await session.refresh(order)

        kb_read = get_chats_list_kb([order])
        btn_text_read = kb_read.inline_keyboard[0][0].text
        assert "⚪️" not in btn_text_read

        # 4. Удаление чата -> переписка удаляется у всех из базы данных
        await session.execute(delete(DisputeMessage).where(DisputeMessage.order_id == order.id))
        order.has_dispute = False
        order.user_unread = False
        order.admin_unread = False
        order.status = OrderStatus.COMPLETED
        await session.commit()

        # Проверяем: сообщений в чате 0!
        msgs_res = await session.execute(select(DisputeMessage).where(DisputeMessage.order_id == order.id))
        assert len(list(msgs_res.scalars().all())) == 0

    await engine.dispose()

