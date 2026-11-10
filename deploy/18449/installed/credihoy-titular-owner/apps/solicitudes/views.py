from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.documentos.models import AceptacionDocumento
from apps.documentos.services import get_active_documents
from apps.utils.logging import audit_event
from apps.utils.throttling import BurstRateThrottle, SustainedRateThrottle

from .models import EstadoSolicitud, Solicitud
from .serializers import (
    SolicitudAuthorizeSerializer,
    SolicitudStartSerializer,
    SolicitudStatusSerializer,
)
from .services import dispatch_credit_pipeline


def _get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class SolicitudStartView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [BurstRateThrottle, SustainedRateThrottle]

    def post(self, request):
        serializer = SolicitudStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        solicitud = serializer.save()
        audit_event(
            "solicitud_iniciada",
            solicitud=solicitud,
            actor="public",
            payload={"numero_solicitud": solicitud.numero_solicitud},
        )
        return Response(SolicitudStatusSerializer(solicitud).data, status=status.HTTP_201_CREATED)


class SolicitudAuthorizeView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [BurstRateThrottle, SustainedRateThrottle]

    def post(self, request, solicitud_id):
        try:
            solicitud = Solicitud.objects.select_related("solicitante").get(pk=solicitud_id)
        except Solicitud.DoesNotExist:
            return Response({"detail": "Solicitud no encontrada."}, status=status.HTTP_404_NOT_FOUND)
        serializer = SolicitudAuthorizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ip_address = _get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "")
        accepted_documents = {
            item["document_id"]: item for item in serializer.validated_data["accepted_documents"]
        }

        for document in get_active_documents():
            if document.id in accepted_documents:
                accepted_payload = accepted_documents[document.id]
                AceptacionDocumento.objects.update_or_create(
                    solicitud=solicitud,
                    documento=document,
                    defaults={
                        "aceptado": True,
                        "fecha_aceptacion": timezone.now(),
                        "visualizacion_segundos": accepted_payload["viewed_seconds"],
                        "llego_al_final": accepted_payload["reached_end"],
                        "ip_address": ip_address,
                        "user_agent": user_agent[:255],
                    },
                )

        solicitud.estado = EstadoSolicitud.AUTORIZADA
        solicitud.paso_actual = "autorizaciones"
        solicitud.ultimo_error = ""
        solicitud.save(update_fields=("estado", "paso_actual", "ultimo_error", "updated_at"))
        is_async = dispatch_credit_pipeline(solicitud.id)
        audit_event(
            "autorizaciones_aceptadas",
            solicitud=solicitud,
            actor="public",
            payload={"async": is_async, "documentos": serializer.validated_data["accepted_documents"]},
        )
        return Response(
            {
                "solicitud_id": solicitud.id,
                "numero_solicitud": solicitud.numero_solicitud,
                "estado": solicitud.estado,
                "async": is_async,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class SolicitudStatusView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, solicitud_id):
        try:
            solicitud = Solicitud.objects.select_related("solicitante").get(pk=solicitud_id)
        except Solicitud.DoesNotExist:
            return Response({"detail": "Solicitud no encontrada."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SolicitudStatusSerializer(solicitud).data)
