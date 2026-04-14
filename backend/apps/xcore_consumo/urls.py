from django.urls import path

from .views import (
    ConsumoConsentView,
    ConsumoDecisionPdfView,
    ConsumoDecisionView,
    ConsumoOrchestrationPreviewView,
    ConsumoOtpSendView,
    ConsumoOtpStatusView,
    ConsumoOtpVerifyView,
    ConsumoProcessView,
    ConsumoSolicitudCoreView,
    ConsumoSolicitudCreateView,
    ConsumoSolicitudFormView,
    ConsumoSolicitudStatusView,
)

urlpatterns = [
    path("orquestacion/preview/", ConsumoOrchestrationPreviewView.as_view(), name="consumo-orquestacion-preview"),
    path("solicitudes/", ConsumoSolicitudCreateView.as_view(), name="consumo-solicitud-create"),
    path("solicitudes/<uuid:solicitud_id>/", ConsumoSolicitudStatusView.as_view(), name="consumo-solicitud-status"),
    path("solicitudes/<uuid:solicitud_id>/otp/send/", ConsumoOtpSendView.as_view(), name="consumo-solicitud-otp-send"),
    path("solicitudes/<uuid:solicitud_id>/otp/verify/", ConsumoOtpVerifyView.as_view(), name="consumo-solicitud-otp-verify"),
    path("solicitudes/<uuid:solicitud_id>/otp/status/", ConsumoOtpStatusView.as_view(), name="consumo-solicitud-otp-status"),
    path("solicitudes/<uuid:solicitud_id>/consentimiento/", ConsumoConsentView.as_view(), name="consumo-solicitud-consent"),
    path("solicitudes/<uuid:solicitud_id>/core/consultar/", ConsumoSolicitudCoreView.as_view(), name="consumo-solicitud-core"),
    path("solicitudes/<uuid:solicitud_id>/formularios/xcore/", ConsumoSolicitudFormView.as_view(), name="consumo-solicitud-form"),
    path("solicitudes/<uuid:solicitud_id>/procesar/", ConsumoProcessView.as_view(), name="consumo-solicitud-procesar"),
    path("solicitudes/<uuid:solicitud_id>/decision/", ConsumoDecisionView.as_view(), name="consumo-solicitud-decision"),
    path("solicitudes/<uuid:solicitud_id>/decision/pdf/", ConsumoDecisionPdfView.as_view(), name="consumo-solicitud-decision-pdf"),
]
