from django.urls import path

from .views import HDCConsultView, HDCPdfEvidenceView, HDCXmlEvidenceView, InternalHealthView, PreselectaConsultView

app_name = "internal_api_v1"
urlpatterns = [
    path("preselecta/consultar/", PreselectaConsultView.as_view(), name="preselecta-consult"),
    path("hdc/consultar/", HDCConsultView.as_view(), name="hdc-consult"),
    path("hdc/consultas/<uuid:consulta_id>/pdf/", HDCPdfEvidenceView.as_view(), name="hdc-pdf"),
    path("hdc/consultas/<uuid:consulta_id>/xml/", HDCXmlEvidenceView.as_view(), name="hdc-xml"),
    path("health/", InternalHealthView.as_view(), name="health"),
]
