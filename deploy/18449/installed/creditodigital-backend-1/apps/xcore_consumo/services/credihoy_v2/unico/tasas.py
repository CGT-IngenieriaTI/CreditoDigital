"""Versiones y transiciones auditadas. Nunca modifica Oracle ni expedientes."""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from apps.xcore_consumo.tasas_models import (
    PerfilTasasCredihoy, TasaComercialCredihoy, AsignacionTarifaCredihoy, EventoTasasCredihoy,
)

PERMISOS = {'crear_tasa':'administrar', 'crear_asignacion':'administrar',
    'aprobar':'aprobar','activar':'activar','cerrar':'activar'}
PERMISOS_CONSULTA = ('xcore_consumo.view_tasacomercialcredihoy',
    'xcore_consumo.view_asignaciontarifacredihoy', 'xcore_consumo.view_eventotasascredihoy')
MEDIOS = ({'codigo':'999','nombre':'Ventanilla','tipoNomina':'V'},
          {'codigo':'100','nombre':'CONGENTE / Nómina','tipoNomina':'N'})


def hoy():
    return timezone.now().astimezone(ZoneInfo('America/Bogota')).date()


def permiso(usuario, accion):
    return (usuario.is_authenticated and usuario.is_active and usuario.is_staff
        and (usuario.has_perms(PERMISOS_CONSULTA) if accion == 'consultar'
             else usuario.has_perm('xcore_consumo.'+accion+'_tasas_credihoy')))


def fecha(value):
    if value is None:return None
    if not isinstance(value,str) or len(value)!=10:raise ValidationError('FECHA_ISO_REQUERIDA')
    try:return date.fromisoformat(value)
    except ValueError:raise ValidationError('FECHA_ISO_REQUERIDA')


def texto(v):
    if not isinstance(v,str) or not v.strip() or len(v)>240:raise ValidationError('REFERENCIA_REQUERIDA')
    return v.strip()


def estado(obj):
    eventos = list(obj.eventos.all())
    return eventos[-1].accion if eventos else 'CREADA'


def condiciones_aprobadas(obj):
    aprobacion = next((e for e in reversed(list(obj.eventos.all())) if e.accion == 'APROBADA'), None)
    return aprobacion.verificacion.get('ratificacionDocumental') if aprobacion else None


def inicio(obj):
    ratificacion = condiciones_aprobadas(obj)
    return fecha(ratificacion['desde']) if ratificacion else obj.desde


def fin_documental(obj):
    ratificacion = condiciones_aprobadas(obj)
    return fecha(ratificacion['hasta']) if ratificacion else obj.hasta


def vigencia_abierta(obj):
    ratificacion = condiciones_aprobadas(obj)
    return bool(ratificacion and ratificacion['vigenciaAbierta'] is True)


def fin(obj):
    cierre = next((e.cierre_desde for e in reversed(list(obj.eventos.all())) if e.accion=='CERRADA'), None)
    limite = fin_documental(obj)
    return min(limite,cierre) if limite and cierre else cierre or limite


def vigente(obj, corte):
    activada = any(e.accion=='ACTIVADA' for e in obj.eventos.all())
    desde, hasta = inicio(obj), fin(obj)
    return bool(activada and desde and desde <= corte
                and (corte < hasta if hasta else vigencia_abierta(obj)))


def serializar(obj):
    ratificacion = condiciones_aprobadas(obj)
    documental = ratificacion or {'documento':obj.documento,'versionDocumento':obj.version_documento,
        'referencia':obj.referencia,'soporteSha256':obj.soporte_sha256}
    return {'id':obj.pk,'tipo':'TASA' if isinstance(obj,TasaComercialCredihoy) else 'ASIGNACION',
        'producto':obj.perfil.producto,'modalidad':obj.perfil.modalidad,'version':obj.numero_version,
        'linea':obj.linea,'desde':inicio(obj),'hasta':fin_documental(obj),'finEfectivo':fin(obj),'estado':estado(obj),
        'vigenciaAbierta':vigencia_abierta(obj),'ratificacionDocumental':ratificacion,
        'antecedenteBorrador':{'documento':obj.documento,'versionDocumento':obj.version_documento,
            'referencia':obj.referencia,'soporteSha256':obj.soporte_sha256,'desde':obj.desde,'hasta':obj.hasta},
        'vigente':vigente(obj,hoy()),**{k:documental[k] for k in
            ('documento','versionDocumento','referencia','soporteSha256')},'observacion':obj.observacion,
        **({'ea':str(obj.tasa_ea),'namv':str(obj.tasa_namv),'mvPublicada':str(obj.tasa_mv_publicada)}
            if isinstance(obj,TasaComercialCredihoy) else {'medioPago':obj.medio_pago,'tipoNomina':obj.tipo_nomina,
                'alcance':obj.alcance,'lineaOrigen':{'N':'NOMINA','V':'VENTANILLA'}[obj.tipo_nomina]}),
        'eventos':[{'accion':e.accion,'fecha':e.creado_at,'responsable':e.responsable.get_username(),
            'referencia':e.referencia_autorizacion,'cierreDesde':e.cierre_desde} for e in obj.eventos.all()]}


def catalogo(usuario):
    if not any(permiso(usuario,a) for a in ('consultar','administrar','aprobar','activar')):raise PermissionDenied
    return {'versionContrato':'credihoy-tasas-v1','unidad':'PORCENTAJE','finVigencia':'EXCLUSIVO',
        'permisos':{k:permiso(usuario,k) for k in ('administrar','aprobar','activar')},'medios':MEDIOS,
        'tasas':[serializar(v) for v in TasaComercialCredihoy.objects.select_related('perfil').prefetch_related('eventos__responsable')],
        'asignaciones':[serializar(v) for v in AsignacionTarifaCredihoy.objects.select_related('perfil').prefetch_related('eventos__responsable')]}


@transaction.atomic
def administrar(usuario, payload, clave):
    if type(payload) is not dict or payload.get('accion') not in PERMISOS:raise ValidationError('ACCION_INVALIDA')
    accion=payload['accion']
    if not permiso(usuario,PERMISOS[accion]):raise PermissionDenied
    expected={'accion','referenciaAutorizacion'} | ({'datos'} if accion.startswith('crear_') else
        {'tipo','id'} | ({'verificacionSoporte'} if accion=='aprobar' else {'cierreDesde'} if accion=='cerrar' else set()))
    if set(payload)!=expected or (not accion.startswith('crear_') and type(payload['id']) is not int):
        raise ValidationError('CAMPOS_INVALIDOS')
    key=UUID(str(clave))
    digest=hashlib.sha256(json.dumps({'actor':usuario.pk,'payload':payload},sort_keys=True).encode()).hexdigest()
    perfil,_=PerfilTasasCredihoy.objects.get_or_create(producto='390',modalidad='111')
    PerfilTasasCredihoy.objects.select_for_update().get(pk=perfil.pk)
    anterior=EventoTasasCredihoy.objects.filter(idempotency_key=key).first()
    if anterior:
        if anterior.request_sha256!=digest:raise ValidationError('CLAVE_IDEMPOTENTE_INCOMPATIBLE')
        return
    reference=texto(payload.get('referenciaAutorizacion'))
    verification={}
    close=None
    if accion.startswith('crear_'):
        data=payload.get('datos')
        common={'linea','desde','hasta','documento','versionDocumento','referencia','soporteSha256','observacion'}
        extra={'ea','namv','mvPublicada'} if accion=='crear_tasa' else {'medioPago','tipoNomina'}
        if isinstance(data,dict) and accion=='crear_asignacion' and 'alcance' in data:extra.add('alcance')
        if type(data) is not dict or set(data)!=common|extra:raise ValidationError('CAMPOS_INVALIDOS')
        model=TasaComercialCredihoy if accion=='crear_tasa' else AsignacionTarifaCredihoy
        qs=model.objects.filter(perfil=perfil)
        qs=qs.filter(linea=data['linea']) if accion=='crear_tasa' else qs.filter(
            alcance=data.get('alcance','MEDIO'),medio_pago=data['medioPago'],tipo_nomina=data['tipoNomina'])
        num=(qs.aggregate(n=Max('numero_version'))['n'] or 0)+1
        fields=dict(perfil=perfil,numero_version=num,responsable=usuario,linea=data['linea'],
            desde=fecha(data['desde']),hasta=fecha(data['hasta']),documento=texto(data['documento']),
            version_documento=texto(data['versionDocumento']),referencia=texto(data['referencia']),
            soporte_sha256=data['soporteSha256'],observacion=data['observacion'])
        if accion=='crear_tasa':
            for k,dest in (('ea','tasa_ea'),('namv','tasa_namv'),('mvPublicada','tasa_mv_publicada')):
                if not isinstance(data[k],str):raise ValidationError('PORCENTAJE_DECIMAL_EXPLICITO')
                try:v=Decimal(data[k])
                except InvalidOperation:raise ValidationError('PORCENTAJE_INVALIDO')
                if not v.is_finite():raise ValidationError('PORCENTAJE_INVALIDO')
                fields[dest]=v
        else:fields.update(medio_pago=data['medioPago'],tipo_nomina=data['tipoNomina'],alcance=data.get('alcance','MEDIO'))
        obj=model.objects.create(**fields)
        transition='CREADA'
    else:
        model={'TASA':TasaComercialCredihoy,'ASIGNACION':AsignacionTarifaCredihoy}.get(payload.get('tipo'))
        if model is None:raise ValidationError('TIPO_INVALIDO')
        obj=model.objects.select_for_update().get(pk=payload.get('id'),perfil=perfil)
        current=estado(obj)
        if accion=='aprobar':
            if current!='CREADA':raise ValidationError('TRANSICION_INVALIDA')
            verification=payload.get('verificacionSoporte')
            keys={'hash','metadatosVerificados','vigenciaRatificada','aplicabilidadRatificada'}
            if isinstance(obj,AsignacionTarifaCredihoy) and obj.alcance=='TIPO':keys.add('aprobacionFuncionalRatificada')
            ratificacion = verification.get('ratificacionDocumental') if isinstance(verification,dict) else None
            if isinstance(verification,dict) and 'ratificacionDocumental' in verification:keys.add('ratificacionDocumental')
            if (type(verification) is not dict or set(verification)!=keys
                or verification['hash']!=obj.soporte_sha256
                or any(verification[k] is not True for k in keys-{'hash','ratificacionDocumental'})):
                raise ValidationError('SOPORTE_VIGENCIA_Y_APLICABILIDAD_NO_RATIFICADOS')
            if 'ratificacionDocumental' in verification:
                validar_ratificacion(ratificacion)
            elif not obj.desde or not obj.hasta:
                raise ValidationError('SOPORTE_VIGENCIA_Y_APLICABILIDAD_NO_RATIFICADOS')
            transition='APROBADA'
        elif accion=='activar':
            desde, hasta = inicio(obj), fin(obj)
            if current!='APROBADA' or not desde or (hasta<=hoy() if hasta else not vigencia_abierta(obj)):
                raise ValidationError('APROBACION_VIGENCIA_REQUERIDAS')
            others=model.objects.filter(perfil=perfil).exclude(pk=obj.pk).prefetch_related('eventos')
            others=others.filter(linea=obj.linea) if model is TasaComercialCredihoy else others.filter(
                alcance=obj.alcance,medio_pago=obj.medio_pago,tipo_nomina=obj.tipo_nomina)
            for other in others:
                other_start, other_end = inicio(other), fin(other)
                if (any(e.accion=='ACTIVADA' for e in other.eventos.all()) and other_start
                    and (other_end is None or desde<other_end)
                    and (hasta is None or other_start<hasta)):
                    raise ValidationError('VIGENCIAS_SOLAPADAS')
            if isinstance(obj,AsignacionTarifaCredihoy):
                rates=TasaComercialCredihoy.objects.filter(perfil=perfil,linea=obj.linea).prefetch_related('eventos')
                if not any(vigente(r,max(hoy(),desde)) for r in rates):raise ValidationError('TARIFA_ACTIVA_REQUERIDA')
            transition='ACTIVADA'
        else:
            close=fecha(payload.get('cierreDesde'))
            desde, hasta = inicio(obj), fin(obj)
            if current!='ACTIVADA' or not close or close<hoy() or not desde or close<=desde or (hasta and close>hasta):
                raise ValidationError('CIERRE_INVALIDO_NO_REESCRIBIR_PASADO')
            transition='CERRADA'
    EventoTasasCredihoy.objects.create(responsable=usuario,
        tasa=obj if isinstance(obj,TasaComercialCredihoy) else None,
        asignacion=obj if isinstance(obj,AsignacionTarifaCredihoy) else None,accion=transition,
        referencia_autorizacion=reference,verificacion=verification,cierre_desde=close,
        idempotency_key=key,request_sha256=digest)


def validar_ratificacion(data):
    # An approval event may ratify documentary provenance; it never rewrites a draft or its rates.
    keys={'documento','versionDocumento','referencia','soporteSha256','desde','hasta','vigenciaAbierta'}
    if type(data) is not dict or set(data)!=keys:
        raise ValidationError('RATIFICACION_DOCUMENTAL_INVALIDA')
    for k in ('documento','versionDocumento','referencia'):texto(data[k])
    if not isinstance(data['soporteSha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',data['soporteSha256']):
        raise ValidationError('SOPORTE_RATIFICACION_INVALIDO')
    desde, hasta = fecha(data['desde']), fecha(data['hasta'])
    if (type(data['vigenciaAbierta']) is not bool or not desde
        or data['vigenciaAbierta'] != (hasta is None) or (hasta and hasta<=desde)):
        raise ValidationError('VIGENCIA_RATIFICADA_INVALIDA')


def concordancia(medio, tipo, tasa_linix, *, corte=None, contexto_certificado=False):
    absent={'estado':'TARIFA_PENDIENTE_DE_ASIGNACION','linea':None,'tasaComercialNamv':None,
        'tasaLinixNamv':tasa_linix,'diferenciaPuntos':None,'contratoConcordante':False,
        'referenciaComercial':None,'motivos':['ASOCIACION_COMERCIAL_NO_RATIFICADA']}
    if getattr(settings,'CREDIHOY_TASAS_ENABLED',False) is not True:return absent
    corte=corte or hoy()
    qs=AsignacionTarifaCredihoy.objects.filter(perfil__producto='390',perfil__modalidad='111',alcance='MEDIO',medio_pago=medio,tipo_nomina=tipo).prefetch_related('eventos')
    matches=[m for m in qs if vigente(m,corte)]
    if len(matches)!=1:return absent
    rates=TasaComercialCredihoy.objects.filter(perfil=matches[0].perfil,linea=matches[0].linea).prefetch_related('eventos')
    matches_t=[r for r in rates if vigente(r,corte)]
    if len(matches_t)!=1:return {**absent,'estado':'TARIFA_VIGENTE_NO_DISPONIBLE','linea':matches[0].linea,'motivos':['TARIFA_VIGENTE_UNIVOCA_AUSENTE']}
    return comparar_tarifa(absent,matches_t[0],tasa_linix,contexto_certificado)


def comparar_tarifa(absent, r, tasa_linix, contexto_certificado=False):
    contexto_certificado = contexto_certificado is True
    try:
        observed=Decimal(tasa_linix)
        if not observed.is_finite():raise ValueError
    except (InvalidOperation,ValueError,TypeError):observed=None
    equal=observed is not None and observed==r.tasa_namv
    documental=condiciones_aprobadas(r)
    return {**absent,'estado':'CONCORDANTE' if equal and contexto_certificado else 'DISCREPANCIA_TASA' if observed is not None and not equal else 'PENDIENTE_VALIDACION_CONTRACTUAL',
        'linea':r.linea,'tasaComercialNamv':str(r.tasa_namv),'tasaComercialEa':str(r.tasa_ea),
        'referenciaComercial':documental['referencia'] if documental else r.referencia,
        'tarifaId':r.pk,'versionTarifa':r.numero_version,
        'versionDocumentoTarifa':documental['versionDocumento'] if documental else r.version_documento,
        'soporteTarifaSha256':documental['soporteSha256'] if documental else r.soporte_sha256,
        'vigenciaTarifa':{'desde':inicio(r).isoformat(),'hasta':fin(r).isoformat() if fin(r) else None},
        'diferenciaPuntos':str(observed-r.tasa_namv) if observed is not None else None,
        'contratoConcordante':equal and contexto_certificado,
        'motivos':[] if equal and contexto_certificado else ['TASA_LINIX_DIFIERE'] if observed is not None and not equal else ['VALIDAR_ADMISION_LINIX_CUOTA_SEGURO_Y_CALENDARIO']}
