from django.db import models

from apps.utils.models import TimeStampedModel


class XcoreConsulta(TimeStampedModel):
    solicitud = models.OneToOneField(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="xcore_consulta",
    )
    estado = models.CharField(max_length=24, default="PENDIENTE")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    resultado = models.CharField(max_length=16)
    mensaje = models.CharField(max_length=255)

    class Meta:
        verbose_name = "Consulta XCORE"
        verbose_name_plural = "Consultas XCORE"

    def __str__(self) -> str:
        return f"{self.solicitud.numero_solicitud} - {self.resultado}"

# Create your models here.
