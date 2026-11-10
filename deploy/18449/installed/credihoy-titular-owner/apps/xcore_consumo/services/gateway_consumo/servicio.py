from django.conf import settings
from django.db import connection

from apps.xcore_consumo.models import SolicitudConsumo
from .adaptador import DatosGatewayConsumo, RequestsGatewayConsumo, adaptar_hdc, adaptar_preselecta
from .errores import EvaluacionGatewayBloqueada


def _autorizacion_local():
    if getattr(settings, "RISK_GATEWAY_CONSUMO_ENABLED", False) is not True:
        raise EvaluacionGatewayBloqueada("GATEWAY_CONSUMO_DESACTIVADO")
    if settings.DEBUG is not True or connection.vendor != "sqlite":
        raise EvaluacionGatewayBloqueada("GATEWAY_CONSUMO_SOLO_LOCAL")


def _detalle_financiero(resultado, proveedor):
    if resultado.proveedor != proveedor:
        raise EvaluacionGatewayBloqueada("OPERACION_PROVEEDOR_INCONSISTENTE")
    if resultado.estado == "SIN_INFORMACION":
        raise EvaluacionGatewayBloqueada("GATEWAY_SIN_INFORMACION")
    if resultado.estado != "COMPLETADA":
        raise EvaluacionGatewayBloqueada("GATEWAY_OPERACION_NO_EVALUABLE")
    if resultado.respuesta is None:
        raise EvaluacionGatewayBloqueada("GATEWAY_DETALLE_NO_RECUPERABLE")
    return resultado.respuesta


def obtener_datos_gateway(solicitud_id, requests):
    _autorizacion_local()
    if type(requests) is not RequestsGatewayConsumo:
        raise EvaluacionGatewayBloqueada("REQUESTS_GATEWAY_REQUERIDOS")
    try:
        detail = SolicitudConsumo.objects.select_related("solicitud__solicitante").get(solicitud_id=solicitud_id)
    except SolicitudConsumo.DoesNotExist:
        raise EvaluacionGatewayBloqueada("SOLICITUD_CONSUMO_NO_DISPONIBLE") from None
    if detail.solicitud.producto not in {"CONSUMO", "CONSUMO_REAL"}:
        raise EvaluacionGatewayBloqueada("PRODUCTO_GATEWAY_CONSUMO_NO_SOPORTADO")
    if not detail.oracle_consultado or type(detail.form_data) is not dict or not detail.form_data:
        raise EvaluacionGatewayBloqueada("CONTEXTO_CONSUMO_INCOMPLETO")
    # La API existente crea CONSUMO_REAL; no inferir CrediHoy desde esa etiqueta.
    if str(detail.form_data.get("tipo_credito", "")).strip().casefold() != "libre inversion":
        raise EvaluacionGatewayBloqueada("TIPO_CREDITO_GATEWAY_NO_HOMOLOGADO")
    from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
    from apps.integraciones.risk_gateway.operaciones.servicio import OrquestadorGateway
    orchestrator = OrquestadorGateway()
    try:
        # Ambos requests y consentimientos se validan antes del primer envio.
        orchestrator.preparar(solicitud_id, requests.preselecta)
        orchestrator.preparar(solicitud_id, requests.hdc)
        pre = adaptar_preselecta(_detalle_financiero(orchestrator.ejecutar(solicitud_id, requests.preselecta), "PRESELECTA"))
        if not pre.decision.disponible:
            raise EvaluacionGatewayBloqueada("PRESELECTA_RESULTADO_NO_DISPONIBLE")
        from apps.xcore_consumo.services.orchestration import normalize_preselecta_business_status
        # Reutilizar la politica de continuidad existente, NO una regla de credito.
        if not normalize_preselecta_business_status(pre.para_evaluador())["puede_continuar"]:
            raise EvaluacionGatewayBloqueada("PRESELECTA_CONTINUIDAD_NO_AUTORIZADA")
        hdc = adaptar_hdc(_detalle_financiero(orchestrator.ejecutar(solicitud_id, requests.hdc), "HDC"))
    except OperacionError as exc:
        raise EvaluacionGatewayBloqueada(exc.codigo) from None
    return detail, DatosGatewayConsumo(pre, hdc)


def procesar_consumo_gateway(solicitud, *, requests, selected_hc2_keys=None):
    # No pasar al procesamiento legacy tras un bloqueo, ni persistir snapshots.
    if selected_hc2_keys is not None:
        raise EvaluacionGatewayBloqueada("RECOGIDA_HC2_GATEWAY_NO_HOMOLOGADA")
    detail, datos = obtener_datos_gateway(solicitud.pk, requests)
    datos.entrada_evaluador(detail.form_data)
    # Defensa adicional: esta fase NO autoriza persistencia de decision/radicacion.
    raise EvaluacionGatewayBloqueada("FINALIZACION_GATEWAY_NO_AUTORIZADA")
