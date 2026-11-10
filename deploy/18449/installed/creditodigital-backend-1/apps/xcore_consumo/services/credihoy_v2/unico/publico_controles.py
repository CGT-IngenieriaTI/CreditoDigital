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
        try:
            validar_habilitacion()
            ip_cliente(request)
        except AccesoDenegado:
            return HttpResponse(status=404, headers={'Cache-Control': 'no-store, private'})
        return self.get_response(request)


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
