from .client import XcoreClient
from .models import XcoreConsulta
from .serializers import XcoreResponseSerializer


def run_xcore(solicitud, preselecta_consulta, historial_consulta):
    applicant = solicitud.solicitante
    payload = {
        "numero_solicitud": solicitud.numero_solicitud,
        "solicitante": {
            "tipo_identificacion": applicant.tipo_identificacion,
            "numero_identificacion": applicant.numero_identificacion,
            "primer_apellido": applicant.primer_apellido,
            "celular": applicant.celular,
            "email": applicant.email,
        },
        "preselecta": preselecta_consulta.response_payload,
        "historial_pago": historial_consulta.response_payload,
        "producto": "CONSUMO",
    }
    response = XcoreClient().evaluate(payload)
    serializer = XcoreResponseSerializer(data=response)
    serializer.is_valid(raise_exception=True)
    consulta, _ = XcoreConsulta.objects.update_or_create(
        solicitud=solicitud,
        defaults={
            "estado": serializer.validated_data["estado"],
            "request_payload": payload,
            "response_payload": serializer.validated_data,
            "resultado": serializer.validated_data["resultado"],
            "mensaje": serializer.validated_data["mensaje"],
        },
    )
    return serializer.validated_data, consulta
