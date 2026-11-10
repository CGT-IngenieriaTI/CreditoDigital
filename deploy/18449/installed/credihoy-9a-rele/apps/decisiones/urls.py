from django.urls import path

from .views import DecisionFinalPdfView, DecisionFinalView

urlpatterns = [
    path("<uuid:solicitud_id>/", DecisionFinalView.as_view(), name="decision-detail"),
    path("<uuid:solicitud_id>/pdf/", DecisionFinalPdfView.as_view(), name="decision-pdf"),
]
