from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("historial_pago", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="historialpagoconsulta",
            name="soap_request_xml",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="historialpagoconsulta",
            name="xml_payload",
            field=models.TextField(blank=True),
        ),
    ]
