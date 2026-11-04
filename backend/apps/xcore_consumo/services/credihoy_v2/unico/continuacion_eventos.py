"""Transactional continuation intent; delivery never grants financial permission."""

import hashlib
import json
import logging
from uuid import NAMESPACE_URL, UUID, uuid5

from django.conf import settings
from django.db import connections, transaction

from apps.utils.models import AuditLog

EVENTO = 'credihoy_continuacion_solicitada_v1'
FINAL = 'credihoy_continuacion_evento_completado_v1'
logger = logging.getLogger(__name__)


def publicar(evento_id):
    """Failure leaves the committed intent available for individual recovery."""
    if not getattr(settings, 'CELERY_BROKER_URL', None):
        return False
    try:
        from celery import current_app
        current_app.send_task('credihoy.continuar_unico',
            kwargs={'evento_id': str(UUID(str(evento_id)))}, task_id=str(evento_id))
        return True
    except Exception:
        logger.warning('credihoy_continuacion_publicacion_pendiente evento=%s', evento_id)
        return False


def registrar(case, alias, journal):
    """Caller holds the dossier lock; OTP confirmation and intent commit together."""
    if not connections[alias].in_atomic_block or journal['estado'] != 'OTP_CONFIRMADA':
        raise ValueError('CONTINUACION_EVENTO_REQUIERE_AUTORIZACION_Y_TRANSACCION')
    binding = journal['vinculo']
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True,
        separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    event_id = str(uuid5(NAMESPACE_URL, 'credihoy-continuacion-evento:' + str(case.pk) + ':' + digest))
    AuditLog.objects.using(alias).create(solicitud=case, event=EVENTO,
        actor='servicio:centrales', request_id=event_id,
        payload={'version': 1, 'solicitudId': str(case.pk), 'vinculoSha256': digest,
                 'registroSha256': journal['registros'][-1]['sha256'],
                 'operacionAutorizacion': binding['operacionId']})
    transaction.on_commit(lambda: publicar(event_id), using=alias)
    return event_id


def ejecutar(evento_id):
    """One explicitly scoped event, with a session lock across non-atomic IO."""
    from django.contrib.auth import get_user_model
    from apps.xcore_consumo.services.orchestration import continuar_credihoy_unico
    from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo

    eid = str(UUID(str(evento_id)))
    intent = AuditLog.objects.get(event=EVENTO, request_id=eid)
    sid = str(intent.solicitud_id)
    allowed = getattr(settings, 'CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS', ())
    if not isinstance(allowed, (tuple, list, set)) or sid not in allowed:
        return {'estado': 'EXPEDIENTE_NO_AUTORIZADO', 'requestId': eid}
    operator_id = getattr(settings, 'CREDIHOY_CONTINUACION_OPERADOR_ID', None)
    if operator_id is None:
        return {'estado': 'OPERADOR_NO_CONFIGURADO', 'requestId': eid}
    connection = connections['default']
    if connection.vendor != 'postgresql' or connection.in_atomic_block or not connection.get_autocommit():
        raise ValueError('CONTINUACION_EVENTO_REQUIERE_POSTGRES_AUTOCOMMIT')
    lock_id = int(hashlib.sha256(eid.encode()).hexdigest()[:16], 16) - 2**63
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [lock_id])
        locked = cursor.fetchone()[0]
    if not locked:
        return {'estado': 'EN_EJECUCION', 'requestId': eid}
    try:
        done = AuditLog.objects.filter(solicitud_id=sid, event=FINAL, request_id=eid).first()
        if done:
            return done.payload
        detail = SolicitudConsumo.objects.get(solicitud_id=sid)
        from .centrales_personal import KEY
        journal = (detail.orchestration_data or {}).get(KEY, {})
        digest = hashlib.sha256(json.dumps(journal.get('vinculo'), sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        if (journal.get('estado') != 'OTP_CONFIRMADA'
                or digest != intent.payload['vinculoSha256']
                or journal['registros'][-1]['sha256'] != intent.payload['registroSha256']):
            raise ValueError('CONTINUACION_EVENTO_AUTORIZACION_CAMBIO')
        operator = get_user_model().objects.get(pk=operator_id)
        existing = EvaluacionConsumo.objects.filter(solicitud_id=sid).exists()
        result = continuar_credihoy_unico(sid, operador=operator, recuperar_existente=existing)
        packet = {'requestId': eid, 'resultado': result, 'estado': result['estado']}
        if result['estado'] in {'EVALUACION_EXISTENTE_RECUPERADA', 'EVALUACION_COMPLETADA'}:
            AuditLog.objects.create(solicitud_id=sid, event=FINAL, request_id=eid,
                actor='servicio:continuacion', payload=packet)
        return packet
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [lock_id])
