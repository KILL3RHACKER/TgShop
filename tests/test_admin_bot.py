import io
import zipfile
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Document, Message, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.admin_bot.router import (
    ACCESS_DENIED_TEXT,
    AdminAuthMiddleware,
    AdminBotStates,
    cb_admin_disputes,
    cb_admin_dispute_ban,
    cb_admin_dispute_do_replace,
    cb_admin_dispute_replace_select,
    cb_admin_dispute_resolve,
    cb_admin_main,
    cb_admin_mods_list,
    cb_admin_stats,
    cb_admin_stock,
    cb_admin_sweep,
    cb_admin_ticket_action,
    cb_admin_tickets,
    cb_admin_upload_cat_chosen,
    cb_admin_upload_save_cat,
    cb_admin_upload_select_cat,
    cb_admin_view_dispute,
    cb_admin_view_ticket,
    cb_admin_wallet,
    cmd_admin_broadcast,
    cmd_admin_start,
    cmd_ban_user,
    cmd_del_moderator,
    cmd_list_moderators,
    cmd_set_moderator,
    cmd_unban_user,
    process_broadcast_text,
    process_document_upload,
    process_text_upload,
    process_withdraw_address,
    process_withdraw_amount,
)
from bot.config import config
from bot.database.models import (
    Account,
    Base,
    Category,
    DisputeMessage,
    Order,
    OrderStatus,
    Product,
    Subcategory,
    TicketStatus,
    TopUpTicket,
    User,
)


async def create_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, maker


def make_tg_user(user_id: int, username: str = "testuser") -> TgUser:
    return TgUser(id=user_id, is_bot=False, first_name="Test", username=username)


def make_message(user_id: int, text: str = "", username: str = "testuser", document: Document | None = None) -> Message:
    msg = AsyncMock(spec=Message)
    msg.from_user = make_tg_user(user_id, username)
    msg.text = text
    msg.caption = None
    msg.photo = None
    msg.document = document
    msg.answer = AsyncMock()
    return msg


def make_callback(user_id: int, data: str, username: str = "testuser") -> CallbackQuery:
    cb = AsyncMock(spec=CallbackQuery)
    cb.from_user = make_tg_user(user_id, username)
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = AsyncMock(spec=Message)
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_admin_auth_middleware():
    engine, maker = await create_db()
    async with maker() as async_db:
        middleware = AdminAuthMiddleware()
        handler = AsyncMock(return_value="OK")

        # 1. Superadmin is allowed
        event_admin = make_message(config.admin_id)
        res_admin = await middleware(handler, event_admin, {"event_from_user": event_admin.from_user, "session": async_db})
        assert res_admin == "OK"
        handler.assert_called_once()
        handler.reset_mock()

        # 2. Unknown user is rejected
        unknown_user = make_message(99999999)
        res_unknown = await middleware(handler, unknown_user, {"event_from_user": unknown_user.from_user, "session": async_db})
        assert res_unknown is None
        handler.assert_not_called()
        unknown_user.answer.assert_called_once_with(ACCESS_DENIED_TEXT)

        # 3. Regular buyer is rejected
        buyer = User(tg_id=111222, username="buyer1", role="buyer", tron_address="TAddr1", tron_private_key="key1")
        async_db.add(buyer)
        await async_db.commit()

        buyer_msg = make_message(111222)
        res_buyer = await middleware(handler, buyer_msg, {"event_from_user": buyer_msg.from_user, "session": async_db})
        assert res_buyer is None
        handler.assert_not_called()
        buyer_msg.answer.assert_called_once_with(ACCESS_DENIED_TEXT)

        # 4. Moderator is allowed
        mod = User(tg_id=333444, username="mod1", role="moderator", tron_address="TAddr2", tron_private_key="key2")
        async_db.add(mod)
        await async_db.commit()

        mod_msg = make_message(333444)
        res_mod = await middleware(handler, mod_msg, {"event_from_user": mod_msg.from_user, "session": async_db})
        assert res_mod == "OK"
        handler.assert_called_once()
        handler.reset_mock()

        # 5. Banned moderator is rejected
        mod.is_banned = True
        await async_db.commit()

        banned_cb = make_callback(333444, "ab_main")
        res_banned = await middleware(handler, banned_cb, {"event_from_user": banned_cb.from_user, "session": async_db})
        assert res_banned is None
        handler.assert_not_called()
        banned_cb.answer.assert_called_once_with(ACCESS_DENIED_TEXT, show_alert=True)
    await engine.dispose()


@pytest.mark.asyncio
async def test_main_menu_and_stats():
    engine, maker = await create_db()
    async with maker() as async_db:
        # Setup dummy data
        user = User(tg_id=config.admin_id, role="admin", tron_address="TAdmin", tron_private_key="keyAdmin", balance=150.0)
        async_db.add(user)
        tck = TopUpTicket(user_id=config.admin_id, amount=50.0, status=TicketStatus.PENDING, expires_at=datetime.utcnow() + timedelta(hours=1))
        async_db.add(tck)
        order = Order(user_id=config.admin_id, amount=20.0, status=OrderStatus.DISPUTED, hold_until=datetime.utcnow())
        async_db.add(order)
        await async_db.commit()

        storage = MemoryStorage()
        state = FSMContext(storage=storage, key=MagicMock())

        # /start
        msg = make_message(config.admin_id, "/start")
        await cmd_admin_start(msg, async_db, state)
        msg.answer.assert_called_once()
        call_text = msg.answer.call_args[0][0]
        assert "Админ-терминал магазина TG Shop Bot" in call_text

        # ab_stats
        cb = make_callback(config.admin_id, "ab_stats")
        await cb_admin_stats(cb, async_db)
        cb.message.edit_text.assert_called_once()
        stats_text = cb.message.edit_text.call_args[0][0]
        assert "Сводная статистика магазина" in stats_text
        assert "$150.00 USDT" in stats_text
        assert "Тикеты пополнения (ожидают): <b>1</b>" in stats_text
        assert "Открытых диспутов: <b>1</b>" in stats_text
    await engine.dispose()


@pytest.mark.asyncio
async def test_tickets_management():
    engine, maker = await create_db()
    async with maker() as async_db:
        user = User(tg_id=555666, username="topup_user", role="buyer", tron_address="TTicketUser", tron_private_key="k1", balance=10.0)
        async_db.add(user)
        await async_db.commit()

        ticket = TopUpTicket(user_id=user.tg_id, amount=25.0, status=TicketStatus.PENDING, expires_at=datetime.utcnow() + timedelta(hours=1))
        async_db.add(ticket)
        await async_db.commit()

        # List tickets
        cb_list = make_callback(config.admin_id, "ab_tickets")
        await cb_admin_tickets(cb_list, async_db)
        assert "Ожидающие тикеты пополнения" in cb_list.message.edit_text.call_args[0][0]

        # View ticket
        cb_view = make_callback(config.admin_id, f"ab_tck:{ticket.id}")
        await cb_admin_view_ticket(cb_view, async_db)
        assert f"Тикет на пополнение #{ticket.id}" in cb_view.message.edit_text.call_args[0][0]

        # Approve ticket with mock shop_bot
        mock_bot = AsyncMock(spec=Bot)
        mock_shop_bot = AsyncMock(spec=Bot)

        cb_approve = make_callback(config.admin_id, f"ab_tck_act:{ticket.id}:approve")
        await cb_admin_ticket_action(cb_approve, async_db, mock_bot, shop_bot=mock_shop_bot)

        # Check that ticket is approved and buyer balance updated
        await async_db.refresh(ticket)
        await async_db.refresh(user)
        assert ticket.status == TicketStatus.APPROVED
        assert float(user.balance) == 35.0
        mock_shop_bot.send_message.assert_called_once()
        assert "успешно подтвержден" in cb_approve.message.edit_text.call_args[0][0]
    await engine.dispose()


@pytest.mark.asyncio
async def test_disputes_and_replacements():
    engine, maker = await create_db()
    async with maker() as async_db:
        buyer = User(tg_id=777111, username="buyer_disp", role="buyer", tron_address="TDispUser", tron_private_key="k2", balance=0.0)
        async_db.add(buyer)
        cat = Category(name="Truist")
        async_db.add(cat)
        await async_db.flush()
        subcat = Subcategory(category_id=cat.id, name="Truist | 7 Days")
        async_db.add(subcat)
        await async_db.flush()
        prod = Product(subcategory_id=subcat.id, name="Truist | 7 Days", price=15.0)
        async_db.add(prod)
        await async_db.flush()

        acc1 = Account(product_id=prod.id, state="FL", name="JOHN DOE", date_info="10.10", data="FL | JOHN DOE | 10.10", is_sold=True, sold_to_user_id=buyer.tg_id)
        acc2 = Account(product_id=prod.id, state="FL", name="JANE SMITH", date_info="12.10", data="FL | JANE SMITH | 12.10", is_sold=False)
        async_db.add_all([acc1, acc2])
        await async_db.commit()

        order = Order(
            user_id=buyer.tg_id,
            account_id=acc1.id,
            product_id=prod.id,
            amount=15.0,
            status=OrderStatus.DISPUTED,
            hold_until=datetime.utcnow() + timedelta(hours=24),
            disputed_at=datetime.utcnow(),
        )
        async_db.add(order)
        await async_db.commit()

        # 1. List disputes
        cb_list = make_callback(config.admin_id, "ab_disputes")
        await cb_admin_disputes(cb_list, async_db)
        assert "Активные обращения покупателей" in cb_list.message.edit_text.call_args[0][0]

        # 2. View dispute
        cb_view = make_callback(config.admin_id, f"ab_disp_view:{order.id}")
        await cb_admin_view_dispute(cb_view, async_db)
        assert f"Диспут по заказу {order.order_code}" in cb_view.message.edit_text.call_args[0][0]

        # 3. Select replacement
        cb_repl_sel = make_callback(config.admin_id, f"ab_disp_replace:{order.id}")
        await cb_admin_dispute_replace_select(cb_repl_sel, async_db)
        assert "Выбор аккаунта для замены" in cb_repl_sel.message.edit_text.call_args[0][0]

        # 4. Confirm replacement
        mock_bot = AsyncMock(spec=Bot)
        mock_shop_bot = AsyncMock(spec=Bot)
        cb_repl_do = make_callback(config.admin_id, f"ab_disp_do_replace:{order.id}:{acc2.id}")
        await cb_admin_dispute_do_replace(cb_repl_do, async_db, mock_bot, shop_bot=mock_shop_bot)

        await async_db.refresh(order)
        await async_db.refresh(acc2)
        assert acc2.is_sold is True
        assert order.account_id == acc2.id
        assert order.status == OrderStatus.COMPLETED
        mock_shop_bot.send_document.assert_called_once()
        assert "успешно выдана" in cb_repl_do.message.edit_text.call_args[0][0]
    await engine.dispose()


@pytest.mark.asyncio
async def test_dispute_refund_and_ban():
    engine, maker = await create_db()
    async with maker() as async_db:
        buyer = User(tg_id=888222, username="froder", role="buyer", tron_address="TFrod", tron_private_key="kfrod", balance=0.0)
        async_db.add(buyer)
        order1 = Order(user_id=buyer.tg_id, amount=20.0, status=OrderStatus.DISPUTED, hold_until=datetime.utcnow())
        order2 = Order(user_id=buyer.tg_id, amount=30.0, status=OrderStatus.DISPUTED, hold_until=datetime.utcnow())
        async_db.add_all([order1, order2])
        await async_db.commit()

        mock_bot = AsyncMock(spec=Bot)
        mock_shop_bot = AsyncMock(spec=Bot)

        # Test refund
        cb_ref = make_callback(config.admin_id, f"ab_disp_resolve:{order1.id}:refund")
        await cb_admin_dispute_resolve(cb_ref, async_db, mock_bot, shop_bot=mock_shop_bot)
        await async_db.refresh(order1)
        await async_db.refresh(buyer)
        assert order1.status == OrderStatus.REFUNDED
        assert float(buyer.balance) == 20.0
        mock_shop_bot.send_message.assert_called_once()

        # Test ban froder
        mock_shop_bot.reset_mock()
        cb_ban = make_callback(config.admin_id, f"ab_disp_ban:{order2.id}")
        await cb_admin_dispute_ban(cb_ban, async_db, mock_bot, shop_bot=mock_shop_bot)
        await async_db.refresh(order2)
        await async_db.refresh(buyer)
        assert order2.status == OrderStatus.COMPLETED
        assert buyer.is_banned is True
        assert "Мошенничество" in buyer.banned_reason
        assert "заблокирован за фрод" in cb_ban.message.edit_text.call_args[0][0]
    await engine.dispose()


@pytest.mark.asyncio
async def test_stock_upload_and_reporting():
    engine, maker = await create_db()
    async with maker() as async_db:
        cat = Category(name="Truist")
        async_db.add(cat)
        await async_db.commit()

        storage = MemoryStorage()
        state = FSMContext(storage=storage, key=MagicMock())

        # Upload lines directly via process_text_upload
        await state.set_state(AdminBotStates.waiting_upload_file)
        await state.update_data(upload_category_id=cat.id)

        raw_text = (
            "CA | ALICE WALKER | 05.10\n"
            "TX | BOB JOHNSON | 25.09\n"
            "NY | CHARLIE DAVIS | 15.08\n"
            "CA | ALICE WALKER | 05.10\n"  # Duplicate
        )
        msg = make_message(config.admin_id, text=raw_text)
        await process_text_upload(msg, state, async_db)

        msg.answer.assert_called_once()
        report = msg.answer.call_args[0][0]
        assert "Загружено 3 аккаунтов в категорию Truist:" in report
        assert "• 7 Days:" in report
        assert "• 15 Days:" in report
        assert "• 30+ Days:" in report
        assert "Дубликатов по SSN пропущено: 1" in report

        # Check warehouse stock view
        cb_stock = make_callback(config.admin_id, "ab_stock")
        await cb_admin_stock(cb_stock, async_db)
        stock_text = cb_stock.message.edit_text.call_args[0][0]
        assert "Складские остатки товаров" in stock_text
        assert "Truist" in stock_text
        assert "Всего товара в наличии: 3 шт." in stock_text
    await engine.dispose()


@pytest.mark.asyncio
async def test_wallet_and_withdraw():
    engine, maker = await create_db()
    async with maker() as async_db:
        admin = await async_db.scalar(select(User).where(User.tg_id == config.admin_id))
        if not admin:
            admin = User(tg_id=config.admin_id, role="admin", tron_address="TAdmin123", tron_private_key="priv", balance=100.0)
            async_db.add(admin)
        else:
            admin.balance = 100.0
        await async_db.commit()

        storage = MemoryStorage()
        state = FSMContext(storage=storage, key=MagicMock())

        # View wallet
        cb_w = make_callback(config.admin_id, "ab_wallet")
        await cb_admin_wallet(cb_w, async_db)
        assert "$100.00 USDT" in cb_w.message.edit_text.call_args[0][0]

        from bot.services.tron import generate_tron_wallet
        valid_addr, _ = generate_tron_wallet()

        # Withdraw address
        msg_addr = make_message(config.admin_id, valid_addr)
        await process_withdraw_address(msg_addr, state, async_db)
        msg_addr.answer.assert_called_once()
        assert "Адрес принят" in msg_addr.answer.call_args[0][0]

        # Withdraw amount
        msg_amt = make_message(config.admin_id, "40.0")
        await process_withdraw_amount(msg_amt, state, async_db)
        msg_amt.answer.assert_called_once()
        assert "Средства успешно выведены" in msg_amt.answer.call_args[0][0]

        await async_db.refresh(admin)
        assert float(admin.balance) == 60.0
    await engine.dispose()


@pytest.mark.asyncio
async def test_management_commands_and_broadcast():
    engine, maker = await create_db()
    async with maker() as async_db:
        target = User(tg_id=987654, username="user_to_mod", role="buyer", tron_address="TTarget", tron_private_key="ptarget")
        async_db.add(target)
        await async_db.commit()

        # /set_mod
        msg_set = make_message(config.admin_id, "/set_mod 987654")
        await cmd_set_moderator(msg_set, async_db)
        await async_db.refresh(target)
        assert target.role == "moderator"
        assert "присвоена роль <b>Модератор</b>" in msg_set.answer.call_args[0][0]

        # /mods
        msg_mods = make_message(config.admin_id, "/mods")
        await cmd_list_moderators(msg_mods, async_db)
        assert "user_to_mod" in msg_mods.answer.call_args[0][0]

        # /del_mod
        msg_del = make_message(config.admin_id, "/del_mod 987654")
        await cmd_del_moderator(msg_del, async_db)
        await async_db.refresh(target)
        assert target.role == "buyer"

        # /ban
        msg_ban = make_message(config.admin_id, "/ban 987654 Spamming")
        await cmd_ban_user(msg_ban, async_db)
        await async_db.refresh(target)
        assert target.is_banned is True
        assert target.banned_reason == "Spamming"

        # /unban
        msg_unban = make_message(config.admin_id, "/unban 987654")
        await cmd_unban_user(msg_unban, async_db)
        await async_db.refresh(target)
        assert target.is_banned is False

        # /broadcast
        mock_bot = AsyncMock(spec=Bot)
        mock_shop_bot = AsyncMock(spec=Bot)
        msg_bc = make_message(config.admin_id, "/broadcast Привет всем клиентам!")
        await cmd_admin_broadcast(msg_bc, async_db, mock_bot, shop_bot=mock_shop_bot)
        mock_shop_bot.send_message.assert_called_once_with(chat_id=987654, text="Привет всем клиентам!")
    await engine.dispose()


@pytest.mark.asyncio
async def test_zip_document_upload():
    engine, maker = await create_db()
    async with maker() as async_db:
        cat1 = Category(name="Chase")
        cat2 = Category(name="Bank of America")
        async_db.add_all([cat1, cat2])
        await async_db.commit()

        # Build in-memory zip
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("accounts.txt", "CA | JOHN SMITH | 10.10\nNY | EMMA WATSON | 20.09\n")
        zip_bytes = zip_buf.getvalue()

        mock_bot = AsyncMock(spec=Bot)
        async def fake_download(doc, destination):
            destination.write(zip_bytes)
        mock_bot.download.side_effect = fake_download

        storage = MemoryStorage()
        state = FSMContext(storage=storage, key=MagicMock())

        doc = Document(file_id="zip123", file_unique_id="uniq123", file_name="accounts.zip", mime_type="application/zip")
        msg = make_message(config.admin_id, document=doc)
        status_msg = AsyncMock()
        status_msg.edit_text = AsyncMock()
        msg.answer.return_value = status_msg

        # Direct drop without pre-selected category -> prompts category choice
        await process_document_upload(msg, state, async_db, mock_bot)
        status_msg.edit_text.assert_called_once()
        call_text = status_msg.edit_text.call_args[0][0]
        assert "Выберите категорию для распределения" in call_text

        # Now simulate admin clicking category Chase
        cb_cat = make_callback(config.admin_id, f"ab_upload_save_cat:{cat1.id}")
        await cb_admin_upload_save_cat(cb_cat, state, async_db)
        cb_cat.message.edit_text.assert_called_once()
        report = cb_cat.message.edit_text.call_args[0][0]
        assert "Загружено 2 аккаунтов в категорию Chase:" in report
        assert "• 7 Days:" in report
    await engine.dispose()
