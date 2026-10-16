from urllib.parse import quote
from datetime import date, timedelta
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc, or_, and_, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Account, Product, Subcategory, Category
from web.auth import AdminUser
from web.dependencies import get_db
from web.services import (
    parse_accounts_from_archive,
    parse_archive_by_files,
    add_accounts_bulk,
    add_accounts_auto_age_tiers,
    add_accounts_to_category_auto_age,
    sync_all_accounts_age_tiers,
    delete_accounts_by_list,
    get_or_create_age_tier_products,
)

router = APIRouter(prefix="/accounts", tags=["accounts"])

def get_templates():
    from web.main import templates
    return templates

@router.get("", response_class=HTMLResponse)
async def list_accounts(
    request: Request,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    category_id: int | None = None,
    subcategory_id: int | None = None,
    product_id: int | None = None,
    age_filter: str = "all",
    status_filter: str = "all",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    msg: str | None = None,
    msg_type: str = "success",
):
    page = max(1, page)
    offset = (page - 1) * page_size
    today = date.today()

    # Fetch all categories, subcategories, products for filters and modals
    cats_res = await session.execute(
        select(Category)
        .options(selectinload(Category.subcategories).selectinload(Subcategory.products))
        .order_by(Category.name)
    )
    categories = list(cats_res.scalars().all())

    prods_res = await session.execute(
        select(Product)
        .options(selectinload(Product.subcategory).selectinload(Subcategory.category))
        .order_by(Product.name)
    )
    products = list(prods_res.scalars().all())

    # Build filter conditions
    conditions = []
    if product_id is not None and product_id > 0:
        conditions.append(Account.product_id == product_id)
    elif subcategory_id is not None and subcategory_id > 0:
        matching_pids = [p.id for p in products if p.subcategory_id == subcategory_id]
        if matching_pids:
            conditions.append(Account.product_id.in_(matching_pids))
        else:
            conditions.append(Account.product_id == -1)
    elif category_id is not None and category_id > 0:
        matching_pids = [
            p.id for p in products
            if p.subcategory and p.subcategory.category_id == category_id
        ]
        if matching_pids:
            conditions.append(Account.product_id.in_(matching_pids))
        else:
            conditions.append(Account.product_id == -1)

    if status_filter == "available":
        conditions.append(Account.is_sold == False)
    elif status_filter == "sold":
        conditions.append(Account.is_sold == True)

    # Age filter
    if age_filter == "fresh":
        # 0 to 6 days
        min_date = today - timedelta(days=6)
        conditions.append(Account.registration_date >= min_date)
    elif age_filter == "7":
        # 7 to 14 days
        date_14 = today - timedelta(days=14)
        date_7 = today - timedelta(days=7)
        conditions.append(Account.registration_date.between(date_14, date_7))
    elif age_filter == "15":
        # 15 to 29 days
        date_29 = today - timedelta(days=29)
        date_15 = today - timedelta(days=15)
        conditions.append(Account.registration_date.between(date_29, date_15))
    elif age_filter == "30":
        # 30+ days
        date_30 = today - timedelta(days=30)
        conditions.append(Account.registration_date <= date_30)

    search = q.strip()
    if search:
        search_pattern = f"%{search}%"
        conditions.append(
            or_(
                Account.state.ilike(search_pattern),
                Account.name.ilike(search_pattern),
                Account.date_info.ilike(search_pattern),
                Account.data.ilike(search_pattern),
            )
        )

    # Count total matching query
    count_stmt = select(func.count(Account.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total_matching = await session.scalar(count_stmt) or 0

    # Overall stock stats
    total_all = await session.scalar(select(func.count(Account.id))) or 0
    total_available = await session.scalar(
        select(func.count(Account.id)).where(Account.is_sold == False)
    ) or 0
    total_sold = await session.scalar(
        select(func.count(Account.id)).where(Account.is_sold == True)
    ) or 0

    # Fetch paginated accounts: Сортировка аккаунтов - свежие на первой странице (created_at DESC, id DESC)
    stmt = (
        select(Account)
        .options(selectinload(Account.product).selectinload(Product.subcategory).selectinload(Subcategory.category))
        .order_by(Account.created_at.desc(), Account.id.desc())
        .offset(offset)
        .limit(page_size)
    )
    if conditions:
        stmt = stmt.where(*conditions)
    accounts_res = await session.execute(stmt)
    accounts = list(accounts_res.scalars().all())

    total_pages = max(1, (total_matching + page_size - 1) // page_size)

    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="accounts.html",
        context={
            "admin": admin,
            "active_tab": "accounts",
            "accounts": accounts,
            "categories": categories,
            "products": products,
            "selected_category_id": category_id,
            "selected_subcategory_id": subcategory_id,
            "selected_product_id": product_id,
            "age_filter": age_filter,
            "status_filter": status_filter,
            "q": search,
            "page": page,
            "page_size": page_size,
            "total_matching": total_matching,
            "total_pages": total_pages,
            "total_all": total_all,
            "total_available": total_available,
            "total_sold": total_sold,
            "msg": msg,
            "msg_type": msg_type,
        }
    )

@router.post("/upload-zip")
@router.post("/upload-archive")
async def upload_archive_accounts(
    admin: AdminUser,
    request: Request,
    category_id: int | None = Form(None),
    subcategory_id: int | None = Form(None),
    product_id: int | None = Form(None),
    upload_mode: str = Form("auto_age"),  # "auto_age" | "specific_product" | "by_filenames"
    archive_files: list[UploadFile] = File(default=[]),
    session: AsyncSession = Depends(get_db),
):
    uploaded_files = []
    # 1. Check files from archive_files parameter
    if isinstance(archive_files, list):
        for f in archive_files:
            if hasattr(f, "filename") and f.filename and f.filename.strip():
                uploaded_files.append(f)
    elif hasattr(archive_files, "filename") and archive_files.filename and archive_files.filename.strip():
        uploaded_files.append(archive_files)

    # 2. Fallback check from request.form() for any file field variations
    if not uploaded_files:
        try:
            form = await request.form()
            seen_names = set()
            for key in ("archive_files", "archive_files[]", "archive_file", "zip_file", "file", "files"):
                for item in form.getlist(key):
                    if hasattr(item, "filename") and item.filename and item.filename.strip():
                        if item.filename not in seen_names:
                            seen_names.add(item.filename)
                            uploaded_files.append(item)
                single_val = form.get(key)
                if hasattr(single_val, "filename") and single_val.filename and single_val.filename.strip():
                    if single_val.filename not in seen_names:
                        seen_names.add(single_val.filename)
                        uploaded_files.append(single_val)
        except Exception:
            pass

    if not uploaded_files:
        msg = quote("Файлы архивов не загружены. Пожалуйста, выберите .zip или .rar файл на диске перед отправкой.")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    valid_files = [
        f for f in uploaded_files
        if f.filename.lower().endswith((".zip", ".rar"))
    ]

    if not valid_files:
        msg = quote("Поддерживаются только архивы .zip и .rar")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    try:
        all_parsed_accounts = []
        seen = set()
        total_files_count = 0

        for f in valid_files:
            content = await f.read()
            parsed_accounts, files_count = parse_accounts_from_archive(f.filename, content)
            total_files_count += files_count
            for acc in parsed_accounts:
                key = acc["data"].upper()
                if key not in seen:
                    seen.add(key)
                    all_parsed_accounts.append(acc)

        if not all_parsed_accounts:
            msg = quote(f"В загруженных архивах ({len(valid_files)} шт.) не найдено подходящих аккаунтов")
            return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

        # 1. Specific product upload
        if upload_mode == "specific_product" and product_id and product_id > 0:
            added = await add_accounts_bulk(session, product_id, all_parsed_accounts)
            msg = quote(f"Успешно импортировано {added} аккаунтов в товар из {len(valid_files)} архива(ов)")
            return RedirectResponse(
                url=f"/accounts?product_id={product_id}&msg={msg}&msg_type=success",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # 2. Upload into Category (Оставлена только Категория)
        if category_id and category_id > 0:
            stats = await add_accounts_to_category_auto_age(session, category_id, all_parsed_accounts)
            total_added = sum(stats.values())
            details_str = ", ".join(f"{k}: {v} шт." for k, v in stats.items())
            msg = quote(f"🎉 Загружено {total_added} аккаунтов из {len(valid_files)} архива(ов)! Авто-сортировка: {details_str}")
            return RedirectResponse(
                url=f"/accounts?category_id={category_id}&msg={msg}&msg_type=success",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # 3. Fallback: upload into Subcategory if passed
        if subcategory_id and subcategory_id > 0:
            stats = await add_accounts_auto_age_tiers(session, subcategory_id, all_parsed_accounts)
            total_added = sum(stats.values())
            details_str = ", ".join(f"{k}: {v} шт." for k, v in stats.items())
            msg = quote(f"🎉 Загружено {total_added} аккаунтов! Авто-сортировка: {details_str}")
            return RedirectResponse(
                url=f"/accounts?subcategory_id={subcategory_id}&msg={msg}&msg_type=success",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # 4. Fallback if no category: pick first category or create one
        cat = await session.scalar(select(Category).limit(1))
        if not cat:
            cat = Category(name="Аккаунты USA")
            session.add(cat)
            await session.commit()
            await session.refresh(cat)

        stats = await add_accounts_to_category_auto_age(session, cat.id, all_parsed_accounts)
        total_added = sum(stats.values())
        details_str = ", ".join(f"{k}: {v} шт." for k, v in stats.items())
        msg = quote(f"🎉 Загружено {total_added} аккаунтов в категорию «{cat.name}»! {details_str}")
        return RedirectResponse(
            url=f"/accounts?category_id={cat.id}&msg={msg}&msg_type=success",
            status_code=status.HTTP_303_SEE_OTHER,
        )

        # 3. Filename matching mode
        file_map, files_count = parse_archive_by_files(filename, content)
        if not file_map:
            msg = quote(f"В архиве {filename} не найдено .txt файлов с аккаунтами")
            return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

        products_res = await session.execute(select(Product))
        all_products = list(products_res.scalars().all())

        total_added = 0
        matched_details = []

        for base_name, acc_list in file_map.items():
            best_prod = None
            base_clean = base_name.strip().lower()
            for p in all_products:
                p_clean = p.name.strip().lower()
                if p_clean == base_clean or p_clean in base_clean or base_clean in p_clean:
                    best_prod = p
                    break

            if best_prod:
                added = await add_accounts_bulk(session, best_prod.id, acc_list)
                total_added += added
                matched_details.append(f"{best_prod.name}: +{added}")
            elif all_products:
                if len(all_products) == 1:
                    added = await add_accounts_bulk(session, all_products[0].id, acc_list)
                    total_added += added
                    matched_details.append(f"{all_products[0].name}: +{added}")

        if total_added > 0:
            details_str = ", ".join(matched_details)
            msg = quote(f"Пачка успешно загружена! Добавлено {total_added} акк. из {files_count} файлов ({details_str})")
            return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)
        else:
            msg = quote("Файлы из архива не удалось сопоставить с существующими товарами. Выберите категорию или конкретный товар.")
            return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    except Exception as e:
        msg = quote(f"Ошибка при обработке архива: {str(e)}")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/sync-age")
async def sync_accounts_age(
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
):
    """
    Принудительный пересчет и перевод аккаунтов в старшие тиры по дням отлёжки (15+, 30+).
    """
    migrated = await sync_all_accounts_age_tiers(session)
    msg = quote(f"Синхронизация завершена. Автоматически переведено {migrated} аккаунтов, достигших следующего периода отлёжки!")
    return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/delete-single")
async def delete_single_account(
    admin: AdminUser,
    account_id: int = Form(...),
    session: AsyncSession = Depends(get_db),
    product_id: int | None = None,
):
    account = await session.get(Account, account_id)
    if not account:
        msg = quote("Аккаунт не найден")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    if account.is_sold:
        msg = quote("Нельзя удалить проданный аккаунт")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

    prod_id = account.product_id
    await session.delete(account)
    await session.commit()

    msg = quote(f"Аккаунт #{account_id} успешно удален")
    redir_url = f"/accounts?product_id={prod_id}&msg={msg}&msg_type=success" if prod_id else f"/accounts?msg={msg}&msg_type=success"
    return RedirectResponse(url=redir_url, status_code=status.HTTP_303_SEE_OTHER)

@router.post("/delete-bulk")
async def delete_bulk_accounts(
    admin: AdminUser,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    form = await request.form()
    account_ids_raw = form.getlist("account_ids")

    if not account_ids_raw:
        msg = quote("Не выбрано ни одного аккаунта для удаления")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

    try:
        account_ids = [int(i) for i in account_ids_raw]
    except ValueError:
        msg = quote("Некорректный формат идентификаторов")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    stmt = delete(Account).where(
        Account.id.in_(account_ids),
        Account.is_sold == False
    )
    result = await session.execute(stmt)
    await session.commit()

    deleted_count = result.rowcount or 0
    msg = quote(f"Успешно удалено {deleted_count} выбранных аккаунтов")
    return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/delete-list")
async def delete_accounts_list(
    admin: AdminUser,
    list_data: str = Form(...),
    product_id: int | None = Form(None),
    session: AsyncSession = Depends(get_db),
):
    if not list_data.strip():
        msg = quote("Список аккаунтов пуст")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

    deleted_count = await delete_accounts_by_list(session, list_data, product_id)

    if deleted_count > 0:
        msg = quote(f"Успешно найдено и удалено {deleted_count} аккаунтов по списку")
        msg_type = "success"
    else:
        msg = quote("Совпадений по нераспроданным аккаунтам не найдено")
        msg_type = "warning"

    redir_url = f"/accounts?product_id={product_id}&msg={msg}&msg_type={msg_type}" if product_id else f"/accounts?msg={msg}&msg_type={msg_type}"
    return RedirectResponse(url=redir_url, status_code=status.HTTP_303_SEE_OTHER)
