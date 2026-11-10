"""Lectura conservadora de snapshots, sin consultas externas ni defaults legacy."""

from decimal import Decimal, InvalidOperation
from datetime import datetime
import re
import unicodedata

from ..credihoy import ContextoEvaluacionCrediHoy, DatosDatacredito, Hecho
from ..credihoy_integracion.externos import normalizar_hdc_credihoy, incorporar_preselecta_credihoy


INGRESOS = ("asalariados", "pensionados", "prestadores_prof", "independientes",
            "rentistas_capital", "transportadores")


def fecha_evidencia(valor):
    if not isinstance(valor, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?", valor):
        raise ValueError("FECHA_EVIDENCIA_INVALIDA")
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        raise ValueError("FECHA_EVIDENCIA_INVALIDA") from None


def decimal_declarado(valor):
    if valor is None or type(valor) not in (str, int, Decimal):
        return None
    try:
        numero = Decimal(valor)
    except (ValueError, InvalidOperation):
        return None
    return numero if numero.is_finite() else None


def formulario_shadow(solicitud, detail, fecha, *, form_data=None):
    datos = (detail.form_data if form_data is None else form_data) or {}
    ingresos = [decimal_declarado(datos.get(k)) for k in INGRESOS]
    total = sum(ingresos, Decimal("0")) if all(v is not None for v in ingresos) else None
    smmlv = decimal_declarado(datos.get("smmlv"))
    plazo = decimal_declarado(datos.get("plazo"))
    return ContextoEvaluacionCrediHoy(
        fecha_evaluacion=fecha, solicitud=str(solicitud.pk), solicitante=str(solicitud.solicitante_id),
        producto=str(solicitud.producto).lower(), monto=decimal_declarado(datos.get("monto_solicitado")),
        plazo=int(plazo) if plazo is not None and plazo == plazo.to_integral_value() else None,
        garantia=datos.get("garantia") if datos.get("garantia") in ("Firma Personal", "Fianza") else None,
        forma_pago=datos.get("forma_pago") if datos.get("forma_pago") in (
            "Libranza", "D\u00e9bito autom\u00e1tico", "Ventanilla") else None,
        ingresos=Hecho(valor=total, disponible=total is not None, fuente="FORMULARIO", fecha_corte=fecha),
        smmlv=Hecho(valor=smmlv, disponible=smmlv is not None, fuente="FORMULARIO", fecha_corte=fecha),
    )


def _nombre(valor):
    return unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode().strip().upper()


def identidad_valida(payload, solicitante):
    if not isinstance(payload, dict) or payload.get("provider_test_identity") or payload.get("provider_mode") == "test":
        return False
    documento = payload.get("numero_identificacion", payload.get("idNumber"))
    apellido = payload.get("primer_apellido", payload.get("firstLastName"))
    tipo = payload.get("tipo_identificacion", payload.get("idType"))
    tipo = {"1": "CC", "2": "CE"}.get(str(tipo), tipo)
    return (documento == solicitante.numero_identificacion and tipo == solicitante.tipo_identificacion
            and _nombre(apellido) == _nombre(solicitante.primer_apellido))


def recuperar_externos(solicitud, evaluacion, *, preselecta=None, historial=None):
    """Los scores/ceros de columnas y response_payload legacy nunca son evidencia."""
    snapshot = (evaluacion.integraciones_snapshot or {}) if evaluacion else {}
    corte = evaluacion.updated_at.date() if evaluacion else solicitud.updated_at.date()
    importacion = snapshot.get("shadow_import")
    cortes_importados = None
    if importacion is not None:
        # Una evidencia importada sin fecha explicita nunca hereda updated_at.
        if not isinstance(importacion, dict) or importacion.get("version") != "credihoy-shadow-import-v1":
            raise ValueError("IMPORTACION_INVALIDA")
        cortes_importados = {k: fecha_evidencia(importacion.get("fechas_consulta", {}).get(k)).date()
                             for k in ("PRESELECTA", "HDC")}
    corte_hdc = cortes_importados["HDC"] if cortes_importados else corte
    corte_pre = cortes_importados["PRESELECTA"] if cortes_importados else corte
    datos, errores = DatosDatacredito(), []
    raw_hdc = snapshot.get("historial_raw", {})
    if (identidad_valida(snapshot.get("historial_payload"), solicitud.solicitante)
            and isinstance(raw_hdc, dict) and not any(k in raw_hdc for k in (
                "source", "metrics", "manual_fields", "metrics_formatted"))):
        try:
            datos = normalizar_hdc_credihoy(raw_hdc, fecha_corte=corte_hdc)
        except Exception:
            errores.append("SHADOW_HDC_NORMALIZACION")
    elif historial and identidad_valida(historial.request_payload, solicitud.solicitante):
        # Solo existe XML/normalizado antiguo: no reconstruir hechos no extraidos.
        try:
            datos = normalizar_hdc_credihoy({}, fecha_corte=corte_hdc if cortes_importados else historial.updated_at.date())
        except Exception:
            errores.append("SHADOW_HDC_NORMALIZACION")

    previo = snapshot.get("preselecta_snapshot", {})
    raw_preselecta = None
    if isinstance(previo, dict) and identidad_valida(previo.get("request_payload"), solicitud.solicitante):
        # engine_response conserva campos del proveedor; score superior es legacy.
        raw_preselecta = {"engineResponse": previo.get("engine_response", []),
                          "estado": previo.get("estado"), "Fault": previo.get("fault")}
    elif preselecta and identidad_valida(preselecta.request_payload, solicitud.solicitante):
        raw = (preselecta.response_payload or {}).get("raw")
        if isinstance(raw, dict):
            raw_preselecta = raw
            if not cortes_importados:
                corte_pre = preselecta.updated_at.date()
    if raw_preselecta is not None:
        try:
            datos = incorporar_preselecta_credihoy(datos, raw_preselecta, fecha_corte=corte_pre)
        except Exception:
            errores.append("SHADOW_PRESELECTA_NORMALIZACION")
    return datos, tuple(errores)


def disponibilidad_externa(datos):
    return {
        "PRESELECTA": any(getattr(datos, k).disponible for k in (
            "decision_preselecta", "riesgo_preselecta", "score_preselecta")),
        "HDC": any(getattr(datos, k).disponible for k in (
            "score", "mora", "categoria", "embargo", "experiencia", "obligaciones")),
    }
