"""Contrato Health oficial; no certifica disponibilidad de proveedores."""

from dataclasses import dataclass
from uuid import UUID

from .errores import GatewayInvalidResponse, GatewayValidationError


def validar_request_id(valor):
    try:
        if type(valor) is not str or str(UUID(valor)) != valor.lower():
            raise ValueError
        return valor
    except (ValueError, TypeError, AttributeError):
        raise GatewayValidationError() from None


@dataclass(frozen=True, slots=True)
class HealthResponse:
    api_version: str
    status: str
    provider_checks: bool
    request_id: str

    def __post_init__(self):
        try:
            if (type(self.api_version) is not str or self.api_version != "v1"
                    or type(self.status) is not str or self.status != "ready"
                    or type(self.provider_checks) is not bool or self.provider_checks is not False):
                raise ValueError
            validar_request_id(self.request_id)
        except (ValueError, GatewayValidationError):
            raise GatewayInvalidResponse() from None

    @classmethod
    def desde_payload(cls, payload, *, request_id, header_request_id=None):
        try:
            if type(payload) is not dict:
                raise ValueError
            resultado = cls(api_version=payload["apiVersion"], status=payload["status"],
                provider_checks=payload["providerChecks"], request_id=payload["requestId"])
            if resultado.request_id != request_id or (
                    header_request_id is not None and header_request_id != request_id):
                raise ValueError
            return resultado
        except (KeyError, TypeError, ValueError, GatewayInvalidResponse):
            raise GatewayInvalidResponse(status=200, request_id=request_id) from None

    def a_dict(self):
        return {"httpStatus": 200, "apiVersion": self.api_version, "status": self.status,
                "providerChecks": self.provider_checks, "requestId": self.request_id}
