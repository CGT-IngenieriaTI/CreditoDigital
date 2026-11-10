import logging

from django.conf import settings

from apps.decisiones.services import persist_final_decision
from apps.historial_pago.services import run_historial_pago
from apps.preselecta.services import run_preselecta
from apps.utils.logging import audit_event
from apps.utils.models import AuditLog
from apps.xcore.services import run_xcore

from .models import EstadoSolicitud, Solicitud
from .tasks import process_credit_application_task

logger = logging.getLogger("credito.pipeline")


def dispatch_credit_pipeline(solicitud_id):
    if settings.CREDIT_PIPELINE_ASYNC and hasattr(process_credit_application_task, "delay"):
        process_credit_application_task.delay(str(solicitud_id))
        return True
    process_credit_application(str(solicitud_id))
    return False


def process_credit_application(solicitud_id: str):
    solicitud = Solicitud.objects.select_related("solicitante").get(pk=solicitud_id)
    audit_event("pipeline_started", solicitud=solicitud, payload={"estado": solicitud.estado})
    try:
        preselecta = run_preselecta(solicitud)
        solicitud.estado = EstadoSolicitud.PRESELECTA_OK
        solicitud.paso_actual = "preselecta"
        solicitud.save(update_fields=("estado", "paso_actual", "updated_at"))

        if not preselecta.preaprobado:
            decision = persist_final_decision(
                solicitud,
                {
                    "resultado": "RECHAZADO",
                    "mensaje": "La solicitud no supero la preseleccion inicial.",
                    "detalle": preselecta.response_payload,
                },
                observaciones=preselecta.mensaje,
            )
            audit_event(
                "pipeline_finished_preselecta",
                solicitud=solicitud,
                payload={"resultado": decision.resultado},
            )
            return decision

        historial = run_historial_pago(solicitud)
        solicitud.estado = EstadoSolicitud.HISTORIAL_OK
        solicitud.paso_actual = "historial_pago"
        solicitud.save(update_fields=("estado", "paso_actual", "updated_at"))

        solicitud.estado = EstadoSolicitud.ENVIADA_XCORE
        solicitud.paso_actual = "xcore"
        solicitud.save(update_fields=("estado", "paso_actual", "updated_at"))

        xcore_result, _ = run_xcore(solicitud, preselecta, historial)
        decision = persist_final_decision(solicitud, xcore_result)
        audit_event(
            "pipeline_finished",
            solicitud=solicitud,
            payload={"resultado": decision.resultado},
        )
        return decision
    except Exception as exc:
        logger.exception("Error procesando la solicitud %s", solicitud.numero_solicitud)
        solicitud.estado = EstadoSolicitud.ERROR
        solicitud.paso_actual = "error"
        solicitud.ultimo_error = str(exc)
        solicitud.save(update_fields=("estado", "paso_actual", "ultimo_error", "updated_at"))
        audit_event(
            "pipeline_error",
            solicitud=solicitud,
            level=AuditLog.Levels.ERROR,
            payload={"error": str(exc)},
        )
        raise
