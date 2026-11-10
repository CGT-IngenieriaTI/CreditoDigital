"""Aritmetica Decimal neutral: no expresa politica de producto."""

from decimal import Decimal, ROUND_DOWN, localcontext

from .base import decimal_finito


def precision_exacta(*valores: Decimal) -> int:
    for valor in valores:
        decimal_finito(valor, "operando")
    return max(28, max(v.adjusted() for v in valores)
               - min(v.as_tuple().exponent for v in valores) + len(valores) + 4)


def trunc_producto(valor: Decimal, factor: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = precision_exacta(valor, factor)
        return (valor * factor).to_integral_value(rounding=ROUND_DOWN)


def trunc_cuota(monto: Decimal, plazo: int) -> Decimal:
    if type(plazo) is not int or plazo <= 0:
        raise ValueError("Divisor requiere int positivo")
    with localcontext() as ctx:
        ctx.prec = precision_exacta(monto, Decimal(plazo))
        return monto // Decimal(plazo)
