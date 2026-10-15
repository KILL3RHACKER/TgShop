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

MONTH_NAMES = {
    "jan": 1, "янв": 1, "feb": 2, "фев": 2, "mar": 3, "мар": 3,
    "apr": 4, "апр": 4, "may": 5, "май": 5, "мая": 5, "jun": 6, "июн": 6,
    "jul": 7, "июл": 7, "aug": 8, "авг": 8, "sep": 9, "сен": 9,
    "oct": 10, "окт": 10, "nov": 11, "ноя": 11, "dec": 12, "дек": 12,
}

def extract_name_and_date(raw: str) -> tuple[str, str] | None:
    """
    Универсальный парсер:
    - Принимает дату любого формата: DD.MM, DD/MM, DD-MM, DD MM, DD.MM.YYYY, YYYY-MM-DD и др.
    - Автоматически приводит имя к ВЕРХНЕМУ РЕГИСТРУ (CAPS).
    - Дата на выходе ВСЕГДА в формате DD.MM (с точкой!).
    - Все данные/символы после месяца строго отсекаются.
    """
    # 1. ISO format: (name)[delimiter](YYYY)[-/. ](MM)[-/. ](DD)
    m_iso = re.search(r'^(.*?)[,;:\s|]+\s*(?:20\d\d|19\d\d)[\.\/\-\s](\d{1,2})[\.\/\-\s](\d{1,2})', raw)
    if m_iso:
        name_raw = m_iso.group(1).strip()
        month, day = int(m_iso.group(2)), int(m_iso.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            clean_name = " ".join(name_raw.split()).upper()
            return clean_name, f"{day:02d}.{month:02d}"

    # 2. Text month: (name)[delimiter](DD)[-/. ](MonthName)
    m_text = re.search(r'^(.*?)[,;:\s|]+\s*(\d{1,2})[\.\/\-\s]([A-Za-zА-Яа-яЁё]{3,8})', raw, re.IGNORECASE)
    if m_text:
        name_raw = m_text.group(1).strip()
        day = int(m_text.group(2))
        m_str = m_text.group(3).lower()[:3]
        if m_str in MONTH_NAMES and 1 <= day <= 31:
            clean_name = " ".join(name_raw.split()).upper()
            return clean_name, f"{day:02d}.{MONTH_NAMES[m_str]:02d}"

    # 3. Standard DD[-/. ]MM with optional [-/. ](YYYY|YY). Extra tokens after date are discarded.
    m_std = re.search(r'^(.*?)[,;:\s|]+\s*(\d{1,2})[\.\/\-\s](\d{1,2})(?:[\.\/\-\s](?:\d{4}|\d{2}))?(?:\b|[^0-9]|$)', raw)
    if m_std:
        name_raw = m_std.group(1).strip()
        d, m = int(m_std.group(2)), int(m_std.group(3))
        if 1 <= d <= 31 and 1 <= m <= 12:
            clean_name = " ".join(name_raw.split()).upper()
            return clean_name, f"{d:02d}.{m:02d}"
        elif 1 <= m <= 31 and 1 <= d <= 12:  # Inverted MM/DD
            clean_name = " ".join(name_raw.split()).upper()
            return clean_name, f"{m:02d}.{d:02d}"

    return None

def parse_account_line(line: str) -> ParsedAccount | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    extracted = extract_name_and_date(raw)
    if extracted:
        name, date_info = extracted
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

        dot_date = parsed["date_info"]
        slash_date = dot_date.replace(".", "/")
        dash_date = dot_date.replace(".", "-")

        conditions = [
            Account.is_sold == False,
            or_(
                Account.data.ilike(parsed["data"]),
                Account.data.ilike(f"{parsed['name']}, {slash_date}"),
                Account.data.ilike(f"{parsed['name']}, {dash_date}"),
                Account.data.ilike(line),
                and_(
                    Account.name.ilike(parsed["name"]),
                    or_(
                        Account.date_info.ilike(dot_date),
                        Account.date_info.ilike(slash_date),
                        Account.date_info.ilike(dash_date),
                    )
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
