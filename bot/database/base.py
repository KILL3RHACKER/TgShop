from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from bot.config import config
from bot.database.models import Base

engine = create_async_engine(config.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Migration: DDL columns and constraints
    ddl_statements = [
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS min_days INTEGER;",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS max_days INTEGER;",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS state VARCHAR(16) DEFAULT 'US';",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS registration_date DATE;",
        "ALTER TABLE subcategories ALTER COLUMN description DROP NOT NULL;",
        "ALTER TABLE subcategories ALTER COLUMN description SET DEFAULT '';",
        "ALTER TABLE subcategories ALTER COLUMN price DROP NOT NULL;",
        "ALTER TABLE subcategories ALTER COLUMN price SET DEFAULT 0.0;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS rules_accepted BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS rules_accepted_at TIMESTAMP;",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS last_reply_by VARCHAR(16);",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS last_reply_at TIMESTAMP;",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS dispute_deadline TIMESTAMP;",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS has_dispute BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS account_data TEXT;",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS user_unread BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS admin_unread BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS cookie_data TEXT;",
        "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS ssn VARCHAR(32);",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(16) DEFAULT 'buyer';",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS escalated_to_admin BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMP;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_reason VARCHAR(255);",
    ]
    for stmt in ddl_statements:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            pass

    # SQLite compatibility for ADD COLUMN
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

    # Ensure restock_subscriptions table exists
    create_restock_table_sql = """
    CREATE TABLE IF NOT EXISTS restock_subscriptions (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(tg_id),
        subcategory_id INTEGER NOT NULL REFERENCES subcategories(id) ON DELETE CASCADE,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
        CONSTRAINT uq_restock_subscriptions_user_subcategory UNIQUE (user_id, subcategory_id)
    );
    """ if engine.dialect.name != "sqlite" else """
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
    try:
        async with engine.begin() as conn:
            await conn.execute(text(create_restock_table_sql))
    except Exception:
        pass

    # Migration: add 'QUESTION' and 'question' to orderstatus enum if using postgres enum
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'orderstatus') THEN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_enum 
                            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid 
                            WHERE pg_type.typname = 'orderstatus' AND pg_enum.enumlabel = 'QUESTION'
                        ) THEN
                            ALTER TYPE orderstatus ADD VALUE 'QUESTION';
                        END IF;
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_enum 
                            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid 
                            WHERE pg_type.typname = 'orderstatus' AND pg_enum.enumlabel = 'question'
                        ) THEN
                            ALTER TYPE orderstatus ADD VALUE 'question';
                        END IF;
                    END IF;
                END $$;
            """))
    except Exception:
        pass

    # Migration: create products and migrate accounts / orders
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='subcategories') 
                       AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='products') THEN
                        IF NOT EXISTS (SELECT 1 FROM products) AND EXISTS (SELECT 1 FROM subcategories) THEN
                            INSERT INTO products (subcategory_id, name, description, price)
                            SELECT id, name, '', 100.0
                            FROM subcategories;
                        END IF;
                    END IF;

                    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='accounts' AND column_name='subcategory_id') THEN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='accounts' AND column_name='product_id') THEN
                            ALTER TABLE accounts ADD COLUMN product_id INTEGER REFERENCES products(id) ON DELETE CASCADE;
                            UPDATE accounts a
                            SET product_id = p.id
                            FROM products p
                            WHERE a.subcategory_id = p.subcategory_id;
                        END IF;
                        ALTER TABLE accounts ALTER COLUMN subcategory_id DROP NOT NULL;
                    END IF;

                    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='orders' AND column_name='subcategory_id') THEN
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='orders' AND column_name='product_id') THEN
                            ALTER TABLE orders ADD COLUMN product_id INTEGER REFERENCES products(id) ON DELETE SET NULL;
                            UPDATE orders o
                            SET product_id = p.id
                            FROM products p
                            WHERE o.subcategory_id = p.subcategory_id;
                        END IF;
                        ALTER TABLE orders ALTER COLUMN subcategory_id DROP NOT NULL;
                    END IF;
                END $$;
            """))
    except Exception:
        pass

    # Normalize any existing accounts to DD.MM format
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                UPDATE accounts 
                SET date_info = REPLACE(date_info, '/', '.'),
                    data = REPLACE(data, '/', '.')
                WHERE date_info LIKE '%/%' OR data LIKE '%/%';
            """))
    except Exception:
        pass
