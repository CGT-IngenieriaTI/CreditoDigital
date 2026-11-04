from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
import re
import unicodedata
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.historial_pago.client import HistorialPagoClientError, HistorialPagoSOAPClient
from apps.historial_pago.models import HistorialPagoConsulta
from apps.historial_pago.services import (
    build_historial_from_stored_xml,
    persist_historial_consulta,
    persist_historial_failure,
    persist_historial_normalized,
)
from apps.preselecta.client import PreselectaClient, PreselectaClientError
from apps.utils.logging import audit_event
from apps.usuarios.models import Solicitante
from apps.xcore_consumo.models import (
    ConsultaAsociadoIntento,
    EstadoSolicitudConsumo,
    SolicitudConsumo,
)
from apps.xcore_consumo.services.oracle import (
    OracleIntegrationError,
    consultar_capa,
    validar_credito_digital,
)
from apps.xcore_consumo.services.provider_test_identities import (
    get_provider_mode,
    get_provider_test_case,
    get_provider_test_identity,
    use_provider_test_identity,
)


MAX_FAILED_ASSOCIATE_LOOKUPS = 3
ACTIVE_CONSUMO_STATES = (
    EstadoSolicitudConsumo.OTP_PENDIENTE,
    EstadoSolicitudConsumo.OTP_VALIDADA,
    EstadoSolicitudConsumo.CONSENTIMIENTO_PENDIENTE,
    EstadoSolicitudConsumo.CONSENTIMIENTO_FIRMADO,
    EstadoSolicitudConsumo.CORE_CONSULTADO,
    EstadoSolicitudConsumo.FORMULARIO_XCORE_OK,
    EstadoSolicitudConsumo.PROCESANDO,
)
RESUMABLE_CONSUMO_STATES = (
    EstadoSolicitudConsumo.CONSENTIMIENTO_FIRMADO,
    EstadoSolicitudConsumo.CORE_CONSULTADO,
    EstadoSolicitudConsumo.FORMULARIO_XCORE_OK,
)
LINIX_FIELD_KEYS = (
    "nombre",
    "estrato",
    "nivel_estudios",
    "estado_civil",
    "genero",
    "tipo_vivienda",
    "forma_pago",
    "tipo_contrato",
    "numero_personas_cargo",
    "edad",
    "antiguedad_asociado",
    "ingresos",
    "aportes_sociales",
    "activos",
    "pasivos",
    "valor_pasivos",
    "saldo_creditos",
    "ocupacion",
    "zona",
)
SYSTEM_DERIVED_FIELD_KEYS = (
    *LINIX_FIELD_KEYS,
    "valor_score",
    "valor_activos",
    "rango_score",
    "valor_pasivos_recoge",
    "cupos_tarjetas_rotativos",
    "tasa_cupos_rotativos",
    "cuotas_creditos_egresos",
    "valor_cuotas_recoge_per",
)
ALL_XCORE_FIELD_KEYS = (
    "tipo_cliente",
    "tipo_credito",
    "estrato",
    "nivel_estudios",
    "estado_civil",
    "genero",
    "tipo_vivienda",
    "forma_pago",
    "garantia",
    "tipo_contrato",
    "numero_personas_cargo",
    "edad",
    "antiguedad_asociado",
    "ingresos",
    "rango_score",
    "aportes_sociales",
    "activos",
    "pasivos",
    "ocupacion",
    "canal",
    "zona",
    "tipo_garantia",
    "valor_score",
    "valor_activos",
    "valor_pasivos",
    "valor_pasivos_recoge",
    "saldo_creditos",
    "cupos_tarjetas_rotativos",
    "tasa_cupos_rotativos",
    "asalariados",
    "pensionados",
    "prestadores_prof",
    "independientes",
    "rentistas_capital",
    "transportadores",
    "personas_cargo_ingresos",
    "cuotas_creditos_egresos",
    "cuotas_creditos_codeudor",
    "valor_cuotas_recoge_per",
    "valor_cuotas_recoge_nom",
    "otros_descuentos",
    "monto_solicitado",
    "plazo",
    "capitalizacion_aportes",
    "nombre",
)
READONLY_NOTICE = (
    "La informacion mostrada proviene del sistema central LINIX. "
    "Si requiere actualizar sus datos debe hacerlo directamente con la cooperativa."
)
logger = logging.getLogger("credito")

PRESELECTA_USER_FRIENDLY_REJECTION = (
    "Por ahora no es posible continuar con la solicitud de credito. "
    "Si necesitas mas informacion, comunicate con la cooperativa."
)
PRESELECTA_REJECTION_COOLDOWN_DAYS = 30
PRESELECTA_REJECTION_COOLDOWN_MESSAGE = (
    "La identificacion fue rechazada por centrales y debe esperar 1 mes antes de intentar nuevamente. "
    "Si necesitas mas informacion, comunicate con la cooperativa."
)

ASSOCIATE_VALIDATION_RETRY_MESSAGE = (
    "Validemos nuevamente tu informacion para continuar con la solicitud."
)
ASSOCIATE_VALIDATION_BLOCKED_MESSAGE = (
    "Por ahora no fue posible validar esta identificacion. "
    "Te recomendamos revisar el caso con la cooperativa."
)


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _as_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_text(value: Any) -> str:
    if not value:
        return ""
    return unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().strip().lower()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _score_bucket(score: Any) -> str:
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return ""
    if numeric < 300:
        return "Menor a 300"
    if numeric <= 500:
        return "300 - 500"
    if numeric <= 700:
        return "500 - 700"
    if numeric <= 800:
        return "700 - 800"
    if numeric <= 900:
        return "800 - 900"
    return "Mas de 900"


def _extract_engine_value(engine_response: Any, key: str) -> str:
    if not isinstance(engine_response, list):
        return ""
    key_lower = key.lower()
    for item in engine_response:
        if str(item.get("key", "")).lower() == key_lower:
            return str(item.get("value", "")).strip()
    return ""


def _contains_zona_gris(value: Any) -> bool:
    normalized = _as_string(value).upper()
    return "ZONA" in normalized and "GRIS" in normalized


def normalize_preselecta_business_status(response: dict[str, Any]) -> dict[str, Any]:
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    fault = response.get("fault") if isinstance(response.get("fault"), dict) else {}
    if not fault and isinstance(raw.get("Fault"), dict):
        fault = raw.get("Fault", {})
    engine_response = response.get("engine_response")
    if not isinstance(engine_response, list):
        engine_response = raw.get("engineResponse", []) if isinstance(raw, dict) else []
    decision = _as_string(response.get("decision") or _extract_engine_value(engine_response, "DECISION")).strip()
    risk_level = _as_string(response.get("risk_level") or _extract_engine_value(engine_response, "RIESGO_SCORE")).strip()
    preaprobado = response.get("preaprobado")
    score = response.get("score")
    mensaje_tecnico = _as_string(response.get("mensaje_tecnico") or fault.get("faultstring") or "").strip()
    mensaje = (
        _as_string(response.get("mensaje")).strip()
        or mensaje_tecnico
        or decision
        or risk_level
        or "Consulta PRESELECTA realizada."
    )

    decision_upper = decision.upper()
    risk_upper = risk_level.upper()
    if fault:
        estado_negocio = "ERROR"
        puede_continuar = False
    elif decision_upper == "APROBADO":
        estado_negocio = "APROBADO"
        puede_continuar = True
    elif _contains_zona_gris(risk_upper) or _contains_zona_gris(decision_upper):
        estado_negocio = "ZONA_GRIS"
        puede_continuar = True
    elif decision_upper:
        estado_negocio = "RECHAZADO"
        puede_continuar = False
    elif preaprobado is True:
        estado_negocio = "APROBADO"
        puede_continuar = True
    elif preaprobado is False:
        estado_negocio = "RECHAZADO"
        puede_continuar = False
    else:
        estado_negocio = "ERROR"
        puede_continuar = False

    if estado_negocio == "RECHAZADO":
        mensaje_usuario = PRESELECTA_USER_FRIENDLY_REJECTION
    elif estado_negocio == "ZONA_GRIS":
        mensaje_usuario = (
            "La solicitud puede continuar a validacion de historial para completar la evaluacion preliminar."
        )
    elif estado_negocio == "APROBADO":
        mensaje_usuario = "La preseleccion fue aprobada y el flujo puede continuar."
    else:
        mensaje_usuario = (
            "No fue posible completar la preseleccion en este momento. "
            "Intenta nuevamente o valida el caso con la cooperativa."
        )

    decision_inputs = {
        "decision": decision,
        "risk_level": risk_level,
        "faultstring": fault.get("faultstring") if isinstance(fault, dict) else "",
        "mensaje": mensaje,
        "mensaje_tecnico": mensaje_tecnico,
        "preaprobado": preaprobado,
        "score": score,
        "engine_response": engine_response,
    }
    return {
        **response,
        "estado_negocio": estado_negocio,
        "puede_continuar": puede_continuar,
        "mensaje_usuario": mensaje_usuario,
        "mensaje": mensaje,
        "mensaje_tecnico": mensaje_tecnico,
        "decision": decision,
        "risk_level": risk_level,
        "engine_response": engine_response,
        "fault": fault,
        "decision_inputs": decision_inputs,
        "score": score if score not in (None, "") else "",
    }

def _compact_preselecta_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    if not response:
        return {}
    return {
        "estado": response.get("estado", ""),
        "estado_negocio": response.get("estado_negocio", ""),
        "puede_continuar": bool(response.get("puede_continuar")),
        "mensaje": response.get("mensaje", ""),
        "mensaje_usuario": response.get("mensaje_usuario", ""),
        "mensaje_tecnico": response.get("mensaje_tecnico", ""),
        "decision": response.get("decision", ""),
        "risk_level": response.get("risk_level", ""),
        "score": response.get("score", ""),
        "engine_response": response.get("engine_response", []),
        "fault": response.get("fault", {}),
        "request_payload": response.get("request_payload", {}),
        "identidad_efectiva": response.get("identidad_efectiva") or response.get("identity_effective", {}),
    }


def _historial_principal_metric(metrics: dict[str, Any], key: str, fallback_key: str) -> Any:
    return metrics.get(key, metrics.get(fallback_key, 0))

def _full_name(value: Any) -> str:
    return _as_string(value).strip()


def _preselecta_tipo_asociado(datos_linix: dict[str, Any] | None) -> str:
    antiguedad = _as_string((datos_linix or {}).get("antiguedad_asociado")).strip()
    return "2" if antiguedad else "1"


def _preselecta_medio_pago(datos_linix: dict[str, Any] | None) -> str:
    forma_pago = _normalize_text((datos_linix or {}).get("forma_pago", ""))
    return "2" if "nomina" in forma_pago else "1"


def _preselecta_actividad(datos_linix: dict[str, Any] | None) -> str:
    ocupacion = _normalize_text((datos_linix or {}).get("ocupacion", ""))
    return "3" if any(token in ocupacion for token in ["independ", "profesional", "rentista", "transport"]) else "1"


def _parse_money_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^0-9-]", "", str(value))
    if not digits or digits == "-":
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _bucket_ingresos(value: Any) -> str:
    numeric = _parse_money_value(value)
    if numeric is None:
        return _as_string(value).strip()
    if numeric <= 3000000:
        return "($1,500,000 - $3,000,000)"
    if numeric <= 5000000:
        return "($3,000,000 - $5,000,000)"
    return "Mas de $5,000,000"


def _bucket_aportes_sociales(value: Any) -> str:
    numeric = _parse_money_value(value)
    if numeric is None:
        return _as_string(value).strip()
    if numeric <= 6400000:
        return "($2,300,000 - $6,400,000)"
    if numeric <= 12800000:
        return "($6,400,000 - $12,800,000)"
    if numeric <= 24800000:
        return "($12,800,000 - $24,800,000)"
    return "Mas de $24,800,000"


def _bucket_activos(value: Any) -> str:
    numeric = _parse_money_value(value)
    if numeric is None:
        return _as_string(value).strip()
    if numeric < 150000000:
        return "Menos de $150,000,000"
    return "Mas de $150,000,000"


def _existing_contact_data(tipo_identificacion: str, numero_identificacion: str) -> dict[str, Any]:
    applicant = (
        Solicitante.objects.filter(
            tipo_identificacion=tipo_identificacion,
            numero_identificacion=numero_identificacion,
        )
        .order_by("-updated_at")
        .first()
    )
    if not applicant:
        return {}
    return {
        "fecha_expedicion": applicant.fecha_expedicion.isoformat(),
        "celular": applicant.celular,
        "email": applicant.email,
        "primer_apellido": applicant.primer_apellido,
    }


def failed_consult_attempts(tipo_identificacion: str, numero_identificacion: str) -> int:
    return ConsultaAsociadoIntento.objects.filter(
        tipo_identificacion=tipo_identificacion,
        numero_identificacion=numero_identificacion,
        puede_continuar=False,
    ).count()


def find_recent_centrales_rejection(tipo_identificacion: str, numero_identificacion: str) -> SolicitudConsumo | None:
    cutoff = timezone.now() - timedelta(days=PRESELECTA_REJECTION_COOLDOWN_DAYS)
    candidates = SolicitudConsumo.objects.select_related("solicitud").filter(
        solicitud__solicitante__tipo_identificacion=tipo_identificacion,
        solicitud__solicitante__numero_identificacion=numero_identificacion,
        updated_at__gte=cutoff,
    ).order_by("-updated_at")
    for detail in candidates:
        estado_negocio = str(((detail.orchestration_data or {}).get("datos_preselecta") or {}).get("estado_negocio", "")).upper()
        if estado_negocio == "RECHAZADO":
            return detail
    return None


def _advisor_name(detail: SolicitudConsumo | None) -> str:
    if not detail or not detail.solicitud.asesor_id:
        return ""
    full_name = detail.solicitud.asesor.get_full_name().strip()
    return full_name or detail.solicitud.asesor.username


def find_active_duplicate_request(user, tipo_identificacion: str, numero_identificacion: str):
    detail = (
        SolicitudConsumo.objects.select_related("solicitud", "solicitud__asesor")
        .filter(
            solicitud__solicitante__tipo_identificacion=tipo_identificacion,
            solicitud__solicitante__numero_identificacion=numero_identificacion,
            estado__in=ACTIVE_CONSUMO_STATES,
        )
        .order_by("-updated_at")
        .first()
    )
    if not detail:
        return (
            {
                "has_active": False,
                "solicitud_id": "",
                "numero_solicitud": "",
                "estado": "",
                "asesor_nombre": "",
                "is_incomplete": False,
                "resume_available": False,
                "message": "",
            },
            None,
            False,
        )

    same_advisor = bool(user and user.is_authenticated and detail.solicitud.asesor_id == user.id)
    resume_available = same_advisor and detail.estado in RESUMABLE_CONSUMO_STATES
    asesor_nombre = _advisor_name(detail)

    if resume_available:
        message = (
            f"La solicitud {detail.solicitud.numero_solicitud} ya existe y puede reanudarse "
            "en el formulario XCORE sin repetir autenticacion."
        )
    else:
        message = (
            "Ya existe una solicitud activa de consumo para esta identificacion. "
            f"Solicitud {detail.solicitud.numero_solicitud}, estado {detail.estado}, "
            f"asesor {asesor_nombre or 'sin asignar'}."
        )

    return (
        {
            "has_active": True,
            "solicitud_id": str(detail.solicitud_id),
            "numero_solicitud": detail.solicitud.numero_solicitud,
            "estado": detail.estado,
            "asesor_nombre": asesor_nombre,
            "is_incomplete": detail.estado != EstadoSolicitudConsumo.FINALIZADO,
            "resume_available": resume_available,
            "message": message,
        },
        detail,
        resume_available,
    )


def _remaining_attempts(failed_attempts: int) -> int:
    return max(0, MAX_FAILED_ASSOCIATE_LOOKUPS - failed_attempts)


def register_associate_consult_attempt(
    *,
    asesor,
    tipo_identificacion: str,
    numero_identificacion: str,
    request_payload: dict[str, Any],
    snapshot: dict[str, Any],
    solicitud=None,
) -> dict[str, Any]:
    validation = snapshot.get("validation_credito_digital", {})
    core = snapshot.get("core", {})
    ConsultaAsociadoIntento.objects.create(
        solicitud=solicitud,
        asesor=asesor if getattr(asesor, "is_authenticated", False) else None,
        tipo_identificacion=tipo_identificacion,
        numero_identificacion=numero_identificacion,
        oracle_ok=bool(core.get("found")),
        preselecta_ok=False,
        datacredito_ok=False,
        puede_continuar=bool(snapshot.get("can_continue")),
        bloqueado=bool(validation.get("blocked")),
        request_payload=_json_safe(request_payload),
        response_payload=_json_safe(snapshot),
        mensaje=_as_string(
            validation.get("message")
            or snapshot.get("duplicate_request", {}).get("message")
            or core.get("message")
        )[:255],
    )
    return snapshot


def _base_form_defaults(payload: dict[str, Any], existing_defaults: dict[str, Any]) -> dict[str, Any]:
    form_defaults = {
        "nombre": "",
        "fecha_expedicion": _json_safe(payload["fecha_expedicion"]),
        "celular": payload["celular"],
        "email": payload["email"],
        "primer_apellido": payload["primer_apellido"],
    }
    for key, value in existing_defaults.items():
        if value and not form_defaults.get(key):
            form_defaults[key] = value
    return form_defaults


def _blocked_snapshot(message: str, *, duplicate_request: dict[str, Any], failed_attempts: int, form_defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        "duplicate_request": duplicate_request,
        "validation_credito_digital": {
            "ok": False,
            "message": message,
            "failed_attempts": failed_attempts,
            "remaining_attempts": _remaining_attempts(failed_attempts),
            "blocked": True,
        },
        "core": {"found": False, "message": "La consulta al CORE no se ejecuto.", "data": {}},
        "form_defaults": form_defaults,
        "can_continue": False,
    }


def build_consulta_identificacion_response(*, user, payload: dict[str, Any]) -> dict[str, Any]:
    tipo_identificacion = payload["tipo_identificacion"]
    numero_identificacion = payload["numero_identificacion"]
    duplicate_request, duplicate_detail, resume_available = find_active_duplicate_request(
        user,
        tipo_identificacion,
        numero_identificacion,
    )
    existing_defaults = _existing_contact_data(tipo_identificacion, numero_identificacion)
    form_defaults = _base_form_defaults(payload, existing_defaults)
    failed_before = failed_consult_attempts(tipo_identificacion, numero_identificacion)

    if duplicate_request["has_active"]:
        snapshot = {
            "duplicate_request": duplicate_request,
            "validation_credito_digital": {
                "ok": resume_available,
                "message": duplicate_request["message"],
                "failed_attempts": failed_before,
                "remaining_attempts": _remaining_attempts(failed_before),
                "blocked": not resume_available,
            },
            "core": {"found": False, "message": "Consulta bloqueada por solicitud activa.", "data": {}},
            "form_defaults": form_defaults,
            "can_continue": resume_available,
        }
        return register_associate_consult_attempt(
            asesor=user,
            solicitud=duplicate_detail.solicitud if duplicate_detail else None,
            tipo_identificacion=tipo_identificacion,
            numero_identificacion=numero_identificacion,
            request_payload=payload,
            snapshot=snapshot,
        )

    recent_centrales_rejection = find_recent_centrales_rejection(tipo_identificacion, numero_identificacion)
    if recent_centrales_rejection is not None:
        snapshot = _blocked_snapshot(
            PRESELECTA_REJECTION_COOLDOWN_MESSAGE,
            duplicate_request=duplicate_request,
            failed_attempts=failed_before,
            form_defaults=form_defaults,
        )
        snapshot["centrales_restriction"] = {
            "blocked": True,
            "reason": "PRESELECTA_RECHAZADO",
            "message": PRESELECTA_REJECTION_COOLDOWN_MESSAGE,
            "blocked_until": (recent_centrales_rejection.updated_at + timedelta(days=PRESELECTA_REJECTION_COOLDOWN_DAYS)).isoformat(),
            "numero_solicitud": recent_centrales_rejection.solicitud.numero_solicitud,
        }
        return register_associate_consult_attempt(
            asesor=user,
            solicitud=recent_centrales_rejection.solicitud,
            tipo_identificacion=tipo_identificacion,
            numero_identificacion=numero_identificacion,
            request_payload=payload,
            snapshot=snapshot,
        )

    if failed_before >= MAX_FAILED_ASSOCIATE_LOOKUPS:
        snapshot = _blocked_snapshot(
            ASSOCIATE_VALIDATION_BLOCKED_MESSAGE,
            duplicate_request=duplicate_request,
            failed_attempts=failed_before,
            form_defaults=form_defaults,
        )
        return register_associate_consult_attempt(
            asesor=user,
            tipo_identificacion=tipo_identificacion,
            numero_identificacion=numero_identificacion,
            request_payload=payload,
            snapshot=snapshot,
        )

    try:
        validation = validar_credito_digital(
            numero_identificacion=numero_identificacion,
            fecha_expedicion=payload["fecha_expedicion"],
            primer_apellido=payload["primer_apellido"],
            celular=payload["celular"],
            correo=payload["email"],
        )
    except OracleIntegrationError as exc:
        logger.error(
            "associate_lookup.validation_failed numero_identificacion=%s error=%s",
            numero_identificacion,
            exc,
        )
        failed_after = failed_before + 1
        snapshot = {
            "duplicate_request": duplicate_request,
            "validation_credito_digital": {
                "ok": False,
                "message": ASSOCIATE_VALIDATION_RETRY_MESSAGE,
                "failed_attempts": failed_after,
                "remaining_attempts": _remaining_attempts(failed_after),
                "blocked": failed_after >= MAX_FAILED_ASSOCIATE_LOOKUPS,
                "debug_error": str(exc),
            },
            "core": {"found": False, "message": "La consulta al CORE no se ejecuto.", "data": {}},
            "form_defaults": form_defaults,
            "can_continue": False,
        }
        return register_associate_consult_attempt(
            asesor=user,
            tipo_identificacion=tipo_identificacion,
            numero_identificacion=numero_identificacion,
            request_payload=payload,
            snapshot=snapshot,
        )

    if not validation["ok"]:
        failed_after = failed_before + 1
        blocked = failed_after >= MAX_FAILED_ASSOCIATE_LOOKUPS
        snapshot = {
            "duplicate_request": duplicate_request,
            "validation_credito_digital": {
                "ok": False,
                "message": (
                    ASSOCIATE_VALIDATION_BLOCKED_MESSAGE if blocked else ASSOCIATE_VALIDATION_RETRY_MESSAGE
                ),
                "failed_attempts": failed_after,
                "remaining_attempts": _remaining_attempts(failed_after),
                "blocked": blocked,
            },
            "core": {"found": False, "message": "La consulta al CORE no se ejecuto.", "data": {}},
            "form_defaults": form_defaults,
            "can_continue": False,
        }
        return register_associate_consult_attempt(
            asesor=user,
            tipo_identificacion=tipo_identificacion,
            numero_identificacion=numero_identificacion,
            request_payload=payload,
            snapshot=snapshot,
        )

    try:
        core_data = consultar_capa(None, numero_identificacion)
    except Exception as exc:
        logger.error(
            "associate_lookup.core_failed numero_identificacion=%s validation_ok=%s error=%s",
            numero_identificacion,
            validation.get("ok"),
            exc,
        )
        snapshot = {
            "duplicate_request": duplicate_request,
            "validation_credito_digital": {
                "ok": True,
                "message": validation["message"],
                "failed_attempts": failed_before,
                "remaining_attempts": _remaining_attempts(failed_before),
                "blocked": False,
            },
            "core": {"found": False, "message": ASSOCIATE_VALIDATION_RETRY_MESSAGE, "data": {}, "debug_error": str(exc)},
            "form_defaults": form_defaults,
            "can_continue": False,
        }
        return register_associate_consult_attempt(
            asesor=user,
            tipo_identificacion=tipo_identificacion,
            numero_identificacion=numero_identificacion,
            request_payload=payload,
            snapshot=snapshot,
        )

    form_defaults["nombre"] = _full_name(core_data.get("nombre"))
    snapshot = {
        "duplicate_request": duplicate_request,
        "validation_credito_digital": {
            "ok": True,
            "message": validation["message"],
            "failed_attempts": failed_before,
            "remaining_attempts": _remaining_attempts(failed_before),
            "blocked": False,
        },
        "core": {
            "found": bool(core_data),
            "message": "Consulta al CORE completada.",
            "data": core_data,
        },
        "form_defaults": form_defaults,
        "can_continue": bool(core_data),
    }
    return register_associate_consult_attempt(
        asesor=user,
        tipo_identificacion=tipo_identificacion,
        numero_identificacion=numero_identificacion,
        request_payload=payload,
        snapshot=snapshot,
    )


def _effective_external_identity(numero_identificacion: str, primer_apellido: str, datos_linix: dict[str, Any] | None = None) -> dict[str, Any]:
    if use_provider_test_identity():
        case_name = get_provider_test_case()
        provider = get_provider_test_identity()
        return {
            "numero_identificacion": provider["numero_identificacion"],
            "tipo_identificacion": provider["tipo_identificacion"],
            "primer_apellido": provider["primer_apellido"],
            "provider_mode": "test",
            "provider_test_identity": True,
            "provider_test_case": case_name,
            "linea_credito": provider["linea_credito"],
            "tipo_asociado": provider["tipo_asociado"],
            "medio_pago": provider["medio_pago"],
            "actividad": provider["actividad"],
        }
    return {
        "numero_identificacion": numero_identificacion,
        "tipo_identificacion": "1",
        "primer_apellido": primer_apellido,
        "provider_mode": "real",
        "provider_test_identity": False,
        "provider_test_case": "",
        "linea_credito": "1",
        "tipo_asociado": _preselecta_tipo_asociado(datos_linix),
        "medio_pago": _preselecta_medio_pago(datos_linix),
        "actividad": _preselecta_actividad(datos_linix),
    }


def _payload_preselecta(numero_identificacion: str, primer_apellido: str, datos_linix: dict[str, Any] | None = None) -> dict[str, Any]:
    identity = _effective_external_identity(numero_identificacion, primer_apellido, datos_linix)
    payload = {
        "idNumber": identity["numero_identificacion"],
        "idType": identity["tipo_identificacion"],
        "firstLastName": identity["primer_apellido"],
        "linea_credito": identity["linea_credito"],
        "tipo_asociado": identity["tipo_asociado"],
        "medio_pago": identity["medio_pago"],
        "actividad": identity["actividad"],
        "provider_mode": identity["provider_mode"],
        "provider_test_identity": identity["provider_test_identity"],
        "provider_test_case": identity["provider_test_case"],
    }
    return payload


def _payload_historial(identity_effective: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "numero_solicitud": f"PREV-{identity_effective['numero_identificacion']}",
        "tipo_identificacion": identity_effective.get("tipo_identificacion", "1"),
        "numero_identificacion": identity_effective["numero_identificacion"],
        "primer_apellido": identity_effective.get("primer_apellido", ""),
        "provider_mode": identity_effective.get("provider_mode", "real"),
        "provider_test_identity": bool(identity_effective.get("provider_test_identity")),
        "provider_test_case": identity_effective.get("provider_test_case", ""),
    }
    return payload


def _stored_historial_matches_identity(consulta: HistorialPagoConsulta | None, identity_effective: dict[str, Any]) -> bool:
    if not consulta or not consulta.xml_payload:
        return False

    expected_numero = str(identity_effective.get("numero_identificacion", "")).strip().lstrip("0")
    expected_apellido = str(identity_effective.get("primer_apellido", "")).strip().upper()
    request_payload = consulta.request_payload or {}
    stored_numero = str(request_payload.get("numero_identificacion", "")).strip().lstrip("0")
    stored_apellido = str(request_payload.get("primer_apellido", "")).strip().upper()

    if stored_numero:
        return stored_numero == expected_numero and (not expected_apellido or stored_apellido == expected_apellido)

    xml_head = (consulta.xml_payload or "")[:4000]
    numero_match = re.search(r"identificacionDigitada\s*=\s*['\"]([^'\"]+)['\"]", xml_head)
    apellido_match = re.search(r"apellidoDigitado\s*=\s*['\"]([^'\"]+)['\"]", xml_head)
    xml_numero = numero_match.group(1).strip().lstrip("0") if numero_match else ""
    xml_apellido = apellido_match.group(1).strip().upper() if apellido_match else ""
    return bool(xml_numero and xml_numero == expected_numero and (not expected_apellido or xml_apellido == expected_apellido))


def _datos_datacredito(historial: dict[str, Any]) -> dict[str, Any]:
    return {
        "estado": historial.get("estado", ""),
        "score_pago": historial.get("score_pago"),
        "mora_maxima": historial.get("mora_maxima"),
        "categoria": historial.get("categoria", ""),
        "resumen": historial.get("resumen", ""),
    }


def _merge_consolidated_values(
    *,
    datos_linix: dict[str, Any],
    datos_preselecta: dict[str, Any],
    datos_datacredito: dict[str, Any],
    historial_pago: dict[str, Any],
) -> dict[str, Any]:
    metrics = historial_pago.get("metrics", {})
    score = (
        historial_pago.get("advance_score")
        or datos_preselecta.get("score")
        or datos_datacredito.get("score_pago")
    )
    raw_ingresos = datos_linix.get("ingresos", "")
    raw_aportes = datos_linix.get("aportes_sociales", "")
    raw_activos = datos_linix.get("activos", "")
    historial_pasivos = metrics.get("valor_pasivos", 0)
    consolidated = {
        "tipo_cliente": "ANTIGUO" if _value_present(datos_linix.get("antiguedad_asociado")) else "",
        "nombre": datos_linix.get("nombre", ""),
        "estrato": datos_linix.get("estrato", ""),
        "nivel_estudios": datos_linix.get("nivel_estudios", ""),
        "estado_civil": datos_linix.get("estado_civil", ""),
        "genero": datos_linix.get("genero", ""),
        "tipo_vivienda": datos_linix.get("tipo_vivienda", ""),
        "forma_pago": datos_linix.get("forma_pago", ""),
        "tipo_contrato": datos_linix.get("tipo_contrato", ""),
        "numero_personas_cargo": datos_linix.get("numero_personas_cargo", ""),
        "edad": datos_linix.get("edad", ""),
        "antiguedad_asociado": datos_linix.get("antiguedad_asociado", ""),
        "ingresos": _bucket_ingresos(raw_ingresos),
        "aportes_sociales": _bucket_aportes_sociales(raw_aportes),
        "activos": _bucket_activos(raw_activos),
        "pasivos": "NO",
        "ocupacion": datos_linix.get("ocupacion", ""),
        "zona": datos_linix.get("zona", ""),
        "valor_score": score or "",
        "valor_activos": datos_linix.get("valor_activos", "") or "",
        "rango_score": _score_bucket(score),
        "valor_pasivos": historial_pasivos,
        "valor_pasivos_recoge": metrics.get("valor_pasivos_que_recoge", 0),
        "saldo_creditos": _historial_principal_metric(metrics, "saldo_total_creditos_deudor_principal", "saldo_total_creditos") or datos_linix.get("saldo_creditos", 0),
        "cupos_tarjetas_rotativos": metrics.get("cupos_tarjetas_rotativos", 0),
        "tasa_cupos_rotativos": settings.XCORE_CONSUMO_TASA_CUPOS_ROTATIVOS,
        "cuotas_creditos_egresos": _historial_principal_metric(metrics, "total_cuotas_credito_deudor_principal", "total_cuotas_credito"),
        "cuotas_creditos_codeudor": metrics.get("cuota_abierta_codeudor", metrics.get("cuotas_creditos_codeudor", 0)),
        "valor_cuotas_recoge_per": metrics.get("valor_cuota_que_recoge_pago_personal", 0),
    }
    try:
        consolidated["pasivos"] = "SI" if float(historial_pasivos or 0) > 0 else "NO"
    except (TypeError, ValueError):
        consolidated["pasivos"] = "NO"
    return consolidated


def _collect_blocked_fields(consolidated_values: dict[str, Any]) -> list[str]:
    blocked = []
    for key in SYSTEM_DERIVED_FIELD_KEYS:
        if _value_present(consolidated_values.get(key)):
            blocked.append(key)
    return sorted(set(blocked))


def _editable_fields(blocked_fields: list[str]) -> list[str]:
    blocked = set(blocked_fields)
    return [field for field in ALL_XCORE_FIELD_KEYS if field not in blocked]


def _missing_fields(field_names: list[str] | tuple[str, ...], values: dict[str, Any]) -> list[str]:
    return [field for field in field_names if not _value_present(values.get(field))]


def _fetch_external_data(solicitud, numero_identificacion: str, primer_apellido: str, datos_linix: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    integration_errors: dict[str, str] = {}
    preselecta_payload = _payload_preselecta(numero_identificacion, primer_apellido, datos_linix)
    effective_identity = {
        "numero_identificacion": preselecta_payload.get("idNumber", numero_identificacion),
        "tipo_identificacion": preselecta_payload.get("idType", "1"),
        "primer_apellido": preselecta_payload.get("firstLastName", primer_apellido),
        "provider_mode": preselecta_payload.get("provider_mode", get_provider_mode()),
        "provider_test_identity": bool(preselecta_payload.get("provider_test_identity")),
        "provider_test_case": preselecta_payload.get("provider_test_case", ""),
    }
    logger.info(
        "consumo.external_lookup provider_mode=%s provider_test_case=%s numero_identificacion=%s",
        effective_identity.get("provider_mode", "real"),
        effective_identity.get("provider_test_case", ""),
        effective_identity.get("numero_identificacion", ""),
    )
    try:
        raw_preselecta = PreselectaClient().evaluate(preselecta_payload)
        datos_preselecta = normalize_preselecta_business_status(
            {
                **raw_preselecta,
                "request_payload": raw_preselecta.get("request_payload", preselecta_payload),
                "identidad_efectiva": raw_preselecta.get("identity_effective", effective_identity),
            }
        )
    except PreselectaClientError as exc:
        datos_preselecta = {
            "estado": "ERROR",
            "estado_negocio": "ERROR",
            "puede_continuar": False,
            "mensaje": str(exc),
            "mensaje_tecnico": str(exc),
            "mensaje_usuario": (
                "No fue posible completar la preseleccion en este momento. "
                "Intenta nuevamente o valida el caso con la cooperativa."
            ),
            "request_payload": preselecta_payload,
            "identidad_efectiva": effective_identity,
            "engine_response": [],
            "decision": "",
            "risk_level": "",
            "fault": {},
        }
        integration_errors["preselecta"] = str(exc)
    except Exception as exc:  # pragma: no cover
        datos_preselecta = {
            "estado": "ERROR",
            "estado_negocio": "ERROR",
            "puede_continuar": False,
            "mensaje": str(exc),
            "mensaje_tecnico": str(exc),
            "mensaje_usuario": (
                "No fue posible completar la preseleccion en este momento. "
                "Intenta nuevamente o valida el caso con la cooperativa."
            ),
            "request_payload": preselecta_payload,
            "identidad_efectiva": effective_identity,
            "engine_response": [],
            "decision": "",
            "risk_level": "",
            "fault": {},
        }
        integration_errors["preselecta"] = str(exc)

    if not datos_preselecta.get("puede_continuar"):
        return _compact_preselecta_snapshot(datos_preselecta), {}, integration_errors

    historial_payload = _payload_historial(datos_preselecta.get("identidad_efectiva", effective_identity))
    historial_raw = None
    try:
        consulta_existente = HistorialPagoConsulta.objects.filter(solicitud=solicitud).first()
        same_identity = _stored_historial_matches_identity(
            consulta_existente,
            datos_preselecta.get("identidad_efectiva", effective_identity),
        )
        if consulta_existente and consulta_existente.xml_payload and same_identity:
            historial_pago = build_historial_from_stored_xml(consulta_existente, selected_keys=[])
        else:
            historial_raw = HistorialPagoSOAPClient().consult(historial_payload)
            _, historial_pago = persist_historial_consulta(
                solicitud,
                historial_payload,
                historial_raw,
                selected_keys=[],
            )
    except HistorialPagoClientError as exc:
        if historial_raw is not None:
            persist_historial_failure(solicitud, historial_payload, historial_raw, str(exc))
        historial_pago = {"estado": "ERROR", "resumen": str(exc), "metrics": {}}
        integration_errors["historial_pago"] = str(exc)
    except Exception as exc:  # pragma: no cover
        if historial_raw is not None:
            persist_historial_failure(solicitud, historial_payload, historial_raw, str(exc))
        historial_pago = {"estado": "ERROR", "resumen": str(exc), "metrics": {}}
        integration_errors["historial_pago"] = str(exc)

    return _compact_preselecta_snapshot(datos_preselecta), historial_pago, integration_errors


def build_consumo_snapshot(*, solicitud) -> dict[str, Any]:
    numero_identificacion = solicitud.solicitante.numero_identificacion
    primer_apellido = solicitud.solicitante.primer_apellido
    existing_basic = _existing_contact_data(
        solicitud.solicitante.tipo_identificacion,
        numero_identificacion,
    )
    datos_linix = consultar_capa(solicitud, numero_identificacion)
    datos_preselecta, historial_pago, integration_errors = _fetch_external_data(
        solicitud,
        numero_identificacion,
        primer_apellido,
        datos_linix,
    )
    external_summary = {
        "numero_identificacion": numero_identificacion,
        "primer_apellido": primer_apellido,
        "provider_mode": (datos_preselecta.get("identidad_efectiva") or {}).get("provider_mode", get_provider_mode()),
        "provider_test_case": (datos_preselecta.get("identidad_efectiva") or {}).get("provider_test_case", ""),
        "preselecta_estado_negocio": datos_preselecta.get("estado_negocio"),
        "preselecta_decision": datos_preselecta.get("decision"),
        "preselecta_risk_level": datos_preselecta.get("risk_level"),
        "preselecta_identidad_efectiva": datos_preselecta.get("identidad_efectiva"),
        "preselecta_puede_continuar": datos_preselecta.get("puede_continuar"),
        "preselecta_mensaje_usuario": datos_preselecta.get("mensaje_usuario"),
        "historial_ejecutado": bool(historial_pago),
        "integration_errors": integration_errors,
    }
    audit_event(
        "consumo_external_summary",
        solicitud=solicitud,
        level="WARNING" if integration_errors else "INFO",
        payload=external_summary,
    )
    datos_datacredito = _datos_datacredito(historial_pago) if historial_pago else {}
    consolidated_values = _merge_consolidated_values(
        datos_linix=datos_linix,
        datos_preselecta=datos_preselecta,
        datos_datacredito=datos_datacredito,
        historial_pago=historial_pago,
    )
    preselecta_can_continue = bool(datos_preselecta.get("puede_continuar"))
    blocked_fields = _collect_blocked_fields(consolidated_values) if preselecta_can_continue else []
    editable_fields = _editable_fields(blocked_fields) if preselecta_can_continue else []
    missing_fields = _missing_fields(editable_fields, consolidated_values) if preselecta_can_continue else []

    return {
        "datos_linix": datos_linix,
        "datos_datacredito": datos_datacredito,
        "datos_preselecta": datos_preselecta,
        "historial_pago": historial_pago,
        "external_summary": external_summary,
        "datos_basicos": existing_basic,
        "campos_basicos_faltantes": _missing_fields(("fecha_expedicion", "celular", "email"), existing_basic),
        "campos_basicos_bloqueados": [],
        "valores_consolidados": consolidated_values if preselecta_can_continue else {},
        "campos_editables": editable_fields,
        "campos_bloqueados": blocked_fields,
        "campos_faltantes": missing_fields,
        "nota_datos_oficiales": READONLY_NOTICE,
        "integration_errors": integration_errors,
        "nombre_completo": _full_name(datos_linix.get("nombre")),
        "primer_apellido_interno": primer_apellido,
        "can_continue": preselecta_can_continue,
    }


def persist_orchestration_snapshot(detail: SolicitudConsumo) -> SolicitudConsumo:
    snapshot = build_consumo_snapshot(solicitud=detail.solicitud)
    historial_snapshot = snapshot.get("historial_pago", {})
    if historial_snapshot.get("xml_payload"):
        persist_historial_normalized(detail.solicitud, historial_snapshot)
    detail.orchestration_data = snapshot
    detail.core_data = snapshot.get("datos_linix", {})
    detail.oracle_consultado = bool(snapshot.get("datos_linix"))
    detail.save(update_fields=("orchestration_data", "core_data", "oracle_consultado", "updated_at"))
    return detail


def persist_initial_orchestration_snapshot(detail: SolicitudConsumo) -> SolicitudConsumo:
    detail.orchestration_data = {
        "datos_linix": {},
        "datos_datacredito": {},
        "datos_preselecta": {},
        "historial_pago": {},
        "datos_basicos": {},
        "campos_basicos_faltantes": ["fecha_expedicion", "celular", "email"],
        "campos_basicos_bloqueados": [],
        "valores_consolidados": {},
        "campos_editables": list(ALL_XCORE_FIELD_KEYS),
        "campos_bloqueados": [],
        "campos_faltantes": list(ALL_XCORE_FIELD_KEYS),
        "nota_datos_oficiales": READONLY_NOTICE,
        "integration_errors": {},
        "external_summary": {},
        "nombre_completo": "",
        "primer_apellido_interno": detail.solicitud.solicitante.primer_apellido,
    }
    detail.save(update_fields=("orchestration_data", "updated_at"))
    return detail





