class OperacionError(Exception):
    """Errores seguros: sin payload, identidad ni texto remoto."""

    codigo = "OPERACION_GATEWAY_BLOQUEADA"

    def __init__(self, codigo=None):
        self.codigo = codigo or self.codigo
        super().__init__(self.codigo)


class OrquestacionDesactivada(OperacionError):
    codigo = "ORQUESTACION_GATEWAY_DESACTIVADA"


class ConsentimientoInvalido(OperacionError):
    codigo = "CONSENTIMIENTO_GATEWAY_INVALIDO"


class IdentidadInconsistente(OperacionError):
    codigo = "IDENTIDAD_GATEWAY_INCONSISTENTE"


class OperacionIncompatible(OperacionError):
    codigo = "OPERACION_GATEWAY_INCOMPATIBLE"
