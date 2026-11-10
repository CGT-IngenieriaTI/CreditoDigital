from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("solicitudes", "0003_solicitud_consecutivo"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("xcore_consumo", "0003_solicitudconsumo_orchestration_data"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConsultaAsociadoIntento",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tipo_identificacion", models.CharField(max_length=8)),
                ("numero_identificacion", models.CharField(max_length=32)),
                ("oracle_ok", models.BooleanField(default=False)),
                ("preselecta_ok", models.BooleanField(default=False)),
                ("datacredito_ok", models.BooleanField(default=False)),
                ("puede_continuar", models.BooleanField(default=False)),
                ("bloqueado", models.BooleanField(default=False)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("mensaje", models.CharField(blank=True, max_length=255)),
                (
                    "asesor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="consultas_asociado_consumo",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "solicitud",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="consultas_asociado",
                        to="solicitudes.solicitud",
                    ),
                ),
            ],
            options={
                "verbose_name": "Intento consulta asociado",
                "verbose_name_plural": "Intentos consulta asociado",
                "ordering": ("-created_at",),
            },
        ),
    ]
