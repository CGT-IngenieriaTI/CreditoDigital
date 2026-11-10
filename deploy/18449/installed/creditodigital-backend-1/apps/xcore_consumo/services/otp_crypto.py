import base64
import os

from django.conf import settings


class OTPCryptoError(Exception):
    pass


def _load_key() -> bytes:
    key_b64 = str(getattr(settings, "OTP_AES_KEY_B64", "") or os.getenv("OTP_AES_KEY_B64", "")).strip()
    if not key_b64:
        raise OTPCryptoError("Falta OTP_AES_KEY_B64 para cifrado OTP.")
    try:
        key = base64.b64decode(key_b64)
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError("OTP_AES_KEY_B64 no es base64 válido.") from exc
    if len(key) != 32:
        raise OTPCryptoError("OTP_AES_KEY_B64 debe decodificar a 32 bytes (AES-256).")
    return key


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError("La dependencia 'cryptography' no está disponible en el entorno.") from exc
    return AESGCM(_load_key())


def encrypt_text(plain_text: str) -> str:
    nonce = os.urandom(12)
    cipher = _aesgcm().encrypt(nonce, (plain_text or "").encode("utf-8"), None)
    return base64.b64encode(nonce + cipher).decode("ascii")


def decrypt_text(token_b64: str) -> str:
    try:
        token = base64.b64decode((token_b64 or "").encode("ascii"))
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError("Ciphertext OTP inválido (base64).") from exc
    if len(token) < 13:
        raise OTPCryptoError("Ciphertext OTP inválido.")
    nonce, cipher = token[:12], token[12:]
    try:
        plain = _aesgcm().decrypt(nonce, cipher, None)
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError("No fue posible descifrar OTP.") from exc
    return plain.decode("utf-8")

