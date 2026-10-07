"""Clientes S2S explicitos, independientes del pipeline y de los proveedores."""

from .transporte import GatewayTransport


class RiskGatewayHealthClient:
    def __init__(self, transport=None):
        self.transport = transport if transport is not None else GatewayTransport()

    def check(self, *, request_id=None):
        return self.transport.health(request_id=request_id)


class PreselectaGatewayClient:
    def __init__(self, transport=None):
        self.transport = transport if transport is not None else GatewayTransport()

    def consultar_preselecta(self, request, *, request_id=None, idempotency_key=None):
        return self.transport.consultar_preselecta(request,
            request_id=request_id, idempotency_key=idempotency_key)


class HDCGatewayClient:
    def __init__(self, transport=None):
        self.transport = transport if transport is not None else GatewayTransport()

    def consultar_hdc(self, request, *, request_id=None, idempotency_key=None):
        return self.transport.consultar_hdc(request,
            request_id=request_id, idempotency_key=idempotency_key)

    def obtener_pdf_hdc(self, consulta_id, *, request_id=None):
        return self.transport.obtener_pdf_hdc(consulta_id, request_id=request_id)
