"""Perfil del backend propietario existente; sin credenciales del piloto ni Verify.

No activa rutas en procesos ya desplegados. Requiere custodia y rol lector propios.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

os.environ['DJANGO_SKIP_DOTENV'] = '1'
from .settings import *  # noqa: E402,F403


def privado(path):
    try:
        path = Path(path)
        info = path.stat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISREG(info.st_mode)
                or info.st_size > 65536 or (os.name != 'nt' and info.st_mode & 0o027)):
            raise ValueError
        value = path.read_text(encoding='utf-8').strip()
        if not value:
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ImproperlyConfigured('CUSTODIA_PROPIETARIA_NO_DISPONIBLE') from None


def configurar_otp_contractual(environ, databases, *, session_key, delegation_key, login_sid='', centrales_sid=''):
    """Opt-in metadata only; no accounts, provider calls or permission changes."""
    prefix = 'CREDIHOY_OTP_CONTRACTUAL_'
    try:
        flags = [environ.get(prefix + name, '0') for name in ('ENABLED', 'ENVIO_ENABLED')]
        if any(value not in ('0', '1') for value in flags) or flags == ['0', '1']:
            raise ValueError
        if prefix + 'HMAC_KEY' in environ:
            raise ValueError
        result = {'ENABLED': flags[0] == '1', 'ENVIO_ENABLED': flags[1] == '1',
                  'HMAC_KEY': '', 'DB_ALIAS': '', 'VERIFY_SID': ''}
        provisioned = any(environ.get(prefix + name, '') for name in ('VERIFY_SID', 'HMAC_KEY_FILE'))
        if result['ENABLED'] or provisioned:
            key = privado(environ.get(prefix + 'HMAC_KEY_FILE', ''))
            alias = environ.get(prefix + 'DB_ALIAS', 'default')
            sid = environ.get(prefix + 'VERIFY_SID', '')
            if (len(key) < 48 or key in (session_key, delegation_key)
                    or alias not in {'default', 'cotizacion'} or alias not in databases
                    or type(sid) is not str or not re.fullmatch(r'VA[a-fA-F0-9]{32}', sid)
                    or any(type(value) is not str or not re.fullmatch(r'VA[a-fA-F0-9]{32}', value)
                           for value in (login_sid, centrales_sid))
                    or sid.lower() in (login_sid.lower(), centrales_sid.lower())):
                raise ValueError
            result.update(HMAC_KEY=key, DB_ALIAS=alias, VERIFY_SID=sid)
        return result
    except (ValueError, TypeError, KeyError):
        raise ImproperlyConfigured('CONFIGURACION_OTP_CONTRACTUAL_INVALIDA') from None


def configurar_propietario(config):
    """Valida referencias tecnicas; no crea usuarios, bases, endpoints o expedientes."""
    try:
        fields = {'host', 'redes', 'proxies', 'documentosHmac', 'solicitudes', 'fuenteReferencia',
            'fuente', 'nonce', 'secretFile', 'delegacionFile', 'vigenteHasta'}
        enabled = config.get('fuenteHabilitada', True) if type(config) is dict else None
        optional={'fuenteHabilitada','autoservicioHabilitado','autoservicioAlcance','borradores','promotoresCatalogo','historico','progresoTemporal','cotizacion','autorizacionHabilitada','autorizacionSolicitudes','autorizacionCifradoFile','centralesOtpHabilitado','simuladorLocalHabilitado'}
        if type(config.get('simuladorLocalHabilitado',False)) is not bool:raise ValueError
        optional.update({'admisionEnsayo', 'disponibilidad','origen'})
        availability = config.get('disponibilidad', 'PILOT_WINDOW')
        if (availability not in ('PILOT_WINDOW', 'PERMANENT_PRIVATE_V1')
                or availability != os.environ.get('CREDIHOY_PERFIL_DISPONIBILIDAD', 'PILOT_WINDOW')
                or availability == 'PERMANENT_PRIVATE_V1' and config.get('admisionEnsayo')):
            raise ValueError
        if (type(config) is not dict or not fields<=set(config) or set(config)-fields-optional
                or type(enabled) is not bool
                or type(config['fuenteReferencia']) is not str or not config['fuenteReferencia'].strip()
                or not re.fullmatch(r'[A-Za-z0-9.-]+', config['host'])
                or type(config['documentosHmac']) is not list
                or (config['documentosHmac']!=[] if availability=='PERMANENT_PRIVATE_V1' else not 1<=len(config['documentosHmac'])<=2)
                or not all(re.fullmatch(r'[a-f0-9]{64}', v) for v in config['documentosHmac'])
                or type(config['solicitudes']) is not list
                or (not 1 <= len(config['solicitudes']) <= 2 if enabled and 'historico' not in config
                    else config['solicitudes'] != [])
                or len(set(config['solicitudes'])) != len(config['solicitudes'])
                or not all(str(UUID(v)) == v for v in config['solicitudes'])):
            raise ValueError
        until = parse_datetime(config['vigenteHasta'])
        # Expired access stays denied by middleware; offline cleanup must still boot.
        if (config['vigenteHasta'] != '' if availability == 'PERMANENT_PRIVATE_V1'
                else not until or not timezone.is_aware(until)):
            raise ValueError
        for key in ('redes', 'proxies'):
            if not config[key] or not all(ipaddress.ip_network(v, strict=True).prefixlen == 32 for v in config[key]):
                raise ValueError
        databases = {}
        for key, alias in (('fuente', 'default'), ('nonce', 'nonce')):
            db = config[key]
            if (set(db) != {'host', 'puerto', 'nombre', 'usuario', 'passwordFile'}
                    or type(db['puerto']) is not int or not 1 <= db['puerto'] <= 65535
                    or not all(type(db[k]) is str and db[k].strip() for k in ('host','nombre','usuario'))):
                raise ValueError
            if key == 'fuente':
                if db['usuario'] != 'credihoy_titular_lector' or db['nombre'] != 'credito_digital':
                    raise ValueError
            elif db['usuario'] != 'credihoy_9a_nonce' or db['nombre'] != 'credihoy_9a':
                raise ValueError
            databases[alias] = {'ENGINE': 'django.db.backends.postgresql', 'HOST': db['host'],
                'PORT': db['puerto'], 'NAME': db['nombre'], 'USER': db['usuario'],
                'PASSWORD': privado(db['passwordFile']), 'CONN_MAX_AGE': 0,
                'OPTIONS': {'connect_timeout': 5, **({'options': '-c default_transaction_read_only=on'}
                    if key == 'fuente' else {})}}
        writing=config.get('autoservicioHabilitado',False)
        if type(writing) is not bool or (writing and 'borradores' not in config):raise ValueError
        progress=config.get('progresoTemporal')
        if 'progresoTemporal' in config and progress is None:raise ValueError
        if writing and progress is None:raise ValueError
        if progress is not None:
            import base64
            from apps.xcore_consumo.services.credihoy_v2.unico import progreso_privacidad as notice
            if (type(progress) is not dict or set(progress)!={'claveCifradoFile','validacion'}
                    or progress['claveCifradoFile'] in {config['secretFile'],config['delegacionFile'],
                        config['fuente']['passwordFile'],config['nonce']['passwordFile']}):raise ValueError
            if len(base64.b64decode(privado(progress['claveCifradoFile']),validate=True))!=32:raise ValueError
            v=progress['validacion']
            if (type(v) is not dict or v.get('version')!=notice.VERSION or v.get('sha256')!=notice.SHA256
                    or not v.get('consistenciaPoliticaReferencia') or not v.get('respaldoCustodiaReferencia')
                    or type(v.get('limpiezaMaxSegundos')) is not int or not 1<=v['limpiezaMaxSegundos']<=60):raise ValueError
        # Provision and validate the dedicated alias before exposing write actions.
        if 'borradores' in config:
            db=config['borradores'];source=config['fuente']
            if (set(db)!=set(source) or db['usuario']!='credihoy_titular_borrador'
                    or source['nombre']!='credito_digital'
                    or any(db[k]!=source[k] for k in ('host','puerto','nombre'))
                    or db['passwordFile'] in {source['passwordFile'],config['nonce']['passwordFile']}):raise ValueError
            databases['borradores']={'ENGINE':'django.db.backends.postgresql','HOST':db['host'],
                'PORT':db['puerto'],'NAME':db['nombre'],'USER':db['usuario'],'PASSWORD':privado(db['passwordFile']),
                'CONN_MAX_AGE':0,'OPTIONS':{'connect_timeout':5}}
        if 'historico' in config:
            historical=config['historico']; db=historical['conexion']
            cases=historical['solicitudes']
            if (set(historical)!={'conexion','solicitudes','referencia'}
                    or config['fuente']['nombre']!='credito_digital'
                    or type(historical['referencia']) is not str or not historical['referencia'].strip()
                    or type(cases) is not list or not 1<=len(cases)<=2 or len(set(cases))!=len(cases)
                    or any(type(v) is not str or str(UUID(v))!=v for v in cases)
                    or set(db)!={'host','puerto','nombre','usuario','passwordFile'}
                    or db['nombre']!='credihoy_validacion' or db['usuario']!='credihoy_titular_historico'
                    or type(db['host']) is not str or not db['host'].strip()
                    or type(db['puerto']) is not int or not 1<=db['puerto']<=65535
                    or db['passwordFile'] in {config['fuente']['passwordFile'],config['nonce']['passwordFile'],
                        config.get('borradores',{}).get('passwordFile')}):raise ValueError
            # A separate, custodian-provisioned identity sees scoped views only.
            # Never replace default or the operative draft connection.
            databases['historico']={'ENGINE':'django.db.backends.postgresql','HOST':db['host'],
                'PORT':db['puerto'],'NAME':db['nombre'],'USER':db['usuario'],'PASSWORD':privado(db['passwordFile']),
                'CONN_MAX_AGE':0,'OPTIONS':{'connect_timeout':5,
                    'options':'-c default_transaction_read_only=on -c search_path=credihoy_lectura,pg_catalog'}}
        if 'cotizacion' in config:
            quote=config['cotizacion'];source=config['fuente']
            required={'habilitada','conexion','solicitudes','referencia'}
            if (type(quote) is not dict or not required<=set(quote) or set(quote)-required-{'apiData','custodiaEstudioHabilitada','motor'}
                    or type(quote['habilitada']) is not bool or not isinstance(quote['referencia'],str)
                    or not quote['referencia'].strip() or type(quote['solicitudes']) is not list
                    or not (1 if quote['habilitada'] else 0)<=len(quote['solicitudes'])<=2
                    or len(set(quote['solicitudes']))!=len(quote['solicitudes'])
                    or any(type(v) is not str or str(UUID(v))!=v for v in quote['solicitudes'])):raise ValueError
            custody=quote.get('custodiaEstudioHabilitada',False)
            if type(custody) is not bool or custody and not quote['habilitada']:raise ValueError
            db=quote['conexion'];api=quote.get('apiData')
            if (set(db)!=set(source) or db['usuario']!='credihoy_titular_cotizacion'
                    or any(db[k]!=source[k] for k in ('host','puerto','nombre'))
                    or db['passwordFile'] in {source['passwordFile'],config['nonce']['passwordFile'],
                        config.get('borradores',{}).get('passwordFile'),
                        config.get('historico',{}).get('conexion',{}).get('passwordFile'),
                        config['secretFile'],config['delegacionFile']}):raise ValueError
            motor=quote.get('motor','LINIX')
            if motor not in {'LINIX','PROPIO_390_111_CFA'}:raise ValueError
            if quote['habilitada'] and api is None and motor!='PROPIO_390_111_CFA':raise ValueError
            if 'apiData' in quote:
                if type(api) is not dict or set(api)!={'origen','tokenFile','timeout'}:raise ValueError
                origin=urlsplit(api['origen'])
                if (origin.scheme!='https' or not origin.hostname or origin.username or origin.password
                        or origin.path not in ('','/') or origin.query or origin.fragment
                        or type(api['timeout']) is not int or not 1<=api['timeout']<=30
                        or api['tokenFile'] in {db['passwordFile'],source['passwordFile'],config['nonce']['passwordFile'],
                            config['secretFile'],config['delegacionFile']}):raise ValueError
                token=privado(api['tokenFile'])
                if not token or any(v.isspace() for v in token):raise ValueError
            databases['cotizacion']={'ENGINE':'django.db.backends.postgresql','HOST':db['host'],
                'PORT':db['puerto'],'NAME':db['nombre'],'USER':db['usuario'],'PASSWORD':privado(db['passwordFile']),
                'CONN_MAX_AGE':0,'OPTIONS':{'connect_timeout':5}}
        authorization=config.get('autorizacionHabilitada',False)
        centrales_otp=config.get('centralesOtpHabilitado',False)
        if type(centrales_otp) is not bool or centrales_otp and not authorization:raise ValueError
        cases=config.get('autorizacionSolicitudes',[])
        if (type(authorization) is not bool or type(cases) is not list
                or len(cases)>1 or any(type(v) is not str or str(UUID(v))!=v for v in cases)
                or authorization and (len(cases)!=1 or 'cotizacion' not in databases or not enabled
                    or config.get('autorizacionCifradoFile')!='/run/owner/documentos/otp-aes')):
            raise ValueError
        secret, delegation = privado(config['secretFile']), privado(config['delegacionFile'])
        if len(secret) < 50 or len(delegation) < 48 or secret == delegation:
            raise ValueError
        return databases, secret, delegation
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ImproperlyConfigured('CONFIGURACION_PROPIETARIA_INVALIDA') from None


try:
    cfg = json.loads(privado(os.environ.get('CREDIHOY_TITULAR_CONFIG_FILE', '')))
except (ValueError, TypeError):
    raise ImproperlyConfigured('CONFIGURACION_PROPIETARIA_INVALIDA') from None
DATABASES, SECRET_KEY, CREDIHOY_TITULAR_SECRET = configurar_propietario(cfg)
SECRET_KEY_FALLBACKS = []
DEBUG = False
ROOT_URLCONF = 'core.urls_credihoy_titular'
ALLOWED_HOSTS = [cfg['host']]
INSTALLED_APPS = [a for a in INSTALLED_APPS if a != 'django.contrib.admin']
CREDIHOY_TITULAR_ENABLED = True
CREDIHOY_TITULAR_FUENTE_ENABLED = cfg.get('fuenteHabilitada', True)
CREDIHOY_SEGUIMIENTO_ENABLED = False
CREDIHOY_AUTOSERVICIO_ENABLED = cfg.get('autoservicioHabilitado',False)
CREDIHOY_AUTOSERVICIO_ALCANCE = cfg.get('autoservicioAlcance',{})
CREDIHOY_AUTOSERVICIO_DB_ALIAS = 'borradores' if 'borradores' in DATABASES else ''
CREDIHOY_AUTORIZACION_TITULAR_ENABLED=cfg.get('autorizacionHabilitada',False)
CREDIHOY_CENTRALES_OTP_ENABLED=cfg.get('centralesOtpHabilitado',False)
CREDIHOY_AUTORIZACION_DB_ALIAS='cotizacion' if 'cotizacion' in DATABASES else ''
CREDIHOY_AUTORIZACION_SOLICITUDES=cfg.get('autorizacionSolicitudes',[])
if CREDIHOY_AUTORIZACION_TITULAR_ENABLED:
    import base64
    try:
        OTP_AES_KEY_B64=privado(cfg.get('autorizacionCifradoFile',''))
        if len(base64.b64decode(OTP_AES_KEY_B64,validate=True))!=32:raise ValueError
    except (ValueError,TypeError):raise ImproperlyConfigured('CUSTODIA_DOCUMENTAL_PRIVADA_REQUERIDA') from None
CREDIHOY_PROGRESO_VALIDACION = cfg.get('progresoTemporal',{}).get('validacion',{})
CREDIHOY_PROGRESO_AES_KEY_B64 = (privado(cfg['progresoTemporal']['claveCifradoFile'])
    if 'progresoTemporal' in cfg else '')
CREDIHOY_PROMOTORES_CATALOGO = cfg.get('promotoresCatalogo')
CREDIHOY_TITULAR_DOCUMENTOS_HMAC = cfg['documentosHmac']
CREDIHOY_ADMISION_ENSAYO = cfg.get('admisionEnsayo',{})
CREDIHOY_PUBLICO_ENSAYO = {'solicitudes': cfg.get('historico',cfg)['solicitudes']}
CREDIHOY_SEGUIMIENTO_DB_ALIAS = 'historico' if 'historico' in cfg else 'default'
CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE = cfg['historico']['referencia'] if 'historico' in cfg else cfg['fuenteReferencia']
CREDIHOY_TITULAR_NONCE_DB_ALIAS = 'nonce'
_quote=cfg.get('cotizacion',{})
CREDIHOY_COTIZACION_MOTOR=_quote.get('motor','LINIX')
if _quote:
    # Read prior gateway evidence only; no producer, queue or financial transport.
    INSTALLED_APPS=[*INSTALLED_APPS,'apps.integraciones.risk_gateway.operaciones']
CREDIHOY_COTIZACION_TITULAR_ENABLED=CREDIHOY_COTIZACION_ENABLED=_quote.get('habilitada',False)
CREDIHOY_OFERTA_CUSTODIA_ENABLED=_quote.get('custodiaEstudioHabilitada',False)
CREDIHOY_COTIZACION_DB_ALIAS='cotizacion' if 'cotizacion' in DATABASES else ''
CREDIHOY_COTIZACION_SOLICITUDES=_quote.get('solicitudes',[])
CREDIHOY_COTIZACION_SOURCE_REFERENCE=_quote.get('referencia','')
CREDIHOY_TASAS_ENABLED=CREDIHOY_COTIZACION_TITULAR_ENABLED or CREDIHOY_TASAS_ENABLED
API_DATA_CREDIHOY_BASE_URL=_quote.get('apiData',{}).get('origen','')
API_DATA_CREDIHOY_TOKEN=privado(_quote['apiData']['tokenFile']) if _quote.get('apiData') else ''
API_DATA_CREDIHOY_CLIENT_ID=API_DATA_CREDIHOY_CLIENT_SECRET=''
API_DATA_CREDIHOY_TIMEOUT=_quote.get('apiData',{}).get('timeout',15)
DATABASE_ROUTERS=['apps.xcore_consumo.services.credihoy_v2.unico.publico_restringido.CotizacionPropietariaRouter']
CREDIHOY_PUBLICO_REDES = cfg['redes']
CREDIHOY_PUBLICO_PROXIES = cfg['proxies']
CREDIHOY_PUBLICO_RESTRINGIDO = True
CREDIHOY_PUBLICO_IDENTIDADES_HMAC = cfg['documentosHmac']
CREDIHOY_PUBLICO_AUTORIZACION_HASTA = cfg['vigenteHasta']
CREDIHOY_PERFIL_DISPONIBILIDAD = cfg.get('disponibilidad', 'PILOT_WINDOW')
if CREDIHOY_PERFIL_DISPONIBILIDAD == 'PERMANENT_PRIVATE_V1':
    CREDIHOY_PUBLICO_MODO = 'OWNER_PERMANENT'
    CREDIHOY_PUBLICO_HOST = cfg['host']
    _origin=cfg.get('origen','https://'+cfg['host'])
    _parsed=urlsplit(_origin)
    if (_parsed.scheme!='https' or _parsed.hostname!=cfg['host'] or _parsed.port not in (None,443,18450)
            or _parsed.path or _parsed.query or _parsed.fragment or _parsed.username or _parsed.password):
        raise ImproperlyConfigured('ORIGEN_PROPIETARIO_PERMANENTE_INVALIDO')
    CSRF_TRUSTED_ORIGINS = [_origin]
    # Availability admission is separate from the owner's document/case authorization.
    CREDIHOY_PUBLICO_IDENTIDADES_HMAC = []
CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA = cfg['fuenteReferencia']
MIDDLEWARE = ['apps.xcore_consumo.services.credihoy_v2.unico.publico_controles.RedRestringidaMiddleware']
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CORS_ALLOWED_ORIGINS = []
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': (), 'DEFAULT_PERMISSION_CLASSES': (),
    'DEFAULT_THROTTLE_CLASSES': (), 'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PARSER_CLASSES': ['rest_framework.parsers.JSONParser']}
DATA_UPLOAD_MAX_MEMORY_SIZE = 4096
CELERY_BROKER_URL = CELERY_RESULT_BACKEND = None
CELERY_BEAT_SCHEDULE = {}
CREDIT_PIPELINE_ASYNC = XCORE_CONSUMO_ORACLE_ENABLED = CREDIHOY_PUBLICO_ENABLED = False
RISK_GATEWAY_ENABLED = 'N'
TWILIO_ACCOUNT_SID = TWILIO_AUTH_TOKEN = TWILIO_VERIFY_API_KEY_SID = TWILIO_VERIFY_API_KEY_SECRET = ''
EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
LOGGING = {'version': 1, 'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'WARNING'}}

_otp_contractual = configurar_otp_contractual(os.environ, DATABASES,
    session_key=SECRET_KEY, delegation_key=CREDIHOY_TITULAR_SECRET,
    login_sid=globals().get('CREDIHOY_PUBLICO_VERIFY_SID', '') or os.environ.get('CREDIHOY_PUBLICO_VERIFY_SID', '')
        or os.environ.get('CREDIHOY_OTP_CONTRACTUAL_INGRESO_SID_REFERENCIA', ''),
    centrales_sid=globals().get('TWILIO_VERIFY_SID', '') or os.environ.get('TWILIO_VERIFY_SID', '')
        or os.environ.get('CREDIHOY_OTP_CONTRACTUAL_CENTRALES_SID_REFERENCIA', ''))
CREDIHOY_OTP_CONTRACTUAL_ENABLED = _otp_contractual['ENABLED']
CREDIHOY_OTP_CONTRACTUAL_ENVIO_ENABLED = _otp_contractual['ENVIO_ENABLED']
CREDIHOY_OTP_CONTRACTUAL_HMAC_KEY = _otp_contractual['HMAC_KEY']
CREDIHOY_OTP_CONTRACTUAL_DB_ALIAS = _otp_contractual['DB_ALIAS']
CREDIHOY_OTP_CONTRACTUAL_VERIFY_SID = _otp_contractual['VERIFY_SID']
from apps.integraciones.autentic.pdf_criptografia import referencias_registro_sello
try:
    CREDIHOY_AUTENTIC_SELLO_REGISTRO_FILE,CREDIHOY_AUTENTIC_SELLO_AUTORIDAD_FILE=referencias_registro_sello(os.environ)
except ValueError as error:
    raise ImproperlyConfigured(str(error)) from None
