"""Adaptacion explicita del contrato HTTP real credihoy-hechos-v2."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from .base import EstadoCertificacion as EC, Hecho
from .errores_http import ApiDataCrediHoyV2Error, respuesta_invalida
from .pagaduria import adaptar_pagaduria
from .hechos import (
    AportesSociales203V2, CarteraAlCierreV2, DocumentoAporte203V2,
    HechosInternosCrediHoyV2, MetadatosHechosInternosV2, ObligacionCierreV2,
    ObligacionesActualesV2, ObligacionInternaCrediHoyV2, CandidatoCuotaInternaCrediHoyV2,
    CatalogoInternoV2, SolicitudOrigenInternaV2,
    MatrizEconomicaSolicitudV2, ConceptoEconomicoSolicitudV2,
)


VERSION_CONTRATO = "credihoy-hechos-v2"
HECHO_KEYS = frozenset({"valor", "disponible", "fuente", "referenciaEvidencia", "rutaOrigen",
                        "fechaCorte", "fechaConsulta", "estadoCertificacion", "observacion"})


def objeto(valor, claves):
    if not isinstance(valor, dict) or set(valor) != set(claves):
        respuesta_invalida()
    return valor


def texto(valor):
    if not isinstance(valor, str):
        respuesta_invalida()
    return valor


def texto_no_vacio(valor):
    if not texto(valor).strip():
        respuesta_invalida()
    return valor


def booleano(valor):
    if type(valor) is not bool:
        respuesta_invalida()
    return valor


def entero(valor):
    if type(valor) is not int:
        respuesta_invalida()
    return valor


def decimal_exacto(valor):
    if type(valor) not in (str, int, Decimal):
        respuesta_invalida()
    if isinstance(valor, str) and not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", valor):
        respuesta_invalida()
    try:
        numero = Decimal(valor)
    except (ValueError, InvalidOperation):
        respuesta_invalida()
    if not numero.is_finite():
        respuesta_invalida()
    return numero


def fecha_hora(valor, *, sin_fracciones=False):
    patron = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    if not sin_fracciones:
        patron += r"(?:\.[0-9]{1,6})?"
    patron += r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
    if not isinstance(valor, str) or not re.fullmatch(patron, valor):
        respuesta_invalida()
    if not valor.endswith("Z") and int(valor[-2:]) > 59:
        respuesta_invalida()
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        respuesta_invalida()


def fecha(valor):
    if not isinstance(valor, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", valor):
        respuesta_invalida()
    try:
        return date.fromisoformat(valor)
    except ValueError:
        respuesta_invalida()


def corte(valor):
    return fecha(valor) if isinstance(valor, str) and len(valor) == 10 else fecha_hora(valor)


def fecha_registrada(valor):
    # Las fechas registradas Oracle pueden conservar hora local sin zona.
    # No son fechaCorte ni se les puede asignar una zona supuesta.
    if isinstance(valor, str) and len(valor) == 10:
        return fecha(valor)
    if isinstance(valor, str) and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?", valor):
        try:
            return datetime.fromisoformat(valor)
        except ValueError:
            respuesta_invalida()
    return fecha_hora(valor)


def periodo(valor):
    if not isinstance(valor, str) or not re.fullmatch(r"[0-9]{6}", valor):
        respuesta_invalida()
    try:
        date(int(valor[:4]), int(valor[4:]), 1)
    except ValueError:
        respuesta_invalida()
    return valor


def lista(valor):
    if not isinstance(valor, list):
        respuesta_invalida()
    return valor


def estado(valor):
    try:
        return EC(valor)
    except (ValueError, TypeError):
        respuesta_invalida()


def adaptar_hecho_http(raw, parser, *, consulta):
    """Parser compartido; conserva las invariantes del envelope Hecho V2."""
    try:
        h = objeto(raw, HECHO_KEYS)
        disponible = booleano(h["disponible"])
        valor = None if h["valor"] is None else parser(h["valor"])
        certificacion = estado(h["estadoCertificacion"])
        if disponible != (valor is not None):
            respuesta_invalida()
        if certificacion in {EC.NO_CERTIFICADO, EC.NO_DISPONIBLE, EC.AMBIGUO} and disponible:
            respuesta_invalida()
        fuente = None if h["fuente"] is None else texto_no_vacio(h["fuente"])
        fecha_consulta = fecha_hora(h["fechaConsulta"])
        if fecha_consulta != consulta:
            respuesta_invalida()
        return Hecho(valor=valor, disponible=disponible, fuente=fuente,
            referencia_evidencia=texto_no_vacio(h["referenciaEvidencia"]),
            ruta_origen=texto_no_vacio(h["rutaOrigen"]),
            fecha_corte=None if h["fechaCorte"] is None else corte(h["fechaCorte"]),
            fecha_consulta=fecha_consulta, estado_certificacion=certificacion,
            observacion=texto(h["observacion"]))
    except ApiDataCrediHoyV2Error:
        raise
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        respuesta_invalida()


class AdaptadorHechosInternosV2:
    def adaptar(self, payload, *, request_id=None, fecha_evaluacion=None) -> HechosInternosCrediHoyV2:
        try:
            return self._adaptar(payload, request_id=request_id, fecha_evaluacion=fecha_evaluacion)
        except ApiDataCrediHoyV2Error:
            raise
        except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
            # Nunca propagar valores ni mensajes de validacion de la respuesta.
            respuesta_invalida()

    def _adaptar(self, payload, *, request_id, fecha_evaluacion):
        anteriores = {"versionContrato", "requestId", "fechaConsulta", "fechaEvaluacion",
                             "habilitadoDecisionProductiva", "datosAsociado", "carteraAlCierre",
                             "cuotaVigenteInterna", "aportesSociales203", "economiaInterna",
                             "obligacionesActuales", "recogida"}
        con_matriz = isinstance(payload, dict) and "matrizEconomicaSolicitud" in payload
        p = objeto(payload, anteriores | ({"matrizEconomicaSolicitud"} if con_matriz else set())
            | ({'hechosFinancieros'} if isinstance(payload,dict) and 'hechosFinancieros' in payload else set())
            | ({'pagaduria'} if isinstance(payload,dict) and 'pagaduria' in payload else set()))
        if p["versionContrato"] != VERSION_CONTRATO or p["habilitadoDecisionProductiva"] is not False:
            respuesta_invalida()
        rid = texto_no_vacio(p["requestId"])
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", rid) or (request_id is not None and rid != request_id):
            respuesta_invalida()
        consulta = fecha_hora(p["fechaConsulta"])
        evaluacion = fecha_hora(p["fechaEvaluacion"], sin_fracciones=True)
        if fecha_evaluacion is not None and evaluacion != fecha_evaluacion:
            respuesta_invalida()

        def hecho(raw, parser):
            return adaptar_hecho_http(raw, parser, consulta=consulta)

        asociado_map = {
            "fechaNacimiento": ("fecha_nacimiento", fecha), "estadoCivil": ("estado_civil", texto),
            "personasACargo": ("personas_a_cargo", entero), "fechaAfiliacion": ("fecha_afiliacion", fecha),
            "estrato": ("estrato", texto), "profesion": ("profesion", texto),
            "sueldoRegistrado": ("sueldo_registrado", decimal_exacto), "agencia": ("agencia", texto),
            "tipoVivienda": ("tipo_vivienda", texto),
        }
        asociado = objeto(p["datosAsociado"], asociado_map)
        valores = {destino: hecho(asociado[origen], parser)
                   for origen, (destino, parser) in asociado_map.items()}
        cierre = objeto(p["carteraAlCierre"], {"periodoAnterior", "estadoCierreOperativo", "estado", "obligaciones"})
        periodo_anterior = hecho(cierre["periodoAnterior"], periodo)
        cierre_operativo = estado(cierre["estadoCierreOperativo"])
        estado_cartera = texto(cierre["estado"])
        if estado_cartera not in {"DISPONIBLE", "PARCIAL", "SIN_REGISTROS_AL_CORTE"}:
            respuesta_invalida()
        obligacion_map = {
            "tipoDocumento": ("tipo_documento", texto), "numeroObligacion": ("referencia", texto),
            "clasificacionCierre": ("clasificacion", texto), "diasMoraAlCierre": ("dias_mora", entero),
            "diasVencidosAlCierre": ("dias_vencidos", entero),
            "saldoMoraRegistradoAlCierre": ("saldo_mora", decimal_exacto),
            "saldoCapitalAlCierre": ("saldo_capital", decimal_exacto),
        }
        obligaciones = []
        for fila in lista(cierre["obligaciones"]):
            fila = objeto(fila, obligacion_map)
            obligaciones.append(ObligacionCierreV2(**{destino: hecho(fila[origen], parser)
                for origen, (destino, parser) in obligacion_map.items()}))
        if ((estado_cartera == "DISPONIBLE" and not obligaciones)
                or (estado_cartera == "SIN_REGISTROS_AL_CORTE" and obligaciones)):
            respuesta_invalida()
        cartera = CarteraAlCierreV2(periodo_anterior=periodo_anterior,
            estado_cierre_operativo=cierre_operativo, estado=estado_cartera, obligaciones=tuple(obligaciones))
        cuota = hecho(p["cuotaVigenteInterna"], decimal_exacto)
        if cuota.disponible or cuota.estado_certificacion != EC.NO_CERTIFICADO:
            respuesta_invalida()
        aportes = objeto(p["aportesSociales203"], {"estado", "estadoConsolidacion", "documentos", "saldoAportesUtilizable"})
        estado_aportes = texto(aportes["estado"])
        if estado_aportes not in {"POSICIONES_DISPONIBLES", "PARCIAL", "AMBIGUO", "SIN_REGISTROS_203"}:
            respuesta_invalida()
        if aportes["estadoConsolidacion"] != "NO_CONSOLIDADO":
            respuesta_invalida()
        documento_map = {
            "numeroDocumento": ("referencia", texto), "tipoDocumento": ("tipo_documento", texto),
            "saldo": ("saldo_observado", decimal_exacto),
            "cancelacionRegistrada": ("cancelacion_registrada", texto),
            "fechaVigenciaRegistrada": ("fecha_vigencia_registrada", fecha_registrada),
        }
        documentos = []
        for fila in lista(aportes["documentos"]):
            fila = objeto(fila, documento_map)
            doc = DocumentoAporte203V2(**{destino: hecho(fila[origen], parser)
                for origen, (destino, parser) in documento_map.items()})
            if doc.tipo_documento.valor is not None and doc.tipo_documento.valor != "203":
                respuesta_invalida()
            documentos.append(doc)
        if ((estado_aportes == "SIN_REGISTROS_203" and documentos)
                or (estado_aportes != "SIN_REGISTROS_203" and not documentos)
                or (estado_aportes == "AMBIGUO" and len(documentos) < 2)
                or (estado_aportes in {"POSICIONES_DISPONIBLES", "PARCIAL"} and len(documentos) != 1)):
            respuesta_invalida()
        saldo_utilizable = hecho(aportes["saldoAportesUtilizable"], decimal_exacto)
        bloque_aportes = AportesSociales203V2(estado=estado_aportes, estado_consolidacion=aportes["estadoConsolidacion"],
            documentos=tuple(documentos), saldo_aportes_utilizable=saldo_utilizable)
        economia = objeto(p["economiaInterna"], {"ingresoOperacional", "deduccionesLinix"})
        bloque_actuales = self._obligaciones_actuales(p["obligacionesActuales"], hecho, consulta,
                                                     con_identidad=con_matriz)
        matriz = self._matriz_economica(p["matrizEconomicaSolicitud"], hecho, consulta) if con_matriz else None
        recogida = objeto(p["recogida"], {"obligacionSeleccionada", "cuotaCreditoRecogido"})
        financieros = None
        if 'hechosFinancieros' in p:
            from .financieros_http import adaptar_financieros
            financieros = adaptar_financieros(p['hechosFinancieros'], consulta=consulta)
        return HechosInternosCrediHoyV2(**valores, periodo_anterior=periodo_anterior, cartera_al_cierre=cartera,
            financieros=financieros,
            pagaduria=adaptar_pagaduria(p.get('pagaduria'),request_id=rid,fecha_consulta=p['fechaConsulta']),
            cuota_vigente_interna=cuota, aportes_documentos_203=bloque_aportes.documentos, aportes_sociales_203=bloque_aportes,
            saldo_aportes_utilizable=saldo_utilizable,
            ingreso_operacional=hecho(economia["ingresoOperacional"], decimal_exacto),
            deducciones_linix=hecho(economia["deduccionesLinix"], decimal_exacto),
            obligaciones_vigentes=Hecho(fuente=None, observacion="NO_PRESENTE_EN_CONTRATO_HTTP_V2"),
            cuotas_creditos_internos=Hecho(fuente=None, observacion="NO_PRESENTE_EN_CONTRATO_HTTP_V2"),
            obligaciones_actuales=bloque_actuales,
            matriz_economica_solicitud=matriz,
            obligacion_seleccionada=hecho(recogida["obligacionSeleccionada"], texto),
            cuota_credito_recogido=hecho(recogida["cuotaCreditoRecogido"], decimal_exacto),
            metadatos=MetadatosHechosInternosV2(version_contrato=VERSION_CONTRATO, request_id=rid,
                fecha_consulta=consulta, fecha_evaluacion=evaluacion, habilitado_decision_productiva=False))

    @staticmethod
    def _obligaciones_actuales(raw, hecho, consulta, *, con_identidad=False):
        anteriores = {"estadoCertificacion", "disponible", "obligaciones", "observacion"}
        nuevas = anteriores | {"estado", "temporalidad", "fechaCorte", "solicitudEvaluadaNoIdentificada"}
        if isinstance(raw, dict) and set(raw) == anteriores:
            p = objeto(raw, anteriores)
            if (estado(p["estadoCertificacion"]) != EC.NO_CERTIFICADO
                    or booleano(p["disponible"]) or lista(p["obligaciones"])):
                respuesta_invalida()
            return ObligacionesActualesV2(estado_certificacion=EC.NO_CERTIFICADO,
                disponible=False, obligaciones=(), observacion=texto(p["observacion"]))

        p = objeto(raw, nuevas)
        fecha_corte = fecha_hora(p["fechaCorte"])
        if fecha_corte != consulta:
            respuesta_invalida()
        plan_map = {
            "cuotaContractual": ("cuota_contractual", decimal_exacto),
            "numeroCuotas": ("numero_cuotas", entero),
            "periodicidad": ("periodicidad", texto), "fechaCuota": ("fecha_cuota", fecha_registrada),
        }
        candidata_map = {
            "seriePlan": ("serie_plan", texto), "reestructuracion": ("reestructuracion", texto),
            "tipoCuota": ("tipo_cuota", texto), **plan_map,
        }
        obligacion_map = {
            "tipoObligacion": ("tipo_obligacion", texto), "numeroObligacion": ("numero_obligacion", texto),
            "estadoSolicitud": ("estado_solicitud", texto), "aprobada": ("aprobada", texto),
            "liquidada": ("liquidada", texto), "indicadorCancelacion": ("indicador_cancelacion", texto),
            "indicadorAnulacion": ("indicador_anulacion", texto),
            "fechaPrestamo": ("fecha_prestamo", fecha_registrada),
            "fechaCancelacion": ("fecha_cancelacion", fecha_registrada),
            "saldoRegistrado": ("saldo_registrado", decimal_exacto), **plan_map,
        }

        def mapear(campos, raw):
            return {destino: hecho(raw[origen], parser) for origen, (destino, parser) in campos.items()}

        obligaciones = []
        for fila in lista(p["obligaciones"]):
            fila = objeto(fila, set(obligacion_map) | {
                "solicitudOrigen", "modalidad", "linea", "estadoPlan", "cuotasCandidatas"}
                | ({"esSolicitudEvaluada"} if con_identidad else set()))
            origen = objeto(fila["solicitudOrigen"], {"tipo", "numero"})
            catalogos = {}
            for nombre in ("modalidad", "linea"):
                catalogo = objeto(fila[nombre], {"codigo", "descripcion"})
                catalogos[nombre] = CatalogoInternoV2(codigo=hecho(catalogo["codigo"], texto),
                    descripcion=hecho(catalogo["descripcion"], texto))
            candidatas = tuple(CandidatoCuotaInternaCrediHoyV2(**mapear(candidata_map,
                objeto(c, candidata_map))) for c in lista(fila["cuotasCandidatas"]))
            obligaciones.append(ObligacionInternaCrediHoyV2(**mapear(obligacion_map, fila), **catalogos,
                solicitud_origen=SolicitudOrigenInternaV2(tipo=hecho(origen["tipo"], texto),
                                                         numero=hecho(origen["numero"], texto)),
                estado_plan=estado(fila["estadoPlan"]), cuotas_candidatas=candidatas,
                es_solicitud_evaluada=(None if not con_identidad or fila["esSolicitudEvaluada"] is None
                                      else booleano(fila["esSolicitudEvaluada"]))))
        return ObligacionesActualesV2(estado_certificacion=estado(p["estadoCertificacion"]),
            disponible=booleano(p["disponible"]), obligaciones=tuple(obligaciones),
            observacion=texto(p["observacion"]), estado=texto(p["estado"]),
            temporalidad=texto(p["temporalidad"]), fecha_corte=fecha_corte,
            solicitud_evaluada_no_identificada=booleano(p["solicitudEvaluadaNoIdentificada"]))

    @staticmethod
    def _matriz_economica(raw, hecho, consulta):
        totales = {
            "totalDevengadoMensual": "total_devengado_mensual",
            "totalDeducidoMensual": "total_deducido_mensual",
            "totalDevengadoExtraordinario": "total_devengado_extraordinario",
            "totalDeducidoExtraordinario": "total_deducido_extraordinario",
        }
        p = objeto(raw, set(totales) | {"disponible", "estadoCertificacion", "temporalidad", "fechaCorte",
            "solicitudEvaluadaNoIdentificada", "solicitud", "conceptos", "fechaModificacionMaxima", "observacion"})
        fecha_corte = fecha_hora(p["fechaCorte"])
        if fecha_corte != consulta:
            respuesta_invalida()

        def hecho_matriz(raw, parser):
            h = hecho(raw, parser)
            if h.fecha_corte != consulta:
                respuesta_invalida()
            return h

        solicitud = objeto(p["solicitud"], {"tipo", "numero"})
        concepto_map = {
            "codigo": ("codigo", texto), "descripcion": ("descripcion", texto),
            "naturaleza": ("naturaleza", texto), "automatico": ("automatico", texto),
            "porcentajeAplicacion": ("porcentaje_aplicacion", decimal_exacto),
            "devengadoMensual": ("devengado_mensual", decimal_exacto),
            "deducidoMensual": ("deducido_mensual", decimal_exacto),
            "devengadoExtraordinario": ("devengado_extraordinario", decimal_exacto),
            "deducidoExtraordinario": ("deducido_extraordinario", decimal_exacto),
            "fechaModificacion": ("fecha_modificacion", fecha_registrada),
        }
        conceptos = []
        for fila in lista(p["conceptos"]):
            fila = objeto(fila, concepto_map)
            conceptos.append(ConceptoEconomicoSolicitudV2(**{destino: hecho_matriz(fila[origen], parser)
                for origen, (destino, parser) in concepto_map.items()}))
        return MatrizEconomicaSolicitudV2(disponible=booleano(p["disponible"]),
            estado_certificacion=estado(p["estadoCertificacion"]), temporalidad=texto(p["temporalidad"]),
            fecha_corte=fecha_corte,
            solicitud_evaluada_no_identificada=booleano(p["solicitudEvaluadaNoIdentificada"]),
            solicitud=SolicitudOrigenInternaV2(**{k: hecho_matriz(solicitud[k], texto) for k in ("tipo", "numero")}),
            conceptos=tuple(conceptos), **{destino: hecho_matriz(p[origen], decimal_exacto)
                                          for origen, destino in totales.items()},
            fecha_modificacion_maxima=hecho_matriz(p["fechaModificacionMaxima"], fecha_registrada),
            observacion=texto(p["observacion"]))
