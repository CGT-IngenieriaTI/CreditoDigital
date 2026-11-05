"""Serializador existente compartido; sin fixtures ni construccion de hechos."""


from datetime import datetime, timezone
from .inventario import inventario_reglas_unico_v2
from .reglas import estado_evaluacion_regla, datos_faltantes_regla

def serializar_evaluacion(capacidad, reglas, request_id, *, fuente='SINTETICO_CD3C', fecha=None, evidencia_reglas=None, hechos_reglas=None):
    """Contrato de presentacion compartido, sin inferir hechos ni decisiones."""
    nombres = {f'UNICO_FILA_{r.fila}': r.nombre for r in inventario_reglas_unico_v2()}
    from .normativa import actualizacion_al_corte
    evidencia_reglas = dict(evidencia_reglas or {})
    if hechos_reglas is not None:
        actualizacion = actualizacion_al_corte(hechos_reglas.fecha_actualizacion_datos, fecha)
        if actualizacion is not None:
            evidencia_reglas['UNICO_FILA_9'] = actualizacion
        evidencia_reglas['UNICO_FILA_32'] = {'existeCredihoy111Registrado': hechos_reglas.existe_credihoy_111_registrado.a_dict(), 'efectoNormativo': 'PENDIENTE; ausencia de modalidad111 no determina aprobacion ni rechazo'}
        evidencia_reglas['UNICO_FILA_6'] = {'hecho': hechos_reglas.cuentas_embargadas.a_dict()}

    def importe(operando):
        return {'disponible': operando.disponible, 'valor': None if operando.valor is None else str(operando.valor)}
    return {'versionContrato': 'credihoy-shadow-lab-v1', 'requestId': request_id, 'fechaEvaluacion': (fecha or datetime.now(timezone.utc)).isoformat(), 'fuenteHechos': fuente, 'capacidadMatematica': {'estado': capacidad.estado.value, **importe(capacidad.capacidad_pago)}, 'cuotaParaCapacidad': importe(capacidad.cuota_nuevo_credito), 'cuotaContractual': {'disponible': False, 'valor': None}, 'gastosFamiliares': importe(capacidad.gastos_familiares), 'informacionInsuficiente': list(dict.fromkeys(list(capacidad.operandos_faltantes) + [dato for r in reglas.resultados for dato in datos_faltantes_regla(r)])), 'reglasPendientes': list(reglas.pendientes), 'reglas': [{'codigo': r.codigo, 'nombre': nombres[r.codigo], 'resultado': r.resultado.value, 'efecto': r.efecto.value, 'motivos': list(r.motivos), 'estadoEvaluacion': estado_evaluacion_regla(r), 'datosFaltantes': list(datos_faltantes_regla(r)), **({'evidencia': evidencia_reglas[r.codigo]} if evidencia_reglas and r.codigo in evidencia_reglas else {})} for r in reglas.resultados], 'recogida': {'estado': capacidad.validacion_recogida.estado_validacion.value, 'motivos': list(capacidad.validacion_recogida.motivos), 'observacionAplicacion': capacidad.cuotas_deudas_hdc_aplicables.observacion}, 'decisionCrediticia': 'NO_AUTORIZADA', 'conclusionFuncional': 'NO_DETERMINADA', 'autorizadoDecisionCrediticia': False, 'radicacionLinix': 'NO_EJECUTADA'}
