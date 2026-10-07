"""DTOs inmutables validados contra el contrato OpenAPI de PRESELECTA 78fff713, sin catalogos.

NodoContrato conserva cada campo del wire sin replicar treinta clases del servidor.
El validador cubre solo los keywords presentes en este contrato versionado.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
from pathlib import Path
import re
from uuid import UUID

from .errores import GatewayInvalidResponse, GatewayValidationError


@lru_cache(maxsize=1)
def _schemas():
    with Path(__file__).with_name("contrato_openapi.json").open(encoding="utf-8") as archivo:
        return json.load(archivo)["schemas"]


def _validar(valor, schema):
    if "$ref" in schema:
        return _validar(valor, _schemas()[schema["$ref"].rsplit("/", 1)[1]])
    if valor is None and schema.get("nullable"):
        if "enum" in schema and None not in schema["enum"]:
            raise ValueError
        return
    for sub in schema.get("allOf", ()):
        _validar(valor, sub)
    if "oneOf" in schema:
        aciertos = 0
        for sub in schema["oneOf"]:
            try:
                _validar(valor, sub)
                aciertos += 1
            except ValueError:
                pass
        if aciertos != 1:
            raise ValueError
    if "not" in schema:
        try:
            _validar(valor, schema["not"])
        except ValueError:
            pass
        else:
            raise ValueError
    if "enum" in schema and not any(type(valor) is type(v) and valor == v for v in schema["enum"]):
        raise ValueError
    tipo = schema.get("type")
    if tipo and type(valor) is not {"object": dict, "array": list, "string": str,
                                  "integer": int, "boolean": bool}[tipo]:
        raise ValueError
    if isinstance(valor, dict):
        props = schema.get("properties", {})
        if any(k not in valor for k in schema.get("required", ())):
            raise ValueError
        additional = schema.get("additionalProperties", True)
        for k, v in valor.items():
            if not isinstance(k, str) or (k not in props and additional is False):
                raise ValueError
            if k in props:
                _validar(v, props[k])
            elif isinstance(additional, dict):
                _validar(v, additional)
    elif isinstance(valor, list):
        if len(valor) < schema.get("minItems", 0) or ("maxItems" in schema and len(valor) > schema["maxItems"]):
            raise ValueError
        for v in valor:
            _validar(v, schema.get("items", {}))
    elif isinstance(valor, str):
        if len(valor) < schema.get("minLength", 0) or ("maxLength" in schema and len(valor) > schema["maxLength"]):
            raise ValueError
        if "pattern" in schema and not re.search(schema["pattern"], valor):
            raise ValueError
        if schema.get("format") == "uuid":
            if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", valor):
                raise ValueError
            UUID(valor)
        if schema.get("format") == "date-time":
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
                                r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})", valor):
                raise ValueError
            fecha = datetime.fromisoformat(valor.replace("z", "+00:00").replace("Z", "+00:00"))
            if fecha.utcoffset() is None:
                raise ValueError
    if "minimum" in schema and valor < schema["minimum"]:
        raise ValueError


def _decimal(valor):
    if valor is None:
        return None
    if not isinstance(valor, str) or not re.fullmatch(
            r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", valor):
        raise ValueError
    resultado = Decimal(valor)
    if not resultado.is_finite():
        raise ValueError
    return resultado


def _wire(valor):
    if isinstance(valor, NodoContrato):
        return valor.a_dict()
    if isinstance(valor, tuple):
        return [_wire(v) for v in valor]
    if isinstance(valor, Decimal):
        return str(valor)
    return valor


def _inmutable(valor):
    if isinstance(valor, NodoContrato):
        return
    if isinstance(valor, tuple):
        for v in valor:
            _inmutable(v)
    elif valor is not None and type(valor) not in (str, int, bool, Decimal):
        raise ValueError
    elif isinstance(valor, Decimal) and not valor.is_finite():
        raise ValueError


@dataclass(frozen=True, slots=True, repr=False)
class NodoContrato:
    campos: tuple[tuple[str, object], ...]

    def __post_init__(self):
        if type(self.campos) is not tuple:
            raise ValueError("Contrato requiere estructura inmutable")
        nombres = set()
        for par in self.campos:
            if type(par) is not tuple or len(par) != 2 or type(par[0]) is not str or par[0] in nombres:
                raise ValueError("Campo contractual invalido")
            nombres.add(par[0])
            _inmutable(par[1])

    def __repr__(self):
        return f"{type(self).__name__}(campos=<{len(self.campos)} campos>)"

    def __getitem__(self, nombre):
        for k, v in self.campos:
            if k == nombre:
                return v
        raise KeyError(nombre)

    def get(self, nombre, default=None):
        try:
            return self[nombre]
        except KeyError:
            return default

    def a_dict(self):
        return {k: _wire(v) for k, v in self.campos}


def _congelar(valor, schema):
    nombre = None
    if "$ref" in schema:
        nombre = schema["$ref"].rsplit("/", 1)[1]
        schema = _schemas()[nombre]
    if valor is None:
        return None
    if schema.get("allOf"):
        return _congelar(valor, schema["allOf"][0])
    if schema.get("oneOf"):
        for sub in schema["oneOf"]:
            try:
                _validar(valor, sub)
            except ValueError:
                continue
            return _congelar(valor, sub)
    if isinstance(valor, dict):
        if nombre == "EstadoCompuestoHDC":
            for par in valor["entradas_originales"]:
                if type(par[0]) is not str:
                    raise ValueError
        props = schema.get("properties", {})
        adicional = schema.get("additionalProperties", {})
        campos = []
        for k, v in valor.items():
            if (nombre, k) in {("ImporteHDC", "valor_monetario"),
                              ("CampoProveedorPreselecta", "valor_homologado")} or (
                    nombre in {"EndeudamientoActualHDC", "DesgloseEndeudamientoHDC"}
                    and k.endswith("_pesos")):
                convertido = _decimal(v)
            else:
                convertido = _congelar(v, props.get(k, adicional if isinstance(adicional, dict) else {}))
            campos.append((k, convertido))
        return NodoContrato(tuple(campos))
    if isinstance(valor, list):
        return tuple(_congelar(v, schema.get("items", {})) for v in valor)
    return valor


class _Contrato(NodoContrato):
    __slots__ = ()
    schema_name = ""
    error_class = GatewayInvalidResponse

    def __post_init__(self):
        super().__post_init__()
        try:
            _validar(self.a_dict(), _schemas()[self.schema_name])
            esperado = _congelar(self.a_dict(), {"$ref": f"#/components/schemas/{self.schema_name}"})
            if esperado.campos != self.campos:
                raise ValueError
            if self.schema_name in {"PreselectaResponse", "HDCResponse"}:
                r = self["resultado"]
                if (r["request_id"] != self["requestId"]
                        or r["estado_tecnico"] not in {"COMPLETADO", "SIN_INFORMACION"}
                        or r["trazabilidad"]["version_contrato"] != self["versionContrato"]):
                    raise ValueError
                if self.schema_name == "PreselectaResponse" and r["resultado_proveedor"]["naturaleza"] != "RESULTADO_PROVEEDOR":
                    raise ValueError
        except (ValueError, TypeError, InvalidOperation, RecursionError):
            raise self.error_class() from None

    @classmethod
    def desde_payload(cls, payload):
        try:
            _validar(payload, _schemas()[cls.schema_name])
            nodo = _congelar(payload, {"$ref": f"#/components/schemas/{cls.schema_name}"})
            return cls(nodo.campos)
        except (ValueError, TypeError, InvalidOperation, RecursionError):
            raise cls.error_class() from None


@dataclass(frozen=True, slots=True, repr=False)
class PreselectaRequest(_Contrato):
    schema_name = "PreselectaRequest"
    error_class = GatewayValidationError


@dataclass(frozen=True, slots=True, repr=False)
class HDCRequest(_Contrato):
    schema_name = "HDCRequest"
    error_class = GatewayValidationError


@dataclass(frozen=True, slots=True, repr=False)
class PreselectaNormalizado(_Contrato):
    schema_name = "PreselectaResponse"


@dataclass(frozen=True, slots=True, repr=False)
class HDCNormalizado(_Contrato):
    schema_name = "HDCResponse"
