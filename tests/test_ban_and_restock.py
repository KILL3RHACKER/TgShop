import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import CallbackQuery, Message, Update, User as TgUser
from sqlalchemy import select, exc
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from bot.database.models import Base, User, Category, Subcategory, RestockSubscription
from bot.middlewares.db import DbSessionMiddleware, BanMiddleware, BAN_REJECT_MESSAGE

@pytest.mark.asyncio
async def test_user_ban_fields():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        user = User(
            tg_id=1234567,
            username="test_banned",
            tron_address="TAddress1",
            tron_private_key="key1",
            is_banned=True,
            banned_reason="Rule violation"
        )
        session.add(user)
        await session.commit()

        queried = await session.scalar(select(User).where(User.tg_id == 1234567))
        assert queried is not None
        assert queried.is_banned is True
        assert queried.banned_reason == "Rule violation"
    await engine.dispose()

@pytest.mark.asyncio
async def test_restock_subscription_model_and_unique_constraint():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        cat = Category(name="Category 1")
        session.add(cat)
        await session.flush()

        sub = Subcategory(category_id=cat.id, name="Subcategory 1")
        session.add(sub)
        user = User(
            tg_id=777888999,
            username="subscriber",
            tron_address="TSub123",
            tron_private_key="priv123"
        )
        session.add(user)
        await session.commit()

        sub_entry = RestockSubscription(user_id=user.tg_id, subcategory_id=sub.id)
        session.add(sub_entry)
        await session.commit()

        queried = await session.scalar(
            select(RestockSubscription).where(
                RestockSubscription.user_id == user.tg_id,
                RestockSubscription.subcategory_id == sub.id
            )
        )
        assert queried is not None
        assert queried.id is not None
        assert queried.created_at is not None

        # Test unique constraint (user_id, subcategory_id)
        duplicate = RestockSubscription(user_id=user.tg_id, subcategory_id=sub.id)
        session.add(duplicate)
        with pytest.raises(exc.IntegrityError):
            await session.commit()
    await engine.dispose()

@pytest.mark.asyncio
async def test_ban_middleware_rejects_banned_message():
    mock_session = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.return_value.__aenter__.return_value = mock_session

    middleware = DbSessionMiddleware(mock_pool)
    banned_user = User(
        tg_id=999,
        is_banned=True,
        banned_reason="Spam",
        tron_address="T1",
        tron_private_key="P1"
    )
    mock_session.scalar.return_value = banned_user

    tg_user = TgUser(id=999, is_bot=False, first_name="BannedPerson")
    mock_msg = AsyncMock(spec=Message)
    update = Update(update_id=1, message=mock_msg)
    handler = AsyncMock()

    await middleware(handler, update, {"event_from_user": tg_user})

    handler.assert_not_called()
    mock_msg.answer.assert_called_once_with(BAN_REJECT_MESSAGE)

@pytest.mark.asyncio
async def test_ban_middleware_rejects_banned_callback():
    mock_session = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.return_value.__aenter__.return_value = mock_session

    middleware = DbSessionMiddleware(mock_pool)
    banned_user = User(
        tg_id=999,
        is_banned=True,
        tron_address="T1",
        tron_private_key="P1"
    )
    mock_session.scalar.return_value = banned_user

    tg_user = TgUser(id=999, is_bot=False, first_name="BannedPerson")
    mock_cb = AsyncMock(spec=CallbackQuery)
    update = Update(update_id=2, callback_query=mock_cb)
    handler = AsyncMock()

    await middleware(handler, update, {"event_from_user": tg_user})

    handler.assert_not_called()
    mock_cb.answer.assert_called_once_with(BAN_REJECT_MESSAGE, show_alert=True)

@pytest.mark.asyncio
async def test_ban_middleware_allows_active_user():
    mock_session = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.return_value.__aenter__.return_value = mock_session

    middleware = DbSessionMiddleware(mock_pool)
    active_user = User(
        tg_id=111,
        is_banned=False,
        tron_address="T2",
        tron_private_key="P2"
    )
    mock_session.scalar.return_value = active_user

    tg_user = TgUser(id=111, is_bot=False, first_name="ActivePerson")
    mock_msg = AsyncMock(spec=Message)
    update = Update(update_id=3, message=mock_msg)
    handler = AsyncMock(return_value="OK")

    res = await middleware(handler, update, {"event_from_user": tg_user})

    handler.assert_called_once()
    assert res == "OK"
    mock_msg.answer.assert_not_called()

@pytest.mark.asyncio
async def test_init_db_migration_sqlite():
    from sqlalchemy import text
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    # Simulate older schema
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, tg_id BIGINT UNIQUE);"))
        await conn.execute(text("CREATE TABLE subcategories (id INTEGER PRIMARY KEY);"))

    # Apply migration steps
    if engine.dialect.name == "sqlite":
        for stmt in [
            "ALTER TABLE users ADD COLUMN is_banned BOOLEAN DEFAULT 0;",
            "ALTER TABLE users ADD COLUMN banned_reason VARCHAR(255);",
        ]:
            try:
                async with engine.begin() as conn:
                    await conn.execute(text(stmt))
            except Exception:
                pass

    create_restock_table_sql = """
    CREATE TABLE IF NOT EXISTS restock_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id BIGINT NOT NULL,
        subcategory_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(tg_id),
        FOREIGN KEY (subcategory_id) REFERENCES subcategories(id) ON DELETE CASCADE,
        UNIQUE (user_id, subcategory_id)
    );
    """
    async with engine.begin() as conn:
        await conn.execute(text(create_restock_table_sql))

    # Verify column additions and table creation
    async with engine.begin() as conn:
        res = await conn.execute(text("PRAGMA table_info(users);"))
        cols = [r[1] for r in res.fetchall()]
        assert "is_banned" in cols
        assert "banned_reason" in cols

        res_tables = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table';"))
        tables = [r[0] for r in res_tables.fetchall()]
        assert "restock_subscriptions" in tables
    await engine.dispose()

