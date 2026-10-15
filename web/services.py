import io
import re
import zipfile
from typing import TypedDict
from sqlalchemy import select, delete, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Account, Subcategory

class ParsedAccount(TypedDict):
    name: str
    date_info: str
    data: str
    raw_data: str

def parse_account_line(line: str) -> ParsedAccount | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    # Regex: match Name and DD/MM. Everything after MM is strictly trimmed/cut off!
    m = re.search(r"^([A-Za-zА-Яа-яЁё0-9_\-\.\s]+?)\s*[,;:\s]\s*(\d{1,2})[/-](\d{1,2})", raw)
    if m:
        raw_name = m.group(1).strip()
        # Автоматически меняем название в Капс NAME SURNAME
        name = " ".join(raw_name.split()).upper()
        day = int(m.group(2))
        month = int(m.group(3))
        date_info = f"{day:02d}/{month:02d}"
        normalized_data = f"{name}, {date_info}"
        return {
            "name": name,
            "date_info": date_info,
            "data": normalized_data,
            "raw_data": raw,
        }

    # Fallback if no date pattern found
    clean_name = " ".join(raw.split()).upper()
    return {
        "name": clean_name,
        "date_info": "N/A",
        "data": clean_name,
        "raw_data": raw,
    }

def parse_accounts_from_text(text: str) -> list[ParsedAccount]:
    results: list[ParsedAccount] = []
    seen = set()
    for line in text.splitlines():
        parsed = parse_account_line(line)
        if parsed:
            key = parsed["data"].upper()
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
                    key = parsed["data"].upper()
                    if key not in seen:
                        seen.add(key)
                        results.append(parsed)

    return results, files_processed

async def add_accounts_bulk(
    session: AsyncSession,
    subcategory_id: int,
    accounts_data: list[ParsedAccount]
) -> int:
    if not accounts_data:
        return 0

    subcategory = await session.get(Subcategory, subcategory_id)
    if not subcategory:
        raise ValueError(f"Subcategory with ID {subcategory_id} not found")

    new_accounts = [
        Account(
            subcategory_id=subcategory_id,
            name=acc["name"],
            date_info=acc["date_info"],
            data=acc["data"],
            raw_data=acc.get("raw_data"),
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
    subcategory_id: int | None = None
) -> int:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0

    deleted_count = 0
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
        if subcategory_id is not None and subcategory_id > 0:
            conditions.append(Account.subcategory_id == subcategory_id)

        stmt = select(Account.id).where(*conditions).limit(1)
        acc_id = await session.scalar(stmt)
        if acc_id:
            await session.execute(delete(Account).where(Account.id == acc_id))
            deleted_count += 1

    await session.commit()
    return deleted_count
