from rest_framework import serializers

from .models import DecisionFinal


class DecisionFinalSerializer(serializers.ModelSerializer):
    numero_solicitud = serializers.CharField(source="solicitud.numero_solicitud", read_only=True)
    solicitante = serializers.SerializerMethodField()

    class Meta:
        model = DecisionFinal
        fields = (
            "numero_solicitud",
            "resultado",
            "mensaje",
            "observaciones",
            "monto_aprobado",
            "plazo_aprobado",
            "tasa_interes",
            "detalle",
            "created_at",
            "solicitante",
        )

    def get_solicitante(self, obj):
        applicant = obj.solicitud.solicitante
        return {
            "tipo_identificacion": applicant.tipo_identificacion,
            "numero_identificacion": applicant.numero_identificacion,
            "primer_apellido": applicant.primer_apellido,
            "celular": applicant.celular,
            "email": applicant.email,
        }
