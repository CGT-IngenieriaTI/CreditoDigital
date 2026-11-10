from django.contrib import admin

from .models import HistorialPagoConsulta


@admin.register(HistorialPagoConsulta)
class HistorialPagoConsultaAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "estado", "score_pago", "mora_maxima", "categoria")
    list_filter = ("estado", "categoria", "created_at")
    search_fields = ("solicitud__numero_solicitud",)

# Register your models here.
