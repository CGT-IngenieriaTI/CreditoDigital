"""Dos proyecciones de lectura del expediente; no reevalua ni consulta proveedores."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.xcore_consumo.models import EvaluacionConsumo
from .elegibilidad import ContextoElegibilidad, PERFIL, contrato_evaluacion_decision
from .inventario import inventario_reglas_unico_v2
from .pendientes import detalle_regla, matriz_documental, FUENTES, estado_scoring_unico
from .motor import partidas_hdc_conciliadas
from .simulacion_lectura import leer_evidencia
from ..pagaduria import adaptar_pagaduria
from .condicion_comercial import resolver_condicion


VERSION = 'credihoy-estado-credito-v1'
MOTIVOS = {
    'MATRIZ_POLITICA_VIGENTE_AUTORIZADA_AUSENTE': 'Crédito debe ratificar la política vigente y sus efectos.',
    'EXPEDIENTE_CON_INTENCION_O_START_PREVIO': 'El expediente ya tiene un flujo LINIX; solo procede su conciliación.',
    'CAPACIDAD_NO_EVALUABLE': 'Faltan datos acreditados para determinar la capacidad de pago.',
    'DECISION_GRANT_AUSENTE_INVALIDO_O_INCOMPATIBLE': 'Falta una decisión autorizada y compatible para radicar.',
    'CONDICIONES_O_XML_INCOMPLETOS': 'Falta verificar el contrato para la radicación ordinaria.',
    'OFERTA_CONTRACTUAL_AUSENTE_O_NO_VIGENTE': 'Falta una oferta contractual vigente y acreditada.',
    'ACEPTACION_NO_VINCULADA_A_OFERTA_VIGENTE': 'No consta una aceptacion vinculada a la oferta vigente.',
    'CONSENTIMIENTO_VIGENCIA_NO_ACREDITADA': 'Debe verificarse la vigencia del consentimiento, sin repetir la firma.',
    'DOCUMENTOS_VIGENCIA_NO_ACREDITADA': 'Deben validarse los requisitos documentales vigentes.',
    'EVALUACION_PENDIENTE': 'La evaluación está pendiente.',
    'TARIFA_Y_CONDICIONES_LINIX_NO_CONCORDANTES': 'Falta ratificar la tarifa y su concordancia con cuota, seguro y calendario LINIX.',
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _simulacion(s):
    raw = s.consumo_detail.orchestration_data.get('credihoy_simulacion_resumen')
    if raw is None:
        raw = leer_evidencia()
    absent = {'disponible': False, 'valor': None, 'fuente': None, 'fecha': None}
    if not isinstance(raw, dict):
        return absent
    try:
        if (raw['solicitudId'] != str(s.pk) or raw['perfil'] != list(PERFIL)
                or Decimal(raw['monto']) != s.monto_solicitado or raw['plazoMeses'] != s.plazo_solicitado
                or raw['fuente'] != 'SIMULACION_OFICIAL_LINIX' or raw['unidad'] != 'COP'
                or not re.fullmatch(r'[0-9a-f]{64}', raw['evidenciaSha256'])
                or raw['incluyeSeguro'] is not True or not Decimal(raw['cuota']).is_finite()
                or Decimal(raw['cuota']) <= 0 or not parse_datetime(raw['fecha'])
                or parse_datetime(raw['fecha']).utcoffset() is None
                or parse_datetime(raw['fecha']) > timezone.now()):
            return absent
        result = {'disponible': True, 'valor': str(raw['cuota']), 'fuente': raw['fuente'], 'fecha': raw['fecha'],
            'ofertaContractual': False, 'desgloseSeguroDisponible': False,
            'cargosContractualesDisponibles': False, 'calendarioContractualDisponible': False}
        # A date-only source is never presented as an observed clock time.
        if raw.get('precisionFecha') == 'DIA':
            result['fecha'] = raw['fecha'][:10]
        if isinstance(raw.get('referenciaTecnica'), str) and len(raw['referenciaTecnica']) <= 120:
            result['referenciaTecnica'] = raw['referenciaTecnica']
            result['evidenciaSha256'] = raw['evidenciaSha256']
        tasa = raw.get('tasaObservada', {})
        if (isinstance(tasa, dict) and tasa.get('unidad') == 'PORCENTAJE_NOMINAL_ANUAL_MES_VENCIDO'
                and tasa.get('certificacionComercial') is False
                and re.fullmatch(r'[0-9]{1,2}(?:\.[0-9]{1,6})?', str(tasa.get('valor', '')))
                and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(raw.get('primeraCuota', '')))
                and parse_date(raw['primeraCuota']) is not None):
            result['tasaNamvObservada'] = str(tasa['valor'])
            result['primeraCuota'] = raw['primeraCuota']
        return result
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return absent


def construir_estado(s, paquete, *, politica=None, autoridad=None, ahora=None):
    ahora = ahora or timezone.now()
    r = paquete['resultado']
    linix = paquete.get('estadoLinix')
    if linix is not None and linix.get('solicitudId') != str(s.pk):
        raise ValueError('ESTADO_LINIX_DE_OTRO_EXPEDIENTE')
    evaluation = EvaluacionConsumo.objects.filter(solicitud=s).first()
    rows = tuple(r['reglas']) if r else ()
    simulacion = _simulacion(s)
    # GET never refreshes api_data or alters the historical selection snapshot.
    from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
    selection=SeleccionTarifaCredihoy.objects.filter(solicitud=s).order_by('-creado_at','-pk').first()
    p=None
    if selection and selection.evidencia and _hash(selection.evidencia)==selection.evidencia_sha256:
        evidence=selection.evidencia
        try:
            from .condicion_comercial import pagaduria_seleccion
            p=pagaduria_seleccion(selection)
        except (ValueError,TypeError,KeyError):pass
    elif selection is None:
        conserved=leer_evidencia('CREDIHOY_PAGADURIA_EVIDENCIA_PATH') or {}
        if (conserved.get('solicitudId')==str(s.pk) and conserved.get('perfil')==list(PERFIL)
                and conserved.get('identidadVerificada') is True
                and re.fullmatch(r'[a-f0-9]{64}',str(conserved.get('fuenteSha256','')))):
            try:
                evidence=conserved['pagaduria']
                p=adaptar_pagaduria(evidence,request_id=evidence['requestId'],fecha_consulta=evidence['fechaConsulta'])
            except (ValueError,TypeError,KeyError):pass
    tarifa=resolver_condicion(p,simulacion.get('tasaNamvObservada'))
    context = ContextoElegibilidad(solicitud_id=str(s.pk), evaluacion_id=_hash(r) if r else None,
        perfil=PERFIL, reglas=rows,
        capacidad_estado=r['capacidadMatematica']['estado'] if r else 'NO_EVALUABLE',
        capacidad_cumple_criterio=(None if not r or r['resultadoCapacidad']['estado'] == 'NO_EVALUABLE'
                                 else r['resultadoCapacidad']['estado'] == 'CUMPLE_CAPACIDAD'),
        capacidad_referencia=r['resultadoCapacidad']['referenciaPolitica'] if r else None,
        decision_autorizada=False, decision_referencia=None, huella_decision=None, consumidor=None,
        xml_completo=None, xml_referencia=None, condiciones_completas=None,
        consentimiento_vigente=None, consentimiento_referencia=paquete['consentimiento']['version'],
        documentos_vigentes=None, documentos_referencia=None,
        start_previos=linix['startEjecutados'] if linix else 0,
        intencion_existente=linix['intencionId'] if linix else None,
        flujo_existente=linix['kFlujo'] if linix else None,tasa_concordante=tarifa['contratoConcordante'])
    contrato = contrato_evaluacion_decision(context, politica, autoridad=autoridad, ahora=ahora)
    etapas = contrato['etapasProceso']
    institucional=bool(linix and linix.get('versionContrato')=='credihoy-linix-estado-v2')
    if institucional:
        etapas['radicacion'].update(estado='CONFIRMADA_EN_LINIX',flujoReferenciado=linix['kFlujo'],numeroSc=linix['numeroSc'])
        etapas['workflowInstitucional'].update(estado='ACTIVO_PREFIRMA',observacionLocal=None,
                                               lecturaInstitucional=linix)
        etapas['decisionHumana'].update(estado='APROBADA_EN_LINIX',aprobacion=linix['aprobacion'])
        etapas['formalizacion'].update(estado='PENDIENTE_FIRMA_INSTITUCIONAL')
    elif linix:
        # This legacy observation does not contain a verified active task or SC.
        etapas['workflowInstitucional']['observacionLocal'] = {
            'estado': linix['estado'], 'actualizadoAt': linix['actualizadoAt'],
            'actividadObservada': {k: linix['actividad'][k] for k in ('codigo', 'nombre')},
            'esTareaActivaAcreditada': False, 'esDecisionHumana': False,
        }
    eligibility = contrato['resultadoElegibilidad']
    pending = list(dict.fromkeys(MOTIVOS.get(c['codigo'], 'Se requiere una validación autorizada.')
                                 for c in eligibility['causales']))
    summary = {'producto': 'CrediHoy Único', 'codigoProducto': '390', 'modalidad': '111',
        'monto': str(s.monto_solicitado), 'plazoMeses': s.plazo_solicitado,
        'periodicidad': 'MENSUAL', 'cuotaSimulada': simulacion}
    titulo = {'ELEGIBLE_PARA_RADICACION': 'Elegible para radicar', 'PENDIENTE_EVALUACION': 'Evaluación pendiente',
        'REQUIERE_REVISION': 'En revisión', 'NO_ELEGIBLE': 'No elegible para radicar'}[eligibility['estado']]
    asociado = {'estado': eligibility['estado'], 'tituloEstado': titulo, 'resumen': summary,
        'capacidad': ('Por determinar' if not r or r['capacidadMatematica']['estado'] != 'EVALUABLE'
                      else 'Calculada, criterio pendiente' if context.capacidad_cumple_criterio is None else 'Evaluada'),
        'decision': 'Sin aprobación', 'score': None, 'pendientes': pending,
        'proximaAccion': 'Crédito revisará los datos y condiciones pendientes. No envíes otra solicitud.',
        'linix': 'INCIDENCIA_TECNICA' if linix else 'SIN_RADICACION_CONFIRMADA',
        'consentimientoFirmado': paquete['consentimiento']['estado'] == 'CONSENTIMIENTO_FIRMADO',
        'hdcCompletada': any(d['fuente'] == 'HDC' and d['estado'] == 'DISPONIBLE_EN_CONTEXTO_NO_APROBACION'
                            for d in paquete['estadoObtencionDatos'])}
    if any(row['codigo']=='UNICO_FILA_9' and row.get('estadoEvaluacion')=='NO_CUMPLE' for row in rows):
        from .normativa import MENSAJE_ACTUALIZACION
        asociado['proximaAccion'] = MENSAJE_ACTUALIZACION
        asociado['pendientes'] = [MENSAJE_ACTUALIZACION] + [p for p in pending if p != MENSAJE_ACTUALIZACION]
    if institucional:
        asociado['decision']='Aprobacion institucional registrada; formalizacion y firma pendientes.'
        asociado['linix']='APROBACION_INSTITUCIONAL_REGISTRADA'
        asociado['decisionInstitucional']={'aprobada':True,'fecha':linix['aprobacion']['fecha'],
                                          'firmaAcreditada':False,'liquidada':False,'alCorte':linix['actualizadoAt']}
    preliminary=r.get('capacidadPreliminar') if r else None
    usable_preliminary=bool(preliminary and preliminary['capacidad']['disponible'])
    reconciled = bool(usable_preliminary and partidas_hdc_conciliadas(preliminary))
    if usable_preliminary:
        asociado['capacidad']='Preliminar COP '+preliminary['capacidad']['valor']+'; no autoriza aprobacion'
        if reconciled:
            asociado['pendientes'].append('Obligaciones conciliadas; el criterio de capacidad sigue pendiente de validacion institucional.')
        else:
            asociado['pendientes'].append('Capacidad preliminar: hay obligaciones pendientes de conciliar; no autoriza aprobacion.')
    history = []
    if evaluation:
        for key, value in evaluation.resultados.items():
            if (key == 'credihoy_unico_funcional' or key.startswith('credihoy_unico_hdc_')) and isinstance(value, dict):
                date = value.get('fechaEvaluacion')
                parsed = parse_datetime(date) if isinstance(date, str) else None
                if value.get('solicitudId') == str(s.pk) and parsed and parsed.utcoffset() is not None:
                    history.append({'id': key, 'fecha': value['fechaEvaluacion'],
                        'capacidad': value.get('resultadoCapacidad', {}).get('estado', 'NO_EVALUABLE'),
                        'decision': value.get('decisionCrediticia', 'NO_AUTORIZADA'),
                        'activa': _hash(value) == _hash(r)})
    names = {f'UNICO_FILA_{v.fila}': v for v in inventario_reglas_unico_v2()}
    matriz = matriz_documental()
    reglas = []
    for c in eligibility['reglas']:
        row = next((v for v in rows if v['codigo'] == c['codigo']), {})
        rule = {**c, 'nombre': names[c['codigo']].nombre,
            'motivos': row.get('motivos', []), 'datosFaltantes': row.get('datosFaltantes', []),
            **({'evidencia': row['evidencia']} if isinstance(row.get('evidencia'), dict) else {}),
            'activaEnInventario': names[c['codigo']].activa}
        reglas.append(detalle_regla(rule, names[c['codigo']], matriz['version']))
    faltantes = []
    for row in reglas:
        if row['estado'] in ('NO_EVALUABLE', 'PENDIENTE_POLITICA', 'NO_APLICA', 'INACTIVA', 'NO_CUMPLE'):
            faltantes.append({'codigo': row['codigo'], 'nombre': row['nombre'], 'estado': row['estado'],
                'accion': row['accionPendiente'], 'fuentes': row['fuentesPendientes']})
    capacidad_pendientes = [{'campo': k, 'nombre': FUENTES[k][0], 'fuenteRequerida': FUENTES[k][1], 'accion': FUENTES[k][2]}
        for k in (r.get('informacionInsuficiente', []) if r else []) if k in FUENTES]
    return {'versionContrato': VERSION, 'solicitudId': str(s.pk), 'soloLectura': True,
        'asociado': asociado, 'operador': {'elegibilidad': {**eligibility, 'reglas': reglas},
            'preparacionDecision':contrato['preparacionDecision'],
            'etapasProceso':etapas,
            'preparacionFormalizacion':_preparacion_formalizacion(s, paquete, contrato, ahora=ahora),
            'scoring':estado_scoring_unico(),
            'matrizDocumental': matriz, 'queFaltaParaContinuar': faltantes,
            'concordanciaTasas': tarifa,
            'capacidad': {'estadoCalculo': ('PRELIMINAR_NO_DECISORIA' if reconciled else
                    'PRELIMINAR_NO_DECISORIA_DEUDA_INCOMPLETA') if usable_preliminary else context.capacidad_estado,
                'partidasHdcConciliadas': reconciled, 'autorizadoDecisionCrediticia': False,
                'valor': preliminary['capacidad']['valor'] if usable_preliminary else r['capacidadMatematica']['valor'] if r else None,
                'estadoCriterio': 'NO_EVALUABLE_PARA_DECISION' if usable_preliminary else r['resultadoCapacidad']['estado'] if r else 'NO_EVALUABLE',
                'referenciaPolitica': preliminary['versionPolitica'] if usable_preliminary else context.capacidad_referencia,
                'pendientes': capacidad_pendientes},
            'historial': sorted(history, key=lambda h: parse_datetime(h['fecha']).timestamp(), reverse=True),
            'linix': linix, 'evaluacion': {'capacidad': context.capacidad_estado, 'decision': 'NO_AUTORIZADA',
                'fecha': r['fechaEvaluacion'] if r else None},
            'modoLinix': 'PRUEBA_ADMINISTRATIVA' if linix else 'SIN_ENVIO',
            'transporteOrdinarioHabilitado': False, 'perspectivaAsociadoAsistida': True}}


def _preparacion_formalizacion(s, paquete, contrato, *, ahora):
    from apps.documentos.services import leer_evidencia_documental
    codes = {v['codigo'] for v in contrato['resultadoElegibilidad']['causales']}
    documents = leer_evidencia_documental(s.pk, ahora=ahora)
    requirements = [
        ('DECISION_AUTORIZADA', contrato['decisionReferenciaVerificada'], 'Credito'),
        ('OFERTA_CONTRACTUAL_VIGENTE', 'OFERTA_CONTRACTUAL_AUSENTE_O_NO_VIGENTE' not in codes, 'Credito / Finanzas'),
        ('ACEPTACION_VINCULADA_A_OFERTA', 'ACEPTACION_NO_VINCULADA_A_OFERTA_VIGENTE' not in codes, 'Titular'),
        ('DOCUMENTOS_CONTRACTUALES', 'DOCUMENTOS_VIGENCIA_NO_ACREDITADA' not in codes, 'Juridica / Credito'),
        ('FIRMA_CONTRATO', documents['firmaContratoAcreditada'], 'Juridica / proveedor de firma'),
    ]
    return {'versionContrato': 'credihoy-preparacion-formalizacion-v1', 'soloLectura': True,
        'estado': 'NO_HABILITADA', 'emiteOferta': False, 'autorizaFirma': False,
        'ordenInstitucionalConfirmado': False,
        'autorizaRadicacion': False, 'huellaContexto': contrato['huellaContexto'],
        'evaluacionId': contrato['evaluacionId'], 'versionPolitica': contrato['versionPolitica'],
        'accionRadicacion': contrato['accionPermitida'],
        'consentimientoCentrales': {'registrado': paquete['consentimiento']['estado'] == 'CONSENTIMIENTO_FIRMADO',
            'version': paquete['consentimiento']['version'], 'equivaleAceptacionOferta': False,
            'equivaleFirmaContrato': False, 'equivaleAutorizacionRadicacion': False},
        'documentos': documents, 'requisitos': [
            {'codigo': code, 'estado': 'ACREDITADO' if present else 'PENDIENTE', 'responsable': role,
             'etapaInstitucional': 'PENDIENTE_CONFIRMACION'}
            for code, present, role in requirements]}
