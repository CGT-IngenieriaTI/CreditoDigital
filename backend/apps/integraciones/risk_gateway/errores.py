"""Errores seguros: no conservan cuerpos HTTP, headers ni errores remotos."""


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


class GatewayValidationError(GatewayError):
    codigo = "GATEWAY_REQUEST_INVALIDO"


class GatewayUnauthorized(GatewayError):
    codigo = "GATEWAY_NO_AUTORIZADO"


class GatewayForbidden(GatewayError):
    codigo = "GATEWAY_PROHIBIDO"


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


class GatewayIdempotencyConflict(GatewayError):
    codigo = "GATEWAY_IDEMPOTENCIA_CONFLICTO"


class GatewayEvidenceNotFound(GatewayError):
    codigo = "GATEWAY_EVIDENCIA_NO_ENCONTRADA"


class GatewayEvidenceConflict(GatewayError):
    codigo = "GATEWAY_EVIDENCIA_NO_DISPONIBLE"


class GatewayContentTypeError(GatewayInvalidResponse):
    codigo = "GATEWAY_CONTENT_TYPE_INVALIDO"


class GatewayJSONError(GatewayInvalidResponse):
    codigo = "GATEWAY_JSON_INVALIDO"


class GatewaySchemaError(GatewayInvalidResponse):
    codigo = "GATEWAY_SCHEMA_INVALIDO"


class GatewayCorrelationError(GatewayInvalidResponse):
    codigo = "GATEWAY_CORRELACION_INVALIDA"


class GatewayPDFError(GatewayInvalidResponse):
    codigo = "GATEWAY_PDF_INVALIDO"


class GatewayRedirectError(GatewayInvalidResponse):
    codigo = "GATEWAY_REDIRECT_INESPERADO"
