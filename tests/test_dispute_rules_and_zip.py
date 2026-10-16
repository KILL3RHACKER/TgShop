import io
import zipfile
import pytest
from datetime import datetime, timedelta, date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from bot.database.models import (
    Base, User, Category, Subcategory, Product, Account, Order, OrderStatus, OrderReview
)
from bot.services.account_zip import generate_account_zip

@pytest.mark.asyncio
async def test_account_zip_generation():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        cat = Category(name="Test Bank")
        session.add(cat)
        await session.flush()

        sub = Subcategory(category_id=cat.id, name="Checking")
        session.add(sub)
        await session.flush()

        prod = Product(subcategory_id=sub.id, name="Truist 7+ Days", price=25.0)
        session.add(prod)
        await session.flush()

        user = User(
            tg_id=12345678,
            username="buyer1",
            balance=100.0,
            tron_address="TTestAddr",
            tron_private_key="testkey",
            rules_accepted=True
        )
        session.add(user)
        await session.flush()

        acc = Account(
            product_id=prod.id,
            state="FL",
            name="JOHN DOE",
            date_info="12.04",
            data="FL | JOHN DOE | 12.04",
            raw_data="Name: JOHN DOE\nAddress: 123 Main St FL 33101\nCookies: [{\"name\": \"session\", \"value\": \"xyz\"}]",
            is_sold=True,
            sold_to_user_id=user.tg_id
        )
        session.add(acc)
        await session.flush()

        order = Order(
            user_id=user.tg_id,
            account_id=acc.id,
            product_id=prod.id,
            amount=25.0,
            status=OrderStatus.HOLD,
            hold_until=datetime.utcnow() + timedelta(hours=24)
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        # Generate ZIP
        zip_bytes, zip_filename = generate_account_zip(order, acc, prod)
        assert zip_filename.endswith(".zip")
        assert len(zip_bytes) > 0

        # Inspect ZIP contents
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            namelist = zf.namelist()
            assert "account.txt" in namelist
            assert "full_data.txt" in namelist
            assert "cookies.json" in namelist
            assert "WARRANTY_MEMO.txt" in namelist

            account_content = zf.read("account.txt").decode("utf-8")
            assert "FL | JOHN DOE | 12.04" in account_content

            memo_content = zf.read("WARRANTY_MEMO.txt").decode("utf-8")
            assert "24 ЧАСА" in memo_content
            assert "SLA" in memo_content

@pytest.mark.asyncio
async def test_order_tabs_and_reviews():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        user = User(
            tg_id=999,
            username="tester",
            balance=50.0,
            tron_address="TAddr999",
            tron_private_key="key999",
            rules_accepted=True
        )
        session.add(user)
        await session.flush()

        cat = Category(name="TestCat")
        session.add(cat)
        await session.flush()
        sub = Subcategory(category_id=cat.id, name="TestSub")
        session.add(sub)
        await session.flush()
        prod = Product(subcategory_id=sub.id, name="TestProd", price=10.0)
        session.add(prod)
        await session.flush()

        # Order 1: HOLD (Attention tab)
        o1 = Order(user_id=user.tg_id, product_id=prod.id, amount=10.0, status=OrderStatus.HOLD, hold_until=datetime.utcnow() + timedelta(hours=24))
        # Order 2: DISPUTED (Attention tab)
        o2 = Order(user_id=user.tg_id, product_id=prod.id, amount=10.0, status=OrderStatus.DISPUTED, hold_until=datetime.utcnow() + timedelta(hours=24), has_dispute=True)
        # Order 3: COMPLETED (Completed tab)
        o3 = Order(user_id=user.tg_id, product_id=prod.id, amount=10.0, status=OrderStatus.COMPLETED, hold_until=datetime.utcnow() - timedelta(hours=1))

        session.add_all([o1, o2, o3])
        await session.commit()

        # Query Attention Orders
        att_res = await session.execute(
            select(Order).where(Order.user_id == user.tg_id, Order.status.in_([OrderStatus.HOLD, OrderStatus.DISPUTED, OrderStatus.QUESTION]))
        )
        attention_orders = list(att_res.scalars().all())
        assert len(attention_orders) == 2

        # Query Completed Orders
        comp_res = await session.execute(
            select(Order).where(Order.user_id == user.tg_id, Order.status.in_([OrderStatus.COMPLETED, OrderStatus.REFUNDED]))
        )
        completed_orders = list(comp_res.scalars().all())
        assert len(completed_orders) == 1

        # Leave Review for o3
        rev = OrderReview(
            order_id=o3.id,
            user_id=user.tg_id,
            product_id=prod.id,
            rating=5,
            comment="Отличный валидный аккаунт!",
            is_after_dispute=False
        )
        session.add(rev)
        await session.commit()

        await session.refresh(o3)
        assert o3.review is not None
        assert o3.review.rating == 5
        assert o3.review.is_after_dispute is False

@pytest.mark.asyncio
async def test_kraken_dispute_sla_auto_actions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        user = User(
            tg_id=777,
            username="buyer777",
            balance=0.0,
            tron_address="TAddr777",
            tron_private_key="key777",
            rules_accepted=True
        )
        session.add(user)
        await session.flush()

        cat = Category(name="Cat")
        session.add(cat)
        await session.flush()
        sub = Subcategory(category_id=cat.id, name="Sub")
        session.add(sub)
        await session.flush()
        prod = Product(subcategory_id=sub.id, name="Prod", price=50.0)
        session.add(prod)
        await session.flush()

        # Case 1: Store went silent for 24 hours -> auto-refund to buyer
        past_deadline = datetime.utcnow() - timedelta(minutes=5)
        o_store_silent = Order(
            user_id=user.tg_id,
            product_id=prod.id,
            amount=50.0,
            status=OrderStatus.DISPUTED,
            hold_until=datetime.utcnow() + timedelta(hours=10),
            last_reply_by="client",
            dispute_deadline=past_deadline,
            has_dispute=True
        )
        session.add(o_store_silent)

        # Case 2: Client went silent for 24 hours -> auto-close in store's favor
        o_client_silent = Order(
            user_id=user.tg_id,
            product_id=prod.id,
            amount=30.0,
            status=OrderStatus.DISPUTED,
            hold_until=datetime.utcnow() + timedelta(hours=10),
            last_reply_by="admin",
            dispute_deadline=past_deadline,
            has_dispute=True
        )
        session.add(o_client_silent)
        await session.commit()

        # Simulate SLA worker action
        now = datetime.utcnow()
        stmt = select(Order).where(Order.status == OrderStatus.DISPUTED, Order.dispute_deadline <= now)
        res = await session.execute(stmt)
        expired = list(res.scalars().all())
        assert len(expired) == 2

        for disp in expired:
            u = await session.scalar(select(User).where(User.tg_id == disp.user_id))
            if disp.last_reply_by == "client":
                disp.status = OrderStatus.REFUNDED
                u.balance = float(u.balance) + float(disp.amount)
            elif disp.last_reply_by == "admin":
                disp.status = OrderStatus.COMPLETED

        await session.commit()

        # Verify outcomes
        await session.refresh(user)
        await session.refresh(o_store_silent)
        await session.refresh(o_client_silent)

        assert o_store_silent.status == OrderStatus.REFUNDED
        assert float(user.balance) == 50.0  # Refund credited!
        assert o_client_silent.status == OrderStatus.COMPLETED  # Closed in store favor!
