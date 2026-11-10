"""Encrypted UNLOGGED progress in the owner's operational PostgreSQL, never in 9A."""
import base64
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
import os
import re
from uuid import UUID
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.db import connections, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from apps.solicitudes.models import ProgresoTemporalCredihoy as Progreso
from apps.utils.models import AuditLog
from . import autoservicio as service, progreso_privacidad as privacy, publico_institucional as wire
from .publico_seguimiento import document_hmac

TTL = timedelta(minutes=60)


def alias_operativo(*, limpieza=False, sesion=None, lectura=None, sesion_expira_at=None):
    if lectura is not None:
        if limpieza:raise wire.NoDisponible
        service.habilitar_lectura(lectura)
    elif not limpieza:service.habilitar(sesion,expira_at=sesion_expira_at)
    alias = getattr(settings,'CREDIHOY_AUTOSERVICIO_DB_ALIAS','')
    db = settings.DATABASES.get(alias,{})
    if (alias not in {'default','borradores'} or db.get('NAME') in {'credihoy_9a','credihoy_validacion'}
            or db.get('USER') in {'credihoy_titular_lector','credihoy_titular_historico','credihoy_9a_nonce'}):
        raise wire.NoDisponible
    if lectura is not None and alias=='borradores' and (
            db.get('NAME')!='credito_digital' or db.get('USER')!='credihoy_titular_borrador'):
        raise wire.NoDisponible
    if not limpieza:privacy.configuracion()
    return alias


def clave_cifrado():
    value = getattr(settings,'CREDIHOY_PROGRESO_AES_KEY_B64','')
    try:
        key = base64.b64decode(value,validate=True)
        if len(key)!=32 or value == getattr(settings,'OTP_AES_KEY_B64',None):raise ValueError
        return key
    except (ValueError,TypeError):raise wire.NoDisponible from None


def cifrar(row, data):
    nonce = os.urandom(12)
    aad = (str(row.pk)+':'+row.titular_hmac).encode()
    return base64.b64encode(nonce+AESGCM(clave_cifrado()).encrypt(nonce,wire.encode(data),aad)).decode()


def descifrar(row):
    try:
        raw = base64.b64decode(row.datos_cifrados,validate=True)
        return json.loads(AESGCM(clave_cifrado()).decrypt(raw[:12],raw[12:],(str(row.pk)+':'+row.titular_hmac).encode()))
    except Exception:raise wire.NoDisponible from None


def proteger_conexion(alias):
    if connections[alias].vendor=='postgresql':
        with connections[alias].cursor() as q:
            q.execute('SET LOCAL search_path=pg_catalog,public,pg_temp')


def limpiar(alias=None, *, ahora=None, lote=500):
    alias = alias or alias_operativo(limpieza=True)
    now = ahora or timezone.now()
    if not 1 <= lote <= 5000:raise ValueError('LOTE_INVALIDO')
    with transaction.atomic(using=alias):
        proteger_conexion(alias)
        rows = list(Progreso.objects.using(alias).filter(vence_at__lte=now).order_by('vence_at').values_list('pk',flat=True)[:lote])
        count, _ = Progreso.objects.using(alias).filter(pk__in=rows,vence_at__lte=now).delete()
    return count


def proyectar(row):
    state = descifrar(row)
    return {'referencia':str(row.pk),'fase':'BORRADOR','version':row.version,
        'datos':state['datos'],'promotor':state['promotor']['referencia'] if state['promotor'] else None,
        'actualizadoAt':row.actividad_at.isoformat(),'consultadoAt':timezone.now().isoformat(),'venceAt':row.vence_at.isoformat(),
        'acciones':{'radicar':False,'firmar':False},'solicitudRegistrada':False}


def operar(identity,payload):
    alias = alias_operativo(sesion=identity.get('_sesionReferencia'),sesion_expira_at=identity.get('_sesionExpiraAt'))
    document = identity.get('numeroDocumento')
    try:
        UUID(identity['_sesionReferencia'])
        expires = datetime.fromisoformat(identity['_sesionExpiraAt'])
        if not timezone.is_aware(expires) or expires <= timezone.now():raise ValueError
    except (KeyError,ValueError,TypeError):raise wire.NoDisponible from None
    if type(document) is not str or not re.fullmatch('[0-9]{6,15}',document):raise ValueError('IDENTIDAD_REQUERIDA')
    if type(payload) is not dict or payload.get('operacion')!='borrador' or payload.get('accion') not in {'guardar','retomar','enviar'}:
        raise ValueError('CONTRATO_PROGRESO_INVALIDO')
    reading = payload['accion']=='retomar'
    fields = {'operacion','accion'} if reading else {'operacion','accion','clave','operacionId','version','datos','promotor','aceptacion'}
    if reading and 'actividad' in payload:
        fields.add('actividad')
        if type(payload['actividad']) is not bool:raise ValueError('ACTIVIDAD_EXPLICITA_INVALIDA')
    if set(payload)!=fields:raise ValueError('CONTRATO_PROGRESO_INVALIDO')
    owner = document_hmac(document)
    if not reading:
        key, operation = UUID(payload['clave']), UUID(payload['operacionId'])
        if (str(key)!=payload['clave'] or str(operation)!=payload['operacionId']
                or type(payload['version']) is not int or payload['version']<0
                or type(payload['datos']) is not dict or set(payload['datos'])-{
                    'monto','plazo','email','periodicidadPago','recogerCredihoy','obligacionRecoger'}):
            raise ValueError('CONTRATO_PROGRESO_INVALIDO')
        class Datos(serializers.Serializer):
            monto = serializers.DecimalField(max_digits=12,decimal_places=2,min_value=Decimal('0.01'),required=False)
            plazo = serializers.IntegerField(min_value=1,max_value=48,required=False)
            email = serializers.EmailField(max_length=254,required=False)
            periodicidadPago = serializers.ChoiceField(choices=('MENSUAL',),required=False)
            recogerCredihoy = serializers.BooleanField(required=False)
            obligacionRecoger = serializers.RegexField(r'^[0-9]{1,20}$',required=False)
        if 'recogerCredihoy' in payload['datos'] and type(payload['datos']['recogerCredihoy']) is not bool:
            raise ValueError('RECOGIDA_REQUIERE_ELECCION_BOOLEAN_PERSONAL')
        serializer = Datos(data=payload['datos']);serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        if 'monto' in values:values['monto']=str(values['monto'])
        acceptance = privacy.aceptar(identity,payload['aceptacion'])
        # Presentation tokens may change on reload; idempotency binds the accepted text, not that token.
        canonical = {**payload,'aceptacion':{k:payload['aceptacion'][k] for k in ('version','sha256','acepta')}}
        digest = hmac.new(clave_cifrado(),wire.encode(canonical),'sha256').hexdigest()
    limpiar(alias)
    with transaction.atomic(using=alias):
        proteger_conexion(alias)
        if connections[alias].vendor=='postgresql':
            with connections[alias].cursor() as q:
                q.execute('SELECT pg_catalog.pg_advisory_xact_lock(%s)',[int.from_bytes(bytes.fromhex(owner)[:8],'big',signed=True)])
        now = timezone.now()
        row = Progreso.objects.using(alias).select_for_update().filter(titular_hmac=owner).first()
        if row and row.vence_at<=now:
            row.delete(using=alias);row=None
        if reading:
            if not row:
                submitted = service.enviada(identity,alias)
                return service.proyectar(submitted) if submitted else None
            if payload.get('actividad') is True:
                row.actividad_at=now;row.vence_at=now+TTL
                row.save(using=alias,update_fields=['actividad_at','vence_at'])
            return proyectar(row)
        submitted = service.enviada(identity,alias,key)
        if submitted:
            previous = submitted.metadatos['envio']
            if payload['accion']!='enviar' or previous['operacionId']!=str(operation) or not hmac.compare_digest(previous['peticionHmac'],digest):
                raise ValueError('ENVIO_YA_REGISTRADO')
            service.vincular_consumo_enviada(submitted,alias)
            return service.proyectar(submitted)
        candidate = Progreso.objects.using(alias).filter(pk=key).first()
        if candidate and candidate.titular_hmac!=owner:raise Http404
        if row and row.pk!=key:raise ValueError('PROGRESO_EXISTENTE_RETOMAR')
        if row and row.operacion_id==operation:
            if not hmac.compare_digest(row.operacion_hmac,digest):raise ValueError('OPERACION_PROGRESO_INCOMPATIBLE')
            row.actividad_at=now;row.vence_at=now+TTL
            row.save(using=alias,update_fields=['actividad_at','vence_at'])
            return proyectar(row)
        if payload['version'] != (row.version if row else 0):raise ValueError('VERSION_PROGRESO_INCOMPATIBLE')
        if payload['accion']=='enviar' and not row:raise ValueError('PROGRESO_VENCIDO_O_NO_GUARDADO')
        promoter = service.validar_promotor(payload['promotor'])
        if row:
            state = descifrar(row);state['datos'].update(values)
            if values.get('recogerCredihoy') is False and 'obligacionRecoger' not in values:
                state['datos'].pop('obligacionRecoger',None)
        else:
            row = Progreso(id=key,titular_hmac=owner,version=0)
            state={'datos':dict(values)}
        # The product pays monthly; reject other values above, never infer a divisor.
        state['datos']['periodicidadPago']='MENSUAL'
        first_acceptance = row.version==0 or state.get('aceptacion',{}).get('sha256')!=acceptance['sha256']
        state.update(promotor=promoter,aceptacion=acceptance)
        row.version+=1
        if payload['accion']=='enviar':
            financial=state['datos']
            if (financial.get('recogerCredihoy') is False and 'obligacionRecoger' in financial):
                raise ValueError('NO_RECOGER_CON_REFERENCIA_INCOMPATIBLE')
            s = service.registrar_enviada(identity,alias,key,row.version,state['datos'],promoter,acceptance,operation,digest)
            row.delete(using=alias)
            return service.proyectar(s)
        row.actividad_at=now;row.vence_at=now+TTL
        row.operacion_id=operation;row.operacion_hmac=digest
        row.datos_cifrados=cifrar(row,state)
        row.save(using=alias)
        if first_acceptance:
            AuditLog.objects.using(alias).create(event='credihoy_progreso_aceptado',actor='titular:9A',
                request_id=str(operation),payload={'progresoReferencia':str(row.pk),'titularHmac':owner,
                    'avisoVersion':acceptance['version'],'avisoSha256':acceptance['sha256'],
                    'accion':acceptance['accion'],'aceptadoEn':acceptance['aceptadoEn']})
        return proyectar(row)
