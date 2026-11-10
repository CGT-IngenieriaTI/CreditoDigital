"""Transporte HTTP opt-in. No registra documento, respuestas ni secretos."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import math
import re
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from .dto import ApiDataCrediHoyError, CodigoErrorApiData, RespuestaApiDataDTO, fecha_hora


ENDPOINT = "/api/v1/incluirtec/credihoy-digital/hechos-internos/"


@dataclass(frozen=True, kw_only=True)
class ConfiguracionApiData:
    base_url: str
    token: str = field(repr=False)
    timeout: float

    def __post_init__(self):
        try:
            url = urlsplit(self.base_url)
            valida = (url.scheme in {"http", "https"} and url.hostname and not url.username
                      and not url.password and not url.query and not url.fragment)
        except (ValueError, TypeError):
            valida = False
        if not valida:
            raise ValueError("Configurar API_DATA_CREDIHOY_BASE_URL sin credenciales ni query")
        if not isinstance(self.token, str) or not self.token.strip() or any(c.isspace() for c in self.token):
            raise ValueError("Configurar API_DATA_CREDIHOY_TOKEN")
        if type(self.timeout) not in (int, float) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("Configurar API_DATA_CREDIHOY_TIMEOUT como numero positivo")

    @classmethod
    def desde_settings(cls, configuracion=None):
        if configuracion is None:
            from django.conf import settings
            configuracion = settings
        try:
            timeout = float(configuracion.API_DATA_CREDIHOY_TIMEOUT)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Configurar API_DATA_CREDIHOY_TIMEOUT") from None
        return cls(base_url=getattr(configuracion, "API_DATA_CREDIHOY_BASE_URL", ""),
                   token=getattr(configuracion, "API_DATA_CREDIHOY_TOKEN", ""), timeout=timeout)


class ApiDataCrediHoyClient:
    def __init__(self, configuracion: ConfiguracionApiData, *, session=None):
        self.configuracion = configuracion
        self.session = session if session is not None else requests.Session()

    def consultar(self, *, numero_documento: str, fecha_evaluacion: datetime,
                  request_id: str | None = None) -> RespuestaApiDataDTO:
        if not isinstance(numero_documento, str) or not re.fullmatch(r"[0-9]{1,30}", numero_documento):
            raise ValueError("numero_documento requiere 1 a 30 digitos ASCII")
        if (not isinstance(fecha_evaluacion, datetime) or fecha_evaluacion.tzinfo is None
                or fecha_evaluacion.utcoffset() is None or fecha_evaluacion.microsecond):
            raise ValueError("fecha_evaluacion requiere segundos, zona y ninguna fraccion")
        fecha_serializada = fecha_evaluacion.isoformat(timespec="seconds")
        try:
            fecha_hora(fecha_serializada, sin_fracciones=True)
        except ApiDataCrediHoyError:
            raise ValueError("fecha_evaluacion requiere zona con precision de minutos") from None
        request_id = request_id if request_id is not None else str(uuid4())
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
            raise ValueError("X-Request-ID invalido")
        try:
            response = self.session.post(
                self.configuracion.base_url.rstrip("/") + ENDPOINT,
                headers={"Authorization": "Bearer " + self.configuracion.token,
                         "X-Request-ID": request_id, "Accept": "application/json"},
                json={"numeroDocumento": numero_documento, "fechaEvaluacion": fecha_serializada},
                timeout=self.configuracion.timeout, allow_redirects=False, verify=True,
            )
        except requests.Timeout:
            raise ApiDataCrediHoyError(CodigoErrorApiData.TIMEOUT) from None
        except requests.RequestException:
            raise ApiDataCrediHoyError(CodigoErrorApiData.ERROR_TEMPORAL) from None
        try:
            if response.status_code in (401, 403):
                raise ApiDataCrediHoyError(CodigoErrorApiData.NO_AUTORIZADO)
            if response.status_code in (400, 404, 405, 409, 415, 503):
                self._error_financiero(response)
            if response.status_code >= 500 or response.status_code in (408, 429):
                raise ApiDataCrediHoyError(CodigoErrorApiData.ERROR_TEMPORAL)
            if response.status_code != 200:
                raise ApiDataCrediHoyError(CodigoErrorApiData.RESPUESTA_INVALIDA)
            try:
                payload = response.json(parse_float=Decimal)
            except (ValueError, requests.RequestException):
                raise ApiDataCrediHoyError(CodigoErrorApiData.RESPUESTA_INVALIDA) from None
            return RespuestaApiDataDTO.desde_json(payload, request_id=request_id,
                                                 fecha_evaluacion=fecha_evaluacion)
        finally:
            response.close()

    @staticmethod
    def _error_financiero(response):
        permitidos = {
            400: {"REQUEST_INVALIDO", "HTTP_INVALIDO"}, 405: {"HTTP_INVALIDO"},
            415: {"HTTP_INVALIDO"},
            404: {"ASOCIADO_NO_DISPONIBLE", "INFORMACION_NO_DISPONIBLE"},
            409: {"INFORMACION_INCONSISTENTE"}, 503: {"ERROR_TEMPORAL"},
        }
        try:
            payload = response.json()
        except (ValueError, requests.RequestException):
            if response.status_code == 503:
                raise ApiDataCrediHoyError(CodigoErrorApiData.ERROR_TEMPORAL) from None
            raise ApiDataCrediHoyError(CodigoErrorApiData.RESPUESTA_INVALIDA) from None
        # Solo se conserva el codigo publico, nunca mensajes ni cuerpos de error.
        codigo = payload.get("codigo", payload.get("estado")) if isinstance(payload, dict) else None
        if not isinstance(codigo, str) or codigo not in permitidos[response.status_code]:
            if response.status_code == 503:
                raise ApiDataCrediHoyError(CodigoErrorApiData.ERROR_TEMPORAL)
            raise ApiDataCrediHoyError(CodigoErrorApiData.RESPUESTA_INVALIDA)
        raise ApiDataCrediHoyError(CodigoErrorApiData(codigo))
