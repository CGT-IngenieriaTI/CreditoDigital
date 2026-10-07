"""Transporte S2S comun: una llamada explicita, sin retry ni persistencia."""

from contextlib import contextmanager
from decimal import Decimal
import re
from uuid import UUID, uuid4

import requests

from .configuracion import RiskGatewayConfig
from .consultas import HDCNormalizado, HDCRequest, PreselectaNormalizado, PreselectaRequest
from .contratos import HealthResponse, validar_request_id
from .evidencia import EvidenciaPDFHDC, MAX_PDF_BYTES, PDF_PATH
from .errores import (
    GatewayContentTypeError, GatewayCorrelationError, GatewayEvidenceConflict,
    GatewayEvidenceNotFound, GatewayForbidden, GatewayIdempotencyConflict,
    GatewayInvalidResponse, GatewayJSONError, GatewayNetworkError, GatewayPDFError,
    GatewayProviderError, GatewayRateLimited, GatewayRedirectError, GatewaySchemaError,
    GatewayTimeout, GatewayUnauthorized, GatewayValidationError,
)


HEALTH_PATH = "/api/internal/v1/health/"
PRESELECTA_PATH = "/api/internal/v1/preselecta/consultar/"
HDC_PATH = "/api/internal/v1/hdc/consultar/"


def _pares_unicos(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError
        resultado[clave] = valor
    return resultado


def _constante_invalida(_):
    raise ValueError


def _json_compatible(content_type):
    media = content_type.split(";", 1)[0].strip().lower()
    return media == "application/json" or (media.startswith("application/") and media.endswith("+json"))


def _request_id(value):
    return str(uuid4()) if value is None else str(UUID(validar_request_id(value)))


def _idempotency_key(value):
    if value is not None and (type(value) is not str or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)):
        raise GatewayValidationError()
    return value


def _headers(config, rid, accept, key):
    headers = {"Authorization": "Bearer " + config.internal_secret,
               "X-Internal-Client": config.internal_client,
               "X-Request-ID": rid, "Accept": accept}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _estado_200(response, rid, operation):
    status = response.status_code
    if status == 200:
        return
    error = {401: GatewayUnauthorized, 403: GatewayForbidden,
             429: GatewayRateLimited, 504: GatewayTimeout}.get(status)
    if error is None and operation == "consulta":
        error = {400: GatewayValidationError, 405: GatewayValidationError,
                 406: GatewayValidationError, 415: GatewayValidationError,
                 422: GatewayValidationError, 409: GatewayIdempotencyConflict}.get(status)
    if error is None and operation == "pdf":
        error = {404: GatewayEvidenceNotFound, 409: GatewayEvidenceConflict}.get(status)
    if error is None:
        if 500 <= status <= 599:
            error = GatewayProviderError
        elif 300 <= status <= 399 and operation != "health":
            error = GatewayRedirectError
        else:
            error = GatewayInvalidResponse
    raise error(status=status, request_id=rid)


def _json(response, rid, *, health=False):
    invalid_type = GatewayInvalidResponse if health else GatewayContentTypeError
    if not _json_compatible(response.headers.get("Content-Type", "")):
        raise invalid_type(status=200, request_id=rid)
    try:
        return response.json(object_pairs_hook=_pares_unicos,
                             parse_constant=_constante_invalida, parse_float=Decimal)
    except (ValueError, TypeError, RecursionError):
        error = GatewayInvalidResponse if health else GatewayJSONError
        raise error(status=200, request_id=rid) from None


class GatewayTransport:
    def __init__(self, config=None):
        self.config = config if config is not None else RiskGatewayConfig.from_settings()

    @contextmanager
    def _response(self, config, method, path, rid, *, payload=None, key=None, pdf=False):
        headers = _headers(config, rid, "application/pdf" if pdf else "application/json", key)
        options = {"headers": headers, "timeout": (config.connect_timeout, config.read_timeout),
                   "verify": config.verify, "allow_redirects": False}
        if payload is not None:
            options.update(json=payload)
            headers["Content-Type"] = "application/json"
        if pdf:
            options["stream"] = True
        try:
            with requests.Session() as session:
                session.trust_env = False
                if method == "GET":
                    response = session.get(config.base_url.rstrip("/") + path, **options)
                else:
                    response = session.post(config.base_url.rstrip("/") + path, **options)
                try:
                    yield response
                finally:
                    response.close()
        except requests.Timeout:
            raise GatewayTimeout(request_id=rid) from None
        except requests.RequestException:
            raise GatewayNetworkError(request_id=rid) from None

    def health(self, *, request_id=None) -> HealthResponse:
        config = self.config.validar()
        rid = validar_request_id(str(uuid4()) if request_id is None else request_id)
        with self._response(config, "GET", HEALTH_PATH, rid) as response:
            _estado_200(response, rid, "health")
            try:
                return HealthResponse.desde_payload(_json(response, rid, health=True),
                    request_id=rid, header_request_id=response.headers.get("X-Request-ID"))
            except (ValueError, TypeError, RecursionError, GatewayInvalidResponse):
                raise GatewayInvalidResponse(status=200, request_id=rid) from None

    def consultar_preselecta(self, request, *, request_id=None, idempotency_key=None):
        return self._consultar(request, PreselectaRequest, PreselectaNormalizado, PRESELECTA_PATH,
            request_id=request_id, idempotency_key=idempotency_key)

    def consultar_hdc(self, request, *, request_id=None, idempotency_key=None):
        return self._consultar(request, HDCRequest, HDCNormalizado, HDC_PATH,
            request_id=request_id, idempotency_key=idempotency_key)

    def _consultar(self, request, request_type, response_type, path, *, request_id, idempotency_key):
        config = self.config.validar()
        if type(request) is not request_type:
            raise GatewayValidationError()
        rid, key = _request_id(request_id), _idempotency_key(idempotency_key)
        metadata = request.get("metadata")
        if metadata is not None and metadata.get("consumidor", config.internal_client) != config.internal_client:
            raise GatewayValidationError(request_id=rid)
        with self._response(config, "POST", path, rid, payload=request.a_dict(), key=key) as response:
            _estado_200(response, rid, "consulta")
            payload = _json(response, rid)
            try:
                result = response_type.desde_payload(payload)
            except GatewayInvalidResponse:
                raise GatewaySchemaError(status=200, request_id=rid) from None
            if (response.headers.get("X-Request-ID") != rid or result["requestId"] != rid
                    or result["resultado"]["request_id"] != rid):
                raise GatewayCorrelationError(status=200, request_id=rid)
            if (response.headers.get("Idempotency-Key") != key
                    or response.headers.get("X-Idempotency-Status") != (
                        "IDEMPOTENCIA_DE_NEGOCIO_PENDIENTE" if key is not None else None)):
                raise GatewayCorrelationError(status=200, request_id=rid)
            return result

    def obtener_pdf_hdc(self, consulta_id, *, request_id=None) -> EvidenciaPDFHDC:
        config = self.config.validar()
        cid = str(UUID(validar_request_id(consulta_id)))
        rid = _request_id(request_id)
        with self._response(config, "GET", PDF_PATH.format(consulta_id=cid), rid, pdf=True) as response:
            _estado_200(response, rid, "pdf")
            if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/pdf":
                raise GatewayContentTypeError(status=200, request_id=rid)
            if response.headers.get("X-Request-ID") != rid:
                raise GatewayCorrelationError(status=200, request_id=rid)
            try:
                length = response.headers.get("Content-Length")
                if length is not None and (not re.fullmatch(r"[0-9]+", length)
                        or not 0 < int(length) <= MAX_PDF_BYTES):
                    raise ValueError
                content = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if len(content) + len(chunk) > MAX_PDF_BYTES:
                        raise ValueError
                    content.extend(chunk)
                return EvidenciaPDFHDC(consulta_id=cid, request_id=rid, contenido=bytes(content))
            except (ValueError, TypeError):
                raise GatewayPDFError(status=200, request_id=rid) from None
