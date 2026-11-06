"""Opt-in owner route; not mounted on Committee pilot or public URLConf."""
from django.urls import path
from apps.xcore_consumo.services.credihoy_v2.unico.publico_seguimiento import PATH
from apps.xcore_consumo.services.credihoy_v2.unico.publico_seguimiento_http import SolicitudesTitularView

urlpatterns=[path(PATH.lstrip('/'),SolicitudesTitularView.as_view())]
