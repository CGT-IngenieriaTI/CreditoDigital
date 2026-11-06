"""Consumo puro de respuestas de CD2B: motor existente, sin HTTP ni persistencia."""





from dataclasses import replace
from datetime import datetime

from apps.integraciones.risk_gateway.contratos import HDCNormalizado
from apps.integraciones.risk_gateway.operaciones.servicio import ResultadoOperacion

from ..base import EstadoCertificacion as EC, Hecho
from ..gateway import adaptar_hdc_a_v2, materializar_cuotas_hdc_brutas
from .dto import EntradaCredihoyUnicoV2
from .presentacion import serializar_evaluacion
from .motor import evaluar_capacidad_unico_v2
from .reglas import HechosReglasUnicoV2, evaluar_reglas_unico_v2
from .normativa import incorporar_smmlv, evidencia_monetaria


def _respuesta(op, proveedor, dto, solicitud_operaciones, custodia_sha256=None):
    if type(op) is not ResultadoOperacion or op.proveedor != proveedor or op.operacion_id not in solicitud_operaciones or (solicitud_operaciones[op.operacion_id] != (proveedor, op.request_id)) or (op.estado not in {'COMPLETADA', 'SIN_INFORMACION'}) or (type(op.respuesta) is not dto):
        raise ValueError('RESPUESTA_NO_RECUPERABLE_O_PENDIENTE_CONCILIACION')
    if op.reutilizada:
        import hashlib, json, hmac
        expected = hashlib.sha256(json.dumps(op.respuesta.a_dict(), sort_keys=True, default=str).encode()).hexdigest()
        if type(custodia_sha256) is not str or not hmac.compare_digest(expected, custodia_sha256):
            raise ValueError('RESPUESTA_REUTILIZADA_SIN_CUSTODIA_VERIFICADA')
    r = op.respuesta
    esperado = 'COMPLETADO' if op.estado == 'COMPLETADA' else 'SIN_INFORMACION'
    if r['requestId'] != str(op.request_id) or r['resultado']['consulta_id'] != str(op.consulta_id) or r['versionContrato'] != op.version_contrato or (r['resultado']['estado_tecnico'] != esperado):
        raise ValueError('RESPUESTA_DE_OTRA_OPERACION')
    return r


def evaluar_respuestas_controladas(entrada, *, hdc, operaciones_esperadas, ingresos, hechos_reglas, fecha, request_id, cuotas_aplicables_explicitas=None, informe_xml=None, descuentos_nomina=None, cuota_estimada=None, titularidad_recogida=None, fecha_consulta_hdc=None, custodia_respuesta_sha256=None):
    """La pertenencia se suministra desde las reservas DB de la solicitud autorizada."""
    if type(entrada) is not EntradaCredihoyUnicoV2 or not isinstance(ingresos, Hecho) or type(hechos_reglas) is not HechosReglasUnicoV2 or (type(fecha) is not datetime) or (fecha.utcoffset() is None) or (type(operaciones_esperadas) is not dict) or (len(operaciones_esperadas) != 1):
        raise ValueError('CONTEXTO_CONTROLADO_INVALIDO')
    h = _respuesta(hdc, 'HDC', HDCNormalizado, operaciones_esperadas, custodia_respuesta_sha256)
    xml_validado = None
    if informe_xml is not None:
        from .embargos import informe_hdc_concordante, leer_embargos_persistidos
        try:
            xml_validado = informe_hdc_concordante(informe_xml, h)
            if fecha_consulta_hdc is not None:
                r = h['resultado']
                corte = datetime.fromisoformat(r['fecha_consulta'].replace('Z', '+00:00')).date()
                embargo = leer_embargos_persistidos(xml_validado, referencia=str(hdc.consulta_id), fecha_consulta=fecha_consulta_hdc, fecha_corte=corte, cobertura=(r['cobertura']['total_ahorros'], r['cobertura']['total_corrientes']))
                hechos_reglas = replace(hechos_reglas, cuentas_embargadas=embargo.cuentas_embargadas)
        except ValueError:
            raise
    externos = adaptar_hdc_a_v2(h)
    cuotas = materializar_cuotas_hdc_brutas(externos)
    conciliacion = None
    if cuotas_aplicables_explicitas is None and entrada.recogida is not None and (not entrada.recogida.aplica):
        from .conciliacion import conciliar_cuotas_hdc
        conciliacion = conciliar_cuotas_hdc(h, criterio_funcional=True, informe_xml=xml_validado)
        cuotas_aplicables_explicitas = conciliacion.cuotas_aplicables if cuotas.disponible else None
    capacidad = evaluar_capacidad_unico_v2(entrada, ingresos_mensuales=ingresos, cuotas_deudas_hdc_brutas=cuotas, cuotas_aplicables_explicitas=cuotas_aplicables_explicitas)
    consulta = Hecho(valor=True, disponible=True, fuente='RISK_GATEWAY_OPERACIONES_CONTROLADAS', referencia_evidencia=str(hdc.consulta_id), ruta_origen='resultado.estado_tecnico', estado_certificacion=EC.CERTIFICADO, observacion='Consulta realizada; no resultado de aprobacion')
    contexto = incorporar_smmlv(replace(hechos_reglas, consulta_central_realizada=consulta), fecha=fecha, modalidad=entrada.modalidad_codigo)
    preliminary = None
    escenario = None
    if conciliacion is not None:
        from .motor import calcular_preliminar_congente, normalizar_capacidad_disponible, escenario_capacidad_persistido
        preliminary = calcular_preliminar_congente(entrada, ingresos, conciliacion, descuentos_nomina=descuentos_nomina)
        if preliminary is not None and descuentos_nomina is not None:
            preliminary = normalizar_capacidad_disponible(preliminary, cuota_estimada=cuota_estimada)
            if conciliacion.cobertura_completa is True and conciliacion.partidas_resueltas is True and (preliminary['cuotaNuevaEstimada']['disponible'] is True):
                escenario = escenario_capacidad_persistido(preliminary)
    reglas = evaluar_reglas_unico_v2(entrada, hechos=contexto, capacidad=capacidad, fecha_evaluacion=fecha, capacidad_disponible=escenario, titularidad_recogida=titularidad_recogida)
    r = serializar_evaluacion(capacidad, reglas, request_id, fuente='CONSULTA_REAL_CONTROLADA', fecha=fecha, evidencia_reglas=evidencia_monetaria(entrada, contexto), hechos_reglas=contexto)
    r.update(estadoTramite='PENDIENTE_REVISION', revisionHumanaHabilitada=False, motivoRevision='DECISION_Y_VIA_DE_REVISION_REQUIEREN_AUTORIZACION_FUNCIONAL', ingresosAplicables={'disponible': capacidad.ingresos_mensuales.disponible, 'valor': None if capacidad.ingresos_mensuales.valor is None else str(capacidad.ingresos_mensuales.valor)}, cuotasHdcAplicables={'disponible': capacidad.cuotas_deudas_hdc_aplicables.disponible, 'valor': None if capacidad.cuotas_deudas_hdc_aplicables.valor is None else str(capacidad.cuotas_deudas_hdc_aplicables.valor)}, evidenciaConsultaControlada={'HDC': {'consultaId': str(hdc.consulta_id), 'estado': hdc.estado, 'fechaOriginal': externos.fecha_consulta_original, 'agregadoUtilizable': cuotas.disponible, 'motivoAgregado': cuotas.observacion}}, vigenciaParaDecision='NO_CERTIFICADA')
    if conciliacion is not None:
        r['conciliacionHdc'] = conciliacion.a_dict()
        if preliminary is not None:
            if descuentos_nomina is not None:
                from copy import deepcopy
                for name in ('versionSemanticaCapacidad', 'disponibleAntesGastosFamiliares', 'capacidadDisponible', 'gastosFamiliaresExactos', 'residualPosteriorEstimado', 'cuotaNuevaEstimada', 'antecedenteTrunc', 'semanticaCapacidadHistorica', 'formulasCapacidad', 'escenarioEstimadoNoContractual'):
                    r[name] = deepcopy(preliminary[name])
            r['capacidadPreliminar'] = preliminary
            r['politicaFinancieraCongente'] = preliminary['versionPolitica']
            row = next((v for v in r['reglas'] if v['codigo'] == 'UNICO_FILA_2'))
            row['evidencia'] = {'capacidadPreliminar': preliminary}
            if escenario is None and (not capacidad.capacidad_pago.disponible):
                row['motivos'] = ['CRITERIO_ENDEUDAMIENTO_PENDIENTE_VALIDACION_INSTITUCIONAL' if conciliacion.partidas_resueltas else 'CAPACIDAD_PRELIMINAR_NO_DECISORIA_DEUDA_INCOMPLETA']
    from .reglas import definicion_funcional_reglas_unico_v2
    for row in r['reglas']:
        if row['codigo'] in {'UNICO_FILA_2', 'UNICO_FILA_32'}:
            row.setdefault('evidencia', {})['definicionFuncionalComunicada'] = definicion_funcional_reglas_unico_v2()
    r['capacidadDecisoria'] = construir_capacidad_decisoria(r)
    next((row for row in r['reglas'] if row['codigo'] == 'UNICO_FILA_2'))['evidencia']['capacidadDecisoria'] = r['capacidadDecisoria']
    return r


def construir_capacidad_decisoria(resultado):
    import hashlib, json
    from .motor import escenario_capacidad_persistido
    from .reglas import evaluar_capacidad_disponible_regla_unico, definicion_funcional_reglas_unico_v2
    rows = [r for r in resultado['reglas'] if r.get('codigo') == 'UNICO_FILA_2']
    if len(rows) != 1:
        raise ValueError('CAPACIDAD_DECISORIA_REGLA_AMBIGUA')
    row = rows[0]
    preliminary = resultado.get('capacidadPreliminar')
    coverage = resultado.get('conciliacionHdc', {})
    scenario = None
    if coverage.get('cobertura_completa') is True and coverage.get('partidas_resueltas') is True and (type(preliminary) is dict) and (preliminary.get('cuotaNuevaEstimada', {}).get('disponible') is True):
        scenario = escenario_capacidad_persistido(preliminary)
    comparison, motive, refs = evaluar_capacidad_disponible_regla_unico(scenario)
    definition = definicion_funcional_reglas_unico_v2()
    if row.get('estadoEvaluacion') != comparison.value or row.get('resultado') != comparison.value or row.get('evidencia', {}).get('definicionFuncionalComunicada') != definition:
        raise ValueError('CAPACIDAD_DECISORIA_NO_CORRESPONDE_A_REGLA_2')
    evidence = {'preliminar': preliminary, 'conciliacion': coverage, 'definicion': definition}
    return {'version': VERSION_CAPACIDAD_DECISORIA, 'estado': 'EVALUABLE' if scenario else 'NO_EVALUABLE', 'cumpleCriterio': comparison.value == 'CUMPLE' if scenario else None, 'escenario': scenario.a_dict() if scenario else None, 'motivos': [motive], 'referencias': list(refs), 'evidenciaSha256': hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest(), 'cuotaContractual': False, 'autorizaAprobacionFinal': False}


def leer_capacidad_decisoria(resultado):
    expected = construir_capacidad_decisoria(resultado)
    row = next((r for r in resultado['reglas'] if r.get('codigo') == 'UNICO_FILA_2'))
    if resultado.get('capacidadDecisoria') != expected or row.get('evidencia', {}).get('capacidadDecisoria') != expected:
        raise ValueError('CAPACIDAD_DECISORIA_PERSISTIDA_NO_INTEGRA')
    return expected

VERSION_CAPACIDAD_DECISORIA = 'credihoy-capacidad-decisoria-v1'
