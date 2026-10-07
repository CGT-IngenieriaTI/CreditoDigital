"""Cliente Health explicito, independiente del pipeline y de los proveedores."""

from .transporte import GatewayTransport


class RiskGatewayHealthClient:
    def __init__(self, transport=None):
        self.transport = transport if transport is not None else GatewayTransport()

    def check(self, *, request_id=None):
        return self.transport.health(request_id=request_id)
