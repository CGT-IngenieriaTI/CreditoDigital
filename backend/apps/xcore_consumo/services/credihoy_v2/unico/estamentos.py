"""Institutional novelty evidence; never consults Oracle or chooses a decision."""




from datetime import datetime
from decimal import Decimal, localcontext
import re
import unicodedata
import json
from pathlib import Path

from ..base import Hecho, EstadoCertificacion as EC, instante

VERSION='CREDIHOY_ESTAMENTOS_ADENDA_20261026_V1'
ORDINARIO='APTO_PARA_AUTOMATIZACION_ORDINARIA'
FAMILIAR='REQUIERE_COMITE_INSTITUCIONAL_CREDITO'
COLABORADOR='REQUIERE_CONSEJO_ADMINISTRACION'
BLOQUEADO='AUTOMATIZACION_BLOQUEADA_EVIDENCIA_INSUFICIENTE'


def contrato_familiar():
    return json.loads(Path(__file__).with_name('contrato_familiar.json').read_text(encoding='utf-8'))


def exigir_consulta_familiar_segura():
    # The installed procedure commits and deletes a SHARED table without a WHERE.
    # A client rollback or a local lock cannot contain other institutional callers.
    raise ValueError('SP_CONSULTAFAMILIAR_EFECTOS_COMPARTIDOS_NO_CONTENIDOS')


def validar_lectura_original(evidencia, *, solicitud_id, documento, request_id):
    from hashlib import sha256
    try:
        if (type(evidencia) is not dict or evidencia['versionContrato']!='credihoy-estamentos-select-v1'
                or evidencia['fuente']!='API_DATA.SELECT_FUENTES_SP_CONSULTAFAMILIAR'
                or evidencia['fuenteSPSha256']!=contrato_familiar()['fuenteSha256']
                or evidencia['solicitudId']!=str(solicitud_id) or evidencia['requestId']!=request_id
                or evidencia['consultaReferencia']!=request_id
                or evidencia['titularReferencia']!=sha256(documento.encode()).hexdigest()
                or evidencia['soloLectura'] is not True or evidencia['autorizaAprobacion'] is not False
                or evidencia.get('lectorSelectExclusivo') is not True
                or evidencia['ejecutaStart'] is not False or evidencia['contratoAcreditado'] is not True
                or evidencia['mocked'] is not False or evidencia['completa'] is not True or evidencia['estado']!='OK'
                or set(evidencia['fuentesVerificadas'])!={'GR001MTERCERO','AP579DCARGXASO','NO223MEMPLEADO','GR1068MGRUPFAM'}
                or not re.fullmatch('[a-f0-9]{64}',evidencia['selectSha256'])):
            raise ValueError()
        return evidencia
    except (KeyError,TypeError,AttributeError,ValueError):raise ValueError('LECTURA_ORIGINAL_ESTAMENTOS_NO_ACREDITADA') from None


def actualizar_estamentos(solicitud_id, *, actor, cliente=None, autogestion=False, revision_conservada=False):
    """Owned dossier -> existing API -> persisted original evidence; no Oracle here."""
    from uuid import uuid4
    from django.db import transaction
    from django.utils import timezone
    from apps.xcore_consumo.models import ConsultaEstamentoOracle
    from .funcional import obtener_para_evaluacion, _firma
    from ..cliente import ApiDataCrediHoyV2Client, ConfiguracionApiDataV2
    rid = str(uuid4())
    with transaction.atomic():
        case = obtener_para_evaluacion(solicitud_id, actor, autogestion=autogestion, lock=True, revision_conservada=revision_conservada)
        if autogestion:
            _firma(case)
        attempt = ConsultaEstamentoOracle.objects.create(solicitud=case, estado='PENDIENTE', request_payload={'requestId': rid})
        document = case.solicitante.numero_identificacion
    try:
        client = cliente or ApiDataCrediHoyV2Client(ConfiguracionApiDataV2.desde_settings())
        evidence = client.consultar_estamentos(numero_documento=document, solicitud_id=str(case.pk), fecha_evaluacion=timezone.now().replace(microsecond=0), request_id=rid)
        validar_lectura_original(evidence, solicitud_id=case.pk, documento=document, request_id=rid)
        attempt.estado = 'OK'
        attempt.response_payload = evidence
        attempt.resultado = 'SELECT_ORIGINAL'
        attempt.mensaje = 'Lectura original institucional conservada; no constituye decision.'
        attempt.save(update_fields=['estado', 'response_payload', 'resultado', 'mensaje', 'updated_at'])
        return evidence
    except Exception as exc:
        code = getattr(exc, 'codigo', None)
        code = getattr(code, 'value', code)
        attempt.estado = 'ERROR'
        attempt.response_payload = {'requestId': rid, 'errorTipo': type(exc).__name__, 'errorCodigo': code if isinstance(code, str) and re.fullmatch('[A-Z][A-Z0-9_]{1,120}', code) else None, 'httpStatus': getattr(exc, 'status_http', None)}
        attempt.mensaje = 'Lectura no acreditada; no inferir ausencia de novedades.'
        attempt.save(update_fields=['estado', 'response_payload', 'mensaje', 'updated_at'])
        raise ValueError('LECTURA_ESTAMENTOS_NO_ACREDITADA_SIN_REINTENTO') from None


def preparar_ruta_estamentos(clasificacion, lectura):
    """Plan against real metadata, not permission to START or issue a concept."""
    state=clasificacion.get('estado') if type(clasificacion) is dict else None
    result={'paradaConfigurada':False,'paradaEnInstanciaComprobada':False,'autorizaStart':False,
        'ejecutaDecisionEstamento':False,'requiereRevalidacionAntesDeTransaccion':True,
        'exclusionWorkersNativosComprobada':False,
        'requisitosParadaNativa':['ENCADENAMIENTO_HASTA_121_SIN_EFECTOS_NO_AUTORIZADOS',
            'INSTANCIA_ACTIVA_121_CON_ASIGNACION_HUMANA', 'EXCLUSION_JOBS_Y_RECUPERADORES_EXTERNOS']}
    if state not in {FAMILIAR,COLABORADOR} or type(lectura) is not dict:return result
    if lectura.get('soloLectura') is not True:return result
    catalog={r['K_IDACTI']:r for r in lectura.get('catalogo',[])}
    selector=catalog.get(121,{})
    slots=lectura.get('rutas',[])
    selector_ok=(selector.get('I_EJECUC')=='H' and selector.get('I_CAPTUR')=='N'
        and any(r.get('K_IDRUTA')==10002852 and r.get('K_SERIE')==7423
            and r.get('K_IDACTI')==121 and r.get('I_EJECUC')=='H' for r in slots))
    target=10002991 if state==FAMILIAR else 10002856
    matches=[r for r in lectura.get('caminos',[]) if r.get('K_IDRUTA')==10002852
        and r.get('K_SERIE')==7424 and r.get('K_IDRUTA_CAM')==target and r.get('I_DEFAUL')=='N']
    if selector_ok and len(matches)==1:
        result.update(paradaConfigurada=True,actividadParada=121,rutaParada=10002852,
            slotParada=7423,respuestaSeleccion=matches[0]['N_CONDIC'],rutaSeleccionada=target,
            motivo='SELECTOR_HUMANO_NO_APORTAR_APROBAR_RECHAZAR',
            contratoFamiliarSeguro=False)
    return result


def preparar_politica_ejecucion(contexto, decision, *, intencion_id, lectura, ahora):
    """Bind the existing classification/decision to ONE intention, without START.

    Other institutional destinations may be consumed by the executor contract,
    but this producer never invents a classification the current reader lacks.
    """
    from datetime import timedelta
    from hashlib import sha256
    from uuid import UUID
    def fingerprint(value):
        return sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),default=str).encode()).hexdigest()
    classification = contexto.clasificacion_estamentos
    if (type(classification) is not dict or classification.get('version') != VERSION
            or classification.get('solicitudId') != contexto.solicitud_id
            or classification.get('titularReferencia') != contexto.titular_referencia
            or not contexto.evaluacion_id or not classification.get('consultaReferencia')
            or type(decision) is not dict or decision.get('solicitudId') != contexto.solicitud_id):
        raise ValueError('CONTEXTO_POLITICA_EJECUCION_NO_ACREDITADO')
    when = datetime.fromisoformat(classification['fechaConsulta'])
    if (when.utcoffset() is None or ahora.utcoffset() is None
            or not 0 <= (ahora-when).total_seconds() < 300):
        raise ValueError('CLASIFICACION_NO_VIGENTE')
    state = classification.get('estado')
    kind, destination, stop = 'INDETERMINADO', None, None
    controls = {}
    if (state == ORDINARIO and classification.get('autorizaAutomatizacion') is True
            and decision.get('estado') == 'APROBADO'):
        kind = 'ORDINARIO'
        controls = decision.get('preflightRadicacionOrdinaria', {})
    elif state in {FAMILIAR, COLABORADOR}:
        route = preparar_ruta_estamentos(classification, lectura)
        if route['paradaConfigurada']:
            kind = 'ESPECIAL'
            destination = {'ruta':route['rutaSeleccionada'], 'respuestaSeleccion':route['respuestaSeleccion']}
            stop = {'actividad':121, 'ruta':10002852, 'slot':7423}
            controls = decision.get('preflightRadicacionEspecial', {})
    result = {'versionContrato':'credihoy-ejecucion-dos-recorridos-v1',
        'solicitudId':str(UUID(contexto.solicitud_id)), 'intencionId':str(UUID(str(intencion_id))),
        'titularReferencia':contexto.titular_referencia, 'tipo':kind,
        'clasificacionEstado':state, 'clasificacionReferencia':classification['consultaReferencia'],
        'evaluacionReferencia':contexto.evaluacion_id, 'decisionSha256':fingerprint(decision),
        'configuracionSha256':fingerprint(lectura), 'preparadoEn':ahora.isoformat(),
        'clasificacionVigenteHasta':(when+timedelta(seconds=300)).isoformat(),
        'precondicionesRadicacionAcreditadas':controls.get('elegible') is True,
        'destinoHumano':destination, 'parada':stop,
        'autorizaStart':False, 'autorizaFirma':False, 'autorizaDesembolso':False}
    result['sha256'] = fingerprint(result)
    return result


def normalizar(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',value.lower())
        if not unicodedata.combining(c)).split())


def clasificar_novedades(evidencia, *, solicitud_id, titular_referencia, ahora, monto, smmlv=None, patrimonio=None):
    instante(ahora,'ahora')
    result={'version':VERSION,'solicitudId':str(solicitud_id),'titularReferencia':titular_referencia,
        'estado':BLOQUEADO,'autorizaAutomatizacion':False,'descripcionOriginal':[],
        'consultaReferencia':None,'fechaConsulta':None,'motivo':'CONTRATO_O_EVIDENCIA_NO_ACREDITADOS',
        'actividadSeleccionEstamento':121,'rutaSeleccion':10002852,'paradaInstitucionalGarantizada':False,
        'accionPermitida':'NO_INICIAR_CADENA_LINIX','contencionLocal':'TRANSPORTE_ORDINARIO_CERRADO'}
    try:
        if (type(evidencia) is not dict or evidencia.get('fuente') not in {'SP_CONSULTAFAMILIAR','API_DATA.SELECT_FUENTES_SP_CONSULTAFAMILIAR'}
                or evidencia.get('estado')!='OK' or evidencia.get('mocked') is not False
                or evidencia.get('completa') is not True or evidencia.get('contratoAcreditado') is not True
                or evidencia['solicitudId']!=str(solicitud_id) or evidencia['titularReferencia']!=titular_referencia
                or not re.fullmatch(r'[a-f0-9]{64}',titular_referencia)
                or not evidencia['consultaReferencia']):return result
        if evidencia.get('fuente')=='API_DATA.SELECT_FUENTES_SP_CONSULTAFAMILIAR':
            if (evidencia.get('versionContrato')!='credihoy-estamentos-select-v1'
                    or evidencia.get('fuenteSPSha256')!=contrato_familiar()['fuenteSha256']
                    or evidencia.get('lectorSelectExclusivo') is not True
                    or evidencia.get('soloLectura') is not True):return result
        when=datetime.fromisoformat(evidencia['fechaConsulta'])
        if when.utcoffset() is None or when>ahora or (ahora-when).total_seconds()>300:return result
        rows=evidencia['filas']
        if type(rows) is not list or not rows:return result
        flags=[]
        for row in rows:
            if type(row) is not dict or set(row)!={'resultado','tipofamiliar'}:return result
            if type(row['resultado']) is not str or type(row['tipofamiliar']) is not str:return result
            status=normalizar(row['resultado']); description=normalizar(row['tipofamiliar'])
            if status not in {'si','no'} or (status=='no' and description not in {'','ninguna'}) or (status=='si' and not description):return result
            if status=='no':flags.append(ORDINARIO);continue
            tokens=set(re.findall(r'[a-z]+',description))
            related=bool(tokens & {'grado','afinidad','consanguinidad','civil','conyuge','pariente'})
            employee=bool(tokens & {'trabajador','colaborador'})
            if not employee:return result
            flags.append(FAMILIAR if related else COLABORADOR)
        if ORDINARIO in flags and len(set(flags))>1:
            return {**result,'motivo':'NOVEDADES_CONTRADICTORIAS'}
        state=FAMILIAR if FAMILIAR in flags else COLABORADOR if COLABORADOR in flags else ORDINARIO
        result.update(estado=state,descripcionOriginal=[r['tipofamiliar'] for r in rows],
            consultaReferencia=evidencia['consultaReferencia'],fechaConsulta=when.isoformat(),motivo=None)
        if state==FAMILIAR:
            result.update(rutaDestinoCandidata=10002991,actividadDestinoCandidata=5997,
                motivo='COMITE_INSTITUCIONAL_NO_SUSTITUIBLE_POR_CGTE')
        elif state==COLABORADOR:
            valid=lambda h:isinstance(h,Hecho) and h.disponible and h.estado_certificacion==EC.CERTIFICADO and type(h.valor) is Decimal and h.valor.is_finite() and h.valor>0 and all((h.referencia_evidencia,h.fuente,h.ruta_origen,h.fecha_corte))
            verified=valid(smmlv) and valid(patrimonio) and smmlv.fecha_corte==patrimonio.fecha_corte
            from zoneinfo import ZoneInfo
            if verified:
                cutoff=smmlv.fecha_corte
                cutoff=cutoff.astimezone(ZoneInfo('America/Bogota')).date() if isinstance(cutoff,datetime) else cutoff
                verified=cutoff==ahora.astimezone(ZoneInfo('America/Bogota')).date()
            within=False
            if verified and type(monto) is Decimal and monto.is_finite():
                with localcontext() as ctx:
                    ctx.prec=50
                    within=smmlv.valor<=monto<=patrimonio.valor*Decimal('0.10')
            result.update(rutaDestinoCandidata=10002856,actividadDestinoCandidata=6094,
                rangoConsejoVerificado=bool(verified),dentroDeRangoConsejo=within,
                motivo='CONSEJO_REQUERIDO' if within else 'PATRIMONIO_O_RUTA_APLICABLE_PENDIENTES')
        else:
            result.update(autorizaAutomatizacion=True,accionPermitida='EVALUAR_POLITICA_ORDINARIA',
                rutaDestinoCandidata=None,actividadDestinoCandidata=None)
        return result
    except (KeyError,ValueError,TypeError,AttributeError):return result


def resultado_cursor_familiar(columnas, filas, *, columnas_acreditadas=None):
    """The existing caller supplies two positional outputs; preserve ALL rows.

    Actual catalog/output names still need corroboration. No config => no ordinary clearance.
    """
    contract=contrato_familiar()
    names=[str(c[0]) for c in columnas] if columnas else []
    if len(names)!=2 or not filas or any(len(r)!=2 or any(type(v) is not str for v in r) for r in filas):
        raise ValueError('RESPUESTA_FAMILIAR_INCOMPLETA')
    def oracle_type(column):
        raw=str(column[1]) if len(column)>1 else ''
        return next((t for t in ('DB_TYPE_VARCHAR','DB_TYPE_CHAR') if t in raw),None)
    types=[oracle_type(c) for c in columnas]
    positive=names==contract['columnasPositivas'] and types==contract['tiposPositivos']
    negative=(names==contract['columnasNegativas'] and types==contract['tiposNegativos']
        and len(filas)==1 and list(filas[0])==contract['salidaSinNovedades'])
    return {'fuente':'SP_CONSULTAFAMILIAR','mocked':False,'completa':True,'columnas':names,'tipos':types,
        'contratoVersion':contract['version'],'contratoFuenteSha256':contract['fuenteSha256'],
        'contratoAcreditado':positive or negative,
        'filas':[{'resultado':r[0],'tipofamiliar':r[1]} for r in filas]}
