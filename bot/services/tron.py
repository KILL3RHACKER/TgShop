import os
import hashlib
import ecdsa
import base58

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
