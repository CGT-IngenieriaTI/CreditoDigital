from django.contrib import admin

from .models import (
    ConsentimientoConsumo,
    ConfiguracionAgenciaCanal,
    ConfiguracionGastosFamiliares,
    ConfiguracionRegresion,
    ConsultaCoreOracle,
    ConsultaEstamentoOracle,
    EvaluacionConsumo,
    OtpChallenge,
    SolicitudConsumo,
    TasaInteresConsumo,
)


@admin.register(SolicitudConsumo)
class SolicitudConsumoAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "estado", "oracle_consultado", "created_at")
    search_fields = ("solicitud__numero_solicitud", "solicitud__solicitante__numero_identificacion")
    list_filter = ("estado", "oracle_consultado")


@admin.register(EvaluacionConsumo)
class EvaluacionConsumoAdmin(admin.ModelAdmin):
    list_display = (
        "solicitud",
        "puntaje_xcore",
        "perfil_riesgo",
        "perfil_credito",
        "decision_final",
        "estamento",
        "created_at",
    )
    search_fields = ("solicitud__numero_solicitud", "solicitud__solicitante__numero_identificacion")
    list_filter = ("perfil_riesgo", "decision_final", "capacidad_pago_final")


@admin.register(ConsultaCoreOracle)
class ConsultaCoreOracleAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "estado", "created_at")
    search_fields = ("solicitud__numero_solicitud", "solicitud__solicitante__numero_identificacion")


@admin.register(ConsultaEstamentoOracle)
class ConsultaEstamentoOracleAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "estado", "resultado", "created_at")
    search_fields = ("solicitud__numero_solicitud", "solicitud__solicitante__numero_identificacion")


@admin.register(OtpChallenge)
class OtpChallengeAdmin(admin.ModelAdmin):
    list_display = (
        "solicitud",
        "canal",
        "provider",
        "estado",
        "transaction_uuid",
        "intentos",
        "enviado_at",
        "verificado_at",
    )
    search_fields = (
        "solicitud__numero_solicitud",
        "solicitud__solicitante__numero_identificacion",
        "transaction_uuid",
        "verification_sid",
        "verification_check_sid",
    )
    list_filter = ("canal", "provider", "estado", "validation_result")
    readonly_fields = (
        "destination_masked",
        "destination_full_encrypted",
        "otp_code_encrypted",
        "otp_hash",
        "otp_masked",
        "verification_sid",
        "verification_check_sid",
        "validation_result",
        "transaction_uuid",
        "ip_address",
        "user_agent",
    )


@admin.register(ConsentimientoConsumo)
class ConsentimientoConsumoAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "version", "canal", "fecha_aceptacion", "tipo_firma", "pdf_consentimiento")
    search_fields = ("solicitud__numero_solicitud", "solicitud__solicitante__numero_identificacion", "text_hash")
    list_filter = ("version", "canal", "tipo_firma")
    readonly_fields = ("text_hash", "evidencia", "pdf_consentimiento")


admin.site.register(ConfiguracionRegresion)
admin.site.register(ConfiguracionGastosFamiliares)
admin.site.register(ConfiguracionAgenciaCanal)
admin.site.register(TasaInteresConsumo)

# Catalogo nativo CrediHoy: registro de solo lectura.
from .tasas_models import PerfilTasasCredihoy, TasaComercialCredihoy, AsignacionTarifaCredihoy, EventoTasasCredihoy


class CatalogoCredihoySoloLecturaAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class CondicionCredihoyAdmin(CatalogoCredihoySoloLecturaAdmin):
    date_hierarchy = 'creado_at'
    list_filter = ('linea','documento','version_documento')
    search_fields = ('documento','version_documento','referencia','soporte_sha256')
    list_select_related = ('perfil','responsable')

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('eventos__responsable')

    @admin.display(description='Estado')
    def estado_actual(self,obj):
        from .services.credihoy_v2.unico.tasas import estado
        return estado(obj)

    @admin.display(description='Inicio efectivo')
    def inicio_efectivo(self,obj):
        from .services.credihoy_v2.unico.tasas import inicio
        return inicio(obj)

    @admin.display(description='Fin exclusivo')
    def fin_efectivo(self,obj):
        from .services.credihoy_v2.unico.tasas import fin
        return fin(obj)

    @admin.display(description='Vigente al corte',boolean=True)
    def vigente_hoy(self,obj):
        from .services.credihoy_v2.unico.tasas import vigente,hoy
        return vigente(obj,hoy())


class TasaComercialCredihoyAdmin(CondicionCredihoyAdmin):
    list_display = ('linea','numero_version','tasa_namv','tasa_ea','tasa_mv_publicada',
        'inicio_efectivo','fin_efectivo','estado_actual','vigente_hoy','documento','version_documento','referencia')


class AsignacionTarifaCredihoyAdmin(CondicionCredihoyAdmin):
    list_display = ('linea','alcance','tipo_nomina','medio_pago','numero_version',
        'inicio_efectivo','fin_efectivo','estado_actual','vigente_hoy','documento','referencia')


class EventoTasasCredihoyAdmin(CatalogoCredihoySoloLecturaAdmin):
    list_display = ('creado_at','accion','tasa','asignacion','responsable','referencia_autorizacion')
    list_filter = ('accion',)
    search_fields = ('referencia_autorizacion',)
    list_select_related = ('tasa','asignacion','responsable')
    date_hierarchy = 'creado_at'


admin.site.register(PerfilTasasCredihoy,CatalogoCredihoySoloLecturaAdmin)
admin.site.register(TasaComercialCredihoy,TasaComercialCredihoyAdmin)
admin.site.register(AsignacionTarifaCredihoy,AsignacionTarifaCredihoyAdmin)
admin.site.register(EventoTasasCredihoy,EventoTasasCredihoyAdmin)
