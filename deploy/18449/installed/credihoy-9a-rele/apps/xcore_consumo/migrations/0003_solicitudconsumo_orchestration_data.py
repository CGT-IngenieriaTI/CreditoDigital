from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("xcore_consumo", "0002_alter_configuracionagenciacanal_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitudconsumo",
            name="orchestration_data",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
