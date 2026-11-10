"""Restricted 9A window, network boundary and temporary-identity lifecycle."""
import hmac
import re
from ipaddress import ip_address, ip_network
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from apps.xcore_consumo.public_access_models import AccesoPublicoCredihoy as Acceso


class AccesoDenegado(Exception):
    pass


def validar_habilitacion():
    from .publico_privacidad import compensacion_configurada
    restricted=getattr(settings,'CREDIHOY_PUBLICO_RESTRINGIDO',None)
    if type(restricted) is not bool:raise AccesoDenegado
    availability = getattr(settings, 'CREDIHOY_PERFIL_DISPONIBILIDAD', 'PILOT_WINDOW')
    if availability not in ('PILOT_WINDOW', 'PERMANENT_PRIVATE_V1', 'PUBLICO_HTTPS'):raise AccesoDenegado
    if availability == 'PUBLICO_HTTPS':
        if (restricted or getattr(settings,'SETTINGS_MODULE','')!='core.settings_credihoy_publico'
                or getattr(settings,'CREDIHOY_PUBLICO_BORDE_BASIC_EXTERNO',False) is not False
                or getattr(settings,'CREDIHOY_PUBLICO_AUTORIZACION_HASTA',None)!=''
                or getattr(settings,'CREDIHOY_PUBLICO_IDENTIDADES_HMAC',None)!=[]
                or getattr(settings,'CREDIHOY_ADMISION_ENSAYO',{})
                or getattr(settings,'CREDIHOY_IDENTIDAD_GRANTS_ENABLED',False) is not True and not compensacion_configurada()):
            raise AccesoDenegado
    permanent = availability == 'PERMANENT_PRIVATE_V1'
    if permanent:
        modules = {'INSTITUTIONAL_PERMANENT': 'core.settings_credihoy_institucional',
                   'OWNER_PERMANENT': 'core.settings_credihoy_titular'}
        if (not restricted or modules.get(getattr(settings, 'CREDIHOY_PUBLICO_MODO', ''))
                != getattr(settings, 'SETTINGS_MODULE', '')
                or getattr(settings, 'CREDIHOY_PUBLICO_BORDE_BASIC_EXTERNO', False) is not False
                or getattr(settings, 'CREDIHOY_ADMISION_ENSAYO', {})):
            raise AccesoDenegado
    if restricted:
        deadline=parse_datetime(getattr(settings,'CREDIHOY_PUBLICO_AUTORIZACION_HASTA',''))
        if deadline and timezone.is_aware(deadline) and deadline<=timezone.now():
            raise AccesoDenegado('ACCESS_WINDOW_EXPIRED')
    _borde_basic_externo()
    if not restricted or permanent:
        from urllib.parse import urlsplit
        proxies=getattr(settings,'CREDIHOY_PUBLICO_PROXIES',())
        origins=getattr(settings,'CSRF_TRUSTED_ORIGINS',())
        try:
            if ((not permanent and getattr(settings,'CREDIHOY_PUBLICO_MODO',None)!='PUBLICO_HTTPS')
                    or type(proxies) not in (tuple,list) or not proxies
                    or type(origins) not in (tuple,list) or len(origins)!=1):raise ValueError
            for value in proxies:
                network=ip_network(value)
                if network.prefixlen!=network.max_prefixlen:raise ValueError
            origin=urlsplit(origins[0]);origin.port
            if (origin.scheme!='https' or origin.hostname!=getattr(settings,'CREDIHOY_PUBLICO_HOST',None)
                    or origin.username or origin.password or origin.path or origin.query or origin.fragment
                    or availability=='PUBLICO_HTTPS' and origin.port not in (None,443,18449)
                    or permanent and origin.port not in (None,443,18449,18450)):raise ValueError
        except (ValueError,TypeError):raise AccesoDenegado from None
        if not restricted:
            from . import publico_privacidad as privacy
            privacy.validar_aviso();privacy.validar_proteccion_automatizacion()
    if restricted:
        identities = getattr(settings, 'CREDIHOY_PUBLICO_IDENTIDADES_HMAC', ())
        networks = getattr(settings, 'CREDIHOY_PUBLICO_REDES', ())
        proxies = getattr(settings, 'CREDIHOY_PUBLICO_PROXIES', ())
        try:
            deadline = getattr(settings, 'CREDIHOY_PUBLICO_AUTORIZACION_HASTA', '')
            expires = parse_datetime(deadline)
            if expires and timezone.is_aware(expires) and expires<=timezone.now():
                raise AccesoDenegado('ACCESS_WINDOW_EXPIRED')
            valid = (bool(getattr(settings, 'CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA', ''))
                and type(networks) in (tuple, list) and bool(networks)
                and type(proxies) in (tuple, list))
            if permanent:
                valid = valid and deadline == '' and identities == [] and bool(proxies)
            else:
                valid = (valid and expires and timezone.is_aware(expires) and expires > timezone.now()
                    and type(identities) in (tuple, list) and 1 <= len(identities) <= 2
                    and all(type(v) is str and re.fullmatch(r'[a-f0-9]{64}', v) for v in identities))
            if not valid:
                raise ValueError
            for value in (*networks, *proxies):
                network = ip_network(value, strict=True)
                if network.prefixlen == 0 or (permanent and network.prefixlen != network.max_prefixlen):
                    raise ValueError
        except (ValueError, TypeError):
            raise AccesoDenegado from None


def identidad_permitida(identity):
    validar_habilitacion()
    if type(identity) is not str or not re.fullmatch('[a-f0-9]{64}',identity):return False
    # Admission to identity verification is not an authenticated session or ownership.
    if settings.CREDIHOY_PUBLICO_RESTRINGIDO is False:return True
    if getattr(settings, 'CREDIHOY_PERFIL_DISPONIBILIDAD', 'PILOT_WINDOW') == 'PERMANENT_PRIVATE_V1':return True
    if admision_ensayo_vigente():return True
    return any(hmac.compare_digest(identity, candidate)
        for candidate in settings.CREDIHOY_PUBLICO_IDENTIDADES_HMAC)


def ip_cliente(request):
    try:
        peer = ip_address(request.META.get('REMOTE_ADDR', ''))
        trusted = any(peer in ip_network(n) for n in settings.CREDIHOY_PUBLICO_PROXIES)
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        protocol = request.META.get('HTTP_X_FORWARDED_PROTO')
        if request.META.get('HTTP_X_REAL_IP'):
            raise ValueError
        if trusted:
            # The institutional last hop must replace, not append, these headers.
            if not forwarded or ',' in forwarded or protocol != 'https':
                raise ValueError
            client = ip_address(forwarded)
        else:
            if forwarded is not None or protocol is not None:
                raise ValueError
            client = peer
        if getattr(settings, 'CREDIHOY_PERFIL_DISPONIBILIDAD', 'PILOT_WINDOW') == 'PERMANENT_PRIVATE_V1':
            origins = settings.CSRF_TRUSTED_ORIGINS
            if (getattr(settings, 'CREDIHOY_PUBLICO_RESTRINGIDO', None) is not True
                    or not trusted or not request.is_secure()
                    or 'https://' + request.get_host() not in origins
                    or request.META.get('HTTP_ORIGIN') is not None and request.META['HTTP_ORIGIN'] not in origins
                    or 'HTTP_FORWARDED' in request.META
                    or request.META.get('HTTP_X_FORWARDED_HOST') or request.META.get('HTTP_X_FORWARDED_PORT')
                    or request.META.get('HTTP_X_FORWARDED_PREFIX', '') != (getattr(settings, 'FORCE_SCRIPT_NAME', None) or '')):
                raise ValueError
        if getattr(settings,'CREDIHOY_PUBLICO_RESTRINGIDO',None) is False:
            if (not trusted or client.is_unspecified or client.is_multicast
                    or request.META.get('HTTP_FORWARDED')
                    or 'https://'+request.get_host() not in settings.CSRF_TRUSTED_ORIGINS
                    or request.META.get('HTTP_X_FORWARDED_PREFIX','')!=(getattr(settings,'FORCE_SCRIPT_NAME',None) or '')):
                raise ValueError
        elif not any(client in ip_network(n) for n in settings.CREDIHOY_PUBLICO_REDES):
            if (not _borde_basic_externo() or not trusted or not request.is_secure()
                    or client.is_unspecified or client.is_multicast
                    or 'HTTP_FORWARDED' in request.META
                    or 'https://'+request.get_host() not in settings.CSRF_TRUSTED_ORIGINS
                    or request.META.get('HTTP_X_FORWARDED_PREFIX','')!=(getattr(settings,'FORCE_SCRIPT_NAME',None) or '')):
                raise ValueError
            from .publico_restringido import autorizar_edge
            autorizar_edge(request)
        return str(client)
    except (ValueError, TypeError, AttributeError, DisallowedHost):
        raise AccesoDenegado from None


class RedRestringidaMiddleware:
    """Run before SecurityMiddleware interprets a proxy TLS header."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.http import HttpResponse
        from uuid import UUID,uuid4
        from time import monotonic
        import json,logging
        started=monotonic()
        try:rid=str(UUID(request.headers.get('X-Request-ID','')))
        except (ValueError,TypeError,AttributeError):rid=str(uuid4())
        stage='ACCESS_BOUNDARY'
        try:
            stage='ACCESS_WINDOW'
            validar_habilitacion()
            stage='ACCESS_BOUNDARY'
            ip_cliente(request)
        except AccesoDenegado as error:
            response=HttpResponse(status=404, headers={'Cache-Control':'no-store, private','X-Error-Code':error.codigo})
            reason=error.codigo
        else:
            response=self.get_response(request)
            reason='HTTP_REJECTION' if response.status_code>=400 else None
            stage='ACCESS_APPLICATION'
        response['X-Request-ID']=rid
        if reason or (request.method=='POST' and request.path in {
                '/api/v1/credihoy/publico/solicitudes/',
                '/api/interno/credihoy/acceso/seguimiento/',
                '/api/interno/credihoy/solicitudes/titular/'}):
            guardar_traza_saneada({'codigo':reason or 'HTTP_COMPLETED','etapa':stage,
                'correlationId':rid,'httpStatus':response.status_code,'servicio':'CREDIHOY_PUBLIC_ACCESS',
                'duracionMs':round((monotonic()-started)*1000,2)})
        if reason:
            logging.getLogger('credito.publico').warning(json.dumps({'codigo':reason,'etapa':stage,
                'correlationId':rid,'httpStatus':response.status_code,'servicio':'CREDIHOY_PUBLIC_ACCESS',
                'duracionMs':round((monotonic()-started)*1000,2),'motivoTecnico':reason}))
        return response


def revocar(acceso_id, *, actor, referencia):
    from .publico import auditar
    with transaction.atomic():
        a = Acceso.objects.select_for_update().get(pk=acceso_id)
        if a.revocado and not a.datos_cifrados:
            return False
        a.revocado = True
        a.datos_cifrados = ''
        a.save(update_fields=['revocado', 'datos_cifrados', 'updated_at'])
        auditar(a, 'revocado', operador=actor, referencia=referencia)
    return True


def temporales_vencidos():
    now = timezone.now()
    return Acceso.objects.exclude(datos_cifrados='').filter(
        Q(revocado=True) | Q(estado__in=['ERROR', 'BLOQUEADO'])
        | Q(estado='VERIFICADO', sesion_expira_at__lte=now)
        | Q(estado='VERIFICADO', sesion_expira_at__isnull=True)
        | (~Q(estado='VERIFICADO') & Q(expira_at__lte=now)))


def purgar_temporales(*, actor, referencia):
    # Retain access rows, provider references, cases and audit; clear only ciphertext.
    count = 0
    for key in temporales_vencidos().values_list('pk', flat=True).iterator():
        with transaction.atomic():
            a = Acceso.objects.select_for_update().get(pk=key)
            if temporales_vencidos().filter(pk=key).exists():
                revocar(key, actor=actor, referencia=referencia)
                count += 1
    return count


def _borde_basic_externo():
    """Opt-in for the existing Basic-authenticated 18449 edge, not public mode."""
    from . import publico_restringido as edge
    enabled=getattr(settings,'CREDIHOY_PUBLICO_BORDE_BASIC_EXTERNO',False)
    if type(enabled) is not bool:raise AccesoDenegado
    if not enabled:return False
    secret=getattr(settings,'CREDIHOY_PUBLICO_EDGE_SECRET','')
    origins=getattr(settings,'CSRF_TRUSTED_ORIGINS',())
    host=getattr(settings,'CREDIHOY_PUBLICO_HOST',None)
    proxies=getattr(settings,'CREDIHOY_PUBLICO_PROXIES',())
    try:
        if (getattr(settings,'CREDIHOY_PUBLICO_RESTRINGIDO',None) is not True
                or getattr(settings,'SETTINGS_MODULE','')!='core.settings_credihoy_restringido'
                or getattr(settings,'CREDIHOY_PUBLICO_MODO',None)!=edge.MODE
                or type(secret) is not str or len(secret)<48
                or type(host) is not str or not host
                or type(origins) not in (tuple,list) or list(origins)!=['https://'+host+':18449']
                or type(proxies) not in (tuple,list) or not proxies
                or not callable(edge.autorizar_edge)):
            raise ValueError
        for value in proxies:
            network=ip_network(value)
            if network.prefixlen!=network.max_prefixlen:raise ValueError
        edge.validar()
    except (ValueError,TypeError,AttributeError):raise AccesoDenegado from None
    return True


def admision_ensayo_vigente():
    """Temporary admission to verification, never proof of identity or ownership."""
    from datetime import datetime
    config=getattr(settings,'CREDIHOY_ADMISION_ENSAYO',{})
    if not config:return False
    try:
        if (type(config) is not dict or set(config)!={'version','hasta','referencia'}
                or config['version']!='ADMISION_IDENTIDADES_ENSAYO_V1'
                or config['referencia']!='ORDEN_RESPONSABLE_20261103_TODAS_IDENTIFICACIONES'
                or settings.CREDIHOY_PUBLICO_RESTRINGIDO is not True):return False
        deadline=datetime.fromisoformat(config['hasta'])
        window=parse_datetime(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA)
        return bool(window and timezone.is_aware(deadline) and timezone.is_aware(window)
                    and timezone.now()<deadline<=window)
    except (ValueError,TypeError,KeyError,AttributeError):return False


def guardar_traza_saneada(record):
    """Bounded private diagnostics independent of the container logging driver."""
    import json,os,stat,time
    from uuid import UUID
    fd=None
    try:
        import fcntl
        allowed={'codigo','etapa','correlationId','httpStatus','servicio','duracionMs','tipo','sqlstate','puntos'}
        if type(record) is not dict or set(record)-allowed:return False
        clean={}
        for key in ('codigo','etapa','servicio'):
            value=record.get(key)
            if type(value) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,99}',value):return False
            clean[key]=value
        clean['correlationId']=str(UUID(record['correlationId']))
        status=record.get('httpStatus')
        duration=record.get('duracionMs')
        if type(status) is not int or not 100<=status<=599:return False
        if type(duration) not in (int,float) or not 0<=duration<=3600000:return False
        clean.update(httpStatus=status,duracionMs=round(duration,2),observadoEn=round(time.time(),3))
        for key,pattern in (('tipo','[A-Za-z][A-Za-z0-9_]{0,79}'),('sqlstate','[A-Z0-9]{5}')):
            value=record.get(key)
            if value is not None:
                if type(value) is not str or not re.fullmatch(pattern,value):return False
                clean[key]=value
        if 'puntos' in record:
            points=record['puntos']
            if type(points) is not list or len(points)>6:return False
            for point in points:
                if type(point) is not dict or set(point)!={'archivo','linea','funcion'}:return False
                if any(type(point[key]) is not str or not re.fullmatch('[A-Za-z0-9_.<>-]{1,80}',point[key]) for key in ('archivo','funcion')):return False
                if type(point['linea']) is not int or not 1<=point['linea']<=1000000:return False
            clean['puntos']=points
        raw=(json.dumps(clean,separators=(',',':'))+'\n').encode('ascii')
        if len(raw)>2048:return False
        fd=os.open('/tmp/credihoy-rechazos-v1.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:return False
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if os.fstat(fd).st_size+len(raw)>65536:os.ftruncate(fd,0)
        return os.write(fd,raw)==len(raw)
    except (OSError,ImportError,ValueError,TypeError,KeyError,AttributeError):
        return False
    finally:
        if fd is not None:os.close(fd)


def exigir_sesion_permanente(sesion,expira_at):
    from uuid import UUID
    from datetime import datetime,timedelta
    if getattr(settings,'CREDIHOY_PERFIL_DISPONIBILIDAD','')!='PERMANENT_PRIVATE_V1':raise AccesoDenegado
    validar_habilitacion()
    try:
        if type(sesion) is not str or str(UUID(sesion))!=sesion:raise ValueError
        expiry=datetime.fromisoformat(expira_at) if type(expira_at) is str else expira_at
        now=timezone.now()
        if not isinstance(expiry,datetime) or not timezone.is_aware(expiry) or not now<expiry<=now+timedelta(seconds=900):raise ValueError
    except (ValueError,TypeError,AttributeError):raise AccesoDenegado from None

from django.core.exceptions import DisallowedHost
