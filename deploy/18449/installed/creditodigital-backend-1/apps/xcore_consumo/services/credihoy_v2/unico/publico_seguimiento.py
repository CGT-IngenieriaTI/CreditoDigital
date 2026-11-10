"""Session-bound delegation to the data owner; no case DB credentials in 9A."""
from datetime import datetime
import hashlib
import hmac
import logging
import re
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import connections,transaction
from django.http import Http404
from django.utils import timezone

from . import publico_institucional as wire

PATH='/api/interno/credihoy/solicitudes/titular/'
SALT='credihoy-solicitudes-titular-v1'
TTL=30


def key():
    value=getattr(settings,'CREDIHOY_TITULAR_SECRET','')
    if (type(value) is not str or len(value)<48
            or value==getattr(settings,'CREDIHOY_INSTITUCIONAL_SECRET','')):
        raise wire.NoDisponible
    return value


def document_hmac(document):
    return hmac.new(key().encode(),('CC:'+document).encode(),'sha256').hexdigest()


def consult(grant,identity,payload,rid):
    # Called only after live session, browser binding and allowlist checks.
    if (grant.estado!='VERIFICADO' or grant.revocado or not grant.sesion_expira_at
            or grant.sesion_expira_at<=timezone.now()):
        raise wire.NoDisponible
    body=wire.encode(payload)
    editing = 'borrador' in payload
    quoting = 'cotizacion' in payload
    consenting='autorizacion' in payload
    if consenting and getattr(settings,'CREDIHOY_AUTORIZACION_TITULAR_ENABLED',False) is not True:raise wire.NoDisponible
    if editing:
        from .autoservicio import habilitar
        habilitar(str(grant.pk))
    proof=signing.dumps({'scope':'solicitudes:autorizar' if consenting else 'solicitudes:cotizar' if quoting else 'solicitudes:borrador' if editing else 'solicitudes:leer','aud':PATH,'requestId':rid,
        'bodySha256':hashlib.sha256(body).hexdigest(),'accesoId':str(grant.pk),
        'numeroDocumento':identity['numeroDocumento'],
        'sesionExpiraAt':grant.sesion_expira_at.isoformat(),
        **({'identidad': {k: identity[k] for k in ('primerApellido','fechaExpedicion','contacto','canal','celular','correo')
                         if k in identity}} if editing or consenting else {})},key=key(),salt=SALT)
    return wire.request_packet(getattr(settings,'CREDIHOY_TITULAR_URL',''),PATH,body,rid,
        {'Content-Type':'application/json','Accept':'application/json',
         'X-Request-ID':rid,'Authorization':'Bearer '+proof})


def authorize(request):
    if getattr(settings,'CREDIHOY_TITULAR_ENABLED',False) is not True:raise wire.NoDisponible
    rid=request.headers.get('X-Request-ID','')
    auth=request.headers.get('Authorization','')
    body=request.body
    payload=request.data
    if (request.method!='POST' or not request.is_secure() or request.path!=PATH
            or request.content_type!='application/json' or len(body)>4096
            or not auth.startswith('Bearer ') or len(auth)>4096 or str(UUID(rid))!=rid
            or type(payload) is not dict or set(payload) not in ({'accesoId','navegador'},
                {'accesoId','navegador','solicitudId'}, {'accesoId','navegador','borrador'}, {'accesoId','navegador','cotizacion'}, {'accesoId','navegador','autorizacion'})
            or str(UUID(payload['accesoId']))!=payload['accesoId']
            or not re.fullmatch('[a-f0-9]{64}',payload['navegador'])):raise ValueError
    if 'solicitudId' in payload and str(UUID(payload['solicitudId']))!=payload['solicitudId']:raise ValueError
    proof=signing.loads(auth[7:],key=key(),salt=SALT,max_age=TTL,fallback_keys=[])
    editing = 'borrador' in payload
    quoting = 'cotizacion' in payload
    consenting='autorizacion' in payload
    if consenting and getattr(settings,'CREDIHOY_AUTORIZACION_TITULAR_ENABLED',False) is not True:raise wire.NoDisponible
    if (proof['scope']!=('solicitudes:autorizar' if consenting else 'solicitudes:cotizar' if quoting else 'solicitudes:borrador' if editing else 'solicitudes:leer') or proof['aud']!=PATH or proof['requestId']!=rid
            or proof['accesoId']!=payload['accesoId']
            or not hmac.compare_digest(proof['bodySha256'],hashlib.sha256(body).hexdigest())
            or type(proof['numeroDocumento']) is not str
            or not re.fullmatch('[0-9]{6,15}',proof['numeroDocumento'])):raise ValueError
    expires=datetime.fromisoformat(proof['sesionExpiraAt'])
    if not timezone.is_aware(expires) or expires<=timezone.now():raise ValueError
    cohort=getattr(settings,'CREDIHOY_TITULAR_DOCUMENTOS_HMAC',())
    if (not 1<=len(cohort)<=2 or not any(hmac.compare_digest(document_hmac(proof['numeroDocumento']),v)
            for v in cohort)):raise ValueError
    identity={'numeroDocumento':proof['numeroDocumento'],
        '_sesionReferencia':proof['accesoId'],'_sesionExpiraAt':proof['sesionExpiraAt']}
    if not editing and not quoting and not consenting:
        identity.update(_scopeLectura=proof['scope'],_requestId=rid)
    if consenting:identity.update(_scopeAutorizacion=proof['scope'],_requestId=rid)
    if editing:
        from .autoservicio import habilitar
        habilitar(proof['accesoId'])
    if quoting:
        identity.update(_sesionReferencia=proof['accesoId'],_sesionExpiraAt=proof['sesionExpiraAt'])
    if editing or consenting:
        attributes=proof.get('identidad',{})
        if (type(attributes) is not dict or set(attributes)-{'primerApellido','fechaExpedicion','contacto','canal','celular','correo'}
                or any(v is not None and (type(v) is not str or len(v)>254) for v in attributes.values())):raise ValueError
        identity.update(attributes)
        identity['_sesionReferencia']=proof['accesoId']
        identity['_sesionExpiraAt']=proof['sesionExpiraAt']
    return rid,identity


def read_owned(identity,payload):
    if getattr(settings,'CREDIHOY_TITULAR_FUENTE_ENABLED',True) is not True:
        raise wire.NoDisponible
    from .autoservicio import leer_enviadas
    submitted = leer_enviadas(identity,payload)
    if submitted and 'solicitudId' in payload:return submitted
    alias=getattr(settings,'CREDIHOY_SEGUIMIENTO_DB_ALIAS','')
    if (alias not in {'default','historico'} or alias not in settings.DATABASES
            or not getattr(settings,'CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE','')):raise wire.NoDisponible
    if alias=='historico':
        db=settings.DATABASES[alias]
        if (db.get('NAME')!='credihoy_validacion' or db.get('USER')!='credihoy_titular_historico'
                or settings.DATABASES['default'].get('NAME')!='credito_digital'
                or db.get('OPTIONS',{}).get('options')!=
                    '-c default_transaction_read_only=on -c search_path=credihoy_lectura,pg_catalog'):
            raise wire.NoDisponible
    from apps.solicitudes.models import Solicitud
    from .publico_institucional import PRODUCTO
    if alias!='historico':
        from .publico_http import estado_publico
    # This function belongs to the case-owning service only. Its own source
    # credentials must never be installed in the public portal or Verify relay.
    with transaction.atomic(using=alias):
        if connections[alias].vendor=='postgresql':
            with connections[alias].cursor() as cursor:cursor.execute('SET TRANSACTION READ ONLY')
        try:
            config=getattr(settings,'CREDIHOY_PUBLICO_ENSAYO',{})
            if type(config) is not dict:raise ValueError
            configured=config.get('solicitudes',[])
            if (type(configured) is not list or not 1<=len(configured)<=2
                    or any(type(v) is not str or str(UUID(v))!=v for v in configured)
                    or len(set(configured))!=len(configured)):
                raise ValueError
        except (ValueError,TypeError,AttributeError):
            raise wire.NoDisponible from None
        source=Solicitud.objects.using(alias).filter(producto=PRODUCTO,
            metadatos__modo='CONSULTA_REAL_CONTROLADA',pk__in=configured)
        # A missing configured case is a broken source, not an empty owner list.
        if source.count()!=len(configured):raise wire.NoDisponible
        query=source.select_related('solicitante').filter(
            solicitante__tipo_identificacion='CC',solicitante__numero_identificacion=identity['numeroDocumento'],
        )
        if alias=='historico':
            query=query.only('id','solicitante_id','monto_solicitado','plazo_solicitado','estado',
                'created_at','updated_at','solicitante__id','solicitante__tipo_identificacion',
                'solicitante__numero_identificacion')
        if 'solicitudId' in payload:
            query=query.filter(pk=UUID(payload['solicitudId']))
            if not query.exists():raise Http404
        rows=[]
        for s in query.order_by('-created_at')[:20]:
            # Historical views deliberately expose no evaluations, quotes or CD6
            # links. Do not run helpers that implicitly query default here.
            row=({'referencia':str(s.pk),'titulo':'Estamos verificando tu solicitud',
                'monto':str(s.monto_solicitado),'plazoMeses':s.plazo_solicitado,'periodicidad':'Mensual',
                'fechaCreacion':s.created_at.isoformat(),'requisitosCumplidos':None,
                'pendientes':['Comunicate con Congente para revisar el estado.'],
                'siguientePaso':'Espera la revision de Congente.','ofertaDisponible':False,
                'acciones':{'radicar':False,'firmar':False},'institucional':None,'simulacion':None}
                if alias=='historico' else estado_publico(s))
            row['procedencia']={'origen':'HISTORICO_PILOTO' if alias=='historico' else 'FUENTE_OPERATIVA',
                'estadoRegistrado':s.get_estado_display(),'actualizadoAt':s.updated_at.isoformat(),
                'consultadoAt':timezone.now().isoformat()}
            rows.append(wire.project_row(row))
    return (submitted+rows)[:20]


def claim_delegation(rid):
    """One use per signed request, persisted outside the owner's credit source."""
    alias=getattr(settings,'CREDIHOY_TITULAR_NONCE_DB_ALIAS','')
    db=settings.DATABASES.get(alias,{})
    if (not alias or alias=='default' or db.get('NAME')!='credihoy_9a'
            or db.get('USER')!='credihoy_9a_nonce'):raise wire.NoDisponible
    from .publico_controles import limite
    return limite([('titular_delegacion',rid,1)],using=alias)


def audit_read(rid,count):
    logging.getLogger('credito.publico.propietario').info('seguimiento request=%s cantidad=%s',rid,count)
