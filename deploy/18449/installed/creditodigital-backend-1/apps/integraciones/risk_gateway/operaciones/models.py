import uuid

from django.db import models

from apps.utils.models import TimeStampedModel


class ProveedorOperacion(models.TextChoices):
    PRESELECTA = "PRESELECTA", "PRESELECTA"
    HDC = "HDC", "HDC"


class EstadoOperacion(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    EN_EJECUCION = "EN_EJECUCION", "En ejecucion"
    COMPLETADA = "COMPLETADA", "Completada"
    SIN_INFORMACION = "SIN_INFORMACION", "Sin informacion"
    PENDIENTE_CONCILIACION = "PENDIENTE_CONCILIACION", "Pendiente conciliacion"
    ERROR_DEFINITIVO = "ERROR_DEFINITIVO", "Error definitivo"


class OperacionGateway(TimeStampedModel):
    """Una reserva inmutable por solicitud/capacidad; nunca guarda payloads completos."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey("solicitudes.Solicitud", on_delete=models.PROTECT,
                                  related_name="operaciones_risk_gateway")
    consentimiento = models.ForeignKey("xcore_consumo.ConsentimientoConsumo", on_delete=models.PROTECT)
    proveedor = models.CharField(max_length=12, choices=ProveedorOperacion.choices)
    identidad_hmac = models.CharField(max_length=64)
    consentimiento_hmac = models.CharField(max_length=64)
    request_hmac = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=80, unique=True)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    estado = models.CharField(max_length=24, choices=EstadoOperacion.choices, default=EstadoOperacion.PENDIENTE)
    inicio_at = models.DateTimeField(null=True)
    fin_at = models.DateTimeField(null=True)
    codigo_error = models.CharField(max_length=64, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True)
    consulta_id = models.UUIDField(null=True)
    version_contrato = models.CharField(max_length=40, blank=True)
    recibo_hmac = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitud", "proveedor"], name="rg_ops_solicitud_proveedor_uq"),
            models.CheckConstraint(condition=models.Q(proveedor__in=ProveedorOperacion.values), name="rg_ops_proveedor_ck"),
            models.CheckConstraint(condition=models.Q(estado__in=EstadoOperacion.values), name="rg_ops_estado_ck"),
        ]

    def __str__(self):
        return f"OperacionGateway({self.pk}, {self.proveedor}, {self.estado})"
