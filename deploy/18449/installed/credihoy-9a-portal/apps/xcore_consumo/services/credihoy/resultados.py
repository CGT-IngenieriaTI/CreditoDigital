"""Resultados de elegibilidad limitada, nunca autorizaciones crediticias."""

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any


class EstadoEvaluacion(str, Enum):
    ELEGIBLE = "ELEGIBLE"
    NO_ELEGIBLE = "NO_ELEGIBLE"
    REVISION_MANUAL = "REVISION_MANUAL"
    INFORMACION_INSUFICIENTE = "INFORMACION_INSUFICIENTE"


class EstadoRegla(str, Enum):
    CUMPLE = "CUMPLE"
    NO_CUMPLE = "NO_CUMPLE"
    NO_APLICA = "NO_APLICA"
    INFORMACION_INSUFICIENTE = "INFORMACION_INSUFICIENTE"
    REVISION_MANUAL = "REVISION_MANUAL"
    PENDIENTE_POLITICA = "PENDIENTE_POLITICA"


@dataclass(frozen=True, kw_only=True)
class ResultadoRegla:
    codigo: str
    cumple: bool | None
    estado: EstadoRegla
    motivo: str
    valor_evaluado: dict[str, Any]
    parametro: dict[str, Any]
    fuente: tuple[str, ...]
    version_regla: str


def _serializable(valor):
    if isinstance(valor, Enum):
        return valor.value
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, date):
        return valor.isoformat()
    if is_dataclass(valor):
        return {campo.name: _serializable(getattr(valor, campo.name)) for campo in fields(valor)}
    if isinstance(valor, dict):
        return {clave: _serializable(item) for clave, item in valor.items()}
    if isinstance(valor, (tuple, list)):
        return [_serializable(item) for item in valor]
    return valor


@dataclass(frozen=True, kw_only=True)
class ResultadoEvaluacion:
    estado_global: EstadoEvaluacion
    reglas: tuple[ResultadoRegla, ...]
    version_politica: str
    requiere_revision_manual: bool
    motivos: tuple[str, ...]
    fecha_evaluacion: date
    alcance: str = field(default="REGLAS_IMPLEMENTADAS_NO_APROBACION_CREDITICIA", init=False)
    autorizado_decision_crediticia: bool = field(default=False, init=False)
    reglas_pendientes: tuple[str, ...] = field(default=(
        "CH06_EXCEPCION_MORA", "CAPACIDAD_COMPLETA", "RIESGO_COMPLETO",
    ), init=False)

    def a_dict(self) -> dict[str, Any]:
        """Dinero como string decimal; no incluye el contexto ni identificaciones."""
        return _serializable(self)
