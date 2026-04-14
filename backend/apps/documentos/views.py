from django.http import FileResponse, Http404, HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.utils.pdf import build_legal_document_pdf

from .serializers import DocumentoLegalSerializer
from .services import get_active_documents, get_document_asset_path


class DocumentoLegalListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        serializer = DocumentoLegalSerializer(
            get_active_documents(),
            many=True,
            context={"request": request},
        )
        return Response(serializer.data)


class DocumentoLegalPdfView(APIView):
    permission_classes = [AllowAny]

    DOCUMENT_CONTENT = {
        "centrales-riesgo": [
            "Autorizo la consulta de mi informacion en centrales de riesgo y operadores de datos.",
            "Autorizo la validacion de informacion financiera, comercial y de comportamiento de pago.",
            "Entiendo que esta autorizacion es requisito para el analisis automatizado del credito.",
        ],
    }

    def get(self, request, codigo: str):
        document = next((doc for doc in get_active_documents() if doc.codigo == codigo), None)
        if not document:
            raise Http404("Documento no encontrado")

        asset_path = get_document_asset_path(codigo)
        if asset_path and asset_path.exists():
            response = FileResponse(asset_path.open("rb"), content_type="application/pdf")
            response["Content-Disposition"] = f'inline; filename="{codigo}.pdf"'
            return response

        content = self.DOCUMENT_CONTENT.get(codigo, [])
        pdf_bytes = build_legal_document_pdf(document.titulo, document.descripcion, content)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{codigo}.pdf"'
        return response
