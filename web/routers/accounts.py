from urllib.parse import quote
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc, or_, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Account, Product
from web.auth import AdminUser
from web.dependencies import get_db
from web.services import (
    parse_accounts_from_text,
    parse_accounts_from_zip,
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
    status_filter: str = "all",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    msg: str | None = None,
    msg_type: str = "success",
):
    page = max(1, page)
    offset = (page - 1) * page_size

    # Fetch all products for filter dropdown and modal selects
    products_res = await session.execute(select(Product).order_by(Product.name))
    products = list(products_res.scalars().all())

    # Build base filter conditions
    conditions = []
    if product_id is not None and product_id > 0:
        conditions.append(Account.product_id == product_id)

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

    # Fetch paginated accounts
    stmt = select(Account).order_by(desc(Account.id)).offset(offset).limit(page_size)
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
            "selected_product_id": product_id,
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
async def upload_zip_accounts(
    admin: AdminUser,
    product_id: int = Form(...),
    zip_file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
):
    if not zip_file.filename.lower().endswith(".zip"):
        msg = quote("Пожалуйста, загрузите валидный .zip архив")
        return RedirectResponse(
            url=f"/accounts?product_id={product_id}&msg={msg}&msg_type=error",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        content = await zip_file.read()
        parsed_accounts, files_count = parse_accounts_from_zip(content)
        if not parsed_accounts:
            msg = quote("В ZIP-архиве не найдено подходящих текстовых строк аккаунтов")
            return RedirectResponse(
                url=f"/accounts?product_id={product_id}&msg={msg}&msg_type=warning",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        added = await add_accounts_bulk(session, product_id, parsed_accounts)
        msg = quote(f"Успешно импортировано {added} аккаунтов из {files_count} файлов ZIP-архива")
        return RedirectResponse(
            url=f"/accounts?product_id={product_id}&msg={msg}&msg_type=success",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as e:
        msg = quote(f"Ошибка при обработке ZIP: {str(e)}")
        return RedirectResponse(
            url=f"/accounts?product_id={product_id}&msg={msg}&msg_type=error",
            status_code=status.HTTP_303_SEE_OTHER,
        )

@router.post("/upload-paste")
async def upload_paste_accounts(
    admin: AdminUser,
    product_id: int = Form(...),
    paste_text: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    try:
        parsed_accounts = parse_accounts_from_text(paste_text)
        if not parsed_accounts:
            msg = quote("Не удалось распознать аккаунты. Проверьте формат строк")
            return RedirectResponse(
                url=f"/accounts?product_id={product_id}&msg={msg}&msg_type=warning",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        added = await add_accounts_bulk(session, product_id, parsed_accounts)
        msg = quote(f"Успешно добавлено {added} аккаунтов")
        return RedirectResponse(
            url=f"/accounts?product_id={product_id}&msg={msg}&msg_type=success",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as e:
        msg = quote(f"Ошибка при добавлении аккаунтов: {str(e)}")
        return RedirectResponse(
            url=f"/accounts?product_id={product_id}&msg={msg}&msg_type=error",
            status_code=status.HTTP_303_SEE_OTHER,
        )

@router.post("/{account_id}/delete")
async def delete_single_account(
    account_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    product_id: int | None = None,
):
    acc = await session.get(Account, account_id)
    if not acc:
        msg = quote("Аккаунт не найден")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    await session.delete(acc)
    await session.commit()
    msg = quote(f"Аккаунт #{account_id} успешно удален")
    query_prod = f"?product_id={product_id}&" if product_id else "?"
    return RedirectResponse(
        url=f"/accounts{query_prod}msg={msg}&msg_type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )

@router.post("/delete-selected")
async def delete_selected_accounts(
    request: Request,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
):
    form = await request.form()
    ids_raw = form.getlist("selected_ids")
    product_id = form.get("product_id")

    if not ids_raw:
        msg = quote("Не выбрано ни одного аккаунта для удаления")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=warning", status_code=status.HTTP_303_SEE_OTHER)

    account_ids = []
    for item in ids_raw:
        try:
            account_ids.append(int(item))
        except ValueError:
            pass

    if account_ids:
        await session.execute(delete(Account).where(Account.id.in_(account_ids)))
        await session.commit()

    msg = quote(f"Успешно удалено {len(account_ids)} аккаунтов")
    query_prod = f"?product_id={product_id}&" if product_id else "?"
    return RedirectResponse(
        url=f"/accounts{query_prod}msg={msg}&msg_type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )

@router.post("/delete-by-list")
async def delete_by_list_accounts(
    admin: AdminUser,
    delete_text: str = Form(...),
    product_id: int | None = Form(None),
    session: AsyncSession = Depends(get_db),
):
    try:
        deleted = await delete_accounts_by_list(session, delete_text, product_id)
        msg = quote(f"Успешно найдено и удалено {deleted} не проданных аккаунтов по списку")
        query_prod = f"?product_id={product_id}&" if product_id else "?"
        return RedirectResponse(
            url=f"/accounts{query_prod}msg={msg}&msg_type=success",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as e:
        msg = quote(f"Ошибка при пакетном удалении: {str(e)}")
        return RedirectResponse(
            url=f"/accounts?msg={msg}&msg_type=error",
            status_code=status.HTTP_303_SEE_OTHER,
        )
