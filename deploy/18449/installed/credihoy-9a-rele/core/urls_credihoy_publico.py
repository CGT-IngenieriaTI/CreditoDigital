"""Separate public process; owner-delegated quote opt-in, never staff or START."""
from django.urls import path
from django.conf import settings
from .views import HealthcheckView

urlpatterns = [path('api/v1/credihoy/publico/salud/', HealthcheckView.as_view())]
if not getattr(settings, 'CREDIHOY_ACCESO_MANTENIMIENTO', False):
    from apps.xcore_consumo.services.credihoy_v2.unico.publico_http import (
        InicioView,SolicitarView,VerificarView,SesionView,CerrarView,SolicitudesView,SolicitudView,CotizacionTitularView)
    urlpatterns += [
    path('api/v1/credihoy/publico/inicio/',InicioView.as_view()),
    path('api/v1/credihoy/publico/acceso/',SolicitarView.as_view()),
    path('api/v1/credihoy/publico/verificar/',VerificarView.as_view()),
    path('api/v1/credihoy/publico/sesion/',SesionView.as_view()),
    path('api/v1/credihoy/publico/salir/',CerrarView.as_view()),
    path('api/v1/credihoy/publico/solicitudes/',SolicitudesView.as_view()),
    path('api/v1/credihoy/publico/solicitudes/<uuid:solicitud_id>/',SolicitudView.as_view()),
    path('api/v1/credihoy/publico/cotizacion/',CotizacionTitularView.as_view()),
    ]
