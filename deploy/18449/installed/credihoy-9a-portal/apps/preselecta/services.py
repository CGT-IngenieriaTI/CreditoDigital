from .client import PreselectaClient
from .models import PreselectaConsulta
from .serializers import PreselectaResponseSerializer


def run_preselecta(solicitud):
    applicant = solicitud.solicitante
    payload = {
        "numero_identificacion": applicant.numero_identificacion,
        "tipo_identificacion": applicant.tipo_identificacion,
        "primer_apellido": applicant.primer_apellido,
        "numero_solicitud": solicitud.numero_solicitud,
    }
    response = PreselectaClient().evaluate(payload)
    normalized = PreselectaResponseSerializer(data=response)
    normalized.is_valid(raise_exception=True)
    consulta, _ = PreselectaConsulta.objects.update_or_create(
        solicitud=solicitud,
        defaults={
            "estado": normalized.validated_data["estado"],
            "request_payload": payload,
            "response_payload": normalized.validated_data,
            "preaprobado": normalized.validated_data["preaprobado"],
            "score": normalized.validated_data.get("score"),
            "mensaje": normalized.validated_data["mensaje"],
        },
    )
    return consulta
