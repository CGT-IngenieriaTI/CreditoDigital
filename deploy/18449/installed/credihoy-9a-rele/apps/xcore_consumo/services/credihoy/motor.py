"""Motor puro: ELEGIBLE solo significa cumplir las reglas de esta version."""

from .contexto import ContextoEvaluacionCrediHoy
from .parametros import PoliticaCrediHoy
from .reglas import REGLAS_CREDIHOY
from .resultados import EstadoEvaluacion, EstadoRegla, ResultadoEvaluacion


class MotorElegibilidadCrediHoy:
    def __init__(self, politica: PoliticaCrediHoy):
        self.politica = politica

    def evaluar(self, contexto: ContextoEvaluacionCrediHoy) -> ResultadoEvaluacion:
        reglas = tuple(regla.evaluar(contexto, self.politica) for regla in REGLAS_CREDIHOY)
        estados = {regla.estado for regla in reglas}
        revision = bool(estados & {EstadoRegla.REVISION_MANUAL, EstadoRegla.PENDIENTE_POLITICA})
        if EstadoRegla.NO_CUMPLE in estados:
            global_ = EstadoEvaluacion.NO_ELEGIBLE
        elif EstadoRegla.INFORMACION_INSUFICIENTE in estados:
            global_ = EstadoEvaluacion.INFORMACION_INSUFICIENTE
        elif revision:
            global_ = EstadoEvaluacion.REVISION_MANUAL
        else:
            global_ = EstadoEvaluacion.ELEGIBLE
        return ResultadoEvaluacion(
            estado_global=global_, reglas=reglas, version_politica=self.politica.version,
            requiere_revision_manual=revision,
            motivos=tuple(dict.fromkeys(r.motivo for r in reglas if r.estado not in (
                EstadoRegla.CUMPLE, EstadoRegla.NO_APLICA,
            ))), fecha_evaluacion=contexto.fecha_evaluacion,
        )
