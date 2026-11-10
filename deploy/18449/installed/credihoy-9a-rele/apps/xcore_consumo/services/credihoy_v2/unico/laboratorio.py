"""Dataset inventado para CD3C. Nunca obtiene hechos de usuarios o proveedores."""

from datetime import datetime, timezone
from decimal import Decimal
import re

from ..base import EstadoCertificacion as EC, Hecho
from .dto import (
    CorrespondenciaRecogidaHDCV2, EntradaCredihoyUnicoV2, OrigenCuotaRecogida,
    RecogidaCredihoyUnicoV2, ReferenciaDocumentoUnicoV2,
)
from .motor import evaluar_capacidad_unico_v2
from .reglas import (HechosReglasUnicoV2, evaluar_reglas_unico_v2,
                     estado_evaluacion_regla, datos_faltantes_regla)
from .inventario import inventario_reglas_unico_v2

ESCENARIOS = ("POSITIVA", "CERO", "NEGATIVA", "SIN_INFORMACION", "RECOGIDA_PERMITIDA",
              "RECOGIDA_NO_PERMITIDA", "RECOGIDA_SIN_CORRESPONDENCIA", "ERROR_TECNICO")
REFERENCIA = "LOCAL-OBLIGACION-FICTICIA"
PRODUCTO = "CREDIHOY UNICO (NUEVO)"


class RequestLaboratorioInvalido(ValueError):
    pass


def validar_request(payload):
    keys = {"producto", "escenario", "monto", "plazoMeses", "periodicidadPago", "recogida"}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise RequestLaboratorioInvalido("ESTRUCTURA_INVALIDA")
    if payload["producto"] != PRODUCTO or payload["escenario"] not in ESCENARIOS:
        raise RequestLaboratorioInvalido("PRODUCTO_O_ESCENARIO_INVALIDO")
    monto = payload["monto"]
    if monto is not None and (type(monto) is not str or not re.fullmatch(r"[0-9]{1,18}(?:\.[0-9]{1,2})?", monto)):
        raise RequestLaboratorioInvalido("MONTO_INVALIDO")
    plazo = payload["plazoMeses"]
    if plazo is not None and (type(plazo) is not int or not 0 <= plazo <= 999):
        raise RequestLaboratorioInvalido("PLAZO_INVALIDO")
    periodo = payload["periodicidadPago"]
    if periodo is not None and (type(periodo) is not str or not periodo.strip() or len(periodo) > 32):
        raise RequestLaboratorioInvalido("PERIODICIDAD_INVALIDA")
    recogida = payload["recogida"]
    if (not isinstance(recogida, dict) or set(recogida) != {"aplica", "referencia"}
            or type(recogida["aplica"]) is not bool
            or recogida["referencia"] != (REFERENCIA if recogida["aplica"] else None)):
        raise RequestLaboratorioInvalido("RECOGIDA_INVALIDA")
    return payload


def _hecho(valor, ruta, *, certificado=False):
    return Hecho(valor=valor, disponible=valor is not None, fuente="SINTETICO_CD3C",
        referencia_evidencia="LAB-CD3C-INVENTADO", ruta_origen=ruta,
        estado_certificacion=(EC.CERTIFICADO if certificado else EC.OBSERVADO)
            if valor is not None else EC.NO_DISPONIBLE,
        observacion="Hecho inventado, no evidencia de una persona ni certificacion productiva")


def evaluar_laboratorio(payload, request_id):
    p = validar_request(payload)
    if p["escenario"] == "ERROR_TECNICO":
        raise RuntimeError("Fallo sintetico de laboratorio")
    ingresos, hdc = Decimal("4000000"), Decimal("700000")
    if p["escenario"] in {"CERO", "NEGATIVA"}:
        ingresos = Decimal("1400000")
        hdc = Decimal("750000" if p["escenario"] == "CERO" else "750001")
    if p["escenario"] == "SIN_INFORMACION":
        ingresos = hdc = None
    seleccion = RecogidaCredihoyUnicoV2(aplica=False)
    if p["recogida"]["aplica"]:
        obligacion = ReferenciaDocumentoUnicoV2(tipo="OB", numero=REFERENCIA)
        cuota = _hecho(Decimal("300000"), "laboratorio.cuotaRecogida")
        seleccion = RecogidaCredihoyUnicoV2(aplica=True, producto_origen="CREDIHOY",
            modalidad_codigo="112" if p["escenario"] == "RECOGIDA_NO_PERMITIDA" else "111",
            solicitud_origen=ReferenciaDocumentoUnicoV2(tipo="SC", numero="LOCAL-SOLICITUD-FICTICIA"),
            obligacion=obligacion, cuota_actual=cuota, periodicidad_cuota="MENSUAL",
            origen_cuota=OrigenCuotaRecogida.LINIX,
            correspondencia_hdc=None if p["escenario"] == "RECOGIDA_SIN_CORRESPONDENCIA" else
                CorrespondenciaRecogidaHDCV2(referencia_agregado_hdc="LAB-CD3C-INVENTADO",
                    obligacion=obligacion, cuota_incluida=_hecho(Decimal("300000"),
                        "laboratorio.inclusionRecogida", certificado=True)))
    entrada = EntradaCredihoyUnicoV2(monto_solicitado=None if p["monto"] is None else Decimal(p["monto"]),
        plazo_meses=p["plazoMeses"], periodicidad_pago=p["periodicidadPago"], modalidad_codigo="111",
        recogida=seleccion)
    capacidad = evaluar_capacidad_unico_v2(entrada,
        ingresos_mensuales=_hecho(ingresos, "laboratorio.ingresos"),
        cuotas_deudas_hdc_brutas=_hecho(hdc, "laboratorio.cuotasHdc"))
    hechos = HechosReglasUnicoV2() if p["escenario"] == "SIN_INFORMACION" else HechosReglasUnicoV2(
        edad_anios=_hecho(18, "laboratorio.edad", certificado=True),
        persona_natural=_hecho(True, "laboratorio.personaNatural", certificado=True),
        consulta_central_realizada=_hecho(True, "laboratorio.consultaSimulada", certificado=True),
        cuentas_embargadas=_hecho(False, "laboratorio.embargo", certificado=True),
        calificacion_interna=_hecho("A", "laboratorio.calificacion", certificado=True),
        smmlv_vigente=_hecho(Decimal("2000000"), "laboratorio.smmlvInventado", certificado=True))
    # C4 deliberadamente ausente: no se fabrica un saldo utilizable.
    reglas = evaluar_reglas_unico_v2(entrada, hechos=hechos, capacidad=capacidad)
    return serializar_evaluacion(capacidad, reglas, request_id)


from .presentacion import serializar_evaluacion
