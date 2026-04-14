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

    class Meta:
        unique_together = ("solicitud", "documento")
        ordering = ("-fecha_aceptacion",)
        verbose_name = "Aceptacion de documento"
        verbose_name_plural = "Aceptaciones de documento"

    def __str__(self) -> str:
        return f"{self.solicitud.numero_solicitud} - {self.documento.codigo}"

# Create your models here.
