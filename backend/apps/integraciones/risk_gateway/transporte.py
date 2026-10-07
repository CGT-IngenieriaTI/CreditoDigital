"""Solo GET Health: una llamada, sin proveedores, redirects, retries o persistencia."""

from uuid import uuid4

import requests

from .configuracion import RiskGatewayConfig
from .contratos import HealthResponse, validar_request_id
from .errores import (
    GatewayForbidden, GatewayInvalidResponse, GatewayNetworkError, GatewayProviderError,
    GatewayRateLimited, GatewayTimeout, GatewayUnauthorized,
)


HEALTH_PATH = "/api/internal/v1/health/"


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


class GatewayTransport:
    def __init__(self, config=None):
        self.config = config if config is not None else RiskGatewayConfig.from_settings()

    def health(self, *, request_id=None) -> HealthResponse:
        config = self.config.validar()
        rid = validar_request_id(str(uuid4()) if request_id is None else request_id)
        headers = {"Authorization": "Bearer " + config.internal_secret,
                   "X-Internal-Client": config.internal_client,
                   "X-Request-ID": rid, "Accept": "application/json"}
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(config.base_url.rstrip("/") + HEALTH_PATH,
                    headers=headers, timeout=(config.connect_timeout, config.read_timeout),
                    verify=config.verify, allow_redirects=False)
                try:
                    status = response.status_code
                    if status != 200:
                        error = {401: GatewayUnauthorized, 403: GatewayForbidden,
                                 429: GatewayRateLimited, 504: GatewayTimeout}.get(status)
                        if error is None:
                            error = GatewayProviderError if 500 <= status <= 599 else GatewayInvalidResponse
                        raise error(status=status, request_id=rid)
                    try:
                        if not _json_compatible(response.headers.get("Content-Type", "")):
                            raise ValueError
                        payload = response.json(object_pairs_hook=_pares_unicos, parse_constant=_constante_invalida)
                        return HealthResponse.desde_payload(payload, request_id=rid,
                            header_request_id=response.headers.get("X-Request-ID"))
                    except (ValueError, TypeError, RecursionError, GatewayInvalidResponse):
                        raise GatewayInvalidResponse(status=200, request_id=rid) from None
                finally:
                    response.close()
        except requests.Timeout:
            raise GatewayTimeout(request_id=rid) from None
        except requests.RequestException:
            raise GatewayNetworkError(request_id=rid) from None
