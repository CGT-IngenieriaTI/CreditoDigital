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
