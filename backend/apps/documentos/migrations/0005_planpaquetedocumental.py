import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def conservar(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''CREATE TRIGGER documentos_plan_append_only BEFORE UPDATE OR DELETE
            ON documentos_planpaquetedocumental FOR EACH ROW
            EXECUTE FUNCTION documentos_conservar_evidencia();''')


def retirar(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER documentos_plan_append_only ON documentos_planpaquetedocumental')


def proteger_reversion(apps, schema_editor):
    if apps.get_model('documentos', 'PlanPaqueteDocumental').objects.using(schema_editor.connection.alias).exists():
        raise RuntimeError('ROLLBACK_NO_PUEDE_ELIMINAR_PLANES_DOCUMENTALES')


class Migration(migrations.Migration):
    dependencies = [('documentos', '0004_versiondocumental_eventodocumental_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='PlanPaqueteDocumental', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('evidencia', models.JSONField(editable=False)),
            ('sha256', models.CharField(editable=False, max_length=64)),
            ('codigo', models.SlugField(max_length=64)),
            ('version', models.CharField(max_length=16)),
            ('etapa', models.CharField(max_length=24, choices=[('PRECONSULTA', 'Antes de centrales'),
                ('PRERADICACION', 'Antes de radicar'), ('FORMALIZACION', 'Formalizacion')])),
            ('clave_idempotencia', models.UUIDField()),
            ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ('solicitud', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='planes_documentales', to='solicitudes.solicitud')),
        ], options={'ordering': ('created_at', 'id')}),
        migrations.AddConstraint(model_name='planpaquetedocumental', constraint=models.UniqueConstraint(
            fields=('solicitud', 'clave_idempotencia'), name='documentos_plan_idempotente')),
        migrations.AddConstraint(model_name='planpaquetedocumental', constraint=models.UniqueConstraint(
            fields=('solicitud', 'codigo', 'version'), name='documentos_plan_version_unica')),
        migrations.RunPython(conservar, retirar),
        migrations.RunPython(migrations.RunPython.noop, proteger_reversion),
    ]
