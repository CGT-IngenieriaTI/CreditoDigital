"""Consumo puro de respuestas de CD2B: motor existente, sin HTTP ni persistencia."""

from dataclasses import replace
from datetime import datetime

from apps.integraciones.risk_gateway.contratos import HDCNormalizado
from apps.integraciones.risk_gateway.operaciones.servicio import ResultadoOperacion

from ..base import EstadoCertificacion as EC, Hecho
from ..gateway import adaptar_hdc_a_v2, materializar_cuotas_hdc_brutas
from .dto import EntradaCredihoyUnicoV2
from .presentacion import serializar_evaluacion
from .motor import evaluar_capacidad_unico_v2
from .reglas import HechosReglasUnicoV2, evaluar_reglas_unico_v2
from .normativa import incorporar_smmlv, evidencia_monetaria


def _respuesta(op, proveedor, dto, solicitud_operaciones):
    if (type(op) is not ResultadoOperacion or op.proveedor != proveedor or op.reutilizada
            or op.operacion_id not in solicitud_operaciones
            or solicitud_operaciones[op.operacion_id] != (proveedor, op.request_id)
            or op.estado not in {"COMPLETADA", "SIN_INFORMACION"} or type(op.respuesta) is not dto):
        raise ValueError("RESPUESTA_NO_RECUPERABLE_O_PENDIENTE_CONCILIACION")
    r = op.respuesta
    esperado = "COMPLETADO" if op.estado == "COMPLETADA" else "SIN_INFORMACION"
    if (r["requestId"] != str(op.request_id) or r["resultado"]["consulta_id"] != str(op.consulta_id)
            or r["versionContrato"] != op.version_contrato or r["resultado"]["estado_tecnico"] != esperado):
        raise ValueError("RESPUESTA_DE_OTRA_OPERACION")
    return r


def evaluar_respuestas_controladas(entrada, *, hdc, operaciones_esperadas,
                                   ingresos, hechos_reglas, fecha, request_id,
                                   cuotas_aplicables_explicitas=None):
    """La pertenencia se suministra desde las reservas DB de la solicitud autorizada."""
    if (type(entrada) is not EntradaCredihoyUnicoV2 or not isinstance(ingresos, Hecho)
            or type(hechos_reglas) is not HechosReglasUnicoV2
            or type(fecha) is not datetime or fecha.utcoffset() is None
            or type(operaciones_esperadas) is not dict or len(operaciones_esperadas) != 1):
        raise ValueError("CONTEXTO_CONTROLADO_INVALIDO")
    h = _respuesta(hdc, "HDC", HDCNormalizado, operaciones_esperadas)
    externos = adaptar_hdc_a_v2(h)
    # Nunca se recurre al historico ni se suman cuentas si falta el agregado.
    cuotas = materializar_cuotas_hdc_brutas(externos)
    conciliacion = None
    if cuotas_aplicables_explicitas is None and entrada.recogida is not None and not entrada.recogida.aplica:
        from .conciliacion import conciliar_cuotas_hdc
        conciliacion = conciliar_cuotas_hdc(h, criterio_funcional=True)
        cuotas_aplicables_explicitas = conciliacion.cuotas_aplicables if cuotas.disponible else None
    capacidad = evaluar_capacidad_unico_v2(entrada, ingresos_mensuales=ingresos,
        cuotas_deudas_hdc_brutas=cuotas,
        cuotas_aplicables_explicitas=cuotas_aplicables_explicitas)
    consulta = Hecho(valor=True, disponible=True, fuente="RISK_GATEWAY_OPERACIONES_CONTROLADAS",
        referencia_evidencia=str(hdc.consulta_id), ruta_origen="resultado.estado_tecnico",
        estado_certificacion=EC.CERTIFICADO, observacion="Consulta realizada; no resultado de aprobacion")
    contexto = incorporar_smmlv(replace(hechos_reglas, consulta_central_realizada=consulta),
        fecha=fecha, modalidad=entrada.modalidad_codigo)
    reglas = evaluar_reglas_unico_v2(entrada, hechos=contexto, capacidad=capacidad, fecha_evaluacion=fecha)
    r = serializar_evaluacion(capacidad, reglas, request_id, fuente="CONSULTA_REAL_CONTROLADA", fecha=fecha,
        evidencia_reglas=evidencia_monetaria(entrada, contexto), hechos_reglas=contexto)
    r.update(estadoTramite="PENDIENTE_REVISION", revisionHumanaHabilitada=False,
        motivoRevision="DECISION_Y_VIA_DE_REVISION_REQUIEREN_AUTORIZACION_FUNCIONAL",
        ingresosAplicables={"disponible": capacidad.ingresos_mensuales.disponible,
            "valor": None if capacidad.ingresos_mensuales.valor is None else str(capacidad.ingresos_mensuales.valor)},
        cuotasHdcAplicables={"disponible": capacidad.cuotas_deudas_hdc_aplicables.disponible,
            "valor": None if capacidad.cuotas_deudas_hdc_aplicables.valor is None else str(capacidad.cuotas_deudas_hdc_aplicables.valor)},
        evidenciaConsultaControlada={"HDC": {"consultaId": str(hdc.consulta_id), "estado": hdc.estado,
                "fechaOriginal": externos.fecha_consulta_original, "agregadoUtilizable": cuotas.disponible,
                "motivoAgregado": cuotas.observacion}}, vigenciaParaDecision="NO_CERTIFICADA")
    if conciliacion is not None:
        r['conciliacionHdc'] = conciliacion.a_dict()
        from .motor import calcular_preliminar_congente
        preliminary=calcular_preliminar_congente(entrada,ingresos,conciliacion)
        if preliminary is not None:
            r['capacidadPreliminar']=preliminary
            r['politicaFinancieraCongente']=preliminary['versionPolitica']
            row=next(v for v in r['reglas'] if v['codigo']=='UNICO_FILA_2')
            row['evidencia']={'capacidadPreliminar':preliminary}
            if not capacidad.capacidad_pago.disponible:
                row['motivos']=['CAPACIDAD_PRELIMINAR_NO_DECISORIA_DEUDA_INCOMPLETA']
    return r
