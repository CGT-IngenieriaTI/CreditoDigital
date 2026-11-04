"""Restricted 9A window, network boundary and temporary-identity lifecycle."""
import hmac
import re
from ipaddress import ip_address, ip_network
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.dateparse import parse_datetime
from apps.xcore_consumo.public_access_models import AccesoPublicoCredihoy as Acceso, LimiteAccesoCredihoy as Limite


class AccesoDenegado(Exception):
    pass


def limite(claves, *, using='default'):
    """Shared atomic limits without importing identity or provider services."""
    now = timezone.now().replace(minute=0,second=0,microsecond=0)
    with transaction.atomic(using=using):
        rows=[]
        for label,value,maximum in sorted(claves):
            key=salted_hmac('credihoy-publico:limite:'+label,value,algorithm='sha256').hexdigest()
            Limite.objects.using(using).get_or_create(pk=key,defaults={'ventana':now})
            row=Limite.objects.using(using).select_for_update().get(pk=key)
            if row.ventana!=now: row.ventana=now;row.contador=0
            if row.contador>=maximum:return False
            rows.append(row)
        for row in rows:
            row.contador+=1;row.save(update_fields=['contador','ventana'])
    return True


def validar_habilitacion():
    identities = getattr(settings, 'CREDIHOY_PUBLICO_IDENTIDADES_HMAC', ())
    networks = getattr(settings, 'CREDIHOY_PUBLICO_REDES', ())
    proxies = getattr(settings, 'CREDIHOY_PUBLICO_PROXIES', ())
    try:
        expires = parse_datetime(getattr(settings, 'CREDIHOY_PUBLICO_AUTORIZACION_HASTA', ''))
        valid = (getattr(settings, 'CREDIHOY_PUBLICO_RESTRINGIDO', False) is True
            and bool(getattr(settings, 'CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA', ''))
            and expires and timezone.is_aware(expires) and expires > timezone.now()
            and type(identities) in (tuple, list) and 1 <= len(identities) <= 2
            and all(type(v) is str and re.fullmatch(r'[a-f0-9]{64}', v) for v in identities)
            and type(networks) in (tuple, list) and bool(networks)
            and type(proxies) in (tuple, list))
        if not valid:
            raise ValueError
        # No open-internet authorization through an accidental catch-all CIDR.
        for value in (*networks, *proxies):
            if ip_network(value).prefixlen == 0:
                raise ValueError
    except (ValueError, TypeError):
        raise AccesoDenegado from None


def identidad_permitida(identity):
    validar_habilitacion()
    if type(identity) is not str or not re.fullmatch('[a-f0-9]{64}',identity):return False
    # Admission to identity verification is not an authenticated session or ownership.
    if settings.CREDIHOY_PUBLICO_RESTRINGIDO is False:return True
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
        if not any(client in ip_network(n) for n in settings.CREDIHOY_PUBLICO_REDES):
            raise ValueError
        return str(client)
    except (ValueError, TypeError, AttributeError):
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
