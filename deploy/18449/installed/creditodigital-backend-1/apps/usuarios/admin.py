from django.contrib import admin

from .models import PerfilAsesor, Solicitante


@admin.register(Solicitante)
class SolicitanteAdmin(admin.ModelAdmin):
    list_display = ("tipo_identificacion", "numero_identificacion", "primer_apellido", "email")
    search_fields = ("numero_identificacion", "primer_apellido", "email")
    list_filter = ("tipo_identificacion",)


@admin.register(PerfilAsesor)
class PerfilAsesorAdmin(admin.ModelAdmin):
    list_display = ("usuario", "rol", "created_at")
    search_fields = ("usuario__username", "usuario__first_name", "usuario__last_name", "usuario__email")
    list_filter = ("rol",)
