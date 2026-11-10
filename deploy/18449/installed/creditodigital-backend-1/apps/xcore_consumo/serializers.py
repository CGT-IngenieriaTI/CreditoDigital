import re

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.documentos.services import get_active_documents
from apps.solicitudes.models import EstadoSolicitud, Solicitud
from apps.solicitudes.serializers import SolicitudStatusSerializer
from apps.usuarios.models import Solicitante, TipoIdentificacion
from apps.utils.validators import validate_credit_policy, validate_document_issue_date

from .models import (
    CanalOtp,
    ConsentimientoConsumo,
    EstadoOtp,
    EstadoSolicitudConsumo,
    EvaluacionConsumo,
    OtpChallenge,
    SolicitudConsumo,
)
from .services.orchestration import find_active_duplicate_request


CONSENTIMIENTO_VERSION = "2026.03"
CONSENTIMIENTO_RESUMEN = (
    "Autorizo a Congente para validar mi identidad, consultar información en centrales de riesgo, "
    "usar mis datos dentro del proceso de crédito digital y conservar la evidencia de esta aceptación."
)

APELLIDO_REGEX = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]+$")


CONSENTIMIENTO_RESUMEN_CENTRALES = (
    "Autorizo a Congente para consultar mi informacion en centrales de riesgo y conservar la evidencia "
    "de esta aceptacion dentro del proceso de credito digital."
)


def _sanitize_valor_activos(core_data: dict, form_data: dict) -> dict:
    data = dict(form_data or {})
    core_activos = str((core_data or {}).get("activos") or "").strip().lower()
    current = str(data.get("valor_activos") or "").replace(".", "").replace(",", "").strip()
    if core_activos.startswith("más de $150") and current == "150000000":
        data["valor_activos"] = ""
    return data


def _validate_primer_apellido(value: str) -> str:

    normalized = " ".join((value or "").strip().upper().split())
    if not normalized:
        raise serializers.ValidationError("El primer apellido es obligatorio.")
    if not APELLIDO_REGEX.fullmatch(normalized):
        raise serializers.ValidationError("El primer apellido solo permite letras y espacios.")
    return normalized


def _normalize_colombia_mobile(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("57") and len(digits) == 12:
        digits = digits[2:]
    if re.fullmatch(r"3\d{9}", digits):
        return digits
    raise serializers.ValidationError("El celular debe ser un n?mero m?vil colombiano v?lido.")


class ConsumoSolicitudCreateSerializer(serializers.Serializer):
    tipo_identificacion = serializers.ChoiceField(choices=TipoIdentificacion.choices)
    numero_identificacion = serializers.RegexField(r"^\d{6,10}$")
    primer_apellido = serializers.CharField(max_length=80)
    fecha_expedicion = serializers.DateField(validators=[validate_document_issue_date])
    celular = serializers.CharField(max_length=20)
    email = serializers.EmailField()

    def validate(self, attrs):
        validate_credit_policy(attrs["tipo_identificacion"], attrs["fecha_expedicion"])
        attrs["primer_apellido"] = _validate_primer_apellido(attrs["primer_apellido"])
        attrs["celular"] = _normalize_colombia_mobile(attrs["celular"])
        # La validacion primaria de solicitud activa vive en el endpoint de consulta
        # del paso 1. Esta segunda validacion protege el create frente a carreras o
        # llamadas directas al endpoint sin pasar por la consulta previa.
        duplicate_request, detail, resume_available = find_active_duplicate_request(
            self.context["request"].user,
            attrs["tipo_identificacion"],
            attrs["numero_identificacion"],
        )
        self.reused_existing = False
        if duplicate_request["has_active"] and resume_available and detail is not None:
            self.reused_existing = True
            attrs["_existing_solicitud"] = detail.solicitud
            return attrs

        if duplicate_request["has_active"]:
            raise serializers.ValidationError(
                "Ya existe una solicitud activa de consumo para esta identificacion."
            )
        return attrs

    def create(self, validated_data):
        existing_solicitud = validated_data.pop("_existing_solicitud", None)
        if existing_solicitud is not None:
            return existing_solicitud

        advisor = self.context["request"].user
        applicant, _ = Solicitante.objects.update_or_create(
            tipo_identificacion=validated_data["tipo_identificacion"],
            numero_identificacion=validated_data["numero_identificacion"],
            defaults={
                "primer_apellido": validated_data["primer_apellido"],
                "fecha_expedicion": validated_data["fecha_expedicion"],
                "celular": validated_data["celular"],
                "email": validated_data["email"],
            },
        )
        solicitud = Solicitud.objects.create(
            solicitante=applicant,
            asesor=advisor,
            producto="CONSUMO_REAL",
            estado=EstadoSolicitud.CONSENTIMIENTO_PENDIENTE,
            paso_actual="consentimiento",
        )
        SolicitudConsumo.objects.create(
            solicitud=solicitud,
            estado=EstadoSolicitudConsumo.CONSENTIMIENTO_PENDIENTE,
        )
        return solicitud


class ConsumoOrchestrationPreviewSerializer(serializers.Serializer):
    tipo_identificacion = serializers.ChoiceField(choices=TipoIdentificacion.choices)
    numero_identificacion = serializers.RegexField(r"^\d{6,10}$")
    primer_apellido = serializers.CharField(max_length=80)
    fecha_expedicion = serializers.DateField(validators=[validate_document_issue_date])
    celular = serializers.CharField(max_length=20)
    email = serializers.EmailField()

    def validate(self, attrs):
        validate_credit_policy(attrs["tipo_identificacion"], attrs["fecha_expedicion"])
        attrs["primer_apellido"] = _validate_primer_apellido(attrs["primer_apellido"])
        attrs["celular"] = _normalize_colombia_mobile(attrs["celular"])
        return attrs


class OtpSendSerializer(serializers.Serializer):
    canal = serializers.ChoiceField(choices=CanalOtp.choices, default=CanalOtp.SMS)


class OtpVerifySerializer(serializers.Serializer):
    codigo = serializers.RegexField(r"^\d{4,8}$")


class ConsentimientoSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    version = serializers.CharField(max_length=32, default=CONSENTIMIENTO_VERSION)
    canal = serializers.ChoiceField(choices=CanalOtp.choices)
    text_snapshot = serializers.CharField(required=False, allow_blank=True)
    accepted_documents = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False,
    )

    def validate(self, attrs):
        if not attrs["accepted"]:
            raise serializers.ValidationError("Debes aceptar el consentimiento para continuar.")
        normalized_documents = []
        expected_ids = {document.id for document in get_active_documents()}
        received_ids = set()

        for item in attrs["accepted_documents"]:
            try:
                document_id = int(item["document_id"])
                viewed_seconds = int(item["viewed_seconds"])
                reached_end = bool(item["reached_end"])
            except (KeyError, TypeError, ValueError):
                raise serializers.ValidationError(
                    "Cada documento debe incluir document_id, viewed_seconds y reached_end."
                )

            if viewed_seconds <= 0:
                raise serializers.ValidationError(
                    "Debes registrar tiempo de visualizacion para cada documento."
                )
            if not reached_end:
                raise serializers.ValidationError(
                    "Debes llegar al final de cada PDF antes de aceptarlo."
                )

            received_ids.add(document_id)
            normalized_documents.append(
                {
                    "document_id": document_id,
                    "viewed_seconds": viewed_seconds,
                    "reached_end": reached_end,
                }
            )

        if expected_ids != received_ids:
            raise serializers.ValidationError(
                "Debes aceptar todos los documentos vigentes antes de continuar."
            )
        attrs["accepted_documents"] = normalized_documents
        return attrs


class CoreConsultaSerializer(serializers.Serializer):
    numero_identificacion = serializers.CharField(required=False, allow_blank=True)


class FormularioConsumoSerializer(serializers.Serializer):
    form_data = serializers.JSONField()


class ConsumoProcessSerializer(serializers.Serializer):
    selected_hc2_keys = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )


class OtpChallengeSerializer(serializers.ModelSerializer):
    resend_available_in_seconds = serializers.SerializerMethodField()
    debug_code = serializers.SerializerMethodField()
    provider = serializers.CharField(read_only=True)
    transaction_uuid = serializers.CharField(read_only=True)

    class Meta:
        model = OtpChallenge
        fields = (
            "canal",
            "destino",
            "provider",
            "estado",
            "enviado_at",
            "expira_at",
            "verificado_at",
            "intentos",
            "max_intentos",
            "ultimo_error",
            "transaction_uuid",
            "resend_available_in_seconds",
            "debug_code",
        )

    def get_resend_available_in_seconds(self, obj):
        if not obj.enviado_at:
            return 0
        remaining = int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 30) or 30) - int(
            (timezone.now() - obj.enviado_at).total_seconds()
        )
        return max(0, remaining)

    def get_debug_code(self, obj):
        otp_mode = str(getattr(settings, "OTP_PROVIDER_MODE", "test" if settings.DEBUG else "real")).strip().lower()
        if otp_mode == "test":
            return obj.codigo
        return None


class ConsentimientoConsumoSerializer(serializers.ModelSerializer):
    otp_verified_at = serializers.DateTimeField(source="otp.verificado_at", read_only=True)
    pdf_consentimiento_url = serializers.SerializerMethodField()

    class Meta:
        model = ConsentimientoConsumo
        fields = (
            "version",
            "aceptado",
            "firmado",
            "canal",
            "fecha_aceptacion",
            "ip_address",
            "user_agent",
            "text_hash",
            "tipo_firma",
            "otp_verified_at",
            "pdf_consentimiento_url",
        )

    def get_pdf_consentimiento_url(self, obj):
        if not obj.pdf_consentimiento:
            return ""
        try:
            return obj.pdf_consentimiento.url
        except Exception:
            return ""


class EvaluacionConsumoSerializer(serializers.ModelSerializer):
    solicitud = SolicitudStatusSerializer(read_only=True)

    class Meta:
        model = EvaluacionConsumo
        fields = (
            "solicitud",
            "puntaje_xcore",
            "perfil_riesgo",
            "perfil_credito",
            "capacidad_pago_final",
            "decision_final",
            "estamento",
            "tiene_novedad",
            "novedad_descripcion",
            "monto_max_posible",
            "valor_cuota",
            "vida_deudores",
            "resultados",
            "created_at",
        )


class SolicitudConsumoStatusSerializer(serializers.ModelSerializer):
    solicitud = SolicitudStatusSerializer(read_only=True)
    evaluacion = serializers.SerializerMethodField()
    otp = serializers.SerializerMethodField()
    consentimiento = serializers.SerializerMethodField()
    consent_copy = serializers.SerializerMethodField()
    wizard_step = serializers.SerializerMethodField()
    orchestration = serializers.SerializerMethodField()
    form_data = serializers.SerializerMethodField()

    class Meta:
        model = SolicitudConsumo
        fields = (
            "solicitud",
            "estado",
            "wizard_step",
            "oracle_consultado",
            "documentos_autorizados",
            "selected_hc2_keys",
            "core_data",
            "form_data",
            "ultimo_error",
            "otp",
            "consentimiento",
            "consent_copy",
            "orchestration",
            "evaluacion",
            "created_at",
            "updated_at",
        )

    def get_form_data(self, obj):
        data = _sanitize_valor_activos(obj.core_data or {}, obj.form_data or {})
        data["tasa_cupos_rotativos"] = str(settings.XCORE_CONSUMO_TASA_CUPOS_ROTATIVOS)
        return data

    def get_orchestration(self, obj):
        orchestration = dict(obj.orchestration_data or {})
        valores = dict(orchestration.get("valores_consolidados") or {})
        if obj.core_data.get("activos") not in (None, ""):
            valores["activos"] = obj.core_data.get("activos")
        if obj.core_data.get("valor_activos") not in (None, ""):
            valores["valor_activos"] = obj.core_data.get("valor_activos")
            orchestration["campos_editables"] = [
                field for field in list(orchestration.get("campos_editables") or []) if field not in {"valor_activos", "activos"}
            ]
            orchestration["campos_faltantes"] = [
                field for field in list(orchestration.get("campos_faltantes") or []) if field not in {"valor_activos", "activos"}
            ]
        valores["tasa_cupos_rotativos"] = settings.XCORE_CONSUMO_TASA_CUPOS_ROTATIVOS
        orchestration["valores_consolidados"] = valores
        return orchestration

    def get_evaluacion(self, obj):
        if not hasattr(obj.solicitud, "evaluacion_consumo"):
            return None
        return EvaluacionConsumoSerializer(obj.solicitud.evaluacion_consumo).data

    def get_otp(self, obj):
        challenge = obj.solicitud.active_otp_challenge
        if challenge is None:
            return None
        return OtpChallengeSerializer(challenge).data

    def get_consentimiento(self, obj):
        if not hasattr(obj.solicitud, "consentimiento_consumo"):
            return None
        return ConsentimientoConsumoSerializer(obj.solicitud.consentimiento_consumo).data

    def get_consent_copy(self, obj):
        return {
            "version": CONSENTIMIENTO_VERSION,
            "summary": CONSENTIMIENTO_RESUMEN_CENTRALES,
        }

    def get_wizard_step(self, obj):
        estado = obj.estado
        preselecta_gate = obj.orchestration_data.get("datos_preselecta", {}) if obj.orchestration_data else {}
        if preselecta_gate.get("puede_continuar") is False and preselecta_gate.get("estado_negocio"):
            return "resultado"
        if estado == EstadoSolicitudConsumo.FINALIZADO:
            return "resultado"
        if estado in (
            EstadoSolicitudConsumo.CONSENTIMIENTO_FIRMADO,
            EstadoSolicitudConsumo.CORE_CONSULTADO,
            EstadoSolicitudConsumo.FORMULARIO_XCORE_OK,
            EstadoSolicitudConsumo.PROCESANDO,
        ):
            return "analisis"
        if estado == EstadoSolicitudConsumo.CONSENTIMIENTO_PENDIENTE:
            return "consentimiento"
        if estado in (EstadoSolicitudConsumo.OTP_PENDIENTE, EstadoSolicitudConsumo.OTP_VALIDADA):
            return "otp"
        return "formulario"
