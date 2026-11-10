"""Parametros, derivados y resultados declarativos; sin evaluadores SC."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Generic

from .base import DTO, Hecho, T, texto


class EstadoDefinicion(str, Enum):
    DOCUMENTADO = "DOCUMENTADO"
    OBSERVADA_EN_CURUBA = "OBSERVADA_EN_CURUBA"
    PENDIENTE_CERTIFICACION = "PENDIENTE_CERTIFICACION"


@dataclass(frozen=True, kw_only=True)
class ParametroV2(DTO):
    nombre: str
    version: str
    hecho: Hecho[Decimal | int | str]
    estado: EstadoDefinicion

    def __post_init__(self):
        super().__post_init__()
        texto(self.nombre, "nombre")
        texto(self.version, "version")
        if not isinstance(self.estado, EstadoDefinicion):
            raise TypeError("estado de definicion invalido")


@dataclass(frozen=True, kw_only=True)
class FormulaV2(DTO):
    formula_id: str
    version: str
    expresion_documental: str
    estado: EstadoDefinicion
    autorizada_calculo: bool = field(default=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        for nombre in ("formula_id", "version", "expresion_documental"):
            texto(getattr(self, nombre), nombre)
        if not isinstance(self.estado, EstadoDefinicion):
            raise TypeError("estado de formula invalido")


@dataclass(frozen=True, kw_only=True)
class ParametrosCrediHoyV2(DTO):
    version: str
    valores: tuple[ParametroV2, ...] = ()
    formulas: tuple[FormulaV2, ...] = ()
    vigencia_desde: date | None = None
    vigencia_hasta: date | None = None

    def __post_init__(self):
        super().__post_init__()
        texto(self.version, "version")
        for items, atributo in ((self.valores, "nombre"), (self.formulas, "formula_id")):
            nombres = [getattr(item, atributo) for item in items]
            if len(nombres) != len(set(nombres)):
                raise ValueError("Identificadores duplicados")
        if self.vigencia_desde and self.vigencia_hasta and self.vigencia_hasta < self.vigencia_desde:
            raise ValueError("Vigencia invertida")


@dataclass(frozen=True, kw_only=True)
class DerivadoCrediHoyV2(DTO, Generic[T]):
    fecha_evaluacion: date
    valor: T | None = None
    calculable: bool = False
    operandos_utilizados: tuple[str, ...] = ()
    formula_id: str | None = None
    formula_version: str | None = None
    motivo_no_calculable: str | None = "FORMULA_Y_OPERANDOS_PENDIENTES_CERTIFICACION"

    def __post_init__(self):
        super().__post_init__()
        if type(self.fecha_evaluacion) is not date or type(self.calculable) is not bool:
            raise TypeError("Fecha o calculable invalidos")
        if not self.calculable:
            if self.valor is not None:
                raise ValueError("No calculable requiere valor=None")
            texto(self.motivo_no_calculable, "motivo_no_calculable")
        else:
            if self.valor is None or self.motivo_no_calculable is not None or not self.operandos_utilizados:
                raise ValueError("Derivado calculable requiere valor y trazabilidad sin motivo de ausencia")
            texto(self.formula_id, "formula_id")
            texto(self.formula_version, "formula_version")
        if (self.formula_id is None) != (self.formula_version is None):
            raise ValueError("Formula y version deben declararse juntas")
        for operando in self.operandos_utilizados:
            texto(operando, "operando")


@dataclass(frozen=True, kw_only=True)
class DerivadosCrediHoyV2(DTO):
    edad: DerivadoCrediHoyV2[int]
    monto_maximo_40_smmlv: DerivadoCrediHoyV2[Decimal]
    monto_minimo_20_smmlv: DerivadoCrediHoyV2[Decimal]
    monto_maximo_por_aportes: DerivadoCrediHoyV2[Decimal]
    gastos_familiares: DerivadoCrediHoyV2[Decimal]
    cuota_credito_solicitado: DerivadoCrediHoyV2[Decimal]
    mora_externa_total: DerivadoCrediHoyV2[Decimal]
    deducciones_externas: DerivadoCrediHoyV2[Decimal]
    capacidad_pago: DerivadoCrediHoyV2[Decimal]

    def __post_init__(self):
        super().__post_init__()
        fechas = {v.fecha_evaluacion for v in vars(self).values()}
        if len(fechas) != 1:
            raise ValueError("Derivados deben compartir fecha de evaluacion")


class ResultadoVariable(str, Enum):
    CUMPLE = "CUMPLE"
    NO_CUMPLE = "NO_CUMPLE"
    NO_EVALUABLE = "NO_EVALUABLE"


class EfectoVariable(str, Enum):
    BLOQUEA = "BLOQUEA"
    GENERA_NOVEDAD = "GENERA_NOVEDAD"
    INFORMATIVO = "INFORMATIVO"
    PENDIENTE_DEFINICION = "PENDIENTE_DEFINICION"


@dataclass(frozen=True, kw_only=True)
class ResultadoVariableCrediHoyV2(DTO):
    codigo: str
    resultado: ResultadoVariable
    version_regla: str
    version_politica: str
    efecto: EfectoVariable = EfectoVariable.PENDIENTE_DEFINICION
    motivos: tuple[str, ...] = ()
    operandos_referenciados: tuple[str, ...] = ()
    derivados_referenciados: tuple[str, ...] = ()

    def __post_init__(self):
        super().__post_init__()
        for nombre in ("codigo", "version_regla", "version_politica"):
            texto(getattr(self, nombre), nombre)
        if not isinstance(self.resultado, ResultadoVariable) or not isinstance(self.efecto, EfectoVariable):
            raise TypeError("Resultado y efecto requieren enums V2")


@dataclass(frozen=True, kw_only=True)
class ResultadoGlobalCrediHoyV2(DTO):
    resultados: tuple[ResultadoVariableCrediHoyV2, ...] = ()
    pendientes: tuple[str, ...] = ()
    bloqueos_efectivos: None = field(default=None, init=False)
    novedades: None = field(default=None, init=False)
    conclusion_funcional: str = field(default="NO_DETERMINADA", init=False)
    continuidad_permitida: None = field(default=None, init=False)
    autorizado_decision_crediticia: bool = field(default=False, init=False)
