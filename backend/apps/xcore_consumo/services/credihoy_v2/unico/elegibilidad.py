"""Puerta de negocio previa a LINIX; no calcula riesgo ni emite decisiones."""










from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

CODIGOS = tuple(f'UNICO_FILA_{n}' for n in (2, 4, 6, 9, 10, 12, 14, 17, 21, 23, 24, 27, 28, 30, 31, 32))
PERFIL = ('390', '111', 'CFA')


@dataclass(frozen=True)
class ReglaPolitica:
    codigo: str
    obligatoria: bool
    aplicable: bool | None
    activa: bool
    excluyente: bool
    permite_no_aplica: bool = False
    permite_inactiva: bool = False
    referencia_exclusion: str | None = None


@dataclass(frozen=True)
class PoliticaElegibilidad:
    version: str
    referencia: str
    autorizada: bool
    desde: datetime
    hasta: datetime
    perfil: tuple[str, str, str]
    reglas: tuple[ReglaPolitica, ...]


@dataclass(frozen=True)
class ContextoElegibilidad:
    solicitud_id: str
    evaluacion_id: str | None
    perfil: tuple[str, str, str]
    reglas: tuple[dict, ...]
    capacidad_estado: str
    capacidad_cumple_criterio: bool | None
    capacidad_referencia: str | None
    decision_autorizada: bool
    decision_referencia: dict | None
    huella_decision: str | None
    consumidor: str | None
    xml_completo: bool | None
    xml_referencia: str | None
    condiciones_completas: bool | None
    consentimiento_vigente: bool | None
    consentimiento_referencia: str | None
    documentos_vigentes: bool | None
    documentos_referencia: str | None
    start_previos: int = 0
    intencion_existente: str | None = None
    flujo_existente: str | None = None
    tasa_concordante: bool | None = None
    oferta_referencia: str | None = None
    oferta_huella: str | None = None
    oferta_desde: datetime | None = None
    oferta_hasta: datetime | None = None
    cuota_contractual: str | None = None
    aceptacion_referencia: str | None = None
    aceptacion_oferta_huella: str | None = None
    aceptacion_fecha: datetime | None = None
    formulario_referencia: str | None = None
    titular_referencia: str | None = None
    clasificacion_estamentos: dict | None = None
    resultado_recogida: dict | None = None


class RadicacionOrdinariaBloqueada(ValueError):
    def __init__(self, resultado):
        self.resultado = resultado
        super().__init__('RADICACION_ORDINARIA_BLOQUEADA')


def _vigente(desde, hasta, ahora):
    return all(isinstance(v, datetime) and v.utcoffset() is not None for v in (desde, hasta, ahora)) and desde <= ahora < hasta


def evaluar_elegibilidad(c: ContextoElegibilidad, politica: PoliticaElegibilidad | None, *, autoridad=None, ahora):
    """Autoridad y politica son dependencias institucionales, nunca campos del request."""
    causas = []

    def falta(codigo, categoria, regla=None):
        causas.append({'codigo': codigo, 'categoria': categoria, 'regla': regla})

    if c.perfil != PERFIL:
        falta('PRODUCTO_MODALIDAD_INCOMPATIBLE', 'REVISION')
    if type(c.start_previos) is not int or c.start_previos < 0:
        falta('HISTORIAL_START_INVALIDO', 'REVISION')
    elif c.start_previos or c.intencion_existente or c.flujo_existente:
        falta('EXPEDIENTE_CON_INTENCION_O_START_PREVIO', 'REVISION')
    if not c.evaluacion_id:
        falta('EVALUACION_PENDIENTE', 'DATOS')
    valid_policy = (isinstance(politica, PoliticaElegibilidad) and politica.autorizada is True
        and bool(politica.version and politica.referencia) and politica.perfil == c.perfil
        and _vigente(politica.desde, politica.hasta, ahora))
    if valid_policy and not all(isinstance(r, ReglaPolitica) for r in politica.reglas):
        valid_policy = False
    if valid_policy:
        configs = {r.codigo: r for r in politica.reglas}
        valid_policy = (len(configs) == len(politica.reglas) == 16 and set(configs) == set(CODIGOS)
            and all(type(v) is bool for r in politica.reglas for v in
                (r.obligatoria, r.activa, r.excluyente, r.permite_no_aplica, r.permite_inactiva))
            and all(r.aplicable is None or type(r.aplicable) is bool for r in politica.reglas))
    if not valid_policy:
        falta('MATRIZ_POLITICA_VIGENTE_AUTORIZADA_AUSENTE', 'REVISION')
        configs = {}
    rows = {r.get('codigo'): r for r in c.reglas if isinstance(r, dict)}
    if len(rows) != len(c.reglas) or set(rows) != set(CODIGOS):
        falta('INVENTARIO_16_REGLAS_INCOMPLETO_O_DUPLICADO', 'DATOS')
    controles = []
    for codigo in CODIGOS:
        row = rows.get(codigo, {})
        estado = row.get('estadoEvaluacion', row.get('resultado', 'NO_EVALUABLE'))
        regla = configs.get(codigo)
        inactiva = 'REGLA_INACTIVA_NO_APLICADA' in row.get('motivos', [])
        if regla:
            exclusion = (isinstance(row.get('justificacionExclusion'), str)
                and bool(row['justificacionExclusion'].strip())
                and bool(row.get('referenciaExclusion')) and bool(regla.referencia_exclusion))
            if regla.aplicable is None:
                falta('APLICABILIDAD_NO_ACREDITADA', 'DATOS', codigo)
            elif not regla.activa or inactiva:
                if not (not regla.activa and regla.permite_inactiva and exclusion
                        and estado in ('INACTIVA', 'NO_APLICA')):
                    falta('REGLA_INACTIVA_SIN_TRATAMIENTO_AUTORIZADO', 'REVISION', codigo)
            elif estado == 'NO_APLICA' or regla.aplicable is False:
                if not (estado == 'NO_APLICA' and regla.permite_no_aplica and exclusion):
                    falta('NO_APLICA_NO_PERMITIDO_O_SIN_JUSTIFICACION', 'REVISION', codigo)
            elif estado == 'NO_CUMPLE' and regla.excluyente:
                falta('REGLA_EXCLUYENTE_NO_CUMPLE', 'EXCLUSION', codigo)
            elif estado == 'PENDIENTE_POLITICA' and regla.obligatoria:
                falta('REGLA_OBLIGATORIA_PENDIENTE_POLITICA', 'REVISION', codigo)
            elif estado not in ('CUMPLE', 'NO_CUMPLE') and regla.obligatoria:
                falta('REGLA_OBLIGATORIA_SIN_RESULTADO_CONCLUYENTE', 'DATOS', codigo)
        controles.append({'codigo': codigo, 'estado': 'INACTIVA' if inactiva else estado,
            'obligatoria': None if regla is None else regla.obligatoria,
            'excluyente': None if regla is None else regla.excluyente})
    if c.capacidad_estado != 'EVALUABLE' or c.capacidad_cumple_criterio is None:
        falta('CAPACIDAD_NO_EVALUABLE', 'DATOS')
    elif c.capacidad_cumple_criterio is not True or not c.capacidad_referencia:
        falta('CAPACIDAD_FUERA_DE_CRITERIO_O_SIN_POLITICA', 'REVISION')
    if c.xml_completo is not True or not c.xml_referencia or c.condiciones_completas is not True:
        falta('CONDICIONES_O_XML_INCOMPLETOS', 'DATOS')
    if c.tasa_concordante is not True:
        falta('TARIFA_Y_CONDICIONES_LINIX_NO_CONCORDANTES', 'REVISION')
    try:
        cuota = Decimal(c.cuota_contractual) if type(c.cuota_contractual) is str else None
        cuota_valida = cuota is not None and cuota.is_finite() and cuota > 0
    except (InvalidOperation, ValueError):
        cuota_valida = False
    oferta_valida = (isinstance(c.oferta_referencia, str) and bool(c.oferta_referencia.strip())
        and isinstance(c.oferta_huella, str) and re.fullmatch(r'[a-f0-9]{64}', c.oferta_huella) is not None
        and cuota_valida and _vigente(c.oferta_desde, c.oferta_hasta, ahora))
    if not oferta_valida:
        falta('OFERTA_CONTRACTUAL_AUSENTE_O_NO_VIGENTE', 'DATOS')
    if not (oferta_valida and isinstance(c.aceptacion_referencia, str) and c.aceptacion_referencia.strip()
            and c.aceptacion_oferta_huella == c.oferta_huella
            and isinstance(c.aceptacion_fecha, datetime) and c.aceptacion_fecha.utcoffset() is not None
            and c.oferta_desde <= c.aceptacion_fecha <= ahora):
        falta('ACEPTACION_NO_VINCULADA_A_OFERTA_VIGENTE', 'DATOS')
    for nombre, vigente, ref in (
        ('CONSENTIMIENTO', c.consentimiento_vigente, c.consentimiento_referencia),
        ('DOCUMENTOS', c.documentos_vigentes, c.documentos_referencia),
    ):
        if vigente is not True or not ref:
            falta(nombre + '_VIGENCIA_NO_ACREDITADA', 'DATOS')
    grant_ok = False
    if autoridad is not None and c.decision_referencia and valid_policy:
        try:
            grant = autoridad.resolve(c.decision_referencia)
            d = asdict(grant) if is_dataclass(grant) and not isinstance(grant, type) else {}
            grant_ok = (set(d) == {'client', 'fingerprint', 'decision_id', 'authorization_id',
                'policy_version', 'authorized', 'expires_at', 'synthetic'}
                and d['synthetic'] is False and d['authorized'] is True
                and bool(c.consumidor and c.huella_decision)
                and all(isinstance(c.decision_referencia.get(k), str) and c.decision_referencia[k].strip()
                        for k in ('decisionId', 'autorizacionId', 'versionPolitica'))
                and d['client'] == c.consumidor and d['fingerprint'] == c.huella_decision
                and d['decision_id'] == c.decision_referencia['decisionId']
                and d['authorization_id'] == c.decision_referencia['autorizacionId']
                and d['policy_version'] == politica.version == c.decision_referencia['versionPolitica']
                and isinstance(d['expires_at'], datetime) and d['expires_at'].utcoffset() is not None
                and d['expires_at'] > ahora)
        except Exception:
            grant_ok = False
    if c.decision_autorizada is not True or not grant_ok:
        falta('DECISION_GRANT_AUSENTE_INVALIDO_O_INCOMPATIBLE', 'REVISION')
    categorias = {r['categoria'] for r in causas}
    estado = ('NO_ELEGIBLE' if 'EXCLUSION' in categorias else 'REQUIERE_REVISION' if 'REVISION' in categorias
              else 'PENDIENTE_EVALUACION' if causas else 'ELEGIBLE_PARA_RADICACION')
    return {'version': 'credihoy-elegibilidad-v1', 'estado': estado,
        'elegible': not causas, 'modo': 'ORDINARIA', 'esAprobacion': False,
        'versionPolitica': politica.version if valid_policy else None,
        'causales': causas, 'reglas': controles}


def exigir_elegibilidad(contexto, politica, *, autoridad=None, ahora):
    r = evaluar_elegibilidad(contexto, politica, autoridad=autoridad, ahora=ahora)
    if not r['elegible']:
        raise RadicacionOrdinariaBloqueada(r)
    return r


def _preparacion_decision(resultado):
    # Reuse the same checks; later transport requirements are not credit rules.
    posteriores = {
        'HISTORIAL_START_INVALIDO', 'EXPEDIENTE_CON_INTENCION_O_START_PREVIO',
        'CONDICIONES_O_XML_INCOMPLETOS', 'TARIFA_Y_CONDICIONES_LINIX_NO_CONCORDANTES',
        'CONSENTIMIENTO_VIGENCIA_NO_ACREDITADA', 'DOCUMENTOS_VIGENCIA_NO_ACREDITADA',
        'DECISION_GRANT_AUSENTE_INVALIDO_O_INCOMPATIBLE',
        'OFERTA_CONTRACTUAL_AUSENTE_O_NO_VIGENTE', 'ACEPTACION_NO_VINCULADA_A_OFERTA_VIGENTE',
    }
    causas = [dict(c) for c in resultado['causales'] if c['codigo'] not in posteriores]
    categorias = {c['categoria'] for c in causas}
    codigos = {c['codigo'] for c in causas}
    if 'MATRIZ_POLITICA_VIGENTE_AUTORIZADA_AUSENTE' in codigos:
        estado = 'PENDIENTE_POLITICA'
    elif 'EXCLUSION' in categorias:
        estado = 'NO_ELEGIBLE'
    elif 'REGLA_OBLIGATORIA_PENDIENTE_POLITICA' in codigos:
        estado = 'PENDIENTE_POLITICA'
    elif 'DATOS' in categorias:
        estado = 'INFORMACION_INSUFICIENTE'
    elif causas:
        estado = 'REQUIERE_REVISION'
    else:
        estado = 'ELEGIBLE_PARA_REVISION'
    return {
        'version': 'credihoy-preparacion-decision-v1', 'estado': estado,
        'alcance': 'REGLAS_Y_CAPACIDAD', 'versionPolitica': resultado['versionPolitica'],
        'causales': causas,
        'verificacionesPosteriores': [dict(c) for c in resultado['causales'] if c['codigo'] in posteriores],
        'scoringEvaluado': False, 'ofertaContractualEvaluada': False,
        'emiteDecision': False, 'autorizaAprobacion': False, 'autorizaRadicacion': False,
    }


def _etapas_proceso(c, resultado, preparacion, *, previous, huella):
    """Projection only: local references and grants do not prove a core decision."""
    posteriores = {'OFERTA_CONTRACTUAL_AUSENTE_O_NO_VIGENTE',
        'ACEPTACION_NO_VINCULADA_A_OFERTA_VIGENTE',
        'DECISION_GRANT_AUSENTE_INVALIDO_O_INCOMPATIBLE'}
    formulario = (isinstance(c.formulario_referencia, str)
        and re.fullmatch(r'SOLICITUD_CONSUMO_FORM_SHA256:[a-f0-9]{64}', c.formulario_referencia) is not None)
    return {
        'versionContrato': 'credihoy-etapas-proceso-lectura-v1', 'soloLectura': True,
        'solicitudId': c.solicitud_id, 'evaluacionId': c.evaluacion_id,
        'huellaContexto': huella, 'versionPolitica': resultado['versionPolitica'],
        'ordenDocumentalInstitucionalConfirmado': False,
        'ordenEtapas': ['FORMULARIO_DURABLE', 'EVALUACION_PREVIA', 'AUTORIZACION_RADICACION_ESTUDIO',
            'RADICACION_SC_UNICA', 'DECISION_HUMANA_LINIX', 'FORMALIZACION_Y_FIRMA'],
        'formularioDurable': {'estado': 'PERSISTIDO' if formulario else 'NO_ACREDITADO_POR_ESTA_LECTURA',
            'fuente': 'POSTGRESQL_PROPIETARIO', 'referencia': c.formulario_referencia if formulario else None,
            'acreditaElegibilidad': False, 'acreditaRadicacionLinix': False},
        'evaluacionPreliminar': {'estado': preparacion['estado'],
            'alcance': preparacion['alcance'], 'esDecisionHumana': False},
        'requisitosRadicacionEstudio': {'estado': 'PENDIENTE_CONTRATO_INSTITUCIONAL',
            'aprobacionFinalPreviaExigida': False,
            'autorizacionEstudioAcreditada': False,
            'causalesPreparacionEstudio': [dict(v) for v in resultado['causales'] if v['codigo'] not in posteriores],
            'requisitosGateNoHomologadosParaEstudio': [dict(v) for v in resultado['causales'] if v['codigo'] in posteriores],
            'controlesConservados': ['POLITICA_Y_ELEGIBILIDAD', 'CONDICIONES_Y_XML', 'CONSENTIMIENTOS_APLICABLES',
                'DOCUMENTOS_POR_ETAPA', 'AUTORIZACION_ESTUDIO', 'JOURNAL_E_IDEMPOTENCIA', 'EFECTOS_TRANSACCIONALES'],
            'gateVigente': resultado['estado'], 'gateVigenteSatisfecho': resultado['elegible'],
            'causalesGateVigente': [dict(v) for v in resultado['causales']],
            'emiteAutorizacionEstudio': False},
        'radicacion': {'estado': 'CONCILIACION_REQUERIDA' if previous else 'SIN_ENVIO_REFERENCIADO',
            'alcance': 'REFERENCIAS_LOCALES', 'scConfirmada': None,
            'intencionReferenciada': c.intencion_existente, 'flujoReferenciado': c.flujo_existente,
            'accionPermitida': 'SOLO_CONCILIAR' if previous else 'COMPLETAR_REQUISITOS'},
        'workflowInstitucional': {'estado': 'REFERENCIA_PENDIENTE_CONCILIACION' if c.flujo_existente
            else 'SIN_EVIDENCIA_CONCILIADA', 'flujoReferenciado': c.flujo_existente,
            'observacionLocal': None, 'tareaActiva': None, 'estamentoAprobador': None},
        'decisionHumana': {'estado': 'NO_ACREDITADA_POR_ESTA_LECTURA',
            'fuenteRequerida': 'WORKFLOW_LINIX', 'decision': None,
            'actividad': None, 'estamento': None, 'evidencia': None},
        'formalizacion': {'estado': 'PENDIENTE_SECUENCIA_INSTITUCIONAL',
            'aceptacionOfertaAcreditada': None, 'firmaContratoAcreditada': None,
            'documentosPreviosConfirmados': False, 'documentosPosterioresConfirmados': False},
        'autorizaRadicacion': False, 'autorizaAprobacion': False,
        'autorizaFirma': False, 'ejecutaStart': False,
    }


def contrato_evaluacion_decision(c,politica,*,autoridad=None,ahora):
    """Read-only handoff contract. No decision/grant/intention is issued here."""
    result=evaluar_elegibilidad(c,politica,autoridad=autoridad,ahora=ahora)
    snapshot={'contexto':asdict(c),'politica':asdict(politica) if isinstance(politica,PoliticaElegibilidad) else None,
        'resultado':result}
    digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':'),
        default=lambda value:value.isoformat() if isinstance(value,datetime) else str(value)).encode()).hexdigest()
    previous=bool(c.start_previos or c.intencion_existente or c.flujo_existente)
    preparacion=_preparacion_decision(result)
    digital=evaluar_decision_digital(c,ahora=ahora)
    etapas=_etapas_proceso(c,result,preparacion,previous=previous,huella=digest)
    if digital['estado']!='POLITICA_NO_APLICABLE':
        etapas['ordenEtapas']=['FORMULARIO_DURABLE','EVALUACION_PREVIA','CLASIFICACION_ESTAMENTOS',
            'RADICACION_SC_UNICA','DECISION_DIGITAL_ORDINARIA_O_ESTAMENTO_ESPECIAL','FORMALIZACION_Y_FIRMA']
        etapas['politicaDecisionVigente']={'version':digital['versionPolitica'],
            'ordinaria':'AUTOMATICA','aprobadorHumanoOrdinarioRequerido':False,
            'excepciones':['COLABORADOR','FAMILIAR_COLABORADOR'],
            'decisionDigitalEstado':digital['estado'],'transporteHabilitado':False}
    return {'versionContrato':'credihoy-evaluacion-decision-radicacion-v1',
        'solicitudId':c.solicitud_id,'evaluacionId':c.evaluacion_id,'huellaContexto':digest,
        'versionPolitica':result['versionPolitica'],'resultadoElegibilidad':result,
        'preparacionDecision':{**preparacion,
            'evaluacionId':c.evaluacion_id,'huellaContexto':digest},
        'etapasProceso':etapas,
        'decisionDigital':digital,
        'decisionReferenciaVerificada':not any(v['codigo']=='DECISION_GRANT_AUSENTE_INVALIDO_O_INCOMPATIBLE'
            for v in result['causales']),
        'emiteDecision':False,'creaIntencion':False,'ejecutaStart':False,
        'accionPermitida':'SOLO_CONCILIAR' if previous else
            'PREPARAR_CON_TRANSPORTE_HABILITADO' if result['elegible'] else 'COMPLETAR_REQUISITOS',
        'idempotencia':{'solicitudId':c.solicitud_id,'intencionExistente':c.intencion_existente,
            'flujoExistente':c.flujo_existente,'requiereJournalDurable':True,
            'anteResultadoAmbiguo':'CONCILIAR_SIN_REPETIR_START'},
        'transporteOrdinarioHabilitado':False}


class PuertaRadicacionOrdinaria:
    """Sin ruta HTTP ni habilitacion productiva. Revalida antes de delegar."""
    def __init__(self, *, autoridad, politica, cargar_contexto, transporte):
        self.autoridad, self.politica = autoridad, politica
        self.cargar_contexto, self.transporte = cargar_contexto, transporte

    def preparar_contrato(self,solicitud_id,*,ahora):
        contexto=self.cargar_contexto(solicitud_id)
        if contexto.solicitud_id!=str(solicitud_id):
            raise RadicacionOrdinariaBloqueada({'codigo':'CONTEXTO_DE_OTRO_EXPEDIENTE'})
        return contrato_evaluacion_decision(contexto,self.politica,autoridad=self.autoridad,ahora=ahora)

    def preflight(self, solicitud_id, *, ahora, contrato_preparado=None):
        contexto = self.cargar_contexto(solicitud_id)
        if contexto.solicitud_id != str(solicitud_id):
            raise RadicacionOrdinariaBloqueada({'codigo': 'CONTEXTO_DE_OTRO_EXPEDIENTE'})
        digital=None
        if contexto.clasificacion_estamentos is not None or ahora.date()>=datetime(2026,10,26).date():
            digital=evaluar_decision_digital(contexto,ahora=ahora)
            if digital['estado']!='APROBADO':
                raise RadicacionOrdinariaBloqueada({'codigo':'PARADA_POLITICA_DIGITAL','decision':digital})
        if contrato_preparado is not None:
            current=contrato_evaluacion_decision(contexto,self.politica,autoridad=self.autoridad,ahora=ahora)
            if (type(contrato_preparado) is not dict or contrato_preparado!=current):
                raise RadicacionOrdinariaBloqueada({'codigo':'EVALUACION_DECISION_O_CONDICIONES_CAMBIARON'})
        if digital is not None:
            controls=digital['preflightRadicacionOrdinaria']
            if not controls['elegible']:
                raise RadicacionOrdinariaBloqueada(controls)
            return controls
        return exigir_elegibilidad(contexto, self.politica,
                                   autoridad=self.autoridad, ahora=ahora)

    def start(self, solicitud_id, *, ahora):
        self.preflight(solicitud_id, ahora=ahora)
        # Technical activation is a separate, intentionally closed boundary.
        raise RadicacionOrdinariaBloqueada({'codigo': 'TRANSPORTE_ORDINARIO_NO_HABILITADO'})

    def preparar_recorrido(self, solicitud_id, intencion_id, *, lectura_workflow, ahora):
        """Prepare either path with the same business checks, never send START."""
        from .estamentos import preparar_politica_ejecucion
        contexto = self.cargar_contexto(solicitud_id)
        if contexto.solicitud_id != str(solicitud_id):
            raise RadicacionOrdinariaBloqueada({'codigo':'CONTEXTO_DE_OTRO_EXPEDIENTE'})
        return preparar_politica_ejecucion(contexto, evaluar_decision_digital(contexto, ahora=ahora),
            intencion_id=intencion_id, lectura=lectura_workflow, ahora=ahora)


def evaluar_decision_digital(c, *, ahora):
    """Versioned consolidation of the SAME 16 rules, never a core transition.

    Raw rule records are retained; neither this projection nor a technical user
    is a committee decision or an authorization to use the production transport.
    """
    from copy import deepcopy
    from dataclasses import replace
    from datetime import timezone
    from .condiciones_producto import politica_digital
    from .estamentos import ORDINARIO, FAMILIAR, COLABORADOR, VERSION as EST_VERSION
    p = politica_digital()
    result = {'versionPolitica': p['version'], 'referencia': p['referencia'], 'responsable': p['responsable'], 'fechaDecisionPolitica': p['fechaDecision'], 'solicitudId': c.solicitud_id, 'estado': 'SUBSANACION_DIGITAL', 'reglasOriginales': deepcopy(c.reglas), 'reglasFuncionales': [], 'alertas': [], 'autorizaContinuacionInstitucional': False, 'decisionLinixRegistrada': False, 'requiereAprobadorManualOrdinario': False, 'ejecutaStart': False, 'autorizaDesembolso': False}
    if c.perfil != PERFIL or ahora.utcoffset() is None or ahora < datetime(2026, 10, 26, tzinfo=timezone.utc):
        return {**result, 'estado': 'POLITICA_NO_APLICABLE'}
    classification = c.clasificacion_estamentos
    try:
        when = datetime.fromisoformat(classification['fechaConsulta'])
        valid = classification['version'] == EST_VERSION and classification['solicitudId'] == c.solicitud_id and c.titular_referencia and (classification['titularReferencia'] == c.titular_referencia) and (when.utcoffset() is not None) and (0 <= (ahora - when).total_seconds() <= 300) and classification['consultaReferencia']
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        return {**result, 'estado': 'BLOQUEO_ESTAMENTOS_SIN_EVIDENCIA'}
    special = classification.get('estado') in {FAMILIAR, COLABORADOR}
    if not special and (classification.get('estado') != ORDINARIO or classification.get('autorizaAutomatizacion') is not True):
        return {**result, 'estado': 'BLOQUEO_ESTAMENTOS_SIN_EVIDENCIA'}
    rows = deepcopy(c.reglas)
    from .inventario import inventario_reglas_unico_v2
    inactive = {f'UNICO_FILA_{v.fila}' for v in inventario_reglas_unico_v2() if not v.activa}
    inactive_used = set()
    for row in rows:
        state = row.get('estadoEvaluacion', row.get('resultado', 'NO_EVALUABLE'))
        if row.get('codigo') in inactive and state == 'NO_APLICA' and (row.get('efecto') == 'INFORMATIVO') and (row.get('motivos') == ['REGLA_INACTIVA_NO_APLICADA']):
            row.update(justificacionExclusion='REGLA_INACTIVA_EN_MATRIZ_DOCUMENTAL', referenciaExclusion=p['referencia'])
            inactive_used.add(row['codigo'])
        if row.get('codigo') == 'UNICO_FILA_31' and state == 'NO_APLICA' and (row.get('efecto') == 'INFORMATIVO') and (row.get('motivos') == ['SOLO_RENOVACION_GER_PO_068_6_2_3_7']):
            row.update(justificacionExclusion='SOLICITUD_NUEVA_NO_RENOVACION', referenciaExclusion='GER-PO.068:6.2.3.7')
        if row.get('codigo') == 'UNICO_FILA_6' and state == 'NO_CUMPLE' and row.get('evidencia'):
            row.update(efecto='INFORMATIVO', excepcionAplicada=p['excepcionEmbargos'], versionExcepcion=p['version'])
            result['alertas'].append({'codigo': 'CUENTAS_EMBARGADAS', 'hechoOriginal': deepcopy(next((r for r in c.reglas if r['codigo'] == 'UNICO_FILA_6'))), 'excepcion': p['version'], 'noOmiteControlLegalCuentaDesembolso': True})
        elif row.get('codigo') == 'UNICO_FILA_6' and state == 'NO_CUMPLE':
            row.update(estadoEvaluacion='NO_EVALUABLE', motivos=['HECHO_EMBARGO_SIN_EVIDENCIA'])
        if row.get('codigo') == 'UNICO_FILA_9' and state == 'NO_CUMPLE':
            row.update(estadoEvaluacion='NO_EVALUABLE', motivos=['SUBSANACION_INFORMACION_DESACTUALIZADA'])
        if row.get('codigo') == 'UNICO_FILA_2' and c.capacidad_estado == 'EVALUABLE' and c.capacidad_referencia:
            if c.capacidad_cumple_criterio is True:
                row['estadoEvaluacion'] = 'CUMPLE'
            elif c.capacidad_cumple_criterio is False:
                row['estadoEvaluacion'] = 'NO_CUMPLE'
        if row.get('codigo') == 'UNICO_FILA_32' and c.resultado_recogida:
            collection = c.resultado_recogida
            if collection.get('solicitudId') == c.solicitud_id and collection.get('evidencia'):
                if collection.get('estado') == 'NO_APLICA':
                    row.update(estadoEvaluacion='NO_APLICA', justificacionExclusion='AUSENCIA_111_ACREDITADA', referenciaExclusion=collection['evidencia'])
                elif collection.get('estado') == 'CONDICION_RECOGIDA_ACREDITADA_NO_COMPENSADA':
                    row.update(estadoEvaluacion='CUMPLE', evidencia=deepcopy(collection['evidencia']))
                elif collection.get('estado') == 'REQUIERE_RECOGIDA_OBLIGATORIA':
                    row.update(estadoEvaluacion='NO_EVALUABLE', motivos=['SUBSANACION_RECOGIDA_OBLIGATORIA'])
    policy = PoliticaElegibilidad(p['version'], p['referencia'], True, datetime(2026, 10, 26, tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc), PERFIL, tuple((ReglaPolitica(code, code != 'UNICO_FILA_6', True, code not in inactive_used, code != 'UNICO_FILA_6', permite_no_aplica=True, permite_inactiva=code in inactive_used, referencia_exclusion=p['referencia']) for code in CODIGOS)))
    checked = evaluar_elegibilidad(replace(c, reglas=rows), policy, ahora=ahora)
    transport_causes = [v for v in checked['causales'] if v['codigo'] != 'DECISION_GRANT_AUSENTE_INVALIDO_O_INCOMPATIBLE']
    result['preflightRadicacionOrdinaria'] = {**checked, 'causales': transport_causes, 'elegible': not transport_causes, 'estado': 'ELEGIBLE_PARA_RADICACION' if not transport_causes else 'RADICACION_BLOQUEADA', 'decisionPoliticaVersion': p['version'], 'requiereGrantAprobadorHumano': False, 'transporteHabilitado': False}
    preparation = _preparacion_decision(checked)
    result.update(reglasFuncionales=rows, preparacion=preparation, causalElegibilidad=deepcopy(preparation['causales']))
    if preparation['estado'] == 'NO_ELEGIBLE':
        result['estado'] = 'RECHAZADO'
    elif preparation['estado'] == 'ELEGIBLE_PARA_REVISION':
        result['estado'] = 'APROBADO'
    else:
        result['estado'] = 'SUBSANACION_DIGITAL'
    if special:
        result['preflightRadicacionEspecial'] = {**result.pop('preflightRadicacionOrdinaria'), 'modo': 'ESPECIAL', 'requiereSeleccionHumana': True}
        result.update(estado=classification['estado'], estamento=deepcopy(classification), accionPermitida='PREPARAR_RADICACION_HASTA_SELECCION_HUMANA', decisionLinixRegistrada=False)
    if c.start_previos or c.intencion_existente or c.flujo_existente:
        result.update(accionPermitida='SOLO_CONCILIAR', autorizaContinuacionInstitucional=False)
    return result


def registrar_decision_digital(solicitud_id, *, operador, contexto, snapshot_clave, clave_idempotencia, ahora, autogestion=False, revision_referencia=None):
    """Append to the existing dossier journal, not to a historical rule snapshot.

    Internal owner-authorized operation; no new public route, START or financial
    transport. A policy APROBADO is not a registered LINIX approval.
    """
    from copy import deepcopy
    from uuid import UUID
    from django.db import transaction
    from apps.xcore_consumo.models import SolicitudConsumo, EvaluacionConsumo
    from apps.utils.models import AuditLog
    from .consentimiento_real import obtener
    key = str(UUID(str(clave_idempotencia)))
    with transaction.atomic():
        if autogestion:
            from .funcional import obtener_para_evaluacion, leer_estado_funcional
            case = obtener_para_evaluacion(solicitud_id, operador, autogestion=True, lock=True, revision_conservada=True)
        else:
            case = obtener(solicitud_id, operador, lock=True)
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=case)
        evaluation = EvaluacionConsumo.objects.select_for_update().get(solicitud=case)
        if autogestion:
            state = leer_estado_funcional(case)
            if not revision_referencia or state.get('revisionEstudio', {}).get('versionReferencia') != str(revision_referencia):
                raise ValueError('DECISION_REVISION_NO_VIGENTE')
            snapshot = state['resultado']
            if asdict(contexto) != asdict(contexto_desde_estudio(case, snapshot, ahora=ahora)):
                raise ValueError('DECISION_CONTEXTO_NO_CORRESPONDE_A_FUENTES')
        else:
            snapshot = evaluation.resultados.get(snapshot_clave)
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
        if contexto.solicitud_id != str(case.pk) or contexto.evaluacion_id != digest or type(snapshot) is not dict or (snapshot.get('solicitudId') != str(case.pk)) or (list(contexto.reglas) != snapshot.get('reglas')):
            raise ValueError('DECISION_DE_OTRA_EVALUACION_O_EXPEDIENTE')
        result = json.loads(json.dumps(evaluar_decision_digital(contexto, ahora=ahora), default=str))
        if autogestion:
            existing = list(AuditLog.objects.filter(solicitud=case, event='CREDIHOY_DECISION_DIGITAL_LOCAL', request_id=key)[:2])
            if existing:
                old = existing[0].payload
                if len(existing) != 1 or old.get('resultado') != result or old.get('evaluacionSha256') != digest or (old.get('revisionReferencia') != str(revision_referencia)) or (old.get('solicitudId') != str(case.pk)) or (old.get('sha256') != hashlib.sha256(json.dumps({k: v for k, v in old.items() if k != 'sha256'}, sort_keys=True, default=str).encode()).hexdigest()):
                    raise ValueError('IDEMPOTENCIA_DECISION_DIGITAL_CONFLICTO')
                return old
            body = {'solicitudId': str(case.pk), 'evaluacionSha256': digest, 'resultado': result, 'operacionId': key, 'registradoEn': ahora.isoformat(), 'actorTecnicoId': str(operador.pk), 'revisionReferencia': str(revision_referencia), 'contextoSha256': hashlib.sha256(json.dumps(asdict(contexto), sort_keys=True, default=str).encode()).hexdigest()}
            body['sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
            AuditLog.objects.create(solicitud=case, event='CREDIHOY_DECISION_DIGITAL_LOCAL', actor=str(operador.pk), request_id=key, payload=body)
            return body
        history = deepcopy(detail.orchestration_data.get('credihoy_decisiones_digitales_v1', {}))
        if key in history:
            if history[key]['resultado'] != result or history[key]['evaluacionSha256'] != digest:
                raise ValueError('IDEMPOTENCIA_DECISION_DIGITAL_CONFLICTO')
            return history[key]
        body = {'solicitudId': str(case.pk), 'evaluacionSha256': digest, 'resultado': result, 'operacionId': key, 'registradoEn': ahora.isoformat(), 'actorTecnicoId': str(operador.pk)}
        body['sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        history[key] = body
        detail.orchestration_data = {**detail.orchestration_data, 'credihoy_decisiones_digitales_v1': history}
        detail.save(update_fields=['orchestration_data', 'updated_at'])
        AuditLog.objects.create(solicitud=case, event='CREDIHOY_DECISION_DIGITAL_LOCAL', actor=str(operador.pk), request_id=key, payload={'estado': result['estado'], 'versionPolitica': result['versionPolitica'], 'sha256': body['sha256'], 'aprobacionLinixEjecutada': False})
        return body


def contexto_desde_estudio(s, resultado, *, ahora):
    """Bind the existing decision engine to current institutional observations."""
    from apps.xcore_consumo.models import ConsultaEstamentoOracle
    from .estamentos import clasificar_novedades
    from .reglas import definicion_funcional_reglas_unico_v2
    from .condiciones_producto import politica_digital
    if resultado.get('solicitudId') != str(s.pk):
        raise ValueError('DECISION_ESTUDIO_DE_OTRO_EXPEDIENTE')
    rows = resultado['reglas']
    capacity = resultado['capacidadMatematica']
    row = [v for v in rows if v.get('codigo') == 'UNICO_FILA_2']
    if len(row) != 1:
        raise ValueError('DECISION_INVENTARIO_CAPACIDAD_INVALIDO')
    definition = row[0].get('evidencia', {}).get('definicionFuncionalComunicada')
    criterion = None
    reference = None
    if definition == definicion_funcional_reglas_unico_v2() and row[0].get('estadoEvaluacion') in {'CUMPLE', 'NO_CUMPLE'}:
        criterion = row[0]['estadoEvaluacion'] == 'CUMPLE'
        reference = politica_digital()['referencia'] + ':' + definition['version']
    if 'capacidadDecisoria' in resultado:
        from .evaluacion_controlada import leer_capacidad_decisoria
        capacity = leer_capacidad_decisoria(resultado)
        criterion = capacity['cumpleCriterio']
        reference = politica_digital()['referencia'] + ':' + capacity['version'] + ':' + capacity['evidenciaSha256']
    holder = hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest()
    observed = ConsultaEstamentoOracle.objects.filter(solicitud=s, estado='OK', created_at__lte=ahora).order_by('-created_at', '-pk').first()
    classification = clasificar_novedades(observed.response_payload if observed else None, solicitud_id=s.pk, titular_referencia=holder, ahora=ahora, monto=s.monto_solicitado)
    collection = None
    row32 = [v for v in rows if v.get('codigo') == 'UNICO_FILA_32']
    if len(row32) == 1:
        evidence = row32[0].get('evidencia', {}).get('existeCredihoy111Registrado', {})
        if row32[0].get('estadoEvaluacion') == 'NO_APLICA' and row32[0].get('motivos') == ['AUSENCIA_CREDIHOY_111_CERTIFICADA_SIN_RECOGIDA'] and (evidence.get('disponible') is True) and (evidence.get('valor') is False) and (evidence.get('estado_certificacion') == 'CERTIFICADO') and all((evidence.get(k) for k in ('fuente', 'ruta_origen', 'referencia_evidencia', 'fecha_corte'))):
            collection = {'solicitudId': str(s.pk), 'estado': 'NO_APLICA', 'evidencia': evidence}
    return ContextoElegibilidad(solicitud_id=str(s.pk), evaluacion_id=hashlib.sha256(json.dumps(resultado, sort_keys=True, default=str).encode()).hexdigest(), perfil=PERFIL, reglas=tuple(rows), capacidad_estado=capacity['estado'], capacidad_cumple_criterio=criterion, capacidad_referencia=reference, decision_autorizada=False, decision_referencia=None, huella_decision=None, consumidor=None, xml_completo=None, xml_referencia=None, condiciones_completas=None, consentimiento_vigente=None, consentimiento_referencia=None, documentos_vigentes=None, documentos_referencia=None, titular_referencia=holder, clasificacion_estamentos=classification, resultado_recogida=collection)


def continuar_decision_estudio(solicitud_id, operador):
    from dataclasses import asdict
    from uuid import uuid5, NAMESPACE_URL
    from django.utils import timezone
    from .funcional import obtener_para_evaluacion, leer_estado_funcional
    from .condiciones_producto import politica_digital
    case = obtener_para_evaluacion(solicitud_id, operador, autogestion=True, revision_conservada=True)
    state = leer_estado_funcional(case)
    revision = state.get('revisionEstudio')
    if not revision:
        raise ValueError('DECISION_REQUIERE_REVISION_VERIFICADA')
    now = timezone.now()
    context = contexto_desde_estudio(case, state['resultado'], ahora=now)
    reference = hashlib.sha256(json.dumps({'revision': revision['versionReferencia'], 'contexto': asdict(context), 'politica': politica_digital()}, sort_keys=True, default=str).encode()).hexdigest()
    key = str(uuid5(NAMESPACE_URL, 'credihoy-decision-revision:' + str(case.pk) + ':' + reference))
    return registrar_decision_digital(case.pk, operador=operador, contexto=context, snapshot_clave=None, clave_idempotencia=key, ahora=now, autogestion=True, revision_referencia=revision['versionReferencia'])


def proyectar_decision_estudio(result):
    from .publico_institucional import project_study_decision
    return project_study_decision(result)


def leer_decision_estudio(s, *, ahora=None):
    """Read the latest policy decision, bound to the verified current revision."""
    from copy import deepcopy
    from uuid import UUID
    from django.utils import timezone
    from apps.utils.models import AuditLog
    from .funcional import leer_estado_funcional, oferta_custodia_activa
    if oferta_custodia_activa():
        return deepcopy(leer_estado_funcional(s)['decisionCustodiada'])
    now = ahora or timezone.now()
    record = AuditLog.objects.filter(solicitud=s, event='CREDIHOY_DECISION_DIGITAL_LOCAL').order_by('-created_at', '-pk').first()
    if record is None:
        return None
    try:
        state = leer_estado_funcional(s)
        revision = state.get('revisionEstudio', {}).get('versionReferencia')
        body = record.payload
        if not revision or body.get('revisionReferencia') != str(revision):
            return None
        key = str(UUID(body['operacionId']))
        date = datetime.fromisoformat(body['registradoEn'])
        if key != str(record.request_id) or body['solicitudId'] != str(s.pk) or (not body['actorTecnicoId']) or (date.utcoffset() is None) or (date > now) or (body['sha256'] != hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != 'sha256'}, sort_keys=True, default=str).encode()).hexdigest()) or (body['evaluacionSha256'] != hashlib.sha256(json.dumps(state['resultado'], sort_keys=True, default=str).encode()).hexdigest()):
            raise ValueError()
        context = contexto_desde_estudio(s, state['resultado'], ahora=date)
        expected = json.loads(json.dumps(evaluar_decision_digital(context, ahora=date), default=str))
        if body['contextoSha256'] != hashlib.sha256(json.dumps(asdict(context), sort_keys=True, default=str).encode()).hexdigest() or body['resultado'] != expected:
            raise ValueError()
        return deepcopy(body)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('DECISION_PERSISTIDA_NO_VERIFICABLE') from None
