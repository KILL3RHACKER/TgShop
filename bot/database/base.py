from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from bot.config import config
from bot.database.models import Base

engine = create_async_engine(config.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
