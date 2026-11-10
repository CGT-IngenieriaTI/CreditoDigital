"""Integridad del consentimiento existente, sin proveedor ni orquestacion OTP."""
import hashlib


def build_consent_hash(version: str, text_snapshot: str, transaction_uuid: str = "", channel: str = "", verified_at=None) -> str:
    verified_value = verified_at.isoformat() if verified_at else ""
    source = f"{version}|{text_snapshot}|{transaction_uuid}|{channel}|{verified_value}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
