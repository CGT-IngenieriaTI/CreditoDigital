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
    return {
        'versionContrato': 'credihoy-etapas-proceso-lectura-v1', 'soloLectura': True,
        'solicitudId': c.solicitud_id, 'evaluacionId': c.evaluacion_id,
        'huellaContexto': huella, 'versionPolitica': resultado['versionPolitica'],
        'ordenDocumentalInstitucionalConfirmado': False,
        'evaluacionPreliminar': {'estado': preparacion['estado'],
            'alcance': preparacion['alcance'], 'esDecisionHumana': False},
        'requisitosRadicacionEstudio': {'estado': 'PENDIENTE_CONTRATO_INSTITUCIONAL',
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
    return {'versionContrato':'credihoy-evaluacion-decision-radicacion-v1',
        'solicitudId':c.solicitud_id,'evaluacionId':c.evaluacion_id,'huellaContexto':digest,
        'versionPolitica':result['versionPolitica'],'resultadoElegibilidad':result,
        'preparacionDecision':{**preparacion,
            'evaluacionId':c.evaluacion_id,'huellaContexto':digest},
        'etapasProceso':_etapas_proceso(c,result,preparacion,previous=previous,huella=digest),
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
        if contrato_preparado is not None:
            current=contrato_evaluacion_decision(contexto,self.politica,autoridad=self.autoridad,ahora=ahora)
            if (type(contrato_preparado) is not dict or contrato_preparado!=current):
                raise RadicacionOrdinariaBloqueada({'codigo':'EVALUACION_DECISION_O_CONDICIONES_CAMBIARON'})
        return exigir_elegibilidad(contexto, self.politica,
                                   autoridad=self.autoridad, ahora=ahora)

    def start(self, solicitud_id, *, ahora):
        self.preflight(solicitud_id, ahora=ahora)
        # Technical activation is a separate, intentionally closed boundary.
        raise RadicacionOrdinariaBloqueada({'codigo': 'TRANSPORTE_ORDINARIO_NO_HABILITADO'})
