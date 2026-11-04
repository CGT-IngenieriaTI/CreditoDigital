from django.db import migrations, models


def proteger_evidencia_al_revertir(apps, schema_editor):
    model=apps.get_model('documentos','AceptacionDocumento')
    if model.objects.using(schema_editor.connection.alias).filter(evidencia_version__isnull=False).exists():
        raise RuntimeError('ROLLBACK_NO_PUEDE_ELIMINAR_EVIDENCIA_VERSIONADA')


class Migration(migrations.Migration):
    dependencies = [('documentos', '0002_aceptaciondocumento_llego_al_final_and_more')]
    operations = [migrations.AddField(model_name='aceptaciondocumento',name='evidencia_version',
        field=models.JSONField(blank=True,editable=False,null=True)),
        migrations.RunPython(migrations.RunPython.noop,proteger_evidencia_al_revertir)]
