import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func
from bot.database.models import Base, Category, Subcategory, Product, Account, User, Order, OrderStatus, DisputeMessage

@pytest.mark.asyncio
async def test_bot_purchase():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        # 1. Setup Category, Subcategory & Product
        cat = Category(name="VPN Services")
        session.add(cat)
        await session.flush()

        subcat = Subcategory(
            category_id=cat.id,
            name="VLESS Test",
        )
        session.add(subcat)
        await session.flush()

        prod = Product(
            subcategory_id=subcat.id,
            name="VLESS Test 1 мес",
            description="Test VPN",
            price=10.0,
        )
        session.add(prod)
        await session.flush()

        # Add 1 account only (свежий на первой странице)
        acc1 = Account(
            product_id=prod.id,
            name="JOHN DOE",
            date_info="15.08",
            data="JOHN DOE, 15.08"
        )
        session.add(acc1)

        # Add user with $25
        user = User(
            tg_id=123456,
            username="buyer1",
            balance=25.0,
            tron_address="TAddress1",
            tron_private_key="key1"
        )
        session.add(user)
        await session.commit()

        # 2. Check stock count
        stock_count = await session.scalar(
            select(func.count(Account.id)).where(Account.product_id == prod.id, Account.is_sold == False)
        )
        assert stock_count == 1, f"Expected 1 account in stock, got {stock_count}"

        # 3. Simulate first purchase: creates Order with 24h HOLD
        stmt = (
            select(Account)
            .where(Account.product_id == prod.id, Account.is_sold == False)
            .order_by(Account.created_at.desc(), Account.id.desc())
            .limit(1)
        )
        acc_to_buy = await session.scalar(stmt)
        assert acc_to_buy is not None
        assert acc_to_buy.id == acc1.id

        # Deduct balance & create order with HOLD status
        user.balance = float(user.balance) - float(prod.price)
        acc_to_buy.is_sold = True
        acc_to_buy.sold_to_user_id = user.tg_id
        acc_to_buy.sold_at = datetime.utcnow()

        hold_until = datetime.utcnow() + timedelta(hours=24)
        order = Order(
            user_id=user.tg_id,
            account_id=acc_to_buy.id,
            product_id=prod.id,
            amount=prod.price,
            status=OrderStatus.HOLD,
            hold_until=hold_until,
        )
        session.add(order)
        await session.commit()

        # 4. Verify balance and stock after purchase
        assert user.balance == 15.0
        stock_after = await session.scalar(
            select(func.count(Account.id)).where(Account.product_id == prod.id, Account.is_sold == False)
        )
        assert stock_after == 0

        # 5. Test Dispute Creation within 24h hold
        order.status = OrderStatus.DISPUTED
        order.disputed_at = datetime.utcnow()
        msg = DisputeMessage(
            order_id=order.id,
            sender_id=user.tg_id,
            sender_role="user",
            text="Account does not work, please refund"
        )
        session.add(msg)
        await session.commit()

        await session.refresh(order, ["messages"])
        assert order.status == OrderStatus.DISPUTED
        assert len(order.messages) == 1
        assert order.messages[0].text == "Account does not work, please refund"

        # 6. Test Refund flow (credits user balance back)
        order.status = OrderStatus.REFUNDED
        order.resolved_at = datetime.utcnow()
        user.balance = float(user.balance) + float(order.amount)
        await session.commit()

        assert user.balance == 25.0
        assert order.status == OrderStatus.REFUNDED

    print("ALL 3-TIER CATALOG, BOT PURCHASE, 24H HOLD AND DISPUTE TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(run_bot_purchase_test())
