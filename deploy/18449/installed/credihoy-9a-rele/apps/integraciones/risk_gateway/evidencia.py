"""Evidencia PDF efimera, separada del contrato financiero y sin persistencia."""

from dataclasses import dataclass, field


MAX_PDF_BYTES = 20 * 1024 * 1024
PDF_PATH = "/api/internal/v1/hdc/consultas/{consulta_id}/pdf/"
PDF_SCOPE = "hdc:evidencia"


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenciaPDFHDC:
    consulta_id: str
    request_id: str
    contenido: bytes = field(repr=False)
    content_type: str = field(default="application/pdf", init=False)

    def __post_init__(self):
        from uuid import UUID

        for valor in (self.consulta_id, self.request_id):
            if type(valor) is not str or str(UUID(valor)) != valor.lower():
                raise ValueError("Referencia PDF invalida")
        if type(self.contenido) is not bytes or not 0 < len(self.contenido) <= MAX_PDF_BYTES:
            raise ValueError("Tamano PDF invalido")


def obtener_pdf_hdc(consulta_id, *, request_id=None, client=None) -> EvidenciaPDFHDC:
    """Invocacion explicita futura; requiere scope hdc:evidencia en el servidor."""
    from .clientes import HDCGatewayClient

    gateway = client if client is not None else HDCGatewayClient()
    return gateway.obtener_pdf(consulta_id, request_id=request_id)
