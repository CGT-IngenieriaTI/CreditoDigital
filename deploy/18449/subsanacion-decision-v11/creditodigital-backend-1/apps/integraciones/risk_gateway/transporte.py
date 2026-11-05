"""Una llamada por operacion, sin retry, redireccion, cache ni persistencia."""



from decimal import Decimal
import re
from uuid import UUID, uuid4

import requests

from .configuracion import GatewayConfig
from .contratos import HDCNormalizado, HDCRequest, PreselectaNormalizado, PreselectaRequest
from .evidencia import EvidenciaPDFHDC, MAX_PDF_BYTES, PDF_PATH
from .errores import (
    GatewayConfigurationError,
    GatewayEvidenceConflict, GatewayEvidenceNotFound, GatewayForbidden,
    GatewayIdempotencyConflict, GatewayInvalidResponse,
    GatewayNetworkError, GatewayProviderError, GatewayRateLimited, GatewayTimeout,
    GatewayUnauthorized, GatewayValidationError,
)


_STATUS_ERROR = {
    400: GatewayValidationError, 401: GatewayUnauthorized, 403: GatewayForbidden,
    405: GatewayValidationError, 406: GatewayValidationError, 409: GatewayIdempotencyConflict,
    415: GatewayValidationError, 422: GatewayValidationError, 429: GatewayRateLimited,
    500: GatewayProviderError, 502: GatewayProviderError, 503: GatewayProviderError,
    504: GatewayTimeout,
}
_OPERACIONES = {
    PreselectaRequest: ("/api/internal/v1/preselecta/consultar/", PreselectaNormalizado),
    HDCRequest: ("/api/internal/v1/hdc/consultar/", HDCNormalizado),
}


def _request_id(valor):
    try:
        return str(UUID(str(valor)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid4())


def _objeto_unico(pares):
    resultado = {}
    for k, v in pares:
        if k in resultado:
            raise ValueError
        resultado[k] = v
    return resultado


def _constante_invalida(_):
    raise ValueError


def _headers(config, rid, accept):
    return {"Authorization": f"Bearer {config.internal_secret}",
            "X-Internal-Client": config.internal_client, "X-Request-ID": rid,
            "Accept": accept}


class GatewayTransport:

    def __init__(self, config=None):
        self.config = config if config is not None else GatewayConfig.from_settings()

    def verificar_health(self, *, request_id=None):
        """GET autenticado, sin consultas ni prueba implicita de scopes o tarifas."""
        config = self.config.validar()
        if config.verify_ssl is not True:
            raise GatewayConfigurationError()
        rid = _request_id(request_id)
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(config.base_url.rstrip('/') + '/api/internal/v1/health/', headers=_headers(config, rid, 'application/json'), timeout=(config.connect_timeout, config.read_timeout), verify=config.verify, allow_redirects=False)
                try:
                    if response.status_code != 200:
                        error = _STATUS_ERROR.get(response.status_code, GatewayInvalidResponse)
                        raise error(status=response.status_code, request_id=rid)
                    try:
                        if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                            raise ValueError
                        p = response.json(object_pairs_hook=_objeto_unico, parse_constant=_constante_invalida)
                        if type(p) is not dict or set(p) != {'requestId', 'status', 'apiVersion', 'providerChecks'} or p['requestId'] != rid or (response.headers.get('X-Request-ID') != rid) or (p['status'] != 'ready') or (p['apiVersion'] != 'v1') or (p['providerChecks'] is not False):
                            raise ValueError
                        return p
                    except (ValueError, TypeError, requests.RequestException):
                        raise GatewayInvalidResponse(status=200, request_id=rid) from None
                finally:
                    response.close()
        except requests.Timeout:
            raise GatewayTimeout(request_id=rid) from None
        except requests.RequestException:
            raise GatewayNetworkError(request_id=rid) from None

    def consultar(self, request, *, request_id=None, idempotency_key=None):
        config = self.config.validar()
        if type(request) not in _OPERACIONES:
            raise GatewayValidationError()
        rid = _request_id(request_id)
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', idempotency_key)):
            raise GatewayValidationError(request_id=rid)
        metadata = request.get('metadata')
        if metadata is not None and metadata.get('consumidor', config.internal_client) != config.internal_client:
            raise GatewayValidationError(request_id=rid)
        headers = _headers(config, rid, 'application/json')
        headers['Content-Type'] = 'application/json'
        if idempotency_key is not None:
            headers['Idempotency-Key'] = idempotency_key
        endpoint, dto = _OPERACIONES[type(request)]
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(config.base_url.rstrip('/') + endpoint, json=request.a_dict(), headers=headers, timeout=(config.connect_timeout, config.read_timeout), verify=config.verify, allow_redirects=False)
                try:
                    if response.status_code != 200:
                        error = _STATUS_ERROR.get(response.status_code, GatewayInvalidResponse)
                        raise error(status=response.status_code, request_id=rid)
                    try:
                        if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                            raise ValueError
                        payload = response.json(parse_float=Decimal, parse_constant=_constante_invalida, object_pairs_hook=_objeto_unico)
                        resultado = dto.desde_payload(payload)
                        body_id = resultado['requestId']
                        if response.headers.get('X-Request-ID') != body_id or UUID(body_id) != UUID(rid) or resultado['resultado']['request_id'] != body_id:
                            raise ValueError
                        r = resultado['resultado']
                        if r['estado_tecnico'] not in {'COMPLETADO', 'SIN_INFORMACION'} or r['trazabilidad']['version_contrato'] != resultado['versionContrato']:
                            raise ValueError
                        if isinstance(resultado, PreselectaNormalizado) and r['resultado_proveedor']['naturaleza'] != 'RESULTADO_PROVEEDOR':
                            raise ValueError
                        return resultado
                    except (ValueError, TypeError, KeyError, RecursionError, GatewayInvalidResponse):
                        raise GatewayInvalidResponse(status=200, request_id=rid) from None
                finally:
                    response.close()
        except requests.Timeout:
            raise GatewayTimeout(request_id=rid) from None
        except requests.RequestException:
            raise GatewayNetworkError(request_id=rid) from None

    def obtener_pdf(self, consulta_id, *, request_id=None) -> EvidenciaPDFHDC:
        config = self.config.validar()
        rid = _request_id(request_id)
        try:
            if type(consulta_id) is not str or str(UUID(consulta_id)) != consulta_id.lower():
                raise ValueError
            consulta_id = str(UUID(consulta_id))
        except (ValueError, TypeError, AttributeError):
            raise GatewayValidationError(request_id=rid) from None
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(config.base_url.rstrip('/') + PDF_PATH.format(consulta_id=consulta_id), headers=_headers(config, rid, 'application/pdf'), stream=True, timeout=(config.connect_timeout, config.read_timeout), verify=config.verify, allow_redirects=False)
                try:
                    if response.status_code != 200:
                        error = {404: GatewayEvidenceNotFound, 409: GatewayEvidenceConflict}.get(response.status_code, _STATUS_ERROR.get(response.status_code, GatewayInvalidResponse))
                        raise error(status=response.status_code, request_id=rid)
                    try:
                        if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/pdf':
                            raise ValueError
                        header_id = response.headers.get('X-Request-ID')
                        if type(header_id) is not str or str(UUID(header_id)) != header_id.lower() or UUID(header_id) != UUID(rid):
                            raise ValueError
                        length = response.headers.get('Content-Length')
                        if length is not None and (not re.fullmatch('[0-9]+', length) or not 0 < int(length) <= MAX_PDF_BYTES):
                            raise ValueError
                        contenido = bytearray()
                        for bloque in response.iter_content(chunk_size=64 * 1024):
                            if len(contenido) + len(bloque) > MAX_PDF_BYTES:
                                raise ValueError
                            contenido.extend(bloque)
                        return EvidenciaPDFHDC(consulta_id=consulta_id, request_id=rid, contenido=bytes(contenido))
                    except (ValueError, TypeError):
                        raise GatewayInvalidResponse(status=200, request_id=rid) from None
                finally:
                    response.close()
        except requests.Timeout:
            raise GatewayTimeout(request_id=rid) from None
        except requests.RequestException:
            raise GatewayNetworkError(request_id=rid) from None

    def obtener_xml(self, consulta_id, *, request_id, titular_sha256, con_respuesta=False):
        """Fetch the same owned report; no POST, retry, redirects or bureau call."""
        import hashlib
        import base64
        import json
        if type(con_respuesta) is not bool:
            raise GatewayValidationError()
        config = self.config.validar()
        rid = _request_id(request_id)
        try:
            cid = str(UUID(str(consulta_id)))
            if str(UUID(str(request_id))) != rid or not re.fullmatch('[a-f0-9]{64}', titular_sha256):
                raise ValueError()
        except (TypeError, ValueError, AttributeError):
            raise GatewayValidationError(request_id=rid) from None
        if config.verify_ssl is not True:
            raise GatewayConfigurationError()
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(config.base_url.rstrip('/') + '/api/internal/v1/hdc/consultas/' + cid + '/pdf/', headers=_headers(config, rid, 'application/json' if con_respuesta else 'application/xml'), stream=True, timeout=(config.connect_timeout, config.read_timeout), verify=config.verify, allow_redirects=False)
                try:
                    if response.status_code != 200:
                        error = {404: GatewayEvidenceNotFound, 409: GatewayEvidenceConflict}.get(response.status_code, _STATUS_ERROR.get(response.status_code, GatewayInvalidResponse))
                        raise error(status=response.status_code, request_id=rid)
                    if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != ('application/json' if con_respuesta else 'application/xml') or response.headers.get('X-Request-ID') != rid or response.headers.get('X-HDC-Request-ID') != rid or (response.headers.get('X-HDC-Holder-SHA256') != titular_sha256):
                        raise GatewayInvalidResponse(status=200, request_id=rid)
                    data = bytearray()
                    for block in response.iter_content(chunk_size=64 * 1024):
                        if len(data) + len(block) > (32 if con_respuesta else 16) * 1024 * 1024:
                            raise GatewayInvalidResponse(status=200, request_id=rid)
                        data.extend(block)
                    if not data or hashlib.sha256(data).hexdigest() != response.headers.get('X-Content-SHA256'):
                        raise GatewayInvalidResponse(status=200, request_id=rid)
                    if not con_respuesta:
                        return bytes(data)
                    try:
                        packet = json.loads(data, parse_float=Decimal, object_pairs_hook=_objeto_unico, parse_constant=_constante_invalida)
                        if packet['versionContrato'] != 'credihoy-hdc-custodia-v1' or packet['consultaNueva'] is not False or packet['normalizadoReconstruidoDesdeXML'] is not True:
                            raise ValueError()
                        xml = base64.b64decode(packet['xmlBase64'], validate=True)
                        if not 0 < len(xml) <= 16 * 1024 * 1024 or hashlib.sha256(xml).hexdigest() != packet['xmlSha256']:
                            raise ValueError()
                        normalized = HDCNormalizado.desde_payload(packet['normalizado'])
                        if normalized['requestId'] != rid or normalized['resultado']['consulta_id'] != cid:
                            raise ValueError()
                        return (normalized, xml)
                    except (ValueError, TypeError, KeyError):
                        raise GatewayInvalidResponse(status=200, request_id=rid) from None
                finally:
                    response.close()
        except requests.Timeout:
            raise GatewayTimeout(request_id=rid) from None
        except requests.RequestException:
            raise GatewayNetworkError(request_id=rid) from None
