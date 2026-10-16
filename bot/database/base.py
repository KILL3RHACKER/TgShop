from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from bot.config import config
from bot.database.models import Base

engine = create_async_engine(config.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Migration: create products and migrate accounts / orders
        try:
            await conn.execute(text("""
                DO $$
                BEGIN
                    -- If products table is empty but subcategories exist, migrate subcategory data into products
                    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='subcategories') 
                       AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='products') THEN
                        IF NOT EXISTS (SELECT 1 FROM products) AND EXISTS (SELECT 1 FROM subcategories) THEN
                            INSERT INTO products (subcategory_id, name, description, price)
                            SELECT id, name, '', 100.0
                            FROM subcategories;
                        END IF;
                    END IF;

                    -- Migrate accounts.subcategory_id to accounts.product_id if needed
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

                    -- Migrate orders.subcategory_id to orders.product_id if needed
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
            await conn.execute(text("""
                UPDATE accounts 
                SET date_info = REPLACE(date_info, '/', '.'),
                    data = REPLACE(data, '/', '.')
                WHERE date_info LIKE '%/%' OR data LIKE '%/%';
            """))
        except Exception:
            pass
