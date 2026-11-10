"""Importacion local explicita: proyeccion permitida, sin red ni decisiones."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
from types import SimpleNamespace

from django.conf import settings
from django.db import connection, transaction

from apps.usuarios.models import Solicitante
from apps.solicitudes.models import Solicitud
from apps.preselecta.models import PreselectaConsulta
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from ..credihoy import DatosDatacredito
from ..credihoy_integracion.externos import incorporar_preselecta_credihoy, normalizar_hdc_credihoy
from .evidencia import identidad_valida, fecha_evidencia

VERSION_IMPORTACION = "credihoy-shadow-import-v1"
HDC_CAMPOS = ("score_pago", "mora_maxima", "categoria", "embargo", "experiencia", "obligaciones")


def exigir_local():
    db = connection.settings_dict
    nombre = str(db.get("NAME", ""))
    memoria = nombre == ":memory:" or (nombre.startswith("file:memorydb_") and "mode=memory" in nombre)
    if (not settings.DEBUG or db.get("ENGINE") != "django.db.backends.sqlite3" or db.get("HOST")
            or not nombre or (not memoria and (nombre.startswith(("\\\\", "//", "file:"))
            or str(Path(nombre).resolve()).startswith(("\\\\", "//"))))):
        raise ValueError("REQUIERE_DEBUG_Y_SQLITE_LOCAL")


def _pares(pares):
    resultado = {}
    for k, v in pares:
        if k in resultado:
            raise ValueError("JSON_CLAVE_DUPLICADA")
        resultado[k] = v
    return resultado


def _leer(ruta):
    try:
        p = Path(ruta)
        if str(p).startswith(("\\\\", "//")):
            raise ValueError("RUTA_NO_LOCAL")
        p = p.resolve(strict=True)
        raiz = Path(settings.BASE_DIR).resolve().parent
        if p.is_relative_to(raiz) or any((d / ".git").exists() for d in p.parents):
            raise ValueError("EVIDENCIA_DENTRO_DE_REPOSITORIO")
        if not p.is_file() or p.stat().st_size > 2_000_000:
            raise ValueError("ARCHIVO_NO_ADMITIDO")
        with p.open(encoding="utf-8-sig") as f:
            valor = json.load(f, parse_float=Decimal, object_pairs_hook=_pares,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("JSON_NO_FINITO")))
        if not isinstance(valor, dict):
            raise ValueError("SCHEMA_INVALIDO")
        return valor
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("ARCHIVO_PRIVADO_NO_LEIBLE_O_JSON_INVALIDO") from None


@dataclass(repr=False)
class Importacion:
    identidad: dict
    pre_request: dict
    pre_raw: dict
    hdc_request: dict
    hdc_raw: dict
    fechas: dict
    resumen: dict
    fecha_expedicion: date | None


def validar_archivos(preselecta, hdc):
    p, h = _leer(preselecta), _leer(hdc)
    try:
        if p["schema"] != "credihoy-shadow-import-preselecta-v1" or h["schema"] != "credihoy-shadow-import-hdc-v1":
            raise ValueError("SCHEMA_NO_SOPORTADO")
        fp, fh = fecha_evidencia(p["fecha_consulta"]), fecha_evidencia(h["fecha_consulta"])
        pr, hr = p["request_payload"], h["historial_payload"]
        identidad = {"tipo_identificacion": {"1": "CC", "2": "CE"}.get(str(pr["idType"]), pr["idType"]),
                     "numero_identificacion": pr["idNumber"], "primer_apellido": pr["firstLastName"]}
        if (identidad["tipo_identificacion"] not in {"CC", "CE", "TI", "PAS"}
                or not isinstance(identidad["numero_identificacion"], str)
                or not re.fullmatch(r"[0-9]{1,30}", identidad["numero_identificacion"])
                or not isinstance(identidad["primer_apellido"], str)
                or not identidad["primer_apellido"].strip() or len(identidad["primer_apellido"]) > 80):
            raise ValueError("IDENTIDAD_INVALIDA")
        persona = SimpleNamespace(**identidad)
        if not identidad_valida(pr, persona) or not identidad_valida(hr, persona):
            raise ValueError("IDENTIDADES_INCONSISTENTES_O_DEMO")
        raw, observaciones = p["response_payload"]["raw"], h["historial_raw"]
        if not isinstance(raw, dict) or not isinstance(observaciones, dict) or set(observaciones) != set(HDC_CAMPOS):
            raise ValueError("SCHEMA_RAW_INVALIDO")
        pre = incorporar_preselecta_credihoy(DatosDatacredito(), raw, fecha_corte=fp.date())
        historial = normalizar_hdc_credihoy(observaciones, fecha_corte=fh.date())
        pre_disponible = {k: getattr(pre, k).disponible for k in (
            "decision_preselecta", "riesgo_preselecta", "score_preselecta")}
        hdc_disponible = {k: getattr(historial, k).disponible for k in (
            "score", "mora", "categoria", "embargo", "experiencia", "obligaciones")}
        if not all(pre_disponible.values()):
            raise ValueError("PRESELECTA_NO_IDENTIFICABLE_O_CONTRADICTORIA")
        for origen, destino in zip(HDC_CAMPOS, ("score", "mora", "categoria", "embargo", "experiencia", "obligaciones")):
            if observaciones[origen] is not None and not hdc_disponible[destino]:
                raise ValueError("HDC_CAMPO_NO_COMPATIBLE")
        if not any(hdc_disponible.values()):
            raise ValueError("HDC_NO_IDENTIFICABLE")
        # Proyeccion de hechos ya validados. No conservar respuesta completa ni datos de seguridad.
        limpio = {"engineResponse": [{"key": k, "value": getattr(pre, campo).valor} for k, campo in (
            ("DECISION", "decision_preselecta"), ("RIESGO_SCORE", "riesgo_preselecta"))],
            "score": str(pre.score_preselecta.valor)}
        seguro_hdc = {k: str(v) if isinstance(v, Decimal) else v for k, v in observaciones.items()}
        expedicion = None
        nacional = raw.get("nationalPerson", {})
        identificacion = nacional.get("identification", {}) if isinstance(nacional, dict) else {}
        if identificacion.get("number"):
            if (identificacion["number"] != identidad["numero_identificacion"] or
                    not identidad_valida({"idNumber": identificacion["number"], "idType": identificacion.get("type") or pr["idType"],
                                          "firstLastName": nacional.get("firstLastName")}, persona)):
                raise ValueError("IDENTIDAD_RESPUESTA_PRESELECTA_INCONSISTENTE")
            if identificacion.get("issueDate"):
                try:
                    expedicion = date.fromisoformat(identificacion["issueDate"])
                except ValueError:
                    pass  # No reinterpretar fechas de expedicion ambiguas; exigir argumento explicito.
        resumen = {"modo": "DRY_RUN", "identidad_coherente": True,
            "PRESELECTA": {"disponibilidad": pre_disponible, "fecha_consulta": p["fecha_consulta"]},
            "HDC": {"disponibilidad": hdc_disponible, "fecha_consulta": h["fecha_consulta"],
                    "cantidad_obligaciones": len(historial.obligaciones.valor) if historial.obligaciones.disponible else None},
            "fecha_expedicion_disponible": expedicion is not None,
            "zona_horaria_declarada": {"PRESELECTA": fp.tzinfo is not None, "HDC": fh.tzinfo is not None},
            "registros_nuevos": ["Solicitud", "SolicitudConsumo", "PreselectaConsulta", "EvaluacionConsumo (solo evidencia)"],
            "solicitante": "reutilizar sin cambios si coincide; crear si no existe y hay fecha_expedicion",
            "DecisionFinal": "NO_SE_CREA", "autorizado_decision_crediticia": False}
        return Importacion(identidad, {k: pr[k] for k in ("idType", "idNumber", "firstLastName")}, limpio,
            dict(identidad), seguro_hdc, {"PRESELECTA": p["fecha_consulta"], "HDC": h["fecha_consulta"]}, resumen, expedicion)
    except (KeyError, TypeError, AttributeError):
        raise ValueError("SCHEMA_INVALIDO") from None


def persistir_local(datos, *, fecha_expedicion=None):
    exigir_local()
    if settings.CREDIHOY_SHADOW_ENABLED:
        raise ValueError("FLAG_GLOBAL_DEBE_SER_FALSE")
    with transaction.atomic():
        persona = Solicitante.objects.filter(tipo_identificacion=datos.identidad["tipo_identificacion"],
            numero_identificacion=datos.identidad["numero_identificacion"]).first()
        expedicion = fecha_expedicion or datos.fecha_expedicion
        if fecha_expedicion and datos.fecha_expedicion and fecha_expedicion != datos.fecha_expedicion:
            raise ValueError("FECHA_EXPEDICION_CONTRADICTORIA")
        if persona:
            if not identidad_valida(datos.hdc_request, persona) or (expedicion and persona.fecha_expedicion != expedicion):
                raise ValueError("SOLICITANTE_EXISTENTE_INCONSISTENTE")
        else:
            if not expedicion:
                raise ValueError("FECHA_EXPEDICION_REAL_REQUERIDA")
            persona = Solicitante.objects.create(**datos.identidad, fecha_expedicion=expedicion, celular="", email="")
        solicitud = Solicitud.objects.create(solicitante=persona, producto="CONSUMO",
            metadatos={"shadow_import": VERSION_IMPORTACION, "tipo_piloto": "PILOTO_TECNICO"})
        SolicitudConsumo.objects.create(solicitud=solicitud, estado="BORRADOR")
        PreselectaConsulta.objects.create(solicitud=solicitud, estado="IMPORTADO", request_payload=datos.pre_request,
            response_payload={"raw": datos.pre_raw, "fecha_consulta": datos.fechas["PRESELECTA"]})
        EvaluacionConsumo.objects.create(solicitud=solicitud, input_snapshot={"form_data": {}},
            integraciones_snapshot={"historial_payload": datos.hdc_request, "historial_raw": datos.hdc_raw,
                "shadow_import": {"version": VERSION_IMPORTACION, "fechas_consulta": datos.fechas}})
        return dict(datos.resumen, modo="PERSISTIDO_LOCAL", solicitud_id=str(solicitud.pk))
