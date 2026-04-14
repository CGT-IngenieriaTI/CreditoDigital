from django.db import models

from apps.utils.models import TimeStampedModel


class ResultadoDecision(models.TextChoices):
    APROBADO = "APROBADO", "Aprobado"
    RECHAZADO = "RECHAZADO", "Rechazado"
    REVISION = "REVISION", "Revision"


class DecisionFinal(TimeStampedModel):
    solicitud = models.OneToOneField(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="decision_final",
    )
    resultado = models.CharField(max_length=16, choices=ResultadoDecision.choices)
    mensaje = models.CharField(max_length=255)
    observaciones = models.TextField(blank=True)
    monto_aprobado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    plazo_aprobado = models.PositiveSmallIntegerField(null=True, blank=True)
    tasa_interes = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    detalle = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Decision final"
        verbose_name_plural = "Decisiones finales"

    def __str__(self) -> str:
        return f"{self.solicitud.numero_solicitud} - {self.resultado}"

# Create your models here.
