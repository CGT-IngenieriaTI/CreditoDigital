"""Explicit quote only. No scoring, provider refresh, financial formula or radication."""
















import hashlib
import json
from uuid import UUID,uuid4
from datetime import datetime
from django.db import transaction
from django.db import connection,connections
from django.utils import timezone
from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
from apps.xcore_consumo.models import SolicitudConsumo
from apps.utils.models import AuditLog
from ..cliente import ConfiguracionApiDataV2
from ..errores_http import ApiDataCrediHoyV2Error
from ..cotizacion_cliente import ApiDataCotizacionClient, VERSION
from ..pagaduria import adaptar_pagaduria
from .condicion_comercial import resolver_condicion, pagaduria_seleccion
from .publico_restringido import alias_cotizacion,fuente_cotizacion

KEY = 'credihoy_cotizacion_institucional'
KEY_MANUAL = KEY + '_manual'
KEY_DYNAMIC = KEY + '_escenarios_v1'
KEY_CALENDAR = 'credihoy_calendario_pago_v1'
MOTIVOS = {'COTIZACION_RECOGIDA_NO_CERTIFICADA', 'SELECCION_COMERCIAL_REQUERIDA',
    'EVIDENCIA_COMERCIAL_INVALIDA', 'CONDICIONES_COMERCIALES_NO_VIGENTES',
    'CONSENTIMIENTO_FIRMADO_VERIFICABLE_REQUERIDO', 'DATOS_SOLICITUD_INVALIDOS',
    'PARAMETROS_DISTINTOS_DEL_EXPEDIENTE_EXISTENTE'}


def _obtener(sid, operador, *, lock=False, titular=None, autogestion_tecnica=False):
    if type(autogestion_tecnica) is not bool:
        raise ValueError('COTIZACION_MODO_INVALIDO')
    if autogestion_tecnica:
        if titular is not None or operador is None:
            raise ValueError('NO_MEZCLAR_ACTOR_Y_TITULAR')
        from .funcional import obtener_para_evaluacion
        from .elegibilidad import leer_decision_estudio, continuidad_desde_decision
        case = obtener_para_evaluacion(sid, operador, autogestion=True, revision_conservada=True, lock=lock)
        decision = leer_decision_estudio(case)
        if decision is None or decision['resultado']['estado'] not in {'APROBADO', 'REQUIERE_CONSEJO_ADMINISTRACION', 'REQUIERE_COMITE_INSTITUCIONAL_CREDITO'} or (not continuidad_desde_decision(decision['resultado'])['estudioFavorable']):
            raise ValueError('COTIZACION_TECNICA_REQUIERE_ESTUDIO_FAVORABLE')
        return case
    if titular is None:
        from .consentimiento_real import obtener
        return obtener(sid, operador, lock=lock)
    from django.conf import settings
    from django.http import Http404
    from . import publico_institucional as wire
    from apps.solicitudes.models import Solicitud
    if operador is not None or type(titular) is not dict or getattr(settings, 'CREDIHOY_COTIZACION_TITULAR_ENABLED', False) is not True or (getattr(settings, 'CREDIHOY_COTIZACION_ENABLED', False) is not True) or (alias_cotizacion() == 'default' and (getattr(settings, 'CREDIHOY_TITULAR_FUENTE_ENABLED', True) is not True or getattr(settings, 'CREDIHOY_SEGUIMIENTO_DB_ALIAS', '') != 'default' or (not getattr(settings, 'CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE', '')))) or (alias_cotizacion() != 'default' and (not getattr(settings, 'CREDIHOY_COTIZACION_SOURCE_REFERENCE', ''))):
        raise wire.NoDisponible
    expires = datetime.fromisoformat(titular['_sesionExpiraAt'])
    if expires.utcoffset() is None or expires <= timezone.now() or str(UUID(titular['_sesionReferencia'])) != titular['_sesionReferencia']:
        raise wire.NoDisponible
    permitted = getattr(settings, 'CREDIHOY_COTIZACION_SOLICITUDES', []) if alias_cotizacion() != 'default' else getattr(settings, 'CREDIHOY_PUBLICO_ENSAYO', {}).get('solicitudes', [])
    if str(sid) not in permitted:
        raise Http404
    query = Solicitud.objects.select_related('solicitante')
    try:
        s = query.get(pk=sid, producto=wire.PRODUCTO, metadatos__modo__in=['CONSULTA_REAL_CONTROLADA', 'AUTOGESTION_ENVIADA'], solicitante__tipo_identificacion='CC', solicitante__numero_identificacion=titular['numeroDocumento'])
    except Solicitud.DoesNotExist:
        raise Http404 from None
    if getattr(s, 'metadatos', {}).get('modo') == 'AUTOGESTION_ENVIADA':
        from .autoservicio import validar_vinculo_consumo
        try:
            validar_vinculo_consumo(s)
        except ValueError:
            raise Http404 from None
    if expires <= timezone.now():
        raise wire.NoDisponible
    return s


def _actor(operador,titular):
    return 'titular:'+titular['_sesionReferencia'] if titular is not None else str(operador.pk)


def _evaluacion_huella(s):
    from apps.xcore_consumo.models import EvaluacionConsumo
    if s.metadatos.get('modo') == 'AUTOGESTION_ENVIADA':
        from .funcional import leer_estado_funcional
        current = leer_estado_funcional(s)
        return hashlib.sha256(json.dumps({'resultado': current['resultado'], 'revision': current.get('revisionEstudio')}, sort_keys=True, default=str).encode()).hexdigest()
    rows = list(EvaluacionConsumo.objects.filter(solicitud=s).order_by('pk').values())
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def _calendario_guardado(detail, medio):
    from datetime import date
    from .condiciones_producto import preparar_calendario
    entry=detail.orchestration_data.get(KEY_CALENDAR,{})
    if not entry.get('base') or entry.get('diaPago') is None:return None
    base=entry['base']
    body={k:v for k,v in base.items() if k!='sha256'}
    if (base['sha256']!=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
            or body['solicitudId']!=str(detail.solicitud_id)):
        raise ValueError('BASE_CALENDARIO_NO_ACREDITADA')
    return preparar_calendario(date.fromisoformat(body['hecho']['valor']),entry['diaPago'],
        medio_pago=medio,tipo_base=body['tipo'],referencia_base=body['hecho']['referencia_evidencia'])


def registrar_base_calendario(solicitud_id,operador,hecho,*,tipo='PROYECTADO',autogestion=False):
    """Internal existing-owner preparation; never accepts a browser date/month."""
    from datetime import date
    from ..base import Hecho,EstadoCertificacion as EC
    now=timezone.now()
    if (tipo not in {'PROYECTADO','EFECTIVO'} or not isinstance(hecho,Hecho)
            or type(hecho.valor) is not date or not hecho.disponible or hecho.estado_certificacion!=EC.CERTIFICADO
            or not all((hecho.fuente,hecho.ruta_origen,hecho.referencia_evidencia))
            or hecho.fecha_corte!=timezone.localdate() or hecho.fecha_consulta is None
            or hecho.fecha_consulta.utcoffset() is None or not 0<=(now-hecho.fecha_consulta).total_seconds()<=300
            or (tipo=='PROYECTADO' and hecho.valor<timezone.localdate())
            or (tipo=='EFECTIVO' and hecho.valor>timezone.localdate())):
        raise ValueError('BASE_CALENDARIO_NO_ACREDITADA')
    with transaction.atomic(using=alias_cotizacion()):
        if autogestion:
            from .funcional import obtener_para_evaluacion,_firma
            s=obtener_para_evaluacion(solicitud_id,operador,autogestion=True,lock=True)
            _firma(s)
        else:s=_obtener(solicitud_id,operador,lock=True)
        detail=SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        data=detail.orchestration_data;entry=data.get(KEY_CALENDAR,{})
        base={'solicitudId':str(s.pk),'tipo':tipo,'hecho':hecho.a_dict()}
        base['sha256']=hashlib.sha256(json.dumps(base,sort_keys=True).encode()).hexdigest()
        if entry.get('base')==base:return base
        detail.orchestration_data={**data,KEY_CALENDAR:{**entry,'base':base},
            KEY_CALENDAR+'_historial':[*data.get(KEY_CALENDAR+'_historial',[]),entry]}
        detail.save(update_fields=['orchestration_data','updated_at'])
        AuditLog.objects.create(solicitud=s,event='CREDIHOY_BASE_CALENDARIO_REGISTRADA',actor=str(operador.pk),
            request_id=str(uuid4()),payload={'baseSha256':base['sha256'],'tipo':tipo,'autorizaStart':False})
        return base


def _guardar_dia_titular(s,identity,dia,rid):
    if type(dia) is not int or not 1<=dia<=20:raise ValueError('DIA_PAGO_INVALIDO')
    with transaction.atomic(using=alias_cotizacion()):
        detail,_=_contexto(s)
        data=detail.orchestration_data;previous=data.get(KEY)
        if previous and (not previous.get('resultado') or previous['resultado']['estado']=='ERROR_TEMPORAL'):
            raise ValueError('COTIZACION_INCIERTA_CONCILIAR')
        entry=data.get(KEY_CALENDAR,{})
        if entry.get('diaPago')==dia:return
        detail.orchestration_data={**data,KEY_CALENDAR:{**entry,'diaPago':dia},
            KEY_CALENDAR+'_historial':[*data.get(KEY_CALENDAR+'_historial',[]),entry]}
        detail.save(update_fields=['orchestration_data','updated_at'])
        AuditLog.objects.create(solicitud=s,event='CREDIHOY_DIA_PAGO_SELECCIONADO',actor=_actor(None,identity),
            request_id=rid,payload={'diaPago':dia,'autorizaStart':False})


def _contexto(s, *, lock=True):
    from .funcional import _firma, _formulario_persistido
    _firma(s)
    query = SolicitudConsumo.objects.select_for_update() if lock else SolicitudConsumo.objects
    detail = query.get(solicitud=s)
    form = _formulario_persistido(s, detail.form_data)
    if form['recogerCredihoy']:
        raise ValueError('COTIZACION_RECOGIDA_NO_CERTIFICADA')
    selected = SeleccionTarifaCredihoy.objects.filter(solicitud=s).order_by('-creado_at', '-pk').first()
    if not selected:
        raise ValueError('SELECCION_COMERCIAL_REQUERIDA')
    digest = hashlib.sha256(json.dumps(selected.evidencia, sort_keys=True).encode()).hexdigest()
    if digest != selected.evidencia_sha256:
        raise ValueError('EVIDENCIA_COMERCIAL_INVALIDA')
    p = pagaduria_seleccion(selected)
    current = resolver_condicion(p)
    snapshot = selected.resultado
    keys = ('tarifaId', 'versionTarifa', 'asignacionId', 'versionAsignacion',
            'soporteTarifaSha256', 'soporteAsignacionSha256', 'tasaComercialNamv')
    if (not current.get('tasaComercialNamv') or any(current.get(k) != snapshot.get(k) for k in keys)
            or p.tipo not in {'N','V'} or not p.codigo or p.activa is not True):
        raise ValueError('CONDICIONES_COMERCIALES_NO_VIGENTES')
    conditions = {'producto': '390', 'modalidad': '111', 'destinacion': 'CFA',
        'pagaduria': p.codigo, 'tipoPagaduria': p.tipo, 'monto': form['monto'],
        'numeroCuotas': form['plazoMeses'], 'tasaNominalNamv': current['tasaComercialNamv'],
        'periodicidad': form['periodicidadPago'], 'amortizacion': 'CI', 'sinRecogida': True}
    from .condiciones_producto import exigir_plazo_por_monto
    from decimal import Decimal
    exigir_plazo_por_monto(Decimal(form['monto']),form['plazoMeses'],fecha=timezone.now())
    extra={'tasaComercialEa':current['tasaComercialEa']} if current.get('tasaComercialEa') is not None else {}
    calendar_input=detail.orchestration_data.get(KEY_CALENDAR)
    from .condiciones_producto import CALENDARIO_VERSION
    fingerprint = hashlib.sha256(json.dumps({'condiciones': conditions,
        'seleccion': selected.pk, 'evidencia': digest,
        'tarifa': {**{k: current.get(k) for k in keys},**extra},
        **({'calendarioPago':calendar_input,'reglaCalendario':CALENDARIO_VERSION} if calendar_input else {})}, sort_keys=True).encode()).hexdigest()
    return detail, {'solicitudId': str(s.pk), 'versionCondiciones': fingerprint, 'condiciones': conditions}


def _empty(sid, rid, estado, payload=None):
    return {'versionContrato': VERSION, 'solicitudId': str(sid), 'requestId': rid,
        'versionCondiciones': payload['versionCondiciones'] if payload else None,
        'fechaConsulta': timezone.now().isoformat(), 'fechaEvaluacion': None, 'vigenteHasta': None,
        'habilitadoDecisionProductiva': False, 'estado': estado, 'disponible': False,
        'cuotaInstitucional': None, 'condicionesSolicitadas': payload['condiciones'] if payload else None,
        'parametrosAplicados': None, 'seguroVida': {'factorAplicado': None, 'valorPorCuota': None, 'disponible': False},
        'primeraFechaPago': None, 'calendario': {'disponible': False, 'cuotas': None},
        'cotizacionIntegral': False, 'ambiente': None, 'fuente': 'LINIX_MOTOR_FORMS', 'camposFaltantes': []}


def _resultado_guardado(detail, payload, rid, *, ejecutar):
    manual = detail.orchestration_data.get(KEY_MANUAL, [])
    for item in reversed(manual):
        stored = item['resultado']
        if stored['versionCondiciones'] == payload['versionCondiciones']:
            from ..cotizacion_cliente import validar_respuesta
            checked = validar_respuesta(stored, payload, stored['requestId'])
            return {**checked, 'calculoRequestId':stored['requestId'], 'requestId':rid}
    previous = detail.orchestration_data.get(KEY)
    if previous and (not previous.get('resultado') or previous['resultado']['estado'] == 'ERROR_TEMPORAL'):
        return _empty(payload['solicitudId'], rid, 'REQUIERE_CONCILIACION', payload)
    if previous and previous['requestId'] == rid and previous['versionCondiciones'] != payload['versionCondiciones']:
        return _empty(payload['solicitudId'], rid, 'DESACTUALIZADA', payload)
    if previous and previous['versionCondiciones'] == payload['versionCondiciones']:
        result = previous['resultado']
        expired = result['vigenteHasta'] is not None and datetime.fromisoformat(result['vigenteHasta']) <= timezone.now()
        if expired and (not ejecutar or previous['requestId'] == rid):
            return _empty(payload['solicitudId'], rid, 'DESACTUALIZADA', payload)
        if not expired and (not ejecutar or previous['requestId'] == rid
                or result['disponible'] or result['ambiente'] == 'HOMOLOGACION'):
            return {**result, 'calculoRequestId': previous['requestId'], 'requestId': rid}
    if not ejecutar:
        return _empty(payload['solicitudId'], rid, 'DESACTUALIZADA' if previous else 'SIN_COTIZACION', payload)
    return None


def leer_cotizacion_guardada(s, request_id):
    """Internal read for an already-authorized request; never claims or calls a motor."""
    from ..cotizacion_cliente import validar_respuesta
    rid = str(UUID(request_id))
    with transaction.atomic(using=alias_cotizacion()):
        detail, payload = _contexto(s, lock=False)
        result = _resultado_guardado(detail, payload, rid, ejecutar=False)
        checked=validar_respuesta(result, payload, rid)
        if 'calculoRequestId' in result:checked['calculoRequestId']=result['calculoRequestId']
        return checked


def conciliar_condiciones_guardadas(solicitud_id, operador, evidencia, *, ahora=None,
        seguro_mensual_porcentaje=None):
    """Internal authenticated-source import; not exposed by any public URL.

    Never calls Oracle. A future institutional reader must authenticate its
    evidence before calling this operation; an arbitrary browser flag is not one.
    """
    from decimal import Decimal
    from .condiciones_producto import conciliar_composicion_contractual
    now=ahora or timezone.now()
    rid=str(UUID(evidencia['requestId']))
    with transaction.atomic(using=alias_cotizacion()):
        s=_obtener(solicitud_id,operador,lock=True)
        detail,payload=_contexto(s)
        result=_resultado_guardado(detail,payload,rid,ejecutar=False)
        if result.get('calculoRequestId',result['requestId'])!=rid:
            raise ValueError('COMPOSICION_DE_OTRA_COTIZACION')
        checked=conciliar_composicion_contractual(payload,result,evidencia,
            titular_referencia=hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest(),
            ahora=now,seguro_mensual_porcentaje=Decimal('0.090') if seguro_mensual_porcentaje is None
                else seguro_mensual_porcentaje)
        data=detail.orchestration_data
        history=dict(data.get('credihoy_composiciones_contractuales_v1',{}))
        previous=history.get(checked['sha256'])
        if previous is not None and previous!=checked:raise ValueError('COMPOSICION_EXISTENTE_NO_SOBRESCRIBIR')
        if previous is None:
            history[checked['sha256']]=checked
            detail.orchestration_data={**data,'credihoy_composiciones_contractuales_v1':history}
            detail.save(update_fields=['orchestration_data','updated_at'])
            AuditLog.objects.create(solicitud=s,event='CREDIHOY_COMPOSICION_CONCILIADA',
                actor=str(operador.pk),request_id=rid,payload={'sha256':checked['sha256'],
                    'versionCondiciones':payload['versionCondiciones'],'autorizaDesembolso':False})
        return checked


def _exigir_cuota_ofertable(q):
    """Historical/manual observations cannot become contractual offers on read."""
    if (q.get('fuente')!='LINIX_CALCULO_CUOTA' or q.get('ambiente')!='PRODUCCION'
            or q.get('disponible') is not True or not q.get('vigenteHasta')
            or q.get('simulacion') or not q.get('calculoRequestId')):
        raise ValueError('OFERTA_REQUIERE_CUOTA_LINIX_VIGENTE_DEL_EXPEDIENTE')


def preparar_oferta_linix(solicitud_id,operador,*,tasa_ea=None,primer_vencimiento=None,titular=None):
    """Reuse the persisted scalar; LINIX owns the later amortization/payment plan.

    Optional EA/date are certified Hechos from existing institutional readers,
    not browser values or a locally generated installment calendar.
    """
    from decimal import Decimal
    from datetime import date
    from ..base import Hecho,EstadoCertificacion as EC
    from .condiciones_producto import parametros_financieros
    with transaction.atomic(using=alias_cotizacion()):
        s=_obtener(solicitud_id,operador,lock=True,titular=titular)
        detail,payload=_contexto(s)
        q=leer_cotizacion_guardada(s,str(uuid4()))
        _exigir_cuota_ofertable(q)
        values={};now=timezone.now()
        selected=SeleccionTarifaCredihoy.objects.filter(solicitud=s).order_by('-creado_at','-pk').first()
        current=resolver_condicion(pagaduria_seleccion(selected))
        calendar=_calendario_guardado(detail,{'N':'NOMINA','V':'VENTANILLA'}[payload['condiciones']['tipoPagaduria']])
        if calendar:
            first=date.fromisoformat(calendar['primerVencimientoProyectado'])
            if primer_vencimiento is not None and primer_vencimiento.valor!=first:
                raise ValueError('PRIMER_VENCIMIENTO_DIFIERE_REGLA')
            primer_vencimiento=Hecho(valor=first,disponible=True,fuente=calendar['fuenteRegla'],
                referencia_evidencia=calendar['version'],ruta_origen='condiciones_producto.preparar_calendario',
                fecha_corte=timezone.localdate(),fecha_consulta=now,estado_certificacion=EC.CERTIFICADO)
        if tasa_ea is None and current.get('tasaComercialEa') is not None:
            tasa_ea=Hecho(valor=Decimal(current['tasaComercialEa']),disponible=True,
                fuente='CATALOGO_COMERCIAL_INSTITUCIONAL',
                referencia_evidencia=current['soporteTarifaSha256'],
                ruta_origen='TasaComercialCredihoy:'+str(current['tarifaId'])+':'+str(current['versionTarifa']),
                fecha_corte=timezone.localdate(),fecha_consulta=now,estado_certificacion=EC.CERTIFICADO)
        for key,h,kind in (('tasaEaPorcentaje',tasa_ea,Decimal),('primeraFechaPago',primer_vencimiento,date)):
            if h is None:values[key]=None;continue
            if (not isinstance(h,Hecho) or not h.disponible or h.estado_certificacion!=EC.CERTIFICADO
                    or not h.referencia_evidencia or not h.ruta_origen or not h.fuente
                    or h.fecha_consulta is None or h.fecha_consulta.utcoffset() is None
                    or not 0<=(now-h.fecha_consulta).total_seconds()<=300 or h.fecha_corte!=timezone.localdate()
                    or type(h.valor) is not kind):raise ValueError('CONDICION_ADICIONAL_NO_ACREDITADA')
            if kind is Decimal and (not h.valor.is_finite() or h.valor<=0):raise ValueError('TASA_EA_INVALIDA')
            if kind is date and h.valor<=timezone.localdate():raise ValueError('PRIMER_VENCIMIENTO_INVALIDO')
            values[key]=h.a_dict()
        body={'versionContrato':'credihoy-oferta-cuota-nativa-v1','solicitudId':str(s.pk),
            'titularReferencia':hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest(),
            'versionCondiciones':payload['versionCondiciones'],'calculoRequestId':q['calculoRequestId'],
            'evaluacionSha256':_evaluacion_huella(s),
            'condiciones':payload['condiciones'],'cuotaContractual':q['cuotaInstitucional'],
            'fuente':q['fuente'],'desde':q['fechaConsulta'],'hasta':q['vigenteHasta'],
            'condicionesAdicionales':values,'planPagos':'GESTION_NATIVA_LINIX_POSTERIOR',
            'calendarioPago':calendar,
            'seguroVida':{'porcentajeMensualParametrizado':parametros_financieros()['seguroVidaPorcentajeMensual'],
                'gestionImporte':'MOTOR_LINIX_SIN_PRIMA_ADICIONAL_DEL_CANAL'},
            'interesesIniciales':'DESCUENTO_EN_DESEMBOLSO_SEGUN_LINIX',
            'primaAdicionalCalculada':False,'aceptacionAcreditada':False,'autorizaStart':False,'autorizaDesembolso':False}
        body['sha256']=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        history=dict(detail.orchestration_data.get('credihoy_ofertas_linix_v1',{}))
        new=body['sha256'] not in history
        history.setdefault(body['sha256'],body)
        detail.orchestration_data={**detail.orchestration_data,'credihoy_ofertas_linix_v1':history,
            'credihoy_oferta_linix_actual':body['sha256']}
        detail.save(update_fields=['orchestration_data','updated_at'])
        if new:
            AuditLog.objects.create(solicitud=s,event='CREDIHOY_OFERTA_CUOTA_NATIVA_PREPARADA',
                actor=_actor(operador,titular),request_id=q['calculoRequestId'],payload={
                    'ofertaSha256':body['sha256'],'versionCondiciones':payload['versionCondiciones'],
                    'aceptacionAcreditada':False,'autorizaStart':False})
        return body


def leer_oferta_linix(s):
    from ..cotizacion_cliente import validar_respuesta
    from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
    current=SolicitudConsumo.objects.get(solicitud=s).orchestration_data
    if not current.get('credihoy_oferta_linix_actual'):raise ValueError('OFERTA_LINIX_NO_PREPARADA')
    try:detail,payload=_contexto(s,lock=False)
    except OperacionError:raise ValueError('OFERTA_LINIX_CONSENTIMIENTO_NO_VIGENTE') from None
    data=detail.orchestration_data
    try:
        offer=data['credihoy_ofertas_linix_v1'][data['credihoy_oferta_linix_actual']]
        body={k:v for k,v in offer.items() if k!='sha256'}
        q=_resultado_guardado(detail,payload,offer['calculoRequestId'],ejecutar=False)
        validar_respuesta(q,payload,offer['calculoRequestId'])
        _exigir_cuota_ofertable(q)
        if (offer['sha256']!=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                or offer['solicitudId']!=str(s.pk) or offer['condiciones']!=payload['condiciones']
                or offer['versionCondiciones']!=payload['versionCondiciones'] or q['disponible'] is not True
                or ('evaluacionSha256' in offer and offer['evaluacionSha256']!=_evaluacion_huella(s))
                or q['cuotaInstitucional']!=offer['cuotaContractual']
                or q.get('calculoRequestId')!=offer['calculoRequestId']
                or offer['titularReferencia']!=hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest()
                or not datetime.fromisoformat(offer['desde'])<=timezone.now()<datetime.fromisoformat(offer['hasta'])):
            raise ValueError()
        if offer.get('calendarioPago')!=_calendario_guardado(detail,
                {'N':'NOMINA','V':'VENTANILLA'}[payload['condiciones']['tipoPagaduria']]):raise ValueError()
        return json.loads(json.dumps(offer))
    except (KeyError,TypeError,ValueError):raise ValueError('OFERTA_LINIX_NO_VIGENTE_O_NO_CONCILIA') from None


def aplicar_oferta_a_contexto(contexto,oferta):
    from dataclasses import replace
    _validar_enlace_oferta(oferta)
    if (contexto.solicitud_id!=oferta['solicitudId'] or contexto.titular_referencia!=oferta['titularReferencia']):
        raise ValueError('OFERTA_DE_OTRO_TITULAR_O_EXPEDIENTE')
    return replace(contexto,oferta_referencia=oferta['calculoRequestId'],oferta_huella=oferta['sha256'],
        oferta_desde=datetime.fromisoformat(oferta['desde']),oferta_hasta=datetime.fromisoformat(oferta['hasta']),
        cuota_contractual=oferta['cuotaContractual'])


def leer_aceptacion_oferta(s):
    """Existing dossier gate can consume this proof; never infer it from OTP."""
    offer=leer_oferta_linix(s)
    if not offer.get('calendarioPago'):return None
    entry=SolicitudConsumo.objects.get(solicitud=s).orchestration_data.get(
        'credihoy_aceptaciones_oferta_v1',{}).get(offer['sha256'])
    if entry is None:return None
    try:
        body={k:v for k,v in entry.items() if k!='sha256'}
        when=datetime.fromisoformat(entry['fecha'])
        if (entry['sha256']!=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                or entry['ofertaSha256']!=offer['sha256'] or entry['versionCondiciones']!=offer['versionCondiciones']
                or entry['evaluacionSha256']!=offer['evaluacionSha256'] or entry['titularReferencia']!=offer['titularReferencia']
                or entry['acepta'] is not True or entry['autorizaStart'] is not False or entry['autorizaDesembolso'] is not False
                or str(UUID(entry['sesionReferencia']))!=entry['sesionReferencia'] or str(UUID(entry['requestId']))!=entry['requestId']
                or when.utcoffset() is None or not datetime.fromisoformat(offer['desde'])<=when<=timezone.now()):raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError):raise ValueError('ACEPTACION_NO_CORRESPONDE_A_OFERTA') from None
    return json.loads(json.dumps(entry))


def aplicar_oferta_aceptada_a_contexto(contexto,s):
    from dataclasses import replace
    offer=leer_oferta_linix(s);accepted=leer_aceptacion_oferta(s)
    if accepted is None:raise ValueError('ACEPTACION_PROPIA_REQUERIDA')
    if not offer.get('calendarioPago'):raise ValueError('CALENDARIO_DE_OFERTA_REQUERIDO')
    linked=aplicar_oferta_a_contexto(contexto,offer)
    return replace(linked,aceptacion_referencia=accepted['requestId'],
        aceptacion_oferta_huella=offer['sha256'],aceptacion_fecha=datetime.fromisoformat(accepted['fecha']))


def _validar_enlace_oferta(oferta):
    try:
        body={k:v for k,v in oferta.items() if k!='sha256'}
        if (oferta['versionContrato']!='credihoy-oferta-cuota-nativa-v1'
                or oferta['fuente']!='LINIX_CALCULO_CUOTA'
                or oferta['sha256']!=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                or not datetime.fromisoformat(oferta['desde'])<=timezone.now()<datetime.fromisoformat(oferta['hasta'])
                or any(oferta[k] is not False for k in ('aceptacionAcreditada','autorizaStart','autorizaDesembolso'))):
            raise ValueError()
        if oferta.get('calendarioPago'):
            from datetime import date
            from .condiciones_producto import revalidar_calendario
            c=oferta['calendarioPago']
            revalidar_calendario(c,date.fromisoformat(c['fechaBaseDesembolso']))
            if oferta['condicionesAdicionales']['primeraFechaPago']['valor']!=c['primerVencimientoProyectado']:
                raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError):raise ValueError('OFERTA_LINIX_NO_INTEGRA_O_VENCIDA') from None


def vincular_oferta_a_payload(payload,oferta,*,fecha_desembolso_efectiva=None,primer_vencimiento_nativo=None):
    from copy import deepcopy
    from decimal import Decimal
    try:
        _validar_enlace_oferta(oferta)
        c=oferta['condiciones'];credit=payload['credito'];product=payload['producto']
        if (payload['solicitudId']!=oferta['solicitudId']
                or hashlib.sha256(payload['titular']['numeroDocumento'].encode()).hexdigest()!=oferta['titularReferencia']
                or [product['codigo'],product['modalidad'],product['destino']]!=['390','111','CFA']
                or Decimal(credit['montoSolicitado'])!=Decimal(c['monto'])
                or credit['plazoMeses']!=c['numeroCuotas'] or credit['periodicidad']!=c['periodicidad']
                or Decimal(credit['tasaNominalAnual'])!=Decimal(c['tasaNominalNamv'])
                or (credit.get('cuotaContractual') is not None
                    and Decimal(credit['cuotaContractual'])!=Decimal(oferta['cuotaContractual']))):raise ValueError()
        result=deepcopy(payload);result['credito']['cuotaContractual']=oferta['cuotaContractual']
        if oferta.get('calendarioPago'):
            from .condiciones_producto import revalidar_calendario
            from datetime import date
            calendar=oferta['calendarioPago']
            if fecha_desembolso_efectiva is not None:
                verified=revalidar_calendario(calendar,fecha_desembolso_efectiva,
                    primer_vencimiento_nativo=primer_vencimiento_nativo)
                if not verified['continuidadPermitida']:raise ValueError('CALENDARIO_CAMBIO_REQUIERE_CONCILIACION_Y_ACEPTACION')
                calendar=verified['actual']
            # Known radication field only. This is NOT the unverified 7283 form input.
            result['contextoLinix']['fechaCuotaOrdinaria']=date.fromisoformat(
                calendar['primerVencimientoProyectado']).strftime('%m/%d/%Y')
        return result
    except (KeyError,TypeError,ValueError,ArithmeticError):raise ValueError('SOLICITUD_NO_CORRESPONDE_A_OFERTA_LINIX') from None


def cotizacion(solicitud_id, operador, request_id, *, ejecutar=False, cliente=None, escenario=None, sesion=None, dinamica=False, titular=None, autogestion_tecnica=False):
    if type(autogestion_tecnica) is not bool or (autogestion_tecnica and (titular is not None or dinamica)):
        raise ValueError('COTIZACION_MODO_INVALIDO')
    if autogestion_tecnica and ejecutar:
        from django.conf import settings
        if getattr(settings, 'CREDIHOY_COTIZACION_ENABLED', False) is not True:
            raise ValueError('COTIZACION_NO_HABILITADA_EN_EJECUTOR')
    if dinamica:
        if titular is not None:
            raise ValueError('ESCENARIO_TITULAR_NO_PERSISTIDO')
        return simulacion_dinamica(solicitud_id, operador, request_id, escenario=escenario, sesion=sesion, ejecutar=ejecutar, cliente=cliente)
    if connections[alias_cotizacion()].in_atomic_block:
        raise ValueError('COTIZACION_REQUIERE_TRANSACCION_INDEPENDIENTE')
    rid = str(UUID(request_id))
    with transaction.atomic(using=alias_cotizacion()):
        s = _obtener(solicitud_id, operador, lock=True, titular=titular, autogestion_tecnica=autogestion_tecnica)
        from .funcional import oferta_custodia_activa
        if ejecutar and oferta_custodia_activa():
            _exigir_decision_ofertable(s, SolicitudConsumo.objects.get(solicitud=s))
        try:
            detail, payload = _contexto(s)
        except (ValueError, KeyError, AttributeError) as exc:
            r = _empty(s.pk, rid, 'PARAMETROS_EXPEDIENTE_INCOMPLETOS')
            r['motivo'] = str(exc) if str(exc) in MOTIVOS else 'EVIDENCIA_INCOMPLETA'
            return r
        stored = _resultado_guardado(detail, payload, rid, ejecutar=ejecutar)
        if stored is not None:
            return stored
        from .condiciones_producto import limite_desde_evaluacion
        from decimal import Decimal
        from .funcional import leer_estado_funcional
        aportes = limite_desde_evaluacion(leer_estado_funcional(s)['resultado'], fecha=timezone.now())
        if Decimal(payload['condiciones']['monto']) > Decimal(aportes['montoMaximo']):
            raise ValueError('MONTO_SUPERA_90_PORCIENTO_APORTES')
        try:
            client = cliente or ApiDataCotizacionClient(ConfiguracionApiDataV2.desde_settings())
        except ApiDataCrediHoyV2Error:
            r = _empty(s.pk, rid, 'NO_HABILITADA', payload)
            r['motivo'] = 'CONFIGURACION_API_DATA_INCOMPLETA'
            return r
        detail.orchestration_data = {**detail.orchestration_data, KEY: {**payload, 'requestId': rid, 'resultado': None}}
        detail.save(update_fields=['orchestration_data', 'updated_at'])
        AuditLog.objects.create(solicitud=s, event='CREDIHOY_COTIZACION_SOLICITADA', actor=_actor(operador, titular), request_id=rid, payload={'versionCondiciones': payload['versionCondiciones']})
    try:
        result = client.cotizar(payload, rid)
    except Exception:
        return _empty(s.pk, rid, 'REQUIERE_CONCILIACION', payload)
    with transaction.atomic(using=alias_cotizacion()):
        s = _obtener(solicitud_id, operador, lock=True, titular=titular, autogestion_tecnica=autogestion_tecnica)
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        entry = detail.orchestration_data.get(KEY, {})
        if entry.get('requestId') != rid:
            return _empty(s.pk, rid, 'REQUIERE_CONCILIACION', payload)
        detail.orchestration_data = {**detail.orchestration_data, KEY: {**entry, 'resultado': result}}
        detail.save(update_fields=['orchestration_data', 'updated_at'])
        AuditLog.objects.create(solicitud=s, event='CREDIHOY_COTIZACION_RESPUESTA', actor=_actor(operador, titular), request_id=rid, payload={'versionCondiciones': payload['versionCondiciones'], 'estado': result['estado']})
        try:
            _, current = _contexto(s)
            if current != payload:
                return _empty(s.pk, rid, 'DESACTUALIZADA', current)
        except (ValueError, KeyError, AttributeError):
            return _empty(s.pk, rid, 'DESACTUALIZADA')
        if autogestion_tecnica:
            return result
        if result.get('disponible') is True and result.get('fuente') == 'LINIX_CALCULO_CUOTA':
            try:
                _exigir_decision_ofertable(s, detail)
            except ValueError:
                return result
            preparar_oferta_linix(s.pk, operador, titular=titular)
        return result


def operacion_titular(identity,payload,rid,*,cliente=None):
    with fuente_cotizacion():
        return _operacion_titular(identity,payload,rid,cliente=cliente)


def _operacion_titular(identity, payload, rid, *, cliente=None):
    """Called only by the authenticated owner delegation, never with staff identity."""
    from django.conf import settings
    if type(payload) is dict and getattr(settings, 'CREDIHOY_COTIZACION_MOTOR', '') == 'PROPIO_390_111_CFA':
        return _operacion_simulacion_propia(identity, payload, rid)
    if type(payload) is not dict or payload.get('operacion') not in {'consultar', 'cotizar', 'aceptar'}:
        raise ValueError('PETICION_COTIZACION_INVALIDA')
    expected = {'solicitudId', 'operacion'}
    if payload['operacion'] == 'aceptar':
        expected |= {'ofertaSha256', 'acepta'}
    if payload['operacion'] == 'cotizar' and 'diaPago' in payload:
        expected.add('diaPago')
    if set(payload) != expected:
        raise ValueError('PETICION_COTIZACION_INVALIDA')
    sid = str(UUID(payload['solicitudId']))
    rid = str(UUID(rid))
    s = _obtener(sid, None, titular=identity)
    if payload['operacion'] == 'cotizar' and 'diaPago' in payload:
        _guardar_dia_titular(s, identity, payload['diaPago'], rid)
    if payload['operacion'] == 'cotizar':
        cotizacion(sid, None, rid, ejecutar=True, cliente=cliente, titular=identity)
    with transaction.atomic(using=alias_cotizacion()):
        s = _obtener(sid, None, lock=True, titular=identity)
        detail, context = _contexto(s)
        q = _resultado_guardado(detail, context, rid, ejecutar=False)
        if q['disponible']:
            from ..cotizacion_cliente import validar_respuesta
            validar_respuesta(q, context, rid)
        try:
            offer = leer_oferta_linix(s)
        except ValueError:
            offer = None
        accepted = None
        if offer:
            data = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
            acceptance = leer_aceptacion_oferta(s)
            if acceptance:
                accepted = acceptance
            if payload['operacion'] == 'aceptar':
                if payload['acepta'] is not True or payload['ofertaSha256'] != offer['sha256']:
                    raise ValueError('ACEPTACION_DE_OTRA_OFERTA')
                if not offer.get('evaluacionSha256') or not offer.get('calendarioPago') or any((offer['condicionesAdicionales'][k] is None for k in ('tasaEaPorcentaje', 'primeraFechaPago'))):
                    raise ValueError('CONDICIONES_CONTRACTUALES_PENDIENTES')
                from .funcional import leer_estado_funcional
                from apps.xcore_consumo.models import EvaluacionConsumo
                if not EvaluacionConsumo.objects.filter(solicitud=s).exists() or leer_estado_funcional(s)['resultado'] is None:
                    raise ValueError('EVALUACION_VIGENTE_REQUERIDA')
                if accepted is None:
                    accepted = {'ofertaSha256': offer['sha256'], 'versionCondiciones': offer['versionCondiciones'], 'evaluacionSha256': offer['evaluacionSha256'], 'sesionReferencia': identity['_sesionReferencia'], 'titularReferencia': offer['titularReferencia'], 'fecha': timezone.now().isoformat(), 'requestId': rid, 'acepta': True, 'autorizaStart': False, 'autorizaDesembolso': False}
                    accepted['sha256'] = hashlib.sha256(json.dumps(accepted, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    history = dict(data.orchestration_data.get('credihoy_aceptaciones_oferta_v1', {}))
                    history[offer['sha256']] = accepted
                    data.orchestration_data = {**data.orchestration_data, 'credihoy_aceptaciones_oferta_v1': history}
                    data.save(update_fields=['orchestration_data', 'updated_at'])
                    AuditLog.objects.create(solicitud=s, event='CREDIHOY_OFERTA_ACEPTADA_TITULAR', actor=_actor(None, identity), request_id=rid, payload={'ofertaSha256': offer['sha256'], 'evaluacionSha256': offer['evaluacionSha256'], 'autorizaStart': False})
        elif payload['operacion'] == 'aceptar':
            raise ValueError('OFERTA_NO_DISPONIBLE')
        conditions = offer['condiciones'] if offer else {}
        return {'versionContrato': 'credihoy-cotizacion-titular-v1', 'solicitudId': sid, 'estado': 'NO_DISPONIBLE' if q['estado'] in {'DISPONIBLE_HOMOLOGACION', 'EVIDENCIA_INSTITUCIONAL_MANUAL'} else q['estado'], 'cuota': offer['cuotaContractual'] if offer else None, 'oferta': {'sha256': offer['sha256'], 'monto': conditions['monto'], 'plazoMeses': conditions['numeroCuotas'], 'periodicidad': conditions['periodicidad'], 'tasaNominalNamv': conditions['tasaNominalNamv'], 'tasaEA': offer['condicionesAdicionales']['tasaEaPorcentaje']['valor'] if offer['condicionesAdicionales']['tasaEaPorcentaje'] else None, 'primerVencimiento': offer['condicionesAdicionales']['primeraFechaPago']['valor'] if offer['condicionesAdicionales']['primeraFechaPago'] else None, 'vigenteHasta': offer['hasta'], 'condicionesCompletas': bool(offer.get('calendarioPago')) and all((offer['condicionesAdicionales'][k] is not None for k in ('tasaEaPorcentaje', 'primeraFechaPago'))), 'calendarioPago': proyectar_calendario_oferta(offer), 'seguroIncluidoPorMotor': True} if offer else None, 'aceptacion': {'aceptada': accepted is not None, 'fecha': accepted['fecha'] if accepted else None}, 'autorizaStart': False, 'autorizaDesembolso': False}


def proyectar_calendario_oferta(offer):
    c=offer.get('calendarioPago')
    if c is None:return None
    return {'versionRegla':c['version'],'diaPago':c['diaSeleccionado'],
        'fechaBaseDesembolso':c['fechaBaseDesembolso'],'tipoBase':c['tipoBase'],
        'primerVencimiento':c['primerVencimientoProyectado'],
        'interesesAnticipados':{k:c['interesesAnticipados'][k] for k in ('desde','hasta','importe')}}


def conciliar_calendario_nativo(solicitud_id,operador,evidencia):
    """Authenticated internal observation only; never executes activity 7283."""
    from datetime import date
    from .condiciones_producto import revalidar_calendario
    from .linix_estado import presentar
    with transaction.atomic(using=alias_cotizacion()):
        s=_obtener(solicitud_id,operador,lock=True)
        detail=SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        offer=leer_oferta_linix(s);native=presentar(s,tecnico=True)
        e=evidencia
        try:
            observed=datetime.fromisoformat(e['observadoEn'])
            if (set(e)!={'solicitudId','SC','K_FLUJO','tarea','actividad','fuente','consultaReferencia',
                    'observadoEn','fechaDesembolso','primeraFechaNativa'}
                    or e['solicitudId']!=str(s.pk) or not native
                    or e['SC']!=native['numeroSc'] or e['K_FLUJO']!=native['kFlujo']
                    or type(e['tarea']) is not str or not e['tarea'].isdigit() or int(e['tarea'])<=0
                    or type(e['actividad']) is not int or e['actividad']!=7283 or e['fuente']!='LINIX'
                    or not e['consultaReferencia'] or observed.utcoffset() is None
                    or not 0<=(timezone.now()-observed).total_seconds()<=300):raise ValueError()
            result=revalidar_calendario(offer['calendarioPago'],date.fromisoformat(e['fechaDesembolso']),
                primer_vencimiento_nativo=date.fromisoformat(e['primeraFechaNativa']))
            if date.fromisoformat(e['fechaDesembolso'])>timezone.localdate():raise ValueError()
        except (TypeError,KeyError,ValueError,AttributeError):raise ValueError('CALENDARIO_NATIVO_NO_ACREDITADO') from None
        result={**result,'ofertaSha256':offer['sha256'],'evidencia':e}
        result['sha256']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
        key=KEY_CALENDAR+'_revalidacion';data=detail.orchestration_data
        prior=data.get(key)
        if prior and prior['sha256']==result['sha256']:return json.loads(json.dumps(prior))
        if prior and datetime.fromisoformat(prior['evidencia']['observadoEn'])>=observed:
            raise ValueError('CALENDARIO_NATIVO_ANTERIOR_NO_SOBRESCRIBIR')
        detail.orchestration_data={**data,key:result,key+'_historial':[*data.get(key+'_historial',[]),result]}
        detail.save(update_fields=['orchestration_data','updated_at'])
        AuditLog.objects.create(solicitud=s,event='CREDIHOY_CALENDARIO_NATIVO_CONCILIADO',actor=str(operador.pk),
            request_id=str(uuid4()),payload={'sha256':result['sha256'],'requiereNuevaAceptacion':result['requiereNuevaAceptacion']})
        return result


def exigir_calendario_formalizacion(s,oferta):
    from datetime import date
    from .condiciones_producto import revalidar_calendario
    from .linix_estado import presentar
    data=SolicitudConsumo.objects.get(solicitud=s).orchestration_data
    p=data.get(KEY_CALENDAR+'_revalidacion')
    try:
        if leer_aceptacion_oferta(s) is None:raise ValueError()
        if (not p or p['ofertaSha256']!=oferta['sha256'] or p['sha256']!=hashlib.sha256(
                json.dumps({k:v for k,v in p.items() if k!='sha256'},sort_keys=True).encode()).hexdigest()):raise ValueError()
        result=revalidar_calendario(oferta['calendarioPago'],date.fromisoformat(p['evidencia']['fechaDesembolso']),
            primer_vencimiento_nativo=date.fromisoformat(p['evidencia']['primeraFechaNativa']))
        observed=datetime.fromisoformat(p['evidencia']['observadoEn'])
        native=presentar(s,tecnico=True)
        if (not result['continuidadPermitida'] or observed.utcoffset() is None
                or not 0<=(timezone.now()-observed).total_seconds()<=300
                or not native or p['evidencia']['SC']!=native['numeroSc'] or p['evidencia']['K_FLUJO']!=native['kFlujo']
                or date.fromisoformat(p['evidencia']['fechaDesembolso'])!=timezone.localdate()):raise ValueError()
    except (TypeError,KeyError,ValueError):raise ValueError('CALENDARIO_DEFINITIVO_REQUIERE_CONCILIACION_O_NUEVA_ACEPTACION') from None


def limites_simulador(s=None):
    """No defaults: transport bounds do not constitute institutional product rules."""
    from django.conf import settings
    from decimal import Decimal, InvalidOperation
    import re
    if getattr(settings,'CREDIHOY_SIMULADOR_DINAMICO_ENABLED',False) is not True:
        raise ValueError('SIMULADOR_DINAMICO_NO_HABILITADO')
    limits=getattr(settings,'CREDIHOY_SIMULADOR_LIMITES',None)
    keys={'version','referencia','modalidad','montoMinimo','montoMaximo','cuotasMinimas','cuotasMaximas'}
    try:
        if (type(limits) is not dict or set(limits)!=keys or limits['modalidad']!='111'
                or any(type(limits[k]) is not str or not limits[k].strip() for k in ('version','referencia'))
                or any(type(limits[k]) is not str or not re.fullmatch(r'[0-9]{1,10}(\.[0-9]{1,2})?',limits[k]) for k in ('montoMinimo','montoMaximo'))
                or not 0<Decimal(limits['montoMinimo'])<=Decimal(limits['montoMaximo'])
                or any(type(limits[k]) is not int for k in ('cuotasMinimas','cuotasMaximas'))
                or not 1<=limits['cuotasMinimas']<=limits['cuotasMaximas']<=48):
            raise ValueError
    except (TypeError,KeyError,InvalidOperation,ValueError):
        raise ValueError('REGLAS_PRODUCTO_NO_CONFIGURADAS') from None
    if s is None:
        raise ValueError('APORTES_DEL_EXPEDIENTE_REQUERIDOS')
    from .condiciones_producto import limite_desde_evaluacion
    from .funcional import leer_estado_funcional
    aportes=limite_desde_evaluacion(leer_estado_funcional(s)['resultado'],fecha=timezone.now())
    maximum=min(Decimal(limits['montoMaximo']),Decimal(aportes['montoMaximo']))
    if maximum<Decimal(limits['montoMinimo']):
        raise ValueError('APORTES_INSUFICIENTES_PARA_MONTO_MINIMO')
    from .condiciones_producto import plazos_por_monto
    plazos=plazos_por_monto(Decimal(s.monto_solicitado),fecha=timezone.now())
    return {**limits,'montoMaximo':format(maximum,'.2f'),'aportes':aportes,'plazosPorMonto':plazos,
        'cuotasMinimas':max(1,limits['cuotasMinimas']),'cuotasMaximas':min(48,limits['cuotasMaximas'])}


def disponibilidad_simulador(s=None):
    try:return {'habilitado':True,'limites':limites_simulador(s),'motivo':None}
    except ValueError as exc:return {'habilitado':False,'limites':None,'motivo':str(exc)}


def preparar_monto_financiable(cuota_disponible, escenarios, *, limite_monto, fecha):
    """Select among supplied adapter results; never invert a financial formula."""
    from decimal import Decimal
    from ..base import Hecho, EstadoCertificacion as EC, instante
    from ..cotizacion_cliente import validar_respuesta
    from zoneinfo import ZoneInfo
    instante(fecha,'fecha')
    if (not isinstance(cuota_disponible,Hecho) or not cuota_disponible.disponible
            or cuota_disponible.estado_certificacion not in {EC.OBSERVADO,EC.CERTIFICADO}
            or not all((cuota_disponible.fuente,cuota_disponible.ruta_origen,
                cuota_disponible.referencia_evidencia,cuota_disponible.fecha_corte,cuota_disponible.fecha_consulta))
            or cuota_disponible.fecha_consulta>fecha
            or (cuota_disponible.fecha_corte.astimezone(ZoneInfo('America/Bogota')).date()
                if isinstance(cuota_disponible.fecha_corte,datetime) else cuota_disponible.fecha_corte)
                !=fecha.astimezone(ZoneInfo('America/Bogota')).date()
            or type(cuota_disponible.valor) is not Decimal or cuota_disponible.valor<0
            or type(limite_monto) is not Decimal or not limite_monto.is_finite() or limite_monto<0):
        raise ValueError('CAPACIDAD_PARA_CUOTA_NO_ACREDITADA')
    candidates=[];context=None
    for payload,result,rid in escenarios:
        checked=validar_respuesta(result,payload,rid)
        conditions=payload['condiciones']
        signature=(payload['solicitudId'],conditions['numeroCuotas'],conditions['tasaNominalNamv'],
            conditions['modalidad'],conditions['periodicidad'],payload.get('simulacion',{}).get('sesion'),
            payload.get('simulacion',{}).get('tarifa'))
        if context is not None and signature!=context:raise ValueError('ESCENARIOS_DE_CONTEXTO_DISTINTO')
        context=signature
        if (conditions.get('producto')!='390' or conditions.get('modalidad')!='111'
                or conditions.get('destinacion')!='CFA' or conditions.get('periodicidad')!='MENSUAL'
                or type(conditions['numeroCuotas']) is not int or not 1<=conditions['numeroCuotas']<=48
                or Decimal(conditions['monto'])<=0 or Decimal(conditions['monto'])>limite_monto):
            raise ValueError('ESCENARIO_FUERA_DE_LIMITES')
        from .condiciones_producto import exigir_plazo_por_monto
        exigir_plazo_por_monto(Decimal(conditions['monto']),conditions['numeroCuotas'],fecha=fecha)
        if (checked['disponible'] and checked['fuente']=='LINIX_CALCULO_CUOTA'
                and Decimal(checked['cuotaInstitucional'])<=cuota_disponible.valor):
            candidates.append({'monto':conditions['monto'],'cuotaReferencia':checked['cuotaInstitucional'],
                'requestId':rid,'versionCondiciones':payload['versionCondiciones']})
    selected=max(candidates,key=lambda item:Decimal(item['monto'])) if candidates else None
    return {'estado':'MAYOR_ESCENARIO_PARCIAL_EVALUADO' if selected else 'PENDIENTE_ESCENARIOS_INSTITUCIONALES',
        'escenario':selected,'cuotaDisponible':cuota_disponible.a_dict(),
        'limitePorAportes':format(limite_monto,'f'),'maximoFinanciableAcreditado':False,
        'incluyeSeguroYCargosAcreditados':False,'esCupoAprobado':False,'autorizaDecision':False}


def _escenario_payload(s, escenario, sesion):
    from decimal import Decimal
    import re
    limits=limites_simulador(s)
    if (type(sesion) is not str or not re.fullmatch(r'[a-f0-9]{64}',sesion)
            or type(escenario) is not dict or set(escenario)!={'monto','plazoMeses','secuencia'}
            or type(escenario['monto']) is not str or not re.fullmatch(r'[0-9]{1,10}(\.[0-9]{1,2})?',escenario['monto'])
            or type(escenario['plazoMeses']) is not int or type(escenario['secuencia']) is not int
            or escenario['secuencia']<1
            or not Decimal(limits['montoMinimo'])<=Decimal(escenario['monto'])<=Decimal(limits['montoMaximo'])
            or not limits['cuotasMinimas']<=escenario['plazoMeses']<=limits['cuotasMaximas']):
        raise ValueError('ESCENARIO_SIMULACION_INVALIDO')
    detail,payload=_contexto(s)
    selected=SeleccionTarifaCredihoy.objects.filter(solicitud=s).order_by('-creado_at','-pk').first()
    names=('tarifaId','versionTarifa','asignacionId','versionAsignacion',
        'soporteTarifaSha256','soporteAsignacionSha256','tasaComercialNamv')
    tarifa={k:selected.resultado[k] for k in names}
    conditions={**payload['condiciones'],'monto':format(Decimal(escenario['monto']),'.2f'),
        'numeroCuotas':escenario['plazoMeses'],'modalidad':limits['modalidad']}
    from .condiciones_producto import exigir_plazo_por_monto
    exigir_plazo_por_monto(Decimal(escenario['monto']),escenario['plazoMeses'],fecha=timezone.now())
    version=hashlib.sha256(json.dumps({'base':payload['versionCondiciones'],'condiciones':conditions,
        'reglasProducto':limits,'tarifa':tarifa,'sesion':sesion},sort_keys=True).encode()).hexdigest()
    return detail,{'solicitudId':str(s.pk),'versionCondiciones':version,'condiciones':conditions,
        'simulacion':{'sesion':sesion,'secuencia':escenario['secuencia'],'tarifa':tarifa}}


def _empty_dynamic(payload,rid,estado):
    return {**_empty(payload['solicitudId'],rid,estado,payload),'simulacion':payload['simulacion']}


def simulacion_dinamica(solicitud_id, operador, request_id, *, escenario, sesion, ejecutar, cliente=None):
    """Durable scenario journal. No modification of accepted conditions or decisions."""
    from copy import deepcopy
    from . import funcional as f
    rid=str(UUID(request_id))
    if connection.in_atomic_block:raise ValueError('COTIZACION_REQUIERE_TRANSACCION_INDEPENDIENTE')
    with transaction.atomic():
        s=_obtener(solicitud_id,operador,lock=True)
        limites_simulador(s)
        detail=SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        journal=deepcopy(detail.orchestration_data.get(KEY_DYNAMIC,{}))
        session=journal.get(sesion,{'ultimaSecuencia':0,'entradas':{}})
        entries=session['entradas']
        if not ejecutar:
            if not entries:return _empty(s.pk,rid,'SIN_COTIZACION')
            latest=next(v for v in entries.values() if v['escenario']['secuencia']==session['ultimaSecuencia'])
            _,payload=_escenario_payload(s,latest['escenario'],sesion)
            if payload!=latest['payload'] or f._digest(f.leer_estado_funcional(s)['resultado'])!=latest['baselineSha256']:
                return _empty_dynamic(payload,rid,'DESACTUALIZADA')
            result=latest['resultado']
            if result is None or result['estado']=='ERROR_TEMPORAL':return _empty_dynamic(payload,rid,'REQUIERE_CONCILIACION')
            if result['vigenteHasta'] is not None and datetime.fromisoformat(result['vigenteHasta'])<=timezone.now():
                return _empty_dynamic(payload,rid,'DESACTUALIZADA')
            return {**result,'requestId':rid,'calculoRequestId':latest['requestId']}
        detail,payload=_escenario_payload(s,escenario,sesion)
        for owner,other in journal.items():
            if rid in other['entradas'] and owner!=sesion:raise ValueError('REQUEST_ID_DE_OTRA_SESION')
        if rid in entries:
            entry=entries[rid]
            if entry['payload']!=payload:raise ValueError('REQUEST_ID_ESCENARIO_DISTINTO')
            if escenario['secuencia']!=session['ultimaSecuencia']:return _empty_dynamic(payload,rid,'DESACTUALIZADA')
            result=entry['resultado']
            if (f._digest(f.leer_estado_funcional(s)['resultado'])!=entry['baselineSha256']
                    or result is not None and result['vigenteHasta'] is not None
                    and datetime.fromisoformat(result['vigenteHasta'])<=timezone.now()):
                return _empty_dynamic(payload,rid,'DESACTUALIZADA')
            return result if result is not None else _empty_dynamic(payload,rid,'REQUIERE_CONCILIACION')
        if escenario['secuencia']<=session['ultimaSecuencia']:
            return _empty_dynamic(payload,rid,'DESACTUALIZADA')
        for entry in entries.values():
            if entry['payload']['versionCondiciones']==payload['versionCondiciones']:
                result=entry['resultado']
                if result is None or result['estado']=='ERROR_TEMPORAL':
                    return _empty_dynamic(payload,rid,'REQUIERE_CONCILIACION')
                if (result['disponible'] and datetime.fromisoformat(result['vigenteHasta'])>timezone.now()
                        and f._digest(f.leer_estado_funcional(s)['resultado'])==entry['baselineSha256']):
                    reused={**result,'requestId':rid,'simulacion':payload['simulacion'],'calculoRequestId':entry['requestId']}
                    entries[rid]={'requestId':rid,'payload':payload,'escenario':escenario,'resultado':reused,
                        'baselineSha256':entry['baselineSha256']}
                    session.update(ultimaSecuencia=escenario['secuencia'],entradas=entries)
                    journal[sesion]=session
                    detail.orchestration_data={**detail.orchestration_data,KEY_DYNAMIC:journal}
                    detail.save(update_fields=['orchestration_data','updated_at'])
                    return reused
        # Include the original evidence digest; changed financial data invalidate projections.
        baseline=f.leer_estado_funcional(s)['resultado']
        entries[rid]={'requestId':rid,'payload':payload,'escenario':escenario,
            'resultado':None,'baselineSha256':f._digest(baseline)}
        session.update(ultimaSecuencia=escenario['secuencia'],entradas=entries)
        journal[sesion]=session
        detail.orchestration_data={**detail.orchestration_data,KEY_DYNAMIC:journal}
        detail.save(update_fields=['orchestration_data','updated_at'])
        AuditLog.objects.create(solicitud=s,event='CREDIHOY_SIMULACION_RECLAMADA',actor=str(operador.pk),
            request_id=rid,payload={'versionCondiciones':payload['versionCondiciones'],'secuencia':escenario['secuencia']})
    try:
        client=cliente or ApiDataCotizacionClient(ConfiguracionApiDataV2.desde_settings())
        result=client.cotizar(payload,rid)
        from ..cotizacion_cliente import validar_respuesta
        result=validar_respuesta(result,payload,rid)
    except Exception:
        # Unknown outcome remains reserved. Never repeat automatically.
        return _empty_dynamic(payload,rid,'REQUIERE_CONCILIACION')
    with transaction.atomic():
        s=_obtener(solicitud_id,operador,lock=True)
        detail=SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        journal=deepcopy(detail.orchestration_data[KEY_DYNAMIC]);session=journal[sesion]
        entry=session['entradas'][rid]
        if entry['resultado'] is not None:return _empty_dynamic(payload,rid,'REQUIERE_CONCILIACION')
        _,current=_escenario_payload(s,escenario,sesion)
        baseline=f.leer_estado_funcional(s)['resultado']
        stale=current!=payload or session['ultimaSecuencia']!=escenario['secuencia'] or f._digest(baseline)!=entry['baselineSha256']
        if result['disponible'] and current==payload and f._digest(baseline)==entry['baselineSha256']:
            from decimal import Decimal
            from .motor import aplicar_cuota_observada_preliminar
            preliminary=baseline.get('capacidadPreliminar') if baseline else None
            result['proyeccionPreliminar']={
                'capacidad':aplicar_cuota_observada_preliminar(preliminary,Decimal(result['cuotaInstitucional']),
                    payload['versionCondiciones'],fuente='LINIX_CALCULO_CUOTA') if preliminary else None,
                'decision':'NO_AUTORIZADA','reglasReevaluadas':False,'condicionesAceptadasModificadas':False,
                'reglasReferencia':[{'codigo':r['codigo'],'estado':r['estadoEvaluacion']} for r in baseline['reglas']] if baseline else [],
                'observacion':'Escenario de simulacion; requiere revision de condiciones y evaluacion antes de una oferta.'}
        entry['resultado']=result
        detail.orchestration_data={**detail.orchestration_data,KEY_DYNAMIC:journal}
        detail.save(update_fields=['orchestration_data','updated_at'])
        AuditLog.objects.create(solicitud=s,event='CREDIHOY_SIMULACION_RESPUESTA',actor=str(operador.pk),
            request_id=rid,payload={'estado':result['estado'],'versionCondiciones':payload['versionCondiciones'],'obsoleta':stale})
        return _empty_dynamic(payload,rid,'DESACTUALIZADA') if stale else result


def incorporar_prueba_manual(solicitud_id, operador, resultado, *, revision_id, resultado_anterior_sha256):
    """Administrative append-only import. No provider calls or automatic calculation."""
    from copy import deepcopy
    from decimal import Decimal
    from apps.xcore_consumo.models import EvaluacionConsumo
    from ..cotizacion_cliente import validar_respuesta
    from . import funcional as f
    from .motor import aplicar_cuota_observada_preliminar
    rid=str(UUID(revision_id))
    with transaction.atomic():
        s=_obtener(solicitud_id,operador,lock=True)
        detail,payload=_contexto(s)
        checked=validar_respuesta(resultado,payload,rid)
        if checked['fuente']!='LINIX_EJECUCION_MANUAL' or checked['estado']!='EVIDENCIA_INSTITUCIONAL_MANUAL':
            raise ValueError('EVIDENCIA_MANUAL_REQUERIDA')
        e=EvaluacionConsumo.objects.select_for_update().get(solicitud=s)
        if e.decision_final!='NO_AUTORIZADA' or detail.orchestration_data.get('credihoy_linix_administrativo'):
            raise ValueError('ESTADO_EXPEDIENTE_NO_PERMITIDO')
        history=detail.orchestration_data.get(KEY_MANUAL,[])
        for old in history:
            if old['resultado']['evidenciaManual']['sha256']==checked['evidenciaManual']['sha256']:
                if ({k:v for k,v in old['resultado'].items() if k!='fechaConsulta'}
                        != {k:v for k,v in checked.items() if k!='fechaConsulta'}):
                    raise ValueError('EVIDENCIA_MANUAL_CONFLICTO')
                return old
        before=f.leer_estado_funcional(s)['resultado']
        if f._digest(before)!=resultado_anterior_sha256:
            raise ValueError('EVALUACION_BASE_CAMBIO')
        if before['decisionCrediticia']!='NO_AUTORIZADA':raise ValueError('DECISION_BASE_INVALIDA')
        after=deepcopy(before)
        preliminary=aplicar_cuota_observada_preliminar(before['capacidadPreliminar'],
            Decimal(checked['cuotaInstitucional']),checked['evidenciaManual']['sha256'])
        after.update(requestId=rid,fechaRevision=timezone.now().isoformat(),
            capacidadPreliminar=preliminary,cuotaParaCapacidad=preliminary['cuotaParaCapacidad'],
            cuotaLinixObservada=checked)
        row=next(row for row in after['reglas'] if row['codigo']=='UNICO_FILA_2')
        row['evidencia']={**row.get('evidencia',{}),'capacidadPreliminar':preliminary}
        key=f.KEY_HDC_REVISION+':'+rid
        if key in e.resultados or key in e.input_snapshot:raise ValueError('REVISION_MANUAL_CONFLICTO')
        active=e.input_snapshot.get('revisionHdcActiva')
        if active is None:active=f.KEY_HDC_REVISION if f.KEY_HDC_REVISION in e.resultados else f.KEY_HDC
        link=deepcopy(e.input_snapshot[active])
        if not all(link.get(k) for k in ('operacionId','consultaId','firma')):
            raise ValueError('VINCULO_HDC_PREVIO_REQUERIDO')
        entry={'resultado':checked,'revisionEvaluacion':key,'fechaImportacion':after['fechaRevision'],
            'responsable':str(operador.pk),'resultadoAnteriorSha256':resultado_anterior_sha256}
        e.resultados={**e.resultados,key:after}
        e.input_snapshot={**e.input_snapshot,key:{**link,'revisionEvidencia':rid,
            'revisionAnterior':active,'revisionFinancieraOrigen':link.get('revisionEvidencia'),
            'evidenciaCuotaSha256':checked['evidenciaManual']['sha256'],
            'resultadoSha256':f._digest(after)},'revisionHdcActiva':key}
        e.save(update_fields=['resultados','input_snapshot','updated_at'])
        detail.orchestration_data={**detail.orchestration_data,KEY_MANUAL:[*history,entry]}
        detail.save(update_fields=['orchestration_data','updated_at'])
        AuditLog.objects.create(solicitud=s,event='CREDIHOY_CUOTA_MANUAL_INSTITUCIONAL_INCORPORADA',
            actor=f'user:{operador.pk}',request_id=rid,payload={
                'evidenciaSha256':checked['evidenciaManual']['sha256'],
                'versionCondiciones':payload['versionCondiciones'],
                'resultadoAnteriorSha256':resultado_anterior_sha256,
                'cuota':checked['cuotaInstitucional'],'capacidadPreliminar':preliminary['capacidad'],
                'aceptacionAsociado':False,'ejecucionAutomaticaHabilitada':False,'HDC':0,'OTP':0,'Oracle':False,'START':0})
        return entry


def _exigir_decision_ofertable(s, detail):
    """Reuse the durable policy decision, not the legacy decision_final label.

    This is not LINIX approval. The latest registered decision must approve the
    current study; an older approval cannot override a later pending decision.
    """
    from apps.xcore_consumo.models import EvaluacionConsumo
    from .funcional import KEY as study_key
    from .condiciones_producto import politica_digital
    try:
        history = detail.orchestration_data.get('credihoy_decisiones_digitales_v1')
        automatic = getattr(s, 'metadatos', {}).get('modo') == 'AUTOGESTION_ENVIADA'
        if automatic:
            from .elegibilidad import leer_decision_estudio
            verified = leer_decision_estudio(s)
            if verified is None:
                raise ValueError()
            history = {verified['operacionId']: verified}
        if type(history) is not dict or not history:
            raise ValueError()
        entries = []
        now = timezone.now()
        for key, entry in history.items():
            body = {k: v for k, v in entry.items() if k != 'sha256'}
            when = datetime.fromisoformat(entry['registradoEn'])
            if str(UUID(key)) != key or entry['operacionId'] != key or entry['solicitudId'] != str(s.pk) or (not entry['actorTecnicoId']) or (when.utcoffset() is None) or (when > now) or (entry['sha256'] != hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()):
                raise ValueError()
            entries.append((when, entry))
        latest = max((when for when, _ in entries))
        current = [entry for when, entry in entries if when == latest]
        if len(current) != 1:
            raise ValueError()
        entry = current[0]
        decision = entry['resultado']
        policy = politica_digital()
        if automatic:
            from .funcional import leer_estado_funcional
            state = leer_estado_funcional(s)
            snapshot = state['resultado']
            if entry.get('revisionReferencia') != state.get('revisionEstudio', {}).get('versionReferencia'):
                raise ValueError()
            if entry != verified:
                raise ValueError()
        else:
            evaluations = list(EvaluacionConsumo.objects.filter(solicitud=s).values_list('resultados', flat=True))
            snapshots = [e[study_key] for e in evaluations if type(e) is dict and study_key in e]
            if len(snapshots) != 1:
                raise ValueError()
            snapshot = snapshots[0]
        if type(snapshot) is not dict or snapshot.get('solicitudId') != str(s.pk) or entry['evaluacionSha256'] != hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest() or (decision['estado'] != 'APROBADO') or (decision['solicitudId'] != str(s.pk)) or (decision['versionPolitica'] != policy['version']) or (decision['referencia'] != policy['referencia']) or (decision['reglasOriginales'] != snapshot['reglas']) or (decision['decisionLinixRegistrada'] is not False) or (decision['autorizaDesembolso'] is not False):
            raise ValueError()
        return {'operacionId': entry['operacionId'], 'sha256': entry['sha256'], 'evaluacionSha256': entry['evaluacionSha256'], 'versionPolitica': decision['versionPolitica']}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('OFERTA_REQUIERE_DECISION_DIGITAL_APROBADA_DEL_ESTUDIO_VIGENTE') from None


def preparar_cotizacion_radicacion_especial(solicitud_id, operador, *, titular=None):
    """Read-only intake preparation, not a quote execution or contractual offer."""
    from .elegibilidad import leer_decision_estudio, continuidad_desde_decision
    from .estamentos import FAMILIAR, COLABORADOR
    s = _obtener(solicitud_id, operador, titular=titular)
    decision = leer_decision_estudio(s)
    if decision is None or decision['resultado']['estado'] not in {FAMILIAR, COLABORADOR} or (not continuidad_desde_decision(decision['resultado'])['estudioFavorable']):
        raise ValueError('RADICACION_ESPECIAL_REQUIERE_ESTUDIO_FAVORABLE')
    _, payload = _contexto(s, lock=False)
    return {'versionContrato': 'credihoy-cuota-preparacion-especial-v1', 'solicitudId': str(s.pk), 'decisionReferencia': decision['operacionId'], 'decisionSha256': decision['sha256'], 'revisionReferencia': decision['revisionReferencia'], 'parametros': payload, 'ofertaContractual': False, 'aceptacionContractual': False, 'ejecutaCuota': False, 'autorizaStart': False, 'autorizaFirma': False, 'autorizaDesembolso': False}


def preparar_radicacion_tecnica(solicitud_id, operador, *, cliente=None, ejecutar_cuota=True):
    """Existing scoped service preparation; never an intake intention or offer."""
    from django.conf import settings
    from uuid import uuid5, NAMESPACE_URL
    from .funcional import leer_estado_funcional
    from .condicion_comercial import resolver_desde_revision_conservada
    if type(ejecutar_cuota) is not bool:
        raise ValueError('COTIZACION_MODO_INVALIDO')
    s = _obtener(solicitud_id, operador, autogestion_tecnica=True)
    state = leer_estado_funcional(s)
    revision = state.get('revisionEstudio', {}).get('versionReferencia')
    if not revision:
        raise ValueError('RADICACION_REQUIERE_REVISION_VERIFICADA')
    if getattr(settings, 'CREDIHOY_COTIZACION_ENABLED', False) is not True:
        raise ValueError('COTIZACION_NO_HABILITADA_EN_EJECUTOR')
    selected = resolver_desde_revision_conservada(s.pk, operador, revision_referencia=revision, persistir=True)
    key = str(uuid5(NAMESPACE_URL, 'credihoy-cuota-radicacion-especial:' + str(s.pk) + ':' + revision + ':' + str(selected['seleccionId'])))
    detail, payload = _contexto(s, lock=False)
    stored = _resultado_guardado(detail, payload, key, ejecutar=False)
    transport_verified = False
    if stored['estado'] == 'SIN_COTIZACION':
        from .funcional import _firma, oferta_custodia_activa
        from ..cotizacion_cliente import ENDPOINT
        if oferta_custodia_activa():
            _exigir_decision_ofertable(s, detail)
        cliente = cliente or ApiDataCotizacionClient(ConfiguracionApiDataV2.desde_settings())
        if not cliente.configuracion.client_id.startswith('credihoy-hechos-'):
            raise ValueError('COTIZACION_REQUIERE_CLIENTE_INSTITUCIONAL_ACOTADO')
        cliente.vincular_radicacion({'solicitudId': str(s.pk), 'revisionReferencia': revision, 'titularSha256': hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest(), 'consentimientoSha256': _firma(s).text_hash, 'versionCondiciones': payload['versionCondiciones'], 'payloadSha256': hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()})
        response = cliente.session.post(cliente.configuracion.base_url.rstrip('/') + ENDPOINT, json={}, headers={'Authorization': 'Bearer ' + cliente._token_cotizacion(payload, key), 'X-Request-ID': key, 'Accept': 'application/json'}, timeout=cliente.configuracion.timeout, allow_redirects=False, verify=True)
        try:
            if response.status_code != 400 or response.headers.get('X-Request-ID') != key:
                raise ValueError('COTIZACION_CONTRATO_VACIO_NO_ACREDITADO')
            transport_verified = True
        finally:
            response.close()
    quote = cotizacion(s.pk, operador, key, ejecutar=True, cliente=cliente, autogestion_tecnica=True) if ejecutar_cuota else stored
    return {'versionContrato': 'credihoy-preparacion-radicacion-tecnica-v1', 'solicitudId': str(s.pk), 'revisionReferencia': revision, 'seleccionId': selected['seleccionId'], 'requestIdCuota': key, 'estadoCuota': quote['estado'], 'cuotaDisponible': quote.get('disponible') is True, 'fuenteCuota': quote.get('fuente'), 'versionCondiciones': quote.get('versionCondiciones'), 'soloVerificacionTransporte': not ejecutar_cuota, 'transporteVerificadoEnEstaEjecucion': transport_verified, 'ofertaGenerada': False, 'aceptacionInferida': False, 'intencionCreada': False, 'autorizaStart': False, 'autorizaFirma': False, 'autorizaDesembolso': False}


def _huella_simulacion(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def calcular_simulacion_propia(solicitud_id, operador, *, vigencia_segundos=3600):
    """Persist the existing calculator's result, without a provider or financial operation."""
    from datetime import timedelta
    from decimal import Decimal
    from .condiciones_producto import parametros_financieros
    from .funcional import leer_estado_funcional
    from ...calculadora import calcular_plan_credihoy
    from .publico_institucional import validar_simulacion_propia
    if type(vigencia_segundos) is not int or not 60 <= vigencia_segundos <= 86400:
        raise ValueError('SIMULACION_VIGENCIA_INVALIDA')
    with transaction.atomic(using=alias_cotizacion()):
        s = _obtener(solicitud_id, operador, autogestion_tecnica=True, lock=True)
        detail, context = _contexto(s)
        revision = leer_estado_funcional(s)['revisionEstudio']
        c = context['condiciones']
        policy = parametros_financieros()
        plan = calcular_plan_credihoy(Decimal(c['monto']), c['numeroCuotas'], Decimal(c['tasaNominalNamv']), perfil=(c['producto'], c['modalidad'], c['destinacion']), periodicidad_meses=1, seguro_mensual_porcentaje=Decimal(policy['seguroVidaPorcentajeMensual']), base_seguro='SALDO_INICIAL_PERIODO', factor_adicional=Decimal('0'), unidad_redondeo=Decimal('1'))
        now = timezone.now()
        body = {'version': 'credihoy-simulacion-aceptable-v1', 'solicitudId': str(s.pk), 'revisionReferencia': revision['versionReferencia'], 'versionCondiciones': context['versionCondiciones'], 'calculadoEn': now.isoformat(), 'vigenteHasta': (now + timedelta(seconds=vigencia_segundos)).isoformat(), 'fuente': 'CREDIHOY_CALCULO_PROPIO', 'monto': c['monto'], 'plazoMeses': c['numeroCuotas'], 'tasaNominalNamv': c['tasaNominalNamv'], 'periodicidad': 'MENSUAL', 'cuota': plan['cuotaCalculada'], 'seguroMensualPorcentaje': policy['seguroVidaPorcentajeMensual'], 'baseSeguro': plan['baseSeguro'], 'unidadRedondeo': plan['unidadRedondeo'], 'versionMotor': plan['version'], 'versionParametros': policy['version'], 'planAproximado': True, 'amortizacion': [{k: r[k] for k in ('periodo', 'saldoInicial', 'capital', 'intereses', 'seguro', 'otros', 'cuota', 'saldoFinal')} for r in plan['amortizacion']], 'totales': plan['totales'], 'autorizaStart': False, 'firmaContractual': False, 'aprobacionInstitucional': False}
        body['sha256'] = _huella_simulacion(body)
        validar_simulacion_propia(body, str(s.pk))
        data = detail.orchestration_data
        prior = data.get(KEY, {})
        if prior and prior.get('motor') != 'PROPIO_390_111_CFA':
            raise ValueError('COTIZACION_PREVIA_REQUIERE_CONCILIACION')
        previous = prior.get('simulacion')
        if previous and previous['versionCondiciones'] == context['versionCondiciones'] and (previous['revisionReferencia'] == revision['versionReferencia']):
            try:
                return validar_simulacion_propia(previous, str(s.pk))
            except ValueError:
                raise ValueError('SIMULACION_PREVIA_VENCIDA_O_ALTERADA_CONCILIAR') from None
        history = dict(prior.get('calculosPropios', {}))
        history[body['sha256']] = body
        entry = {**prior, 'motor': 'PROPIO_390_111_CFA', 'versionCondiciones': context['versionCondiciones'], 'titularReferencia': hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest(), 'simulacion': body, 'calculosPropios': history}
        detail.orchestration_data = {**data, KEY: entry}
        detail.save(update_fields=['orchestration_data', 'updated_at'])
        AuditLog.objects.create(solicitud=s, event='CREDIHOY_SIMULACION_PROPIA_CALCULADA', actor=str(operador.pk), request_id=str(uuid4()), payload={'simulacionSha256': body['sha256'], 'versionCondiciones': context['versionCondiciones'], 'revisionReferencia': revision['versionReferencia'], 'autorizaStart': False, 'autorizaDesembolso': False})
        return body


def _operacion_simulacion_propia(identity,payload,rid):
    """Personal simulation acceptance is not a contractual signature or Council decision."""
    from .autoservicio import _estudio_publico_personal
    from .publico_institucional import validar_simulacion_propia
    op=payload.get('operacion');expected={'solicitudId','operacion'}
    if op=='aceptar_simulacion':expected|={'simulacionSha256','acepta'}
    if set(payload)!=expected or op not in {'consultar','aceptar_simulacion'}:
        raise ValueError('PETICION_SIMULACION_INVALIDA')
    sid=str(UUID(payload['solicitudId']));rid=str(UUID(rid))
    s=_obtener(sid,None,titular=identity)
    # The signed study reader manages its own READ ONLY transaction.
    study=_estudio_publico_personal(s,identity)
    if not study or not study.get('decisionPolitica',{}).get('continuidad',{}).get('estudioFavorable'):
        raise ValueError('ESTUDIO_FAVORABLE_REQUERIDO')
    with transaction.atomic(using=alias_cotizacion()):
        s=_obtener(sid,None,titular=identity)
        detail,context=_contexto(s)
        entry=detail.orchestration_data.get(KEY,{})
        holder=hashlib.sha256(identity['numeroDocumento'].encode()).hexdigest()
        sim,accepted=_simulacion_persistida_para_continuidad(entry,sid,holder)
        if (entry.get('motor')!='PROPIO_390_111_CFA' or entry.get('titularReferencia')!=holder
                or sim['versionCondiciones']!=context['versionCondiciones']
                or sim['revisionReferencia']!=study['referenciaVersion']):
            raise ValueError('SIMULACION_DESACTUALIZADA_O_DE_OTRO_TITULAR')
        history=dict(entry.get('aceptacionesSimulacion',{}))
        if op=='aceptar_simulacion':
            if payload['acepta'] is not True or payload['simulacionSha256']!=sim['sha256']:
                raise ValueError('ACEPTACION_DE_OTRA_SIMULACION')
            if accepted is None:
                accepted={'solicitudId':sid,'simulacionSha256':sim['sha256'],
                    'versionCondiciones':sim['versionCondiciones'],'revisionReferencia':sim['revisionReferencia'],
                    'titularReferencia':holder,'sesionReferencia':identity['_sesionReferencia'],
                    'fecha':timezone.now().isoformat(),'requestId':rid,'acepta':True,
                    'textoVersion':'credihoy-aceptacion-simulacion-v1','firmaContractual':False,
                    'autorizaStart':False,'autorizaDesembolso':False}
                accepted['sha256']=_huella_simulacion(accepted)
                _validar_aceptacion_simulacion(sim,accepted,holder)
                history[sim['sha256']]=accepted
                detail.orchestration_data={**detail.orchestration_data,KEY:{**entry,'aceptacionesSimulacion':history}}
                detail.save(update_fields=['orchestration_data','updated_at'])
                AuditLog.objects.create(solicitud=s,event='CREDIHOY_SIMULACION_ACEPTADA_TITULAR',
                    actor=_actor(None,identity),request_id=rid,payload={'simulacionSha256':sim['sha256'],
                        'aceptacionSha256':accepted['sha256'],'autorizaStart':False,'autorizaDesembolso':False})
        return {'versionContrato':'credihoy-cotizacion-titular-v1','solicitudId':sid,'estado':'SIMULACION_PROPIA',
            'cuota':None,'oferta':None,'aceptacion':{'aceptada':False,'fecha':None},
            'simulacion':sim,'aceptacionSimulacion':{'aceptada':accepted is not None,'fecha':accepted['fecha'] if accepted else None},
            'autorizaStart':False,'autorizaDesembolso':False}


def _validar_aceptacion_simulacion(sim,receipt,holder):
    try:
        body={k:v for k,v in receipt.items() if k!='sha256'}
        when=datetime.fromisoformat(receipt['fecha'])
        if (set(receipt)!={'solicitudId','simulacionSha256','versionCondiciones','revisionReferencia','titularReferencia',
                'sesionReferencia','fecha','requestId','acepta','textoVersion','firmaContractual','autorizaStart','autorizaDesembolso','sha256'}
                or _huella_simulacion(body)!=receipt['sha256'] or receipt['solicitudId']!=sim['solicitudId']
                or receipt['simulacionSha256']!=sim['sha256'] or receipt['versionCondiciones']!=sim['versionCondiciones']
                or receipt['revisionReferencia']!=sim['revisionReferencia'] or receipt['titularReferencia']!=holder
                or receipt['acepta'] is not True or receipt['textoVersion']!='credihoy-aceptacion-simulacion-v1'
                or any(receipt[k] is not False for k in ('firmaContractual','autorizaStart','autorizaDesembolso'))
                or str(UUID(receipt['sesionReferencia']))!=receipt['sesionReferencia']
                or str(UUID(receipt['requestId']))!=receipt['requestId'] or when.utcoffset() is None
                or not datetime.fromisoformat(sim['calculadoEn'])<=when<datetime.fromisoformat(sim['vigenteHasta'])
                or when>timezone.now()):raise ValueError()
    except (TypeError,ValueError,KeyError,AttributeError):raise ValueError('ACEPTACION_SIMULACION_NO_INTEGRA') from None
    return receipt


def aplicar_simulacion_aceptada_a_contexto(contexto,s):
    """Feed the existing special-intake gate, without manufacturing a contractual offer."""
    from dataclasses import replace
    from .publico_institucional import validar_simulacion_propia
    from .funcional import leer_estado_funcional
    detail,context=_contexto(s,lock=False)
    entry=detail.orchestration_data.get(KEY,{})
    holder=hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest()
    sim,accepted=_simulacion_persistida_para_continuidad(entry,str(s.pk),holder)
    if (contexto.solicitud_id!=str(s.pk) or contexto.titular_referencia!=holder
            or entry.get('titularReferencia')!=holder or sim['versionCondiciones']!=context['versionCondiciones']
            or sim['revisionReferencia']!=leer_estado_funcional(s)['revisionEstudio']['versionReferencia']):
        raise ValueError('CONTEXTO_NO_CORRESPONDE_A_SIMULACION')
    if accepted is None:raise ValueError('ACEPTACION_PERSONAL_SIMULACION_REQUERIDA')
    _validar_aceptacion_simulacion(sim,accepted,holder)
    # Offer/contractual-acceptance fields deliberately remain unchanged.
    return replace(contexto,cuota_contractual=sim['cuota'])


def _simulacion_persistida_para_continuidad(entry,sid,holder):
    """An accepted quote is checked at acceptance, without extending its TTL."""
    from .publico_institucional import validar_simulacion_propia
    try:
        raw=entry['simulacion']
        accepted=entry.get('aceptacionesSimulacion',{}).get(raw['sha256'])
        if accepted is not None:
            _validar_aceptacion_simulacion(raw,accepted,holder)
            sim=validar_simulacion_propia(raw,sid,ahora=datetime.fromisoformat(accepted['fecha']))
        else:sim=validar_simulacion_propia(raw,sid)
    except (KeyError,TypeError,AttributeError):raise ValueError('SIMULACION_PERSISTIDA_NO_INTEGRA') from None
    return sim,accepted


def comprobante_simulacion_aceptada(s,identity):
    """Owner-only durable evidence. Does not authorize START or human approval."""
    from .elegibilidad import leer_decision_estudio
    from .funcional import leer_estado_funcional
    holder=hashlib.sha256(identity['numeroDocumento'].encode()).hexdigest()
    if s.solicitante.numero_identificacion!=identity['numeroDocumento']:
        raise ValueError('ACEPTACION_DE_OTRO_TITULAR')
    detail,context=_contexto(s,lock=False)
    entry=detail.orchestration_data.get(KEY,{})
    sim,accepted=_simulacion_persistida_para_continuidad(entry,str(s.pk),holder)
    if accepted is None:raise ValueError('ACEPTACION_PERSONAL_SIMULACION_REQUERIDA')
    revision=leer_estado_funcional(s)['revisionEstudio']['versionReferencia']
    if (entry.get('motor')!='PROPIO_390_111_CFA' or entry.get('titularReferencia')!=holder
            or sim['versionCondiciones']!=context['versionCondiciones'] or sim['revisionReferencia']!=revision):
        raise ValueError('CONDICIONES_ACEPTADAS_NO_CORRESPONDEN_AL_EXPEDIENTE')
    rows=list(AuditLog.objects.filter(solicitud=s,event='CREDIHOY_SIMULACION_ACEPTADA_TITULAR',
        request_id=accepted['requestId']).values('id','actor','payload')[:2])
    if (len(rows)!=1 or rows[0]['actor']!='titular:'+accepted['sesionReferencia']
            or rows[0]['payload']!={'simulacionSha256':sim['sha256'],'aceptacionSha256':accepted['sha256'],
                'autorizaStart':False,'autorizaDesembolso':False}):
        raise ValueError('ACEPTACION_PERSONAL_SIN_AUDITORIA_VINCULADA')
    decision=leer_decision_estudio(s)
    if decision is None or decision['revisionReferencia']!=revision:
        raise ValueError('DECISION_NO_CORRESPONDE_A_REVISION_ACEPTADA')
    return {'solicitudId':str(s.pk),'titularReferencia':holder,'revisionReferencia':revision,
        'decisionReferencia':decision['operacionId'],'decisionSha256':decision['sha256'],
        'clasificacionRegistrada':decision['resultado']['estado'],'versionCondiciones':sim['versionCondiciones'],
        'simulacionSha256':sim['sha256'],'aceptacionSha256':accepted['sha256'],
        'aceptacionAuditoriaId':rows[0]['id'],'aceptadaEn':accepted['fecha'],
        'monto':sim['monto'],'plazoMeses':sim['plazoMeses'],'periodicidad':sim['periodicidad'],
        'tasaNominalNamv':sim['tasaNominalNamv'],'cuota':sim['cuota'],'autorizaStart':False}
