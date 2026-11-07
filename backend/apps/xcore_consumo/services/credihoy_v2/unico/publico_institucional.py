"""Private HTTP transport, never a staff token or a browser credential."""


















import hashlib
import hmac
import json
import re
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import requests
from django.conf import settings

BASE = '/api/interno/credihoy/acceso/'
VERSION = 'credihoy-institucional-v1'
PRODUCTO = 'CREDIHOY UNICO (NUEVO)'


class NoDisponible(Exception):
    pass


class NoEncontrado(NoDisponible):
    pass


RECHAZOS = {
    'AUTHORIZATION_WINDOW_EXPIRED': (403, 'El periodo de prueba finalizo. Solicita una nueva habilitacion.', 'CONTACTAR_ENTIDAD'),
    'DOCUMENT_PRESENTATION_EXPIRED': (410, 'La revision del documento vencio. Actualiza la autorizacion y revisala de nuevo.', 'ACTUALIZAR_AUTORIZACION'),
    'AUTHORIZATION_CONFLICT': (409, 'La autorizacion tiene un estado diferente. Consulta su estado antes de continuar.', 'CONSULTAR_ESTADO'),
    'AUTHORIZATION_SECURITY_REJECTED': (403, 'No pudimos validar esta autorizacion. Ingresa nuevamente de forma personal.', 'INICIAR_SESION'),
    'AUTHORIZATION_UNAVAILABLE': (503, 'No pudimos confirmar la operacion. Consulta su estado antes de continuar.', 'CONSULTAR_ESTADO'),
}


class RechazoOperacion(Exception):
    def __init__(self, codigo):
        if codigo not in RECHAZOS:raise ValueError('CODIGO_RECHAZO_NO_PERMITIDO')
        self.codigo=codigo
        self.status_code,self.mensaje,self.accion=RECHAZOS[codigo]
        super().__init__(codigo)


def clasificar_rechazo(error):
    from django.core.signing import SignatureExpired, BadSignature
    from django.db import DatabaseError
    if isinstance(error,RechazoOperacion):return error
    if isinstance(error,SignatureExpired):return RechazoOperacion('DOCUMENT_PRESENTATION_EXPIRED')
    if isinstance(error,BadSignature):return RechazoOperacion('AUTHORIZATION_SECURITY_REJECTED')
    if isinstance(error,DatabaseError):
        cause=error.__cause__ or error
        state=getattr(cause,'sqlstate',None) or getattr(cause,'pgcode',None)
        if state=='P0001':
            if 'AUTHORIZATION_WINDOW_EXPIRED' in str(cause):return RechazoOperacion('AUTHORIZATION_WINDOW_EXPIRED')
            return RechazoOperacion('AUTHORIZATION_SECURITY_REJECTED')
        if state=='23505':return RechazoOperacion('AUTHORIZATION_CONFLICT')
        return RechazoOperacion('AUTHORIZATION_UNAVAILABLE')
    if isinstance(error,ValueError) and str(error) in {'AUTORIZACION_EXISTENTE_NO_SOBRESCRIBIR','ETAPA_AUTORIZACION_NO_DISPONIBLE'}:
        return RechazoOperacion('AUTHORIZATION_CONFLICT')
    return None


def respuesta_rechazo(error, rid=None):
    return {'versionContrato':VERSION,'requestId':rid,'codigo':error.codigo,
        'mensaje':error.mensaje,'accion':error.accion}


def secret():
    key = getattr(settings, 'CREDIHOY_INSTITUCIONAL_SECRET', '')
    if not isinstance(key, str) or len(key) < 48:
        raise NoDisponible
    return key.encode()


def signature(path, body, rid, stamp):
    value = '\n'.join(('POST', path, rid, stamp, hashlib.sha256(body).hexdigest()))
    return hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()


def headers(path, body, rid, stamp=None):
    stamp = str(int(time.time())) if stamp is None else stamp
    return {'Content-Type': 'application/json', 'Accept': 'application/json',
        'X-Request-ID': rid, 'X-Credihoy-Timestamp': stamp,
        'X-Credihoy-Signature': signature(path, body, rid, stamp)}


def encode(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()


def authorize(request):
    if getattr(settings, 'CREDIHOY_INSTITUCIONAL_ENABLED', False) is not True:
        raise NoDisponible
    rid = request.headers.get('X-Request-ID', '')
    stamp = request.headers.get('X-Credihoy-Timestamp', '')
    supplied = request.headers.get('X-Credihoy-Signature', '')
    if (request.method != 'POST' or not request.is_secure()
            or request.content_type != 'application/json' or len(request.body) > 4096
            or str(UUID(rid)) != rid or not re.fullmatch(r'[0-9]{10}', stamp)
            or abs(time.time() - int(stamp)) > 60
            or not re.fullmatch(r'[a-f0-9]{64}', supplied)
            or not hmac.compare_digest(supplied, signature(request.path, request.body, rid, stamp))):
        raise ValueError('ACCESO_INSTITUCIONAL_DENEGADO')
    return rid


def call(action, payload, rid):
    origin = getattr(settings, 'CREDIHOY_INSTITUCIONAL_URL', '')
    path = BASE + action + '/'
    body = encode(payload)
    return request_packet(origin,path,body,rid,headers(path,body,rid))


def request_packet(origin, path, body, rid, request_headers):
    """Common verified HTTPS transport; never retry a provider-bearing request."""
    u = urlsplit(origin)
    if (u.scheme != 'https' or not u.hostname or u.username or u.password
            or u.query or u.fragment or u.path not in ('', '/')):
        raise NoDisponible
    try:
        with requests.post(origin.rstrip('/') + path, data=body, headers=request_headers,
                timeout=(5, 20), allow_redirects=False, verify=True) as response:
            if response.status_code in (403,409,410,503) and response.headers.get('X-Request-ID')==rid and 'application/json' in response.headers.get('Content-Type',''):
                rejection=response.json()
                code=rejection.get('codigo') if type(rejection) is dict else None
                if (code in RECHAZOS and RECHAZOS[code][0]==response.status_code
                        and rejection.get('versionContrato')==VERSION and rejection.get('requestId')==rid):
                    raise RechazoOperacion(code)
            if response.status_code == 404:
                raise NoEncontrado
            if (response.status_code != 200 or response.headers.get('X-Request-ID') != rid
                    or 'application/json' not in response.headers.get('Content-Type', '')):
                raise NoDisponible
            packet = response.json()
    except (requests.RequestException, ValueError):
        raise NoDisponible from None
    if (type(packet) is not dict or packet.get('versionContrato') != VERSION
            or packet.get('requestId') != rid):
        raise NoDisponible
    return packet


def enviar(acceso_id):
    rid = str(uuid5(UUID(str(acceso_id)), 'ENVIO'))
    p = call('verify/enviar', {'accesoId': str(acceso_id)}, rid)
    if p.get('estadoProveedor') != 'pending' or not re.fullmatch(r'VE[a-fA-F0-9]{32}', p.get('verificationSid', '')):
        raise NoDisponible
    return SimpleNamespace(sid=p['verificationSid'], status='pending')


def comprobar(acceso_id, numero_intento, codigo, contacto, canal, verification_sid):
    rid = str(uuid5(UUID(str(acceso_id)), 'COMPROBAR:' + str(numero_intento)))
    p = call('verify/comprobar', {'accesoId': str(acceso_id), 'numeroIntento': numero_intento, 'codigo': codigo}, rid)
    if p.get('verificationSid') != verification_sid or p.get('estadoProveedor') not in ('approved', 'pending'):
        raise NoDisponible
    return SimpleNamespace(sid=verification_sid, status=p['estadoProveedor'], to=contacto,
                           channel='sms' if canal == 'SMS' else 'email')


def seguimiento(grant, solicitud_id=None, *, borrador=None,autorizacion=None):
    payload = {'accesoId': str(grant.pk), 'navegador': grant.navegador_hmac}
    if solicitud_id is not None:
        payload['solicitudId'] = str(solicitud_id)
    if borrador is not None:
        if solicitud_id is not None:raise ValueError
        payload['borrador']=borrador
    if autorizacion is not None:
        if solicitud_id is not None or borrador is not None:raise ValueError
        payload['autorizacion']=autorizacion
    from uuid import uuid4
    p = call('seguimiento', payload, str(uuid4()))
    if autorizacion is not None:
        if p.get('sesionReferencia')!=str(grant.pk):raise NoDisponible
        return {'sesionReferencia':str(grant.pk),'autorizacion':project_authorization(p['autorizacion'])}
    if borrador is not None:
        if p.get('sesionReferencia')!=str(grant.pk) or 'borrador' not in p:raise NoDisponible
        result={'sesionReferencia':str(grant.pk),'borrador':project_draft(p['borrador'])}
        if 'avisoTemporal' in p:result['avisoTemporal']=project_progress_notice(p['avisoTemporal'])
        return result
    if (p.get('sesionReferencia') != str(grant.pk) or type(p.get('solicitudes')) is not list
            or len(p['solicitudes']) > 20):
        raise NoDisponible
    rows = [project_row(row) for row in p['solicitudes']]
    if (len({v['referencia'] for v in rows}) != len(rows)
            or (solicitud_id is not None and any(v['referencia'] != str(solicitud_id) for v in rows))):
        raise NoDisponible
    return {'sesionReferencia': str(grant.pk), 'solicitudes': rows}


def project_authorization(row):
    fields={'versionContrato','solicitudId','estado','documento','firmado','consultasHabilitadas','evaluacionHabilitada'}
    if (type(row) is not dict or set(row) not in (fields,fields|{'firma'},fields|{'complemento'},fields|{'centrales'}) or row['versionContrato']!='credihoy-autorizacion-titular-v1'
            or any(row[k] is not False for k in ('firmado','consultasHabilitadas','evaluacionHabilitada'))
            or str(UUID(row['solicitudId']))!=row['solicitudId']
            or row['estado'] not in {'PENDIENTE_ACEPTACION_PERSONAL','PENDIENTE_FIRMA_AUTENTIC',
                'AUTORIZACION_AUSENTE','FIRMA_AUTENTIC_PENDIENTE_VERIFICACION',
                'COMPLEMENTO_REGISTRADO','COMPLEMENTO_PENDIENTE','CENTRALES_SIN_ACEPTACION',
                'CENTRALES_PENDIENTE_ACEPTACION','CENTRALES_PENDIENTE_OTP','CENTRALES_ENVIADO',
                'CENTRALES_OTP_CONFIRMADA','CENTRALES_RECHAZADA','CENTRALES_INDETERMINADA'}):raise NoDisponible
    if 'centrales' in row:
        from datetime import datetime,timezone
        c=row['centrales']
        if (type(c) is not dict or set(c)!={'estado','operacionId','proposito','metodo','textoVersion','textoSha256',
                'pdfSha256','presentacion','confirmadoEn','verificacionReferencia','vigenteHasta'}
                or row['estado']!='CENTRALES_'+c['estado'] or c['proposito']!='CONSULTA_CENTRALES'
                or c['metodo']!='OTP' or c['textoVersion']!='COLO-FO.023:v10'
                or any(type(c[k]) is not str or not re.fullmatch('[a-f0-9]{64}',c[k]) for k in ('textoSha256','pdfSha256'))):raise NoDisponible
        if c['estado']=='SIN_ACEPTACION':
            if any(c[k] is not None for k in ('operacionId','presentacion','confirmadoEn','verificacionReferencia','vigenteHasta')):raise NoDisponible
        else:
            if str(UUID(c['operacionId']))!=c['operacionId']:raise NoDisponible
            end=datetime.fromisoformat(c['vigenteHasta'])
            if end.utcoffset() is None:raise NoDisponible
        if c['presentacion'] is not None and (type(c['presentacion']) is not str or not 1<=len(c['presentacion'])<=4096):raise NoDisponible
        if c['estado']=='OTP_CONFIRMADA':
            when=datetime.fromisoformat(c['confirmadoEn'])
            if (when.utcoffset() is None or when>datetime.now(timezone.utc)
                    or type(c['verificacionReferencia']) is not str or not re.fullmatch('[a-f0-9]{64}',c['verificacionReferencia'])):raise NoDisponible
        elif c['confirmadoEn'] is not None or c['verificacionReferencia'] is not None:raise NoDisponible
    elif row['estado'].startswith('CENTRALES_'):raise NoDisponible
    if 'complemento' in row:
        from datetime import datetime,timezone
        c=row['complemento']
        if (type(c) is not dict or set(c)!={'version','datos','confirmadoEn','fuente'}
                or type(c['version']) is not int or not 0<=c['version']<=20 or row['documento'] is not None):raise NoDisponible
        if c['version']==0:
            if any(c[k] is not None for k in ('datos','confirmadoEn','fuente')) or row['estado']!='COMPLEMENTO_PENDIENTE':raise NoDisponible
        else:
            d=c['datos']
            if (type(d) is not dict or set(d)!={'periodicidadPago','recogerCredihoy','obligacionRecoger'}
                    or d['periodicidadPago']!='MENSUAL' or type(d['recogerCredihoy']) is not bool
                    or c['fuente'] not in {'ELECCION_PERSONAL_COMPLEMENTO','ELECCION_PERSONAL_ENVIO'}
                    or row['estado']!='COMPLEMENTO_REGISTRADO'):raise NoDisponible
            if (d['recogerCredihoy'] and (c['fuente']!='ELECCION_PERSONAL_ENVIO'
                    or type(d['obligacionRecoger']) is not str or not re.fullmatch('[0-9]{1,20}',d['obligacionRecoger']))
                    or not d['recogerCredihoy'] and d['obligacionRecoger'] is not None):raise NoDisponible
            when=datetime.fromisoformat(c['confirmadoEn'])
            if when.utcoffset() is None or when>datetime.now(timezone.utc):raise NoDisponible
    elif row['estado'] in {'COMPLEMENTO_REGISTRADO','COMPLEMENTO_PENDIENTE'}:raise NoDisponible
    document=row['documento']
    if 'firma' in row:
        f=row['firma']
        if (type(f) is not dict or set(f)!={'operacionId','envioHabilitado','identidadVerificada'}
                or str(UUID(f['operacionId']))!=f['operacionId'] or f['envioHabilitado'] is not False
                or type(f['identidadVerificada']) is not bool):raise NoDisponible
    if document is not None:
        import base64
        if (set(document)!={'codigo','version','pdfSha256','pdfBase64','presentacion','operacionId','proposito'}
                or document['codigo']!='COLO-FO.023' or document['version']!='10'
                or document['proposito']!='AUTORIZACION_CENTRALES_PRECONSULTA'
                or len(document['pdfBase64'])>800000
                or hashlib.sha256(base64.b64decode(document['pdfBase64'],validate=True)).hexdigest()!=document['pdfSha256']
                or str(UUID(document['operacionId']))!=document['operacionId']
                or type(document['presentacion']) is not str or not 1<=len(document['presentacion'])<=4096):raise NoDisponible
    return dict(row)


def project_progress_notice(row):
    if (type(row) is not dict or set(row)!={'version','sha256','texto','presentacion','inactividadSegundos'}
            or type(row['version']) is not str or not 1<=len(row['version'])<=80
            or type(row['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}',row['sha256'])
            or type(row['texto']) is not str or not 1<=len(row['texto'])<=4096
            or hashlib.sha256(row['texto'].encode()).hexdigest()!=row['sha256']
            or type(row['presentacion']) is not str or not 1<=len(row['presentacion'])<=2048
            or row['inactividadSegundos']!=3600):raise NoDisponible
    return dict(row)


def project_quote(row):
    """Explicit allowlist: never expose holder hashes, XML, actors or core IDs."""
    from datetime import date, datetime, timezone
    from decimal import Decimal
    try:
        fields = {'versionContrato', 'solicitudId', 'estado', 'cuota', 'oferta', 'aceptacion', 'autorizaStart', 'autorizaDesembolso'}
        own = type(row) is dict and row.get('estado') == 'SIMULACION_PROPIA'
        if own:
            fields |= {'simulacion', 'aceptacionSimulacion'}
            validar_simulacion_propia(row['simulacion'], row['solicitudId'])
            a = row['aceptacionSimulacion']
            if type(a) is not dict or set(a) != {'aceptada', 'fecha'} or type(a['aceptada']) is not bool:
                raise ValueError()
            if a['aceptada']:
                t = datetime.fromisoformat(a['fecha'])
                if t.utcoffset() is None or not datetime.fromisoformat(row['simulacion']['calculadoEn']) <= t <= datetime.now(timezone.utc):
                    raise ValueError()
            elif a['fecha'] is not None:
                raise ValueError()
        if type(row) is not dict or set(row) != fields or row['versionContrato'] != 'credihoy-cotizacion-titular-v1' or (str(UUID(row['solicitudId'])) != row['solicitudId']) or (row['autorizaStart'] is not False) or (row['autorizaDesembolso'] is not False) or (row['estado'] not in {'SIMULACION_PROPIA', 'DISPONIBLE_PARCIAL', 'SIN_COTIZACION', 'NO_HABILITADA', 'REQUIERE_CONCILIACION', 'DESACTUALIZADA', 'PARAMETROS_EXPEDIENTE_INCOMPLETOS', 'PARAMETROS_FALTANTES', 'NO_DISPONIBLE', 'ERROR_TEMPORAL'}):
            raise ValueError()
        offer = row['oferta']
        accept = row['aceptacion']
        if set(accept) != {'aceptada', 'fecha'} or type(accept['aceptada']) is not bool:
            raise ValueError()
        if offer is None:
            if row['cuota'] is not None or accept != {'aceptada': False, 'fecha': None}:
                raise ValueError()
        else:
            expected = {'sha256', 'monto', 'plazoMeses', 'periodicidad', 'tasaNominalNamv', 'tasaEA', 'primerVencimiento', 'vigenteHasta', 'condicionesCompletas', 'seguroIncluidoPorMotor'}
            if 'calendarioPago' in offer:
                expected.add('calendarioPago')
            if set(offer) != expected or not re.fullmatch('[a-f0-9]{64}', offer['sha256']) or offer['periodicidad'] != 'MENSUAL' or (type(offer['plazoMeses']) is not int) or (not 1 <= offer['plazoMeses'] <= 48) or (type(offer['condicionesCompletas']) is not bool) or (offer['seguroIncluidoPorMotor'] is not True):
                raise ValueError()
            for number in (row['cuota'], offer['monto'], offer['tasaNominalNamv'], offer['tasaEA']):
                if number is not None and (type(number) is not str or not re.fullmatch('[0-9]{1,12}(\\.[0-9]{1,8})?', number) or Decimal(number) <= 0):
                    raise ValueError()
            if row['cuota'] is None or offer['monto'] is None or offer['tasaNominalNamv'] is None:
                raise ValueError()
            end = datetime.fromisoformat(offer['vigenteHasta'])
            if end.utcoffset() is None or end <= datetime.now(timezone.utc):
                raise ValueError()
            if offer['primerVencimiento'] is not None:
                from datetime import date
                if date.fromisoformat(offer['primerVencimiento']).isoformat() != offer['primerVencimiento']:
                    raise ValueError()
            complete = offer['primerVencimiento'] is not None and offer['tasaEA'] is not None
            if 'calendarioPago' in offer:
                c = offer['calendarioPago']
                complete = complete and c is not None
                if c is not None:
                    from .condiciones_producto import preparar_calendario, CALENDARIO_VERSION
                    if set(c) != {'versionRegla', 'diaPago', 'fechaBaseDesembolso', 'tipoBase', 'primerVencimiento', 'interesesAnticipados'}:
                        raise ValueError()
                    computed = preparar_calendario(date.fromisoformat(c['fechaBaseDesembolso']), c['diaPago'], medio_pago='VENTANILLA', tipo_base=c['tipoBase'])
                    if c['versionRegla'] != CALENDARIO_VERSION or c['primerVencimiento'] != offer['primerVencimiento'] or c['primerVencimiento'] != computed['primerVencimientoProyectado'] or (c['interesesAnticipados'] != {k: computed['interesesAnticipados'][k] for k in ('desde', 'hasta', 'importe')}):
                        raise ValueError()
            if offer['condicionesCompletas'] != complete:
                raise ValueError()
            if accept['aceptada']:
                when = datetime.fromisoformat(accept['fecha'])
                if when.utcoffset() is None or when > datetime.now(timezone.utc) or (not offer['condicionesCompletas']):
                    raise ValueError()
            elif accept['fecha'] is not None:
                raise ValueError()
        return json.loads(json.dumps(row))
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
        raise NoDisponible from None


def project_draft(row):
    if row is None:return None
    from datetime import datetime, timezone
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    try:
        fields={'referencia','fase','version','datos','promotor','actualizadoAt','acciones','solicitudRegistrada'}
        temporal='venceAt' in row if type(row) is dict else False
        if temporal:fields.add('venceAt')
        if type(row) is dict and 'consultadoAt' in row:fields.add('consultadoAt')
        if (type(row) is not dict or set(row)!=fields or str(UUID(row['referencia']))!=row['referencia']
                or row['fase'] not in ({'BORRADOR','ENVIADA'} if temporal else {'BORRADOR'})
                or row['solicitudRegistrada'] is not (row['fase']=='ENVIADA')
                or type(row['version']) is not int or row['version']<1
                or row['acciones']!={'radicar':False,'firmar':False}):raise ValueError
        data=row['datos']
        if type(data) is not dict or set(data)-{
                'monto','plazo','email','periodicidadPago','recogerCredihoy','obligacionRecoger'}:raise ValueError
        if 'periodicidadPago' in data and data['periodicidadPago']!='MENSUAL':raise ValueError
        if 'recogerCredihoy' in data and type(data['recogerCredihoy']) is not bool:raise ValueError
        if 'obligacionRecoger' in data and (type(data['obligacionRecoger']) is not str
                or not re.fullmatch(r'[0-9]{1,20}',data['obligacionRecoger'])):raise ValueError
        if 'monto' in data and (type(data['monto']) is not str
                or not re.fullmatch(r'[0-9]{1,10}(\.[0-9]{1,2})?',data['monto']) or float(data['monto'])<=0):raise ValueError
        if 'plazo' in data and (type(data['plazo']) is not int or not 1<=data['plazo']<=48):raise ValueError
        if 'email' in data:
            if type(data['email']) is not str or len(data['email'])>254:raise ValueError
            validate_email(data['email'])
        promoter=row['promotor']
        if promoter is not None and (type(promoter) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',promoter)):raise ValueError
        observed=datetime.fromisoformat(row['actualizadoAt'])
        if observed.utcoffset() is None or observed>datetime.now(timezone.utc):raise ValueError
        if 'consultadoAt' in fields:
            consulted=datetime.fromisoformat(row['consultadoAt'])
            if consulted.utcoffset() is None or consulted<observed or consulted>datetime.now(timezone.utc):raise ValueError
        if temporal:
            if row['fase']=='ENVIADA':
                if row['venceAt'] is not None:raise ValueError
            else:
                from datetime import timedelta
                expires=datetime.fromisoformat(row['venceAt'])
                if expires.utcoffset() is None or expires-observed!=timedelta(hours=1):raise ValueError
        return {**{k:row[k] for k in fields},'datos':dict(data),'acciones':{'radicar':False,'firmar':False}}
    except (ValueError,TypeError,KeyError,AttributeError,ValidationError):raise NoDisponible from None


def project_row(row):
    from datetime import datetime
    titles = {'Actualiza tus datos', 'Tu solicitud esta en revision', 'Estamos verificando tu solicitud'}
    pending = {'Debes actualizar tus datos para continuar con tu solicitud.', 'Necesitamos completar algunas validaciones antes de tomar una decision.', 'Hay requisitos pendientes que requieren revision con Congente.', 'Comunicate con Congente para revisar el estado.'}
    try:
        if type(row) is not dict or str(UUID(row['referencia'])) != row['referencia'] or row['titulo'] not in titles or (not re.fullmatch('[0-9]{1,10}(\\.[0-9]{1,2})?', row['monto'])) or (type(row['plazoMeses']) is not int) or (not 1 <= row['plazoMeses'] <= 48) or (row['periodicidad'] != 'Mensual') or (not datetime.fromisoformat(row['fechaCreacion']).tzinfo) or (row['requisitosCumplidos'] is not None and (type(row['requisitosCumplidos']) is not int or not 0 <= row['requisitosCumplidos'] <= 16)) or (type(row['pendientes']) is not list) or any((v not in pending for v in row['pendientes'])) or (row['siguientePaso'] not in {'Actualiza tus datos con Congente.', 'Espera la revision de Congente.'}) or (row['ofertaDisponible'] is not False) or (row['acciones'] != {'radicar': False, 'firmar': False}):
            raise ValueError
        clean = {k: row[k] for k in ('referencia', 'titulo', 'monto', 'plazoMeses', 'periodicidad', 'fechaCreacion', 'requisitosCumplidos', 'pendientes', 'siguientePaso')}
        clean.update(ofertaDisponible=False, acciones={'radicar': False, 'firmar': False}, simulacion=None, institucional=None)
        institution = row.get('institucional')
        if institution is not None:
            from datetime import timezone
            fields = {'estado', 'fechaDecision', 'actualizadoAt', 'documentos', 'firma', 'incidencias'}
            if type(institution) is not dict or set(institution) != fields:
                raise ValueError
            decision = datetime.strptime(institution['fechaDecision'], '%Y-%m-%d').date()
            observed = datetime.fromisoformat(institution['actualizadoAt'])
            if decision.isoformat() != institution['fechaDecision'] or observed.utcoffset() is None or observed > datetime.now(timezone.utc) or (decision > observed.date()) or (institution['estado'] != 'APROBADA_PENDIENTE_FIRMA') or (institution['documentos'] != 'NO_ACREDITADOS') or (institution['firma'] != 'PENDIENTE') or (institution['incidencias'] not in {'NO_DISPONIBLE', 'EN_REVISION', 'ANTECEDENTES_SIN_PENDIENTE_ACTIVO', 'SIN_INCIDENCIAS_REGISTRADAS'}):
                raise ValueError
            clean['institucional'] = {k: institution[k] for k in fields}
        q = row.get('simulacion')
        if q is not None:
            if type(q) is not dict or not re.fullmatch('[0-9]{1,10}(\\.[0-9]{1,7})?', q['cuota']) or float(q['cuota']) <= 0 or (not re.fullmatch('[0-9]{1,3}(\\.[0-9]{1,7})?', q['tasaNominalNamv'])) or (q['origen'] not in {'REFERENCIA_INSTITUCIONAL_REGISTRADA', 'CALCULO_INSTITUCIONAL'}) or any((q[k] is not False for k in ('ofertaContractual', 'seguroDisponible', 'calendarioDisponible'))):
                raise ValueError
            clean['simulacion'] = {k: q[k] for k in ('cuota', 'tasaNominalNamv', 'origen', 'ofertaContractual', 'seguroDisponible', 'calendarioDisponible')}
        provenance = row.get('procedencia')
        if provenance is not None:
            from datetime import timezone
            from apps.solicitudes.models import EstadoSolicitud
            if type(provenance) is not dict or set(provenance) != {'origen', 'estadoRegistrado', 'actualizadoAt', 'consultadoAt'} or provenance['origen'] not in {'HISTORICO_PILOTO', 'FUENTE_OPERATIVA'} or (provenance['estadoRegistrado'] not in EstadoSolicitud.labels):
                raise ValueError
            updated = datetime.fromisoformat(provenance['actualizadoAt'])
            consulted = datetime.fromisoformat(provenance['consultadoAt'])
            if updated.utcoffset() is None or consulted.utcoffset() is None or (not updated <= consulted <= datetime.now(timezone.utc)) or (provenance['origen'] == 'HISTORICO_PILOTO' and (clean['institucional'] is not None or clean['simulacion'] is not None or clean['requisitosCumplidos'] is not None)):
                raise ValueError
            clean['procedencia'] = dict(provenance)
        if row.get('estudio') is not None:
            if row.get('procedencia', {}).get('origen') != 'FUENTE_OPERATIVA':
                raise ValueError
            clean['estudio'] = project_study(row['estudio'], row['referencia'])
            if clean['requisitosCumplidos'] != sum((r['resultado'] == 'CUMPLE' for r in clean['estudio']['reglas'])):
                raise ValueError
        if row.get('formalizacionDocumental') is not None:
            p = row['formalizacionDocumental']
            if type(p) is not dict or set(p) != {'versionContrato', 'documento', 'estado', 'firmaAcreditada', 'actualizadoAt', 'continuacionInstitucionalPendiente'} or p['versionContrato'] != 'credihoy-formalizacion-publica-v1' or (p['documento'] != 'COLO-FO.001') or (type(p['firmaAcreditada']) is not bool) or (p['continuacionInstitucionalPendiente'] is not True) or (p['estado'] != ('DOCUMENTO_VERIFICADO' if p['firmaAcreditada'] else 'VALIDACION_PENDIENTE')) or (datetime.fromisoformat(p['actualizadoAt']).utcoffset() is None):
                raise ValueError
            clean['formalizacionDocumental'] = dict(p)
        if row.get('envio') is not None:
            from datetime import timezone
            from decimal import Decimal
            submitted = row['envio']
            if type(submitted) is not dict or set(submitted) != {'operacionId', 'confirmadoEn', 'solicitud'} or str(UUID(submitted['operacionId'])) != submitted['operacionId'] or (row.get('procedencia', {}).get('origen') != 'FUENTE_OPERATIVA'):
                raise ValueError
            confirmed = datetime.fromisoformat(submitted['confirmadoEn'])
            draft = project_draft(submitted['solicitud'])
            if confirmed.utcoffset() is None or confirmed > datetime.now(timezone.utc) or draft is None or (draft['fase'] != 'ENVIADA') or (draft['referencia'] != row['referencia']) or (Decimal(draft['datos']['monto']) != Decimal(row['monto'])) or (draft['datos']['plazo'] != row['plazoMeses']):
                raise ValueError
            clean['envio'] = {**submitted, 'solicitud': draft}
        if row.get('simulacionLocal') is not None:
            from decimal import Decimal
            estimate = project_simulation(row['simulacionLocal'])
            if estimate.get('solicitudId') != row['referencia'] or estimate['resultado'] is None or Decimal(estimate['resultado']['monto']) != Decimal(row['monto']) or (estimate['resultado']['plazoMeses'] != row['plazoMeses']):
                raise ValueError
            clean['simulacionLocal'] = estimate
        return clean
    except (TypeError, KeyError, ValueError, AttributeError):
        raise NoDisponible from None

def project_study(value, solicitud_id):
    from datetime import datetime, timezone
    try:
        fields = {'versionContrato', 'solicitudId', 'evaluacionBaseId', 'version', 'referenciaVersion', 'fechaEvaluacion', 'reglas', 'capacidad', 'decision', 'autorizadoDecisionCrediticia'}
        if type(value) is dict and 'decisionPolitica' in value:
            fields.add('decisionPolitica')
            decision = value['decisionPolitica']
            decision_fields = {'version', 'versionPolitica', 'estado', 'motivos', 'decisionLinixRegistrada', 'autorizaDesembolso'}
            if 'continuidad' in decision:
                decision_fields.add('continuidad')
                c = decision['continuidad']
                if type(c) is not dict or set(c) != {'version', 'estudioFavorable', 'estadoRadicacion', 'decisionInstitucional', 'firmaContractual', 'radicacionHabilitada', 'firmaHabilitada'} or c['version'] != 'credihoy-continuidad-publica-v1' or (type(c['estudioFavorable']) is not bool) or (c['estadoRadicacion'] != 'PREPARACION_PENDIENTE') or (c['decisionInstitucional'] != 'PENDIENTE') or (c['firmaContractual'] != 'PENDIENTE') or (c['radicacionHabilitada'] is not False) or (c['firmaHabilitada'] is not False) or (c['estudioFavorable'] and (decision['estado'] == 'RECHAZADO' or any((r['codigo'] != 'REVISION_ESTAMENTO_INSTITUCIONAL' for r in decision['motivos'])))):
                    raise ValueError
            if type(decision) is not dict or set(decision) != decision_fields or decision['version'] != 'credihoy-decision-publica-v1' or (type(decision['versionPolitica']) is not str) or (not 1 <= len(decision['versionPolitica']) <= 120) or (decision['estado'] not in {'APROBADO', 'RECHAZADO', 'EN_REVISION'}) or (decision['decisionLinixRegistrada'] is not False) or (decision['autorizaDesembolso'] is not False) or (type(decision['motivos']) is not list) or (len(decision['motivos']) > 40):
                raise ValueError
            for reason in decision['motivos']:
                if type(reason) is not dict or set(reason) != {'codigo', 'regla'} or type(reason['codigo']) is not str or (not re.fullmatch('[A-Z][A-Z0-9_]{1,120}', reason['codigo'])) or (reason['regla'] is not None and (not re.fullmatch('UNICO_FILA_[0-9]{1,2}', str(reason['regla'])))):
                    raise ValueError
        if type(value) is not dict or set(value) != fields or value['versionContrato'] != 'credihoy-estudio-publico-v1' or (value['solicitudId'] != str(solicitud_id)) or (str(UUID(value['solicitudId'])) != value['solicitudId']) or (type(value['evaluacionBaseId']) is not int) or (value['evaluacionBaseId'] < 1) or (type(value['version']) is not int) or (value['version'] < 1) or (str(UUID(value['referenciaVersion'])) != value['referenciaVersion']) or (value['autorizadoDecisionCrediticia'] is not False) or (value['decision'] not in {'PENDIENTE_REVISION', 'REQUIERE_SUBSANACION'}):
            raise ValueError
        observed = datetime.fromisoformat(value['fechaEvaluacion'])
        if observed.utcoffset() is None or observed > datetime.now(timezone.utc):
            raise ValueError
        codes = {f'UNICO_FILA_{n}' for n in (2, 4, 6, 9, 10, 12, 14, 17, 21, 23, 24, 27, 28, 30, 31, 32)}
        rows = value['reglas']
        if type(rows) is not list or len(rows) != 16 or {r.get('codigo') for r in rows} != codes:
            raise ValueError
        for row in rows:
            if type(row) is not dict or set(row) != {'codigo', 'nombre', 'resultado', 'estadoEvaluacion', 'motivos', 'datosFaltantes'} or row['resultado'] not in {'CUMPLE', 'NO_CUMPLE', 'NO_EVALUABLE'} or (row['estadoEvaluacion'] not in {'CUMPLE', 'NO_CUMPLE', 'NO_EVALUABLE', 'NO_APLICA', 'PENDIENTE_POLITICA'}) or (type(row['nombre']) is not str) or (not 1 <= len(row['nombre']) <= 120):
                raise ValueError
            for key in ('motivos', 'datosFaltantes'):
                if type(row[key]) is not list or len(row[key]) > 20 or any((type(v) is not str or not 1 <= len(v) <= 240 for v in row[key])):
                    raise ValueError
        if value.get('decisionPolitica', {}).get('continuidad', {}).get('estudioFavorable') is True:
            if any((r['estadoEvaluacion'] not in {'CUMPLE', 'NO_APLICA'} and (not (r['codigo'] == 'UNICO_FILA_6' and r['estadoEvaluacion'] == 'NO_CUMPLE')) for r in rows)):
                raise ValueError
        cap = value['capacidad']
        capacity_fields = {'estado', 'valor', 'preliminarNoDecisoria', 'cuotaContractualDisponible'}
        if 'metodologia' in cap:
            if value['version'] < 2:
                raise ValueError
            capacity_fields.add('metodologia')
            method = cap['metodologia']
            amounts = ('disponibleAntesGastosFamiliares', 'gastosFamiliaresAplicados', 'capacidadDisponible', 'cuotaNuevaEstimada', 'residualPosteriorEstimado')
            if type(method) is not dict or set(method) != {'version', 'deudaConciliada', *amounts} or method['version'] != 'credihoy-capacidad-publica-v1' or (type(method['deudaConciliada']) is not bool):
                raise ValueError
            for key in amounts:
                if method[key] is not None and (type(method[key]) is not str or not re.fullmatch('-?[0-9]{1,15}(?:\\.[0-9]{1,8})?', method[key])):
                    raise ValueError
            from decimal import Decimal, localcontext
            with localcontext() as context:
                context.prec = 60
                if method['gastosFamiliaresAplicados'] is not None and Decimal(method['gastosFamiliaresAplicados']) < 0:
                    raise ValueError
                if method['cuotaNuevaEstimada'] is not None and Decimal(method['cuotaNuevaEstimada']) <= 0:
                    raise ValueError
                if all((method[k] is not None for k in amounts[:3])):
                    if Decimal(method[amounts[0]]) - Decimal(method[amounts[1]]) != Decimal(method[amounts[2]]):
                        raise ValueError
                if all((method[k] is not None for k in amounts[2:])):
                    if Decimal(method[amounts[2]]) - Decimal(method[amounts[3]]) != Decimal(method[amounts[4]]):
                        raise ValueError
        separated = ('capacidadDisponible', 'capacidadResidualPostCuota') if value['version'] >= 8 else ()
        if type(cap) is not dict or set(cap) != capacity_fields | set(separated) or cap['estado'] not in {'EVALUABLE', 'NO_EVALUABLE', 'INFORMACION_INCONSISTENTE'} or (cap['cuotaContractualDisponible'] is not False):
            raise ValueError
        for key in ('valor', 'preliminarNoDecisoria') + separated:
            if cap[key] is not None and (type(cap[key]) is not str or not re.fullmatch('-?[0-9]{1,15}(?:\\.[0-9]{1,8})?', cap[key])):
                raise ValueError
        if any((cap[key] is None for key in separated)):
            raise ValueError
        if (cap['estado'] == 'EVALUABLE') != (cap['valor'] is not None):
            raise ValueError
        import copy
        return copy.deepcopy(value)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise NoDisponible from None


def project_simulation(row):
    """Closed orientative contract shared by relay and public boundaries."""
    from decimal import Decimal
    from .simulacion_lectura import AVISO_SIMULACION, CONCEPTOS_PENDIENTES

    def fields(value, names):
        if type(value) is not dict or set(value) != set(names.split()):
            raise ValueError

    def money(value):
        if type(value) is not str or not re.fullmatch('[0-9]{1,12}\\.[0-9]{2}', value):
            raise ValueError
        return Decimal(value)

    def decimal(value):
        if type(value) is not str or not re.fullmatch('[0-9]{1,3}(\\.[0-9]{1,55})?', value):
            raise ValueError
        return Decimal(value)
    try:
        target = 'borradorId' if 'borradorId' in row else 'solicitudId'
        fields(row, 'versionContrato ' + target + ' requestId alcance limites tasa seguro resultado aviso ofertable autorizaDecision autorizaStart autorizaDesembolso')
        if row['versionContrato'] != 'credihoy-simulacion-local-v1' or row['aviso'] != AVISO_SIMULACION or row['alcance'] != 'ESTIMACION_GENERICA_NO_ELEGIBILIDAD' or any((row[k] is not False for k in ('ofertable', 'autorizaDecision', 'autorizaStart', 'autorizaDesembolso'))) or any((str(UUID(row[k])) != row[k] for k in (target, 'requestId'))):
            raise ValueError
        l, t, s, r = (row[k] for k in ('limites', 'tasa', 'seguro', 'resultado'))
        fields(l, 'version referencia montoMinimo montoMaximo cuotasMinimas cuotasMaximas')
        if any((type(l[k]) is not str or not 1 <= len(l[k]) <= 240 for k in ('version', 'referencia'))) or not 0 < Decimal(l['montoMinimo']) <= money(l['montoMaximo']) or any((type(l[k]) is not int for k in ('cuotasMinimas', 'cuotasMaximas'))) or (not 1 <= l['cuotasMinimas'] <= l['cuotasMaximas'] <= 48):
            raise ValueError
        if type(l['montoMinimo']) is not str or not re.fullmatch('[0-9]{1,10}(\\.[0-9]{1,2})?', l['montoMinimo']):
            raise ValueError
        fields(t, 'valor unidad tarifaId version documento versionDocumento referencia soporteSha256 linea')
        if t['linea'] not in {'LIBRANZA', 'VENTANILLA'} or decimal(t['valor']) != {'LIBRANZA': Decimal('16.67'), 'VENTANILLA': Decimal('17.69')}[t['linea']] or t['unidad'] != 'PORCENTAJE_NAMV' or any((type(t[k]) is not int or t[k] < 1 for k in ('tarifaId', 'version'))) or any((type(t[k]) is not str or not 1 <= len(t[k]) <= 240 for k in ('documento', 'versionDocumento', 'referencia'))) or (not re.fullmatch('[a-f0-9]{64}', t['soporteSha256'])):
            raise ValueError
        fields(s, 'tasaMensualDecimal base fuente comparacionLinix')
        if not 0 <= decimal(s['tasaMensualDecimal']) <= Decimal('0.01') or s['base'] != 'CAPITAL_INICIAL' or s['fuente'] not in {'calculadora.calcular_vida_deudores', 'calculadora.calcular_plan_credihoy'} or (s['comparacionLinix'] != 'BASE_Y_REDONDEO_NO_CONCILIADOS'):
            raise ValueError
        if r is not None:
            fields(r, 'estado unidad monto plazoMeses periodicidad tasaMensualDecimal tasaEaPorcentaje plazoMaximoParaMonto cuotaCapitalInteres seguro cuotaEstimada totalCapital totalIntereses totalSeguroEstimado totalEstimado amortizacion conceptosNoAcreditados fuente precision')
            if r['estado'] != 'ORIENTATIVA' or r['unidad'] != 'COP' or r['periodicidad'] != 'MENSUAL' or (type(r['plazoMeses']) is not int) or (type(r['plazoMaximoParaMonto']) is not int) or (not l['cuotasMinimas'] <= r['plazoMeses'] <= min(l['cuotasMaximas'], r['plazoMaximoParaMonto']) <= 48) or (not Decimal(l['montoMinimo']) <= money(r['monto']) <= money(l['montoMaximo'])) or (decimal(r['tasaMensualDecimal']) != decimal(t['valor']) / Decimal(1200)) or (decimal(r['tasaEaPorcentaje']) <= 0) or (r['conceptosNoAcreditados'] != CONCEPTOS_PENDIENTES) or ((r['fuente'], r['precision']) not in {('calculadora.calcular_valor_cuota/numpy_financial.pmt', 'PMT_LEGACY_2_DECIMALES_INTERES_HALF_UP_AJUSTE_ULTIMA_CUOTA_NO_CONTRACTUAL'), ('calculadora.calcular_plan_credihoy', 'DECIMAL_60_2_DECIMALES_HALF_UP_AJUSTE_ULTIMA_CUOTA_NO_CONTRACTUAL')}):
                raise ValueError
            fields(r['seguro'], 'tasaMensualDecimal base baseCOP valorMensualEstimado fuente comparacionLinix')
            if any((r['seguro'][k] != v for k, v in s.items())) or r['seguro']['baseCOP'] != r['monto'] or money(r['cuotaEstimada']) != money(r['cuotaCapitalInteres']) + money(r['seguro']['valorMensualEstimado']) or (type(r['amortizacion']) is not list) or (len(r['amortizacion']) != r['plazoMeses']):
                raise ValueError
            balance, capital, interest, premium, total = (money(r['monto']), Decimal(0), Decimal(0), Decimal(0), Decimal(0))
            for n, a in enumerate(r['amortizacion'], 1):
                fields(a, 'numero saldoInicial capital intereses subtotalCapitalInteres seguroEstimado cuotaEstimada saldoFinal')
                if type(a['numero']) is not int or a['numero'] != n or money(a['saldoInicial']) != balance or (money(a['capital']) <= 0) or (money(a['saldoFinal']) != balance - money(a['capital'])) or (money(a['subtotalCapitalInteres']) != money(a['capital']) + money(a['intereses'])) or (money(a['seguroEstimado']) != money(r['seguro']['valorMensualEstimado'])) or (money(a['cuotaEstimada']) != money(a['subtotalCapitalInteres']) + money(a['seguroEstimado'])) or (n < r['plazoMeses'] and a['subtotalCapitalInteres'] != r['cuotaCapitalInteres']):
                    raise ValueError
                balance = money(a['saldoFinal'])
                capital += money(a['capital'])
                interest += money(a['intereses'])
                premium += money(a['seguroEstimado'])
                total += money(a['cuotaEstimada'])
            if balance != 0 or capital != money(r['monto']) or capital != money(r['totalCapital']) or (interest != money(r['totalIntereses'])) or (premium != money(r['totalSeguroEstimado'])) or (total != money(r['totalEstimado'])):
                raise ValueError
        return json.loads(json.dumps(row))
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
        raise NoDisponible from None



def project_study_decision(result):
    state = result['estado']
    public_state = state if state in {'APROBADO', 'RECHAZADO'} else 'EN_REVISION'
    reasons = [{'codigo': v['codigo'], 'regla': v.get('regla')} for v in result.get('causalElegibilidad', [])]
    if state == 'BLOQUEO_ESTAMENTOS_SIN_EVIDENCIA':
        reasons.append({'codigo': 'CLASIFICACION_INSTITUCIONAL_PENDIENTE', 'regla': None})
    elif state.startswith('REQUIERE_'):
        reasons.append({'codigo': 'REVISION_ESTAMENTO_INSTITUCIONAL', 'regla': None})
    elif state == 'POLITICA_NO_APLICABLE':
        reasons.append({'codigo': 'POLITICA_PRODUCTO_NO_APLICABLE', 'regla': None})
    projection = {'version': 'credihoy-decision-publica-v1', 'versionPolitica': result['versionPolitica'], 'estado': public_state, 'motivos': reasons, 'decisionLinixRegistrada': False, 'autorizaDesembolso': False}
    if 'preparacion' in result:
        from .elegibilidad import continuidad_desde_decision
        continuity = continuidad_desde_decision(result)
        projection['continuidad'] = {'version': 'credihoy-continuidad-publica-v1', 'estudioFavorable': continuity['estudioFavorable'], 'estadoRadicacion': 'PREPARACION_PENDIENTE', 'decisionInstitucional': 'PENDIENTE', 'firmaContractual': 'PENDIENTE', 'radicacionHabilitada': False, 'firmaHabilitada': False}
    return projection


def validar_simulacion_propia(value, sid, *, ahora=None):
    """Validate the persisted approximate plan without treating it as a signed offer."""
    from datetime import datetime, timezone
    from decimal import Decimal
    import hashlib
    try:
        fields = {'version', 'solicitudId', 'revisionReferencia', 'versionCondiciones', 'calculadoEn', 'vigenteHasta', 'fuente', 'monto', 'plazoMeses', 'tasaNominalNamv', 'periodicidad', 'cuota', 'seguroMensualPorcentaje', 'baseSeguro', 'unidadRedondeo', 'versionMotor', 'versionParametros', 'planAproximado', 'amortizacion', 'totales', 'autorizaStart', 'firmaContractual', 'aprobacionInstitucional', 'sha256'}
        if type(value) is not dict or set(value) != fields or value['solicitudId'] != str(UUID(sid)) or (value['version'] != 'credihoy-simulacion-aceptable-v1') or (value['fuente'] != 'CREDIHOY_CALCULO_PROPIO') or (str(UUID(value['revisionReferencia'])) != value['revisionReferencia']) or (value['planAproximado'] is not True) or (value['autorizaStart'] is not False) or (value['firmaContractual'] is not False) or (value['aprobacionInstitucional'] is not False) or (value['periodicidad'] != 'MENSUAL') or (type(value['plazoMeses']) is not int) or (not 1 <= value['plazoMeses'] <= 48) or (value['baseSeguro'] != 'SALDO_INICIAL_PERIODO') or (value['unidadRedondeo'] != '1') or (value['versionMotor'] != 'credihoy-plan-decimal-v1') or (type(value['versionParametros']) is not str) or (not value['versionParametros']):
            raise ValueError()
        for k in ('sha256', 'versionCondiciones'):
            if type(value[k]) is not str or not re.fullmatch('[a-f0-9]{64}', value[k]):
                raise ValueError()
        body = {k: v for k, v in value.items() if k != 'sha256'}
        if hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest() != value['sha256']:
            raise ValueError()
        now = ahora or datetime.now(timezone.utc)
        start, end = (datetime.fromisoformat(value[k]) for k in ('calculadoEn', 'vigenteHasta'))
        if start.utcoffset() is None or end.utcoffset() is None or (not start <= now < end) or (not 60 <= (end - start).total_seconds() <= 86400):
            raise ValueError()

        def money(v):
            if type(v) is not str or not re.fullmatch('[0-9]{1,15}(\\.[0-9]{1,8})?', v):
                raise ValueError()
            return Decimal(v)
        amount = money(value['monto'])
        quota = money(value['cuota'])
        if amount <= 0 or quota <= 0 or (not 0 <= money(value['tasaNominalNamv']) <= 100) or (not 0 <= money(value['seguroMensualPorcentaje']) <= 100):
            raise ValueError()
        rows = value['amortizacion']
        totals = {k: Decimal(0) for k in ('capital', 'intereses', 'seguro', 'cuotas')}
        if type(rows) is not list or len(rows) != value['plazoMeses']:
            raise ValueError()
        balance = amount
        for n, row in enumerate(rows, 1):
            if type(row) is not dict or set(row) != {'periodo', 'saldoInicial', 'capital', 'intereses', 'seguro', 'otros', 'cuota', 'saldoFinal'}:
                raise ValueError()
            v = {k: money(row[k]) for k in row if k != 'periodo'}
            if type(row['periodo']) is not int or row['periodo'] != n or v['saldoInicial'] != balance or (v['capital'] <= 0) or (v['otros'] != 0) or (v['saldoFinal'] != balance - v['capital']) or (v['cuota'] != v['capital'] + v['intereses'] + v['seguro']) or (n < len(rows) and v['cuota'] != quota):
                raise ValueError()
            for k in totals:
                totals[k] += v['cuota' if k == 'cuotas' else k]
            balance = v['saldoFinal']
        if balance != 0 or totals['capital'] != amount or type(value['totales']) is not dict or (set(value['totales']) != set(totals)) or any((money(value['totales'][k]) != v for k, v in totals.items())):
            raise ValueError()
        return json.loads(json.dumps(value))
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
        raise ValueError('SIMULACION_PROPIA_NO_VIGENTE_O_INVALIDA') from None
