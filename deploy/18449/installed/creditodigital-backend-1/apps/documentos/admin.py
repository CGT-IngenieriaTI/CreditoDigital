from django.contrib import admin

from .models import AceptacionDocumento, DocumentoLegal


@admin.register(DocumentoLegal)
class DocumentoLegalAdmin(admin.ModelAdmin):
    list_display = ("codigo", "titulo", "tipo_documento", "version", "activo", "orden")
    list_filter = ("tipo_documento", "activo")
    search_fields = ("codigo", "titulo")


@admin.register(AceptacionDocumento)
class AceptacionDocumentoAdmin(admin.ModelAdmin):
    list_display = (
        "solicitud",
        "documento",
        "aceptado",
        "llego_al_final",
        "visualizacion_segundos",
        "fecha_aceptacion",
    )
    list_filter = ("aceptado", "llego_al_final", "documento__tipo_documento")
    search_fields = ("solicitud__numero_solicitud", "documento__codigo")

# Register your models here.
