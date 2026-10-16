import unittest
import asyncio
from datetime import date, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from bot.database.models import Base, Category, Subcategory, Product, Account
from web.services import (
    extract_state_name_date,
    extract_state_from_address_line,
    parse_account_line,
    match_product_by_age,
    sync_all_accounts_age_tiers,
    DEFAULT_AGE_TIERS,
)

class TestAgeTiers(unittest.TestCase):
    def test_extract_state_name_date(self):
        # 1. Pipe separated ST | NAME SURNAME | DD.MM
        p1 = extract_state_name_date("CA | JOHN DOE | 15.04")
        self.assertIsNotNone(p1)
        st, name, dt, reg_date = p1
        self.assertEqual(st, "CA")
        self.assertEqual(name, "JOHN DOE")
        self.assertEqual(dt, "15.04")
        self.assertIsNotNone(reg_date)

        # 2. Comma separated with extra password tail
        p2 = extract_state_name_date("NY, MICHAEL SMITH, 01/10:password123:extra")
        self.assertIsNotNone(p2)
        st, name, dt, _ = p2
        self.assertEqual(st, "NY")
        self.assertEqual(name, "MICHAEL SMITH")
        self.assertEqual(dt, "01.10")

        # 3. Explicit year in date
        p3 = extract_state_name_date("TX | BOB J. JOHNSON | 25.12.2025")
        self.assertIsNotNone(p3)
        st, name, dt, reg_date = p3
        self.assertEqual(st, "TX")
        self.assertEqual(name, "BOB J. JOHNSON")
        self.assertEqual(dt, "25.12")
        self.assertEqual(reg_date, date(2025, 12, 25))

        # 4. Text month
        p4 = extract_state_name_date("FL | DAVID BROWN | 15 апр")
        self.assertIsNotNone(p4)
        st, name, dt, _ = p4
        self.assertEqual(st, "FL")
        self.assertEqual(name, "DAVID BROWN")
        self.assertEqual(dt, "15.04")

        # 5. Space separated state and name
        p5 = extract_state_name_date("IL ROBERT BROWN 20.08")
        self.assertIsNotNone(p5)
        st, name, dt, _ = p5
        self.assertEqual(st, "IL")
        self.assertEqual(name, "ROBERT BROWN")
        self.assertEqual(dt, "20.08")

        # 6. Fallback if no state given
        p6 = extract_state_name_date("IVAN IVANOV, 15.04")
        self.assertIsNotNone(p6)
        st, name, dt, _ = p6
        self.assertEqual(st, "US")
        self.assertEqual(name, "IVAN IVANOV")
        self.assertEqual(dt, "15.04")

        # 7. parse_account_line produces format: ST | NAME SURNAME | DD.MM
        acc = parse_account_line("ca | john doe | 15.04")
        self.assertIsNotNone(acc)
        self.assertEqual(acc["state"], "CA")
        self.assertEqual(acc["name"], "JOHN DOE")
        self.assertEqual(acc["date_info"], "15.04")
        self.assertEqual(acc["data"], "CA | JOHN DOE | 15.04")

    def test_info_txt_and_address_extraction(self):
        # User's exact line from Plan:
        address_line = "Address: 109 1ST ST\tRUPERT\tID\t83350"
        st = extract_state_from_address_line(address_line)
        self.assertEqual(st, "ID")

        # Test ZIP with info.txt inside folder
        import io
        import zipfile
        from web.services import parse_accounts_from_zip

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("JOHN_DOE_15.04/info.txt", "Address: 109 1ST ST\tRUPERT\tID\t83350\n")
        zip_buf.seek(0)

        accs, count = parse_accounts_from_zip(zip_buf.getvalue())
        self.assertEqual(len(accs), 1)
        self.assertEqual(accs[0]["state"], "ID")
        self.assertEqual(accs[0]["name"], "JOHN DOE")
        self.assertEqual(accs[0]["date_info"], "15.04")
        self.assertEqual(accs[0]["data"], "ID | JOHN DOE | 15.04")

    def test_match_product_by_age(self):
        prod_fresh = Product(id=1, name="До 7", min_days=0, max_days=6, price=3.0)
        prod_7 = Product(id=2, name="7+", min_days=7, max_days=14, price=5.0)
        prod_15 = Product(id=3, name="15+", min_days=15, max_days=29, price=10.0)
        prod_30 = Product(id=4, name="30+", min_days=30, max_days=None, price=15.0)
        prods = [prod_fresh, prod_7, prod_15, prod_30]

        # Day 3 -> fresh
        self.assertEqual(match_product_by_age(prods, 3).id, 1)

        # Day 10 -> 7+
        self.assertEqual(match_product_by_age(prods, 10).id, 2)

        # Day 14 -> 7+
        self.assertEqual(match_product_by_age(prods, 14).id, 2)

        # Day 15 (next day!) -> 15+!
        self.assertEqual(match_product_by_age(prods, 15).id, 3)

        # Day 29 -> 15+
        self.assertEqual(match_product_by_age(prods, 29).id, 3)

        # Day 30 -> 30+!
        self.assertEqual(match_product_by_age(prods, 30).id, 4)

        # Day 100 -> 30+
        self.assertEqual(match_product_by_age(prods, 100).id, 4)

    def test_sync_accounts_graduation(self):
        async def run_async():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
            sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            async with sessionmaker() as session:
                cat = Category(name="Test USA")
                session.add(cat)
                await session.flush()

                sub = Subcategory(category_id=cat.id, name="General")
                session.add(sub)
                await session.flush()

                p7 = Product(subcategory_id=sub.id, name="7+", min_days=7, max_days=14, price=5.0)
                p15 = Product(subcategory_id=sub.id, name="15+", min_days=15, max_days=29, price=10.0)
                p30 = Product(subcategory_id=sub.id, name="30+", min_days=30, max_days=None, price=15.0)
                session.add_all([p7, p15, p30])
                await session.flush()

                today = date.today()
                # Account registered 14 days ago -> currently in p7
                acc1 = Account(
                    product_id=p7.id,
                    state="CA",
                    name="TEST USER",
                    date_info="01.01",
                    registration_date=today - timedelta(days=14),
                    data="CA | TEST USER | 01.01",
                    is_sold=False,
                )
                session.add(acc1)
                await session.commit()
                await session.refresh(acc1)

                # Before sync: account is in p7 (age 14)
                self.assertEqual(acc1.product_id, p7.id)

                # Now simulate next day: account becomes 15 days old!
                acc1.registration_date = today - timedelta(days=15)
                await session.commit()

                # Run sync_all_accounts_age_tiers
                migrated = await sync_all_accounts_age_tiers(session)
                self.assertEqual(migrated, 1)

                # Check that account automatically migrated to p15!
                await session.refresh(acc1)
                self.assertEqual(acc1.product_id, p15.id)

                # Now simulate day 30
                acc1.registration_date = today - timedelta(days=30)
                await session.commit()

                migrated2 = await sync_all_accounts_age_tiers(session)
                self.assertEqual(migrated2, 1)
                await session.refresh(acc1)
                self.assertEqual(acc1.product_id, p30.id)

            await engine.dispose()

        asyncio.run(run_async())

if __name__ == "__main__":
    unittest.main()
