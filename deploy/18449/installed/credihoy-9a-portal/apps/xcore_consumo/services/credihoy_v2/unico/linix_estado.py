"""Proyeccion local de evidencia conciliada; nunca llama ni avanza LINIX."""
import hashlib
import json
import re
from uuid import UUID

from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from apps.xcore_consumo.services.otp_crypto import decrypt_text, encrypt_text

KEY = 'credihoy_linix_administrativo'
VERSION = 'credihoy-linix-estado-v1'
STATE = 'FLUJO_CREADO_INCIDENCIA_REFERENCIAS'


def validar(p, solicitud_id):
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


def presentar(s, *, tecnico=False):
    p = s.consumo_detail.orchestration_data.get(KEY)
    if p is None:
        return None
    safe = {k: p[k] for k in ('versionContrato', 'solicitudId', 'intencionId', 'kFlujo', 'numeroSc',
            'estado', 'actividad', 'actualizadoAt', 'startEjecutados')}
    if safe['solicitudId'] != str(s.pk):
        raise ValueError('ESTADO_LINIX_DE_OTRO_EXPEDIENTE')
    if tecnico:
        safe['detalleTecnico'] = json.loads(decrypt_text(p['detalleTecnicoCifrado']))
    return safe


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def sincronizar(p, operador):
    """Solo comando administrativo: actualiza metadata, no evaluacion ni solicitud."""
    from .consentimiento_real import obtener
    validar(p, p.get('solicitudId'))
    with transaction.atomic():
        s = obtener(p['solicitudId'], operador, lock=True)
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        evaluacion = EvaluacionConsumo.objects.select_for_update().get(solicitud=s)
        original = _hash(EvaluacionConsumo.objects.filter(pk=evaluacion.pk).values().get())
        prior = detail.orchestration_data.get(KEY)
        if prior:
            if (prior['intencionId'], prior['kFlujo']) != (p['intencionId'], p['kFlujo']):
                raise ValueError('FLUJO_EXISTENTE_NO_SOBRESCRIBIR')
            if prior['evidenciaSha256'] == p['evidenciaSha256']:
                return presentar(s, tecnico=True)
            if parse_datetime(prior['actualizadoAt']) >= parse_datetime(p['actualizadoAt']):
                raise ValueError('CONCILIACION_ANTERIOR_NO_SOBRESCRIBIR')
        stored = {k: v for k, v in p.items() if k != 'detalleTecnico'}
        stored['detalleTecnicoCifrado'] = encrypt_text(json.dumps(p['detalleTecnico']))
        detail.orchestration_data = {**detail.orchestration_data, KEY: stored}
        detail.save(update_fields=['orchestration_data', 'updated_at'])
        if _hash(EvaluacionConsumo.objects.filter(pk=evaluacion.pk).values().get()) != original:
            raise ValueError('EVALUACION_CAMBIO_DURANTE_CONCILIACION')
        AuditLog.objects.create(solicitud=s, event='credihoy_linix_estado_conciliado',
            actor=f'user:{operador.pk}', payload={'intencionId': p['intencionId'], 'kFlujo': p['kFlujo'],
                'estado': p['estado'], 'evidenciaSha256': p['evidenciaSha256']})
        s.consumo_detail = detail
        return presentar(s, tecnico=True)
