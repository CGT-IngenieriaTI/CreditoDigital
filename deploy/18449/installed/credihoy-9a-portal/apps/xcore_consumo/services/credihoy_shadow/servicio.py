"""Shadow post-commit: solo lecturas del flujo y nuevas filas de AuditLog."""

from dataclasses import fields
import logging
import re
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.historial_pago.models import HistorialPagoConsulta
from apps.preselecta.models import PreselectaConsulta
from apps.solicitudes.models import Solicitud
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from ..credihoy import Hecho, PoliticaCrediHoy
from ..credihoy_integracion.cliente import ApiDataCrediHoyClient, ConfiguracionApiData
from ..credihoy_integracion.dto import VERSION_CONTRATO
from ..credihoy_integracion.internos import AdaptadorHechosInternos
from ..credihoy_integracion.servicio import ServicioEvaluacionCrediHoy
from .evidencia import disponibilidad_externa, formulario_shadow, recuperar_externos


logger = logging.getLogger("credito.shadow")
VERSION_POLITICA = "CREDIHOY-SHADOW-D1D2-1"


def _request_id(valor):
    return valor if isinstance(valor, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", valor) else str(uuid4())


def _persistir(solicitud_id, actor, request_id, payload, *, error=False):
    try:
        # Savepoint propio: un error de auditoria no contamina transacciones ajenas.
        with transaction.atomic():
            AuditLog.objects.create(
                solicitud_id=solicitud_id, event="credihoy_shadow_error" if error else "credihoy_shadow_evaluado",
                actor=actor[:120], request_id=request_id[:120],
                level=AuditLog.Levels.WARNING if error else AuditLog.Levels.INFO, payload=payload,
            )
    except Exception:
        logger.warning("credihoy_shadow.auditoria_no_disponible")


def _disponibilidad(contexto):
    resultado = {}
    for nombre, objeto in (("formulario", contexto), ("interno", contexto.interno),
                            ("datacredito", contexto.datacredito)):
        resultado[nombre] = {}
        for campo in fields(objeto):
            hecho = getattr(objeto, campo.name)
            if isinstance(hecho, Hecho):
                resultado[nombre][campo.name] = {
                    "fuente": hecho.fuente, "disponible": hecho.disponible, "certificacion": hecho.estado,
                    "periodo": hecho.periodo,
                    "fecha_corte": hecho.fecha_corte.isoformat() if hecho.fecha_corte else None,
                }
    for campo in ("producto", "monto", "plazo", "garantia", "forma_pago"):
        resultado["formulario"][campo] = {
            "fuente": "SOLICITUD" if campo == "producto" else "FORMULARIO",
            "disponible": getattr(contexto, campo) is not None,
        }
    return resultado


class ServicioEvaluacionCrediHoyShadow:
    def __init__(self, *, integracion=None):
        self.integracion = integracion

    def evaluar(self, solicitud, detail=None, *, actor="", request_id=None, evaluacion_actualizada=None,
                fecha_evaluacion=None, devolver_payload=False, incluir_reporte_piloto=False):
        request_id = _request_id(request_id)
        detail = detail or SolicitudConsumo.objects.get(solicitud=solicitud)
        evaluacion = EvaluacionConsumo.objects.filter(solicitud=solicitud).first()
        if evaluacion_actualizada is not None and (
                evaluacion is None or evaluacion.updated_at != evaluacion_actualizada):
            raise ValueError("SHADOW_SNAPSHOT_REEMPLAZADO")
        fecha = fecha_evaluacion or timezone.localtime().replace(microsecond=0)
        form_snapshot = (evaluacion.input_snapshot or {}).get("form_data") if evaluacion else None
        contexto = formulario_shadow(solicitud, detail, fecha.date(), form_data=form_snapshot)
        preselecta = PreselectaConsulta.objects.filter(solicitud=solicitud).first()
        historial = HistorialPagoConsulta.objects.filter(solicitud=solicitud).first()
        externos, errores = recuperar_externos(
            solicitud, evaluacion, preselecta=preselecta, historial=historial,
        )
        # Baseline de observacion para esta fecha, no una politica crediticia productiva.
        politica = PoliticaCrediHoy(version=VERSION_POLITICA, vigencia_desde=fecha.date(), vigencia_hasta=fecha.date())
        integracion = self.integracion or ServicioEvaluacionCrediHoy(
            cliente=ApiDataCrediHoyClient(ConfiguracionApiData.desde_settings()),
            adaptador=AdaptadorHechosInternos(), politica=politica,
        )
        integrada = integracion.evaluar(
            formulario=contexto, numero_documento=solicitud.solicitante.numero_identificacion,
            fecha_evaluacion=fecha, datacredito=externos, request_id=request_id,
        )
        payload = integrada.resultado.a_dict()
        importacion = (evaluacion.integraciones_snapshot or {}).get("shadow_import") if evaluacion else None
        if importacion:
            payload["fechas_evidencia"] = dict(importacion["fechas_consulta"])
        codigo_error = integrada.evidencia_interna.codigo_error
        payload.update(
            esquema="credihoy-shadow-v1", solicitud_id=str(solicitud.pk), request_id=request_id,
            fecha_evaluacion=fecha.isoformat(), version_contrato_interno=VERSION_CONTRATO,
            version_contrato_recibida=integrada.evidencia_interna.version_contrato,
            autorizado_decision_crediticia=False, hechos=_disponibilidad(integrada.contexto),
            errores_tecnicos=[*errores, *([codigo_error] if codigo_error else [])],
            habilitado_decision_productiva=integrada.evidencia_interna.habilitado_decision_productiva,
            referencias_evidencia={"evaluacion_consumo_id": evaluacion.pk if evaluacion else None,
                                  "preselecta_consulta_id": preselecta.pk if preselecta else None,
                                  "historial_consulta_id": historial.pk if historial else None},
            c4={"saldo_individual_observado_disponible": integrada.evidencia_interna.saldo_individual_observado is not None,
                "saldo_certificado": None, "utilizable_decision": False,
                "motivo": integrada.evidencia_interna.motivo_aportes},
        )
        if incluir_reporte_piloto:
            interno, evidencia = integrada.contexto.interno, integrada.evidencia_interna
            periodo = interno.periodo_anterior
            payload["reporte_piloto"] = {
                "C1": {"disponible": periodo.disponible, "periodo": periodo.valor,
                       "corte": periodo.fecha_corte.isoformat() if periodo.fecha_corte else None},
                "C2": {"estado": evidencia.estado_cartera,
                       "cantidad_obligaciones": len(interno.obligaciones_internas.valor)
                       if interno.obligaciones_internas.valor is not None else
                       (0 if evidencia.estado_cartera == "SIN_REGISTROS_AL_CORTE" else None)},
                "C3": {"disponible": interno.cuota_vigente_interna.disponible, "estado": evidencia.estado_cuota},
                "C4": {"estado": evidencia.estado_aportes,
                       "numero_posiciones": len(evidencia.saldos_documentos) if evidencia.estado_aportes else None,
                       "saldoIndividualObservado": str(evidencia.saldo_individual_observado)
                       if evidencia.saldo_individual_observado is not None else None,
                       "utilizableDecision": False},
                **disponibilidad_externa(externos),
            }
        if devolver_payload:
            return payload
        _persistir(solicitud.pk, actor, request_id, payload, error=bool(payload["errores_tecnicos"]))
        return integrada.resultado


def ejecutar_shadow_seguro(solicitud_id, *, actor="", request_id=None, evaluacion_actualizada=None):
    if not settings.CREDIHOY_SHADOW_ENABLED:
        return None
    request_id = _request_id(request_id)
    try:
        solicitud = Solicitud.objects.select_related("solicitante").get(pk=solicitud_id)
        return ServicioEvaluacionCrediHoyShadow().evaluar(
            solicitud, actor=actor, request_id=request_id, evaluacion_actualizada=evaluacion_actualizada)
    except Exception:
        _persistir(solicitud_id, actor, request_id, {
            "esquema": "credihoy-shadow-v1", "solicitud_id": str(solicitud_id), "request_id": request_id,
            "fecha_evaluacion": timezone.now().isoformat(), "version_politica": VERSION_POLITICA,
            "version_contrato_interno": VERSION_CONTRATO, "estado_global": "INFORMACION_INSUFICIENTE",
            "autorizado_decision_crediticia": False, "motivos": ["SHADOW_ERROR_TECNICO"],
            "reglas": [], "hechos": {}, "errores_tecnicos": ["SHADOW_ERROR_INESPERADO"],
        }, error=True)
        return None


def programar_shadow(solicitud_id, *, actor="", request_id=None, evaluacion_actualizada=None):
    if settings.CREDIHOY_SHADOW_ENABLED:
        from .asincrono import encolar_shadow
        # La version del snapshot hace estable la fecha incluso en dobles enqueue.
        version = evaluacion_actualizada or timezone.now()
        payload = dict(solicitud_id=str(solicitud_id), request_id=_request_id(request_id),
                       evaluacion_actualizada=version.isoformat(),
                       fecha_evaluacion=timezone.localtime(version).replace(microsecond=0).isoformat(),
                       version_politica=VERSION_POLITICA)
        transaction.on_commit(lambda: encolar_shadow(**payload), robust=True)
