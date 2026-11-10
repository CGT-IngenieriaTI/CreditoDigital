from django.db import migrations, models


def backfill_consecutivo(apps, schema_editor):
    Solicitud = apps.get_model("solicitudes", "Solicitud")
    for index, solicitud in enumerate(Solicitud.objects.order_by("created_at", "id"), start=1):
        solicitud.consecutivo = index
        solicitud.numero_solicitud = f"CD{index}"
        solicitud.save(update_fields=("consecutivo", "numero_solicitud"))


class Migration(migrations.Migration):
    dependencies = [
        ("solicitudes", "0002_solicitud_asesor_alter_solicitud_estado"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitud",
            name="consecutivo",
            field=models.PositiveIntegerField(blank=True, null=True, unique=True),
        ),
        migrations.RunPython(backfill_consecutivo, migrations.RunPython.noop),
    ]
