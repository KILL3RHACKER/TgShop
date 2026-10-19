import os
import hashlib
import logging
import base58
import ecdsa
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)

def generate_tron_wallet() -> tuple[str, str]:
    """
    Генерирует криптографическую пару:
    - Tron base58 адрес (начинается на 'T')
    - 32-байтный hex приватный ключ
    Полностью офлайн, без комиссий и API-запросов.
    """
    private_key_bytes = os.urandom(32)
    sk = ecdsa.SigningKey.from_string(private_key_bytes, curve=ecdsa.SECP256k1)
    vk = sk.get_verifying_key()
    
    public_key_bytes = vk.to_string()
    
    # Keccak-256 (SHA3-256)
    keccak = hashlib.new("sha3_256")
    keccak.update(public_key_bytes)
    address_bytes = b"\x41" + keccak.digest()[-20:]
    
    # Base58Check encoding: SHA256(SHA256(address_bytes))[:4]
    checksum = hashlib.sha256(hashlib.sha256(address_bytes).digest()).digest()[:4]
    tron_address = base58.b58encode(address_bytes + checksum).decode("utf-8")
    
    return tron_address, private_key_bytes.hex()

def is_valid_tron_address(addr: str) -> bool:
    """Проверяет корректность Tron base58 адреса и его контрольной суммы."""
    if not addr or not isinstance(addr, str) or not addr.startswith("T") or len(addr) != 34:
        return False
    try:
        raw = base58.b58decode(addr)
        if len(raw) != 25 or raw[0] != 0x41:
            return False
        payload, checksum = raw[:-4], raw[-4:]
        h = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        return h == checksum
    except Exception:
        return False

def tron_to_hex(addr: str) -> str:
    """Конвертирует base58 Tron адрес в hex (с префиксом 41)."""
    raw = base58.b58decode(addr)
    return raw[:-4].hex()

def hex_to_tron(h: str) -> str:
    """Конвертирует hex адрес в base58 Tron адрес."""
    payload = bytes.fromhex(h)
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode("utf-8")

def sign_tron_tx(tx_id_hex: str, priv_key_hex: str) -> str:
    """
    Подписывает транзакцию Tron приватным ключом secp256k1 с вычислением recovery id.
    Возвращает 65-байтовую сигнатуру в hex.
    """
    h = bytes.fromhex(tx_id_hex)
    sk = ecdsa.SigningKey.from_string(bytes.fromhex(priv_key_hex), curve=ecdsa.SECP256k1)
    sig_str = sk.sign_digest_deterministic(h, hashfunc=hashlib.sha256, sigencode=ecdsa.util.sigencode_string)
    candidates = ecdsa.VerifyingKey.from_public_key_recovery_with_digest(sig_str, h, curve=ecdsa.SECP256k1)
    vk = sk.get_verifying_key()
    v = 0
    for idx, cand in enumerate(candidates):
        if cand.to_string() == vk.to_string():
            v = idx
            break
    return (sig_str + bytes([v])).hex()

def get_tron_base_url() -> str:
    from bot.config import config
    net = (config.tron_network or "mainnet").lower()
    if net == "shasta":
        return "https://api.shasta.trongrid.io"
    elif net == "nile":
        return "https://nile.trongrid.io"
    return "https://api.trongrid.io"

def get_tron_headers() -> dict[str, str]:
    from bot.config import config
    headers = {"Content-Type": "application/json"}
    if config.trongrid_api_key:
        headers["TRON-PRO-API-KEY"] = config.trongrid_api_key
    return headers

async def get_tron_balance(address: str, client: httpx.AsyncClient | None = None) -> dict[str, float]:
    """Возвращает баланс в TRX (native) и USDT (TRC-20) для TRON адреса."""
    if not is_valid_tron_address(address):
        return {"trx": 0.0, "usdt": 0.0}
    
    url = f"{get_tron_base_url()}/wallet/getaccount"
    headers = get_tron_headers()
    payload = {"address": tron_to_hex(address)}
    
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True
        
    try:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            trx_sun = data.get("balance", 0)
            trx_val = float(trx_sun) / 1_000_000.0
            
            usdt_val = 0.0
            for item in data.get("trc20", []):
                for _, val_str in item.items():
                    try:
                        usdt_val += float(val_str) / 1_000_000.0
                    except Exception:
                        pass
            return {"trx": trx_val, "usdt": usdt_val}
        return {"trx": 0.0, "usdt": 0.0}
    except Exception as e:
        logger.warning(f"Failed to fetch Tron balance for {address}: {e}")
        return {"trx": 0.0, "usdt": 0.0}
    finally:
        if close_client:
            await client.aclose()

async def sweep_wallet(
    from_address: str,
    from_private_key: str,
    to_address: str,
    client: httpx.AsyncClient | None = None
) -> tuple[bool, str]:
    """
    Переводит доступный баланс TRX с кошелька юзера на мастер-кошелек.
    shortcut: on-chain sweep переводит native TRX за вычетом 1 TRX комиссии; TRC-20 требует энергии
    """
    if not is_valid_tron_address(from_address) or not is_valid_tron_address(to_address):
        return False, "Некорректный адрес кошелька Tron"

    if from_address == to_address:
        return False, "Адрес отправителя и получателя совпадают"

    balances = await get_tron_balance(from_address, client=client)
    trx_bal = balances.get("trx", 0.0)
    
    FEE_RESERVE_SUN = 1_000_000  # 1 TRX reserve
    total_sun = int(trx_bal * 1_000_000)
    send_sun = total_sun - FEE_RESERVE_SUN
    
    if send_sun <= 0:
        return False, f"Баланс {trx_bal:.3f} TRX недостаточен для перевода (требуется > 1.0 TRX)"

    base_url = get_tron_base_url()
    headers = get_tron_headers()
    
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True

    try:
        create_resp = await client.post(
            f"{base_url}/wallet/createtransaction",
            json={
                "to_address": tron_to_hex(to_address),
                "owner_address": tron_to_hex(from_address),
                "amount": send_sun
            },
            headers=headers
        )
        if create_resp.status_code != 200:
            return False, f"HTTP {create_resp.status_code}"
        
        tx_data = create_resp.json()
        if "Error" in tx_data:
            return False, f"Ошибка TronGrid: {tx_data['Error']}"
        if "txID" not in tx_data:
            return False, f"Не удалось создать транзакцию: {tx_data}"
        
        sig = sign_tron_tx(tx_data["txID"], from_private_key)
        
        broadcast_payload = {
            "txID": tx_data["txID"],
            "raw_data": tx_data.get("raw_data"),
            "raw_data_hex": tx_data.get("raw_data_hex"),
            "signature": [sig]
        }
        b_resp = await client.post(
            f"{base_url}/wallet/broadcasttransaction",
            json=broadcast_payload,
            headers=headers
        )
        b_data = b_resp.json()
        if b_data.get("result") is True:
            tx_id = tx_data["txID"]
            logger.info(f"Sweep successful from {from_address} to {to_address}: {tx_id}")
            return True, tx_id
        else:
            code = b_data.get("code", "ERROR")
            msg = b_data.get("message", "Транзакция отклонена сетью")
            try:
                msg = bytes.fromhex(msg).decode("utf-8")
            except Exception:
                pass
            return False, f"Ошибка сети Tron: {code} - {msg}"
    except Exception as e:
        logger.error(f"Sweep exception: {e}")
        return False, str(e)
    finally:
        if close_client:
            await client.aclose()

async def send_tron_transaction(
    from_address: str,
    from_private_key: str,
    to_address: str,
    amount_trx: float,
    client: httpx.AsyncClient | None = None
) -> tuple[bool, str]:
    """
    Отправляет TRX с мастер-кошелька на адрес получателя.
    shortcut: вывод выполняется в native TRX через TronGrid API
    """
    if not is_valid_tron_address(from_address) or not is_valid_tron_address(to_address):
        return False, "Некорректный адрес кошелька Tron"

    send_sun = int(amount_trx * 1_000_000)
    if send_sun <= 0:
        return False, "Сумма перевода должна быть больше 0"

    base_url = get_tron_base_url()
    headers = get_tron_headers()
    
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True

    try:
        create_resp = await client.post(
            f"{base_url}/wallet/createtransaction",
            json={
                "to_address": tron_to_hex(to_address),
                "owner_address": tron_to_hex(from_address),
                "amount": send_sun
            },
            headers=headers
        )
        if create_resp.status_code != 200:
            return False, f"HTTP {create_resp.status_code}"
        
        tx_data = create_resp.json()
        if "Error" in tx_data:
            return False, f"Ошибка TronGrid: {tx_data['Error']}"
        if "txID" not in tx_data:
            return False, f"Ошибка создания: {tx_data}"
        
        sig = sign_tron_tx(tx_data["txID"], from_private_key)
        
        broadcast_payload = {
            "txID": tx_data["txID"],
            "raw_data": tx_data.get("raw_data"),
            "raw_data_hex": tx_data.get("raw_data_hex"),
            "signature": [sig]
        }
        b_resp = await client.post(
            f"{base_url}/wallet/broadcasttransaction",
            json=broadcast_payload,
            headers=headers
        )
        b_data = b_resp.json()
        if b_data.get("result") is True:
            return True, tx_data["txID"]
        else:
            msg = b_data.get("message", "Отказ сети Tron")
            try:
                msg = bytes.fromhex(msg).decode("utf-8")
            except Exception:
                pass
            return False, f"Ошибка Tron: {msg}"
    except Exception as e:
        return False, str(e)
    finally:
        if close_client:
            await client.aclose()

async def sweep_all_wallets(
    session: AsyncSession,
    destination_address: str | None = None
) -> dict[str, any]:
    """
    Проверяет все пользовательские кошельки в БД и переводит найденный баланс на целевой адрес.
    """
    from bot.config import config
    from bot.database.models import User
    
    target_addr = destination_address or config.admin_tron_address
    if not target_addr:
        admin_user = await session.scalar(select(User).where(User.tg_id == config.admin_id))
        if admin_user:
            target_addr = admin_user.tron_address

    if not target_addr or not is_valid_tron_address(target_addr):
        return {
            "success": False,
            "message": "Целевой адрес администратора (ADMIN_TRON_ADDRESS) не настроен или некорректен",
            "swept_count": 0,
            "total_trx": 0.0
        }

    stmt = select(User).where(
        User.tron_address.is_not(None),
        User.tron_private_key.is_not(None),
        User.tron_address != target_addr
    )
    result = await session.execute(stmt)
    users = list(result.scalars().all())

    swept_count = 0
    total_swept = 0.0
    tx_list = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        for u in users:
            bals = await get_tron_balance(u.tron_address, client=client)
            if bals.get("trx", 0.0) > 1.0:
                ok, tx_or_err = await sweep_wallet(
                    u.tron_address,
                    u.tron_private_key,
                    target_addr,
                    client=client
                )
                if ok:
                    swept_count += 1
                    amount = bals["trx"] - 1.0
                    total_swept += amount
                    tx_list.append({"user_id": u.tg_id, "tx": tx_or_err, "amount": amount})

    return {
        "success": True,
        "checked_count": len(users),
        "swept_count": swept_count,
        "total_trx": total_swept,
        "transactions": tx_list,
        "destination": target_addr
    }
