"""Proyeccion local de evidencia conciliada; nunca llama ni avanza LINIX."""
import hashlib
import json
import re
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from apps.xcore_consumo.services.otp_crypto import decrypt_text, encrypt_text

KEY = 'credihoy_linix_administrativo'
VERSION = 'credihoy-linix-estado-v1'
STATE = 'FLUJO_CREADO_INCIDENCIA_REFERENCIAS'
VERSION_INSTITUCIONAL = 'credihoy-linix-estado-v2'
POSTFIRMA_KEY=KEY+'_postfirma_nativa'
FINANCIERO_KEY=KEY+'_conciliacion_financiera'


def validar_financiero(p,s,base,*,ahora=None):
    """Accept the existing reconciler's summary, never a task result as payment."""
    try:
        when=parse_datetime(p['observadoEn'])
        expected=hashlib.sha256(json.dumps({k:v for k,v in p.items() if k!='sha256'},
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if (p['versionContrato']!='credihoy-conciliacion-financiera-v1' or p['fuente']!='LINIX'
                or p['solicitudId']!=str(s.pk) or p['intencionId']!=base['intencionId']
                or p['K_FLUJO']!=base['kFlujo'] or p['SC']!=base['numeroSc']
                or p['titularReferencia']!=hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest()
                or p['pagare']!=base['pagare']['numero'] or p['sha256']!=expected
                or not re.fullmatch('[a-f0-9]{64}',p['fuentesSha256'])
                or when is None or when.utcoffset() is None
                or (ahora is not None and not 0<=(ahora-when).total_seconds()<=300)
                or p['autorizaDesembolso'] is not False or p['transporteHabilitado'] is not False):raise ValueError()
        for v in (p['liquidacion']['acreditada'],p['contabilizacion']['acreditada'],
                p['recogida']['compensacionAcreditada'],
                p['neto']['acreditado'],p['desembolso']['acreditado']):
            if type(v) is not bool:raise ValueError()
        if p['recogida']['requerida'] is not None and type(p['recogida']['requerida']) is not bool:raise ValueError()
        if p['contabilizacion']['acreditada'] and not p['liquidacion']['acreditada']:raise ValueError()
        paid=p['desembolso']['acreditado']
        if paid:
            payment=p['conciliacionDesembolso']
            digest=hashlib.sha256(json.dumps({k:v for k,v in payment.items() if k!='sha256'},
                sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if (not p['liquidacion']['acreditada'] or not p['contabilizacion']['acreditada']
                    or p['ordenPago']['estado'] not in {'GENERADA','NO_APLICA'}
                    or p['ordenPago']['configuracionCompatible'] is not True
                    or not p['neto']['acreditado'] or p['neto']['valor'] is None or p['pendientes']
                    or p['recogida']['requerida'] is None
                    or (p['recogida']['requerida'] and not p['recogida']['compensacionAcreditada'])
                    or payment['sha256']!=digest or payment['desembolso']!=p['desembolso']
                    or not payment['desembolso']['referencia'] or payment['desembolso']['cantidad']!=1
                    or payment['valorNeto']!=p['neto']['valor']
                    or any(payment[k]!=p[k] for k in ('solicitudId','K_FLUJO','SC','titularReferencia','pagare'))):raise ValueError()
        elif (p['neto']['acreditado'] or p['neto']['valor'] is not None or p['conciliacionDesembolso'] is not None
                or p['desembolso']!={'acreditado':False,'referencia':None,'cantidad':0}):raise ValueError()
        return p
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError('CONCILIACION_FINANCIERA_NO_CORRESPONDE_AL_EXPEDIENTE') from None


def sincronizar_financiero(p,operador):
    from django.utils import timezone
    from django.core.exceptions import PermissionDenied
    from .consentimiento_real import obtener
    if not operador.has_perm('solicitudes.view_solicitud'):raise PermissionDenied
    with transaction.atomic():
        s=obtener(p['solicitudId'],operador,lock=True)
        detail=SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        data=detail.orchestration_data;_comprobar_titular(s,data)
        base=data.get(KEY)
        if not base or base.get('versionContrato')!=VERSION_INSTITUCIONAL:
            raise ValueError('APROBACION_CONCILIADA_PREVIA_REQUERIDA')
        validar_institucional(base,s.pk);validar_financiero(p,s,base,ahora=timezone.now())
        prior=data.get(FINANCIERO_KEY)
        if prior and prior['sha256']==p['sha256']:return json.loads(json.dumps(prior))
        if prior and parse_datetime(prior['observadoEn'])>=parse_datetime(p['observadoEn']):
            raise ValueError('CONCILIACION_ANTERIOR_NO_SOBRESCRIBIR')
        detail.orchestration_data={**data,FINANCIERO_KEY:p,
            FINANCIERO_KEY+'_historial':[*data.get(FINANCIERO_KEY+'_historial',[]),*([prior] if prior else [])]}
        detail.save(update_fields=['orchestration_data','updated_at'])
        AuditLog.objects.create(solicitud=s,event='credihoy_linix_financiero_conciliado',actor=str(operador.pk),
            request_id=p['sha256'][:32],payload={'sha256':p['sha256'],
                'liquidada':p['liquidacion']['acreditada'],'contabilizada':p['contabilizacion']['acreditada'],
                'desembolsoAcreditado':p['desembolso']['acreditado']})
        return json.loads(json.dumps(p))


def presentar_financiero(s):
    data=SolicitudConsumo.objects.get(solicitud=s).orchestration_data
    p=data.get(FINANCIERO_KEY)
    if p is None:return None
    _comprobar_titular(s,data);validar_institucional(data[KEY],s.pk)
    validar_financiero(p,s,data[KEY])
    return json.loads(json.dumps(p))


def validar_liberacion_nativa(p,s,base,*,ahora=None):
    """Same reader contract, bound to the already reconciled dossier and note."""
    from django.utils import timezone
    try:
        when=parse_datetime(p['observadoEn'])
        firma=p['firma'];body={k:v for k,v in p.items() if k!='sha256'}
        expected=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        fields={'versionContrato','fuente','solicitudId','intencionId','K_FLUJO','SC','titularReferencia','pagare',
            'observadoEn','aprobada','firma','liquidacion','desembolso','tareasActivas','autorizaEjecucionFinanciera','START','sha256'}
        if (set(p)!=fields or p['versionContrato']!='credihoy-linix-liberacion-v1' or p['fuente']!='LINIX'
                or p['solicitudId']!=str(s.pk) or p['intencionId']!=base['intencionId']
                or p['K_FLUJO']!=base['kFlujo'] or p['SC']!=base['numeroSc']
                or p['titularReferencia']!=hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest()
                or p['pagare']!=base['pagare']['numero'] or p['sha256']!=expected
                or when is None or when.utcoffset() is None or when>timezone.now()
                or (ahora is not None and not 0<=(ahora-when).total_seconds()<=300)
                or p['autorizaEjecucionFinanciera'] is not False or type(p['START']) is not int or p['START']!=0
                or type(p['aprobada']) is not bool or type(p['liquidacion']['liquidada']) is not bool
                or (p['liquidacion']['liquidada'] and not p['liquidacion']['fecha'])
                or type(firma['acreditadaEnLINIX']) is not bool or type(firma['liberadaPorWorkflow']) is not bool
                or firma['archivoFirmadoRecuperado'] is not False
                or p['desembolso']!={'acreditado':False,'referencia':None}
                or p['desembolso']['acreditado'] is not False):raise ValueError()
        if firma['acreditadaEnLINIX']:
            service=firma['evidenciaServicio']
            if (firma['mecanismo']!='ELECTRONICA' or firma['estadoInstitucional']!='DOCUMENTO_FIRMADO'
                    or service['usuario']!='SERVIC' or service['actividad'] not in {99001086,99001088}
                    or service['respuesta']!='DOCUMENTO_FIRMADO'
                    or not re.fullmatch('[1-9][0-9]*',service['K_SERIE'])
                    or not re.fullmatch('[a-f0-9]{64}',service['repositorioSalidaSha256'])):raise ValueError()
        elif firma['liberadaPorWorkflow']:raise ValueError()
        if type(p['tareasActivas']) is not list or len(p['tareasActivas'])>128:raise ValueError()
        series=set()
        for task in p['tareasActivas']:
            if (set(task)!={'actividad','K_SERIE','ruta','usuario'} or type(task['actividad']) is not int
                    or type(task['ruta']) is not int or not re.fullmatch('[1-9][0-9]*',task['K_SERIE'])
                    or task['K_SERIE'] in series):raise ValueError()
            series.add(task['K_SERIE'])
        if firma['liberadaPorWorkflow'] and any(99001065<=t['actividad']<=99001091 for t in p['tareasActivas']):
            raise ValueError()
        return p
    except (KeyError,ValueError,TypeError,AttributeError):
        raise ValueError('LIBERACION_NATIVA_NO_CORRESPONDE_AL_EXPEDIENTE') from None


def sincronizar_liberacion_nativa(p,operador):
    """Append native LINPAG observation, without rewriting approval or decisions."""
    from django.utils import timezone
    from django.core.exceptions import PermissionDenied
    from .consentimiento_real import obtener
    if not operador.has_perm('solicitudes.view_solicitud'):raise PermissionDenied
    with transaction.atomic():
        s=obtener(p['solicitudId'],operador,lock=True)
        detail=SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        data=detail.orchestration_data;_comprobar_titular(s,data)
        base=data.get(KEY)
        if not base or base.get('versionContrato')!=VERSION_INSTITUCIONAL:
            raise ValueError('APROBACION_CONCILIADA_PREVIA_REQUERIDA')
        validar_institucional(base,s.pk)
        validar_liberacion_nativa(p,s,base,ahora=timezone.now())
        previous=data.get(POSTFIRMA_KEY)
        if previous and previous['sha256']==p['sha256']:return presentar_liberacion_nativa(s)
        if previous and parse_datetime(previous['observadoEn'])>=parse_datetime(p['observadoEn']):
            raise ValueError('CONCILIACION_ANTERIOR_NO_SOBRESCRIBIR')
        detail.orchestration_data={**data,POSTFIRMA_KEY:p,
            POSTFIRMA_KEY+'_historial':[*data.get(POSTFIRMA_KEY+'_historial',[]),*([previous] if previous else [])]}
        detail.save(update_fields=['orchestration_data','updated_at'])
        AuditLog.objects.create(solicitud=s,event='credihoy_linpag_estado_conciliado',actor=str(operador.pk),
            request_id=p['sha256'][:32],payload={'sha256':p['sha256'],'firmaAcreditadaEnLINIX':p['firma']['acreditadaEnLINIX'],
                'liquidada':p['liquidacion']['liquidada'],'autorizaEjecucionFinanciera':False})
        return presentar_liberacion_nativa(s)


def presentar_liberacion_nativa(s):
    detail=SolicitudConsumo.objects.get(solicitud=s);data=detail.orchestration_data
    p=data.get(POSTFIRMA_KEY)
    if p is None:return None
    _comprobar_titular(s,data);validar_institucional(data[KEY],s.pk)
    validar_liberacion_nativa(p,s,data[KEY])
    return json.loads(json.dumps(p))


def validar(p, solicitud_id):
    if isinstance(p,dict) and p.get('versionContrato')==VERSION_INSTITUCIONAL:
        return validar_institucional(p,solicitud_id)
    fields = {'versionContrato', 'solicitudId', 'intencionId', 'kFlujo', 'numeroSc',
              'estado', 'actividad', 'actualizadoAt', 'startEjecutados', 'evidenciaSha256', 'detalleTecnico'}
    if (not isinstance(p, dict) or set(p) != fields or p['versionContrato'] != VERSION
            or p['solicitudId'] != str(solicitud_id) or p['estado'] != STATE
            or type(p['startEjecutados']) is not int or p['startEjecutados'] != 1
            or p['actividad'] != {'codigo': 3883, 'nombre': 'REGISTRAR REFERENCIAS SOLICITUD'}
            or p['numeroSc'] is not None
            or not isinstance(p['kFlujo'], str) or not re.fullmatch(r'[1-9][0-9]{0,29}', p['kFlujo'])
            or not isinstance(p['evidenciaSha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', p['evidenciaSha256'])):
        raise ValueError('ESTADO_LINIX_NO_ACREDITADO')
    if str(UUID(p['intencionId'])) != p['intencionId']:
        raise ValueError('INTENCION_LINIX_INVALIDA')
    fecha = parse_datetime(p['actualizadoAt'])
    if fecha is None or fecha.utcoffset() is None:
        raise ValueError('FECHA_CONCILIACION_INVALIDA')
    if p['detalleTecnico'] != {
        'codigo': 'ORA-20000', 'procedimiento': 'PK_CA_WF_REFERENCIA.PR_INSERTA_REFER_SOLICITUD',
        'mensaje': 'Leyendo Variables Trama', 'erroresRegistrados': 2,
    }:
        raise ValueError('ERROR_LINIX_NO_SANEADO')
    return p


def validar_institucional(p,solicitud_id):
    from django.utils import timezone
    from django.utils.dateparse import parse_date
    fields={'versionContrato','solicitudId','intencionId','kFlujo','numeroSc','estado','actividad',
            'actualizadoAt','startEjecutados','evidenciaSha256','aprobacion','liquidacion','pagare','firma','procedencia','incidencias'}
    try:
        date=parse_datetime(p['actualizadoAt']);approved=parse_date(p['aprobacion']['fecha'])
        act=p['actividad'];a=p['aprobacion'];note=p['pagare']
        incidents=p['incidencias']
        positive=lambda v:type(v) is str and re.fullmatch(r'[1-9][0-9]{0,29}',v)
        if (set(p)!=fields or p['versionContrato']!=VERSION_INSTITUCIONAL or p['solicitudId']!=str(solicitud_id)
                or str(UUID(p['solicitudId']))!=p['solicitudId'] or str(UUID(p['intencionId']))!=p['intencionId']
                or p['estado']!='APROBADA_EN_LINIX_PENDIENTE_FIRMA' or type(p['startEjecutados']) is not int
                or p['startEjecutados']!=1 or not positive(p['kFlujo']) or not positive(p['numeroSc'])
                or date is None or date.utcoffset() is None or date>timezone.now()
                or approved is None or approved>date.date()
                or set(act)!={'codigo','nombre','kSerie','ruta','estado','tipoEjecucion'}
                or type(act['codigo']) is not int or act['codigo']!=99001065 or act['ruta']!='10003122' or act['estado']!='A'
                or act['tipoEjecucion']!='H' or not positive(act['kSerie'])
                or type(act['nombre']) is not str or not 1<=len(act['nombre'])<=200
                or set(a)!={'estadoSolicitud','aprobada','fecha','aprobadorReferencia','actividad','kSerie','estadoActividad'}
                or a['estadoSolicitud']!='A' or a['aprobada'] is not True or type(a['actividad']) is not int or a['actividad']!=6774
                or a['estadoActividad']!='E' or not positive(a['kSerie']) or a['kSerie']==act['kSerie']
                or not re.fullmatch(r'[A-Za-z0-9_.$#-]{1,30}',a['aprobadorReferencia'])
                or set(note)!={'numero','tipo','sucursal','estado'} or not all(positive(note[k]) for k in ('numero','tipo','sucursal'))
                or note['estado']!='U' or p['liquidacion']!={'liquidada':False,'fecha':None}
                or p['liquidacion']['liquidada'] is not False
                or p['firma']!={'estado':'PENDIENTE','acreditada':False} or p['firma']['acreditada'] is not False
                or set(incidents)!={'registradas','enTareasActivas','enTareasEjecutadas'}
                or not (all(v is None for v in incidents.values()) or
                        (all(type(v) is int and v>=0 for v in incidents.values()) and
                         incidents['registradas']==incidents['enTareasActivas']+incidents['enTareasEjecutadas']))
                or p['procedencia']!={'sistema':'LINIX','metodo':'SELECT_CA090_WORKFLOW_CA662','perfil':['390','111','CFA']}
                or not re.fullmatch(r'[a-f0-9]{64}',p['evidenciaSha256'])
                or _hash({k:v for k,v in p.items() if k!='evidenciaSha256'})!=p['evidenciaSha256']):
            raise ValueError
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError('ESTADO_INSTITUCIONAL_NO_ACREDITADO') from None
    return p


def presentar(s, *, tecnico=False):
    _comprobar_titular(s, s.consumo_detail.orchestration_data)
    p = s.consumo_detail.orchestration_data.get(KEY)
    if p is None:
        return None
    if p.get('versionContrato')==VERSION_INSTITUCIONAL:
        validar_institucional(p,s.pk)
        return json.loads(json.dumps(p))
    safe = {k: p[k] for k in ('versionContrato', 'solicitudId', 'intencionId', 'kFlujo', 'numeroSc',
            'estado', 'actividad', 'actualizadoAt', 'startEjecutados')}
    if safe['solicitudId'] != str(s.pk):
        raise ValueError('ESTADO_LINIX_DE_OTRO_EXPEDIENTE')
    if tecnico:
        safe['detalleTecnico'] = json.loads(decrypt_text(p['detalleTecnicoCifrado']))
    return safe


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _huella_titular(s):
    # Integrity of the FK/document binding, not authentication. Reader secrets are independent.
    return _hash({'ambito': 'credihoy-linix-titular-v1', 'solicitudId': str(s.pk),
        'solicitanteId': str(s.solicitante_id), 'tipo': s.solicitante.tipo_identificacion,
        'documento': s.solicitante.numero_identificacion})


def _comprobar_titular(s, data):
    owner = data.get(KEY + '_titular')
    if owner is not None and (not isinstance(owner, dict)
            or owner.get('solicitudId') != str(s.pk)
            or owner.get('solicitanteId') != str(s.solicitante_id)
            or owner.get('identidadHuella') != _huella_titular(s)):
        raise ValueError('TITULAR_VINCULADO_NO_SOBRESCRIBIR')


def _comprobar_vinculo(p, s, data):
    """One reconciled operation belongs to one dossier, including earlier snapshots."""
    _comprobar_titular(s, data)
    keys = ('intencionId', 'kFlujo', 'numeroSc')
    for prior in [data.get(KEY), *data.get(KEY + '_historial', [])]:
        if not prior:
            continue
        if (prior['intencionId'], prior['kFlujo']) != (p['intencionId'], p['kFlujo']):
            raise ValueError('FLUJO_EXISTENTE_NO_SOBRESCRIBIR')
        if prior.get('numeroSc') and prior['numeroSc'] != p.get('numeroSc'):
            raise ValueError('SC_EXISTENTE_NO_SOBRESCRIBIR')
    query = Q()
    for key in keys:
        if p.get(key):
            query |= Q(**{'orchestration_data__' + KEY + '__' + key: p[key]})
    others = SolicitudConsumo.objects.exclude(solicitud_id=s.pk)
    if others.filter(query).exists():
        raise ValueError('OPERACION_LINIX_VINCULADA_A_OTRO_EXPEDIENTE')
    # Older versions did not journal the SC in AuditLog; inspect preserved history too.
    for other in others.filter(**{'orchestration_data__has_key': KEY + '_historial'}).iterator():
        for prior in other.orchestration_data[KEY + '_historial']:
            if any(p.get(key) and prior.get(key) == p[key] for key in keys):
                raise ValueError('OPERACION_LINIX_VINCULADA_A_OTRO_EXPEDIENTE')


def sincronizar(p, operador):
    """Solo comando administrativo: actualiza metadata, no evaluacion ni solicitud."""
    if type(p) is dict and p.get('versionContrato')=='credihoy-conciliacion-financiera-v1':
        result=sincronizar_financiero(p,operador)
        return {'estado':'FINANCIERO_CONCILIADO_SIN_EJECUCION','kFlujo':result['K_FLUJO'],
            'numeroSc':result['SC'],'startEjecutados':1}
    if type(p) is dict and p.get('versionContrato')=='credihoy-linix-liberacion-v1':
        result=sincronizar_liberacion_nativa(p,operador)
        return {'estado':'LECTURA_NATIVA_CONCILIADA','kFlujo':result['K_FLUJO'],
            'numeroSc':result['SC'],'startEjecutados':1}
    from .consentimiento_real import obtener
    validar(p, p.get('solicitudId'))
    if p['versionContrato']==VERSION_INSTITUCIONAL and not operador.has_perm('solicitudes.view_solicitud'):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    with transaction.atomic():
        # Serialize ownership checks across dossiers, not just updates of the same row.
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int.from_bytes(b'CDVINCUL', 'big')])
        s = obtener(p['solicitudId'], operador, lock=True)
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        evaluaciones = EvaluacionConsumo.objects.select_for_update().filter(solicitud=s)
        original = _hash(list(evaluaciones.values()))
        _comprobar_vinculo(p, s, detail.orchestration_data)
        prior = detail.orchestration_data.get(KEY)
        if prior:
            if prior['evidenciaSha256'] == p['evidenciaSha256']:
                return presentar(s, tecnico=True)
            if parse_datetime(prior['actualizadoAt']) >= parse_datetime(p['actualizadoAt']):
                raise ValueError('CONCILIACION_ANTERIOR_NO_SOBRESCRIBIR')
        stored = {k: v for k, v in p.items() if k != 'detalleTecnico'}
        if 'detalleTecnico' in p:
            stored['detalleTecnicoCifrado'] = encrypt_text(json.dumps(p['detalleTecnico']))
        history=detail.orchestration_data.get(KEY+'_historial',[])
        if prior:
            history=[*history,prior]
        owner = detail.orchestration_data.get(KEY + '_titular') or {
            'solicitudId': str(s.pk), 'solicitanteId': str(s.solicitante_id),
            'identidadHuella': _huella_titular(s),
            'asesorOrigenId': str(s.asesor_id), 'registradoEn': p['actualizadoAt'],
            'metodo': 'FK_EXPEDIENTE_Y_OPERADOR_ASIGNADO',
            'identidadSesionAsociadoAcreditada': False,
        }
        detail.orchestration_data = {**detail.orchestration_data, KEY: stored,
            KEY+'_historial':history, KEY+'_titular':owner}
        detail.save(update_fields=['orchestration_data', 'updated_at'])
        if _hash(list(evaluaciones.values())) != original:
            raise ValueError('EVALUACION_CAMBIO_DURANTE_CONCILIACION')
        AuditLog.objects.create(solicitud=s, event='credihoy_linix_estado_conciliado',
            actor=f'user:{operador.pk}', payload={'intencionId': p['intencionId'], 'kFlujo': p['kFlujo'],
                'numeroSc': p['numeroSc'], 'solicitudId': str(s.pk),
                'estado': p['estado'], 'evidenciaSha256': p['evidenciaSha256']})
        s.consumo_detail = detail
        return presentar(s, tecnico=True)
