import logging

from apps.historial_pago.extractor import extract_financial_metrics


logger = logging.getLogger("credito")

MANUAL_XML_FIELDS = {
    "valor_cuotas_recoge_nom": "manual",
}


def normalize_historial_response(raw: dict, selected_keys=None) -> dict:
    xml_payload = raw.get("xml_payload") or raw.get("xml") or raw.get("raw_xml") or raw.get("raw")
    if xml_payload:
        extracted = extract_financial_metrics(xml_payload, selected_keys=selected_keys)
        normalized = {
            "estado": raw.get("estado", "OK"),
            "score_pago": raw.get("score_pago", 700),
            "mora_maxima": raw.get("mora_maxima", 0),
            "categoria": raw.get("categoria", "A"),
            "resumen": raw.get("resumen", "Respuesta SOAP recibida y normalizada."),
            "obligaciones_abiertas": extracted.get("obligaciones_abiertas", []),
            "selected_keys_applied": extracted.get("selected_keys_applied", []),
            "metrics": extracted.get("metrics", {}),
            "metrics_formatted": extracted.get("metrics_formatted", {}),
            "config": extracted.get("config", {}),
            "xml_payload": xml_payload,
            "source": "stored_xml" if raw.get("xml_payload") else "live",
            "manual_fields": MANUAL_XML_FIELDS,
        }
        return normalized
    return raw
