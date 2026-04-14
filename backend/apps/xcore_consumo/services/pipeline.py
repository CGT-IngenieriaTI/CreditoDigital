import hashlib
import logging
import unicodedata

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.decisiones.models import ResultadoDecision
from apps.decisiones.services import persist_final_decision
from apps.documentos.models import AceptacionDocumento
from apps.documentos.services import get_active_documents
from apps.historial_pago.models import HistorialPagoConsulta
from apps.historial_pago.services import build_historial_from_stored_xml, persist_historial_consulta, persist_historial_failure
from apps.preselecta.client import PreselectaClient
from apps.utils.logging import audit_event
from apps.xcore_consumo.models import (
    ConsentimientoConsumo,
    EstadoOtp,
    EstadoSolicitudConsumo,
    EvaluacionConsumo,
    SolicitudConsumo,
)
from apps.xcore_consumo.serializers import CONSENTIMIENTO_RESUMEN_CENTRALES, CONSENTIMIENTO_VERSION
from apps.xcore_consumo.services.calculadora import build_consumo_decision_pdf, evaluar_xcore_consumo
from apps.xcore_consumo.services.flow import (
    assert_consent_granted,
    assert_otp_verified,
    get_solicitud_state_for_consumo,
    sync_consumo_state,
    sync_solicitud_state,
)
from apps.xcore_consumo.services.orchestration import (
    _compact_preselecta_snapshot,
    persist_orchestration_snapshot,
)
from apps.xcore_consumo.services.orchestration import normalize_preselecta_business_status
from apps.xcore_consumo.services.provider_test_identities import (
    get_provider_mode,
    get_provider_test_case,
    get_provider_test_identity,
    use_provider_test_identity,
)
from apps.xcore_consumo.services.oracle import consultar_capa, consultar_familiar
from apps.xcore_consumo.services.otp import build_consent_hash


logger = logging.getLogger("credito")


def _normalize_text(value):
    if not value:
        return ""
    return unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().strip().lower()


def get_detail(solicitud_id):
    return SolicitudConsumo.objects.select_related(
        "solicitud",
        "solicitud__solicitante",
    ).get(solicitud_id=solicitud_id)


def registrar_consentimiento(request, solicitud, payload, *, actor: str):
    challenge = solicitud.active_otp_challenge
    now = timezone.now()
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", ""))
    if "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()
    text_snapshot = payload.get("text_snapshot") or CONSENTIMIENTO_RESUMEN_CENTRALES
    version = payload.get("version") or CONSENTIMIENTO_VERSION
    otp_validada = bool(challenge and challenge.estado == EstadoOtp.VALIDADA and challenge.verificado_at)
    consent_hash = build_consent_hash(
        version,
        text_snapshot,
        challenge.transaction_uuid if otp_validada else "",
        challenge.canal if otp_validada else payload["canal"],
        challenge.verificado_at if otp_validada else None,
    )
    accepted_documents = {
        item["document_id"]: item for item in payload.get("accepted_documents", [])
    }

    consentimiento, _ = ConsentimientoConsumo.objects.update_or_create(
        solicitud=solicitud,
        defaults={
            "otp": challenge,
            "version": version,
            "aceptado": True,
            "firmado": otp_validada,
            "canal": payload["canal"],
            "fecha_aceptacion": now,
            "ip_address": ip_address or None,
            "user_agent": request.headers.get("User-Agent", "")[:255],
            "text_snapshot": text_snapshot,
            "text_hash": consent_hash,
            "tipo_firma": "ACEPTACION_OTP" if otp_validada else "ACEPTACION_PENDIENTE_OTP",
            "evidencia": {
                "accepted_by": actor,
                "otp_verified_at": challenge.verificado_at.isoformat() if otp_validada else "",
                "otp_channel": challenge.canal if otp_validada else payload["canal"],
                "provider": challenge.provider if otp_validada else "",
                "destination_masked": challenge.destination_masked if otp_validada else "",
                "transaction_uuid": challenge.transaction_uuid if otp_validada else "",
                "verification_sid": challenge.verification_sid if otp_validada else "",
                "verification_check_sid": challenge.verification_check_sid if otp_validada else "",
            },
        },
    )

    for document in get_active_documents():
        if document.id not in accepted_documents:
            continue
        accepted_payload = accepted_documents[document.id]
        AceptacionDocumento.objects.update_or_create(
            solicitud=solicitud,
            documento=document,
            defaults={
                "aceptado": True,
                "fecha_aceptacion": now,
                "visualizacion_segundos": accepted_payload["viewed_seconds"],
                "llego_al_final": accepted_payload["reached_end"],
                "ip_address": ip_address or None,
                "user_agent": request.headers.get("User-Agent", "")[:255],
            },
        )

    detail = get_detail(solicitud.id)
    next_state = (
        EstadoSolicitudConsumo.CONSENTIMIENTO_FIRMADO if otp_validada else EstadoSolicitudConsumo.OTP_PENDIENTE
    )
    next_step = "analisis" if otp_validada else "otp"
    sync_consumo_state(detail, estado=next_state)
    detail.documentos_autorizados = True
    detail.save(update_fields=("documentos_autorizados", "updated_at"))
    sync_solicitud_state(
        solicitud,
        estado=get_solicitud_state_for_consumo(next_state),
        paso_actual=next_step,
    )
    audit_event(
        "consumo_consentimiento_registrado",
        solicitud=solicitud,
        actor=actor,
        payload={
            "version": version,
            "channel": payload["canal"],
            "text_hash": consent_hash,
            "otp_linked": otp_validada,
            "documents": payload.get("accepted_documents", []),
        },
    )
    return detail, consentimiento


def consultar_core(solicitud, *, actor: str):
    assert_otp_verified(solicitud)
    assert_consent_granted(solicitud)
    detail = get_detail(solicitud.id)
    detail = persist_orchestration_snapshot(detail)
    detail.ultimo_error = ""
    detail.save(update_fields=("ultimo_error", "updated_at"))
    sync_consumo_state(detail, estado=EstadoSolicitudConsumo.CORE_CONSULTADO)
    sync_solicitud_state(
        solicitud,
        estado=get_solicitud_state_for_consumo(EstadoSolicitudConsumo.CORE_CONSULTADO),
        paso_actual="analisis",
    )
    audit_event(
        "consumo_core_consultado",
        solicitud=solicitud,
        actor=actor,
        payload={"core_data": detail.core_data, "orchestration": detail.orchestration_data},
    )
    return detail


def guardar_formulario_xcore(solicitud, form_data, *, actor: str):
    assert_otp_verified(solicitud)
    assert_consent_granted(solicitud)
    detail = get_detail(solicitud.id)
    normalized_form_data = dict(form_data)
    normalized_form_data["tasa_cupos_rotativos"] = str(settings.XCORE_CONSUMO_TASA_CUPOS_ROTATIVOS)
    if detail.core_data.get("activos") not in (None, ""):
        normalized_form_data["activos"] = detail.core_data.get("activos")
    if detail.core_data.get("valor_activos") not in (None, ""):
        normalized_form_data["valor_activos"] = detail.core_data.get("valor_activos")
    historial_metrics = ((detail.orchestration_data or {}).get("historial_pago", {}) or {}).get("metrics", {}) or {}
    normalized_form_data["cuotas_creditos_codeudor"] = (
        detail.form_data.get("cuotas_creditos_codeudor")
        if detail.form_data and "cuotas_creditos_codeudor" in detail.form_data
        else historial_metrics.get("cuota_abierta_codeudor", historial_metrics.get("cuotas_creditos_codeudor", 0))
    )
    detail.form_data = normalized_form_data
    detail.ultimo_error = ""
    detail.save(update_fields=("form_data", "ultimo_error", "updated_at"))
    sync_consumo_state(detail, estado=EstadoSolicitudConsumo.FORMULARIO_XCORE_OK)
    sync_solicitud_state(
        solicitud,
        estado=get_solicitud_state_for_consumo(EstadoSolicitudConsumo.FORMULARIO_XCORE_OK),
        paso_actual="analisis",
    )
    audit_event(
        "consumo_formulario_guardado",
        solicitud=solicitud,
        actor=actor,
        payload={"keys": sorted(form_data.keys())},
    )
    return detail


def _merge_historial_metrics(form_data, historial_response):
    metrics = historial_response.get("metrics", {})
    merged = dict(form_data)
    if not metrics:
        merged["pasivos"] = "NO"
        return merged
    merged["valor_pasivos"] = metrics.get("valor_pasivos", 0)
    merged["valor_pasivos_recoge"] = metrics.get(
        "valor_pasivos_que_recoge", merged.get("valor_pasivos_recoge", 0)
    )
    merged["saldo_creditos"] = metrics.get(
        "saldo_total_creditos_deudor_principal",
        metrics.get("saldo_total_creditos", merged.get("saldo_creditos", 0)),
    )
    merged["cupos_tarjetas_rotativos"] = metrics.get(
        "cupos_tarjetas_rotativos", merged.get("cupos_tarjetas_rotativos", 0)
    )
    merged["tasa_cupos_rotativos"] = str(settings.XCORE_CONSUMO_TASA_CUPOS_ROTATIVOS)
    merged["cuotas_creditos_egresos"] = metrics.get(
        "total_cuotas_credito_deudor_principal",
        metrics.get("total_cuotas_credito", merged.get("cuotas_creditos_egresos", 0)),
    )
    merged["cuotas_creditos_codeudor"] = metrics.get(
        "cuota_abierta_codeudor",
        metrics.get("cuotas_creditos_codeudor", merged.get("cuotas_creditos_codeudor", 0)),
    )
    merged["valor_cuotas_recoge_per"] = metrics.get(
        "valor_cuota_que_recoge_pago_personal", merged.get("valor_cuotas_recoge_per", 0)
    )
    try:
        merged["pasivos"] = "SI" if float(metrics.get("valor_pasivos") or 0) > 0 else "NO"
    except (TypeError, ValueError):
        merged["pasivos"] = "NO"
    return merged


def _payload_preselecta(solicitud, detail):
    if use_provider_test_identity():
        case_name = get_provider_test_case()
        provider = get_provider_test_identity()
        payload = {
            "idNumber": provider["numero_identificacion"],
            "idType": provider["tipo_identificacion"],
            "firstLastName": provider["primer_apellido"],
            "linea_credito": provider["linea_credito"],
            "tipo_asociado": provider["tipo_asociado"],
            "medio_pago": provider["medio_pago"],
            "actividad": provider["actividad"],
            "provider_mode": "test",
            "provider_test_identity": True,
            "provider_test_case": case_name,
        }
        return payload
    form_data = detail.form_data
    core_nombre = str(detail.core_data.get("nombre", "") or "").strip()
    primer_apellido = core_nombre.split()[0].upper() if core_nombre else solicitud.solicitante.primer_apellido
    tipo_cliente = _normalize_text(form_data.get("tipo_cliente", ""))
    forma_pago = _normalize_text(form_data.get("forma_pago", ""))
    ocupacion = _normalize_text(form_data.get("ocupacion", ""))
    return {
        "idNumber": solicitud.solicitante.numero_identificacion,
        "idType": "1",
        "firstLastName": primer_apellido,
        "linea_credito": "1",
        "tipo_asociado": "1" if tipo_cliente == "nuevo" else "2",
        "medio_pago": "2" if "nomina" in forma_pago else "1",
        "provider_mode": "real",
        "actividad": "3"
        if any(token in ocupacion for token in ["independ", "profesional", "rentista", "transport"])
        else "1",
    }


class Hc2SelectionRequired(Exception):
    def __init__(self, payload):
        super().__init__("La consulta de historial requiere seleccionar obligaciones para recoger.")
        self.payload = payload


def _consultar_historial(detail, identity_effective=None, *, selected_keys=None):
    solicitud = detail.solicitud
    if identity_effective:
        historial_payload = {
            "numero_solicitud": f"PREV-{identity_effective['numero_identificacion']}",
            "tipo_identificacion": identity_effective.get("tipo_identificacion", "1"),
            "numero_identificacion": identity_effective["numero_identificacion"],
            "primer_apellido": identity_effective.get("primer_apellido", ""),
            "provider_mode": identity_effective.get("provider_mode", "real"),
            "provider_test_identity": bool(identity_effective.get("provider_test_identity")),
            "provider_test_case": identity_effective.get("provider_test_case", ""),
        }
    elif use_provider_test_identity():
        case_name = get_provider_test_case()
        provider = get_provider_test_identity()
        historial_payload = {
            "numero_solicitud": f"PREV-{provider['numero_identificacion']}",
            "tipo_identificacion": provider["tipo_identificacion"],
            "numero_identificacion": provider["numero_identificacion"],
            "primer_apellido": provider["primer_apellido"],
            "provider_mode": "test",
            "provider_test_identity": True,
            "provider_test_case": case_name,
        }
    else:
        historial_payload = {
            "numero_solicitud": solicitud.numero_solicitud,
            "tipo_identificacion": "1",
            "numero_identificacion": solicitud.solicitante.numero_identificacion,
            "primer_apellido": solicitud.solicitante.primer_apellido,
            "provider_mode": "real",
        }

    consulta_existente = HistorialPagoConsulta.objects.filter(solicitud=solicitud).first()
    same_identity = bool(
        consulta_existente
        and consulta_existente.request_payload.get("numero_identificacion") == historial_payload.get("numero_identificacion")
        and consulta_existente.request_payload.get("primer_apellido", "") == historial_payload.get("primer_apellido", "")
    )
    if consulta_existente and consulta_existente.xml_payload and same_identity:
        normalized = build_historial_from_stored_xml(consulta_existente, selected_keys=selected_keys)
        return historial_payload, {"xml_payload": consulta_existente.xml_payload, "source": "stored_xml"}, normalized

    from apps.historial_pago.client import HistorialPagoSOAPClient

    raw_historial = HistorialPagoSOAPClient().consult(historial_payload)
    try:
        _, normalized = persist_historial_consulta(
            solicitud,
            historial_payload,
            raw_historial,
            selected_keys=selected_keys,
        )
    except Exception as exc:
        persist_historial_failure(solicitud, historial_payload, raw_historial, str(exc))
        raise
    return historial_payload, raw_historial, normalized


def _assert_preselecta_can_continue(preselecta_response):
    normalized = normalize_preselecta_business_status(preselecta_response)
    if not normalized.get("puede_continuar"):
        raise ValueError(
            normalized.get("mensaje_usuario")
            or "Por ahora no es posible continuar con la solicitud de crédito."
        )
    return normalized


def procesar_consumo(solicitud, *, actor: str, selected_hc2_keys=None):
    assert_otp_verified(solicitud)
    assert_consent_granted(solicitud)
    detail = get_detail(solicitud.id)
    if not detail.oracle_consultado:
        raise ValueError("Debes consultar el core antes de procesar la solicitud.")
    if not detail.form_data:
        raise ValueError("Debes guardar el formulario antes de procesar.")

    sync_consumo_state(detail, estado=EstadoSolicitudConsumo.PROCESANDO)
    sync_solicitud_state(
        solicitud,
        estado=get_solicitud_state_for_consumo(EstadoSolicitudConsumo.PROCESANDO),
        paso_actual="analisis",
    )

    preselecta_payload = _payload_preselecta(solicitud, detail)
    logger.info(
        "consumo.process provider_mode=%s provider_test_case=%s numero_identificacion=%s",
        preselecta_payload.get("provider_mode", get_provider_mode()),
        preselecta_payload.get("provider_test_case", ""),
        preselecta_payload.get("idNumber", ""),
    )
    preselecta_response = _compact_preselecta_snapshot(
        _assert_preselecta_can_continue(PreselectaClient().evaluate(preselecta_payload))
    )

    historial_payload, raw_historial, preview_historial = _consultar_historial(
        detail,
        preselecta_response.get("identidad_efectiva") or preselecta_response.get("identity_effective"),
        selected_keys=[],
    )
    elegibles = [row for row in preview_historial.get("obligaciones_abiertas", []) if row.get("elegible_recoge")]
    if selected_hc2_keys is None and elegibles:
        raise Hc2SelectionRequired(
            {
                "requires_hc2_selection": True,
                "obligaciones_abiertas": preview_historial.get("obligaciones_abiertas", []),
                "metrics_preview": preview_historial.get("metrics", {}),
                "metrics_formatted_preview": preview_historial.get("metrics_formatted", {}),
            }
        )

    selected_keys = detail.selected_hc2_keys if selected_hc2_keys is None else list(selected_hc2_keys)
    historial_payload["selected_keys"] = selected_keys
    if selected_keys:
        consulta_historial = HistorialPagoConsulta.objects.filter(solicitud=solicitud).first()
        if consulta_historial and consulta_historial.xml_payload:
            historial_response = build_historial_from_stored_xml(consulta_historial, selected_keys=selected_keys)
        else:
            _, raw_historial, historial_response = _consultar_historial(
                detail,
                preselecta_response.get("identidad_efectiva") or preselecta_response.get("identity_effective"),
                selected_keys=selected_keys,
            )
    else:
        historial_response = preview_historial

    familiar = consultar_familiar(solicitud, solicitud.solicitante.numero_identificacion)
    historial_response["tiene_novedad"] = familiar.get("resultado", "NO")
    historial_response["novedad_descripcion"] = familiar.get("tipofamiliar", "")

    with transaction.atomic():
        form_data = _merge_historial_metrics(detail.form_data, historial_response)
        if detail.core_data.get("activos") not in (None, ""):
            form_data["activos"] = detail.core_data.get("activos")
        if detail.core_data.get("valor_activos") not in (None, ""):
            form_data["valor_activos"] = detail.core_data.get("valor_activos")
        detail.form_data = form_data
        detail.selected_hc2_keys = list(selected_keys)
        resultados = evaluar_xcore_consumo(form_data, detail.core_data, historial_response, preselecta_response)
        if resultados.get("error"):
            raise ValueError(str(resultados["error"]))
        evaluacion, _ = EvaluacionConsumo.objects.update_or_create(
            solicitud=solicitud,
            defaults={
                "input_snapshot": {"core_data": detail.core_data, "form_data": form_data},
                "integraciones_snapshot": {
                    "preselecta_snapshot": preselecta_response,
                    "historial_payload": historial_payload,
                    "historial_response": historial_response,
                    "historial_raw": raw_historial,
                    "consent_hash": getattr(solicitud.consentimiento_consumo, "text_hash", ""),
                },
                "resultados": resultados,
                "puntaje_xcore": resultados.get("puntaje_xcore", 0),
                "perfil_riesgo": resultados.get("resultado_perfil_riesgo", ""),
                "perfil_credito": resultados.get("resultado_perfil_credito", ""),
                "capacidad_pago_final": resultados.get("resultado_capacidad_pago_decision", ""),
                "decision_final": resultados.get("decision_final", ""),
                "estamento": resultados.get("estamento", ""),
                "tiene_novedad": str(resultados.get("tiene_novedad", "NO")).upper() == "SI",
                "novedad_descripcion": resultados.get("novedad_descripcion", ""),
                "monto_max_posible": resultados.get("valor_monto_max_decision") or None,
                "valor_cuota": resultados.get("total_valor_cuota") or None,
                "vida_deudores": resultados.get("total_vida_deudores") or None,
            },
        )
        detail.estado = EstadoSolicitudConsumo.FINALIZADO
        detail.ultimo_error = ""
        detail.save(update_fields=("form_data", "selected_hc2_keys", "estado", "ultimo_error", "updated_at"))

        decision_resultado = (
            ResultadoDecision.APROBADO
            if resultados.get("decision_final") == "Crédito Aprobado"
            else ResultadoDecision.REVISION
            if resultados.get("decision_final") == "Zona gris"
            else ResultadoDecision.RECHAZADO
        )
        decision = persist_final_decision(
            solicitud,
            {
                "resultado": decision_resultado,
                "mensaje": resultados.get("decision_final", "Sin decisión"),
                "monto_aprobado": resultados.get("valor_monto_max_decision"),
                "plazo_aprobado": int(float(form_data.get("plazo") or 0)) or None,
                "tasa_interes": resultados.get("tasa_efectiva_calculada") or form_data.get("tasa_efectiva_anual"),
                "detalle": resultados,
            },
            observaciones=resultados.get("novedad_descripcion", ""),
        )
        pdf_bytes = build_consumo_decision_pdf(solicitud, evaluacion)
        evaluacion.pdf_generado = True
        evaluacion.save(update_fields=("pdf_generado", "updated_at"))
        audit_event(
            "consumo_procesado",
            solicitud=solicitud,
            actor=actor,
            payload={"decision": decision.resultado},
        )
        return evaluacion, decision, pdf_bytes






