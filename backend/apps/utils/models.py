from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLog(TimeStampedModel):
    class Levels(models.TextChoices):
        INFO = "INFO", "Informacion"
        WARNING = "WARNING", "Advertencia"
        ERROR = "ERROR", "Error"

    solicitud = models.ForeignKey(
        "solicitudes.Solicitud",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    event = models.CharField(max_length=120)
    level = models.CharField(max_length=16, choices=Levels.choices, default=Levels.INFO)
    actor = models.CharField(max_length=120, blank=True)
    request_id = models.CharField(max_length=120, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Auditoria"
        verbose_name_plural = "Auditoria"

    def __str__(self) -> str:
        return f"{self.event} - {self.level}"
