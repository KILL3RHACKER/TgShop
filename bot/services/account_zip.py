import io
import json
import zipfile
from datetime import datetime
from bot.database.models import Order, Account, Product

def generate_account_zip(order: Order, account: Account, product: Product) -> tuple[bytes, str]:
    """
    Генерирует ZIP-архив с данными купленного аккаунта:
    - account.txt: ST | NAME SURNAME | DD.MM
    - full_data.txt: полный сырой лог (при наличии)
    - cookies.json: форматированные cookies (если найдены в raw_data)
    - WARRANTY_MEMO.txt: памятка о 24-часовой гарантии, правила диспутов и регламент SLA.
    
    Возвращает: (zip_bytes, filename)
    """
    zip_buffer = io.BytesIO()
    clean_order_code = order.order_code.replace("#", "").replace("/", "_")
    zip_filename = f"{clean_order_code}.zip"

    created_dt = order.created_at or datetime.utcnow()
    hold_dt = order.hold_until or (datetime.utcnow() + timedelta(hours=24))

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Основные данные аккаунта
        account_txt = (
            f"Товар: {product.name}\n"
            f"Формат: {account.data}\n"
            f"Штат (ST): {account.state}\n"
            f"ФИО: {account.name}\n"
            f"Дата регистрации: {account.date_info}\n"
            f"ID Заказа: {order.order_code}\n"
            f"Дата покупки: {created_dt.strftime('%d.%m.%Y %H:%M UTC')}\n"
        )
        zf.writestr("account.txt", account_txt)

        # 2. Сырые данные / логин / пароль / куки
        raw_content = account.raw_data or account.data
        zf.writestr("full_data.txt", raw_content)

        # 3. Сохранение cookies в отдельный файл
        cookie_saved = False
        if account.cookie_data:
            cdata_str = account.cookie_data.strip()
            try:
                if cdata_str.startswith(("[", "{")):
                    parsed_cookies = json.loads(cdata_str)
                    zf.writestr("cookies.json", json.dumps(parsed_cookies, indent=2, ensure_ascii=False))
                else:
                    zf.writestr("cookies.txt", account.cookie_data)
                cookie_saved = True
            except Exception:
                zf.writestr("cookies.txt", account.cookie_data)
                cookie_saved = True

        if not cookie_saved:
            if "Cookies:" in raw_content:
                try:
                    cookies_part = raw_content.split("Cookies:", 1)[1].strip()
                    first_bracket = cookies_part.find("[")
                    last_bracket = cookies_part.rfind("]")
                    if first_bracket != -1 and last_bracket != -1:
                        json_str = cookies_part[first_bracket:last_bracket+1]
                        parsed_cookies = json.loads(json_str)
                        zf.writestr("cookies.json", json.dumps(parsed_cookies, indent=2, ensure_ascii=False))
                    else:
                        zf.writestr("cookies.txt", cookies_part[:100000])
                except Exception:
                    pass
            elif raw_content.strip().startswith("[") and raw_content.strip().endswith("]"):
                try:
                    parsed_cookies = json.loads(raw_content)
                    zf.writestr("cookies.json", json.dumps(parsed_cookies, indent=2, ensure_ascii=False))
                except Exception:
                    pass

        # 4. Памятка покупателю (Kraken-стандарт)
        hold_time_str = hold_dt.strftime("%d.%m.%Y %H:%M UTC")
        memo_txt = (
            "=====================================================\n"
            f"ПАМЯТКА ПОКУПАТЕЛЯ ПО ЗАКАЗУ {order.order_code}\n"
            "=====================================================\n\n"
            "1. СРОК ГАРАНТИИ И ПРОВЕРКИ (ХОЛД) — 24 ЧАСА:\n"
            f"   Действует до: {hold_time_str}\n"
            "   Пожалуйста, проверьте аккаунт в течение 24 часов.\n"
            "   Если аккаунт нерабочий и вы обратились после 24 часов,\n"
            "   средства возвращены НЕ будут (холд закрывается автоматически).\n\n"
            "2. ВОЗНИКЛИ ПРОБЛЕМЫ ИЛИ ВОПРОСЫ:\n"
            "   - «⚠️ Открыть диспут»: используйте ТОЛЬКО при неработоспособности\n"
            "     аккаунта (невалид, блок до покупки). Холд замораживается.\n"
            "     Обязательно приложите скриншот/видео доказательства.\n"
            "   - «❓ Задать вопрос»: используйте для консультаций по входу,\n"
            "     прокси, настройкам и общим вопросам.\n\n"
            "3. РЕГЛАМЕНТ ОТВЕТА В ДИСПУТЕ (SLA — 24 ЧАСА):\n"
            "   - У каждой стороны есть ровно 24 часа на ответ.\n"
            "   - Если покупатель не отвечает 24 часа -> диспут закрывается\n"
            "     в пользу магазина, возврат отклоняется.\n"
            "   - Если магазин не отвечает 24 часа -> средства автоматически\n"
            "     возвращаются на баланс покупателя.\n\n"
            "4. ВЗАИМНОЕ УВАЖЕНИЕ:\n"
            "   Запрещены оскорбления, мат, угрозы и попытки обмана.\n"
            "   Нарушение правил ведет к закрытию спора и блокировке.\n"
            "=====================================================\n"
        )
        zf.writestr("WARRANTY_MEMO.txt", memo_txt)

    return zip_buffer.getvalue(), zip_filename
