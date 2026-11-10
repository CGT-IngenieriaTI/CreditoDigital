"""Authenticated test edge, not an alternative public identity endpoint."""
import hmac
from uuid import UUID
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied

MODE = 'ENSAYO_HTTPS_RESTRINGIDO'
SALT = 'credihoy-ensayo-control-navegador-v1'


def activo():
    return getattr(settings, 'CREDIHOY_PUBLICO_MODO', None) == MODE


def validar():
    from .publico import AccesoDenegado
    from .publico_privacidad import plazos
    cfg = getattr(settings, 'CREDIHOY_PUBLICO_ENSAYO', {})
    if (getattr(settings, 'SETTINGS_MODULE', '') != 'core.settings_credihoy_restringido'
            or cfg.get('modo') != MODE or cfg.get('aperturaPublica') is not False
            or not cfg.get('referenciaOperativa') or cfg.get('avisoFormalAprobado') is not False
            or not cfg.get('solicitudes') or not cfg.get('operadores')
            or len(getattr(settings, 'CREDIHOY_PUBLICO_EDGE_SECRET', '')) < 48):
        raise AccesoDenegado
    plazos()


def autorizar_edge(request):
    from .publico import AccesoDenegado
    validar()
    value = request.META.get('HTTP_X_CREDIHOY_ENSAYO', '')
    actor = request.META.get('HTTP_X_CREDIHOY_PROBADOR', '')
    if (not hmac.compare_digest(value, settings.CREDIHOY_PUBLICO_EDGE_SECRET)
            or actor not in settings.CREDIHOY_PUBLICO_ENSAYO['operadores']):
        raise AccesoDenegado


def emitir(request):
    from .publico import navegador
    autorizar_edge(request)
    return signing.dumps({'navegador': navegador(request)}, salt=SALT)


def comprobar(token, request_id):
    from .publico import Acceso
    from .publico_privacidad import plazos
    validar()
    try:
        p = signing.loads(token, salt=SALT, max_age=plazos()['otpSegundos'])
        a = Acceso.objects.get(pk=UUID(request_id))
        return hmac.compare_digest(p['navegador'], a.navegador_hmac)
    except (signing.BadSignature, ValueError, KeyError, TypeError, Acceso.DoesNotExist):
        return False


class AlmacenamientoAisladoRouter:
    """Only local auth/audit state; never read or write institutional cases via ORM."""
    PRIVATE = {('xcore_consumo', name) for name in ('accesopublicocredihoy',
        'limiteaccesocredihoy','eventoaccesocredihoy','operacionverifycredihoy')}
    BUSINESS = {'usuarios', 'solicitudes', 'documentos', 'preselecta', 'historial_pago',
        'xcore', 'decisiones', 'xcore_consumo', 'risk_gateway_ops'}

    def _business(self, model):
        return model._meta.app_label in self.BUSINESS and (model._meta.app_label, model._meta.model_name) not in self.PRIVATE

    def db_for_read(self, model, **hints):
        if self._business(model):
            raise PermissionDenied('EXPEDIENTES_REQUIEREN_API_INSTITUCIONAL')
        return 'default'

    def db_for_write(self, model, **hints):
        if self._business(model):
            raise PermissionDenied('EXPEDIENTES_REQUIEREN_API_INSTITUCIONAL')
        return 'default'

    def allow_relation(self, obj1, obj2, **hints):
        return not self._business(type(obj1)) and not self._business(type(obj2))

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db != 'default':return False
        if app_label == 'xcore_consumo':return (app_label,model_name) in self.PRIVATE
        return app_label in {'auth','contenttypes','sessions'}


class InstitucionalLecturaRouter(AlmacenamientoAisladoRouter):
    def db_for_read(self, model, **hints):
        if self._business(model):
            alias = getattr(settings,'CREDIHOY_SEGUIMIENTO_DB_ALIAS','')
            if not alias or alias not in settings.DATABASES:
                raise PermissionDenied('FUENTE_INSTITUCIONAL_NO_CONFIGURADA')
            return alias
        return getattr(settings,'CREDIHOY_ACCESO_STATE_DB_ALIAS','default')


def comprobar_continuo(token,request_id):
    from .publico import Acceso
    from .publico_privacidad import compensacion_configurada,plazos
    from django.utils import timezone
    if not compensacion_configurada():return False
    try:
        p=signing.loads(token,salt='credihoy-continuo-control-navegador-v1',max_age=plazos()['otpSegundos'])
        a=Acceso.objects.get(pk=UUID(request_id))
        return (set(p)=={'navegador','proposito'} and p['proposito']=='CONTROL_NAVEGADOR_NO_AUTENTICACION'
            and not a.revocado and a.estado=='PROCESANDO' and a.expira_at>timezone.now()
            and hmac.compare_digest(p['navegador'],a.navegador_hmac))
    except (signing.BadSignature,ValueError,KeyError,TypeError,Acceso.DoesNotExist):return False


def emitir_continuo(request):
    from .publico import navegador,AccesoDenegado
    from .publico_privacidad import compensacion_configurada
    if not compensacion_configurada():raise AccesoDenegado
    return signing.dumps({'navegador':navegador(request),'proposito':'CONTROL_NAVEGADOR_NO_AUTENTICACION'},
        salt='credihoy-continuo-control-navegador-v1')
