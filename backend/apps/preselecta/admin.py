from django.contrib import admin

from .models import PreselectaConsulta


@admin.register(PreselectaConsulta)
class PreselectaConsultaAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "estado", "preaprobado", "score", "created_at")
    list_filter = ("estado", "preaprobado", "created_at")
    search_fields = ("solicitud__numero_solicitud",)

# Register your models here.
