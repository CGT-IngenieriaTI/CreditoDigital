from rest_framework import serializers

from apps.documentos.services import get_active_documents
from apps.usuarios.models import Solicitante, TipoIdentificacion, resolve_user_role
from apps.utils.validators import validate_credit_policy, validate_document_issue_date

from .models import EstadoSolicitud, Solicitud


class SolicitudStartSerializer(serializers.Serializer):
    tipo_identificacion = serializers.ChoiceField(choices=TipoIdentificacion.choices)
    numero_identificacion = serializers.RegexField(r"^\d{6,15}$")
    primer_apellido = serializers.CharField(max_length=80)
    fecha_expedicion = serializers.DateField(validators=[validate_document_issue_date])
    celular = serializers.RegexField(r"^3\d{9}$")
    email = serializers.EmailField()

    def validate(self, attrs):
        validate_credit_policy(attrs["tipo_identificacion"], attrs["fecha_expedicion"])
        active_states = (
            EstadoSolicitud.INICIADA,
            EstadoSolicitud.AUTORIZADA,
            EstadoSolicitud.PRESELECTA_OK,
            EstadoSolicitud.HISTORIAL_OK,
            EstadoSolicitud.ENVIADA_XCORE,
        )
        duplicate_exists = Solicitud.objects.filter(
            solicitante__tipo_identificacion=attrs["tipo_identificacion"],
            solicitante__numero_identificacion=attrs["numero_identificacion"],
            estado__in=active_states,
        ).exists()
        if duplicate_exists:
            raise serializers.ValidationError(
                "Ya existe una solicitud activa para esta identificacion."
            )
        attrs["primer_apellido"] = attrs["primer_apellido"].strip().upper()
        return attrs

    def create(self, validated_data):
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
        return Solicitud.objects.create(solicitante=applicant)


class SolicitudAuthorizeSerializer(serializers.Serializer):
    accepted_documents = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False,
    )

    def validate(self, attrs):
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


class SolicitudStatusSerializer(serializers.ModelSerializer):
    solicitante = serializers.SerializerMethodField()
    asesor = serializers.SerializerMethodField()
    decision = serializers.SerializerMethodField()

    class Meta:
        model = Solicitud
        fields = (
            "id",
            "numero_solicitud",
            "estado",
            "paso_actual",
            "producto",
            "ultimo_error",
            "created_at",
            "updated_at",
            "solicitante",
            "asesor",
            "decision",
        )

    def get_solicitante(self, obj):
        applicant = obj.solicitante
        return {
            "tipo_identificacion": applicant.tipo_identificacion,
            "numero_identificacion": applicant.numero_identificacion,
            "primer_apellido": applicant.primer_apellido,
            "celular": applicant.celular,
            "email": applicant.email,
        }

    def get_decision(self, obj):
        if not hasattr(obj, "decision_final"):
            return None
        decision = obj.decision_final
        return {
            "resultado": decision.resultado,
            "mensaje": decision.mensaje,
            "monto_aprobado": decision.monto_aprobado,
        }

    def get_asesor(self, obj):
        if not obj.asesor_id:
            return None
        advisor = obj.asesor
        full_name = advisor.get_full_name().strip()
        return {
            "id": advisor.id,
            "username": advisor.username,
            "full_name": full_name or advisor.username,
            "role": resolve_user_role(advisor),
        }
