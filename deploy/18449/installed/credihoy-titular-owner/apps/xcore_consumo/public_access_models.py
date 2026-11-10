"""Identity access is not a credit request, consent signature or staff login."""
import uuid
from django.db import models


class EstadoAccesoModel(models.Model):
    # Identity state must also load without credit models or their audit FK.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AccesoPublicoCredihoy(EstadoAccesoModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    navegador_hmac = models.CharField(max_length=64)
    identidad_hmac = models.CharField(max_length=64, db_index=True)
    peticion_hmac = models.CharField(max_length=64)
    datos_cifrados = models.TextField(blank=True)
    estado = models.CharField(max_length=16, default='PENDIENTE')
    motivo_interno = models.CharField(max_length=64, blank=True)
    verification_sid = models.CharField(max_length=128, blank=True)
    verification_check_sid = models.CharField(max_length=128, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    expira_at = models.DateTimeField()
    verificado_at = models.DateTimeField(null=True, blank=True)
    sesion_expira_at = models.DateTimeField(null=True, blank=True)
    revocado = models.BooleanField(default=False)

    class Meta:
        permissions = [('revocar_accesopublicocredihoy', 'Puede revocar accesos publicos CrediHoy')]


class LimiteAccesoCredihoy(models.Model):
    # Keys are keyed hashes, never documents, IPs or contact addresses.
    clave = models.CharField(max_length=64, primary_key=True)
    ventana = models.DateTimeField()
    contador = models.PositiveIntegerField(default=0)


class EventoAccesoCredihoy(EstadoAccesoModel):
    acceso = models.ForeignKey(AccesoPublicoCredihoy, on_delete=models.PROTECT)
    evento = models.CharField(max_length=64)
    datos = models.JSONField(default=dict)


class OperacionVerifyCredihoy(EstadoAccesoModel):
    id = models.UUIDField(primary_key=True, editable=False)
    acceso = models.ForeignKey(AccesoPublicoCredihoy, on_delete=models.PROTECT)
    accion = models.CharField(max_length=16)
    peticion_hmac = models.CharField(max_length=64)
    estado = models.CharField(max_length=16, default='RESERVADA')
    resultado = models.JSONField(default=dict)
