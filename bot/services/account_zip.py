import io
import re
import zipfile
from bot.database.models import Order, Account, Product

def generate_account_zip(order: Order, account: Account, product: Product) -> tuple[bytes, str]:
    """
    Генерирует ZIP-архив с данными купленного аккаунта.
    Требования:
    - Название архива = названию товара/аккаунта (а именно: TN | ROSE OGBURN | 07.10.zip)
    - В архиве строго два файла: cookies.txt и info.txt (и ничего больше)
    
    Возвращает: (zip_bytes, filename)
    """
    zip_buffer = io.BytesIO()

    # Название архива при выдаче = названию конкретного товара/аккаунта (ST | NAME | DD.MM)
    acc_title = ""
    if account and account.data and account.data.strip():
        acc_title = account.data.strip()
    elif order and order.account_data and order.account_data.strip():
        acc_title = order.account_data.strip()
    elif product and product.name:
        acc_title = product.name.strip()
    else:
        acc_title = "Account"

    # Очищаем недопустимые символы слэшей пути, сохраняя вертикальную черту и данные
    safe_name = acc_title.replace("/", " ").replace("\\", " ").strip()
    zip_filename = f"{safe_name}.zip"

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. cookies.txt
        cookie_content = ""
        if account.cookie_data and account.cookie_data.strip():
            cookie_content = account.cookie_data.strip()
        elif account.raw_data and "Cookies:" in account.raw_data:
            try:
                cookie_content = account.raw_data.split("Cookies:", 1)[1].strip()
            except Exception:
                pass
        elif account.raw_data and account.raw_data.strip().startswith(("[", "{")):
            cookie_content = account.raw_data.strip()

        zf.writestr("cookies.txt", cookie_content)

        # 2. info.txt
        parts = []
        if account.data:
            parts.append(account.data)
        if account.raw_data and account.raw_data.strip() != account.data.strip():
            parts.append(account.raw_data.strip())
        info_content = "\n\n".join(parts) if parts else (account.data or "")
        zf.writestr("info.txt", info_content)

    return zip_buffer.getvalue(), zip_filename
