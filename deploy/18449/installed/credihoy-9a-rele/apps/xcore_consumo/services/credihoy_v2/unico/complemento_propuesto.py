"""CONTRATO PROPUESTO, NO PUBLICADO. Adaptador offline, sin cliente ni endpoint.

No acepta credihoy-hechos-v2 ni se conecta al pipeline. Su aprobacion y
produccion de hechos certificados corresponden a api_data_v1/Congente.
"""

from dataclasses import replace
from datetime import timedelta
import re

from ..base import EstadoCertificacion as EC, instante
from ..errores_http import respuesta_invalida
from ..hechos import RequestHechosInternosV2
from ..internos_http import (
    adaptar_hecho_http, booleano, decimal_exacto, fecha as parse_fecha,
    fecha_hora, objeto, texto_no_vacio,
)
from .reglas import HechosReglasUnicoV2


VERSION_PROPUESTA = "credihoy-complemento-unico-propuesta-v1"


def adaptar_complemento_propuesto(payload, *, numero_documento, request_id, fecha,
                                 base: HechosReglasUnicoV2):
    instante(fecha, "fecha_evaluacion")
    try:
        RequestHechosInternosV2(numero_documento=numero_documento, fecha_evaluacion=fecha)
    except (ValueError, TypeError):
        respuesta_invalida()
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
        respuesta_invalida()
    if not isinstance(base, HechosReglasUnicoV2):
        raise TypeError("Requiere hechos UNICO")
    p = objeto(payload, {"versionContrato", "numeroDocumento", "requestId", "fechaConsulta", "fechaEvaluacion",
        "habilitadoDecisionProductiva", "personaNatural", "calificacionInternaGlobal", "alcanceCalificacion",
        "periodoCalificacion", "smmlvVigente", "vigenciaSmmlvDesde", "vigenciaSmmlvHasta",
        "referenciaNormaSmmlv", "saldoAportesUtilizable"})
    if (p["versionContrato"] != VERSION_PROPUESTA or p["numeroDocumento"] != numero_documento
            or p["requestId"] != request_id or p["habilitadoDecisionProductiva"] is not False
            or fecha_hora(p["fechaEvaluacion"]) != fecha):
        respuesta_invalida()
    consulta = fecha_hora(p["fechaConsulta"])
    persona = adaptar_hecho_http(p["personaNatural"], booleano, consulta=consulta)
    calificacion = adaptar_hecho_http(p["calificacionInternaGlobal"], texto_no_vacio, consulta=consulta)
    smmlv = adaptar_hecho_http(p["smmlvVigente"], decimal_exacto, consulta=consulta)
    aportes = adaptar_hecho_http(p["saldoAportesUtilizable"], decimal_exacto, consulta=consulta)
    cierre = fecha.date().replace(day=1) - timedelta(days=1)
    if calificacion.disponible:
        if (p["alcanceCalificacion"] != "ASOCIADO_COMPLETO_AL_CIERRE_ANTERIOR"
                or p["periodoCalificacion"] != cierre.strftime("%Y%m")
                or calificacion.fecha_corte != cierre):
            respuesta_invalida()
    elif p["alcanceCalificacion"] is not None or p["periodoCalificacion"] is not None:
        respuesta_invalida()
    if smmlv.disponible:
        desde, hasta = parse_fecha(p["vigenciaSmmlvDesde"]), parse_fecha(p["vigenciaSmmlvHasta"])
        texto_no_vacio(p["referenciaNormaSmmlv"])
        if smmlv.valor <= 0 or not desde <= fecha.date() <= hasta:
            respuesta_invalida()
    elif any(p[k] is not None for k in ("vigenciaSmmlvDesde", "vigenciaSmmlvHasta", "referenciaNormaSmmlv")):
        respuesta_invalida()
    # Certificacion requiere fuente, ruta y corte; no certificamos por consumir JSON.
    for h in (persona, calificacion, smmlv, aportes):
        if h.estado_certificacion == EC.CERTIFICADO and not (h.fuente and h.ruta_origen and h.fecha_corte):
            respuesta_invalida()
    return replace(base, persona_natural=persona, calificacion_interna=calificacion,
                   smmlv_vigente=smmlv, saldo_aportes_utilizable=aportes)
