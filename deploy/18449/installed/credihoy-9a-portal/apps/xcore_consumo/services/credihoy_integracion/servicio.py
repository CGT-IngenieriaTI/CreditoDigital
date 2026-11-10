"""Composicion explicita para tests/servicio aislado; no persiste ni aprueba."""

from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import uuid4

from ..credihoy import (
    ContextoEvaluacionCrediHoy, DatosDatacredito, MotorElegibilidadCrediHoy,
    ResultadoEvaluacion, EstadoEvaluacion, EstadoRegla,
)
from .dto import ApiDataCrediHoyError, VERSION_CONTRATO
from .internos import EvidenciaInterna, HechosInternosAdaptados, hechos_no_disponibles


def construir_contexto(formulario: ContextoEvaluacionCrediHoy, *, internos: HechosInternosAdaptados,
                       datacredito: DatosDatacredito) -> ContextoEvaluacionCrediHoy:
    """El formulario ya esta normalizado; no arrastra hechos internos anteriores."""
    return replace(formulario, interno=internos.datos, datacredito=datacredito)


@dataclass(frozen=True, kw_only=True)
class EvaluacionIntegrada:
    contexto: ContextoEvaluacionCrediHoy = field(repr=False)
    resultado: ResultadoEvaluacion
    evidencia_interna: EvidenciaInterna
    autorizado_decision_crediticia: bool = field(default=False, init=False)


class ServicioEvaluacionCrediHoy:
    def __init__(self, *, cliente, adaptador, politica):
        self.cliente = cliente
        self.adaptador = adaptador
        self.motor = MotorElegibilidadCrediHoy(politica)

    def evaluar(self, *, formulario: ContextoEvaluacionCrediHoy, numero_documento: str,
                fecha_evaluacion: datetime, datacredito: DatosDatacredito, request_id=None):
        if not isinstance(fecha_evaluacion, datetime) or fecha_evaluacion.date() != formulario.fecha_evaluacion:
            raise ValueError("La fecha de evaluacion HTTP debe coincidir con el contexto")
        request_id = request_id if request_id is not None else str(uuid4())
        try:
            respuesta = self.cliente.consultar(numero_documento=numero_documento,
                                               fecha_evaluacion=fecha_evaluacion, request_id=request_id)
            internos = self.adaptador.adaptar(respuesta)
        except ApiDataCrediHoyError as error:
            internos = hechos_no_disponibles(error.codigo.value, request_id)
        contexto = construir_contexto(formulario, internos=internos, datacredito=datacredito)
        resultado = self.motor.evaluar(contexto)
        # En este contrato CH04 no puede rechazar por un tope parcial conocido.
        # El motor puro conserva su politica; esta restriccion es de integracion v1.
        motivo = internos.evidencia.codigo_error or internos.evidencia.motivo_aportes
        reglas = tuple(
            replace(r, cumple=None, estado=EstadoRegla.INFORMACION_INSUFICIENTE, motivo=motivo,
                    parametro={**r.parametro, "contrato_integracion": VERSION_CONTRATO,
                               "utilizable_decision": False})
            if r.codigo == "CH04_TOPE_MONTO" else r for r in resultado.reglas
        )
        estado = (EstadoEvaluacion.NO_ELEGIBLE if any(r.estado == EstadoRegla.NO_CUMPLE for r in reglas)
                  else EstadoEvaluacion.INFORMACION_INSUFICIENTE)
        resultado = replace(resultado, reglas=reglas, estado_global=estado,
                            motivos=tuple(dict.fromkeys(r.motivo for r in reglas
                                if r.estado not in (EstadoRegla.CUMPLE, EstadoRegla.NO_APLICA))))
        return EvaluacionIntegrada(contexto=contexto, resultado=resultado,
                                   evidencia_interna=internos.evidencia)
