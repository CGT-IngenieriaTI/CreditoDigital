from django.http import Http404, HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.solicitudes.models import Solicitud
from apps.utils.pdf import build_decision_pdf

from .serializers import DecisionFinalSerializer


class DecisionFinalView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, solicitud_id):
        try:
            queryset = Solicitud.objects.select_related("solicitante", "decision_final", "asesor")
            if not request.user.is_staff and not request.user.is_superuser:
                queryset = queryset.filter(asesor=request.user)
            solicitud = queryset.get(pk=solicitud_id)
        except Solicitud.DoesNotExist as exc:
            raise Http404("Solicitud no encontrada") from exc
        if not hasattr(solicitud, "decision_final"):
            raise Http404("Decision no encontrada")
        serializer = DecisionFinalSerializer(solicitud.decision_final)
        return Response(serializer.data)


class DecisionFinalPdfView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, solicitud_id):
        try:
            queryset = Solicitud.objects.select_related("solicitante", "decision_final", "asesor")
            if not request.user.is_staff and not request.user.is_superuser:
                queryset = queryset.filter(asesor=request.user)
            solicitud = queryset.get(pk=solicitud_id)
        except Solicitud.DoesNotExist as exc:
            raise Http404("Solicitud no encontrada") from exc
        if not hasattr(solicitud, "decision_final"):
            raise Http404("Decision no disponible")

        pdf_bytes = build_decision_pdf(solicitud, solicitud.decision_final)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{solicitud.numero_solicitud}.pdf"'
        return response
