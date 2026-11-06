"""Separate, fail-closed 9A service. Never import the Committee pilot profile."""
import base64
import json
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit
from django.core.exceptions import ImproperlyConfigured
from apps.xcore_consumo.services.credihoy_v2.unico.publico_privacidad import validar_plazos

os.environ['DJANGO_SKIP_DOTENV'] = '1'
from .settings import *  # noqa: E402,F403


def requerido(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured('Falta configuracion: ' + name)
    return value


def secreto(name):
    path = Path(requerido(name + '_FILE'))
    try:
        info = path.stat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISREG(info.st_mode)
                or info.st_size > 65536 or (os.name != 'nt' and info.st_mode & 0o027)):
            raise ValueError
        value = path.read_text(encoding='utf-8').strip()
        if not value:
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError):
        raise ImproperlyConfigured('Archivo privado requerido: ' + name) from None


DEBUG = False
CREDIHOY_PERFIL_DISPONIBILIDAD = os.environ.get('CREDIHOY_PERFIL_DISPONIBILIDAD', 'PILOT_WINDOW')
if CREDIHOY_PERFIL_DISPONIBILIDAD not in ('PILOT_WINDOW', 'PERMANENT_PRIVATE_V1'):
    raise ImproperlyConfigured('Perfil de disponibilidad desconocido')
_privado_permanente = CREDIHOY_PERFIL_DISPONIBILIDAD == 'PERMANENT_PRIVATE_V1'
if _privado_permanente and os.environ.get('DJANGO_SETTINGS_MODULE') != 'core.settings_credihoy_institucional':
    raise ImproperlyConfigured('Disponibilidad privada requiere perfil institucional explicito')
_ensayo_privado = os.environ.get('DJANGO_SETTINGS_MODULE') in {
    'core.settings_credihoy_restringido', 'core.settings_credihoy_institucional'}
CREDIHOY_ACCESO_MANTENIMIENTO = _ensayo_privado and os.environ.get('CREDIHOY_PUBLICO_ENABLED') != '1'
SECRET_KEY = secreto('CREDIHOY_PUBLICO_SECRET_KEY')
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 10:
    raise ImproperlyConfigured('Clave de sesion 9A insuficiente')
SECRET_KEY_FALLBACKS = []
try:
    cfg = json.loads(secreto('CREDIHOY_PUBLICO_CONFIG'))
    origin = cfg['origen']
    url = urlsplit(origin)
    if (url.scheme != 'https' or not url.hostname or url.hostname == '*'
            or url.username or url.password or url.path or url.query or url.fragment
            or any(c.isspace() for c in origin)):
        raise ValueError
    url.port  # Reject malformed port declarations.
    public_prefix=cfg.get('prefijoPublico','')
    if public_prefix not in ('','/credihoy'):raise ValueError
    CREDIHOY_PUBLICO_HOST = url.hostname
    CREDIHOY_PUBLICO_AVISO_VERSION = cfg['avisoVersion']
    CREDIHOY_PUBLICO_AVISO_TEXTO = cfg['avisoTexto']
    CREDIHOY_PUBLICO_BOT_SITEKEY = cfg.get('botSitekey', '') if _ensayo_privado else cfg['botSitekey']
    CREDIHOY_PUBLICO_VERIFY_SID = cfg['verifySid']
    TWILIO_VERIFY_SID = cfg['verifyConsentimientoSid']
    CREDIHOY_PUBLICO_CANALES = cfg['canales']
    CREDIHOY_PUBLICO_RETENCION_DIAS = cfg['retencionDias']
    CREDIHOY_PUBLICO_IDENTIDADES_HMAC = cfg['identidadesHmac']
    CREDIHOY_ADMISION_ENSAYO = cfg.get('admisionEnsayo',{})
    CREDIHOY_PUBLICO_REDES = cfg['redesAutorizadas']
    CREDIHOY_PUBLICO_PROXIES = cfg['proxiesConfiables']
    CREDIHOY_PUBLICO_AUTORIZACION_HASTA = cfg['autorizacionHasta']
    CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA = cfg['autorizacionReferencia']
    CREDIHOY_PUBLICO_VALIDACION = cfg['validacionInstitucional']
    CREDIHOY_AUTOSERVICIO_ENABLED = cfg.get('autoservicioHabilitado', False)
    CREDIHOY_AUTOSERVICIO_ALCANCE = cfg.get('autoservicioAlcance', {})
    CREDIHOY_AUTORIZACION_TITULAR_ENABLED = cfg.get('autorizacionHabilitada',False)
    if type(CREDIHOY_AUTORIZACION_TITULAR_ENABLED) is not bool:raise ValueError
    CREDIHOY_CENTRALES_OTP_ENABLED = cfg.get('centralesOtpHabilitado',False)
    if (type(CREDIHOY_CENTRALES_OTP_ENABLED) is not bool
            or CREDIHOY_CENTRALES_OTP_ENABLED and not CREDIHOY_AUTORIZACION_TITULAR_ENABLED):raise ValueError
    if type(CREDIHOY_AUTOSERVICIO_ENABLED) is not bool:
        raise ValueError
    access_times = validar_plazos(CREDIHOY_PUBLICO_VALIDACION)
    if _privado_permanente:
        if (cfg.get('disponibilidad') != CREDIHOY_PERFIL_DISPONIBILIDAD or cfg.get('ensayo')
                or CREDIHOY_ADMISION_ENSAYO or CREDIHOY_PUBLICO_IDENTIDADES_HMAC != []
                or CREDIHOY_PUBLICO_AUTORIZACION_HASTA != ''):
            raise ValueError
    elif _ensayo_privado:
        ensayo = cfg['ensayo']
        if (ensayo['modo'] != 'ENSAYO_HTTPS_RESTRINGIDO' or ensayo['aperturaPublica'] is not False
                or ensayo['avisoFormalAprobado'] is not False or not ensayo['referenciaOperativa']):
            raise ValueError
    if (type(CREDIHOY_PUBLICO_RETENCION_DIAS) is not int
            or not 1 <= CREDIHOY_PUBLICO_RETENCION_DIAS <= 3650
            or type(CREDIHOY_PUBLICO_CANALES) is not list or not CREDIHOY_PUBLICO_CANALES
            or not set(CREDIHOY_PUBLICO_CANALES) <= {'SMS', 'EMAIL'}
            or any(type(v) is not str or not v.strip() for v in (CREDIHOY_PUBLICO_AVISO_VERSION,
                CREDIHOY_PUBLICO_AVISO_TEXTO, CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA))
            or (not _ensayo_privado and (type(CREDIHOY_PUBLICO_BOT_SITEKEY) is not str
                or not CREDIHOY_PUBLICO_BOT_SITEKEY.strip()))):
        raise ValueError
except (KeyError, ValueError, TypeError):
    raise ImproperlyConfigured('Configuracion institucional 9A incompleta') from None

CREDIHOY_PUBLICO_ENABLED = os.environ.get('CREDIHOY_PUBLICO_ENABLED') == '1'
CREDIHOY_PUBLICO_RESTRINGIDO = _ensayo_privado
CREDIHOY_PUBLICO_MODO = 'ENSAYO_HTTPS_RESTRINGIDO' if _ensayo_privado else 'PUBLICO_HTTPS'
if _privado_permanente:
    CREDIHOY_PUBLICO_MODO = 'INSTITUTIONAL_PERMANENT'
CREDIHOY_PUBLICO_BOT_SECRET = '' if _ensayo_privado else secreto('CREDIHOY_PUBLICO_BOT_SECRET')
OTP_AES_KEY_B64 = secreto('CREDIHOY_PUBLICO_OTP_AES_KEY_B64')
try:
    if len(base64.b64decode(OTP_AES_KEY_B64, validate=True)) != 32:
        raise ValueError
except ValueError:
    raise ImproperlyConfigured('Clave de cifrado 9A invalida') from None
OTP_PROVIDER_MODE = 'real'
CREDIHOY_VERIFY_TRANSPORTE = cfg.get('verifyTransporte', 'SDK')
if CREDIHOY_VERIFY_TRANSPORTE == 'SDK':
    TWILIO_ACCOUNT_SID = secreto('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = secreto('TWILIO_AUTH_TOKEN')
elif CREDIHOY_VERIFY_TRANSPORTE == 'RELE_HTTP':
    TWILIO_ACCOUNT_SID = TWILIO_AUTH_TOKEN = ''
else:
    raise ImproperlyConfigured('Transporte Verify no permitido')
if CREDIHOY_AUTOSERVICIO_ENABLED and CREDIHOY_VERIFY_TRANSPORTE != 'RELE_HTTP':
    raise ImproperlyConfigured('Autoservicio requiere el rele institucional existente')
if CREDIHOY_VERIFY_TRANSPORTE == 'RELE_HTTP' or cfg.get('institucional'):
    CREDIHOY_INSTITUCIONAL_SECRET = secreto('CREDIHOY_INSTITUCIONAL_SECRET')
    if len(CREDIHOY_INSTITUCIONAL_SECRET) < 48:
        raise ImproperlyConfigured('Credencial institucional insuficiente')
    CREDIHOY_INSTITUCIONAL_URL = cfg.get('institucionalUrl', '')
API_DATA_CREDIHOY_BASE_URL = requerido('API_DATA_CREDIHOY_BASE_URL')
API_DATA_CREDIHOY_CLIENT_ID = '' if CREDIHOY_ACCESO_MANTENIMIENTO else secreto('API_DATA_CREDIHOY_CLIENT_ID')
API_DATA_CREDIHOY_CLIENT_SECRET = '' if CREDIHOY_ACCESO_MANTENIMIENTO else secreto('API_DATA_CREDIHOY_CLIENT_SECRET')
API_DATA_CREDIHOY_TOKEN = ''
CREDIHOY_IDENTIDAD_GRANTS_ENABLED = os.environ.get('CREDIHOY_IDENTIDAD_GRANTS_ENABLED', '0') == '1'
if os.environ.get('CREDIHOY_IDENTIDAD_GRANTS_ENABLED', '0') not in ('0', '1'):
    raise ImproperlyConfigured('Habilitacion de grants invalida')
if CREDIHOY_IDENTIDAD_GRANTS_ENABLED:
    CREDIHOY_IDENTIDAD_CREDENTIAL_FILE = requerido('CREDIHOY_IDENTIDAD_CREDENTIAL_FILE')
    if not Path(CREDIHOY_IDENTIDAD_CREDENTIAL_FILE).is_absolute():
        raise ImproperlyConfigured('Politica identidad requiere referencia absoluta')
if urlsplit(API_DATA_CREDIHOY_BASE_URL).scheme != 'https':
    raise ImproperlyConfigured('API_DATA requiere HTTPS verificado')
DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql',
    'NAME': requerido('POSTGRES_DB'), 'USER': requerido('POSTGRES_USER'),
    'PASSWORD': secreto('POSTGRES_PASSWORD'), 'HOST': requerido('POSTGRES_HOST'),
    'PORT': requerido('POSTGRES_PORT'), 'CONN_MAX_AGE': 60,
    'CONN_HEALTH_CHECKS': True, 'OPTIONS': {'connect_timeout': 5}}}
INSTALLED_APPS = [a for a in INSTALLED_APPS if a != 'django.contrib.admin']
INSTALLED_APPS += ['apps.integraciones.risk_gateway.operaciones']
ROOT_URLCONF = 'core.urls_credihoy_publico'
ALLOWED_HOSTS = [url.hostname]
CSRF_TRUSTED_ORIGINS = [origin]
CORS_ALLOWED_ORIGINS = []
CORS_ALLOW_CREDENTIALS = False
MIDDLEWARE = ['apps.xcore_consumo.services.credihoy_v2.unico.publico_controles.RedRestringidaMiddleware',
    *[m for m in MIDDLEWARE if m != 'corsheaders.middleware.CorsMiddleware']]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_NAME = 'credihoy_publico_session'
CSRF_COOKIE_NAME = 'credihoy_publico_csrf'
SESSION_COOKIE_DOMAIN = CSRF_COOKIE_DOMAIN = None
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = public_prefix+'/'
FORCE_SCRIPT_NAME = public_prefix or None
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = CSRF_COOKIE_SAMESITE = 'Strict'
SESSION_COOKIE_AGE = access_times['sesionSegundos']
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'no-referrer'
DATA_UPLOAD_MAX_MEMORY_SIZE = 16384
REST_FRAMEWORK = {**REST_FRAMEWORK, 'DEFAULT_AUTHENTICATION_CLASSES': (),
    'DEFAULT_THROTTLE_CLASSES': (), 'DEFAULT_PERMISSION_CLASSES': ()}
CELERY_BROKER_URL = None if _ensayo_privado else secreto('CREDIHOY_PUBLICO_CELERY_BROKER_URL')
CELERY_TASK_DEFAULT_QUEUE = 'credihoy_publico'
CELERY_TASK_ROUTES = {name: {'queue': 'credihoy_publico'} for name in (
    'credihoy.preparar_acceso_publico', 'credihoy.limpiar_accesos_publicos')}
CELERY_BEAT_SCHEDULE = {'limpieza-identidad-9a': {'task': 'credihoy.limpiar_accesos_publicos',
    'schedule': float(access_times['limpiezaTemporalMaxSegundos']),
    'options': {'queue': 'credihoy_publico', 'expires': access_times['limpiezaTemporalMaxSegundos']}}}
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SEND_SENT_EVENT = CELERY_WORKER_SEND_TASK_EVENTS = False
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = False
LOGGING = {'version': 1, 'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {name: {'handlers': ['console'], 'level': level, 'propagate': False}
        for name, level in [('django.request', 'WARNING'), ('twilio', 'CRITICAL'),
            ('celery', 'WARNING'), ('credito.publico', 'WARNING')]}}
# Neither the HTTP service nor its dedicated queue exposes credit actions.
CREDIHOY_CONSENTIMIENTO_REAL_ENABLED = CREDIHOY_FUNCIONAL_ENABLED = False
CREDIHOY_CONSUMO_CONTROLADO_ENABLED = CREDIHOY_COTIZACION_ENABLED = False
CREDIHOY_SIMULADOR_DINAMICO_ENABLED = CREDIHOY_REFERENCIAS_CAPTURE_ENABLED = False
CREDIHOY_PRECARGA_ASISTIDA_ENABLED = CREDIHOY_NUEVO_TITULAR_ENABLED = False
CREDIHOY_UNICO_SHADOW_LAB_ENABLED = CREDIHOY_UNICO_HISTORICO_ENABLED = False
RISK_GATEWAY_ORCHESTRATION_ENABLED = RISK_GATEWAY_CONSUMO_ENABLED = False
RISK_GATEWAY_ENABLED = 'N'
XCORE_CONSUMO_ORACLE_ENABLED = CREDIT_PIPELINE_ASYNC = CREDIT_USE_MOCK_SERVICES = False
EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'

# Deployment metadata only. Credential contents are loaded by the existing task.
_recuperacion = os.environ.get('CREDIHOY_AUTENTIC_RECUPERACION_ENABLED', '0')
if _recuperacion not in ('0', '1'):
    raise ImproperlyConfigured('Habilitacion de recuperacion Autentic invalida')
CREDIHOY_AUTENTIC_RECUPERACION_ENABLED = _recuperacion == '1'
if CREDIHOY_AUTENTIC_RECUPERACION_ENABLED:
    try:
        CREDIHOY_AUTENTIC_AMBIENTE = requerido('CREDIHOY_AUTENTIC_AMBIENTE')
        CREDIHOY_AUTENTIC_CONFIG_FILE = requerido('CREDIHOY_AUTENTIC_CONFIG_FILE')
        _hosts_recuperacion = json.loads(requerido('CREDIHOY_AUTENTIC_HOSTS_DESCARGA'))
        if type(_hosts_recuperacion) is not list or any(type(host) is not str for host in _hosts_recuperacion):
            raise ValueError
        CREDIHOY_AUTENTIC_HOSTS_DESCARGA = tuple(_hosts_recuperacion)
        CREDIHOY_AUTENTIC_OPERADOR = requerido('CREDIHOY_AUTENTIC_OPERADOR')
        CREDIHOY_AUTENTIC_REFERENCIA = requerido('CREDIHOY_AUTENTIC_REFERENCIA')
        CREDIHOY_AUTENTIC_RECUPERACION_LOTE = int(os.environ.get('CREDIHOY_AUTENTIC_RECUPERACION_LOTE', '10'))
        CREDIHOY_AUTENTIC_RECUPERACION_INTERVALO = int(os.environ.get('CREDIHOY_AUTENTIC_RECUPERACION_INTERVALO', '60'))
        if (CREDIHOY_AUTENTIC_AMBIENTE not in ('STAGING', 'PRODUCCION')
                or not Path(CREDIHOY_AUTENTIC_CONFIG_FILE).is_absolute()
                or not CREDIHOY_AUTENTIC_HOSTS_DESCARGA
                or not 1 <= CREDIHOY_AUTENTIC_RECUPERACION_LOTE <= 20
                or not 30 <= CREDIHOY_AUTENTIC_RECUPERACION_INTERVALO <= 3600):
            raise ValueError
    except (ValueError, TypeError):
        raise ImproperlyConfigured('Metadatos de recuperacion Autentic invalidos') from None
