from urllib.parse import quote
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc, or_, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Account, Subcategory
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
    subcategory_id: int | None = None,
    product_id: int | None = None,  # Backward compatibility alias
    status_filter: str = "all",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    msg: str | None = None,
    msg_type: str = "success",
):
    selected_subcat_id = subcategory_id if subcategory_id is not None else product_id

    page = max(1, page)
    offset = (page - 1) * page_size

    # Fetch all subcategories for filter dropdown and modal selects
    subcategories_res = await session.execute(select(Subcategory).order_by(Subcategory.name))
    subcategories = list(subcategories_res.scalars().all())

    # Build base filter conditions
    conditions = []
    if selected_subcat_id is not None and selected_subcat_id > 0:
        conditions.append(Account.subcategory_id == selected_subcat_id)

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
            "subcategories": subcategories,
            "products": subcategories,  # Alias
            "selected_subcategory_id": selected_subcat_id,
            "selected_product_id": selected_subcat_id,
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
    subcategory_id: int | None = Form(None),
    product_id: int | None = Form(None),
    zip_file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
):
    target_id = subcategory_id if subcategory_id is not None else product_id
    if not target_id:
        msg = quote("Выберите подкатегорию для загрузки аккаунтов")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    if not zip_file.filename.lower().endswith(".zip"):
        msg = quote("Пожалуйста, загрузите валидный .zip архив")
        return RedirectResponse(
            url=f"/accounts?subcategory_id={target_id}&msg={msg}&msg_type=error",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        content = await zip_file.read()
        parsed_accounts, files_count = parse_accounts_from_zip(content)
        if not parsed_accounts:
            msg = quote("В ZIP-архиве не найдено подходящих строк аккаунтов")
            return RedirectResponse(
                url=f"/accounts?subcategory_id={target_id}&msg={msg}&msg_type=warning",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        added = await add_accounts_bulk(session, target_id, parsed_accounts)
        msg = quote(f"Успешно импортировано {added} аккаунтов из {files_count} файлов ZIP-архива (свежие на 1-й странице)")
        return RedirectResponse(
            url=f"/accounts?subcategory_id={target_id}&msg={msg}&msg_type=success",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as e:
        msg = quote(f"Ошибка при обработке ZIP: {str(e)}")
        return RedirectResponse(
            url=f"/accounts?subcategory_id={target_id}&msg={msg}&msg_type=error",
            status_code=status.HTTP_303_SEE_OTHER,
        )

@router.post("/upload-paste")
async def upload_paste_accounts(
    admin: AdminUser,
    subcategory_id: int | None = Form(None),
    product_id: int | None = Form(None),
    paste_text: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    target_id = subcategory_id if subcategory_id is not None else product_id
    if not target_id:
        msg = quote("Выберите подкатегорию для загрузки аккаунтов")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    try:
        parsed_accounts = parse_accounts_from_text(paste_text)
        if not parsed_accounts:
            msg = quote("Не удалось распознать аккаунты. Проверьте формат строк")
            return RedirectResponse(
                url=f"/accounts?subcategory_id={target_id}&msg={msg}&msg_type=warning",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        added = await add_accounts_bulk(session, target_id, parsed_accounts)
        msg = quote(f"Успешно добавлено {added} аккаунтов (все капсом, свежие на 1-й странице)")
        return RedirectResponse(
            url=f"/accounts?subcategory_id={target_id}&msg={msg}&msg_type=success",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as e:
        msg = quote(f"Ошибка при добавлении аккаунтов: {str(e)}")
        return RedirectResponse(
            url=f"/accounts?subcategory_id={target_id}&msg={msg}&msg_type=error",
            status_code=status.HTTP_303_SEE_OTHER,
        )

@router.post("/{account_id}/delete")
async def delete_single_account(
    account_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    subcategory_id: int | None = None,
    product_id: int | None = None,
):
    target_id = subcategory_id if subcategory_id is not None else product_id
    acc = await session.get(Account, account_id)
    if not acc:
        msg = quote("Аккаунт не найден")
        return RedirectResponse(url=f"/accounts?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    await session.delete(acc)
    await session.commit()
    msg = quote(f"Аккаунт #{account_id} успешно удален")
    query_sub = f"?subcategory_id={target_id}&" if target_id else "?"
    return RedirectResponse(
        url=f"/accounts{query_sub}msg={msg}&msg_type=success",
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
    subcategory_id = form.get("subcategory_id") or form.get("product_id")

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
    query_sub = f"?subcategory_id={subcategory_id}&" if subcategory_id else "?"
    return RedirectResponse(
        url=f"/accounts{query_sub}msg={msg}&msg_type=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )

@router.post("/delete-by-list")
async def delete_by_list_accounts(
    admin: AdminUser,
    delete_text: str = Form(...),
    subcategory_id: int | None = Form(None),
    product_id: int | None = Form(None),
    session: AsyncSession = Depends(get_db),
):
    target_id = subcategory_id if subcategory_id is not None else product_id
    try:
        deleted = await delete_accounts_by_list(session, delete_text, target_id)
        msg = quote(f"Успешно найдено и удалено {deleted} не проданных аккаунтов по списку")
        query_sub = f"?subcategory_id={target_id}&" if target_id else "?"
        return RedirectResponse(
            url=f"/accounts{query_sub}msg={msg}&msg_type=success",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as e:
        msg = quote(f"Ошибка при пакетном удалении: {str(e)}")
        return RedirectResponse(
            url=f"/accounts?msg={msg}&msg_type=error",
            status_code=status.HTTP_303_SEE_OTHER,
        )
