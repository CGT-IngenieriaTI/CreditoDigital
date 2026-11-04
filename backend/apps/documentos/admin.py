from django.contrib import admin

from .models import (AceptacionDocumento, DocumentoLegal, EventoDocumental,
    PlanPaqueteDocumental, VersionDocumental)


@admin.register(DocumentoLegal)
class DocumentoLegalAdmin(admin.ModelAdmin):
    list_display = ("codigo", "titulo", "tipo_documento", "version", "activo", "orden")
    list_filter = ("tipo_documento", "activo")
    search_fields = ("codigo", "titulo")


@admin.register(AceptacionDocumento)
class AceptacionDocumentoAdmin(admin.ModelAdmin):
    readonly_fields = ('evidencia_version',)
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

    def get_queryset(self, request):
        query = super().get_queryset(request).select_related('solicitud', 'documento')
        if not request.user.has_perm('solicitudes.view_solicitud'):
            return query.none()
        return query if request.user.is_superuser else query.filter(solicitud__asesor=request.user)

    def has_view_permission(self, request, obj=None):
        return (request.user.has_perm('documentos.view_aceptaciondocumento')
            and request.user.has_perm('solicitudes.view_solicitud')
            and (obj is None or request.user.is_superuser or obj.solicitud.asesor_id == request.user.pk))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions=super().get_actions(request);actions.pop('delete_selected',None)
        return actions

@admin.register(VersionDocumental)
class VersionDocumentalAdmin(admin.ModelAdmin):
    list_display = ('documento', 'version', 'sha256', 'created_at')
    search_fields = ('documento__codigo', 'version', 'sha256')
    readonly_fields = ('documento', 'version', 'actor', 'evidencia', 'sha256', 'created_at')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('documentos.view_versiondocumental')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


@admin.register(EventoDocumental)
class EventoDocumentalAdmin(VersionDocumentalAdmin):
    list_display = ('solicitud', 'tipo', 'version_documental', 'created_at')
    list_filter = ('tipo', 'version_documental__documento__codigo')
    search_fields = ('solicitud__numero_solicitud', 'version_documental__documento__codigo')
    readonly_fields = ('solicitud', 'version_documental', 'tipo', 'presentacion',
        'clave_idempotencia', 'actor', 'evidencia', 'sha256', 'created_at')
    list_per_page = 20

    def get_queryset(self, request):
        query = super().get_queryset(request).select_related('solicitud', 'version_documental__documento')
        if not request.user.has_perm('solicitudes.view_solicitud'):
            return query.none()
        return query if request.user.is_superuser else query.filter(solicitud__asesor=request.user)

    def has_view_permission(self, request, obj=None):
        return (request.user.has_perm('documentos.view_eventodocumental')
            and request.user.has_perm('solicitudes.view_solicitud')
            and (obj is None or request.user.is_superuser or obj.solicitud.asesor_id == request.user.pk))


@admin.register(PlanPaqueteDocumental)
class PlanPaqueteDocumentalAdmin(EventoDocumentalAdmin):
    list_display = ('solicitud', 'codigo', 'version', 'etapa', 'created_at')
    list_filter = ('etapa', 'codigo')
    search_fields = ('solicitud__numero_solicitud', 'codigo', 'sha256')
    readonly_fields = ('solicitud', 'codigo', 'version', 'etapa', 'clave_idempotencia',
        'actor', 'evidencia', 'sha256', 'created_at')

    def get_queryset(self, request):
        query = admin.ModelAdmin.get_queryset(self, request).select_related('solicitud')
        if not request.user.has_perm('solicitudes.view_solicitud'):
            return query.none()
        return query if request.user.is_superuser else query.filter(solicitud__asesor=request.user)

    def has_view_permission(self, request, obj=None):
        return (request.user.has_perm('documentos.view_planpaquetedocumental')
            and request.user.has_perm('solicitudes.view_solicitud')
            and (obj is None or request.user.is_superuser or obj.solicitud.asesor_id == request.user.pk))
