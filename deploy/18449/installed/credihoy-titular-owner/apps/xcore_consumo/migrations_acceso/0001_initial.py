"""Same access model state, without credit migrations or their dependencies."""
from importlib import import_module
from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        *import_module('apps.xcore_consumo.migrations.0008_accesopublicocredihoy_limiteaccesocredihoy').Migration.operations,
        *import_module('apps.xcore_consumo.migrations.0009_eventoaccesocredihoy_operacionverifycredihoy').Migration.operations,
    ]
