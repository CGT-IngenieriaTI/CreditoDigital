from django.urls import path

from .views import DocumentoLegalListView, DocumentoLegalPdfView

urlpatterns = [
    path("", DocumentoLegalListView.as_view(), name="documentos-list"),
    path("<slug:codigo>/pdf/", DocumentoLegalPdfView.as_view(), name="documentos-pdf"),
]
