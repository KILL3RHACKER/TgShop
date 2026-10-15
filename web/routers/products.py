from urllib.parse import quote
from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Category, Product, Account
from web.auth import AdminUser
from web.dependencies import get_db

router = APIRouter(prefix="/products", tags=["products"])

def get_templates():
    from web.main import templates
    return templates

@router.get("", response_class=HTMLResponse)
async def list_products(
    request: Request,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
    msg: str | None = None,
    msg_type: str = "success",
):
    categories_res = await session.execute(select(Category).order_by(Category.name))
    categories = list(categories_res.scalars().all())

    products_res = await session.execute(select(Product).order_by(Product.name))
    products = list(products_res.scalars().all())

    # Get stock counts
    counts_res = await session.execute(
        select(Account.product_id, func.count(Account.id))
        .where(Account.is_sold == False)
        .group_by(Account.product_id)
    )
    stock_counts = dict(counts_res.all())

    # Get sold counts
    sold_counts_res = await session.execute(
        select(Account.product_id, func.count(Account.id))
        .where(Account.is_sold == True)
        .group_by(Account.product_id)
    )
    sold_counts = dict(sold_counts_res.all())

    templates = get_templates()
    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={
            "admin": admin,
            "active_tab": "products",
            "categories": categories,
            "products": products,
            "stock_counts": stock_counts,
            "sold_counts": sold_counts,
            "msg": msg,
            "msg_type": msg_type,
        }
    )

@router.post("/categories/create")
async def create_category(
    admin: AdminUser,
    name: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    clean_name = name.strip()
    if not clean_name:
        msg = quote("Название категории не может быть пустым")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    category = Category(name=clean_name)
    session.add(category)
    await session.commit()

    msg = quote(f"Категория «{clean_name}» успешно создана")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/categories/{category_id}/delete")
async def delete_category(
    category_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
):
    category = await session.get(Category, category_id)
    if not category:
        msg = quote("Категория не найдена")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    name = category.name
    await session.delete(category)
    await session.commit()

    msg = quote(f"Категория «{name}» и связанные товары удалены")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/create")
async def create_product(
    admin: AdminUser,
    category_id: int = Form(...),
    name: str = Form(...),
    price: float = Form(...),
    description: str = Form(""),
    content: str = Form(""),
    session: AsyncSession = Depends(get_db),
):
    clean_name = name.strip()
    if not clean_name:
        msg = quote("Название товара не может быть пустым")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    product = Product(
        category_id=category_id,
        name=clean_name,
        price=price,
        description=description.strip(),
        content=content.strip(),
    )
    session.add(product)
    await session.commit()

    msg = quote(f"Товар «{clean_name}» успешно добавлен")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/{product_id}/edit")
async def edit_product(
    product_id: int,
    admin: AdminUser,
    category_id: int = Form(...),
    name: str = Form(...),
    price: float = Form(...),
    description: str = Form(""),
    content: str = Form(""),
    session: AsyncSession = Depends(get_db),
):
    product = await session.get(Product, product_id)
    if not product:
        msg = quote("Товар не найден")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    product.category_id = category_id
    product.name = name.strip()
    product.price = price
    product.description = description.strip()
    product.content = content.strip()

    await session.commit()
    msg = quote(f"Товар «{product.name}» успешно обновлен")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/{product_id}/delete")
async def delete_product(
    product_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
):
    product = await session.get(Product, product_id)
    if not product:
        msg = quote("Товар не найден")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    name = product.name
    await session.delete(product)
    await session.commit()

    msg = quote(f"Товар «{name}» успешно удален")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)
