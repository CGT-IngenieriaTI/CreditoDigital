"""Frontera preparada para procesar_consumo; deliberadamente NO importada por el pipeline."""

from django.conf import settings


def consultar_gateway_para_consumo(solicitud_id, request):
    if getattr(settings, "RISK_GATEWAY_ORCHESTRATION_ENABLED", False) is not True:
        return None
    from .servicio import OrquestadorGateway
    return OrquestadorGateway().ejecutar(solicitud_id, request)
