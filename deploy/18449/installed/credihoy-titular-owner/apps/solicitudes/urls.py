from django.urls import path

from .views import SolicitudAuthorizeView, SolicitudStartView, SolicitudStatusView

urlpatterns = [
    path("", SolicitudStartView.as_view(), name="solicitud-start"),
    path("<uuid:solicitud_id>/autorizar/", SolicitudAuthorizeView.as_view(), name="solicitud-autorizar"),
    path("<uuid:solicitud_id>/", SolicitudStatusView.as_view(), name="solicitud-status"),
]
