import io
import re
import zipfile
from typing import TypedDict
from sqlalchemy import select, delete, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Account, Product

class ParsedAccount(TypedDict):
    name: str
    date_info: str
    data: str

def parse_account_line(line: str) -> ParsedAccount | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    name = ""
    date_info = "N/A"

    if "," in raw:
        parts = raw.split(",", 1)
        name = parts[0].strip()
        date_info = parts[1].strip() or "N/A"
    elif ";" in raw:
        parts = raw.split(";", 1)
        name = parts[0].strip()
        date_info = parts[1].strip() or "N/A"
    elif ":" in raw and not raw.startswith("http"):
        parts = raw.split(":", 1)
        name = parts[0].strip()
        date_info = parts[1].strip() or "N/A"
    else:
        m = re.match(r"^(.+?)\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)$", raw)
        if m:
            name = m.group(1).strip()
            date_info = m.group(2).strip()
        else:
            name = raw
            date_info = "N/A"

    if not name:
        return None

    normalized_data = f"{name}, {date_info}" if date_info != "N/A" else name
    return {
        "name": name,
        "date_info": date_info,
        "data": normalized_data,
    }

def parse_accounts_from_text(text: str) -> list[ParsedAccount]:
    results: list[ParsedAccount] = []
    seen = set()
    for line in text.splitlines():
        parsed = parse_account_line(line)
        if parsed:
            key = parsed["data"].lower()
            if key not in seen:
                seen.add(key)
                results.append(parsed)
    return results

def parse_accounts_from_zip(zip_bytes: bytes) -> tuple[list[ParsedAccount], int]:
    results: list[ParsedAccount] = []
    seen = set()
    files_processed = 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.filename.startswith("__MACOSX"):
                continue

            try:
                data = zf.read(info)
            except Exception:
                continue

            if b"\x00" in data[:1024]:
                continue

            text = None
            for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
                try:
                    text = data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

            if text is None:
                continue

            files_processed += 1
            for line in text.splitlines():
                parsed = parse_account_line(line)
                if parsed:
                    key = parsed["data"].lower()
                    if key not in seen:
                        seen.add(key)
                        results.append(parsed)

    return results, files_processed

async def add_accounts_bulk(
    session: AsyncSession,
    product_id: int,
    accounts_data: list[ParsedAccount]
) -> int:
    if not accounts_data:
        return 0

    product = await session.get(Product, product_id)
    if not product:
        raise ValueError(f"Product with ID {product_id} not found")

    new_accounts = [
        Account(
            product_id=product_id,
            name=acc["name"],
            date_info=acc["date_info"],
            data=acc["data"],
            is_sold=False,
        )
        for acc in accounts_data
    ]

    session.add_all(new_accounts)
    await session.commit()
    return len(new_accounts)

async def delete_accounts_by_list(
    session: AsyncSession,
    text: str,
    product_id: int | None = None
) -> int:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0

    deleted_count = 0
    # Process lines in chunks to avoid oversized SQL queries
    for line in lines:
        parsed = parse_account_line(line)
        if not parsed:
            continue

        conditions = [
            Account.is_sold == False,
            or_(
                Account.data.ilike(parsed["data"]),
                Account.data.ilike(line),
                and_(
                    Account.name.ilike(parsed["name"]),
                    Account.date_info.ilike(parsed["date_info"])
                ) if parsed["date_info"] != "N/A" else Account.name.ilike(parsed["name"])
            )
        ]
        if product_id is not None and product_id > 0:
            conditions.append(Account.product_id == product_id)

        # Select first matching unsold account to delete
        stmt = select(Account.id).where(*conditions).limit(1)
        acc_id = await session.scalar(stmt)
        if acc_id:
            await session.execute(delete(Account).where(Account.id == acc_id))
            deleted_count += 1

    await session.commit()
    return deleted_count
