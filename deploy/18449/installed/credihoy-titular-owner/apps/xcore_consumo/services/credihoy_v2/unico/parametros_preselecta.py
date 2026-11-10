"""Captura explicita del catalogo PRESELECTA, no equivalencias de codigos LINIX."""
from django.db import transaction
from django.utils import timezone

from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import SolicitudConsumo
from .consentimiento_real import obtener, validar_documento

# Contrato vigente: Preselecta/integrations/services/catalogs.py.
CATALOGOS = {
    "LINEA_CREDITO": {"1": "Consumo", "2": "Comercial", "3": "Microcredito"},
    "TIPO_ASOCIADO": {"1": "Nuevo", "2": "Renovacion", "3": "No aplica"},
    "MEDIO_PAGO": {"1": "Ventanilla", "2": "Nomina", "3": "No aplica"},
    "ACTIVIDAD": {"1": "Empleado", "2": "Pensionado", "3": "Independiente"},
}


def validar_parametros(valores):
    if (type(valores) is not dict or set(valores) != set(CATALOGOS)
            or any(type(v) is not str or v not in CATALOGOS[k] for k, v in valores.items())):
        raise OperacionError("PARAMETROS_PRESELECTA_INVALIDOS")
    return dict(valores)


def capturar(solicitud_id, operador, *, valores, confirmado):
    # Catalogos conservados; no se capturan parametros PRESELECTA para UNICO.
    from .consulta_controlada import PRODUCTO
    from apps.solicitudes.models import Solicitud
    if Solicitud.objects.filter(pk=solicitud_id, producto=PRODUCTO).exists():
        raise OperacionError("PRESELECTA_NO_APLICA_CREDIHOY_UNICO")
    if confirmado is not True:
        raise OperacionError("PARAMETROS_PRESELECTA_CONFIRMACION_REQUERIDA")
    valores = validar_parametros(valores)
    with transaction.atomic():
        s = obtener(solicitud_id, operador, lock=True)
        c = s.consentimiento_consumo
        validar_documento(s, c)
        if not c.firmado:
            raise OperacionError("CONSENTIMIENTO_FIRMADO_REQUERIDO")
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        anterior = detail.form_data.get("parametros_preselecta")
        if anterior is not None:
            if anterior != valores:
                raise OperacionError("PARAMETROS_PRESELECTA_EXISTENTES_NO_SOBRESCRIBIR")
            return
        if OperacionGateway.objects.filter(solicitud=s).exists():
            raise OperacionError("PARAMETROS_PRESELECTA_OPERACION_EXISTENTE")
        detail.form_data = {**detail.form_data, "parametros_preselecta": valores,
            "parametros_preselecta_evidencia": {"fuente": "CONFIRMACION_EXPLICITA_OPERADOR",
                "catalogo": "PRESELECTA_CATALOGS_V1", "fecha": timezone.now().isoformat(),
                "operadorId": operador.pk, "producto": "390", "modalidad": "111", "destinacion": "CFA",
                "lineaLinix": "LIV", "equivalenciaAutomaticaLinix": False}}
        detail.save(update_fields=["form_data", "updated_at"])
        AuditLog.objects.create(solicitud=s, event="credihoy_parametros_preselecta_confirmados",
            actor=f"user:{operador.pk}", payload={"catalogo": "PRESELECTA_CATALOGS_V1",
                "campos": list(CATALOGOS), "consultasEjecutadas": 0})


def representacion(s):
    valores = s.consumo_detail.form_data.get("parametros_preselecta")
    if valores is not None:
        validar_parametros(valores)
    return {"catalogos": CATALOGOS, "valores": valores, "confirmados": valores is not None,
        "productoLinix": {"producto": "390", "modalidad": "111", "destinacion": "CFA", "linea": "LIV"}}
