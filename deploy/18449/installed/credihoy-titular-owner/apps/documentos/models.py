import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.utils.models import TimeStampedModel


class TipoDocumento(models.TextChoices):
    DATOS = "DATOS", "Tratamiento de datos"
    CENTRALES = "CENTRALES", "Consulta centrales"
    TERMINOS = "TERMINOS", "Terminos y condiciones"


class DocumentoLegal(TimeStampedModel):
    codigo = models.SlugField(max_length=64, unique=True)
    tipo_documento = models.CharField(max_length=16, choices=TipoDocumento.choices)
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField()
    version = models.CharField(max_length=16, default="1.0")
    orden = models.PositiveSmallIntegerField(default=1)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ("orden", "titulo")
        verbose_name = "Documento legal"
        verbose_name_plural = "Documentos legales"

    def __str__(self) -> str:
        return self.titulo


class AceptacionDocumento(TimeStampedModel):
    solicitud = models.ForeignKey(
        "solicitudes.Solicitud",
        on_delete=models.CASCADE,
        related_name="aceptaciones_documento",
    )
    documento = models.ForeignKey(
        DocumentoLegal,
        on_delete=models.CASCADE,
        related_name="aceptaciones",
    )
    aceptado = models.BooleanField(default=True)
    fecha_aceptacion = models.DateTimeField()
    visualizacion_segundos = models.PositiveIntegerField(default=0)
    llego_al_final = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    evidencia_version = models.JSONField(null=True, blank=True, editable=False)

    class Meta:
        unique_together = ("solicitud", "documento")
        ordering = ("-fecha_aceptacion",)
        verbose_name = "Aceptacion de documento"
        verbose_name_plural = "Aceptaciones de documento"

    def __str__(self) -> str:
        return f"{self.solicitud.numero_solicitud} - {self.documento.codigo}"

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk:
            fields=('solicitud_id','documento_id','aceptado','fecha_aceptacion','evidencia_version')
            previous=type(self).objects.filter(pk=self.pk).values(*fields).first()
            if previous:
                if previous['evidencia_version'] is None and self.evidencia_version is not None:
                    raise ValidationError('ACEPTACION_HISTORICA_NO_RECONSTRUIR')
                if previous['evidencia_version'] is not None and any(
                        previous[field]!=getattr(self,field) for field in fields):
                    raise ValidationError('ACEPTACION_VERSIONADA_NO_SOBRESCRIBIR')
        return super().save(*args, **kwargs)

class EvidenciaInmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('EVIDENCIA_DOCUMENTAL_INMUTABLE')

    def delete(self):
        raise ValidationError('EVIDENCIA_DOCUMENTAL_NO_ELIMINABLE')


class EvidenciaInmutable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    evidencia = models.JSONField(editable=False)
    sha256 = models.CharField(max_length=64, editable=False)
    objects = EvidenciaInmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('EVIDENCIA_DOCUMENTAL_INMUTABLE')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('EVIDENCIA_DOCUMENTAL_NO_ELIMINABLE')


class VersionDocumental(EvidenciaInmutable):
    """Authorized text snapshot; not authorization to sign or consent to credit."""
    documento = models.ForeignKey(DocumentoLegal, on_delete=models.PROTECT, related_name='versiones')
    version = models.CharField(max_length=16)

    class Meta:
        ordering = ('-created_at',)
        constraints = [models.UniqueConstraint(fields=('documento', 'version'),
            name='documentos_version_unica')]

    def __str__(self):
        return f'{self.documento.codigo} / {self.version}'


class EventoDocumental(EvidenciaInmutable):
    class Tipo(models.TextChoices):
        PRESENTACION = 'PRESENTACION', 'Documento presentado'
        ACEPTACION_ADMIN = 'ACEPTACION_ADMIN', 'Declaracion administrativa de aceptacion'
        NEGATIVA_ADMIN = 'NEGATIVA_ADMIN', 'Declaracion administrativa de negativa'

    solicitud = models.ForeignKey('solicitudes.Solicitud', on_delete=models.PROTECT,
        related_name='eventos_documentales')
    version_documental = models.ForeignKey(VersionDocumental, on_delete=models.PROTECT,
        related_name='eventos')
    tipo = models.CharField(max_length=24, choices=Tipo.choices)
    presentacion = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
        related_name='respuestas')
    clave_idempotencia = models.UUIDField()

    class Meta:
        ordering = ('created_at', 'id')
        indexes = [models.Index(fields=('solicitud', 'created_at'), name='documentos_historial_idx')]
        constraints = [
            models.UniqueConstraint(fields=('solicitud', 'clave_idempotencia'),
                name='documentos_evento_idempotente'),
            models.UniqueConstraint(fields=('presentacion',), condition=models.Q(presentacion__isnull=False),
                name='documentos_respuesta_unica'),
            models.CheckConstraint(condition=(
                models.Q(tipo='PRESENTACION', presentacion__isnull=True) |
                models.Q(tipo__in=('ACEPTACION_ADMIN', 'NEGATIVA_ADMIN'), presentacion__isnull=False)),
                name='documentos_tipo_parentesco'),
        ]

    def __str__(self):
        return f'{self.solicitud.numero_solicitud} / {self.get_tipo_display()}'


class PlanPaqueteDocumental(EvidenciaInmutable):
    """Immutable proposal, not a provider operation or a holder's acceptance."""
    solicitud = models.ForeignKey('solicitudes.Solicitud', on_delete=models.PROTECT,
        related_name='planes_documentales')
    codigo = models.SlugField(max_length=64)
    version = models.CharField(max_length=16)
    etapa = models.CharField(max_length=24, choices=[('PRECONSULTA', 'Antes de centrales'),
        ('PRERADICACION', 'Antes de radicar'), ('FORMALIZACION', 'Formalizacion')])
    clave_idempotencia = models.UUIDField()

    class Meta:
        ordering = ('created_at', 'id')
        constraints = [models.UniqueConstraint(fields=('solicitud', 'clave_idempotencia'),
            name='documentos_plan_idempotente'),
            models.UniqueConstraint(fields=('solicitud', 'codigo', 'version'),
                name='documentos_plan_version_unica')]

    def __str__(self):
        return f'{self.solicitud.numero_solicitud} / {self.codigo} / {self.version}'
