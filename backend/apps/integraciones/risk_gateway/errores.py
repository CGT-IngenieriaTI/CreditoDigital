"""Errores publicos seguros: nunca conservan request, response o excepcion remota."""


class GatewayError(Exception):
    codigo = "GATEWAY_ERROR"

    def __init__(self, *, status=None, request_id=None):
        self.status = status
        self.request_id = request_id
        super().__init__(self.codigo)


class GatewayDisabled(GatewayError):
    codigo = "GATEWAY_DISABLED"


class GatewayConfigurationError(GatewayError):
    codigo = "GATEWAY_CONFIGURACION_INVALIDA"


class GatewayUnauthorized(GatewayError):
    codigo = "GATEWAY_NO_AUTORIZADO"


class GatewayForbidden(GatewayError):
    codigo = "GATEWAY_PROHIBIDO"


class GatewayEvidenceNotFound(GatewayError):
    codigo = "GATEWAY_EVIDENCIA_NO_DISPONIBLE"


class GatewayEvidenceConflict(GatewayError):
    codigo = "GATEWAY_EVIDENCIA_INCONSISTENTE"


class GatewayValidationError(GatewayError):
    codigo = "GATEWAY_REQUEST_INVALIDO"


class GatewayIdempotencyConflict(GatewayError):
    codigo = "GATEWAY_IDEMPOTENCY_CONFLICT"


class GatewayRateLimited(GatewayError):
    codigo = "GATEWAY_LIMITE_ALCANZADO"


class GatewayProviderError(GatewayError):
    codigo = "GATEWAY_ERROR_REMOTO"


class GatewayTimeout(GatewayError):
    codigo = "GATEWAY_TIMEOUT"


class GatewayNetworkError(GatewayError):
    codigo = "GATEWAY_ERROR_CONEXION"


class GatewayInvalidResponse(GatewayError):
    codigo = "GATEWAY_RESPUESTA_INVALIDA"
