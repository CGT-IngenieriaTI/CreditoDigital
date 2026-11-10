"""LEGACY_SHADOW_CURUBA: catalogo historico, no parametros normativos de UNICO."""

from decimal import Decimal

from ..base import EstadoCertificacion, Hecho
from ..contratos import EstadoDefinicion, FormulaV2, ParametroV2, ParametrosCrediHoyV2


def parametros_observados_curuba(*, referencia_evidencia: str) -> ParametrosCrediHoyV2:
    if not isinstance(referencia_evidencia, str) or not referencia_evidencia.strip():
        raise ValueError("Requiere referencia documental")
    valores = (
        ("edad_minima", 18), ("plazo_maximo", 48),
        ("multiplicador_monto_maximo_smmlv", Decimal("40")),
        ("porcentaje_maximo_aportes", Decimal("0.90")),
        ("porcentaje_minimo_smmlv", Decimal("0.20")),
        ("porcentaje_gastos_familiares", Decimal("0.25")),
    )
    return ParametrosCrediHoyV2(
        version="observaciones-curuba-v1",
        valores=tuple(ParametroV2(nombre=nombre, version="1",
            estado=EstadoDefinicion.OBSERVADA_EN_CURUBA,
            hecho=Hecho(valor=valor, disponible=True, fuente="CURUBA_REFERENCIA",
                        referencia_evidencia=referencia_evidencia,
                        estado_certificacion=EstadoCertificacion.OBSERVADO))
            for nombre, valor in valores),
        formulas=(
            FormulaV2(formula_id="gastos_familiares", version="observada-1",
                expresion_documental="ingresos * 0.25", estado=EstadoDefinicion.OBSERVADA_EN_CURUBA),
            FormulaV2(formula_id="capacidad_pago", version="observada-1",
                expresion_documental="ingresos - deducciones_externas - deducciones_linix - cuota_credito_solicitado - gastos_familiares + cuota_credito_recogido",
                estado=EstadoDefinicion.OBSERVADA_EN_CURUBA),
            FormulaV2(formula_id="cuota_credito_solicitado", version="hipotesis-1",
                expresion_documental="monto / plazo; unidad y redondeo pendientes",
                estado=EstadoDefinicion.PENDIENTE_CERTIFICACION),
        ),
    )
