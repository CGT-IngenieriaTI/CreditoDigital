import logging

from .models import AuditLog

logger = logging.getLogger("credito.audit")


def audit_event(
    event: str,
    *,
    solicitud=None,
    actor: str = "",
    level: str = AuditLog.Levels.INFO,
    payload: dict | None = None,
    request_id: str = "",
) -> None:
    payload = payload or {}
    try:
        AuditLog.objects.create(
            solicitud=solicitud,
            event=event,
            actor=actor,
            level=level,
            payload=payload,
            request_id=request_id,
        )
    except Exception:  # pragma: no cover
        logger.exception("No fue posible persistir el log de auditoria", extra={"event": event})
