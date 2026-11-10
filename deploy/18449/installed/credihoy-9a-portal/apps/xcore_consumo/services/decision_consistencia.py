"""Invariantes tecnicos de aprobacion de Consumo, no una politica de rechazo."""

from decimal import Decimal, InvalidOperation


class DecisionConsumoBloqueada(ValueError):
    def __init__(self, categoria, codigo):
        self.categoria = categoria
        self.codigo = codigo
        # No incluir montos, identidad ni payload financiero en el error.
        super().__init__(f"{categoria}: {codigo}")


def _decimal_monetario(value, campo):
    if value is None or (type(value) is str and not value.strip()):
        raise DecisionConsumoBloqueada("INFORMACION_INSUFICIENTE", campo + "_NO_DISPONIBLE")
    if type(value) not in (int, float, str, Decimal):
        raise DecisionConsumoBloqueada("ERROR_TECNICO", campo + "_INVALIDO")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise DecisionConsumoBloqueada("ERROR_TECNICO", campo + "_INVALIDO") from None
    if not result.is_finite():
        raise DecisionConsumoBloqueada("ERROR_TECNICO", campo + "_NO_FINITO")
    return result


def validar_monto_solicitado(value):
    result = _decimal_monetario(value, "MONTO_SOLICITADO")
    if result <= 0:
        raise DecisionConsumoBloqueada("ERROR_TECNICO", "MONTO_SOLICITADO_NO_POSITIVO")
    return result


def validar_limite_aprobacion(limite, solicitado):
    monto = validar_monto_solicitado(solicitado)
    maximo = _decimal_monetario(limite, "MONTO_MAX_APROBADO")
    if maximo < 0:
        raise DecisionConsumoBloqueada("ERROR_TECNICO", "MONTO_MAX_APROBADO_NEGATIVO")
    if maximo == 0:
        # No elegir RECHAZADO/REVISION: ese desenlace requiere politica aprobada.
        raise DecisionConsumoBloqueada("CAPACIDAD_INSUFICIENTE", "LIMITE_MONETARIO_CERO")
    return min(maximo, monto)


def validar_monto_aprobado(aprobado, solicitado):
    monto = validar_monto_solicitado(solicitado)
    result = _decimal_monetario(aprobado, "MONTO_APROBADO")
    if result < 0:
        raise DecisionConsumoBloqueada("ERROR_TECNICO", "MONTO_APROBADO_NEGATIVO")
    if result == 0:
        raise DecisionConsumoBloqueada("CAPACIDAD_INSUFICIENTE", "MONTO_APROBADO_CERO")
    if result > monto:
        raise DecisionConsumoBloqueada("ERROR_TECNICO", "MONTO_APROBADO_SUPERA_SOLICITADO")
    return result
