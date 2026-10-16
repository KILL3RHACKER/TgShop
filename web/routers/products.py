from urllib.parse import quote
from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Category, Subcategory, Product, Account
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
    # Fetch all categories
    categories_res = await session.execute(
        select(Category)
        .options(selectinload(Category.subcategories).selectinload(Subcategory.products))
        .order_by(Category.name)
    )
    categories = list(categories_res.scalars().all())

    # Fetch all subcategories
    subcategories_res = await session.execute(
        select(Subcategory)
        .options(selectinload(Subcategory.category), selectinload(Subcategory.products))
        .order_by(Subcategory.name)
    )
    subcategories = list(subcategories_res.scalars().all())

    # Fetch all products
    products_res = await session.execute(
        select(Product)
        .options(selectinload(Product.subcategory).selectinload(Subcategory.category))
        .order_by(Product.name)
    )
    products = list(products_res.scalars().all())

    # Stock counts per product
    counts_res = await session.execute(
        select(Account.product_id, func.count(Account.id))
        .where(Account.is_sold == False)
        .group_by(Account.product_id)
    )
    stock_counts = dict(counts_res.all())

    # Sold counts per product
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
            "subcategories": subcategories,
            "products": products,
            "stock_counts": stock_counts,
            "sold_counts": sold_counts,
            "msg": msg,
            "msg_type": msg_type,
        }
    )

# --- Category Handlers ---
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

    msg = quote(f"Категория «{name}» и все вложенные позиции удалены")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

# --- Subcategory Handlers ---
@router.post("/subcategories/create")
async def create_subcategory(
    admin: AdminUser,
    category_id: int = Form(...),
    name: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    clean_name = name.strip()
    if not clean_name:
        msg = quote("Название подкатегории не может быть пустым")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    subcat = Subcategory(
        category_id=category_id,
        name=clean_name,
    )
    session.add(subcat)
    await session.commit()

    msg = quote(f"Подкатегория «{clean_name}» успешно создана")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/subcategories/{subcategory_id}/edit")
async def edit_subcategory(
    subcategory_id: int,
    admin: AdminUser,
    category_id: int = Form(...),
    name: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    subcat = await session.get(Subcategory, subcategory_id)
    if not subcat:
        msg = quote("Подкатегория не найдена")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    subcat.category_id = category_id
    subcat.name = name.strip()

    await session.commit()
    msg = quote(f"Подкатегория «{subcat.name}» успешно обновлена")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/subcategories/{subcategory_id}/delete")
async def delete_subcategory(
    subcategory_id: int,
    admin: AdminUser,
    session: AsyncSession = Depends(get_db),
):
    subcat = await session.get(Subcategory, subcategory_id)
    if not subcat:
        msg = quote("Подкатегория не найдена")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    name = subcat.name
    await session.delete(subcat)
    await session.commit()

    msg = quote(f"Подкатегория «{name}» и её товары удалены")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

# --- Product (Товар) Handlers ---
@router.post("/create")
async def create_product(
    admin: AdminUser,
    subcategory_id: int = Form(...),
    name: str = Form(...),
    price: float = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_db),
):
    clean_name = name.strip()
    if not clean_name:
        msg = quote("Название товара не может быть пустым")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    product = Product(
        subcategory_id=subcategory_id,
        name=clean_name,
        price=price,
        description=description.strip(),
    )
    session.add(product)
    await session.commit()

    msg = quote(f"Товар «{clean_name}» успешно создан")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)

@router.post("/{product_id}/edit")
async def edit_product(
    product_id: int,
    admin: AdminUser,
    subcategory_id: int = Form(...),
    name: str = Form(...),
    price: float = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_db),
):
    product = await session.get(Product, product_id)
    if not product:
        msg = quote("Товар не найден")
        return RedirectResponse(url=f"/products?msg={msg}&msg_type=error", status_code=status.HTTP_303_SEE_OTHER)

    product.subcategory_id = subcategory_id
    product.name = name.strip()
    product.price = price
    product.description = description.strip()

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

    msg = quote(f"Товар «{name}» и связанные аккаунты удалены")
    return RedirectResponse(url=f"/products?msg={msg}&msg_type=success", status_code=status.HTTP_303_SEE_OTHER)
