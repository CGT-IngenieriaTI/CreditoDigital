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
