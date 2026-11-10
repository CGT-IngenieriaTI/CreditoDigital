import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [("solicitudes", "0003_solicitud_consecutivo"), ("xcore_consumo", "0005_otp_auditability")]
    operations = [migrations.CreateModel(
        name="OperacionGateway",
        fields=[
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("proveedor", models.CharField(max_length=12, choices=[("PRESELECTA", "PRESELECTA"), ("HDC", "HDC")])),
            ("identidad_hmac", models.CharField(max_length=64)),
            ("consentimiento_hmac", models.CharField(max_length=64)),
            ("request_hmac", models.CharField(max_length=64)),
            ("idempotency_key", models.CharField(max_length=80, unique=True)),
            ("request_id", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
            ("estado", models.CharField(max_length=24, default="PENDIENTE", choices=[
                ("PENDIENTE", "Pendiente"), ("EN_EJECUCION", "En ejecucion"), ("COMPLETADA", "Completada"),
                ("SIN_INFORMACION", "Sin informacion"), ("PENDIENTE_CONCILIACION", "Pendiente conciliacion"),
                ("ERROR_DEFINITIVO", "Error definitivo")])),
            ("inicio_at", models.DateTimeField(null=True)), ("fin_at", models.DateTimeField(null=True)),
            ("codigo_error", models.CharField(max_length=64, blank=True)),
            ("http_status", models.PositiveSmallIntegerField(null=True)),
            ("consulta_id", models.UUIDField(null=True)),
            ("version_contrato", models.CharField(max_length=40, blank=True)),
            ("recibo_hmac", models.CharField(max_length=64, blank=True)),
            ("consentimiento", models.ForeignKey(to="xcore_consumo.consentimientoconsumo", on_delete=django.db.models.deletion.PROTECT)),
            ("solicitud", models.ForeignKey(to="solicitudes.solicitud", on_delete=django.db.models.deletion.PROTECT, related_name="operaciones_risk_gateway")),
        ],
        options={"constraints": [
            models.UniqueConstraint(fields=("solicitud", "proveedor"), name="rg_ops_solicitud_proveedor_uq"),
            models.CheckConstraint(condition=models.Q(proveedor__in=["PRESELECTA", "HDC"]), name="rg_ops_proveedor_ck"),
            models.CheckConstraint(condition=models.Q(estado__in=["PENDIENTE", "EN_EJECUCION", "COMPLETADA", "SIN_INFORMACION", "PENDIENTE_CONCILIACION", "ERROR_DEFINITIVO"]), name="rg_ops_estado_ck"),
        ]},
    )]
