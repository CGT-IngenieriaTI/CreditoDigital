"""Validadores existentes de titular compartidos, sin dependencias de proveedores."""
import re
from rest_framework import serializers

APELLIDO_REGEX = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]+$")

def _validate_primer_apellido(value: str) -> str:

    normalized = " ".join((value or "").strip().upper().split())
    if not normalized:
        raise serializers.ValidationError("El primer apellido es obligatorio.")
    if not APELLIDO_REGEX.fullmatch(normalized):
        raise serializers.ValidationError("El primer apellido solo permite letras y espacios.")
    return normalized


def _normalize_colombia_mobile(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("57") and len(digits) == 12:
        digits = digits[2:]
    if re.fullmatch(r"3\d{9}", digits):
        return digits
    raise serializers.ValidationError("El celular debe ser un n?mero m?vil colombiano v?lido.")
