from django.db import models

from apps.utils.models import TimeStampedModel


class PreselectaConsulta(TimeStampedModel):
    solicitud = models.OneToOneField(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="preselecta_consulta",
    )
    estado = models.CharField(max_length=24, default="PENDIENTE")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    preaprobado = models.BooleanField(default=False)
    score = models.PositiveIntegerField(null=True, blank=True)
    mensaje = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Consulta Preselecta"
        verbose_name_plural = "Consultas Preselecta"

    def __str__(self) -> str:
        return f"{self.solicitud.numero_solicitud} - {self.estado}"

# Create your models here.
