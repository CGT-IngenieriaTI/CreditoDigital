"""Existing owner contract: temporary progress, then explicit durable submission."""
from datetime import datetime
from decimal import Decimal
import hmac
import re
from uuid import UUID
from django.conf import settings
from django.http import Http404
from django.utils import timezone
from django.db import connections
from rest_framework import serializers
from apps.solicitudes.models import Solicitud, EstadoSolicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.utils.titular import _normalize_colombia_mobile, _validate_primer_apellido
from apps.utils.validators import validate_document_issue_date
from .publico_institucional import PRODUCTO
from . import publico_institucional as wire

ORIGEN = 'AUTOGESTION_9A'
MODO = 'AUTOGESTION_ENVIADA'
VINCULO_CONSUMO = 'credihoy_autogestion_vinculo_v1'


def validar_vinculo_consumo(s):
    """Read the original submission binding; never create or repair a dossier."""
    try:
        meta=s.metadatos
        binding=s.consumo_detail.orchestration_data[VINCULO_CONSUMO]
        if (s.producto!=PRODUCTO or s.asesor_id is not None
                or meta.get('modo')!=MODO or meta.get('origen')!=ORIGEN
                or any(meta.get(k)!=v for k,v in (
                    ('productoLinix','390'),('modalidadLinix','111'),('destinacionLinix','CFA')))
                or any(binding.get(k)!=v for k,v in (
                    ('version',1),('solicitudId',str(s.pk)),('origen',ORIGEN),('modo',MODO),
                    ('producto','390'),('modalidad','111'),('destinacion','CFA')))
                or str(UUID(meta['envio']['operacionId']))!=binding['operacionEnvioOriginal']):
            raise ValueError
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError('VINCULO_AUTOGESTION_NO_ACREDITADO') from None
    return s


def vincular_consumo_enviada(s, alias):
    """Same submitted dossier, no evaluation or credit consent inferred from 9A."""
    from apps.xcore_consumo.models import SolicitudConsumo, EstadoSolicitudConsumo
    if not connections[alias].in_atomic_block or s._state.db != alias:
        raise ValueError('VINCULO_CONSUMO_REQUIERE_TRANSACCION_ENVIO')
    meta = s.metadatos
    if (s.producto != PRODUCTO or meta.get('origen') != ORIGEN or meta.get('modo') != MODO
            or any(meta.get(k) != v for k, v in (
                ('productoLinix','390'),('modalidadLinix','111'),('destinacionLinix','CFA')))):
        raise ValueError('VINCULO_REQUIERE_ENVIO_AUTOGESTION_PROPIO')
    # The caller holds the holder lock; the one-to-one constraint also prevents duplicates.
    previous = SolicitudConsumo.objects.using(alias).filter(solicitud=s).first()
    if previous is not None:
        return previous
    if s.estado != EstadoSolicitud.INICIADA or s.paso_actual != 'enviada':
        raise ValueError('NO_CREAR_CONSUMO_EN_ANTECEDENTE_CERRADO')
    envio = meta['envio']
    values = envio['datos']
    amount = Decimal(str(s.monto_solicitado))
    if (str(UUID(envio['operacionId'])) != envio['operacionId']
            or not amount.is_finite() or amount <= 0
            or Decimal(str(values['monto'])) != amount
            or values['plazo'] != s.plazo_solicitado):
        raise ValueError('VINCULO_ENVIO_INCOMPATIBLE')
    detail, _ = SolicitudConsumo.objects.using(alias).get_or_create(solicitud=s, defaults={
        'estado':EstadoSolicitudConsumo.CONSENTIMIENTO_PENDIENTE,
        'form_data':{'monto':format(amount,'.2f'),'plazoMeses':s.plazo_solicitado},
        'orchestration_data':{VINCULO_CONSUMO:{'version':1,'solicitudId':str(s.pk),
            'origen':ORIGEN,'modo':MODO,'producto':'390','modalidad':'111','destinacion':'CFA',
            'operacionEnvioOriginal':envio['operacionId'],'creadoDesdeEnvioEn':s.created_at.isoformat(),
            'aceptacionReferencia':'metadatos.envio.aceptacionTemporal'}}})
    return detail


def habilitar(sesion=None, *, expira_at=None):
    if getattr(settings, 'CREDIHOY_AUTOSERVICIO_ENABLED', False) is not True:
        raise wire.NoDisponible
    if getattr(settings, 'CREDIHOY_PUBLICO_RESTRINGIDO', False) is True:
        try:
            scope = settings.CREDIHOY_AUTOSERVICIO_ALCANCE
            if type(scope) is dict and scope.get('modo')=='SESION_PERSONAL_VERIFICADA':
                from .publico_controles import admision_ensayo_vigente
                if set(scope)!={'modo','hasta'} or not admision_ensayo_vigente():raise ValueError
                if type(sesion) is not str or str(UUID(sesion))!=sesion:raise ValueError
                expiry=datetime.fromisoformat(expira_at) if type(expira_at) is str else expira_at
                until=datetime.fromisoformat(scope['hasta'])
                window=datetime.fromisoformat(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA)
                now=timezone.now()
                if (not isinstance(expiry,datetime) or not timezone.is_aware(expiry)
                        or not timezone.is_aware(until) or not timezone.is_aware(window)
                        or not now<expiry<=now+__import__('datetime').timedelta(minutes=15)
                        or not now<until<=window):raise ValueError
                return
            if (type(scope) is not dict or set(scope) != {'sesiones','hasta'}
                    or type(scope['sesiones']) is not list or len(scope['sesiones']) != 1
                    or type(sesion) is not str or str(UUID(sesion)) != sesion
                    or scope['sesiones'] != [sesion]):raise ValueError
            until = datetime.fromisoformat(scope['hasta'])
            window = datetime.fromisoformat(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA)
            now = timezone.now()
            if (not timezone.is_aware(until) or not timezone.is_aware(window)
                    or not now < until <= window):raise ValueError
        except (AttributeError,KeyError,TypeError,ValueError):
            raise wire.NoDisponible from None


def sesion_habilitada(sesion, *, expira_at=None):
    try:habilitar(sesion,expira_at=expira_at)
    except wire.NoDisponible:return False
    return True


def habilitar_lectura(identity):
    """A live signed read grant is independent of permission to capture data."""
    from .publico_seguimiento import document_hmac
    from . import progreso_privacidad as privacy
    try:
        if (type(identity) is not dict or identity.get('_scopeLectura')!='solicitudes:leer'
                or getattr(settings,'CREDIHOY_TITULAR_FUENTE_ENABLED',False) is not True):raise ValueError
        for field in ('_sesionReferencia','_requestId'):
            if str(UUID(identity[field]))!=identity[field]:raise ValueError
        expires=datetime.fromisoformat(identity['_sesionExpiraAt'])
        window=datetime.fromisoformat(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA)
        if (not timezone.is_aware(expires) or not timezone.is_aware(window)
                or timezone.now()>=min(expires,window)
                or not getattr(settings,'CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA','')
                or not re.fullmatch('[0-9]{6,15}',identity['numeroDocumento'])):raise ValueError
        cohort=settings.CREDIHOY_TITULAR_DOCUMENTOS_HMAC
        from .publico_controles import admision_ensayo_vigente
        if not admision_ensayo_vigente() and (not 1<=len(cohort)<=2 or not any(hmac.compare_digest(document_hmac(identity['numeroDocumento']),v) for v in cohort)):raise ValueError
    except (KeyError,AttributeError,TypeError,ValueError):raise wire.NoDisponible from None
    privacy.configuracion()


def leer_enviadas(identity, payload):
    """Owner-filtered durable submissions, independent of historical views/rules."""
    from django.db import transaction
    from .progreso_temporal import alias_operativo, proteger_conexion
    # Historical-only profiles may not have an operational submission alias.
    if not getattr(settings,'CREDIHOY_AUTOSERVICIO_DB_ALIAS',''):return []
    alias = alias_operativo(lectura=identity)
    with transaction.atomic(using=alias):
        with connections[alias].cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
        proteger_conexion(alias)
        query = Solicitud.objects.using(alias).filter(producto=PRODUCTO,
            metadatos__origen=ORIGEN,metadatos__modo=MODO,
            solicitante__tipo_identificacion='CC',
            solicitante__numero_identificacion=identity['numeroDocumento'])
        if 'solicitudId' in payload:query=query.filter(pk=UUID(payload['solicitudId']))
        rows=[]
        for s in query.order_by('-created_at','pk')[:20]:
            estudio=_estudio_publico_personal(s,identity)
            rows.append(wire.project_row({'referencia':str(s.pk),'titulo':'Tu solicitud esta en revision',
                'monto':str(s.monto_solicitado),'plazoMeses':s.plazo_solicitado,'periodicidad':'Mensual',
                'fechaCreacion':s.created_at.isoformat(),'requisitosCumplidos':sum(r['resultado']=='CUMPLE' for r in estudio['reglas']) if estudio else None,
                'estudio':estudio,'envio':envio_publico(s,alias),'simulacionLocal':estimacion_de_envio(s,identity),
                'pendientes':['Necesitamos completar algunas validaciones antes de tomar una decision.'],
                'siguientePaso':'Espera la revision de Congente.','ofertaDisponible':False,
                'acciones':{'radicar':False,'firmar':False},'institucional':None,'simulacion':None,
                'procedencia':{'origen':'FUENTE_OPERATIVA','estadoRegistrado':s.get_estado_display(),
                    'actualizadoAt':s.updated_at.isoformat(),'consultadoAt':timezone.now().isoformat()}}))
    return rows


def validar_promotor(value):
    if value is None:return None
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value):raise ValueError('PROMOTOR_INVALIDO')
    catalog = getattr(settings, 'CREDIHOY_PROMOTORES_CATALOGO', None)
    if type(catalog) is not dict or set(catalog) != {'referencia', 'observadoEn', 'vigenteHasta', 'promotores'}:
        raise wire.NoDisponible
    try:
        observed, until = datetime.fromisoformat(catalog['observadoEn']), datetime.fromisoformat(catalog['vigenteHasta'])
        if (not catalog['referencia'] or not timezone.is_aware(observed) or not timezone.is_aware(until)
                or not observed <= timezone.now() < until):raise ValueError
        row = catalog['promotores'][value]
        if type(row) is not dict or row.get('activo') is not True:raise ValueError
    except (KeyError, TypeError, ValueError):raise ValueError('PROMOTOR_INACTIVO_O_INEXISTENTE') from None
    return {'referencia':value,'catalogoReferencia':catalog['referencia'],'catalogoObservadoEn':catalog['observadoEn']}


def proyectar(s):
    state = s.metadatos['envio']
    return {'referencia':str(s.pk),'fase':'ENVIADA','version':state['version'],
        'datos':state['datos'],'promotor':state['promotor']['referencia'] if state['promotor'] else None,
        'actualizadoAt':s.updated_at.isoformat(),'venceAt':None,
        'acciones':{'radicar':False,'firmar':False},'solicitudRegistrada':True}


def enviada(identity, alias, key=None):
    query = Solicitud.objects.using(alias).filter(producto=PRODUCTO,metadatos__origen=ORIGEN,metadatos__modo=MODO)
    if key:
        candidate = Solicitud.objects.using(alias).filter(pk=key).select_related('solicitante').first()
        if candidate and (candidate.solicitante.numero_identificacion != identity['numeroDocumento']
                or candidate.metadatos.get('modo') != MODO or candidate.producto != PRODUCTO):raise Http404
        query = query.filter(pk=key)
    s = query.filter(solicitante__tipo_identificacion='CC',
        solicitante__numero_identificacion=identity['numeroDocumento']).order_by('-created_at').first()
    return s


def registrar_enviada(identity, alias, key, version, values, promoter, acceptance, operation, digest):
    """Only called inside the progress transaction after explicit, authenticated send."""
    if not connections[alias].in_atomic_block:
        raise ValueError('ENVIO_REQUIERE_TRANSACCION_PROGRESO')
    if not {'monto','plazo'} <= set(values):raise ValueError('SOLICITUD_INCOMPLETA')
    applicant = Solicitante.objects.using(alias).filter(tipo_identificacion='CC',
        numero_identificacion=identity['numeroDocumento']).first()
    if not applicant:
        surname = _validate_primer_apellido(identity.get('primerApellido',''))
        issued = serializers.DateField(validators=[validate_document_issue_date]).run_validation(identity.get('fechaExpedicion'))
        phone = _normalize_colombia_mobile(identity.get('contacto','') if identity.get('canal') == 'SMS' else identity.get('celular',''))
        applicant = Solicitante.objects.using(alias).create(tipo_identificacion='CC',
            numero_identificacion=identity['numeroDocumento'],primer_apellido=surname,fecha_expedicion=issued,
            celular=phone,email=values.get('email',identity.get('correo') or ''))
    s = Solicitud(id=key,solicitante=applicant,asesor=None,producto=PRODUCTO,estado=EstadoSolicitud.INICIADA,
        paso_actual='enviada',monto_solicitado=values['monto'],plazo_solicitado=values['plazo'],
        metadatos={'origen':ORIGEN,'modo':MODO,'productoLinix':'390','modalidadLinix':'111','destinacionLinix':'CFA',
            'envio':{'version':version,'datos':values,'promotor':promoter,'aceptacionTemporal':acceptance,
                'operacionId':str(operation),'peticionHmac':digest}})
    s.save(using=alias,force_insert=True)
    AuditLog.objects.using(alias).create(solicitud=s,event='credihoy_solicitud_enviada',actor='titular:9A',
        request_id=str(operation),payload={'origen':ORIGEN,'version':version,'solicitudRegistrada':True,
            'avisoVersion':acceptance['version'],'avisoSha256':acceptance['sha256']})
    vincular_consumo_enviada(s,alias)
    return s


def operar(identity, payload):
    from .progreso_temporal import operar as temporal
    return temporal(identity,payload)

FORMULARIO_EVALUACION = 'credihoy_formulario_evaluacion_v1'

COMPLEMENTO_PERSONAL = 'credihoy_complemento_personal_v1'


def validar_elecciones_complemento(values):
    if (type(values) is not dict or set(values)!={'periodicidadPago','recogerCredihoy','obligacionRecoger'}
            or values['periodicidadPago']!='MENSUAL' or type(values['recogerCredihoy']) is not bool):
        raise ValueError('ELECCION_FINANCIERA_PERSONAL_INCOMPLETA')
    reference=values['obligacionRecoger']
    if (values['recogerCredihoy'] and (type(reference) is not str or not re.fullmatch('[0-9]{1,20}',reference))
            or not values['recogerCredihoy'] and reference is not None):
        raise ValueError('REFERENCIA_RECOGIDA_PERSONAL_INVALIDA')
    if values['recogerCredihoy']:
        raise ValueError('REFERENCIA_RECOGIDA_INSTITUCIONAL_PENDIENTE')
    return dict(values)


def leer_complemento_personal(s):
    from apps.xcore_consumo.services.otp_crypto import decrypt_text,OTPCryptoError
    from hashlib import sha256
    import json
    journal=s.consumo_detail.orchestration_data.get(COMPLEMENTO_PERSONAL)
    if journal is None:return None
    try:
        if (type(journal) is not dict or set(journal)!={'actual','revisiones'}
                or type(journal['revisiones']) is not dict or not 1<=len(journal['revisiones'])<=20):raise ValueError
        entry=journal['revisiones'][journal['actual']]
        raw=decrypt_text(entry['contenidoCifrado']);data=json.loads(raw)
        if (set(entry)!={'contenidoCifrado','sha256'} or sha256(raw.encode()).hexdigest()!=entry['sha256']
                or type(data) is not dict or set(data)!={'version','solicitudId','operacionEnvioOriginal',
                    'operacionId','sesionReferencia','titularSha256','confirmadoEn','datos','peticionSha256'}
                or data['solicitudId']!=str(s.pk) or data['operacionId']!=journal['actual']
                or data['operacionEnvioOriginal']!=s.metadatos['envio']['operacionId']
                or data['titularSha256']!=sha256(s.solicitante.numero_identificacion.encode()).hexdigest()
                or type(data['version']) is not int or data['version']!=len(journal['revisiones'])
                or str(UUID(data['sesionReferencia']))!=data['sesionReferencia']
                or str(UUID(data['operacionId']))!=data['operacionId']
                or not re.fullmatch('[a-f0-9]{64}',data['peticionSha256'])):raise ValueError
        when=datetime.fromisoformat(data['confirmadoEn'])
        if not timezone.is_aware(when) or when>timezone.now():raise ValueError
        validar_elecciones_complemento(data['datos'])
        return data
    except (KeyError,TypeError,ValueError,AttributeError,OTPCryptoError):
        raise ValueError('COMPLEMENTO_PERSONAL_NO_INTEGRO') from None


def operar_complemento_personal(solicitud_id,identity,payload):
    from django.db import transaction
    from apps.xcore_consumo.models import SolicitudConsumo
    from apps.xcore_consumo.services.otp_crypto import encrypt_text
    from .consentimiento_real import obtener_autogestion
    from hashlib import sha256
    from copy import deepcopy
    reading=payload['accion']=='consultar_complemento'
    expected={'operacion','accion','solicitudId'}
    if not reading:expected|={'operacionId','version','datos','confirma'}
    if set(payload)!=expected:raise ValueError('CONTRATO_COMPLEMENTO_INVALIDO')
    case,alias=obtener_autogestion(solicitud_id,identity)
    with transaction.atomic(using=alias):
        if reading and connections[alias].vendor=='postgresql':
            with connections[alias].cursor() as cursor:cursor.execute('SET TRANSACTION READ ONLY')
        case,_=obtener_autogestion(solicitud_id,identity,lock=not reading)
        detail=SolicitudConsumo.objects.using(alias)
        if not reading:detail=detail.select_for_update()
        detail=detail.get(solicitud=case);case.consumo_detail=detail
        validar_vinculo_consumo(case)
        original=case.metadatos['envio']['datos']
        if reading and {'periodicidadPago','recogerCredihoy'}<=set(original):
            form=formulario_desde_envio(case)
            return {'version':1,'datos':{'periodicidadPago':form['periodicidadPago'],
                'recogerCredihoy':form['recogerCredihoy'],'obligacionRecoger':original.get('obligacionRecoger')},
                'confirmadoEn':case.metadatos['envio']['aceptacionTemporal']['aceptadoEn'],
                'fuente':'ELECCION_PERSONAL_ENVIO'}
        previous=leer_complemento_personal(case)
        if not reading:
            if (payload['confirma'] is not True or type(payload['version']) is not int or payload['version']<0
                    or type(payload['operacionId']) is not str
                    or str(UUID(payload['operacionId']))!=payload['operacionId']):
                raise ValueError('CONFIRMACION_COMPLEMENTO_REQUERIDA')
            values=validar_elecciones_complemento(payload['datos'])
            digest=sha256(wire.encode(payload)).hexdigest()
            data=deepcopy(detail.orchestration_data)
            journal=data.setdefault(COMPLEMENTO_PERSONAL,{'actual':None,'revisiones':{}})
            operation=payload['operacionId']
            if operation in journal['revisiones']:
                if (previous is None or journal['actual']!=operation
                        or not hmac.compare_digest(previous['peticionSha256'],digest)):
                    raise ValueError('OPERACION_COMPLEMENTO_INCOMPATIBLE')
            else:
                if payload['version']!=(previous['version'] if previous else 0):raise ValueError('VERSION_COMPLEMENTO_CAMBIO')
                if 'recogerCredihoy' in case.metadatos['envio']['datos']:
                    raise ValueError('ELECCIONES_DEL_ENVIO_ORIGINAL_NO_SOBRESCRIBIR')
                if len(journal['revisiones'])>=20:raise ValueError('LIMITE_REVISIONES_COMPLEMENTO')
                if detail.estado!='CONSENTIMIENTO_PENDIENTE' or any(k in data for k in
                        ('credihoy_unico_hdc_actual','credihoy_unico_funcional','credihoy_autorizacion_centrales_v1')):
                    raise ValueError('COMPLEMENTO_CERRADO_POR_ESTUDIO')
                previous={'version':payload['version']+1,'solicitudId':str(case.pk),
                    'operacionEnvioOriginal':case.metadatos['envio']['operacionId'],
                    'operacionId':operation,'sesionReferencia':identity['_sesionReferencia'],
                    'titularSha256':sha256(case.solicitante.numero_identificacion.encode()).hexdigest(),
                    'confirmadoEn':timezone.now().isoformat(),'datos':values,'peticionSha256':digest}
                raw=wire.encode(previous).decode()
                journal['revisiones'][operation]={'contenidoCifrado':encrypt_text(raw),'sha256':sha256(raw.encode()).hexdigest()}
                journal['actual']=operation
                detail.orchestration_data=data;detail.save(using=alias,update_fields=['orchestration_data','updated_at'])
                AuditLog.objects.using(alias).create(solicitud=case,event='credihoy_complemento_personal_guardado',
                    actor='titular:9A',request_id=operation,payload={'version':previous['version'],
                        'solicitudId':str(case.pk),'revisionSha256':journal['revisiones'][operation]['sha256']})
        return {'version':previous['version'] if previous else 0,'datos':previous['datos'] if previous else None,
            'confirmadoEn':previous['confirmadoEn'] if previous else None,
            'fuente':'ELECCION_PERSONAL_COMPLEMENTO' if previous else None}


def formulario_desde_envio(s):
    """Personal choices are not institutional certification of an obligation."""
    envio=s.metadatos['envio'];values=envio['datos']
    form={'monto':format(Decimal(str(s.monto_solicitado)),'.2f'),'plazoMeses':s.plazo_solicitado}
    if (Decimal(str(values['monto']))!=Decimal(str(s.monto_solicitado)) or values['plazo']!=s.plazo_solicitado):
        raise ValueError('CONDICIONES_DISTINTAS_DEL_ENVIO_PERSONAL')
    if not {'periodicidadPago','recogerCredihoy'} & set(values):return form
    if values.get('periodicidadPago')=='MENSUAL' and 'recogerCredihoy' not in values:
        # Monthly schedule is product data; absence of a collection choice is not False.
        return form
    if (values.get('periodicidadPago')!='MENSUAL' or type(values.get('recogerCredihoy')) is not bool):
        raise ValueError('ELECCION_FINANCIERA_PERSONAL_INCOMPLETA')
    reference=values.get('obligacionRecoger')
    if ((values['recogerCredihoy'] and (type(reference) is not str or not re.fullmatch(r'[0-9]{1,20}',reference)))
            or not values['recogerCredihoy'] and reference is not None):
        raise ValueError('REFERENCIA_RECOGIDA_PERSONAL_INVALIDA')
    acceptance=envio['aceptacionTemporal']
    if (str(UUID(acceptance['sesionReferencia']))!=acceptance['sesionReferencia']
            or str(UUID(envio['operacionId']))!=envio['operacionId']):
        raise ValueError('PROCEDENCIA_FORMULARIO_PERSONAL_INVALIDA')
    form.update(periodicidadPago=values['periodicidadPago'],recogerCredihoy=values['recogerCredihoy'])
    form[FORMULARIO_EVALUACION]={'version':1,'solicitudId':str(s.pk),
        'operacionEnvioOriginal':envio['operacionId'],'sesionReferencia':acceptance['sesionReferencia'],
        'fuente':'ELECCION_PERSONAL_ENVIO','aceptadoEn':acceptance['aceptadoEn'],
        'obligacionDeclarada':reference,'referenciaInstitucionalVerificada':False}
    return form


def validar_procedencia_formulario(s, raw):
    validar_vinculo_consumo(s)
    try:
        expected=formulario_desde_envio(s)
        complement=leer_complemento_personal(s)
        if complement:
            if FORMULARIO_EVALUACION in expected:raise ValueError('COMPLEMENTO_CONTRADICE_ENVIO_ORIGINAL')
            expected={**expected,
                'periodicidadPago':complement['datos']['periodicidadPago'],
                'recogerCredihoy':complement['datos']['recogerCredihoy'],
                FORMULARIO_EVALUACION:{'fuente':'ELECCION_PERSONAL_COMPLEMENTO','solicitudId':str(s.pk),
                    'version':complement['version'],'operacionId':complement['operacionId'],
                    'operacionEnvioOriginal':complement['operacionEnvioOriginal']}}
    except (KeyError,TypeError,ValueError,ArithmeticError):
        raise ValueError('PROCEDENCIA_FORMULARIO_PERSONAL_NO_ACREDITADA') from None
    if (FORMULARIO_EVALUACION not in expected or type(raw) is not dict
            or any(raw.get(k)!=expected[k] for k in ('monto','plazoMeses'))
            or any(k in raw and raw[k]!=v for k,v in expected.items())):
        raise ValueError('PROCEDENCIA_FORMULARIO_PERSONAL_NO_ACREDITADA')
    # A browser-entered number must not become a verified LINIX 111 reference.
    if expected['recogerCredihoy']:
        raise ValueError('REFERENCIA_RECOGIDA_INSTITUCIONAL_PENDIENTE')
    return expected

def _estudio_publico_personal(s, identity):
    import base64,hashlib,json
    from django.db import transaction
    from apps.xcore_consumo.models import EvaluacionConsumo,ConsentimientoConsumo,SolicitudConsumo
    from cryptography.exceptions import InvalidSignature
    def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,default=str).encode()
    def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
    alias=getattr(settings,'CREDIHOY_AUTORIZACION_DB_ALIAS','')
    if not alias:return None
    with transaction.atomic(using=alias):
        with connections[alias].cursor() as cursor:cursor.execute('SET TRANSACTION READ ONLY')
        with connections[alias].cursor() as cursor:
            cursor.execute('SELECT resumen_tecnico FROM public.credihoy_estudio_publico '
                'WHERE solicitud_id=%s ORDER BY registrado_en DESC,registro_id DESC LIMIT 1',[str(s.pk)])
            row=cursor.fetchone()
        if row is None:return None
        from . import centrales_personal as central
        try:
            envelope=row[0]
            if type(envelope) is str:envelope=json.loads(envelope)
            if type(envelope) is not dict or set(envelope)!={'algoritmo','payload','firmaBase64'} or envelope['algoritmo']!='Ed25519':raise ValueError
            body=envelope['payload']
            if type(body) is not dict or set(body)!={'version','proposito','solicitudId','titularSha256',
                    'evaluacionBaseSha256','consentimientoSha256','consumoSha256','estudio'}:raise ValueError
            central._clave_publica(owner=True).verify(base64.b64decode(envelope['firmaBase64'],validate=True),
                'credihoy-estudio-publico-tecnico-v1\x00'.encode()+canonical(body))
            if (body['version']!='credihoy-estudio-tecnico-firmado-v1'
                    or body['proposito']!='LECTURA_ESTUDIO_NO_DECISION' or body['solicitudId']!=str(s.pk)
                    or body['titularSha256']!=hashlib.sha256(identity['numeroDocumento'].encode()).hexdigest()):raise ValueError
            study=wire.project_study(body['estudio'],str(s.pk))
            evaluation=EvaluacionConsumo.objects.using(alias).values().get(pk=study['evaluacionBaseId'],solicitud_id=s.pk)
            consent=ConsentimientoConsumo.objects.using(alias).values().get(solicitud_id=s.pk)
            detail=SolicitudConsumo.objects.using(alias).values().get(solicitud_id=s.pk)
            if (digest(evaluation)!=body['evaluacionBaseSha256'] or digest(consent)!=body['consentimientoSha256']
                    or digest(detail)!=body['consumoSha256']):raise ValueError
            return study
        except (ValueError,TypeError,KeyError,AttributeError,InvalidSignature,
                EvaluacionConsumo.DoesNotExist,ConsentimientoConsumo.DoesNotExist,SolicitudConsumo.DoesNotExist):
            raise wire.NoDisponible from None


def envio_publico(s,alias):
    """A submitted label requires its native durable event, never just INICIADA."""
    operation=s.metadatos['envio']['operacionId']
    events=list(AuditLog.objects.using(alias).filter(solicitud_id=s.pk,
        event='credihoy_solicitud_enviada',request_id=operation).order_by('created_at')[:2])
    if len(events)!=1:raise wire.NoDisponible
    event=events[0]
    if event.payload.get('solicitudRegistrada') is not True:raise wire.NoDisponible
    return {'operacionId':operation,'confirmadoEn':event.created_at.isoformat(),
        'solicitud':wire.project_draft(proyectar(s))}


def estimacion_de_envio(s,identity):
    """Current read-only estimate of the immutable submitted amount and term."""
    from .simulacion_lectura import simulador_habilitado,operar_simulacion
    from django.core.exceptions import PermissionDenied
    import logging
    if not simulador_habilitado():return None
    try:
        return operar_simulacion(identity,{'solicitudId':str(s.pk),'operacion':'simular',
            'escenario':{'monto':format(s.monto_solicitado,'.2f'),'plazoMeses':s.plazo_solicitado,'linea':'VENTANILLA'}},
            identity['_requestId'])
    except (ValueError,KeyError,Http404,wire.NoDisponible,PermissionDenied) as exc:
        logging.getLogger('credito.publico').warning('estimacion_envio_no_disponible request=%s tipo=%s',
            identity.get('_requestId',''),type(exc).__name__)
        return None
