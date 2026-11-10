"""Snapshot literal y versionado del Excel, sin ejecutar sus condiciones."""

from dataclasses import dataclass, field
import json
from pathlib import Path

from ..base import DTO
from ..contratos import EfectoVariable
from .dto import OrigenReglaUnico


@dataclass(frozen=True, kw_only=True)
class ReglaDocumentalUnicoV2(DTO):
    fila: int
    validacion: str
    nombre: str
    observacion: str
    condicion_original: str
    estado_original: str
    fuente_original: str | None
    referencia_original: str | None
    comentario_original: str | None
    efecto: EfectoVariable = EfectoVariable.PENDIENTE_DEFINICION
    origen: OrigenReglaUnico = field(default=OrigenReglaUnico.REGLA_DURA_EXCEL, init=False)
    producto: str = field(default="CREDIHOY UNICO (NUEVO)", init=False)

    @property
    def activa(self):
        return self.estado_original == "A"

    def __post_init__(self):
        super().__post_init__()
        if self.estado_original not in {"A", "I"}:
            raise ValueError("Estado documental desconocido")
        if not self.activa and self.efecto in {EfectoVariable.BLOQUEA, EfectoVariable.GENERA_NOVEDAD}:
            raise ValueError("Regla inactiva no puede bloquear ni generar novedad")


def inventario_reglas_unico_v2() -> tuple[ReglaDocumentalUnicoV2, ...]:
    datos = json.loads(Path(__file__).with_name("reglas_excel.json").read_text(encoding="utf-8"))
    reglas = []
    for fila in datos["reglas_unico"]:
        c = fila["celdas_originales"]
        if c["A"] != "CREDIHOY UNICO (NUEVO)":
            raise ValueError("Fila ajena a UNICO")
        reglas.append(ReglaDocumentalUnicoV2(fila=fila["fila"], validacion=c["B"], nombre=c["C"],
            observacion=c["D"], condicion_original=c["E"], estado_original=c["F"],
            fuente_original=c.get("G"), referencia_original=c.get("H"), comentario_original=c.get("I")))
    return tuple(reglas)
