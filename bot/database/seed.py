from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.database.models import Category, Product

async def seed_data(session: AsyncSession) -> None:
    result = await session.execute(select(Category))
    if result.scalars().first():
        return

    cat1 = Category(name="VPN & Proxy")
    cat2 = Category(name="Подписки & Сервисы")
    session.add_all([cat1, cat2])
    await session.flush()

    prod1 = Product(
        category_id=cat1.id,
        name="VLESS VPN 1 мес.",
        description="Высокоскоростной конфиг VLESS/Reality с доступом на 30 дней.",
        price=3.50,
        content="vless://e12f458a-364e-4e67-9c6e-example@node1.vpn.net:443?security=reality"
    )
    prod2 = Product(
        category_id=cat1.id,
        name="VLESS VPN 3 мес.",
        description="Конфиг VLESS/Reality на 90 дней со скидкой.",
        price=9.00,
        content="vless://f55b99a1-872e-4b90-bc4a-example@node2.vpn.net:443?security=reality"
    )
    prod3 = Product(
        category_id=cat2.id,
        name="Telegram Premium 1 мес.",
        description="Подарочная ссылка на активацию Telegram Premium 1 месяц.",
        price=4.99,
        content="https://t.me/giftcode/EXAMPLE_TG_PREMIUM_KEY_30D"
    )

    session.add_all([prod1, prod2, prod3])
    await session.commit()
