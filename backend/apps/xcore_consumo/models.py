from django.conf import settings
from django.db import models

from apps.utils.models import TimeStampedModel


class CanalOtp(models.TextChoices):
    SMS = "SMS", "SMS"
    EMAIL = "EMAIL", "Email"


class EstadoOtp(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    ENVIADA = "ENVIADA", "Enviada"
    VALIDADA = "VALIDADA", "Validada"
    EXPIRADA = "EXPIRADA", "Expirada"
    BLOQUEADA = "BLOQUEADA", "Bloqueada"
    ERROR_ENVIO = "ERROR_ENVIO", "Error de envio"
    CANCELADA = "CANCELADA", "Cancelada"


class EstadoSolicitudConsumo(models.TextChoices):
    BORRADOR = "BORRADOR", "Borrador"
    OTP_PENDIENTE = "OTP_PENDIENTE", "OTP pendiente"
    OTP_VALIDADA = "OTP_VALIDADA", "OTP validada"
    CONSENTIMIENTO_PENDIENTE = "CONSENTIMIENTO_PENDIENTE", "Consentimiento pendiente"
    CONSENTIMIENTO_FIRMADO = "CONSENTIMIENTO_FIRMADO", "Consentimiento firmado"
    CORE_CONSULTADO = "CORE_CONSULTADO", "Core consultado"
    FORMULARIO_XCORE_OK = "FORMULARIO_XCORE_OK", "Formulario guardado"
    PROCESANDO = "PROCESANDO", "Procesando"
    FINALIZADO = "FINALIZADO", "Finalizado"
    ERROR = "ERROR", "Error"


class SolicitudConsumo(TimeStampedModel):
    solicitud = models.OneToOneField(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="consumo_detail",
    )
    estado = models.CharField(
        max_length=24,
        choices=EstadoSolicitudConsumo.choices,
        default=EstadoSolicitudConsumo.OTP_PENDIENTE,
    )
    oracle_consultado = models.BooleanField(default=False)
    documentos_autorizados = models.BooleanField(default=False)
    selected_hc2_keys = models.JSONField(default=list, blank=True)
    core_data = models.JSONField(default=dict, blank=True)
    orchestration_data = models.JSONField(default=dict, blank=True)
    form_data = models.JSONField(default=dict, blank=True)
    ultimo_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Solicitud consumo"
        verbose_name_plural = "Solicitudes consumo"

    def __str__(self):
        return self.solicitud.numero_solicitud


class OtpChallenge(TimeStampedModel):
    solicitud = models.ForeignKey(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="otp_challenges",
    )
    canal = models.CharField(max_length=16, choices=CanalOtp.choices, default=CanalOtp.SMS)
    provider = models.CharField(max_length=32, blank=True)
    destino = models.CharField(max_length=255, blank=True)
    destination_masked = models.CharField(max_length=255, blank=True)
    destination_full_encrypted = models.TextField(blank=True)
    codigo = models.CharField(max_length=12, blank=True)
    otp_code_encrypted = models.TextField(blank=True)
    otp_hash = models.CharField(max_length=255, blank=True)
    otp_masked = models.CharField(max_length=32, blank=True)
    estado = models.CharField(max_length=16, choices=EstadoOtp.choices, default=EstadoOtp.PENDIENTE)
    enviado_at = models.DateTimeField(null=True, blank=True)
    expira_at = models.DateTimeField(null=True, blank=True)
    verificado_at = models.DateTimeField(null=True, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    max_intentos = models.PositiveSmallIntegerField(default=3)
    verification_sid = models.CharField(max_length=128, blank=True)
    verification_check_sid = models.CharField(max_length=128, blank=True)
    validation_result = models.CharField(max_length=64, blank=True)
    transaction_uuid = models.CharField(max_length=64, blank=True)
    blocked_until = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    ultimo_error = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "OTP de solicitud"
        verbose_name_plural = "OTPs de solicitud"

    def __str__(self):
        return f"{self.solicitud.numero_solicitud} - {self.canal}"


class ConsentimientoConsumo(TimeStampedModel):
    solicitud = models.OneToOneField(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="consentimiento_consumo",
    )
    otp = models.ForeignKey(
        OtpChallenge,
        on_delete=models.PROTECT,
        related_name="consentimientos",
        null=True,
        blank=True,
    )
    version = models.CharField(max_length=32, default="2026.03")
    aceptado = models.BooleanField(default=True)
    firmado = models.BooleanField(default=True)
    canal = models.CharField(max_length=16, choices=CanalOtp.choices, default=CanalOtp.SMS)
    fecha_aceptacion = models.DateTimeField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    text_snapshot = models.TextField()
    text_hash = models.CharField(max_length=128)
    tipo_firma = models.CharField(max_length=32, default="ACEPTACION_OTP")
    evidencia = models.JSONField(default=dict, blank=True)
    pdf_consentimiento = models.FileField(upload_to="consentimientos/", blank=True)

    class Meta:
        verbose_name = "Consentimiento consumo"
        verbose_name_plural = "Consentimientos consumo"

    def __str__(self):
        return f"{self.solicitud.numero_solicitud} - {self.version}"


class ConsultaCoreOracle(TimeStampedModel):
    solicitud = models.ForeignKey(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="consultas_core_oracle",
    )
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    estado = models.CharField(max_length=24, default="PENDIENTE")
    mensaje = models.CharField(max_length=255, blank=True)


class ConsultaEstamentoOracle(TimeStampedModel):
    solicitud = models.ForeignKey(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="consultas_estamento_oracle",
    )
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    estado = models.CharField(max_length=24, default="PENDIENTE")
    resultado = models.CharField(max_length=16, blank=True)
    tipo_familiar = models.CharField(max_length=255, blank=True)
    mensaje = models.CharField(max_length=255, blank=True)


class EvaluacionConsumo(TimeStampedModel):
    solicitud = models.OneToOneField(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="evaluacion_consumo",
    )
    input_snapshot = models.JSONField(default=dict, blank=True)
    integraciones_snapshot = models.JSONField(default=dict, blank=True)
    resultados = models.JSONField(default=dict, blank=True)
    puntaje_xcore = models.FloatField(default=0)
    perfil_riesgo = models.CharField(max_length=64, blank=True)
    perfil_credito = models.CharField(max_length=64, blank=True)
    capacidad_pago_final = models.CharField(max_length=64, blank=True)
    decision_final = models.CharField(max_length=64, blank=True)
    estamento = models.CharField(max_length=100, blank=True)
    tiene_novedad = models.BooleanField(default=False)
    novedad_descripcion = models.CharField(max_length=255, blank=True)
    monto_max_posible = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    valor_cuota = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    vida_deudores = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    pdf_generado = models.BooleanField(default=False)


class ConsultaAsociadoIntento(TimeStampedModel):
    solicitud = models.ForeignKey(
        "solicitudes.Solicitud",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultas_asociado",
    )
    asesor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultas_asociado_consumo",
    )
    tipo_identificacion = models.CharField(max_length=8)
    numero_identificacion = models.CharField(max_length=32)
    oracle_ok = models.BooleanField(default=False)
    preselecta_ok = models.BooleanField(default=False)
    datacredito_ok = models.BooleanField(default=False)
    puede_continuar = models.BooleanField(default=False)
    bloqueado = models.BooleanField(default=False)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    mensaje = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Intento consulta asociado"
        verbose_name_plural = "Intentos consulta asociado"

    def __str__(self):
        return f"{self.numero_identificacion} - {'OK' if self.puede_continuar else 'FALLA'}"


class ConfiguracionRegresion(models.Model):
    parametro = models.CharField(max_length=255)
    nivel = models.CharField(max_length=255)
    estimacion = models.DecimalField(max_digits=20, decimal_places=4)

    class Meta:
        unique_together = ("parametro", "nivel")
        verbose_name = "Configuración regresión"
        verbose_name_plural = "Configuraciones regresión"

    def __str__(self):
        return f"{self.parametro} / {self.nivel}"


class ConfiguracionGastosFamiliares(models.Model):
    salario_minimo = models.IntegerField()
    cant_personasacargo = models.IntegerField()
    porcentaje = models.DecimalField(max_digits=6, decimal_places=4)
    zona = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        unique_together = ("salario_minimo", "cant_personasacargo", "zona")
        verbose_name = "Configuración gastos familiares"
        verbose_name_plural = "Configuraciones gastos familiares"


class ConfiguracionAgenciaCanal(models.Model):
    canal = models.CharField(max_length=255, unique=True)
    codigo = models.CharField(max_length=255, blank=True)
    puntos = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Configuración agencia canal"
        verbose_name_plural = "Configuraciones agencia canal"


class TasaInteresConsumo(models.Model):
    linea_credito = models.CharField(max_length=100)
    forma_pago = models.CharField(max_length=50)
    sub_categoria = models.CharField(max_length=100, default="General")
    categoria_riesgo = models.CharField(max_length=10, default="NA")
    tasa_ea = models.FloatField()

    class Meta:
        unique_together = ("linea_credito", "forma_pago", "sub_categoria", "categoria_riesgo")
        verbose_name = "Tasa de interés consumo"
        verbose_name_plural = "Tasas de interés consumo"

    def __str__(self):
        return (
            f"{self.linea_credito} - {self.forma_pago} - "
            f"{self.sub_categoria} - {self.categoria_riesgo}: {self.tasa_ea}%"
        )
