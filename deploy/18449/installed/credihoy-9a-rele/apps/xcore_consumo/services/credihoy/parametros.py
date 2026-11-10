"""Politica versionada; la fecha de vigencia la establece el llamador."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, kw_only=True)
class PoliticaCrediHoy:
    version: str
    vigencia_desde: date
    vigencia_hasta: date | None = None
    plazo_minimo: int = 1
    plazo_maximo: int = 48
    porcentaje_monto_minimo_smmlv: Decimal = Decimal("0.20")
    maximo_smmlv: Decimal = Decimal("40")
    porcentaje_maximo_aportes: Decimal = Decimal("0.90")
    garantias_permitidas: tuple[str, ...] = ("Firma Personal", "Fianza")
    formas_pago_permitidas: tuple[str, ...] = (
        "Libranza", "D\u00e9bito autom\u00e1tico", "Ventanilla",
    )
    producto_permitido: str = "consumo"

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("La politica requiere version")
        if type(self.vigencia_desde) is not date:
            raise TypeError("vigencia_desde debe ser date")
        if self.vigencia_hasta is not None:
            if type(self.vigencia_hasta) is not date:
                raise TypeError("vigencia_hasta debe ser date")
            if self.vigencia_hasta < self.vigencia_desde:
                raise ValueError("Vigencia invertida")
        if (type(self.plazo_minimo) is not int or type(self.plazo_maximo) is not int
                or not 1 <= self.plazo_minimo <= self.plazo_maximo):
            raise ValueError("Rango de plazos invalido")
        for nombre in ("porcentaje_monto_minimo_smmlv", "maximo_smmlv", "porcentaje_maximo_aportes"):
            valor = getattr(self, nombre)
            if not isinstance(valor, Decimal) or not valor.is_finite() or valor <= 0:
                raise ValueError(f"{nombre} debe ser Decimal positivo finito")
        if self.porcentaje_maximo_aportes > 1:
            raise ValueError("porcentaje_maximo_aportes no puede superar 1")
        if self.porcentaje_monto_minimo_smmlv > self.maximo_smmlv:
            raise ValueError("El monto minimo supera el maximo")
        for nombre in ("garantias_permitidas", "formas_pago_permitidas"):
            valores = getattr(self, nombre)
            if (not isinstance(valores, tuple) or not valores
                    or any(not isinstance(v, str) or not v.strip() for v in valores)
                    or len(set(valores)) != len(valores)):
                raise ValueError(f"{nombre} requiere una tupla no vacia sin duplicados")
        if not isinstance(self.producto_permitido, str) or not self.producto_permitido.strip():
            raise ValueError("producto_permitido es obligatorio")

    def vigente(self, fecha: date) -> bool:
        return self.vigencia_desde <= fecha and (
            self.vigencia_hasta is None or fecha <= self.vigencia_hasta
        )
