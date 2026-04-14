from django.db import models

from apps.utils.models import TimeStampedModel


class HistorialPagoConsulta(TimeStampedModel):
    solicitud = models.OneToOneField(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="historial_pago_consulta",
    )
    estado = models.CharField(max_length=24, default="PENDIENTE")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    xml_payload = models.TextField(blank=True)
    soap_request_xml = models.TextField(blank=True)
    score_pago = models.PositiveIntegerField(null=True, blank=True)
    mora_maxima = models.PositiveIntegerField(default=0)
    categoria = models.CharField(max_length=8, blank=True)
    resumen = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Consulta historial de pago"
        verbose_name_plural = "Consultas historial de pago"

    def __str__(self) -> str:
        return f"{self.solicitud.numero_solicitud} - {self.estado}"

# Create your models here.
