"""Normalizacion estricta opt-in; no usa defaults ni los serializers legacy."""

from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation

from ..credihoy.contexto import DatosDatacredito, Hecho


def _decimal(valor):
    if valor is None or isinstance(valor, bool) or not isinstance(valor, (str, int, Decimal)):
        return None
    try:
        numero = Decimal(valor)
    except InvalidOperation:
        return None
    return numero if numero.is_finite() else None


def _entero(valor):
    numero = _decimal(valor)
    return int(numero) if numero is not None and numero >= 0 and numero == numero.to_integral_value() else None


def _texto(valor):
    return valor.strip() if isinstance(valor, str) and valor.strip() else None


def _hecho(valor, fuente, corte):
    return Hecho(valor=valor, disponible=valor is not None, fuente=fuente, fecha_corte=corte)


def normalizar_hdc_credihoy(observaciones: dict, *, fecha_corte: date) -> DatosDatacredito:
    """Solo observaciones directas previas al normalizador legacy, nunca XML crudo.

    No extrae campos del XML ni supone que un XML recibido contiene esos hechos.
    Las referencias de obligaciones deben haber sido normalizadas sin defaults.
    """
    if type(fecha_corte) is not date or not isinstance(observaciones, dict):
        raise ValueError("HDC requiere observaciones directas y fecha de corte")
    if any(k in observaciones for k in ("metrics", "manual_fields", "metrics_formatted", "source")):
        raise ValueError("No se admiten respuestas HDC del normalizador legacy")
    fuente = "DATACREDITO_HDC"
    fallo = observaciones.get("estado") == "ERROR" or bool(observaciones.get("Fault"))
    datos = {} if fallo else observaciones
    referencias = datos.get("obligaciones")
    obligaciones = None
    if isinstance(referencias, (list, tuple)) and all(_texto(x) is not None for x in referencias):
        obligaciones = tuple(referencias)
    valores = {
        "obligaciones": obligaciones, "mora": _entero(datos.get("mora_maxima")),
        "score": _decimal(datos.get("score_pago")), "categoria": _texto(datos.get("categoria")),
        "embargo": datos.get("embargo") if type(datos.get("embargo")) is bool else None,
        "experiencia": _entero(datos.get("experiencia")),
    }
    hechos = {k: _hecho(v, fuente, fecha_corte) for k, v in valores.items()}
    return DatosDatacredito(disponible=not fallo and any(h.disponible for h in hechos.values()), **hechos)


def incorporar_preselecta_credihoy(hdc: DatosDatacredito, respuesta: dict, *, fecha_corte: date):
    """Preserva decision/riesgo/score PRESELECTA; nunca los usa como elegibilidad."""
    if not isinstance(respuesta, dict) or type(fecha_corte) is not date:
        raise ValueError("PRESELECTA requiere respuesta de proveedor y fecha de corte")
    if "preaprobado" in respuesta or "identity_effective" in respuesta:
        raise ValueError("Usar la respuesta de proveedor anterior a normalizacion legacy")
    fallo = bool(respuesta.get("Fault")) or respuesta.get("estado") == "ERROR" or respuesta.get("typeResponse") == "ERROR"
    datos = {} if fallo else respuesta
    engine = datos.get("engineResponse", [])

    def valor_engine(clave):
        if not isinstance(engine, list):
            return None
        valores = [_texto(item.get("value")) for item in engine
                   if isinstance(item, dict) and item.get("key") == clave]
        valores = [v for v in valores if v is not None]
        return valores[0] if valores and len(set(valores)) == 1 else None

    score = datos.get("score")
    candidatos = [score.get("rating") if isinstance(score, dict) else score, valor_engine("SCORE")]
    for clave in ("resultado", "data"):
        if isinstance(datos.get(clave), dict):
            candidatos.append(datos[clave].get("score"))
    scores = [_decimal(c) for c in candidatos if c is not None and c != ""]
    score = scores[0] if scores and all(s is not None and s == scores[0] for s in scores) else None
    fuente = "DATACREDITO_PRESELECTA"
    return replace(hdc,
                   decision_preselecta=_hecho(valor_engine("DECISION"), fuente, fecha_corte),
                   riesgo_preselecta=_hecho(valor_engine("RIESGO_SCORE"), fuente, fecha_corte),
                   score_preselecta=_hecho(score, fuente, fecha_corte))
