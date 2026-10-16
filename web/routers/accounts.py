from urllib.parse import quote
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
    delete_accounts_by_list,
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
    product_id: int | None = None,
    subcategory_id: int | None = None,
    status_filter: str = "all",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    msg: str | None = None,
    msg_type: str = "success",
):
    selected_prod_id = product_id if product_id is not None else subcategory_id

    page = max(1, page)
    offset = (page - 1) * page_size

    # Fetch all products with subcategory and category for filter dropdown and modal selects
    products_res = await session.execute(
        select(Product)
        .options(selectinload(Product.subcategory).selectinload(Subcategory.category))
        .order_by(Product.name)
    )
    products = list(products_res.scalars().all())

    # Build filter conditions
    conditions = []
    if selected_prod_id is not None and selected_prod_id > 0:
        conditions.append(Account.product_id == selected_prod_id)

    if status_filter == "available":
        conditions.append(Account.is_sold == False)
    elif status_filter == "sold":
        conditions.append(Account.is_sold == True)

    search = q.strip()
    if search:
        search_pattern = f"%{search}%"
        conditions.append(
            or_(
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
            "products": products,
            "selected_product_id": selected_prod_id,
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
    product_id: int | None = Form(None),
    subcategory_id: int | None = Form(None),
    session: AsyncSession = Depends(get_db),
):
    target_id = product_id if product_id is not None else subcategory_id
    form = await request.form()
    archive_file: UploadFile = form.get("archive_file") or form.get("zip_file")

    if not archive_file:
        msg = quote("Файл архива не загружен")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    filename = archive_file.filename or ""
    fn_lower = filename.lower()
    if not (fn_lower.endswith(".zip") or fn_lower.endswith(".rar")):
        msg = quote("Поддерживаются только архивы .zip и .rar")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    try:
        content = await archive_file.read()

        # Check if target_id is specified
        if target_id is not None and target_id > 0:
            parsed_accounts, files_count = parse_accounts_from_archive(filename, content)
            if not parsed_accounts:
                msg = quote(f"В архиве {filename} не найдено подходящих строк аккаунтов")
                return RedirectResponse(
                    url=f"/accounts?product_id={target_id}&msg={msg}&msg_type=warning",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

            added = await add_accounts_bulk(session, target_id, parsed_accounts)
            msg = quote(f"Успешно импортировано {added} аккаунтов из архива {filename} (свежие на 1-й странице витрины)")
            return RedirectResponse(
                url=f"/accounts?product_id={target_id}&msg={msg}&msg_type=success",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # Automatic mode: match files to products ("пачкой на сервер, а на витрине сами сортируются")
        file_map, files_count = parse_archive_by_files(filename, content)
        if not file_map:
            msg = quote(f"В архиве {filename} не найдено .txt файлов с аккаунтами")
            return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

        # Fetch all products to match
        products_res = await session.execute(select(Product))
        all_products = list(products_res.scalars().all())

        total_added = 0
        matched_details = []

        for base_name, acc_list in file_map.items():
            # Find best matching product
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
                # If only 1 product exists in shop, assign to it
                if len(all_products) == 1:
                    added = await add_accounts_bulk(session, all_products[0].id, acc_list)
                    total_added += added
                    matched_details.append(f"{all_products[0].name}: +{added}")

        if total_added > 0:
            details_str = ", ".join(matched_details)
            msg = quote(f"Пачка успешно загружена! Добавлено {total_added} акк. из {files_count} файлов ({details_str}). На витрине отсортированы!")
            return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)
        else:
            msg = quote("Файлы из архива не удалось сопоставить с существующими товарами. Выберите конкретный товар при загрузке.")
            return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    except Exception as e:
        msg = quote(f"Ошибка при обработке архива: {str(e)}")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/delete-single")
async def delete_single_account(
    admin: AdminUser,
    account_id: int = Form(...),
    session: AsyncSession = Depends(get_db),
    product_id: int | None = None,
):
    acc = await session.get(Account, account_id)
    if acc:
        await session.delete(acc)
        await session.commit()
        msg = quote(f"Аккаунт #{account_id} успешно удален")
    else:
        msg = quote("Аккаунт не найден")

    query = f"?product_id={product_id}&" if product_id else "?"
    return RedirectResponse(url=f"/accounts{query}msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/delete-selected")
async def delete_selected_accounts(
    admin: AdminUser,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    form = await request.form()
    selected_ids = form.getlist("selected_ids")
    product_id = form.get("product_id")

    if not selected_ids:
        msg = quote("Не выбрано ни одного аккаунта для удаления")
        query = f"?product_id={product_id}&" if product_id else "?"
        return RedirectResponse(url=f"/accounts{query}msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

    ids = [int(i) for i in selected_ids if i.isdigit()]
    if ids:
        await session.execute(delete(Account).where(Account.id.in_(ids)))
        await session.commit()
        msg = quote(f"Успешно удалено выбранных аккаунтов: {len(ids)}")
    else:
        msg = quote("Некорректные идентификаторы")

    query = f"?product_id={product_id}&" if product_id else "?"
    return RedirectResponse(url=f"/accounts{query}msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/delete-by-list")
async def delete_by_list_accounts(
    admin: AdminUser,
    delete_text: str = Form(...),
    product_id: int | None = Form(None),
    session: AsyncSession = Depends(get_db),
):
    deleted_count = await delete_accounts_by_list(session, delete_text, product_id)
    msg = quote(f"Удалено аккаунтов по списку: {deleted_count}")
    query = f"?product_id={product_id}&" if product_id else "?"
    return RedirectResponse(url=f"/accounts{query}msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)
