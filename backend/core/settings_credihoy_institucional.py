"""Isolated institutional relay. Credentials are provisioned here, not in 9A web."""
import os

if os.environ.get('CREDIHOY_PERFIL_DISPONIBILIDAD', 'PILOT_WINDOW') == 'PERMANENT_PRIVATE_V1':
    from .settings_credihoy_publico import *  # noqa: F403
    CREDIHOY_SEGUIMIENTO_ENABLED = cfg.get('seguimientoHabilitado', False)
    if type(CREDIHOY_SEGUIMIENTO_ENABLED) is not bool:
        raise ImproperlyConfigured('Habilitacion de seguimiento invalida')
    CREDIHOY_COTIZACION_TITULAR_ENABLED = False
    CREDIHOY_PUBLICO_AUDITORIA_AISLADA = True
else:
    from .settings_credihoy_restringido import *  # noqa: F403

if not cfg.get('institucional') or CREDIHOY_VERIFY_TRANSPORTE != 'RELE_HTTP':
    raise ImproperlyConfigured('Perfil institucional requiere transporte privado configurado')
ROOT_URLCONF = 'core.urls_credihoy_institucional'
CREDIHOY_INSTITUCIONAL_ENABLED = not CREDIHOY_ACCESO_MANTENIMIENTO
CREDIHOY_PUBLICO_ENABLED = False
TWILIO_ACCOUNT_SID = '' if CREDIHOY_ACCESO_MANTENIMIENTO else secreto('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = ''
TWILIO_VERIFY_API_KEY_SID = '' if CREDIHOY_ACCESO_MANTENIMIENTO else secreto('TWILIO_VERIFY_API_KEY_SID')
TWILIO_VERIFY_API_KEY_SECRET = '' if CREDIHOY_ACCESO_MANTENIMIENTO else secreto('TWILIO_VERIFY_API_KEY_SECRET')
CREDIHOY_PUBLICO_REDES = cfg['institucionalRedes']
CREDIHOY_ACCESO_STATE_DB_ALIAS = 'default'
DATABASE_ROUTERS = ['apps.xcore_consumo.services.credihoy_v2.unico.publico_restringido.InstitucionalLecturaRouter']
# A data-owning institutional backend must explicitly configure its approved
# read source. This relay never connects to the Committee pilot's database.
CREDIHOY_SEGUIMIENTO_DB_ALIAS = ''
CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE = ''
CREDIHOY_SEGUIMIENTO_TRANSPORTE = 'HTTP_PROPIETARIO'
CREDIHOY_TITULAR_URL = ''
CREDIHOY_TITULAR_SECRET = ''
if CREDIHOY_AUTOSERVICIO_ENABLED and not cfg.get('titularUrl'):
    raise ImproperlyConfigured('Autoservicio requiere origen propietario configurado')
if CREDIHOY_SEGUIMIENTO_ENABLED or cfg.get('titularUrl'):
    CREDIHOY_TITULAR_URL = cfg.get('titularUrl','')
    target = urlsplit(CREDIHOY_TITULAR_URL)
    if (target.scheme != 'https' or not target.hostname or target.username or target.password
            or target.path not in ('','/') or target.query or target.fragment):
        raise ImproperlyConfigured('Origen propietario HTTPS requerido')
    CREDIHOY_TITULAR_SECRET = secreto('CREDIHOY_TITULAR_SECRET')
    if (len(CREDIHOY_TITULAR_SECRET)<48 or CREDIHOY_TITULAR_SECRET==CREDIHOY_INSTITUCIONAL_SECRET):
        raise ImproperlyConfigured('Credencial de seguimiento independiente requerida')


def configurar_verify_contractual(environ, *, login_sid, centrales_sid):
    """Provision the separate service without enabling sends or changing other purposes."""
    import re
    prefix = 'CREDIHOY_OTP_CONTRACTUAL_'
    sid = environ.get(prefix + 'VERIFY_SID', '')
    if not sid and not any(name.startswith(prefix) for name in environ):
        return ''
    if (any(environ.get(prefix + name, '0') != '0' for name in ('ENABLED', 'ENVIO_ENABLED'))
            or environ.get(prefix + 'AMBIENTE') != 'STAGING'
            or any(type(value) is not str or not re.fullmatch(r'VA[a-fA-F0-9]{32}', value)
                   for value in (sid, login_sid, centrales_sid))
            or sid.lower() in {login_sid.lower(), centrales_sid.lower()}):
        raise ImproperlyConfigured('CONFIGURACION_VERIFY_CONTRACTUAL_INVALIDA')
    return sid


CREDIHOY_OTP_CONTRACTUAL_VERIFY_SID = configurar_verify_contractual(os.environ,
    login_sid=CREDIHOY_PUBLICO_VERIFY_SID, centrales_sid=TWILIO_VERIFY_SID)


CREDIHOY_OTP_CONTRACTUAL_ENABLED = False


CREDIHOY_OTP_CONTRACTUAL_ENVIO_ENABLED = False
