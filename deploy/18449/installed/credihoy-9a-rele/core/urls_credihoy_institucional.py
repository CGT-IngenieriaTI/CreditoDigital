"""Dedicated institutional process only; not included by the pilot URLConf."""
from django.urls import path
from django.conf import settings
from .views import HealthcheckView

urlpatterns = [path('api/interno/credihoy/acceso/salud/', HealthcheckView.as_view())]
if not getattr(settings, 'CREDIHOY_ACCESO_MANTENIMIENTO', False):
    from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional_http import (
        EnviarView, ComprobarView, SeguimientoView)
    urlpatterns += [
    path('api/interno/credihoy/acceso/verify/enviar/', EnviarView.as_view()),
    path('api/interno/credihoy/acceso/verify/comprobar/', ComprobarView.as_view()),
    path('api/interno/credihoy/acceso/seguimiento/', SeguimientoView.as_view()),
    ]
