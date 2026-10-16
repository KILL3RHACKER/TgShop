import os
import io
import re
import tempfile
import subprocess
import zipfile
from datetime import date, datetime
from typing import TypedDict
from sqlalchemy import select, delete, or_, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Account, Product, Subcategory, Category

class ParsedAccount(TypedDict):
    state: str
    name: str
    date_info: str
    data: str
    raw_data: str
    registration_date: date | None

MONTH_NAMES = {
    "jan": 1, "янв": 1, "feb": 2, "фев": 2, "mar": 3, "мар": 3,
    "apr": 4, "апр": 4, "may": 5, "май": 5, "мая": 5, "jun": 6, "июн": 6,
    "jul": 7, "июл": 7, "aug": 8, "авг": 8, "sep": 9, "сен": 9,
    "oct": 10, "окт": 10, "nov": 11, "ноя": 11, "dec": 12, "дек": 12,
}

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"
}

STATE_NAMES_MAP = {
    "CALIFORNIA": "CA", "TEXAS": "TX", "FLORIDA": "FL", "NEW YORK": "NY",
    "ILLINOIS": "IL", "PENNSYLVANIA": "PA", "OHIO": "OH", "GEORGIA": "GA",
    "NORTH CAROLINA": "NC", "MICHIGAN": "MI", "NEW JERSEY": "NJ", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "ARIZONA": "AZ", "MASSACHUSETTS": "MA", "TENNESSEE": "TN",
    "INDIANA": "IN", "MISSOURI": "MO", "MARYLAND": "MD", "WISCONSIN": "WI",
    "COLORADO": "CO", "MINNESOTA": "MN", "SOUTH CAROLINA": "SC", "ALABAMA": "AL",
    "LOUISIANA": "LA", "KENTUCKY": "KY", "OREGON": "OR", "OKLAHOMA": "OK",
    "CONNECTICUT": "CT", "UTAH": "UT", "IOWA": "IA", "NEVADA": "NV",
    "ARKANSAS": "AR", "MISSISSIPPI": "MS", "KANSAS": "KS", "NEW MEXICO": "NM",
    "NEBRASKA": "NE", "IDAHO": "ID", "WEST VIRGINIA": "WV", "HAWAII": "HI",
    "NEW HAMPSHIRE": "NH", "MAINE": "ME", "MONTANA": "MT", "RHODE ISLAND": "RI",
    "DELAWARE": "DE", "SOUTH DAKOTA": "SD", "NORTH DAKOTA": "ND", "ALASKA": "AK",
    "VERMONT": "VT", "WYOMING": "WY"
}

DEFAULT_AGE_TIERS = [
    {
        "name": "Отлёжка 7+ Дней (7-14 дн.)",
        "min_days": 7,
        "max_days": 14,
        "price": 5.0,
        "description": "Отлёжка 7-14 полных дней. Прогретые аккаунты."
    },
    {
        "name": "Отлёжка 15+ Дней (15-29 дн.)",
        "min_days": 15,
        "max_days": 29,
        "price": 10.0,
        "description": "Отлёжка 15-29 полных дней. Высокий траст."
    },
    {
        "name": "Отлёжка 30+ Дней (30+ дн.)",
        "min_days": 30,
        "max_days": None,
        "price": 15.0,
        "description": "Отлёжка от 30 дней и старше. Максимальная отлёжка."
    },
    {
        "name": "Свежие (до 7 дн.)",
        "min_days": 0,
        "max_days": 6,
        "price": 3.0,
        "description": "Свежая регистрация до 7 дней отлёжки."
    },
]

def extract_state_from_address_line(text: str) -> str | None:
    """
    Извлекает штат США из строки адреса вида:
    Address: 109 1ST ST\tRUPERT\tID\t83350
    или Address: 123 Main St, Los Angeles, CA 90001
    """
    m_addr = re.search(r'Address:\s*([^\r\n]+)', text, re.IGNORECASE)
    addr_line = m_addr.group(1).strip() if m_addr else text

    # Standard format: State code before 5-digit US zip: \b([A-Za-z]{2})\b\s+\d{5}
    m_state = re.search(r'\b([A-Za-z]{2})\b[\s\t]+(?:\d{5}|\d{5}-\d{4})', addr_line)
    if m_state:
        cand = m_state.group(1).upper()
        if cand in US_STATES:
            return cand

    # Tab-separated State code: \t([A-Za-z]{2})\t
    m_tab = re.search(r'[\t,]\s*([A-Za-z]{2})\s*[\t,]', addr_line)
    if m_tab:
        cand = m_tab.group(1).upper()
        if cand in US_STATES:
            return cand

    # Direct search in US_STATES
    for token in addr_line.replace(",", " ").replace("\t", " ").split():
        token_clean = token.strip().upper()
        if token_clean in US_STATES:
            return token_clean

    return None

def parse_account_from_info_txt(info_text: str, context_name: str = "") -> ParsedAccount | None:
    """
    Парсит аккаунт из содержимого info.txt и/или имени папки/архива:
    - Address: 109 1ST ST\tRUPERT\tID\t83350 -> State: ID
    - Name: JOHN DOE (или из имени папки)
    - Date: 15.04 (или из имени папки)
    """
    state = extract_state_from_address_line(info_text) or "US"

    name: str | None = None
    date_info: str | None = None
    reg_date: date | None = None

    # Check for Name: line
    m_name = re.search(r'(?:Full\s*Name|Name|Client|Owner):\s*([^\r\n]+)', info_text, re.IGNORECASE)
    if m_name:
        name = " ".join(m_name.group(1).strip().split()).upper()

    # Check for Date: / DOB: / Reg Date: line
    m_date = re.search(r'(?:Date|Reg\s*Date|DOB|Created):\s*([^\r\n]+)', info_text, re.IGNORECASE)
    if m_date:
        extracted = extract_state_name_date(f"DUMMY {m_date.group(1)}")
        if extracted:
            _, _, date_info, reg_date = extracted

    # If name or date not in info.txt, extract from context_name (имя папки или файла)
    if not name or not date_info:
        clean_ctx = os.path.basename(context_name).replace("_", " ")
        extracted_ctx = extract_state_name_date(clean_ctx)
        if extracted_ctx:
            ctx_state, ctx_name, ctx_date, ctx_reg = extracted_ctx
            if not name:
                name = ctx_name
            if not date_info:
                date_info = ctx_date
                reg_date = ctx_reg
            if state == "US" and ctx_state != "US":
                state = ctx_state

    if not name or name == "UNKNOWN":
        clean_ctx = os.path.basename(context_name).replace("_", " ").strip()
        if clean_ctx:
            name = clean_ctx.upper()
        else:
            name = "UNKNOWN"

    if not date_info:
        today = date.today()
        date_info = today.strftime("%d.%m")
        reg_date = today

    normalized_data = f"{state} | {name} | {date_info}"
    return {
        "state": state,
        "name": name,
        "date_info": date_info,
        "data": normalized_data,
        "raw_data": info_text[:200],
        "registration_date": reg_date,
    }

def calculate_registration_date(day: int, month: int, explicit_year: int | None = None) -> date:
    """
    Рассчитывает точную дату регистрации.
    Если год не указан явно:
      Если дата (MM, DD) позже сегодняшней -> значит регистрация была в прошлом году (today.year - 1).
      Иначе -> в текущем году (today.year).
    """
    today = date.today()
    if explicit_year is not None and 2000 <= explicit_year <= 2100:
        year = explicit_year
    else:
        if (month, day) > (today.month, today.day):
            year = today.year - 1
        else:
            year = today.year

    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, 28)

def extract_state_name_date(raw: str) -> tuple[str, str, str, date | None] | None:
    """
    Универсальный парсер строки:
    Формат: ST | NAME SURNAME | DD.MM
    - ST: Штат (2 буквы или название штата, по умолчанию 'US')
    - NAME SURNAME: Имя и фамилия (CAPS)
    - DD.MM: Дата регистрации с точкой
    - Все данные после месяца строго отсекаются.
    """
    line = raw.strip()
    if not line or line.startswith("#"):
        return None

    # Find date component
    d: int | None = None
    m: int | None = None
    explicit_year: int | None = None
    prefix: str = ""

    # 1. ISO format: (prefix)[delimiter](YYYY)[-/. ](MM)[-/. ](DD)
    m_iso = re.search(r'^(.*?)[,;:\s|]+\s*(20\d\d|19\d\d)[\.\/\-\s](\d{1,2})[\.\/\-\s](\d{1,2})', line)
    if m_iso:
        prefix = m_iso.group(1).strip()
        explicit_year = int(m_iso.group(2))
        m = int(m_iso.group(3))
        d = int(m_iso.group(4))

    # 2. Text month: (prefix)[delimiter](DD)[-/. ](MonthName)
    if d is None:
        m_text = re.search(r'^(.*?)[,;:\s|]+\s*(\d{1,2})[\.\/\-\s]([A-Za-zА-Яа-яЁё]{3,8})(?:[\.\/\-\s](\d{4}|\d{2}))?', line, re.IGNORECASE)
        if m_text:
            m_str = m_text.group(3).lower()[:3]
            if m_str in MONTH_NAMES:
                prefix = m_text.group(1).strip()
                d = int(m_text.group(2))
                m = MONTH_NAMES[m_str]
                if m_text.group(4):
                    y_val = int(m_text.group(4))
                    explicit_year = y_val if y_val >= 100 else 2000 + y_val

    # 3. Standard DD[-/. ]MM with optional year
    if d is None:
        m_std = re.search(r'^(.*?)[,;:\s|]+\s*(\d{1,2})[\.\/\-\s](\d{1,2})(?:[\.\/\-\s](\d{4}|\d{2}))?(?:\b|[^0-9]|$)', line)
        if m_std:
            prefix = m_std.group(1).strip()
            v1, v2 = int(m_std.group(2)), int(m_std.group(3))
            if 1 <= v1 <= 31 and 1 <= v2 <= 12:
                d, m = v1, v2
            elif 1 <= v2 <= 31 and 1 <= v1 <= 12:
                d, m = v2, v1
            if m_std.group(4):
                y_val = int(m_std.group(4))
                explicit_year = y_val if y_val >= 100 else 2000 + y_val

    if d is None or m is None or not (1 <= d <= 31 and 1 <= m <= 12):
        return None

    date_str = f"{d:02d}.{m:02d}"
    reg_date = calculate_registration_date(d, m, explicit_year)

    # Now parse state and name from prefix
    state = "US"
    name = prefix

    # Delimiter-based splitting: '|', ',', ':'
    parts: list[str] = []
    if "|" in prefix:
        parts = [p.strip() for p in prefix.split("|") if p.strip()]
    elif "," in prefix:
        parts = [p.strip() for p in prefix.split(",") if p.strip()]
    elif ":" in prefix:
        parts = [p.strip() for p in prefix.split(":") if p.strip()]

    if len(parts) >= 2:
        cand_state = parts[0].upper().strip()
        if cand_state in US_STATES:
            state = cand_state
            name = " ".join(parts[1:])
        elif cand_state in STATE_NAMES_MAP:
            state = STATE_NAMES_MAP[cand_state]
            name = " ".join(parts[1:])
        elif len(cand_state) <= 4 and cand_state.isalpha():
            state = cand_state
            name = " ".join(parts[1:])
        else:
            name = " ".join(parts)
    else:
        # Space-separated first token check (e.g. "CA JOHN DOE")
        tokens = prefix.split()
        if tokens:
            cand_state = tokens[0].upper()
            if cand_state in US_STATES or cand_state in STATE_NAMES_MAP:
                state = STATE_NAMES_MAP.get(cand_state, cand_state)
                name = " ".join(tokens[1:])
            elif len(cand_state) == 2 and cand_state.isalpha():
                state = cand_state
                name = " ".join(tokens[1:])
            else:
                name = prefix

    clean_name = " ".join(name.split()).upper()
    if not clean_name:
        clean_name = "UNKNOWN"

    return state, clean_name, date_str, reg_date

def parse_account_line(line: str) -> ParsedAccount | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    extracted = extract_state_name_date(raw)
    if extracted:
        state, name, date_info, reg_date = extracted
        normalized_data = f"{state} | {name} | {date_info}"
        return {
            "state": state,
            "name": name,
            "date_info": date_info,
            "data": normalized_data,
            "raw_data": raw,
            "registration_date": reg_date,
        }

    # Fallback if no valid date found
    clean_name = " ".join(raw.split()).upper()
    return {
        "state": "US",
        "name": clean_name,
        "date_info": "N/A",
        "data": f"US | {clean_name} | N/A",
        "raw_data": raw,
        "registration_date": None,
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

    dir_info_texts: dict[str, str] = {}
    dir_info_states: dict[str, str] = {}
    file_contents: list[tuple[str, str]] = []

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
            fn_base = os.path.basename(info.filename).lower()
            folder = os.path.dirname(info.filename)

            if fn_base == "info.txt":
                dir_info_texts[folder] = text
                st = extract_state_from_address_line(text)
                if st:
                    dir_info_states[folder] = st
            else:
                file_contents.append((info.filename, text))

    # Parse accounts from info.txt if present
    for folder, info_text in dir_info_texts.items():
        ctx_name = folder if folder else "ACCOUNT"
        acc = parse_account_from_info_txt(info_text, context_name=ctx_name)
        if acc:
            key = acc["data"].upper()
            if key not in seen:
                seen.add(key)
                results.append(acc)

    # Parse remaining text files, inheriting state from info.txt if present
    for filename, text in file_contents:
        folder = os.path.dirname(filename)
        inherited_state = dir_info_states.get(folder) or dir_info_states.get("")

        for line in text.splitlines():
            parsed = parse_account_line(line)
            if parsed:
                if parsed["state"] == "US" and inherited_state:
                    parsed["state"] = inherited_state
                    parsed["data"] = f"{parsed['state']} | {parsed['name']} | {parsed['date_info']}"
                key = parsed["data"].upper()
                if key not in seen:
                    seen.add(key)
                    results.append(parsed)

    return results, files_processed

def parse_accounts_from_rar(rar_bytes: bytes) -> tuple[list[ParsedAccount], int]:
    results: list[ParsedAccount] = []
    seen = set()
    files_processed = 0

    dir_info_texts: dict[str, str] = {}
    dir_info_states: dict[str, str] = {}
    file_contents: list[tuple[str, str]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        rar_file = os.path.join(tmpdir, "archive.rar")
        with open(rar_file, "wb") as f:
            f.write(rar_bytes)

        out_dir = os.path.join(tmpdir, "out")
        os.makedirs(out_dir, exist_ok=True)

        try:
            subprocess.run(
                ["unrar", "x", "-f", rar_file, out_dir],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False
            )
        except Exception:
            pass

        for root, _, files in os.walk(out_dir):
            for file in files:
                if not file.lower().endswith(".txt"):
                    continue
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "rb") as f:
                        data = f.read()
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
                rel_path = os.path.relpath(file_path, out_dir)
                folder = os.path.dirname(rel_path)
                fn_base = os.path.basename(file).lower()

                if fn_base == "info.txt":
                    dir_info_texts[folder] = text
                    st = extract_state_from_address_line(text)
                    if st:
                        dir_info_states[folder] = st
                else:
                    file_contents.append((rel_path, text))

        for folder, info_text in dir_info_texts.items():
            ctx_name = folder if folder else "ACCOUNT"
            acc = parse_account_from_info_txt(info_text, context_name=ctx_name)
            if acc:
                key = acc["data"].upper()
                if key not in seen:
                    seen.add(key)
                    results.append(acc)

        for filename, text in file_contents:
            folder = os.path.dirname(filename)
            inherited_state = dir_info_states.get(folder) or dir_info_states.get("")

            for line in text.splitlines():
                parsed = parse_account_line(line)
                if parsed:
                    if parsed["state"] == "US" and inherited_state:
                        parsed["state"] = inherited_state
                        parsed["data"] = f"{parsed['state']} | {parsed['name']} | {parsed['date_info']}"
                    key = parsed["data"].upper()
                    if key not in seen:
                        seen.add(key)
                        results.append(parsed)

    return results, files_processed

def parse_accounts_from_archive(filename: str, archive_bytes: bytes) -> tuple[list[ParsedAccount], int]:
    fn = filename.lower()
    if fn.endswith(".zip"):
        return parse_accounts_from_zip(archive_bytes)
    elif fn.endswith(".rar"):
        return parse_accounts_from_rar(archive_bytes)
    else:
        raise ValueError("Поддерживаются только архивы .zip и .rar")

def parse_archive_by_files(filename: str, archive_bytes: bytes) -> tuple[dict[str, list[ParsedAccount]], int]:
    """
    Парсит архив (.zip или .rar) и возвращает:
    { "имя_файла_без_расширения": [ParsedAccount, ...] }
    """
    file_map: dict[str, list[ParsedAccount]] = {}
    files_processed = 0
    fn = filename.lower()

    if fn.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
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

                base_name = os.path.splitext(os.path.basename(info.filename))[0].strip()
                lines = [parse_account_line(l) for l in text.splitlines() if parse_account_line(l)]
                if lines:
                    files_processed += 1
                    file_map.setdefault(base_name, []).extend(lines)

    elif fn.endswith(".rar"):
        with tempfile.TemporaryDirectory() as tmpdir:
            rar_file = os.path.join(tmpdir, "archive.rar")
            with open(rar_file, "wb") as f:
                f.write(archive_bytes)
            out_dir = os.path.join(tmpdir, "out")
            os.makedirs(out_dir, exist_ok=True)
            try:
                subprocess.run(
                    ["unrar", "x", "-f", rar_file, out_dir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False
                )
            except Exception:
                pass

            for root, _, files in os.walk(out_dir):
                for file in files:
                    if not file.lower().endswith(".txt"):
                        continue
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "rb") as f:
                            data = f.read()
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
                    base_name = os.path.splitext(file)[0].strip()
                    lines = [parse_account_line(l) for l in text.splitlines() if parse_account_line(l)]
                    if lines:
                        files_processed += 1
                        file_map.setdefault(base_name, []).extend(lines)

    return file_map, files_processed

async def get_or_create_age_tier_products(
    session: AsyncSession,
    subcategory_id: int
) -> list[Product]:
    """
    Получает или создает стандартные товары по диапазонам отлёжки (7+, 15+, 30+, до 7).
    """
    res = await session.execute(
        select(Product)
        .where(Product.subcategory_id == subcategory_id)
        .order_by(Product.min_days.asc().nullslast())
    )
    products = list(res.scalars().all())

    # Check if age tier products already exist
    has_tier = any(p.min_days is not None for p in products)
    if not has_tier:
        created = []
        for tier in DEFAULT_AGE_TIERS:
            p = Product(
                subcategory_id=subcategory_id,
                name=tier["name"],
                description=tier["description"],
                price=tier["price"],
                min_days=tier["min_days"],
                max_days=tier["max_days"],
            )
            session.add(p)
            created.append(p)
        await session.commit()
        for p in created:
            await session.refresh(p)
        return created

    return products

def match_product_by_age(products: list[Product], age_days: int) -> Product | None:
    """
    Находит товар, в диапазон которого попадает возраст аккаунта (age_days).
    """
    for p in products:
        if p.min_days is not None:
            min_ok = age_days >= p.min_days
            max_ok = (p.max_days is None) or (age_days <= p.max_days)
            if min_ok and max_ok:
                return p

    for p in products:
        if p.min_days == 0:
            return p
    return products[0] if products else None

async def sync_all_accounts_age_tiers(session: AsyncSession) -> int:
    """
    Проверяет все непроданные аккаунты.
    Если возраст аккаунта изменился и он перешел в диапазон другого тира (например, исполнилось 15 дней),
    автоматически обновляет product_id на соответствующий товар в рамках категории!
    Возвращает количество перенесенных аккаунтов.
    """
    prods_res = await session.execute(
        select(Product).options(selectinload(Product.subcategory))
    )
    all_products = list(prods_res.scalars().all())

    # Group products by category_id (or subcategory_id)
    cat_prods: dict[int, list[Product]] = {}
    for p in all_products:
        if p.min_days is not None:
            cid = p.subcategory.category_id if (p.subcategory and p.subcategory.category_id) else p.subcategory_id
            cat_prods.setdefault(cid, []).append(p)

    if not cat_prods:
        return 0

    accs_res = await session.execute(
        select(Account)
        .options(selectinload(Account.product).selectinload(Product.subcategory))
        .where(Account.is_sold == False, Account.registration_date.isnot(None))
    )
    accounts = list(accs_res.scalars().all())

    today = date.today()
    migrated_count = 0

    for acc in accounts:
        if not acc.registration_date or not acc.product:
            continue
        age_days = (today - acc.registration_date).days
        subcat = acc.product.subcategory
        cid = subcat.category_id if (subcat and subcat.category_id) else acc.product.subcategory_id
        if not cid or cid not in cat_prods:
            continue

        available_prods = cat_prods[cid]
        target_prod = match_product_by_age(available_prods, age_days)

        if target_prod and target_prod.id != acc.product_id:
            acc.product_id = target_prod.id
            migrated_count += 1

    if migrated_count > 0:
        await session.commit()

    return migrated_count

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
            state=acc.get("state", "US"),
            name=acc["name"],
            date_info=acc["date_info"],
            registration_date=acc.get("registration_date"),
            data=acc["data"],
            raw_data=acc.get("raw_data"),
            is_sold=False,
        )
        for acc in accounts_data
    ]

    session.add_all(new_accounts)
    await session.commit()
    return len(new_accounts)

async def add_accounts_auto_age_tiers(
    session: AsyncSession,
    subcategory_id: int,
    accounts_data: list[ParsedAccount]
) -> dict[str, int]:
    """
    Автоматически распределяет аккаунты по товарам-тирам в подкатегории на основе их возраста (отлёжки).
    """
    if not accounts_data:
        return {}

    products = await get_or_create_age_tier_products(session, subcategory_id)
    today = date.today()

    prod_map: dict[int, list[ParsedAccount]] = {}
    stats: dict[str, int] = {}

    for acc in accounts_data:
        reg_date = acc.get("registration_date")
        age_days = (today - reg_date).days if reg_date else 0
        matched_prod = match_product_by_age(products, age_days)
        if not matched_prod:
            matched_prod = products[0]

        prod_map.setdefault(matched_prod.id, []).append(acc)
        stats[matched_prod.name] = stats.get(matched_prod.name, 0) + 1

    new_accounts: list[Account] = []
    for pid, acc_list in prod_map.items():
        for acc in acc_list:
            new_accounts.append(
                Account(
                    product_id=pid,
                    state=acc.get("state", "US"),
                    name=acc["name"],
                    date_info=acc["date_info"],
                    registration_date=acc.get("registration_date"),
                    data=acc["data"],
                    raw_data=acc.get("raw_data"),
                    is_sold=False,
                )
            )

    session.add_all(new_accounts)
    await session.commit()
    return stats

async def delete_accounts_by_list(
    session: AsyncSession,
    text: str,
    product_id: int | None = None
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
                Account.data.ilike(f"{parsed['state']} | {parsed['name']} | {parsed['date_info']}"),
                Account.data.ilike(f"{parsed['name']}, {dot_date}"),
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
        if product_id is not None and product_id > 0:
            conditions.append(Account.product_id == product_id)

        stmt = select(Account.id).where(*conditions).limit(1)
        acc_id = await session.scalar(stmt)
        if acc_id:
            await session.execute(delete(Account).where(Account.id == acc_id))
            deleted_count += 1

    await session.commit()
    return deleted_count
