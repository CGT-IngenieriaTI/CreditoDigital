from django.urls import path
from .cotizacion_http import CotizacionInstitucionalView

urlpatterns = [path('api/v1/credihoy/unico/solicitudes/<uuid:solicitud_id>/cotizacion/',
    CotizacionInstitucionalView.as_view(), name='credihoy-cotizacion')]
