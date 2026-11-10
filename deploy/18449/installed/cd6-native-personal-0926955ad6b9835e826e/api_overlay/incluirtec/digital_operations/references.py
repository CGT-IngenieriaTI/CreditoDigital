"""Reference capture contract and shared XML preparation; never sends to Oracle."""

import base64
import gzip
import hashlib
import json
from importlib.resources import files
import re

from incluirtec.services.xml_builder import build_credihoy_references_xml


FIELDS = (
    "tipoReferencia", "nombre", "direccion", "ciudad", "ciudadDescripcion",
    "codigoCiudad", "codigoDepartamento", "codigoPais", "telefono", "email",
    "observacion", "parentesco", "tipoReferenciaDescripcion",
)


class ReferenceError(ValueError):
    pass


def reference_catalog():
    envelope = json.loads(files(__package__).joinpath("reference_catalogs.json").read_text(encoding="utf-8"))
    raw = gzip.decompress(base64.b64decode(envelope["datos"], validate=True))
    if len(raw) != envelope["bytesSinComprimir"] or hashlib.sha256(raw).hexdigest() != envelope["sha256"]:
        raise ReferenceError("CATALOGO_INCONSISTENTE")
    return json.loads(raw), envelope["sha256"]


def normalize_references(values):
    catalog, _ = reference_catalog()
    types = {row["codigo"]: row["descripcion"] for row in catalog["tipos"]}
    relatives = {row["codigo"] for row in catalog["parentescos"]}
    cities = {(country, department, code): name for country, department, code, name in catalog["ciudades"]}
    if not isinstance(values, list):
        raise ReferenceError("REFERENCIAS_INVALIDAS")
    result = []
    for value in values:
        if not isinstance(value, dict) or set(value) - set(FIELDS):
            raise ReferenceError("REFERENCIA_INVALIDA")
        row = {field: value.get(field) if value.get(field) is not None else "" for field in FIELDS}
        for field, text in row.items():
            if not isinstance(text, str) or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in text):
                raise ReferenceError("REFERENCIA_INVALIDA")
            if field in catalog["limites"] and len(text) > catalog["limites"][field]:
                raise ReferenceError("REFERENCIA_LONGITUD_INVALIDA")
        if (row["tipoReferencia"] not in types or not row["nombre"].strip()
                or row["parentesco"] and row["parentesco"] not in relatives):
            raise ReferenceError("REFERENCIA_CATALOGO_INVALIDO")
        location = (row["codigoPais"], row["codigoDepartamento"], row["codigoCiudad"])
        if location not in cities:
            raise ReferenceError("REFERENCIA_UBICACION_INVALIDA")
        if not row["telefono"] and not row["email"]:
            raise ReferenceError("REFERENCIA_CONTACTO_REQUERIDO")
        if row["telefono"] and (not re.fullmatch(r"\+?[0-9 ()-]{7,20}", row["telefono"])
                                or not re.search(r"[0-9]", row["telefono"])):
            raise ReferenceError("REFERENCIA_TELEFONO_INVALIDO")
        if row["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", row["email"]):
            raise ReferenceError("REFERENCIA_EMAIL_INVALIDO")
        for field, expected in (("tipoReferenciaDescripcion", types[row["tipoReferencia"]]),
                                ("ciudad", cities[location]), ("ciudadDescripcion", cities[location])):
            if row[field] and row[field] != expected:
                raise ReferenceError("REFERENCIA_DESCRIPCION_INCONSISTENTE")
            row[field] = expected
        result.append(row)
    return result


def prepare_references(values):
    records = normalize_references(values)
    _, fingerprint = reference_catalog()
    return {
        "versionContrato": "credihoy-referencias-preparadas-v1",
        "referencias": records, "xmlReferencias": build_credihoy_references_xml(records),
        "catalogoSha256": fingerprint, "envioOracleHabilitado": False,
        "estadoContrato3883": "PENDIENTE_VALIDACION",
    }


def prepare_radication_references(payload, values, *, allow_without_promoter_offline=False):
    """A NEW expediente draft only. No intent, grant, gateway or Oracle call."""
    from .credihoy import build_radication_xml
    result = prepare_references(values)
    result["xmlRadicacion"] = build_radication_xml(payload,
        allow_without_promoter_offline=allow_without_promoter_offline,
        references_for_preparation=result["referencias"])
    result["enviable"] = False
    return result
