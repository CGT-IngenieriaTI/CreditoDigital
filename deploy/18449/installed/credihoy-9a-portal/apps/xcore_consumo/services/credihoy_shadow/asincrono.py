"""Transporte y auditoria shadow. Ningun fallback ejecuta el motor en el request."""

from hashlib import sha256

from celery import current_app
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.decisiones.models import DecisionFinal
from apps.solicitudes.models import Solicitud
from apps.utils.models import AuditLog
from ..credihoy_integracion.dto import VERSION_CONTRATO
from .servicio import logger

TASK = "credihoy.evaluar_shadow"
QUEUE = "credihoy_shadow"
TRANSITORIOS = {"API_DATA_TIMEOUT", "API_DATA_ERROR_TEMPORAL", "ERROR_TEMPORAL"}
TERMINALES = ("credihoy_shadow_evaluado", "credihoy_shadow_error")


def clave_funcional(datos):
    componentes = [datos[k] for k in ("solicitud_id", "version_politica", "evaluacion_actualizada", "fecha_evaluacion")]
    if datos.get("tipo_piloto") == "PILOTO_TECNICO":
        componentes.append("PILOTO_TECNICO")
    return sha256(("SHADOW|" + "|".join(componentes)).encode("ascii")).hexdigest()


def terminal(datos):
    return AuditLog.objects.filter(solicitud_id=datos["solicitud_id"], event__in=TERMINALES,
                                   payload__clave_funcional=clave_funcional(datos)).exists()


def registrar(datos, estado, *, intentos=0, payload=None, codigo=None, evento=None):
    contenido = dict(payload or {})
    contenido.update(esquema="credihoy-shadow-v1", modo="SHADOW", estado_operativo=estado,
        solicitud_id=datos["solicitud_id"], request_id=datos["request_id"],
        fecha_evaluacion=datos["fecha_evaluacion"], evaluacion_actualizada=datos["evaluacion_actualizada"],
        fecha_registro=timezone.now().isoformat(), version_politica=datos["version_politica"],
        version_contrato_interno=VERSION_CONTRATO, clave_funcional=clave_funcional(datos),
        autorizado_decision_crediticia=False, intentos=intentos)
    if datos.get("piloto_id"):
        contenido["piloto_id"] = datos["piloto_id"]
    if datos.get("tipo_piloto") == "PILOTO_TECNICO":
        contenido.update(tipo_piloto="PILOTO_TECNICO", decision_productiva_baseline="NO_DISPONIBLE",
                         comparacion_productiva="NO_APLICA")
    if codigo:
        contenido.update(resultado_operativo="SHADOW_ERROR", categoria_error=codigo)
        contenido.setdefault("errores_tecnicos", [codigo])
        contenido.setdefault("estado_global", "INFORMACION_INSUFICIENTE")
    contenido.setdefault("motivos", [])
    contenido.setdefault("reglas", [])
    contenido["estado_pipeline"] = Solicitud.objects.filter(pk=datos["solicitud_id"]).values_list("estado", flat=True).first()
    contenido["decision_pipeline"] = DecisionFinal.objects.filter(solicitud_id=datos["solicitud_id"]).values_list("resultado", flat=True).first()
    evento = evento or {"ENCOLADO": "credihoy_shadow_encolado", "EJECUTADO": "credihoy_shadow_evaluado",
              "ERROR": "credihoy_shadow_error"}[estado]
    with transaction.atomic():
        AuditLog.objects.create(solicitud_id=datos["solicitud_id"], event=evento,
            request_id=datos["request_id"][:120], payload=contenido,
            level=AuditLog.Levels.WARNING if codigo else AuditLog.Levels.INFO)
    logger.info("credihoy_shadow estado=%s solicitud_id=%s request_id=%s intentos=%s",
                estado, datos["solicitud_id"], datos["request_id"], intentos)


def encolar_shadow(**datos):
    try:
        if datos.get("tipo_piloto") == "PILOTO_TECNICO" and not datos.get("piloto_id"):
            return "DESAUTORIZADO"
        if datos.get("piloto_id"):
            from .piloto import autorizacion_valida
            if autorizacion_valida(datos) is None:
                return "DESAUTORIZADO"
        elif not settings.CREDIHOY_SHADOW_ENABLED:
            return "DESHABILITADO"
        if terminal(datos):
            return "DUPLICADO"
        # send_task siempre publica: no ejecuta localmente ni con task_always_eager.
        current_app.send_task(TASK, kwargs=datos, queue=QUEUE, serializer="json",
                              retry=False, ignore_result=True)
    except Exception:
        try:
            # No es terminal: el operador puede reencolar si recupera el broker.
            registrar(datos, "ERROR", codigo="SHADOW_ENQUEUE_ERROR", evento="credihoy_shadow_enqueue_error")
        except Exception:
            logger.warning("credihoy_shadow.enqueue_auditoria_no_disponible")
        return "ERROR_ENQUEUE"
    try:
        registrar(datos, "ENCOLADO")
    except Exception:
        logger.warning("credihoy_shadow.encolado_auditoria_no_disponible")
        return "ENCOLADO_SIN_AUDITORIA"
    return "ENCOLADO"


def reporte_comparativo(solicitud_id, *, limite=100):
    """Sin XML/documentos: snapshot comparativo y decision actual en consulta administrativa."""
    actual = DecisionFinal.objects.filter(solicitud_id=solicitud_id).values_list("resultado", flat=True).first()
    filas = AuditLog.objects.filter(solicitud_id=solicitud_id, event__startswith="credihoy_shadow_").order_by("-id")[:limite]
    campos = ("estado_operativo", "estado_pipeline", "decision_pipeline", "estado_global", "motivos",
              "fecha_evaluacion", "version_politica", "version_contrato_interno", "request_id",
              "autorizado_decision_crediticia", "intentos", "categoria_error", "clave_funcional",
              "evaluacion_actualizada")
    return [dict(solicitud_id=str(solicitud_id), decision_actual=actual,
                 **{k: fila.payload.get(k) for k in campos}) for fila in filas]
