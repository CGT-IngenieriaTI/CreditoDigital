"""PDF HDC efimero: sin almacenamiento, rutas publicas ni consulta implicita."""

from dataclasses import dataclass, field

from .contratos import validar_request_id


MAX_PDF_BYTES = 20 * 1024 * 1024
PDF_PATH = "/api/internal/v1/hdc/consultas/{consulta_id}/pdf/"


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenciaPDFHDC:
    consulta_id: str
    request_id: str
    contenido: bytes = field(repr=False)
    content_type: str = field(default="application/pdf", init=False)

    def __post_init__(self):
        validar_request_id(self.consulta_id)
        validar_request_id(self.request_id)
        if (type(self.contenido) is not bytes or not self.contenido.startswith(b"%PDF-")
                or not 0 < len(self.contenido) <= MAX_PDF_BYTES):
            raise ValueError("Evidencia PDF invalida")
