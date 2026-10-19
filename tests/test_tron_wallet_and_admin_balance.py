import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from bot.config import config
from bot.database.models import Base, User, Order, OrderStatus, Product, Account
from bot.services.ticket import credit_admin_balance
from bot.services.tron import (
    generate_tron_wallet,
    is_valid_tron_address,
    tron_to_hex,
    hex_to_tron,
    sign_tron_tx,
    get_tron_base_url,
    get_tron_headers,
)

def test_tron_crypto_utilities():
    """Проверка генерации кошельков, валидации адресов и подписи транзакций."""
    addr, privkey = generate_tron_wallet()
    assert is_valid_tron_address(addr) is True
    assert is_valid_tron_address("InvalidAddr") is False
    assert is_valid_tron_address("") is False

    # Roundtrip conversion
    h = tron_to_hex(addr)
    assert h.startswith("41")
    assert hex_to_tron(h) == addr

    # Signature test (65 bytes hex = 130 chars)
    dummy_txid = "a" * 64
    sig = sign_tron_tx(dummy_txid, privkey)
    assert len(sig) == 130
    assert bytes.fromhex(sig) is not None

@pytest.mark.asyncio
async def test_credit_admin_balance_flow():
    """Проверка зачисления средств от завершенных заказов на внутренний баланс админа."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        # Сначала админа нет в базе: функция должна безопасно создать запись админа
        await credit_admin_balance(session, 15.50)
        await session.commit()

        admin = await session.scalar(select(User).where(User.tg_id == config.admin_id))
        assert admin is not None
        assert admin.role == "admin"
        assert float(admin.balance) == 15.50

        # Повторное начисление (следующий завершенный заказ)
        await credit_admin_balance(session, 20.00)
        await session.commit()
        await session.refresh(admin)
        assert float(admin.balance) == 35.50

        # Отрицательные или нулевые суммы игнорируются
        await credit_admin_balance(session, 0.0)
        await credit_admin_balance(session, -10.0)
        await session.commit()
        await session.refresh(admin)
        assert float(admin.balance) == 35.50

@pytest.mark.asyncio
async def test_order_completion_to_admin_wallet():
    """Тест сквозного сценария: покупка -> холд -> завершение -> деньги на балансе админа."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        # Создаем покупателя и товар
        buyer = User(
            tg_id=777888,
            username="buyer777",
            balance=50.0,
            tron_address="TAddrBuyer",
            tron_private_key="keybuyer",
            rules_accepted=True
        )
        admin = User(
            tg_id=config.admin_id,
            username="shop_admin",
            balance=0.0,
            tron_address="TAddrAdmin",
            tron_private_key="keyadmin",
            role="admin",
            rules_accepted=True
        )
        session.add_all([buyer, admin])
        await session.flush()

        # Покупка: $15 списано у покупателя, заказ в HOLD
        buyer.balance = float(buyer.balance) - 15.0
        order = Order(
            user_id=buyer.tg_id,
            amount=15.0,
            status=OrderStatus.HOLD,
            hold_until=datetime.utcnow() + timedelta(hours=24)
        )
        session.add(order)
        await session.commit()

        assert float(buyer.balance) == 35.0
        assert float(admin.balance) == 0.0

        # Разморозка холда (24ч прошло): заказ COMPLETED -> средства падают админу
        order.status = OrderStatus.COMPLETED
        order.resolved_at = datetime.utcnow()
        await credit_admin_balance(session, float(order.amount))
        await session.commit()

        await session.refresh(admin)
        assert float(admin.balance) == 15.0

        # Вывод средств админом
        withdraw_amount = 10.0
        assert withdraw_amount <= float(admin.balance)
        admin.balance = float(admin.balance) - withdraw_amount
        await session.commit()

        await session.refresh(admin)
        assert float(admin.balance) == 5.0
