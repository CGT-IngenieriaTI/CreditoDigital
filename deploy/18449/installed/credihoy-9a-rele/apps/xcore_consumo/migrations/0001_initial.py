from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("solicitudes", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfiguracionAgenciaCanal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("canal", models.CharField(max_length=255, unique=True)),
                ("codigo", models.CharField(blank=True, max_length=255)),
                ("puntos", models.IntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="ConfiguracionGastosFamiliares",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("salario_minimo", models.IntegerField()),
                ("cant_personasacargo", models.IntegerField()),
                ("porcentaje", models.DecimalField(decimal_places=4, max_digits=6)),
                ("zona", models.CharField(blank=True, max_length=50, null=True)),
            ],
            options={"unique_together": {("salario_minimo", "cant_personasacargo", "zona")}},
        ),
        migrations.CreateModel(
            name="ConfiguracionRegresion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("parametro", models.CharField(max_length=255)),
                ("nivel", models.CharField(max_length=255)),
                ("estimacion", models.DecimalField(decimal_places=4, max_digits=20)),
            ],
            options={"unique_together": {("parametro", "nivel")}},
        ),
        migrations.CreateModel(
            name="TasaInteresConsumo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("linea_credito", models.CharField(max_length=100)),
                ("forma_pago", models.CharField(max_length=50)),
                ("sub_categoria", models.CharField(default="General", max_length=100)),
                ("categoria_riesgo", models.CharField(default="NA", max_length=10)),
                ("tasa_ea", models.FloatField()),
            ],
            options={"unique_together": {("linea_credito", "forma_pago", "sub_categoria", "categoria_riesgo")}},
        ),
        migrations.CreateModel(
            name="SolicitudConsumo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("estado", models.CharField(choices=[("BORRADOR", "Borrador"), ("CORE_OK", "Core consultado"), ("FORM_OK", "Formulario guardado"), ("AUTORIZADA", "Autorizada"), ("PROCESADA", "Procesada"), ("ERROR", "Error")], default="BORRADOR", max_length=24)),
                ("oracle_consultado", models.BooleanField(default=False)),
                ("documentos_autorizados", models.BooleanField(default=False)),
                ("selected_hc2_keys", models.JSONField(blank=True, default=list)),
                ("core_data", models.JSONField(blank=True, default=dict)),
                ("form_data", models.JSONField(blank=True, default=dict)),
                ("ultimo_error", models.TextField(blank=True)),
                ("solicitud", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="consumo_detail", to="solicitudes.solicitud")),
            ],
        ),
        migrations.CreateModel(
            name="EvaluacionConsumo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("input_snapshot", models.JSONField(blank=True, default=dict)),
                ("integraciones_snapshot", models.JSONField(blank=True, default=dict)),
                ("resultados", models.JSONField(blank=True, default=dict)),
                ("puntaje_xcore", models.FloatField(default=0)),
                ("perfil_riesgo", models.CharField(blank=True, max_length=64)),
                ("perfil_credito", models.CharField(blank=True, max_length=64)),
                ("capacidad_pago_final", models.CharField(blank=True, max_length=64)),
                ("decision_final", models.CharField(blank=True, max_length=64)),
                ("estamento", models.CharField(blank=True, max_length=100)),
                ("tiene_novedad", models.BooleanField(default=False)),
                ("novedad_descripcion", models.CharField(blank=True, max_length=255)),
                ("monto_max_posible", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("valor_cuota", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("vida_deudores", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("pdf_generado", models.BooleanField(default=False)),
                ("solicitud", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="evaluacion_consumo", to="solicitudes.solicitud")),
            ],
        ),
        migrations.CreateModel(
            name="ConsultaCoreOracle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("estado", models.CharField(default="PENDIENTE", max_length=24)),
                ("mensaje", models.CharField(blank=True, max_length=255)),
                ("solicitud", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="consultas_core_oracle", to="solicitudes.solicitud")),
            ],
        ),
        migrations.CreateModel(
            name="ConsultaEstamentoOracle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("estado", models.CharField(default="PENDIENTE", max_length=24)),
                ("resultado", models.CharField(blank=True, max_length=16)),
                ("tipo_familiar", models.CharField(blank=True, max_length=255)),
                ("mensaje", models.CharField(blank=True, max_length=255)),
                ("solicitud", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="consultas_estamento_oracle", to="solicitudes.solicitud")),
            ],
        ),
    ]
