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

def _permiso_documental(actor, permiso):
    from django.core.exceptions import PermissionDenied
    if (not actor or not actor.is_authenticated or not actor.is_active or not actor.is_staff
            or not actor.has_perm('solicitudes.view_solicitud')
            or not actor.has_perm('documentos.' + permiso)):
        raise PermissionDenied


def _expediente_documental(solicitud_id, actor, *, bloquear=False):
    from django.core.exceptions import PermissionDenied
    from apps.solicitudes.models import Solicitud
    query = Solicitud.objects.all()
    if bloquear:
        query = query.select_for_update()
    if not actor.is_superuser:
        query = query.filter(asesor=actor)
    try:
        return query.get(pk=solicitud_id)
    except Solicitud.DoesNotExist:
        raise PermissionDenied from None

