from __future__ import annotations

import logging

from .adapters import normalize_historial_response
from .client import HistorialPagoSOAPClient
from .models import HistorialPagoConsulta
from .serializers import HistorialPagoResponseSerializer


logger = logging.getLogger("credito")


def persist_historial_normalized(solicitud, normalized: dict, *, request_payload: dict | None = None):
    serializer = HistorialPagoResponseSerializer(data=normalized)
    serializer.is_valid(raise_exception=True)

    xml_payload = normalized.get("xml_payload") or ""
    soap_request_xml = normalized.get("soap_request_xml") or ""
    response_payload = dict(normalized)
    response_payload["source"] = normalized.get("source", "live")

    consulta, _ = HistorialPagoConsulta.objects.update_or_create(
        solicitud=solicitud,
        defaults={
            "estado": serializer.validated_data["estado"],
            "request_payload": request_payload or {},
            "response_payload": response_payload,
            "xml_payload": xml_payload,
            "soap_request_xml": soap_request_xml,
            "score_pago": serializer.validated_data["score_pago"],
            "mora_maxima": serializer.validated_data["mora_maxima"],
            "categoria": serializer.validated_data["categoria"],
            "resumen": serializer.validated_data["resumen"],
        },
    )
    return consulta

def persist_historial_failure(solicitud, request_payload: dict, raw_response: dict | None, error_message: str):
    raw_response = raw_response or {}
    xml_payload = raw_response.get("xml") or raw_response.get("raw_xml") or raw_response.get("xml_payload") or ""
    soap_request_xml = raw_response.get("soap_request_xml") or ""
    consulta, _ = HistorialPagoConsulta.objects.update_or_create(
        solicitud=solicitud,
        defaults={
            "estado": "ERROR",
            "request_payload": request_payload or {},
            "response_payload": {"error": error_message, "source": raw_response.get("source", "live")},
            "xml_payload": xml_payload,
            "soap_request_xml": soap_request_xml,
            "score_pago": None,
            "mora_maxima": 0,
            "categoria": "",
            "resumen": error_message[:255],
        },
    )
    return consulta



def _base_historial_payload(raw_response: dict | None = None, consulta: HistorialPagoConsulta | None = None) -> dict:
    raw_response = raw_response or {}
    payload = {
        "estado": raw_response.get("estado") or (consulta.estado if consulta else "OK"),
        "score_pago": raw_response.get("score_pago") if raw_response.get("score_pago") is not None else (consulta.score_pago if consulta else 700),
        "mora_maxima": raw_response.get("mora_maxima") if raw_response.get("mora_maxima") is not None else (consulta.mora_maxima if consulta else 0),
        "categoria": raw_response.get("categoria") or (consulta.categoria if consulta else "A"),
        "resumen": raw_response.get("resumen") or (consulta.resumen if consulta else "Respuesta SOAP recibida y normalizada."),
        "soap_request_xml": raw_response.get("soap_request_xml") or (consulta.soap_request_xml if consulta else ""),
    }
    return payload


def build_historial_from_stored_xml(consulta: HistorialPagoConsulta, *, selected_keys=None) -> dict:
    payload = _base_historial_payload(consulta=consulta)
    payload["xml_payload"] = consulta.xml_payload
    payload["soap_request_xml"] = consulta.soap_request_xml
    normalized = normalize_historial_response(payload, selected_keys=selected_keys)
    normalized["source"] = "stored_xml"
    return normalized


def persist_historial_consulta(solicitud, request_payload: dict, raw_response: dict, *, selected_keys=None):
    normalized = normalize_historial_response(raw_response, selected_keys=selected_keys)
    normalized["xml_payload"] = raw_response.get("xml") or raw_response.get("raw_xml") or raw_response.get("xml_payload") or normalized.get("xml_payload") or ""
    normalized["soap_request_xml"] = raw_response.get("soap_request_xml") or normalized.get("soap_request_xml") or ""

    consulta = persist_historial_normalized(solicitud, normalized, request_payload=request_payload)
    return consulta, normalized


def run_historial_pago(solicitud):
    applicant = solicitud.solicitante
    payload = {
        "numero_solicitud": solicitud.numero_solicitud,
        "tipo_identificacion": applicant.tipo_identificacion,
        "numero_identificacion": applicant.numero_identificacion,
    }
    raw_response = HistorialPagoSOAPClient().consult(payload)
    consulta, _ = persist_historial_consulta(solicitud, payload, raw_response)
    return consulta
