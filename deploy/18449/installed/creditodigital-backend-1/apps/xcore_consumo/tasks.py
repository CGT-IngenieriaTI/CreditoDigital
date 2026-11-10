from celery import shared_task
from django.conf import settings
import logging
logger=logging.getLogger("credito")

@shared_task(name='credihoy.continuar_unico', ignore_result=True, acks_late=True, reject_on_worker_lost=True, autoretry_for=(RuntimeError,), retry_backoff=True, max_retries=3)
def continuar_credihoy_unico(*, solicitud_id=None, operador_id=None, recuperar_existente=False, evento_id=None):
    if evento_id is not None:
        if solicitud_id is not None or operador_id is not None or recuperar_existente:
            raise ValueError('CONTINUACION_EVENTO_ARGUMENTOS_INCOMPATIBLES')
        from .services.credihoy_v2.unico.continuacion_eventos import ejecutar
        try:
            return ejecutar(evento_id)
        except Exception:
            raise RuntimeError('CONTINUACION_EVENTO_PENDIENTE_CONCILIAR') from None
    from django.contrib.auth import get_user_model
    from .services.orchestration import continuar_credihoy_unico as continuar
    operador = get_user_model().objects.get(pk=operador_id, is_active=True, is_staff=True)
    return continuar(solicitud_id, operador=operador, recuperar_existente=recuperar_existente)

@shared_task(name='credihoy.continuar_unico_pendientes', ignore_result=True, acks_late=False, autoretry_for=(), max_retries=0)
def continuar_credihoy_unico_pendientes(*, operador_id):
    """Existing worker polls durable authorization evidence, never a browser event."""
    if getattr(settings, 'CREDIHOY_CONTINUACION_MASIVA_ENABLED', False) is not True:
        return 'CONTINUACION_MASIVA_NO_HABILITADA'
    if getattr(settings, 'RISK_GATEWAY_ORCHESTRATION_ENABLED', False) is not True or getattr(settings, 'CREDIHOY_EVALUACION_AUTOGESTION_ENABLED', False) is not True:
        return 'CONTINUACION_OPERATIVA_NO_HABILITADA'
    from django.contrib.auth import get_user_model
    from .models import SolicitudConsumo
    from .services.orchestration import continuar_credihoy_unico as continuar
    operador = get_user_model().objects.get(pk=operador_id, is_active=True, is_staff=True)
    if not operador.has_perm('xcore_consumo.add_evaluacionconsumo'):
        raise ValueError('OPERADOR_TECNICO_CONTINUACION_NO_AUTORIZADO')
    candidates = SolicitudConsumo.objects.filter(solicitud__asesor__isnull=True, solicitud__metadatos__modo='AUTOGESTION_ENVIADA', solicitud__metadatos__origen='AUTOGESTION_9A', orchestration_data__credihoy_autorizacion_centrales_v1__estado='OTP_CONFIRMADA', solicitud__evaluacion_consumo__isnull=True).order_by('created_at')
    results = []
    for sid in list(candidates.values_list('solicitud_id', flat=True)[:20]):
        try:
            results.append(continuar(str(sid), operador=operador))
        except Exception:
            logger.warning('continuacion_unico_interrumpida solicitud=%s', sid)
    return {'revisadas': len(results), 'operacionesFinancieras': 0}
