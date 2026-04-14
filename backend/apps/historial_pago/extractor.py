import logging
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from html import unescape

try:
    from lxml import etree as lxml_etree
except ImportError:  # pragma: no cover
    lxml_etree = None


ESTADO_PAGO_VIGENTE = {
    "01", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23",
    "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35",
    "36", "37", "38", "39", "40", "41", "45", "47", "60",
}
ESTADO_PAGO_CERRADA = {"02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "46", "49"}
ESTADO_CUENTA_CERRADA = {"03", "04", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17"}
TELCO_TIPOS = {"CTC", "CDC", "COM"}
INVALID_XML_CHAR_RE = re.compile(r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]")
BARE_AMPERSAND_RE = re.compile(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|\w+;)")
GROUPED_PESOS_RE = re.compile(r"^\d{1,3}(?:[\.,]\d{3})+$")
logger = logging.getLogger("credito")


def _as_bool_env(name, default):
    return str(os.getenv(name, str(default))).strip().lower() in {"1", "true", "t", "yes", "si", "on"}


def _as_set_env(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return set(default)
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _normalize_entity_name(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text


def _is_excluded_entity(value, excluded_entities):
    normalized = _normalize_entity_name(value)
    return any(token in normalized for token in excluded_entities)


def _sanitize_xml_payload(text):
    cleaned = INVALID_XML_CHAR_RE.sub("", text or "")
    cleaned = BARE_AMPERSAND_RE.sub("&amp;", cleaned)
    return cleaned


def _coerce_xml_string(xml_input):
    if isinstance(xml_input, bytes):
        text = xml_input.decode("utf-8", errors="ignore")
    else:
        text = str(xml_input or "")
    text = text.replace("\ufeff", "").strip()
    if "&lt;" in text and "&gt;" in text:
        text = unescape(text)
    idx = text.find("<Informes")
    if idx >= 0:
        text = text[idx:]
    else:
        first_tag_idx = text.find("<")
        if first_tag_idx > 0:
            text = text[first_tag_idx:]
    return _sanitize_xml_payload(text).strip()


def _clean_xml_payload(xml_input):
    return _coerce_xml_string(xml_input)


def _parse_root(xml_input):
    payload = _coerce_xml_string(xml_input)
    if not payload:
        raise ValueError("XML vacio")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        if lxml_etree is not None:
            try:
                parser = lxml_etree.XMLParser(recover=True, huge_tree=True)
                recovered_root = lxml_etree.fromstring(payload.encode("utf-8", errors="ignore"), parser=parser)
                if recovered_root is not None:
                    recovered_xml = lxml_etree.tostring(recovered_root, encoding="utf-8")
                    logger.warning(
                        "historial_pago.xml_recovered original_len=%s recovered_len=%s parse_error=%s",
                        len(payload),
                        len(recovered_xml or b""),
                        exc,
                    )
                    return ET.fromstring(recovered_xml)
            except Exception:
                pass
        preview = payload[:240].replace("\n", " ").replace("\r", " ")
        raise ValueError(f"XML invalido o no parseable. Inicio recibido: {preview}") from exc


def _attr(elem, key, default=""):
    return str(elem.attrib.get(key, default)).strip() if elem is not None else default


def _latest_valor(parent):
    if parent is None:
        return None
    valores = parent.findall("Valores/Valor")
    return valores[-1] if valores else parent.find("Valores/Valor")


def _parse_number(value):
    text = str(value or "").strip()
    if text in {"", "-", "--", "N", "NN", "N/A"}:
        return 0.0
    text = text.replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = text.split(",")[-1]
        text = text.replace(",", ".") if len(tail) in {1, 2} else text.replace(",", "")
    try:
        return float(text)
    except Exception:
        return 0.0


def _to_int(value):
    return int(round(float(value or 0.0)))


def _fmt(value):
    return f"{_to_int(float(value)):,}".replace(",", ".")


def _to_pesos_from_account(value, multiplier):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    normalized = raw.replace(" ", "")
    base_multiplier = float(multiplier or 1)

    # Account-level values may already arrive grouped in COP, e.g. 1.027.000 or 56,000.
    if GROUPED_PESOS_RE.fullmatch(normalized):
        return float(re.sub(r"[^0-9-]", "", normalized) or 0)

    numeric = _parse_number(normalized)
    if numeric == 0:
        return 0.0

    if abs(numeric) >= 10000:
        return numeric

    return numeric * base_multiplier


def _to_pesos_from_aggregate(value, multiplier):
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    normalized = raw.replace(" ", "")
    base_multiplier = float(multiplier or 1)

    # Aggregate values in EndeudamientoActual are expressed in thousands.
    # Examples:
    # - 4.674    -> 4,674,000 COP
    # - 37696.0  -> 37,696,000 COP
    # If the payload already arrives grouped as full pesos (4.674.000), keep it.
    if GROUPED_PESOS_RE.fullmatch(normalized):
        digits = float(re.sub(r"[^0-9-]", "", normalized) or 0)
        separator_count = normalized.count(".") + normalized.count(",")
        return digits if separator_count >= 2 else digits * base_multiplier

    numeric = _parse_number(normalized)
    if numeric == 0:
        return 0.0
    return numeric * base_multiplier


def _to_pesos(value, multiplier, *, aggregate=False):
    if aggregate:
        return _to_pesos_from_aggregate(value, multiplier)
    return _to_pesos_from_account(value, multiplier)


def _infer_condicion(estado_pago, estado_cuenta):
    pago = str(estado_pago or "").strip()
    cuenta = str(estado_cuenta or "").strip()
    if pago in ESTADO_PAGO_VIGENTE:
        return "VIGENTE"
    if pago in ESTADO_PAGO_CERRADA or cuenta in ESTADO_CUENTA_CERRADA:
        return "CERRADA"
    return "DESCONOCIDA"


def _is_telco(sector_code, tipo_cuenta):
    return str(sector_code or "").strip() == "4" or str(tipo_cuenta or "").strip().upper() in TELCO_TIPOS


def _obligation_key(source, tipo_cuenta, entidad, numero, llave):
    if llave:
        return llave.strip()
    return "|".join([(source or "").strip(), (tipo_cuenta or "").strip(), (entidad or "").strip(), (numero or "").strip()])


def _normalize_role(value):
    raw = str(value or "").strip()
    if not raw:
        return "Deudor principal"
    normalized = raw.lower()
    if raw == "01" or "codeudor" in normalized or "co-deudor" in normalized:
        return "Codeudor"
    if raw == "00" or "principal" in normalized or "deudor" in normalized:
        return "Deudor principal"
    return raw


def _is_principal(role):
    return _normalize_role(role) == "Deudor principal"


def _estado_detalle(cuenta, estado_pago, condicion):
    detail = _attr(cuenta, "estadoActual") or _attr(cuenta, "estado")
    if detail:
        return detail
    if estado_pago and estado_pago not in {"01"}:
        return f"Estado {estado_pago}"
    return condicion.title()


def _build_obligation_row(*, source, cuenta, tipo_cuenta, sector_code, saldo_actual, valor_cuota, valor_inicial, condicion, elegible, blocked):
    entidad = _attr(cuenta, "entidad")
    numero_cuenta = _attr(cuenta, "numero") or _attr(cuenta, "numeroCuenta")
    llave = (cuenta.findtext("Llave") or "").strip()
    role = _normalize_role(_attr(cuenta, "calidadDeudor") or _attr(cuenta, "rol") or cuenta.findtext("CalidadDeudor"))
    estado_pago = _attr(cuenta.find("Estados/EstadoPago"), "codigo")
    key = _obligation_key(source, tipo_cuenta, entidad, numero_cuenta, llave)
    return {
        "key": key,
        "source": source,
        "tipo_cuenta": tipo_cuenta,
        "sector_code": sector_code,
        "entidad": entidad,
        "numero_cuenta": numero_cuenta,
        "saldo_actual": _to_int(saldo_actual),
        "valor_cuota": _to_int(valor_cuota),
        "valor_inicial": _to_int(valor_inicial),
        "condicion": condicion,
        "rol": role,
        "estado_detalle": _estado_detalle(cuenta, estado_pago, condicion),
        "elegible_recoge": elegible,
        "motivo_no_elegible": "; ".join(blocked) if blocked else "",
    }


def extract_financial_metrics(xml_input, selected_keys=None):
    root = _parse_root(xml_input)
    informe = root.find("Informe")
    if informe is None:
        raise ValueError("XML no contiene nodo Informe")

    pesos_multiplier = _parse_number(os.getenv("HC2_PESOS_MULTIPLIER", "1000")) or 1000.0
    rotative_types = _as_set_env("HC2_ROTATIVE_TYPES", {"CBR"})
    exclude_telco = _as_bool_env("HC2_EXCLUDE_TELCO", True)
    exclude_tdc_recoge = _as_bool_env("HC2_EXCLUDE_TDC_IN_RECOGE", True)
    excluded_recoge_entities = _as_set_env("HC2_EXCLUDED_RECOGE_ENTITIES", {"SISTECREDITO", "ADDI"})
    selected_set = {str(k).strip() for k in (selected_keys or []) if str(k).strip()}

    obligaciones_abiertas = []
    saldo_total_creditos_principal = 0.0
    total_cuotas_credito_principal = 0.0
    saldo_abierto_codeudor = 0.0
    cuota_abierta_codeudor = 0.0
    cupos_rotativos = 0.0
    cupos_tarjetas = 0.0

    for cuenta in informe.findall("CuentaCartera"):
        valor = _latest_valor(cuenta)
        caracteristicas = cuenta.find("Caracteristicas")
        estados = cuenta.find("Estados")
        estado_pago = _attr(estados.find("EstadoPago") if estados is not None else None, "codigo")
        estado_cuenta = _attr(estados.find("EstadoCuenta") if estados is not None else None, "codigo")
        condicion = _infer_condicion(estado_pago, estado_cuenta)
        abierta = condicion == "VIGENTE"
        tipo_cuenta = (_attr(caracteristicas, "tipoCuenta") or _attr(cuenta, "tipoCuenta") or "CAB").upper()
        sector_code = _attr(cuenta, "sector")
        telco = _is_telco(sector_code, tipo_cuenta)
        rotative = tipo_cuenta in rotative_types
        saldo_actual = _to_pesos(_attr(valor, "saldoActual"), pesos_multiplier, aggregate=False)
        valor_cuota = _to_pesos(_attr(valor, "cuota"), pesos_multiplier, aggregate=False)
        valor_inicial = _to_pesos(_attr(valor, "valorInicial"), pesos_multiplier, aggregate=False)
        entidad = _attr(cuenta, "entidad")
        if _is_excluded_entity(entidad, excluded_recoge_entities):
            continue
        role = _normalize_role(
            _attr(caracteristicas, "calidadDeudor")
            or _attr(cuenta, "calidadDeudor")
            or _attr(cuenta, "rol")
            or cuenta.findtext("CalidadDeudor")
        )
        principal = _is_principal(role)

        blocked = []
        if exclude_tdc_recoge and tipo_cuenta == "TDC":
            blocked.append("TDC no permitido para recoger")
        if exclude_telco and telco:
            blocked.append("Telcos no permitido para recoger")
        if rotative:
            blocked.append("Rotativos no permitido para recoger")
        if not principal:
            blocked.append("Solo obligaciones como deudor principal")
        elegible = abierta and not blocked

        if abierta and principal and not rotative and (not telco or not exclude_telco) and tipo_cuenta != "TDC":
            saldo_total_creditos_principal += saldo_actual
            total_cuotas_credito_principal += valor_cuota
        elif abierta and not principal and not rotative and (not telco or not exclude_telco) and tipo_cuenta != "TDC":
            saldo_abierto_codeudor += saldo_actual
            cuota_abierta_codeudor += valor_cuota

        if abierta and rotative and (not telco or not exclude_telco):
            cupos_rotativos += valor_inicial

        if abierta:
            obligaciones_abiertas.append(
                _build_obligation_row(
                    source="CuentaCartera",
                    cuenta=cuenta,
                    tipo_cuenta=tipo_cuenta,
                    sector_code=sector_code,
                    saldo_actual=saldo_actual,
                    valor_cuota=valor_cuota,
                    valor_inicial=valor_inicial,
                    condicion=condicion,
                    elegible=elegible,
                    blocked=blocked,
                )
            )

    for cuenta in informe.findall("TarjetaCredito"):
        valor = _latest_valor(cuenta)
        estados = cuenta.find("Estados")
        estado_pago = _attr(estados.find("EstadoPago") if estados is not None else None, "codigo")
        estado_cuenta = _attr(estados.find("EstadoCuenta") if estados is not None else None, "codigo")
        condicion = _infer_condicion(estado_pago, estado_cuenta)
        abierta = condicion == "VIGENTE"
        sector_code = _attr(cuenta, "sector")
        cupo_total = _to_pesos(_attr(valor, "cupoTotal"), pesos_multiplier, aggregate=False)
        saldo_actual = _to_pesos(_attr(valor, "saldoActual"), pesos_multiplier, aggregate=False)
        valor_cuota = _to_pesos(_attr(valor, "cuota"), pesos_multiplier, aggregate=False)
        entidad = _attr(cuenta, "entidad")
        if _is_excluded_entity(entidad, excluded_recoge_entities):
            continue
        blocked = ["TDC no permitido para recoger"] if exclude_tdc_recoge else []
        if exclude_telco and _is_telco(sector_code, "TDC"):
            blocked.append("Telcos no permitido para recoger")
        if not _is_principal(
            _attr(cuenta.find("Caracteristicas"), "calidadDeudor")
            or _attr(cuenta, "calidadDeudor")
            or _attr(cuenta, "rol")
            or cuenta.findtext("CalidadDeudor")
        ):
            blocked.append("Solo obligaciones como deudor principal")
        elegible = abierta and not blocked
        if abierta:
            cupos_tarjetas += cupo_total
            obligaciones_abiertas.append(
                _build_obligation_row(
                    source="TarjetaCredito",
                    cuenta=cuenta,
                    tipo_cuenta="TDC",
                    sector_code=sector_code,
                    saldo_actual=saldo_actual,
                    valor_cuota=valor_cuota,
                    valor_inicial=cupo_total,
                    condicion=condicion,
                    elegible=elegible,
                    blocked=blocked,
                )
            )

    valor_pasivos_miles = 0.0
    resumen = informe.find("InfoAgregadaMicrocredito/Resumen")
    endeudamiento = resumen.find("EndeudamientoActual") if resumen is not None else None
    if endeudamiento is not None:
        for sector in endeudamiento.findall("Sector"):
            for tipo_cuenta in sector.findall("TipoCuenta"):
                for cuenta in tipo_cuenta.findall(".//Cuenta"):
                    valor_pasivos_miles += _parse_number(_attr(cuenta, "saldoActual"))

    valor_pasivos = _to_pesos(valor_pasivos_miles, pesos_multiplier, aggregate=True)
    valor_pasivos_recoge = 0.0
    valor_cuota_recoge = 0.0
    applied = []
    if selected_set:
        index = {row["key"]: row for row in obligaciones_abiertas}
        for key in selected_set:
            row = index.get(key)
            if not row or not row.get("elegible_recoge"):
                continue
            valor_pasivos_recoge += float(row.get("saldo_actual", 0) or 0)
            valor_cuota_recoge += float(row.get("valor_cuota", 0) or 0)
            applied.append(key)

    metrics = {
        "valor_pasivos": _to_int(valor_pasivos),
        "valor_pasivos_que_recoge": _to_int(valor_pasivos_recoge),
        "saldo_total_creditos": _to_int(saldo_total_creditos_principal + saldo_abierto_codeudor),
        "saldo_total_creditos_deudor_principal": _to_int(saldo_total_creditos_principal),
        "saldo_abierto_codeudor": _to_int(saldo_abierto_codeudor),
        "cupos_tarjetas_rotativos": _to_int(cupos_rotativos + cupos_tarjetas),
        "total_cuotas_credito": _to_int(total_cuotas_credito_principal + cuota_abierta_codeudor),
        "total_cuotas_credito_deudor_principal": _to_int(total_cuotas_credito_principal),
        "cuota_abierta_codeudor": _to_int(cuota_abierta_codeudor),
        "cuotas_creditos_codeudor": _to_int(cuota_abierta_codeudor),
        "valor_cuota_que_recoge_pago_personal": _to_int(valor_cuota_recoge),
    }
    return {
        "metrics": metrics,
        "metrics_formatted": {key: _fmt(value) for key, value in metrics.items()},
        "obligaciones_abiertas": sorted(
            obligaciones_abiertas,
            key=lambda row: (not row["elegible_recoge"], row["entidad"], row["numero_cuenta"]),
        ),
        "selected_keys_applied": applied,
        "config": {
            "pesos_multiplier": _to_int(pesos_multiplier),
            "rotative_types": sorted(rotative_types),
            "exclude_telco": exclude_telco,
            "exclude_tdc_in_recoge": exclude_tdc_recoge,
        },
    }
