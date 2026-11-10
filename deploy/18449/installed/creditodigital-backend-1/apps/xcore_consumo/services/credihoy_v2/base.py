"""Primitivas V2 independientes de Django, proveedores y PROTOTIPO_V1."""

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from types import UnionType
from typing import Generic, TypeVar, Union, get_args, get_origin, get_type_hints


T = TypeVar("T")


class EstadoCertificacion(str, Enum):
    CERTIFICADO = "CERTIFICADO"
    OBSERVADO = "OBSERVADO"
    SEMANTICA_PENDIENTE = "SEMANTICA_PENDIENTE"
    NO_CERTIFICADO = "NO_CERTIFICADO"
    NO_DISPONIBLE = "NO_DISPONIBLE"
    AMBIGUO = "AMBIGUO"


def texto(valor, nombre):
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"{nombre} requiere texto no vacio")


def decimal_finito(valor, nombre):
    if not isinstance(valor, Decimal) or not valor.is_finite():
        raise ValueError(f"{nombre} requiere Decimal finito")


def instante(valor, nombre):
    if not isinstance(valor, datetime) or valor.tzinfo is None or valor.utcoffset() is None:
        raise ValueError(f"{nombre} requiere datetime con zona")


def _inmutable(valor):
    if valor is None or isinstance(valor, (str, bool, int, date, Enum)):
        return
    if isinstance(valor, Decimal):
        decimal_finito(valor, "valor")
        return
    if isinstance(valor, tuple):
        for item in valor:
            _inmutable(item)
        return
    if is_dataclass(valor) and valor.__dataclass_params__.frozen:
        for campo in fields(valor):
            _inmutable(getattr(valor, campo.name))
        return
    raise TypeError("DTO V2 solo admite valores inmutables; no float/list/dict")


def serializar(valor):
    if isinstance(valor, Enum):
        return valor.value
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if is_dataclass(valor):
        return {f.name: serializar(getattr(valor, f.name)) for f in fields(valor)}
    if isinstance(valor, tuple):
        return [serializar(v) for v in valor]
    return valor


@lru_cache(maxsize=None)
def _tipos(clase):
    return get_type_hints(clase)


def _coincide(valor, esperado):
    if isinstance(esperado, TypeVar):
        return True
    origen, argumentos = get_origin(esperado), get_args(esperado)
    if origen in (UnionType, Union):
        return any(_coincide(valor, tipo) for tipo in argumentos)
    if origen is tuple:
        if not isinstance(valor, tuple):
            return False
        if len(argumentos) == 2 and argumentos[1] is Ellipsis:
            return all(_coincide(v, argumentos[0]) for v in valor)
        return len(valor) == len(argumentos) and all(_coincide(v, t) for v, t in zip(valor, argumentos))
    if origen is not None:
        return (isinstance(valor, origen) and (valor.valor is None or _coincide(valor.valor, argumentos[0])))
    if esperado in (int, bool):
        return type(valor) is esperado
    return isinstance(valor, esperado)


@dataclass(frozen=True, kw_only=True)
class DTO:
    def __post_init__(self):
        _inmutable(self)
        for nombre, tipo in _tipos(type(self)).items():
            if not _coincide(getattr(self, nombre), tipo):
                raise TypeError(f"{nombre}: tipo incompatible con contrato V2")

    def a_dict(self):
        return serializar(self)


@dataclass(frozen=True, kw_only=True)
class Hecho(DTO, Generic[T]):
    valor: T | None = None
    disponible: bool = False
    fuente: str | None
    referencia_evidencia: str | None = None
    ruta_origen: str | None = None
    fecha_corte: date | datetime | None = None
    fecha_consulta: datetime | None = None
    estado_certificacion: EstadoCertificacion = EstadoCertificacion.NO_DISPONIBLE
    observacion: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.fuente is not None:
            texto(self.fuente, "fuente")
        if type(self.disponible) is not bool:
            raise TypeError("disponible requiere bool")
        if self.disponible != (self.valor is not None):
            raise ValueError("disponibilidad y valor inconsistentes")
        if not isinstance(self.estado_certificacion, EstadoCertificacion):
            raise TypeError("estado_certificacion invalido")
        if self.estado_certificacion in {
                EstadoCertificacion.NO_DISPONIBLE, EstadoCertificacion.NO_CERTIFICADO,
                EstadoCertificacion.AMBIGUO} and self.disponible:
            raise ValueError("Estado sin valor individual disponible")
        if self.estado_certificacion == EstadoCertificacion.CERTIFICADO:
            if not self.disponible or not self.referencia_evidencia:
                raise ValueError("CERTIFICADO requiere valor y evidencia")
        if self.fecha_consulta is not None:
            instante(self.fecha_consulta, "fecha_consulta")
        if self.fecha_corte is not None:
            if not isinstance(self.fecha_corte, date):
                raise TypeError("fecha_corte requiere date/datetime")
            if isinstance(self.fecha_corte, datetime):
                instante(self.fecha_corte, "fecha_corte")


def pendiente(fuente="LINIX_API_DATA_V2"):
    return Hecho(fuente=fuente, estado_certificacion=EstadoCertificacion.NO_CERTIFICADO)
