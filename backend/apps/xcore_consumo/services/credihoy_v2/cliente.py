"""Cliente HTTP V2 aislado, sin fallback, logs financieros ni reintentos."""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import math
import re
import time
import os
import json
import stat
import hashlib
import hmac
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from .errores_http import ApiDataCrediHoyV2Error, CodigoErrorApiDataV2 as Codigo
from .internos_http import AdaptadorHechosInternosV2, VERSION_CONTRATO
from .hechos import RequestHechosInternosV2, SolicitudEvaluadaV2


ENDPOINT_V2 = "/api/v2/incluirtec/credihoy-digital/hechos-internos/"
ENDPOINT_SOCIO = "/api/v1/incluirtec/consultarSocio/"
ENDPOINT_TOKEN = "/api/v1/incluirtec/token/"


@dataclass(frozen=True, kw_only=True)
class ConfiguracionApiDataV2:
    base_url: str
    token: str = field(default="", repr=False)
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)
    timeout: float = 15
    titular_sha256: str = field(default='',repr=False)
    vigente_hasta: datetime | None = field(default=None,repr=False)

    def __post_init__(self):
        try:
            url = urlsplit(self.base_url)
            valida = (isinstance(self.base_url, str) and url.scheme in {"http", "https"}
                      and url.hostname and not url.username and not url.password
                      and not url.query and not url.fragment and url.path in ("", "/"))
            url.port
        except (ValueError, TypeError, AttributeError):
            valida = False
        token_valido = type(self.token) is str and bool(self.token.strip()) and not any(c.isspace() for c in self.token)
        cliente_valido = all(type(v) is str and bool(v.strip()) for v in (self.client_id, self.client_secret))
        if (not valida or type(self.token) is not str or type(self.client_id) is not str or type(self.client_secret) is not str
                or not (token_valido and not self.client_id and not self.client_secret
                        or not self.token and cliente_valido)
                or type(self.timeout) not in (int, float)
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ApiDataCrediHoyV2Error(Codigo.CONFIGURACION_INCOMPLETA)

    @classmethod
    def desde_settings(cls, configuracion=None):
        if configuracion is None:
            from django.conf import settings
            configuracion = settings
        try:
            path=Path(getattr(configuracion,'API_DATA_CREDIHOY_CREDENTIAL_FILE',
                '/run/credihoy-hechos-cliente.json'))
            if path.exists() or path.is_symlink():
                if not path.is_absolute() or path.is_symlink():raise ValueError()
                with path.open('rb') as stream:
                    info=os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_size>8192
                            or os.name!='nt' and (info.st_uid!=os.geteuid() or info.st_mode&0o077)):
                        raise ValueError()
                    cfg=json.load(stream,object_pairs_hook=_pares_unicos)
                if set(cfg)!={'base_url','client_id','client_secret','timeout','hasta','documentoSha256','referencia'}:
                    raise ValueError()
                until=datetime.fromisoformat(cfg['hasta']);now=datetime.now(timezone.utc)
                if (until.utcoffset() is None or not now<until<=now+timedelta(hours=1)
                        or not cfg['base_url'].startswith('https://') or not cfg['referencia'].strip()
                        or not re.fullmatch('[a-f0-9]{64}',cfg['documentoSha256'])):
                    raise ValueError()
                return cls(base_url=cfg['base_url'],client_id=cfg['client_id'],client_secret=cfg['client_secret'],
                    timeout=cfg['timeout'],titular_sha256=cfg['documentoSha256'],vigente_hasta=until)
            timeout = getattr(configuracion, "API_DATA_CREDIHOY_TIMEOUT", 15)
            if type(timeout) is bool:
                raise ValueError()
            return cls(base_url=getattr(configuracion, "API_DATA_CREDIHOY_BASE_URL", ""),
                       token=getattr(configuracion, "API_DATA_CREDIHOY_TOKEN", ""), timeout=float(timeout),
                       client_id=getattr(configuracion, "API_DATA_CREDIHOY_CLIENT_ID", ""),
                       client_secret=getattr(configuracion, "API_DATA_CREDIHOY_CLIENT_SECRET", ""))
        except (OSError,ValueError, TypeError, AttributeError,KeyError):
            raise ApiDataCrediHoyV2Error(Codigo.CONFIGURACION_INCOMPLETA) from None


def _pares_unicos(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError("JSON duplicado")
        resultado[clave] = valor
    return resultado


def _constante_invalida(_):
    raise ValueError("JSON no finito")


class ApiDataCrediHoyV2Client:
    def __init__(self, configuracion: ConfiguracionApiDataV2, *, session=None):
        if not isinstance(configuracion, ConfiguracionApiDataV2):
            raise ApiDataCrediHoyV2Error(Codigo.CONFIGURACION_INCOMPLETA)
        self.configuracion = configuracion
        self.session = session if session is not None else requests.Session()
        self.adaptador = AdaptadorHechosInternosV2()
        self._token_temporal = None
        self._token_expira = 0

    def _token(self, request_id):
        if self.configuracion.token:
            return self.configuracion.token
        if self._token_temporal is not None and time.monotonic() < self._token_expira:
            return self._token_temporal
        try:
            response = self.session.post(self.configuracion.base_url.rstrip("/") + ENDPOINT_TOKEN,
                headers={"X-Request-ID": request_id, "Accept": "application/json"},
                json={"client_id": self.configuracion.client_id, "client_secret": self.configuracion.client_secret},
                timeout=self.configuracion.timeout, allow_redirects=False, verify=True)
        except requests.Timeout:
            raise ApiDataCrediHoyV2Error(Codigo.TIMEOUT) from None
        except requests.RequestException:
            raise ApiDataCrediHoyV2Error(Codigo.NETWORK) from None
        try:
            if response.status_code in (401, 403):
                raise ApiDataCrediHoyV2Error(Codigo.NO_AUTORIZADO)
            if response.status_code != 200:
                raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA)
            p = self._json(response)
            if (type(p) is not dict or type(p.get("result")) is not int or p["result"] != 0
                    or type(p.get("access_token")) is not str or not p["access_token"]
                    or any(c.isspace() for c in p["access_token"])
                    or type(p.get("expires_in")) is not int or p["expires_in"] <= 0):
                raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA)
            self._token_temporal = p["access_token"]
            self._token_expira = time.monotonic() + p["expires_in"]
            return self._token_temporal
        finally:
            response.close()

    def consultar_socio(self, *, numero_documento, request_id):
        """Contrato consultarSocio existente; no invoca hechos V1 ni scoring."""
        if (type(numero_documento) is not str or not re.fullmatch(r"[0-9]{6,15}", numero_documento)
                or type(request_id) is not str or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id)):
            raise ApiDataCrediHoyV2Error(Codigo.REQUEST_INVALIDO)
        try:
            response = self.session.post(self.configuracion.base_url.rstrip("/") + ENDPOINT_SOCIO,
                headers={"Authorization": "Bearer " + self._token(request_id), "X-Request-ID": request_id,
                    "Accept": "application/json"}, json={"numeroDocumento": numero_documento},
                timeout=self.configuracion.timeout, allow_redirects=False, verify=True)
        except requests.Timeout:
            raise ApiDataCrediHoyV2Error(Codigo.TIMEOUT) from None
        except requests.RequestException:
            raise ApiDataCrediHoyV2Error(Codigo.NETWORK) from None
        try:
            if response.status_code in (401, 403):
                raise ApiDataCrediHoyV2Error(Codigo.NO_AUTORIZADO)
            if response.status_code == 404:
                raise ApiDataCrediHoyV2Error(Codigo.ASOCIADO_NO_DISPONIBLE)
            if response.status_code >= 500 or response.status_code in (408, 429):
                raise ApiDataCrediHoyV2Error(Codigo.ERROR_TEMPORAL)
            if response.status_code != 200:
                raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA)
            if response.headers.get("X-Request-ID") != request_id:
                raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA)
            p = self._json(response)
            if not isinstance(p, dict) or type(p.get("result")) is not int:
                raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA)
            if p["result"] == 1 and p.get("informacionSocio") is None:
                raise ApiDataCrediHoyV2Error(Codigo.ASOCIADO_NO_DISPONIBLE)
            if p["result"] != 0 or not isinstance(p.get("informacionSocio"), dict):
                raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA)
            return p["informacionSocio"]
        finally:
            response.close()

    def consultar(self, *, numero_documento: str, fecha_evaluacion: datetime, request_id=None,
                  solicitud_evaluada: SolicitudEvaluadaV2 | None = None):
        if self.configuracion.vigente_hasta is not None:
            if (datetime.now(timezone.utc)>=self.configuracion.vigente_hasta
                    or not isinstance(numero_documento,str)
                    or not hmac.compare_digest(hashlib.sha256(numero_documento.encode()).hexdigest(),self.configuracion.titular_sha256)):
                raise ApiDataCrediHoyV2Error(Codigo.NO_AUTORIZADO)
        try:
            entrada = RequestHechosInternosV2(numero_documento=numero_documento,
                fecha_evaluacion=fecha_evaluacion, solicitud_evaluada=solicitud_evaluada)
        except (TypeError, ValueError):
            raise ApiDataCrediHoyV2Error(Codigo.REQUEST_INVALIDO) from None
        request_id = request_id if request_id is not None else str(uuid4())
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
            raise ApiDataCrediHoyV2Error(Codigo.REQUEST_INVALIDO)
        try:
            response = self.session.post(
                self.configuracion.base_url.rstrip("/") + ENDPOINT_V2,
                headers={"Authorization": "Bearer " + self._token(request_id),
                         "X-Request-ID": request_id, "Accept": "application/json"},
                json=entrada.a_payload_http(),
                timeout=self.configuracion.timeout, allow_redirects=False, verify=True,
            )
        except requests.Timeout:
            raise ApiDataCrediHoyV2Error(Codigo.TIMEOUT) from None
        except requests.RequestException:
            raise ApiDataCrediHoyV2Error(Codigo.NETWORK) from None
        try:
            if response.status_code in (401, 403):
                # El middleware de autenticacion no usa envelope financiero.
                raise ApiDataCrediHoyV2Error(Codigo.NO_AUTORIZADO, status_http=response.status_code)
            if response.status_code in (400, 404, 405, 409, 415, 503):
                self._error_http(response, request_id)
            if response.status_code >= 500 or response.status_code in (408, 429):
                raise ApiDataCrediHoyV2Error(Codigo.ERROR_TEMPORAL, status_http=response.status_code)
            if response.status_code != 200:
                raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA, status_http=response.status_code)
            payload = self._json(response)
            return self.adaptador.adaptar(payload, request_id=request_id, fecha_evaluacion=fecha_evaluacion)
        finally:
            response.close()

    def consultar_estamentos(self, *, numero_documento, solicitud_id, fecha_evaluacion, request_id):
        """Same internal facts URL/token/transport, a read-only dedicated scope."""
        from uuid import UUID
        try:
            sid=str(UUID(str(solicitud_id)))
            entrada=RequestHechosInternosV2(numero_documento=numero_documento,fecha_evaluacion=fecha_evaluacion)
            if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',request_id):raise ValueError()
        except (ValueError,TypeError):
            raise ApiDataCrediHoyV2Error(Codigo.REQUEST_INVALIDO) from None
        payload={**entrada.a_payload_http(),'solicitudId':sid,'alcance':'ESTAMENTOS'}
        try:
            response=self.session.post(self.configuracion.base_url.rstrip('/')+ENDPOINT_V2,
                headers={'Authorization':'Bearer '+self._token(request_id),'X-Request-ID':request_id,'Accept':'application/json'},
                json=payload,timeout=self.configuracion.timeout,allow_redirects=False,verify=True)
        except requests.Timeout:raise ApiDataCrediHoyV2Error(Codigo.TIMEOUT) from None
        except requests.RequestException:raise ApiDataCrediHoyV2Error(Codigo.NETWORK) from None
        try:
            if response.status_code in (401,403):raise ApiDataCrediHoyV2Error(Codigo.NO_AUTORIZADO)
            if response.status_code!=200:raise ApiDataCrediHoyV2Error(Codigo.ERROR_TEMPORAL)
            evidence=self._json(response)
            from .unico.estamentos import validar_lectura_original
            return validar_lectura_original(evidence,solicitud_id=sid,documento=numero_documento,request_id=request_id)
        finally:response.close()

    @staticmethod
    def _json(response):
        try:
            return response.json(parse_float=Decimal, parse_constant=_constante_invalida,
                                 object_pairs_hook=_pares_unicos)
        except (ValueError, requests.RequestException):
            raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA, status_http=response.status_code) from None

    def _error_http(self, response, request_id):
        permitidos = {
            400: {"REQUEST_INVALIDO", "HTTP_INVALIDO"}, 405: {"HTTP_INVALIDO"}, 415: {"HTTP_INVALIDO"},
            404: {"ASOCIADO_NO_DISPONIBLE", "INFORMACION_NO_DISPONIBLE"},
            409: {"INFORMACION_INCONSISTENTE"}, 503: {"ERROR_TEMPORAL"},
        }
        payload = self._json(response)
        if (not isinstance(payload, dict) or payload.get("versionContrato") != VERSION_CONTRATO
                or payload.get("requestId") != request_id
                or payload.get("habilitadoDecisionProductiva") is not False):
            raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA, status_http=response.status_code)
        error = payload.get("error")
        codigo = error.get("codigo") if isinstance(error, dict) else None
        if not isinstance(codigo, str) or codigo not in permitidos[response.status_code]:
            raise ApiDataCrediHoyV2Error(Codigo.RESPUESTA_INVALIDA, status_http=response.status_code)
        raise ApiDataCrediHoyV2Error(Codigo(codigo), status_http=response.status_code)
