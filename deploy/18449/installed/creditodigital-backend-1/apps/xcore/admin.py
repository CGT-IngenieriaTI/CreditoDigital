from django.contrib import admin

from .models import XcoreConsulta


@admin.register(XcoreConsulta)
class XcoreConsultaAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "estado", "resultado", "created_at")
    list_filter = ("estado", "resultado", "created_at")
    search_fields = ("solicitud__numero_solicitud",)

# Register your models here.
