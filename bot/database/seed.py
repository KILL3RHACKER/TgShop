from datetime import date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.database.models import Category, Subcategory, Product, Account

async def seed_data(session: AsyncSession) -> None:
    result = await session.execute(select(Category))
    if result.scalars().first():
        return

    cat1 = Category(name="Аккаунты USA")
    session.add(cat1)
    await session.flush()

    subcat1 = Subcategory(category_id=cat1.id, name="Общая база США")
    session.add(subcat1)
    await session.flush()

    today = date.today()

    prod_7 = Product(
        subcategory_id=subcat1.id,
        name="Отлёжка 7+ Дней (7-14 дн.)",
        description="Отлёжка от 7 до 14 полных дней. Прогретые аккаунты.",
        price=5.00,
        min_days=7,
        max_days=14,
    )
    prod_15 = Product(
        subcategory_id=subcat1.id,
        name="Отлёжка 15+ Дней (15-29 дн.)",
        description="Отлёжка от 15 до 29 полных дней. Высокий траст.",
        price=10.00,
        min_days=15,
        max_days=29,
    )
    prod_30 = Product(
        subcategory_id=subcat1.id,
        name="Отлёжка 30+ Дней (30+ дн.)",
        description="Отлёжка от 30 дней и старше. Максимальная отлёжка.",
        price=15.00,
        min_days=30,
        max_days=None,
    )
    session.add_all([prod_7, prod_15, prod_30])
    await session.flush()

    date_10d = today - timedelta(days=10)
    date_14d = today - timedelta(days=14)
    date_15d = today - timedelta(days=15)
    date_20d = today - timedelta(days=20)
    date_35d = today - timedelta(days=35)
    date_60d = today - timedelta(days=60)

    sample_accounts = [
        Account(
            product_id=prod_7.id,
            state="CA",
            name="JOHN DOE",
            date_info=date_10d.strftime("%d.%m"),
            registration_date=date_10d,
            data=f"CA | JOHN DOE | {date_10d.strftime('%d.%m')}",
            raw_data=f"CA | JOHN DOE | {date_10d.strftime('%d.%m')}",
        ),
        Account(
            product_id=prod_7.id,
            state="NY",
            name="MICHAEL SMITH",
            date_info=date_14d.strftime("%d.%m"),
            registration_date=date_14d,
            data=f"NY | MICHAEL SMITH | {date_14d.strftime('%d.%m')}",
            raw_data=f"NY | MICHAEL SMITH | {date_14d.strftime('%d.%m')}",
        ),
        Account(
            product_id=prod_15.id,
            state="TX",
            name="DAVID JOHNSON",
            date_info=date_15d.strftime("%d.%m"),
            registration_date=date_15d,
            data=f"TX | DAVID JOHNSON | {date_15d.strftime('%d.%m')}",
            raw_data=f"TX | DAVID JOHNSON | {date_15d.strftime('%d.%m')}",
        ),
        Account(
            product_id=prod_15.id,
            state="FL",
            name="EMILY DAVIS",
            date_info=date_20d.strftime("%d.%m"),
            registration_date=date_20d,
            data=f"FL | EMILY DAVIS | {date_20d.strftime('%d.%m')}",
            raw_data=f"FL | EMILY DAVIS | {date_20d.strftime('%d.%m')}",
        ),
        Account(
            product_id=prod_30.id,
            state="IL",
            name="ROBERT BROWN",
            date_info=date_35d.strftime("%d.%m"),
            registration_date=date_35d,
            data=f"IL | ROBERT BROWN | {date_35d.strftime('%d.%m')}",
            raw_data=f"IL | ROBERT BROWN | {date_35d.strftime('%d.%m')}",
        ),
        Account(
            product_id=prod_30.id,
            state="OH",
            name="SARAH WILSON",
            date_info=date_60d.strftime("%d.%m"),
            registration_date=date_60d,
            data=f"OH | SARAH WILSON | {date_60d.strftime('%d.%m')}",
            raw_data=f"OH | SARAH WILSON | {date_60d.strftime('%d.%m')}",
        ),
    ]
    session.add_all(sample_accounts)
    await session.commit()
