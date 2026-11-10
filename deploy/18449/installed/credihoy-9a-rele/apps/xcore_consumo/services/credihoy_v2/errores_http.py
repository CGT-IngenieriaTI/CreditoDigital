"""Errores publicos V2; no conservan documento, token ni cuerpo remoto."""

from enum import Enum


class CodigoErrorApiDataV2(str, Enum):
    CONFIGURACION_INCOMPLETA = "CONFIGURACION_API_DATA_INCOMPLETA"
    REQUEST_INVALIDO = "REQUEST_INVALIDO"
    HTTP_INVALIDO = "HTTP_INVALIDO"
    NO_AUTORIZADO = "API_DATA_NO_AUTORIZADO"
    ASOCIADO_NO_DISPONIBLE = "ASOCIADO_NO_DISPONIBLE"
    INFORMACION_NO_DISPONIBLE = "INFORMACION_NO_DISPONIBLE"
    INFORMACION_INCONSISTENTE = "INFORMACION_INCONSISTENTE"
    ERROR_TEMPORAL = "ERROR_TEMPORAL"
    TIMEOUT = "API_DATA_TIMEOUT"
    NETWORK = "API_DATA_NETWORK"
    RESPUESTA_INVALIDA = "API_DATA_RESPUESTA_V2_INVALIDA"


class ApiDataCrediHoyV2Error(Exception):
    def __init__(self, codigo: CodigoErrorApiDataV2, *, status_http: int | None = None):
        self.codigo = codigo
        self.status_http = status_http
        super().__init__(codigo.value)


def respuesta_invalida():
    raise ApiDataCrediHoyV2Error(CodigoErrorApiDataV2.RESPUESTA_INVALIDA) from None
