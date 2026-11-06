"""Private authenticated test deployment. Public-profile gates stay unchanged."""
from .settings_credihoy_publico import *  # noqa: F403

CREDIHOY_PUBLICO_MODO = 'ENSAYO_HTTPS_RESTRINGIDO'
try:
    CREDIHOY_PUBLICO_ENSAYO = cfg['ensayo']
    if (CREDIHOY_PUBLICO_ENSAYO['modo'] != CREDIHOY_PUBLICO_MODO
            or CREDIHOY_PUBLICO_ENSAYO['aperturaPublica'] is not False
            or CREDIHOY_PUBLICO_ENSAYO['avisoFormalAprobado'] is not False
            or not CREDIHOY_PUBLICO_ENSAYO['referenciaOperativa']
            or not 1 <= len(CREDIHOY_PUBLICO_ENSAYO['solicitudes']) <= 2
            or CREDIHOY_PUBLICO_CANALES != ['SMS']):
        raise ValueError
except (KeyError, TypeError, ValueError):
    raise ImproperlyConfigured('Autorizacion de ensayo restringido incompleta') from None
CREDIHOY_PUBLICO_EDGE_SECRET = secreto('CREDIHOY_PUBLICO_EDGE_SECRET')
if len(CREDIHOY_PUBLICO_EDGE_SECRET) < 48:
    raise ImproperlyConfigured('Control privado de entrada insuficiente')
# There is no connection to the Committee pilot DB, even for reading.
# Business data requires a separately authorized institutional HTTP interface.
CREDIHOY_PUBLICO_SOLICITUDES_POR_API = True
CREDIHOY_SEGUIMIENTO_ENABLED = cfg.get('seguimientoHabilitado', False)
if type(CREDIHOY_SEGUIMIENTO_ENABLED) is not bool:
    raise ImproperlyConfigured('Habilitacion de seguimiento invalida')
CREDIHOY_COTIZACION_TITULAR_ENABLED=cfg.get('cotizacionHabilitada',False)
if type(CREDIHOY_COTIZACION_TITULAR_ENABLED) is not bool:
    raise ImproperlyConfigured('Habilitacion de cotizacion invalida')
CREDIHOY_PUBLICO_AUDITORIA_AISLADA = True
MIGRATION_MODULES = {name: None for name in (
    'usuarios','solicitudes','documentos','preselecta','historial_pago','xcore','decisiones','utils','risk_gateway_ops')}
MIGRATION_MODULES['xcore_consumo'] = 'apps.xcore_consumo.migrations_acceso'
DATABASE_ROUTERS = ['apps.xcore_consumo.services.credihoy_v2.unico.publico_restringido.AlmacenamientoAisladoRouter']
# The dedicated single-process service executes only the existing identity task.
# No shared worker or broker is used by this private test deployment.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
SECURE_HSTS_SECONDS = 0  # Do not change the existing hostname's browser policy from a test port.
if CREDIHOY_ACCESO_MANTENIMIENTO:
    INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
        'apps.xcore_consumo.access_app.AccesoAppConfig']
