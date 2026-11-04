"""Capacidades independientes; no ejecuta una segunda consulta implicitamente."""

from .contratos import HDCRequest, PreselectaRequest
from .errores import GatewayValidationError
from .transporte import GatewayTransport


class PreselectaGatewayClient:
    def __init__(self, transport=None):
        self.transport = transport if transport is not None else GatewayTransport()

    def consultar(self, request: PreselectaRequest, *, request_id=None, idempotency_key=None):
        if type(request) is not PreselectaRequest:
            raise GatewayValidationError()
        return self.transport.consultar(request, request_id=request_id, idempotency_key=idempotency_key)


class HDCGatewayClient:
    def __init__(self, transport=None):
        self.transport = transport if transport is not None else GatewayTransport()

    def consultar(self, request: HDCRequest, *, request_id=None, idempotency_key=None):
        if type(request) is not HDCRequest:
            raise GatewayValidationError()
        return self.transport.consultar(request, request_id=request_id, idempotency_key=idempotency_key)

    def obtener_pdf(self, consulta_id, *, request_id=None):
        """Capacidad independiente hdc:evidencia; no consulta HDC ni PRESELECTA."""
        return self.transport.obtener_pdf(consulta_id, request_id=request_id)
