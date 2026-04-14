from django.conf import settings
from django.db import models

from apps.utils.models import TimeStampedModel


class TipoIdentificacion(models.TextChoices):
    CC = "CC", "Cedula de ciudadania"
    CE = "CE", "Cedula de extranjeria"
    TI = "TI", "Tarjeta de identidad"
    PAS = "PAS", "Pasaporte"


class Solicitante(TimeStampedModel):
    tipo_identificacion = models.CharField(max_length=8, choices=TipoIdentificacion.choices)
    numero_identificacion = models.CharField(max_length=32)
    primer_apellido = models.CharField(max_length=80)
    fecha_expedicion = models.DateField()
    celular = models.CharField(max_length=20)
    email = models.EmailField()

    class Meta:
        unique_together = ("tipo_identificacion", "numero_identificacion")
        ordering = ("-created_at",)
        verbose_name = "Solicitante"
        verbose_name_plural = "Solicitantes"

    def __str__(self) -> str:
        return f"{self.tipo_identificacion} {self.numero_identificacion}"


class RolAsesor(models.TextChoices):
    ASESOR = "ASESOR", "Asesor"
    SUPERVISOR = "SUPERVISOR", "Supervisor"
    ADMIN = "ADMIN", "Administrador"


class PerfilAsesor(TimeStampedModel):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_asesor",
    )
    rol = models.CharField(max_length=16, choices=RolAsesor.choices, default=RolAsesor.ASESOR)

    class Meta:
        verbose_name = "Perfil asesor"
        verbose_name_plural = "Perfiles asesores"

    def __str__(self) -> str:
        return f"{self.usuario.username} - {self.rol}"


def resolve_user_role(user) -> str:
    if not user or not user.is_authenticated:
        return ""
    if hasattr(user, "perfil_asesor"):
        return user.perfil_asesor.rol
    if user.is_superuser:
        return RolAsesor.ADMIN
    if user.is_staff:
        return RolAsesor.SUPERVISOR
    return RolAsesor.ASESOR
