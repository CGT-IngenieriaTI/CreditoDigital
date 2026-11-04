"""Worker Celery de observacion, sin autoridad sobre el flujo productivo."""

from datetime import datetime

from celery import shared_task
from celery.exceptions import Retry
from django.conf import settings
from django.db import connection, transaction, DatabaseError

from apps.solicitudes.models import Solicitud
from .models import EvaluacionConsumo
from .services.credihoy_shadow.asincrono import (
    TASK, TRANSITORIOS, clave_funcional, registrar, terminal,
)
from .services.credihoy_shadow.servicio import ServicioEvaluacionCrediHoyShadow, VERSION_POLITICA, logger


@shared_task(name='credihoy.continuar_unico',ignore_result=True,
             acks_late=True,reject_on_worker_lost=True,
             autoretry_for=(RuntimeError,),retry_backoff=True,max_retries=3)
def continuar_credihoy_unico(*,solicitud_id=None,operador_id=None,recuperar_existente=False,evento_id=None):
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
    operador=get_user_model().objects.get(pk=operador_id,is_active=True,is_staff=True)
    return continuar(solicitud_id,operador=operador,recuperar_existente=recuperar_existente)


@shared_task(name='credihoy.continuar_unico_pendientes',ignore_result=True,
             acks_late=False,autoretry_for=(),max_retries=0)
def continuar_credihoy_unico_pendientes(*,operador_id):
    """Existing worker polls durable authorization evidence, never a browser event."""
    if getattr(settings,'CREDIHOY_CONTINUACION_MASIVA_ENABLED',False) is not True:
        return 'CONTINUACION_MASIVA_NO_HABILITADA'
    if (getattr(settings,'RISK_GATEWAY_ORCHESTRATION_ENABLED',False) is not True
            or getattr(settings,'CREDIHOY_EVALUACION_AUTOGESTION_ENABLED',False) is not True):
        return 'CONTINUACION_OPERATIVA_NO_HABILITADA'
    from django.contrib.auth import get_user_model
    from .models import SolicitudConsumo
    from .services.orchestration import continuar_credihoy_unico as continuar
    operador=get_user_model().objects.get(pk=operador_id,is_active=True,is_staff=True)
    if not operador.has_perm('xcore_consumo.add_evaluacionconsumo'):
        raise ValueError('OPERADOR_TECNICO_CONTINUACION_NO_AUTORIZADO')
    candidates=SolicitudConsumo.objects.filter(solicitud__asesor__isnull=True,
        solicitud__metadatos__modo='AUTOGESTION_ENVIADA',solicitud__metadatos__origen='AUTOGESTION_9A',
        orchestration_data__credihoy_autorizacion_centrales_v1__estado='OTP_CONFIRMADA',
        solicitud__evaluacion_consumo__isnull=True).order_by('created_at')
    # Repeated scheduler deliveries are arbitrated by the native HDC reservation.
    results=[]
    for sid in list(candidates.values_list('solicitud_id',flat=True)[:20]):
        try:results.append(continuar(str(sid),operador=operador))
        except Exception:
            logger.warning('continuacion_unico_interrumpida solicitud=%s',sid)
    return {'revisadas':len(results),'operacionesFinancieras':0}


@shared_task(name='credihoy.preparar_acceso_publico', ignore_result=True,
             acks_late=False, autoretry_for=(), max_retries=0)
def preparar_acceso_publico(*, acceso_id):
    from .services.credihoy_v2.unico.publico import preparar
    return preparar(acceso_id)


@shared_task(name='credihoy.limpiar_accesos_publicos', ignore_result=True,
             acks_late=False, autoretry_for=(), max_retries=0)
def limpiar_accesos_publicos():
    from .services.credihoy_v2.unico.publico_controles import purgar_temporales
    from .services.credihoy_v2.unico.publico_privacidad import validar_aviso
    from .services.credihoy_v2.unico import publico_restringido as restringido
    # Cleanup still works after the restricted test window expires.
    if settings.ROOT_URLCONF != 'core.urls_credihoy_publico':
        return 'PERFIL_NO_AUTORIZADO'
    if restringido.activo():
        restringido.validar()
    else:
        validar_aviso()
    return purgar_temporales(actor='servicio-9a',
        referencia=settings.CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA)


@shared_task(bind=True, name=TASK, max_retries=3, ignore_result=True,
             acks_late=True, reject_on_worker_lost=False)
def evaluar_credihoy_shadow(self, *, solicitud_id, request_id, fecha_evaluacion,
                            evaluacion_actualizada, version_politica, piloto_id=None, tipo_piloto="PILOTO_COMPARATIVO"):
    if tipo_piloto not in {"PILOTO_COMPARATIVO", "PILOTO_TECNICO"}:
        return "DESAUTORIZADO"
    if tipo_piloto == "PILOTO_TECNICO" and piloto_id is None:
        return "DESAUTORIZADO"
    if not settings.CREDIHOY_SHADOW_ENABLED and piloto_id is None:
        return "DESHABILITADO"
    datos = dict(solicitud_id=solicitud_id, request_id=request_id, fecha_evaluacion=fecha_evaluacion,
                 evaluacion_actualizada=evaluacion_actualizada, version_politica=version_politica)
    if piloto_id is not None:
        datos["piloto_id"] = piloto_id
    if tipo_piloto == "PILOTO_TECNICO":
        datos["tipo_piloto"] = tipo_piloto
    intentos = self.request.retries + 1
    try:
        with transaction.atomic():
            permiso = None
            if piloto_id is not None:
                from .services.credihoy_shadow.piloto import autorizacion_valida, evidencia_invariantes, preflight
                permiso = autorizacion_valida(datos)
                if permiso is None:
                    return "DESAUTORIZADO"
            # Lock de la clave shadow, no de Solicitud/DecisionFinal. Se libera al commit/crash.
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    lock_id = int(clave_funcional(datos)[:16], 16) - 2**63
                    cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [lock_id])
                    if not cursor.fetchone()[0]:
                        return "EN_EJECUCION"
            if terminal(datos):
                return "DUPLICADO"
            if permiso:
                invariantes = evidencia_invariantes(permiso)
                revision = preflight(solicitud_id, tipo_piloto=tipo_piloto)
                if invariantes["alteracion_productiva"] or revision["estado"] != "PILOTO_APTO":
                    registrar(datos, "ERROR", intentos=intentos, codigo="PILOTO_NO_APTO_AL_EJECUTAR",
                              payload={"invariantes": invariantes, "errores_tecnicos": revision["errores"]})
                    return "ERROR"
            try:
                if version_politica != VERSION_POLITICA:
                    raise ValueError("VERSION_POLITICA")
                solicitud = Solicitud.objects.select_related("solicitante").get(pk=solicitud_id)
                payload = ServicioEvaluacionCrediHoyShadow().evaluar(
                    solicitud, request_id=request_id,
                    evaluacion_actualizada=datetime.fromisoformat(evaluacion_actualizada),
                    fecha_evaluacion=datetime.fromisoformat(fecha_evaluacion), devolver_payload=True,
                    **({"incluir_reporte_piloto": True} if permiso else {}))
            except DatabaseError:
                raise
            except Exception:
                registrar(datos, "ERROR", intentos=intentos, codigo="SHADOW_ERROR_INESPERADO",
                          payload={"invariantes": evidencia_invariantes(permiso)} if permiso else None)
                return "ERROR"
            if permiso:
                payload["invariantes"] = evidencia_invariantes(permiso)
                if payload["invariantes"]["alteracion_productiva"]:
                    registrar(datos, "ERROR", intentos=intentos, payload=payload,
                              codigo="PILOTO_ALTERACION_PRODUCTIVA_DETECTADA")
                    return "ERROR"
            errores = payload.get("errores_tecnicos", [])
            if not EvaluacionConsumo.objects.filter(solicitud_id=solicitud_id,
                    updated_at=datetime.fromisoformat(evaluacion_actualizada)).exists():
                registrar(datos, "ERROR", intentos=intentos, codigo="SHADOW_SNAPSHOT_REEMPLAZADO")
                return "ERROR"
            transitorio = next((c for c in errores if c in TRANSITORIOS), None)
            if transitorio and self.request.retries < self.max_retries:
                # Solo la categoria publica llega al broker/log; nunca la excepcion HTTP.
                raise self.retry(exc=RuntimeError(transitorio), countdown=10 * 2**self.request.retries)
            registrar(datos, "ERROR" if errores else "EJECUTADO", intentos=intentos,
                      payload=payload, codigo=transitorio or (errores[0] if errores else None))
            return "ERROR" if errores else "EJECUTADO"
    except Retry:
        raise
    except Exception:
        # La auditoria fallida no cuenta como ejecucion completada. Reintento acotado.
        if self.request.retries < self.max_retries:
            raise self.retry(exc=RuntimeError("SHADOW_PERSISTENCIA_O_COLA"),
                             countdown=10 * 2**self.request.retries) from None
        logger.error("credihoy_shadow.error_sin_auditoria solicitud_id=%s request_id=%s intentos=%s",
                     solicitud_id, request_id, intentos)
        return "ERROR_SIN_AUDITORIA"
