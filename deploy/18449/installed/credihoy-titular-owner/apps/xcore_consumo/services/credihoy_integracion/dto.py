"""Contrato de transporte conocido y DTO de bloques, sin dependencias de Django."""

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping


VERSION_CONTRATO = "credihoy-hechos-v1"


class CodigoErrorApiData(str, Enum):
    TIMEOUT = "API_DATA_TIMEOUT"
    NO_AUTORIZADO = "API_DATA_NO_AUTORIZADO"
    RESPUESTA_INVALIDA = "API_DATA_RESPUESTA_INVALIDA"
    VERSION_NO_SOPORTADA = "API_DATA_VERSION_NO_SOPORTADA"
    ERROR_TEMPORAL = "API_DATA_ERROR_TEMPORAL"
    REQUEST_INVALIDO = "REQUEST_INVALIDO"
    HTTP_INVALIDO = "HTTP_INVALIDO"
    ASOCIADO_NO_DISPONIBLE = "ASOCIADO_NO_DISPONIBLE"
    INFORMACION_NO_DISPONIBLE = "INFORMACION_NO_DISPONIBLE"
    INFORMACION_INCONSISTENTE = "INFORMACION_INCONSISTENTE"
    ERROR_TEMPORAL_FUENTE = "ERROR_TEMPORAL"


class ApiDataCrediHoyError(Exception):
    def __init__(self, codigo: CodigoErrorApiData):
        self.codigo = codigo
        super().__init__(codigo.value)


def invalida():
    raise ApiDataCrediHoyError(CodigoErrorApiData.RESPUESTA_INVALIDA)


def objeto(valor):
    if not isinstance(valor, dict):
        invalida()
    return valor


def booleano(valor):
    if type(valor) is not bool:
        invalida()
    return valor


def texto(valor, *, nullable=False):
    if nullable and valor is None:
        return None
    if not isinstance(valor, str) or not valor.strip():
        invalida()
    return valor


def decimal_exacto(valor):
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (str, int, Decimal)):
        invalida()
    try:
        numero = Decimal(valor)
    except (InvalidOperation, ValueError):
        invalida()
    if not numero.is_finite():
        invalida()
    return numero


def entero_nullable(valor):
    if valor is None:
        return None
    if type(valor) is not int:
        invalida()
    return valor


def fecha_nullable(valor):
    if valor is None:
        return None
    if not isinstance(valor, str) or len(valor) != 10:
        invalida()
    try:
        return date.fromisoformat(valor)
    except ValueError:
        invalida()


def fecha_hora(valor, *, sin_fracciones=False):
    patron = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    if not sin_fracciones:
        patron += r"(?:\.[0-9]+)?"
    patron += r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
    if not isinstance(valor, str) or not re.fullmatch(patron, valor):
        invalida()
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        invalida()


@dataclass(frozen=True, kw_only=True)
class RespuestaApiDataDTO:
    version_contrato: str
    habilitado_decision_productiva: bool
    request_id: str
    fecha_consulta: datetime
    fecha_evaluacion: datetime
    periodo_anterior: Mapping[str, Any] = field(repr=False)
    cartera_interna: Mapping[str, Any] = field(repr=False)
    cuota_vigente_interna: Mapping[str, Any] = field(repr=False)
    aportes_sociales: Mapping[str, Any] = field(repr=False)

    @classmethod
    def desde_json(cls, payload, *, request_id, fecha_evaluacion=None):
        payload = objeto(payload)
        version = texto(payload.get("versionContrato"))
        if version != VERSION_CONTRATO:
            raise ApiDataCrediHoyError(CodigoErrorApiData.VERSION_NO_SOPORTADA)
        habilitado = booleano(payload.get("habilitadoDecisionProductiva"))
        referencia = texto(payload.get("requestId"))
        evaluacion = fecha_hora(payload.get("fechaEvaluacion"), sin_fracciones=True)
        if habilitado or referencia != request_id:
            invalida()
        if fecha_evaluacion is not None and evaluacion != fecha_evaluacion:
            invalida()
        return cls(version_contrato=version, habilitado_decision_productiva=habilitado,
                   request_id=referencia, fecha_evaluacion=evaluacion,
                   fecha_consulta=fecha_hora(payload.get("fechaConsulta")),
                   periodo_anterior=objeto(payload.get("periodoAnterior")),
                   cartera_interna=objeto(payload.get("carteraInterna")),
                   cuota_vigente_interna=objeto(payload.get("cuotaVigenteInterna")),
                   aportes_sociales=objeto(payload.get("aportesSociales")))
