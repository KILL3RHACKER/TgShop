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

class ParsedAccount(TypedDict, total=False):
    state: str
    name: str
    date_info: str
    data: str
    raw_data: str
    registration_date: date | None
    cookie_data: str | None

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
        "name": "7d",
        "min_days": 7,
        "max_days": 14,
        "price": 10.0,
        "description": "Отлёжка 7-14 полных дней."
    },
    {
        "name": "15d",
        "min_days": 15,
        "max_days": 29,
        "price": 15.0,
        "description": "Отлёжка 15-29 полных дней."
    },
    {
        "name": "30d",
        "min_days": 30,
        "max_days": None,
        "price": 20.0,
        "description": "Отлёжка от 30 дней и старше."
    },
    {
        "name": "<7d",
        "min_days": 0,
        "max_days": 6,
        "price": 5.0,
        "description": "Свежая регистрация до 7 дней."
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
    if not line or len(line) < 3 or len(line) > 300 or line.startswith(("#", "//", "/*", "=", "-", "_", "[", "{")):
        return None

    # Skip lines that look like log labels or JSON or separators
    lower = line.lower()
    if any(lower.startswith(k) for k in [
        "ssn:", "ssn ", "address:", "account number", "routing",
        "saving", "checking", "balance:", "email:", "password:",
        "phone:", "cookie:", "domain:", "host:", "user-agent:"
    ]):
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
    if not clean_name or len(clean_name) > 80:
        return None

    # Ensure no JSON or forbidden code characters in name
    if any(ch in clean_name for ch in "{}[]()<>=/\\@:;\""):
        return None

    return state, clean_name, date_str, reg_date

def parse_account_line(line: str) -> ParsedAccount | None:
    raw = line.strip()
    if not raw or len(raw) < 5 or raw.startswith(("#", "//", "/*", "=", "-", "_", "[", "{")):
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

    return None

def parse_account_from_log_block(
    block: str,
    fallback_name: str = "",
    allow_fallback_name: bool = True
) -> ParsedAccount | None:
    """
    Парсит отдельный блок лога банковского аккаунта (содержит Name:, Address:, DOB:, cookies и т.д.).
    Возвращает ParsedAccount, где raw_data содержит ПОЛНЫЙ текст блока.
    """
    b = block.strip()
    if not b or len(b) < 15:
        return None

    # Имя клиента
    m_name = re.search(r'(?:Full\s*Name|Name|Client|Owner|Holder|Customer|User)[:\s]+([^\r\n]+)', b, re.IGNORECASE)
    name: str = ""
    if m_name:
        cand_name = m_name.group(1).strip()
        cand_name = re.sub(r'["\';]', '', cand_name).strip()
        cand_name = " ".join(cand_name.split()).upper()
        if cand_name and len(cand_name) <= 80 and not any(ch in cand_name for ch in "{}[]()<>=/\\@:;"):
            name = cand_name

    if not name and allow_fallback_name and fallback_name:
        # Разрешаем fallback_name только если в блоке есть признаки персональных данных (SSN, Address, DOB)
        has_identity_signals = bool(re.search(r'(?:SSN|Address|DOB|Date|Zip|State)[:\s]', b, re.IGNORECASE))
        if has_identity_signals:
            clean_fb = os.path.basename(fallback_name).replace("_", " ").strip()
            # Очищаем дату или номер лога с конца имени архива/папки (например, "CURTIS PURVIS 06.10 13" -> "CURTIS PURVIS")
            clean_fb = re.sub(r'\s*\d{1,2}[./-]\d{1,2}.*$', '', clean_fb).strip()
            if clean_fb and len(clean_fb) <= 80 and not clean_fb.lower().endswith((".txt", ".log", ".json")):
                name = clean_fb.upper()

    if not name:
        return None

    # Штат из адреса
    state = extract_state_from_address_line(b) or "US"

    # Дата (Open Date / Reg Date / Date / Created / DOB)
    date_info: str = ""
    reg_date: date | None = None

    m_date = re.search(r'(?:Open\s*Date|Reg\s*Date|Created|Date)[:\s]+([^\r\n]+)', b, re.IGNORECASE)
    if m_date:
        d_cand = m_date.group(1).strip()
        ext = extract_state_name_date(f"DUMMY {d_cand}")
        if ext:
            _, _, date_info, reg_date = ext

    if not date_info:
        m_dob = re.search(r'(?:DOB|Date\s*of\s*Birth|Birth\s*Date)[:\s]+([^\r\n]+)', b, re.IGNORECASE)
        if m_dob:
            d_cand = m_dob.group(1).strip()
            ext = extract_state_name_date(f"DUMMY {d_cand}")
            if ext:
                _, _, date_info, reg_date = ext

    if not date_info:
        today = date.today()
        date_info = today.strftime("%d.%m")
        reg_date = today

    normalized_data = f"{state} | {name} | {date_info}"
    return {
        "state": state,
        "name": name[:100],
        "date_info": date_info,
        "data": normalized_data,
        "raw_data": b,
        "registration_date": reg_date,
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
    return parse_accounts_from_archive("upload.zip", zip_bytes)

def parse_accounts_from_rar(rar_bytes: bytes) -> tuple[list[ParsedAccount], int]:
    return parse_accounts_from_archive("upload.rar", rar_bytes)

def extract_archive_recursively(root_dir: str, max_depth: int = 5) -> None:
    """
    Рекурсивно распаковывает любые вложенные .zip и .rar архивы внутри каталога root_dir.
    Поддерживает произвольную глубину вложенности (архивы в архивах).
    """
    for _ in range(max_depth):
        nested_archives: list[str] = []
        for dirpath, _, filenames in os.walk(root_dir):
            if "__MACOSX" in dirpath:
                continue
            for fn in filenames:
                fn_lower = fn.lower()
                if fn_lower.endswith(".zip") or fn_lower.endswith(".rar"):
                    nested_archives.append(os.path.join(dirpath, fn))

        if not nested_archives:
            break

        for arch_path in nested_archives:
            arch_lower = arch_path.lower()
            target_dir = os.path.splitext(arch_path)[0] + "_extracted"
            os.makedirs(target_dir, exist_ok=True)

            try:
                if arch_lower.endswith(".zip"):
                    with zipfile.ZipFile(arch_path) as zf:
                        zf.extractall(target_dir)
                elif arch_lower.endswith(".rar"):
                    subprocess.run(
                        ["unrar", "x", "-f", arch_path, target_dir],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=30,
                        check=False,
                    )
            except Exception:
                pass
            finally:
                try:
                    os.remove(arch_path)
                except Exception:
                    pass

def find_cookie_for_account_file(fpath: str) -> str | None:
    """
    Ищет companion cookie файл для аккаунта:
    1. Другой файл в той же папке (с именем cookie*, *.json, *.cookie, netscape или единственный второй файл)
    2. Файл с совпадающим префиксом имени (например: acc1_cookie.json)
    """
    dir_name = os.path.dirname(fpath)
    base_no_ext = os.path.splitext(os.path.basename(fpath))[0].lower()

    try:
        entries = os.listdir(dir_name)
    except Exception:
        entries = []

    candidates: list[str] = []
    for fn in entries:
        full_p = os.path.join(dir_name, fn)
        if not os.path.isfile(full_p) or full_p == fpath:
            continue
        fn_l = fn.lower()
        if "cookie" in fn_l or fn_l.endswith((".json", ".cookie", ".netscape")):
            candidates.insert(0, full_p)
        elif fn_l.endswith((".txt", ".log")) and base_no_ext in fn_l:
            candidates.append(full_p)
        elif len(entries) <= 4 and fn_l.endswith((".txt", ".json", ".log", ".csv")):
            candidates.append(full_p)

    for cand in candidates:
        try:
            with open(cand, "rb") as cf:
                cdata = cf.read()
            if cdata and len(cdata) > 2:
                for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
                    try:
                        return cdata.decode(enc)
                    except UnicodeDecodeError:
                        continue
        except Exception:
            continue
    return None

def parse_accounts_from_extracted_dir(root_dir: str) -> tuple[list[ParsedAccount], int]:
    results: list[ParsedAccount] = []
    seen = set()
    files_processed = 0

    txt_files: list[str] = []
    for dirpath, _, filenames in os.walk(root_dir):
        if "__MACOSX" in dirpath:
            continue
        for fn in filenames:
            fn_lower = fn.lower()
            if fn_lower.endswith((".txt", ".log", ".json", ".csv")):
                txt_files.append(os.path.join(dirpath, fn))

    for fpath in txt_files:
        fn_base = os.path.basename(fpath).lower()
        folder_name = os.path.basename(os.path.dirname(fpath))

        try:
            with open(fpath, "rb") as f:
                data = f.read()
        except Exception:
            continue

        if not data or len(data) < 5:
            continue

        text = None
        for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if not text:
            continue

        files_processed += 1

        # 1. Проверяем, сколько клиентов в файле (дамп нескольких аккаунтов или один аккаунт)
        name_matches = re.findall(r'(?:Full\s*Name|Name|Client|Owner|Holder|Customer)[:\s]+[A-Za-zА-Яа-я]', text, re.IGNORECASE)
        has_multiple_customers = len(name_matches) > 1

        companion_cookie = find_cookie_for_account_file(fpath)

        sep_pattern = re.compile(r'\n\s*={5,}\s*\n|\n\s*-{5,}\s*\n|\n\s*_{5,}\s*\n')
        if sep_pattern.search(text) and has_multiple_customers:
            blocks = [b.strip() for b in sep_pattern.split(text) if b.strip()]
            for block in blocks:
                # В дампах с несколькими аккаунтами каждый блок обязан иметь реальное имя клиента
                acc = parse_account_from_log_block(block, allow_fallback_name=False)
                if acc:
                    if companion_cookie and not acc.get("cookie_data"):
                        acc["cookie_data"] = companion_cookie
                    key = acc["data"].upper()
                    if key not in seen:
                        seen.add(key)
                        results.append(acc)
            continue

        # 2. Если в файле один клиент (или один лог с разделителями) — это ОДИН аккаунт целиком!
        if re.search(r'(?:Full\s*Name|Name|Client|Owner|Holder)[:\s]+[A-Za-zА-Яа-я]', text, re.IGNORECASE):
            acc = parse_account_from_log_block(text, fallback_name=folder_name or fn_base, allow_fallback_name=True)
            if acc:
                if companion_cookie and not acc.get("cookie_data"):
                    acc["cookie_data"] = companion_cookie
                key = acc["data"].upper()
                if key not in seen:
                    seen.add(key)
                    results.append(acc)
            continue

        # 3. Файл info.txt
        if fn_base == "info.txt":
            acc = parse_account_from_info_txt(text, context_name=folder_name)
            if acc:
                if companion_cookie and not acc.get("cookie_data"):
                    acc["cookie_data"] = companion_cookie
                key = acc["data"].upper()
                if key not in seen:
                    seen.add(key)
                    results.append(acc)
            continue

        # 4. Пропускаем чисто JSON-файлы (куки) от построчного парсинга
        if fn_base.endswith(".json") or text.strip().startswith(("{", "[")):
            continue

        # 5. Обычный построчный парсинг списка строк аккаунтов
        for line in text.splitlines():
            line_str = line.strip()
            if not line_str or len(line_str) < 5:
                continue
            parsed = parse_account_line(line_str)
            if parsed:
                if companion_cookie and not parsed.get("cookie_data"):
                    parsed["cookie_data"] = companion_cookie
                key = parsed["data"].upper()
                if key not in seen:
                    seen.add(key)
                    results.append(parsed)

    return results, files_processed

def parse_accounts_from_archive(filename: str, archive_bytes: bytes) -> tuple[list[ParsedAccount], int]:
    fn_lower = filename.lower()
    if not (fn_lower.endswith(".zip") or fn_lower.endswith(".rar")):
        raise ValueError("Поддерживаются только архивы .zip и .rar")

    with tempfile.TemporaryDirectory() as tmpdir:
        ext = os.path.splitext(filename)[1].lower()
        init_arch = os.path.join(tmpdir, "upload" + ext)
        with open(init_arch, "wb") as f:
            f.write(archive_bytes)

        if fn_lower.endswith(".zip"):
            try:
                with zipfile.ZipFile(init_arch) as zf:
                    zf.extractall(tmpdir)
            except Exception:
                pass
        elif fn_lower.endswith(".rar"):
            try:
                subprocess.run(
                    ["unrar", "x", "-f", init_arch, tmpdir],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
            except Exception:
                pass

        try:
            os.remove(init_arch)
        except Exception:
            pass

        # Рекурсивная распаковка всех вложенных архивов (архивы в архивах)
        extract_archive_recursively(tmpdir)

        # Парсинг файлов из распакованного дерева
        return parse_accounts_from_extracted_dir(tmpdir)

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
            cookie_data=acc.get("cookie_data"),
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
                    cookie_data=acc.get("cookie_data"),
                    is_sold=False,
                )
            )

    session.add_all(new_accounts)
    await session.commit()
    return stats

async def get_or_create_age_tier_products_for_category(
    session: AsyncSession,
    category_id: int
) -> list[Product]:
    """
    Получает все товары в категории (по всем подкатегориям).
    Если товаров нет, создает линейку подкатегорий (Truist 7d, Truist 15d, Truist 30d) и товары внутри них.
    """
    cat = await session.get(Category, category_id)
    cat_name = cat.name.strip() if cat else "Банк"

    prods_res = await session.execute(
        select(Product)
        .join(Subcategory, Product.subcategory_id == Subcategory.id)
        .where(Subcategory.category_id == category_id)
        .order_by(Product.min_days.asc().nullslast(), Product.id.asc())
    )
    products = list(prods_res.scalars().all())

    # Если уже есть товары с заданными min_days
    has_tier = any(p.min_days is not None for p in products)
    if has_tier:
        return products

    if products:
        return products

    # Создаем подкатегории и товары для каждого тира
    created = []
    for tier in DEFAULT_AGE_TIERS:
        subcat_title = f"{cat_name} {tier['name']}"
        sub_res = await session.execute(
            select(Subcategory).where(
                Subcategory.category_id == category_id,
                Subcategory.name == subcat_title
            ).limit(1)
        )
        subcat = sub_res.scalar_one_or_none()
        if not subcat:
            subcat = Subcategory(
                category_id=category_id,
                name=subcat_title,
            )
            session.add(subcat)
            await session.commit()
            await session.refresh(subcat)

        p = Product(
            subcategory_id=subcat.id,
            name=subcat_title,
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

async def add_accounts_to_category_auto_age(
    session: AsyncSession,
    category_id: int,
    accounts_data: list[ParsedAccount]
) -> dict[str, int]:
    """
    Распределяет аккаунты по товарам-тирам в рамках указанной Категории.
    """
    if not accounts_data:
        return {}

    products = await get_or_create_age_tier_products_for_category(session, category_id)
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
                    name=acc["name"][:250],
                    date_info=acc["date_info"][:30],
                    registration_date=acc.get("registration_date"),
                    data=acc["data"],
                    raw_data=acc.get("raw_data"),
                    cookie_data=acc.get("cookie_data"),
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
