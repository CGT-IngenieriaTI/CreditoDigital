"""Explicit quote only. No scoring, provider refresh, financial formula or radication."""
import hashlib
import json
from uuid import UUID
from datetime import datetime
from django.db import transaction
from django.db import connection
from django.utils import timezone
from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
from apps.xcore_consumo.models import SolicitudConsumo
from apps.utils.models import AuditLog
from ..cliente import ConfiguracionApiDataV2
from ..errores_http import ApiDataCrediHoyV2Error
from ..cotizacion_cliente import ApiDataCotizacionClient, VERSION
from ..pagaduria import adaptar_pagaduria
from .condicion_comercial import resolver_condicion, pagaduria_seleccion
from .consentimiento_real import obtener
from .funcional import _firma, _formulario_persistido

KEY = 'credihoy_cotizacion_institucional'
KEY_MANUAL = KEY + '_manual'
KEY_DYNAMIC = KEY + '_escenarios_v1'
MOTIVOS = {'COTIZACION_RECOGIDA_NO_CERTIFICADA', 'SELECCION_COMERCIAL_REQUERIDA',
    'EVIDENCIA_COMERCIAL_INVALIDA', 'CONDICIONES_COMERCIALES_NO_VIGENTES',
    'CONSENTIMIENTO_FIRMADO_VERIFICABLE_REQUERIDO', 'DATOS_SOLICITUD_INVALIDOS',
    'PARAMETROS_DISTINTOS_DEL_EXPEDIENTE_EXISTENTE'}


def _contexto(s, *, lock=True):
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
            or p.codigo != '100' or p.tipo != 'N' or p.activa is not True):
        raise ValueError('CONDICIONES_COMERCIALES_NO_VIGENTES')
    conditions = {'producto': '390', 'modalidad': '111', 'destinacion': 'CFA',
        'pagaduria': p.codigo, 'tipoPagaduria': p.tipo, 'monto': form['monto'],
        'numeroCuotas': form['plazoMeses'], 'tasaNominalNamv': current['tasaComercialNamv'],
        'periodicidad': form['periodicidadPago'], 'amortizacion': 'CI', 'sinRecogida': True}
    fingerprint = hashlib.sha256(json.dumps({'condiciones': conditions,
        'seleccion': selected.pk, 'evidencia': digest,
        'tarifa': {k: current.get(k) for k in keys}}, sort_keys=True).encode()).hexdigest()
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
    with transaction.atomic():
        detail, payload = _contexto(s, lock=False)
        result = _resultado_guardado(detail, payload, rid, ejecutar=False)
        return validar_respuesta(result, payload, rid)


def cotizacion(solicitud_id, operador, request_id, *, ejecutar=False, cliente=None, escenario=None, sesion=None, dinamica=False):
    if dinamica:
        return simulacion_dinamica(solicitud_id,operador,request_id,escenario=escenario,
            sesion=sesion,ejecutar=ejecutar,cliente=cliente)
    if connection.in_atomic_block:
        raise ValueError('COTIZACION_REQUIERE_TRANSACCION_INDEPENDIENTE')
    rid = str(UUID(request_id))
    with transaction.atomic():
        s = obtener(solicitud_id, operador, lock=True)
        try:
            detail, payload = _contexto(s)
        except (ValueError, KeyError, AttributeError) as exc:
            r = _empty(s.pk, rid, 'PARAMETROS_EXPEDIENTE_INCOMPLETOS')
            r['motivo'] = str(exc) if str(exc) in MOTIVOS else 'EVIDENCIA_INCOMPLETA'
            return r
        stored = _resultado_guardado(detail, payload, rid, ejecutar=ejecutar)
        if stored is not None:
            return stored
        try:
            client = cliente or ApiDataCotizacionClient(ConfiguracionApiDataV2.desde_settings())
        except ApiDataCrediHoyV2Error:
            r = _empty(s.pk, rid, 'NO_HABILITADA', payload)
            r['motivo'] = 'CONFIGURACION_API_DATA_INCOMPLETA'
            return r
        # Commit the claim before HTTP; a crash cannot trigger an automatic retry.
        detail.orchestration_data = {**detail.orchestration_data, KEY: {
            **payload, 'requestId': rid, 'resultado': None}}
        detail.save(update_fields=['orchestration_data', 'updated_at'])
        AuditLog.objects.create(solicitud=s, event='CREDIHOY_COTIZACION_SOLICITADA',
            actor=str(operador.pk), request_id=rid, payload={'versionCondiciones': payload['versionCondiciones']})
    try:
        result = client.cotizar(payload, rid)
    except Exception:
        return _empty(s.pk, rid, 'REQUIERE_CONCILIACION', payload)
    with transaction.atomic():
        s = obtener(solicitud_id, operador, lock=True)
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        entry = detail.orchestration_data.get(KEY, {})
        if entry.get('requestId') != rid:
            return _empty(s.pk, rid, 'REQUIERE_CONCILIACION', payload)
        detail.orchestration_data = {**detail.orchestration_data, KEY: {**entry, 'resultado': result}}
        detail.save(update_fields=['orchestration_data', 'updated_at'])
        AuditLog.objects.create(solicitud=s, event='CREDIHOY_COTIZACION_RESPUESTA',
            actor=str(operador.pk), request_id=rid,
            payload={'versionCondiciones': payload['versionCondiciones'], 'estado': result['estado']})
        try:
            _, current = _contexto(s)
            if current != payload:
                return _empty(s.pk, rid, 'DESACTUALIZADA', current)
        except (ValueError, KeyError, AttributeError):
            return _empty(s.pk, rid, 'DESACTUALIZADA')
        return result


def limites_simulador():
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
    return dict(limits)


def disponibilidad_simulador():
    try:return {'habilitado':True,'limites':limites_simulador(),'motivo':None}
    except ValueError as exc:return {'habilitado':False,'limites':None,'motivo':str(exc)}


def _escenario_payload(s, escenario, sesion):
    from decimal import Decimal
    import re
    limits=limites_simulador()
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
        s=obtener(solicitud_id,operador,lock=True)
        limites_simulador()
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
        s=obtener(solicitud_id,operador,lock=True)
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
        s=obtener(solicitud_id,operador,lock=True)
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
