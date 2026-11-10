"""Hechos normalizados. No resuelve fuentes ni completa valores ausentes."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar


T = TypeVar("T")


class Certificacion(str, Enum):
    CERTIFICADO = "CERTIFICADO"
    NO_CERTIFICADO = "NO_CERTIFICADO"


@dataclass(frozen=True, kw_only=True)
class Hecho(Generic[T]):
    valor: T | None = None
    disponible: bool = False
    fuente: str = "NO_INFORMADA"
    fecha_corte: date | None = None
    periodo: str | None = None
    estado: Certificacion = Certificacion.NO_CERTIFICADO

    def __post_init__(self):
        if type(self.disponible) is not bool:
            raise TypeError("disponible debe ser booleano")
        if not self.disponible and self.valor is not None:
            raise ValueError("Un hecho no disponible debe tener valor=None")


@dataclass(frozen=True, kw_only=True)
class ObligacionInterna:
    """C2: q_clasif pertenece al periodo anterior informado en el Hecho."""

    a_tipodr: str | None = None
    a_obliga: str | None = None
    q_clasif: str | None = None
    q_diamora: int | None = None
    q_diaven: int | None = None
    v_salmor: Decimal | None = None
    v_salkap: Decimal | None = None


@dataclass(frozen=True, kw_only=True)
class DatosDatacredito:
    disponible: bool = False
    obligaciones: Hecho[tuple[str, ...]] = field(default_factory=Hecho)
    mora: Hecho[int] = field(default_factory=Hecho)
    score: Hecho[Decimal] = field(default_factory=Hecho)
    categoria: Hecho[str] = field(default_factory=Hecho)
    embargo: Hecho[bool] = field(default_factory=Hecho)
    experiencia: Hecho[int] = field(default_factory=Hecho)
    decision_preselecta: Hecho[str] = field(default_factory=Hecho)
    riesgo_preselecta: Hecho[str] = field(default_factory=Hecho)
    score_preselecta: Hecho[Decimal] = field(default_factory=Hecho)


@dataclass(frozen=True, kw_only=True)
class DatosInternos:
    estado_asociado: Hecho[str] = field(default_factory=Hecho)
    periodo_anterior: Hecho[str] = field(default_factory=Hecho)
    obligaciones_internas: Hecho[tuple[ObligacionInterna, ...]] = field(default_factory=Hecho)
    aporte_social_203: Hecho[Decimal] = field(default_factory=Hecho)
    cuota_vigente_interna: Hecho[Decimal] = field(default_factory=Hecho)


@dataclass(frozen=True, kw_only=True)
class ContextoEvaluacionCrediHoy:
    fecha_evaluacion: date
    solicitud: str | None = None
    solicitante: str | None = None
    ingresos: Hecho[Decimal] = field(default_factory=Hecho)
    producto: str | None = None
    monto: Decimal | None = None
    plazo: int | None = None
    garantia: str | None = None
    forma_pago: str | None = None
    smmlv: Hecho[Decimal] = field(default_factory=Hecho)
    datacredito: DatosDatacredito = field(default_factory=DatosDatacredito)
    interno: DatosInternos = field(default_factory=DatosInternos)
    solicita_excepcion_mora: bool = False

    def __post_init__(self):
        if type(self.fecha_evaluacion) is not date:
            raise TypeError("fecha_evaluacion debe ser una fecha explicita")
        if type(self.solicita_excepcion_mora) is not bool:
            raise TypeError("solicita_excepcion_mora debe ser booleano")
