import uuid
from django.db import migrations, models


def unlogged(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('ALTER TABLE public.solicitudes_progresotemporalcredihoy SET UNLOGGED')


class Migration(migrations.Migration):
    dependencies = [('solicitudes', '0003_solicitud_consecutivo')]
    operations = [
        migrations.CreateModel(name='ProgresoTemporalCredihoy', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('titular_hmac', models.CharField(max_length=64, unique=True)),
            ('version', models.PositiveIntegerField(default=1)),
            ('datos_cifrados', models.TextField()),
            ('operacion_id', models.UUIDField()),
            ('operacion_hmac', models.CharField(max_length=64)),
            ('actividad_at', models.DateTimeField()),
            ('vence_at', models.DateTimeField(db_index=True)),
        ], options={'db_table': 'solicitudes_progresotemporalcredihoy', 'constraints': [
            models.CheckConstraint(condition=models.Q(version__gte=1), name='ch_progreso_version_positiva')]}),
        migrations.RunPython(unlogged, migrations.RunPython.noop),
    ]
