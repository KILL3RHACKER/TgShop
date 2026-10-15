import asyncio
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func
from bot.database.models import Base, Category, Product, Account, User

async def run_bot_purchase_test():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        # 1. Setup category & product
        cat = Category(name="VPN Services")
        session.add(cat)
        await session.flush()

        prod = Product(
            category_id=cat.id,
            name="VLESS Test",
            description="Test VPN",
            price=10.0,
            content="default-key"
        )
        session.add(prod)
        await session.flush()

        # Add 1 account only
        acc1 = Account(
            product_id=prod.id,
            name="JOHN DOE",
            date_info="15/08",
            data="JOHN DOE, 15/08"
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

        # 3. Simulate first purchase
        stmt = (
            select(Account)
            .where(Account.product_id == prod.id, Account.is_sold == False)
            .order_by(Account.id.asc())
            .limit(1)
        )
        acc_to_buy = await session.scalar(stmt)
        assert acc_to_buy is not None
        assert acc_to_buy.id == acc1.id

        user.balance = float(user.balance) - float(prod.price)
        acc_to_buy.is_sold = True
        acc_to_buy.sold_to_user_id = user.tg_id
        acc_to_buy.sold_at = datetime.utcnow()
        await session.commit()

        assert float(user.balance) == 15.0
        assert acc_to_buy.is_sold is True
        assert acc_to_buy.sold_to_user_id == 123456

        # 4. Try to buy again (now out of stock)
        acc_second_try = await session.scalar(stmt)
        assert acc_second_try is None, "Should be out of stock"

        # Check balance was not deducted further
        assert float(user.balance) == 15.0

    print("[OK] Bot purchase simulation test passed!")

if __name__ == "__main__":
    asyncio.run(run_bot_purchase_test())
