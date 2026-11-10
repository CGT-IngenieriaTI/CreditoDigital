import uuid

from django.conf import settings
from django.db import connections, models, router
from django.db import transaction

from apps.utils.models import TimeStampedModel


class EstadoSolicitud(models.TextChoices):
    INICIADA = "INICIADA", "Iniciada"
    AUTORIZADA = "AUTORIZADA", "Autorizada"
    PRESELECTA_OK = "PRESELECTA_OK", "Preselecta OK"
    HISTORIAL_OK = "HISTORIAL_OK", "Historial OK"
    ENVIADA_XCORE = "ENVIADA_XCORE", "Enviada a XCORE"
    FINALIZADA = "FINALIZADA", "Finalizada"
    OTP_PENDIENTE = "OTP_PENDIENTE", "OTP pendiente"
    OTP_VALIDADA = "OTP_VALIDADA", "OTP validada"
    CONSENTIMIENTO_PENDIENTE = "CONSENTIMIENTO_PENDIENTE", "Consentimiento pendiente"
    CONSENTIMIENTO_FIRMADO = "CONSENTIMIENTO_FIRMADO", "Consentimiento firmado"
    CORE_CONSULTADO = "CORE_CONSULTADO", "Core consultado"
    FORMULARIO_XCORE_OK = "FORMULARIO_XCORE_OK", "Formulario XCORE OK"
    PROCESANDO = "PROCESANDO", "Procesando"
    ERROR = "ERROR", "Error"


class Solicitud(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consecutivo = models.PositiveIntegerField(unique=True, null=True, blank=True)
    numero_solicitud = models.CharField(max_length=32, unique=True, blank=True)
    solicitante = models.ForeignKey(
        "usuarios.Solicitante",
        on_delete=models.PROTECT,
        related_name="solicitudes",
    )
    asesor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solicitudes_consumo",
    )
    estado = models.CharField(
        max_length=24,
        choices=EstadoSolicitud.choices,
        default=EstadoSolicitud.INICIADA,
    )
    paso_actual = models.CharField(max_length=32, default="formulario")
    producto = models.CharField(max_length=32, default="CONSUMO")
    monto_solicitado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    plazo_solicitado = models.PositiveSmallIntegerField(default=24)
    metadatos = models.JSONField(default=dict, blank=True)
    ultimo_error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Solicitud"
        verbose_name_plural = "Solicitudes"

    def save(self, *args, **kwargs):
        if self.consecutivo and not self.numero_solicitud:
            self.numero_solicitud = f"CD{self.consecutivo}"

        if self.consecutivo and self.numero_solicitud:
            return super().save(*args, **kwargs)

        alias = kwargs.get('using') or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=alias):
            if not self.consecutivo:
                # Locking the last row cannot serialize an empty table or a new maximum.
                if connections[alias].vendor == 'postgresql':
                    with connections[alias].cursor() as cursor:
                        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int.from_bytes(b'CDNUMERO', 'big')])
                last_request = (
                    Solicitud.objects.using(alias).select_for_update()
                    .exclude(consecutivo__isnull=True)
                    .order_by("-consecutivo")
                    .first()
                )
                self.consecutivo = (last_request.consecutivo if last_request else 0) + 1
            if not self.numero_solicitud:
                self.numero_solicitud = f"CD{self.consecutivo}"
            return super().save(*args, **kwargs)

    @property
    def latest_otp_challenge(self):
        return self.otp_challenges.order_by("-created_at").first()

    @property
    def active_otp_challenge(self):
        return self.otp_challenges.exclude(estado="CANCELADA").order_by("-created_at").first()

    def __str__(self) -> str:
        return self.numero_solicitud

class ProgresoTemporalCredihoy(models.Model):
    """Unsent, encrypted progress; never an institutional dossier or 9A identity."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    titular_hmac = models.CharField(max_length=64, unique=True)
    version = models.PositiveIntegerField(default=1)
    datos_cifrados = models.TextField()
    operacion_id = models.UUIDField()
    operacion_hmac = models.CharField(max_length=64)
    actividad_at = models.DateTimeField()
    vence_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'solicitudes_progresotemporalcredihoy'
        constraints = [models.CheckConstraint(condition=models.Q(version__gte=1), name='ch_progreso_version_positiva')]
