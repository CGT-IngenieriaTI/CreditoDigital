from pathlib import Path

from .models import DocumentoLegal, TipoDocumento


DEFAULT_DOCUMENTS = [
    {
        "codigo": "centrales-riesgo",
        "tipo_documento": TipoDocumento.CENTRALES,
        "titulo": "Autorizacion de consulta a centrales de riesgo",
        "descripcion": "Autorizacion para consultar informacion crediticia y financiera.",
        "orden": 1,
    },
]


def ensure_default_documents():
    DocumentoLegal.objects.exclude(codigo="centrales-riesgo").update(activo=False)
    for item in DEFAULT_DOCUMENTS:
        DocumentoLegal.objects.update_or_create(
            codigo=item["codigo"],
            defaults={**item, "activo": True},
        )


def get_active_documents():
    ensure_default_documents()
    return DocumentoLegal.objects.filter(codigo="centrales-riesgo", activo=True).order_by("orden", "titulo")


def get_document_asset_path(codigo: str) -> Path | None:
    if codigo == "centrales-riesgo":
        return Path(__file__).resolve().parents[1] / "xcore_consumo" / "assets" / "autorizacion_centrales_riesgo.pdf"
    return None
