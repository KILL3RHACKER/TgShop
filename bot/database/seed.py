from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.database.models import Category, Subcategory, Product, Account

async def seed_data(session: AsyncSession) -> None:
    result = await session.execute(select(Category))
    if result.scalars().first():
        return

    cat1 = Category(name="VPN & Proxy")
    cat2 = Category(name="Подписки & Сервисы")
    session.add_all([cat1, cat2])
    await session.flush()

    subcat1 = Subcategory(category_id=cat1.id, name="VLESS Reality")
    subcat2 = Subcategory(category_id=cat1.id, name="Wireguard")
    subcat3 = Subcategory(category_id=cat2.id, name="Telegram")
    session.add_all([subcat1, subcat2, subcat3])
    await session.flush()

    prod1 = Product(
        subcategory_id=subcat1.id,
        name="VLESS Reality (USA) 1 мес",
        description="Высокоскоростной конфиг VLESS Reality с локацией США на 1 месяц.",
        price=3.50,
    )
    prod2 = Product(
        subcategory_id=subcat2.id,
        name="Wireguard (Германия) 1 мес",
        description="Конфигурация Wireguard с европейской локацией.",
        price=2.99,
    )
    prod3 = Product(
        subcategory_id=subcat3.id,
        name="Telegram Premium 1 мес",
        description="Активация подписки Telegram Premium на 1 месяц.",
        price=4.99,
    )
    session.add_all([prod1, prod2, prod3])
    await session.flush()

    sample_accounts = [
        Account(product_id=prod1.id, data="IVAN IVANOV, 15.04", name="IVAN IVANOV", date_info="15.04"),
        Account(product_id=prod1.id, data="JOHN DOE, 12.03", name="JOHN DOE", date_info="12.03"),
        Account(product_id=prod1.id, data="ALICE SMITH, 22.11", name="ALICE SMITH", date_info="22.11"),
        Account(product_id=prod2.id, data="MICHAEL BROWN, 04.05", name="MICHAEL BROWN", date_info="04.05"),
        Account(product_id=prod2.id, data="DAVID WILSON, 12.08", name="DAVID WILSON", date_info="12.08"),
        Account(product_id=prod3.id, data="EMMA JOHNSON, 30.09", name="EMMA JOHNSON", date_info="30.09"),
        Account(product_id=prod3.id, data="OLIVIA DAVIS, 19.07", name="OLIVIA DAVIS", date_info="19.07"),
    ]
    session.add_all(sample_accounts)
    await session.commit()
