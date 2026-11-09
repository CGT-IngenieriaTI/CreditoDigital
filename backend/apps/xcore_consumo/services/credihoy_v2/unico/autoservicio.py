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
    if getattr(settings,'CREDIHOY_PERFIL_DISPONIBILIDAD','')=='PERMANENT_PRIVATE_V1':
        from .publico_controles import exigir_sesion_permanente,AccesoDenegado
        try:
            exigir_sesion_permanente(sesion,expira_at)
            if getattr(settings,'CREDIHOY_AUTOSERVICIO_ALCANCE',None)!={'modo':'SESION_PERSONAL_VERIFICADA'}:raise ValueError
        except (AccesoDenegado,ValueError):raise wire.NoDisponible from None
    elif getattr(settings, 'CREDIHOY_PUBLICO_RESTRINGIDO', False) is True:
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


def sesion_habilitada(sesion):
    try:habilitar(sesion)
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
        permanent=getattr(settings,'CREDIHOY_PERFIL_DISPONIBILIDAD','')=='PERMANENT_PRIVATE_V1'
        if permanent:
            from .publico_controles import exigir_sesion_permanente,AccesoDenegado
            try:exigir_sesion_permanente(identity['_sesionReferencia'],expires)
            except AccesoDenegado:raise ValueError from None
        window=expires if permanent else datetime.fromisoformat(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA)
        if (not timezone.is_aware(expires) or not timezone.is_aware(window)
                or timezone.now()>=min(expires,window)
                or not getattr(settings,'CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA','')
                or not re.fullmatch('[0-9]{6,15}',identity['numeroDocumento'])):raise ValueError
        cohort=settings.CREDIHOY_TITULAR_DOCUMENTOS_HMAC
        from .publico_controles import admision_ensayo_vigente
        if permanent:
            if cohort!=[]:raise ValueError
        elif not admision_ensayo_vigente() and (not 1<=len(cohort)<=2 or not any(hmac.compare_digest(document_hmac(identity['numeroDocumento']),v) for v in cohort)):raise ValueError
    except (KeyError,AttributeError,TypeError,ValueError):raise wire.NoDisponible from None
    privacy.configuracion()


def leer_enviadas(identity, payload):
    """Owner-filtered durable submissions, independent of historical views/rules."""
    from django.db import transaction
    from .progreso_temporal import alias_operativo, proteger_conexion
    if not getattr(settings, 'CREDIHOY_AUTOSERVICIO_DB_ALIAS', ''):
        return []
    alias = alias_operativo(lectura=identity)
    with transaction.atomic(using=alias):
        with connections[alias].cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
        proteger_conexion(alias)
        query = Solicitud.objects.using(alias).filter(producto=PRODUCTO, metadatos__origen=ORIGEN, metadatos__modo=MODO, solicitante__tipo_identificacion='CC', solicitante__numero_identificacion=identity['numeroDocumento'])
        if 'solicitudId' in payload:
            query = query.filter(pk=UUID(payload['solicitudId']))
        rows = []
        for s in query.order_by('-created_at', 'pk')[:20]:
            estudio = _estudio_publico_personal(s, identity)
            from apps.documentos.formularios_credihoy import presentar_formalizacion_autentic
            formalizacion = presentar_formalizacion_autentic(s, alias=alias)
            rows.append(wire.project_row({'referencia': str(s.pk), 'titulo': 'Tu solicitud esta en revision', 'monto': str(s.monto_solicitado), 'plazoMeses': s.plazo_solicitado, 'periodicidad': 'Mensual', 'fechaCreacion': s.created_at.isoformat(), 'requisitosCumplidos': sum((r['resultado'] == 'CUMPLE' for r in estudio['reglas'])) if estudio else None, 'estudio': estudio, 'envio': envio_publico(s, alias), 'simulacionLocal': estimacion_de_envio(s, identity), 'formalizacionDocumental': formalizacion, 'pendientes': ['Necesitamos completar algunas validaciones antes de tomar una decision.'], 'siguientePaso': 'Espera la revision de Congente.', 'ofertaDisponible': False, 'acciones': {'radicar': False, 'firmar': False}, 'institucional': None, 'simulacion': None, 'procedencia': {'origen': 'FUENTE_OPERATIVA', 'estadoRegistrado': s.get_estado_display(), 'actualizadoAt': s.updated_at.isoformat(), 'consultadoAt': timezone.now().isoformat()}}))
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


def formulario_desde_envio(s):
    """Personal choices are not institutional certification of an obligation."""
    envio=s.metadatos['envio'];values=envio['datos']
    form={'monto':format(Decimal(str(s.monto_solicitado)),'.2f'),'plazoMeses':s.plazo_solicitado}
    if (Decimal(str(values['monto']))!=Decimal(str(s.monto_solicitado)) or values['plazo']!=s.plazo_solicitado):
        raise ValueError('CONDICIONES_DISTINTAS_DEL_ENVIO_PERSONAL')
    if not {'periodicidadPago','recogerCredihoy'} & set(values):return form
    if not {'periodicidadPago','recogerCredihoy'} <= set(values):
        # Older submissions carried only part of the later personal choices.
        complement=leer_complemento_personal(s)
        if complement is None:raise ValueError('ELECCION_FINANCIERA_PERSONAL_INCOMPLETA')
        choices=complement['datos']
        if any(key in values and (type(values[key]) is not type(choices.get(key))
                or values[key]!=choices.get(key))
                for key in ('periodicidadPago','recogerCredihoy','obligacionRecoger')):
            raise ValueError('COMPLEMENTO_CONTRADICE_ENVIO_ORIGINAL')
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

FORMULARIO_EVALUACION = 'credihoy_formulario_evaluacion_v1'

COMPLEMENTO_PERSONAL = 'credihoy_complemento_personal_v1'


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
            cursor.execute('SELECT resumen_tecnico,registrado_en FROM public.credihoy_estudio_publico '
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
            if (body['version'] not in ('credihoy-estudio-tecnico-firmado-v1','credihoy-estudio-tecnico-firmado-v2')
                    or body['proposito']!='LECTURA_ESTUDIO_NO_DECISION' or body['solicitudId']!=str(s.pk)
                    or body['titularSha256']!=hashlib.sha256(identity['numeroDocumento'].encode()).hexdigest()):raise ValueError
            study=wire.project_study(body['estudio'],str(s.pk))
            evaluation=EvaluacionConsumo.objects.using(alias).values().get(pk=study['evaluacionBaseId'],solicitud_id=s.pk)
            consent=ConsentimientoConsumo.objects.using(alias).values().get(solicitud_id=s.pk)
            detail=SolicitudConsumo.objects.using(alias).values().get(solicitud_id=s.pk)
            from .funcional import huella_consumo_estudio
            if (digest(evaluation)!=body['evaluacionBaseSha256'] or digest(consent)!=body['consentimientoSha256']
                    or (huella_consumo_estudio(detail,version=body['version'])!=body['consumoSha256']
                        and not verificar_custodia_contractual_posterior(detail,body,row[1],s))):raise ValueError
            return study
        except (ValueError,TypeError,KeyError,AttributeError,InvalidSignature,
                EvaluacionConsumo.DoesNotExist,ConsentimientoConsumo.DoesNotExist,SolicitudConsumo.DoesNotExist):
            raise wire.NoDisponible from None


def verificar_custodia_contractual_posterior(detail,body,registrado_en,case=None):
    """Match the signed input snapshot without deleting later document revisions."""
    from copy import deepcopy
    from datetime import datetime,timezone
    from uuid import UUID
    import re
    from .funcional import huella_consumo_estudio
    try:
        if body['version']!='credihoy-estudio-tecnico-firmado-v2':return False
        cutoff=registrado_en if isinstance(registrado_en,datetime) else datetime.fromisoformat(registrado_en)
        if cutoff.utcoffset() is None or cutoff>datetime.now(timezone.utc):return False
        data=detail['orchestration_data'];history=data.get('credihoy_pdf_oficiales_v1')
        if type(history) is not dict or not 1<=len(history)<=20:return False
        retained={};removed=[]
        for key,entry in history.items():
            if str(UUID(key))!=key or type(entry) is not dict:return False
            created=datetime.fromisoformat(entry['creadoEn'])
            if created.utcoffset() is None or created>datetime.now(timezone.utc):return False
            if created<=cutoff:retained[key]=entry;continue
            meta=entry['metadata']
            if (meta['codigo']!='COLO-FO.001' or meta['version']!='13'
                    or meta['solicitudId']!=body['solicitudId']
                    or meta['originalSha256']!='93a8f53214a95fdbeb249572f40cac9c751d3b8ac481372e9d11dfa75899cc9e'
                    or not re.fullmatch('[a-f0-9]{64}',meta['pdfSha256'])
                    or type(entry['contenidoCifrado']) is not str or not entry['contenidoCifrado']):return False
            removed.append(key)
        if not removed:return False
        snapshot=deepcopy(detail);snapshot['orchestration_data']['credihoy_pdf_oficiales_v1']=retained
        empty=['credihoy_pdf_oficiales_v1'] if not retained else []
        contracts=data.get('credihoy_otp_contractual_v1')
        if contracts:
            from types import SimpleNamespace
            from .consentimiento_real import _leer_contractual
            if (case is None or str(case.pk)!=body['solicitudId']
                    or type(contracts) is not dict or len(contracts)>20):return False
            previous={}
            for key,stored in contracts.items():
                record=_leer_contractual(case,key,detail=SimpleNamespace(orchestration_data=data))
                binding=record['vinculo'];document=binding['documentoOperacionId']
                if binding['codigo']=='PRUEBA-FIRMA-STAGING':
                    from .consentimiento_real import _documento_contractual,_fecha_contractual,_exigir_auditoria_contractual,_digest_contractual
                    alias='cotizacion' if 'cotizacion' in settings.DATABASES else case._state.db
                    scoped=case.__class__.objects.using(alias).select_related('solicitante').filter(pk=case.pk,solicitante_id=case.solicitante_id).first()
                    if scoped is None:return False
                    _,meta=_documento_contractual(scoped,document,historico=True)
                    _exigir_auditoria_contractual(scoped,alias,'presentado',key,{'vinculoSha256':_digest_contractual(binding)})
                    if (binding['versionDocumento']!='1' or binding['pdfSha256']!=meta['pdfSha256']
                            or binding['originalSha256']!=meta['originalSha256']
                            or meta.get('proposito')!='PRUEBA_INTEGRACION_AUTENTIC'
                            or binding['proposito']!='FIRMA_DOCUMENTO_CONTRACTUAL'
                            or binding['scope']!='solicitudes:firmarContrato'
                            or _fecha_contractual(meta['preparadoEn'])<=cutoff):return False
                    continue
                if document not in removed:previous[key]=stored;continue
                meta=history[document]['metadata']
                if (binding['proposito']!='FIRMA_DOCUMENTO_CONTRACTUAL'
                        or binding['scope']!='solicitudes:firmarContrato'
                        or binding['codigo']!='COLO-FO.001' or binding['versionDocumento']!='13'
                        or binding['pdfSha256']!=meta['pdfSha256']
                        or binding['originalSha256']!=meta['originalSha256']):return False
            snapshot['orchestration_data']['credihoy_otp_contractual_v1']=previous
            if not previous:empty.append('credihoy_otp_contractual_v1')
        candidates=[snapshot]
        for key in empty:
            for value in list(candidates):
                absent=deepcopy(value);absent['orchestration_data'].pop(key);candidates.append(absent)
        return any(huella_consumo_estudio(value,body['version'])==body['consumoSha256'] for value in candidates)
    except (ValueError,KeyError,TypeError,AttributeError):return False

