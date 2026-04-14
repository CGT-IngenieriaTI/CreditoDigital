from django.contrib import admin

from .models import Solicitud


@admin.register(Solicitud)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ("numero_solicitud", "solicitante", "estado", "paso_actual", "created_at")
    list_filter = ("estado", "paso_actual", "created_at")
    search_fields = (
        "numero_solicitud",
        "solicitante__numero_identificacion",
        "solicitante__primer_apellido",
    )
    readonly_fields = ("numero_solicitud", "created_at", "updated_at")

# Register your models here.
