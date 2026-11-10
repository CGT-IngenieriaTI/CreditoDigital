from datetime import date

from django.db.models import Q
from django.http import Http404, HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.historial_pago.client import HistorialPagoClientError
from apps.xcore_consumo.models import SolicitudConsumo
from apps.xcore_consumo.serializers import (
    ConsentimientoSerializer,
    ConsumoOrchestrationPreviewSerializer,
    ConsumoProcessSerializer,
    ConsumoSolicitudCreateSerializer,
    CoreConsultaSerializer,
    EvaluacionConsumoSerializer,
    FormularioConsumoSerializer,
    OtpSendSerializer,
    OtpVerifySerializer,
    SolicitudConsumoStatusSerializer,
)
from apps.xcore_consumo.services.calculadora import build_consumo_decision_pdf, encode_pdf_base64
from apps.xcore_consumo.services.orchestration import (
    build_consulta_identificacion_response,
    persist_initial_orchestration_snapshot,
)
from apps.xcore_consumo.services.otp import send_otp, verify_otp
from apps.xcore_consumo.services.pipeline import (
    Hc2SelectionRequired,
    consultar_core,
    guardar_formulario_xcore,
    procesar_consumo,
    registrar_consentimiento,
)


def _actor(request) -> str:
    user = request.user
    if not user.is_authenticated:
        return ""
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _consentimiento_real_response(call):
    from django.core.exceptions import ObjectDoesNotExist
    from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
    try:
        return Response(call())
    except ObjectDoesNotExist:
        return Response({"codigo": "EXPEDIENTE_NO_DISPONIBLE"}, status=404)
    except OperacionError as exc:
        return Response({"codigo": exc.codigo}, status=409)
    except Exception:
        return Response({"codigo": "CONSENTIMIENTO_OPERACION_NO_COMPLETADA"}, status=400)


def get_detail_or_404(request, solicitud_id):
    from django.conf import settings
    queryset = SolicitudConsumo.objects.select_related(
        "solicitud",
        "solicitud__solicitante",
        "solicitud__asesor",
    )
    firma_id = getattr(settings, "CREDIHOY_FIRMA_SOLICITUD_ID", None)
    if firma_id is not None:
        queryset = queryset.filter(solicitud_id=firma_id, solicitud__asesor=request.user,
            solicitud__producto="CREDIHOY UNICO (NUEVO)", solicitud__metadatos__modo="CONSULTA_REAL_CONTROLADA")
    if not request.user.is_staff and not request.user.is_superuser:
        queryset = queryset.filter(solicitud__asesor=request.user)
    try:
        return queryset.get(solicitud_id=solicitud_id)
    except SolicitudConsumo.DoesNotExist as exc:
        raise Http404("Solicitud de consumo no encontrada.") from exc


def _parse_iso_date(value: str | None):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _serialize_dashboard_item(request, detail: SolicitudConsumo) -> dict:
    solicitud = detail.solicitud
    evaluacion = getattr(solicitud, "evaluacion_consumo", None)
    decision = getattr(solicitud, "decision_final", None)
    form_data = detail.form_data or {}
    orchestration = detail.orchestration_data or {}
    valores = orchestration.get("valores_consolidados", {}) if isinstance(orchestration, dict) else {}
    canal = form_data.get("canal") or valores.get("canal") or "Sin canal"
    pdf_available = bool(evaluacion and evaluacion.pdf_generado)
    pdf_url = (
        request.build_absolute_uri(f"/api/v1/consumo/solicitudes/{detail.solicitud_id}/decision/pdf/")
        if pdf_available
        else ""
    )
    return {
        "id": str(detail.solicitud_id),
        "numero_solicitud": solicitud.numero_solicitud,
        "created_at": detail.created_at,
        "updated_at": detail.updated_at,
        "estado": detail.estado,
        "decision_final": evaluacion.decision_final if evaluacion else (decision.resultado if decision else ""),
        "pdf_url": pdf_url,
        "numero_identificacion": solicitud.solicitante.numero_identificacion,
        "primer_apellido": solicitud.solicitante.primer_apellido,
        "canal": canal,
        "agencia": canal,
    }


class ConsumoSolicitudCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = SolicitudConsumo.objects.select_related(
            "solicitud",
            "solicitud__solicitante",
            "solicitud__asesor",
            "solicitud__evaluacion_consumo",
            "solicitud__decision_final",
        ).order_by("-created_at")

        if not request.user.is_staff and not request.user.is_superuser:
            queryset = queryset.filter(solicitud__asesor=request.user)

        estado = (request.query_params.get("estado") or "").strip()
        if estado:
            queryset = queryset.filter(estado__iexact=estado)

        date_from = _parse_iso_date(request.query_params.get("date_from"))
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)

        date_to = _parse_iso_date(request.query_params.get("date_to"))
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        q = (request.query_params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(solicitud__numero_solicitud__icontains=q)
                | Q(solicitud__solicitante__numero_identificacion__icontains=q)
                | Q(solicitud__solicitante__primer_apellido__icontains=q)
            )

        payload = [_serialize_dashboard_item(request, detail) for detail in queryset[:200]]
        return Response(payload)

    def post(self, request):
        if request.data.get("modo") == "CONSULTA_REAL_CONTROLADA":
            if "titular" in request.data:
                from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import crear_para_titular
                return _consentimiento_real_response(lambda: {"solicitudId": str(crear_para_titular(
                    request.data["titular"], request.user).pk)})
            from rest_framework import serializers
            from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import crear_desde_expediente
            class PrepararFirmaSerializer(serializers.Serializer):
                modo = serializers.ChoiceField(choices=["CONSULTA_REAL_CONTROLADA"])
                solicitud_origen = serializers.UUIDField()
                clave = serializers.UUIDField()
            data = PrepararFirmaSerializer(data=request.data)
            data.is_valid(raise_exception=True)
            return _consentimiento_real_response(lambda: {"solicitudId": str(crear_desde_expediente(
                data.validated_data["solicitud_origen"], data.validated_data["clave"], request.user).pk)})
        serializer = ConsumoSolicitudCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        solicitud = serializer.save()
        if not getattr(serializer, "reused_existing", False):
            persist_initial_orchestration_snapshot(solicitud.consumo_detail)
        return Response(
            SolicitudConsumoStatusSerializer(solicitud.consumo_detail).data,
            status=status.HTTP_200_OK if getattr(serializer, "reused_existing", False) else status.HTTP_201_CREATED,
        )


class ConsumoOrchestrationPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ConsumoOrchestrationPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = build_consulta_identificacion_response(
            user=request.user,
            payload=serializer.validated_data,
        )
        return Response(payload)


class ConsumoSolicitudStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        return Response(SolicitudConsumoStatusSerializer(detail).data)


class ConsumoOtpSendView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        serializer = OtpSendSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import es_controlada, accion_otp
        if es_controlada(detail.solicitud):
            from django.conf import settings
            if (getattr(settings, "CREDIHOY_OTP_CONFIRMACION_EXPLICITA_REQUIRED", False)
                    and request.data.get("confirmacion_envio") is not True):
                return Response({"codigo": "CONFIRMACION_ENVIO_OTP_REQUERIDA"}, status=409)
            return _consentimiento_real_response(lambda: accion_otp(solicitud_id, request.user,
                accion="enviar", canal=serializer.validated_data["canal"], request=request))
        try:
            send_otp(
                detail.solicitud,
                channel=serializer.validated_data["canal"],
                actor=_actor(request),
                request=request,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        refreshed = get_detail_or_404(request, solicitud_id)
        return Response(SolicitudConsumoStatusSerializer(refreshed).data, status=status.HTTP_202_ACCEPTED)


class ConsumoOtpVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        serializer = OtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import es_controlada, accion_otp
        if es_controlada(detail.solicitud):
            return _consentimiento_real_response(lambda: accion_otp(solicitud_id, request.user,
                accion="verificar", codigo=serializer.validated_data["codigo"], request=request))
        try:
            verify_otp(
                detail.solicitud,
                code=serializer.validated_data["codigo"],
                actor=_actor(request),
                request=request,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        refreshed = get_detail_or_404(request, solicitud_id)
        return Response(SolicitudConsumoStatusSerializer(refreshed).data)


class ConsumoOtpStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import es_controlada, estado
        if es_controlada(detail.solicitud):
            return _consentimiento_real_response(lambda: estado(solicitud_id, request.user))
        return Response({"otp": SolicitudConsumoStatusSerializer(detail).data["otp"]})


class ConsumoConsentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, solicitud_id):
        from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import estado
        return _consentimiento_real_response(lambda: estado(solicitud_id, request.user))

    def post(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import es_controlada, aceptar, firmar, estado
        if es_controlada(detail.solicitud):
            from rest_framework import serializers
            class FirmaSerializer(serializers.Serializer):
                accion = serializers.ChoiceField(choices=["aceptar", "firmar"])
                aceptado = serializers.BooleanField(required=False)
                contacto_aceptado = serializers.BooleanField(required=False)
                canal = serializers.ChoiceField(choices=["SMS", "EMAIL"], required=False)
                destino = serializers.CharField(max_length=255, required=False, write_only=True)
            data = FirmaSerializer(data=request.data)
            data.is_valid(raise_exception=True)
            def ejecutar():
                p = data.validated_data
                if p["accion"] == "aceptar":
                    aceptar(solicitud_id, request.user, aceptado=p.get("aceptado"),
                        contacto_aceptado=p.get("contacto_aceptado"), canal=p.get("canal"), destino=p.get("destino", ""))
                else:
                    if p.get("aceptado") is not True:
                        raise ValueError("ACEPTACION_EXPRESA_REQUERIDA")
                    firmar(solicitud_id, request.user)
                return estado(solicitud_id, request.user)
            return _consentimiento_real_response(ejecutar)
        serializer = ConsentimientoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated, _ = registrar_consentimiento(
                request,
                detail.solicitud,
                serializer.validated_data,
                actor=_actor(request),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SolicitudConsumoStatusSerializer(updated).data, status=status.HTTP_202_ACCEPTED)


class ConsumoSolicitudCoreView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        serializer = CoreConsultaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = consultar_core(detail.solicitud, actor=_actor(request))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SolicitudConsumoStatusSerializer(updated).data)


class ConsumoSolicitudFormView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        serializer = FormularioConsumoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = guardar_formulario_xcore(
                detail.solicitud,
                serializer.validated_data["form_data"],
                actor=_actor(request),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SolicitudConsumoStatusSerializer(updated).data)


class ConsumoProcessView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        serializer = ConsumoProcessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        selected_keys = (
            serializer.validated_data.get("selected_hc2_keys")
            if "selected_hc2_keys" in request.data
            else None
        )
        try:
            evaluacion, decision, pdf_bytes = procesar_consumo(
                detail.solicitud,
                actor=_actor(request),
                selected_hc2_keys=selected_keys,
                request_id=getattr(request, "request_id", None) or request.headers.get("X-Request-ID"),
            )
        except Hc2SelectionRequired as exc:
            return Response(exc.payload, status=status.HTTP_409_CONFLICT)
        except HistorialPagoClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payload = EvaluacionConsumoSerializer(evaluacion).data
        payload["decision"] = {
            "resultado": decision.resultado,
            "mensaje": decision.mensaje,
            "monto_aprobado": decision.monto_aprobado,
            "plazo_aprobado": decision.plazo_aprobado,
            "tasa_interes": decision.tasa_interes,
        }
        payload["pdf_url"] = request.build_absolute_uri(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/decision/pdf/"
        )
        payload["pdf_base64"] = encode_pdf_base64(pdf_bytes)
        return Response(payload)


class ConsumoDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        if not hasattr(detail.solicitud, "evaluacion_consumo"):
            raise Http404("La solicitud aun no tiene evaluacion.")
        payload = EvaluacionConsumoSerializer(detail.solicitud.evaluacion_consumo).data
        if hasattr(detail.solicitud, "decision_final"):
            decision = detail.solicitud.decision_final
            payload["decision"] = {
                "resultado": decision.resultado,
                "mensaje": decision.mensaje,
                "monto_aprobado": decision.monto_aprobado,
                "plazo_aprobado": decision.plazo_aprobado,
                "tasa_interes": decision.tasa_interes,
            }
        payload["pdf_url"] = request.build_absolute_uri(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/decision/pdf/"
        )
        return Response(payload)


class ConsumoDecisionPdfView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, solicitud_id):
        detail = get_detail_or_404(request, solicitud_id)
        if not hasattr(detail.solicitud, "evaluacion_consumo"):
            raise Http404("La solicitud aun no tiene evaluacion.")
        try:
            pdf_bytes = build_consumo_decision_pdf(detail.solicitud, detail.solicitud.evaluacion_consumo)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        disposition = "attachment" if str(request.query_params.get("download", "")).lower() in {"1", "true", "yes"} else "inline"
        response["Content-Disposition"] = (
            f'{disposition}; filename="{detail.solicitud.numero_solicitud}_consumo.pdf"'
        )
        return response
