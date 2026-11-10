"""State-only migrations app: no imports of credit, consent or Oracle models."""
from importlib import import_module
from django.apps import AppConfig


class AccesoAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.xcore_consumo'

    def import_models(self):
        self.models = self.apps.all_models[self.label]
        self.models_module = import_module('apps.xcore_consumo.public_access_models')
