"""Capacidad shadow UNICO. No suma cuotas LINIX ni consulta/correlaciona HDC."""

from dataclasses import fields, replace
from decimal import Decimal, InvalidOperation, localcontext

from ..aritmetica import precision_exacta, trunc_cuota, trunc_producto
from ..base import EstadoCertificacion as EC, Hecho
from ..hechos import HechosExternosCrediHoyV2, HechosInternosCrediHoyV2
from ..gateway import materializar_cuotas_hdc_brutas
from .dto import (
    CapacidadCredihoyUnicoV2, EntradaCredihoyUnicoV2, EstadoCapacidadUnico,
    EstadoOperandoUnico as EO, EstadoValidacionRecogida as EV,
    ModalidadCredihoyPermitidaV2, OperandoCredihoyUnicoV2, OrigenReglaUnico as OR,
    ValidacionRecogidaUnicoV2,
)


def _operando(hecho, *, origen, motivo, estado_ausente=EO.NO_DISPONIBLE):
    if not isinstance(hecho, Hecho):
        raise TypeError("Requiere Hecho monetario tipado")
    datos = {f.name: getattr(hecho, f.name) for f in fields(Hecho)}
    datos["observacion"] = ((hecho.observacion + "; ") if hecho.observacion else "") + motivo
    if hecho.disponible and hecho.estado_certificacion not in {EC.OBSERVADO, EC.CERTIFICADO}:
        datos.update(valor=None, disponible=False)
        datos["observacion"] += "; SEMANTICA_OPERANDO_NO_UTILIZABLE"
    return OperandoCredihoyUnicoV2(**datos, origen_regla=origen,
        estado=EO.DISPONIBLE if datos["disponible"] else estado_ausente)


def _derivado(valor, *, nombre, motivo, origen, estado=EO.NO_DISPONIBLE):
    return OperandoCredihoyUnicoV2(valor=valor, disponible=valor is not None,
        fuente="DOMINIO_CREDIHOY_UNICO", estado_certificacion=EC.OBSERVADO if valor is not None else EC.NO_DISPONIBLE,
        estado=EO.DISPONIBLE if valor is not None else estado, origen_regla=origen,
        observacion=motivo, formula_id=nombre, formula_version="unico-8a-1")


def _correspondencia_recogida(entrada, hdc, cuota):
    if entrada.recogida is None or not entrada.recogida.aplica or not cuota.disponible:
        return None
    prueba = entrada.recogida.correspondencia_hdc
    if prueba is None:
        return "CORRESPONDENCIA_RECOGIDA_HDC_NO_DISPONIBLE"
    hecho = prueba.cuota_incluida
    if (not hecho.disponible or hecho.estado_certificacion != EC.CERTIFICADO
            or not hecho.fuente or not hecho.ruta_origen):
        return "CORRESPONDENCIA_RECOGIDA_HDC_NO_CERTIFICADA"
    if (prueba.obligacion != entrada.recogida.obligacion
            or not hdc.referencia_evidencia
            or prueba.referencia_agregado_hdc != hdc.referencia_evidencia
            or hecho.valor != cuota.valor):
        return "CORRESPONDENCIA_RECOGIDA_HDC_INCONSISTENTE"
    return None


def evaluar_capacidad_unico_v2(entrada: EntradaCredihoyUnicoV2, *,
        ingresos_mensuales: Hecho[Decimal], cuotas_deudas_hdc_brutas: Hecho[Decimal],
        modalidad_permitida: ModalidadCredihoyPermitidaV2 | None = None,
        cuotas_aplicables_explicitas: Hecho[Decimal] | None = None) -> CapacidadCredihoyUnicoV2:
    """Recibe magnitudes ya suministradas. Los tests inyectan HDC sintetico, no raw."""
    if not isinstance(entrada, EntradaCredihoyUnicoV2):
        raise TypeError("Requiere entrada UNICO, no legacy CURUBA ni otro producto")
    validacion = ValidacionRecogidaUnicoV2(entrada=entrada, modalidad_permitida=modalidad_permitida)
    if ingresos_mensuales.ruta_origen == 'hechosFinancieros.ingresoMensualRegistrado':
        ingresos_mensuales = replace(ingresos_mensuales, valor=None, disponible=False,
            estado_certificacion=EC.NO_CERTIFICADO,
            observacion='INGRESO_REGISTRADO_AP014_NO_ACREDITA_INGRESO_COMPUTABLE; '
                'requiere matriz de la solicitud exacta o soporte institucional aplicable')
    adaptado=ingresos_mensuales.ruta_origen=='adaptacionCongente.ingresoComputableAp014'
    ingresos = _operando(ingresos_mensuales, origen=OR.ADAPTACION_CONGENTE if adaptado else OR.REGLA_DURA_EXCEL,
                         motivo='V_SUELDO + V_INGVAR, adaptacion Congente versionada' if adaptado else
                         'Total mensual suministrado con procedencia')
    hdc = _operando(cuotas_deudas_hdc_brutas, origen=OR.ADAPTACION_CONGENTE,
        motivo="Inventario general exclusivo HDC; sin cuotas LINIX adicionales",
        estado_ausente=EO.HOMOLOGACION_HDC_PENDIENTE)
    recogida = _derivado(None, nombre="cuota_credihoy_recogido", origen=OR.ACLARACION_NEGOCIO,
        estado=EO.RECOGIDA_PENDIENTE_VALIDACION, motivo="; ".join(validacion.motivos)) if validacion.motivos else None
    if validacion.estado_validacion == EV.NO_APLICA:
        recogida = _derivado(Decimal("0"), nombre="cuota_credihoy_recogido",
            origen=OR.ACLARACION_NEGOCIO, motivo="Escenario explicitamente sin recogida; cero no inferido")
    elif validacion.estado_validacion == EV.VALIDA:
        recogida = _operando(entrada.recogida.cuota_actual, origen=OR.ACLARACION_NEGOCIO,
                             motivo="Cuota mensual LINIX de CrediHoy seleccionado misma modalidad")
    inconsistencias = []
    if ingresos.disponible and ingresos.valor < 0:
        inconsistencias.append("INGRESOS_MENSUALES_NEGATIVOS")
    if hdc.disponible and hdc.valor < 0:
        inconsistencias.append("CUOTAS_HDC_BRUTAS_NEGATIVAS")
    if recogida.disponible and hdc.disponible and recogida.valor > hdc.valor:
        inconsistencias.append("CUOTA_RECOGIDA_SUPERA_CUOTAS_HDC_BRUTAS")
    correspondencia = _correspondencia_recogida(entrada, hdc, recogida) if hdc.disponible else None
    if correspondencia == "CORRESPONDENCIA_RECOGIDA_HDC_INCONSISTENTE":
        inconsistencias.append(correspondencia)
    aplicables_valor = None
    if hdc.disponible and recogida.disponible and not inconsistencias and correspondencia is None:
        with localcontext() as ctx:
            ctx.prec = precision_exacta(hdc.valor, recogida.valor)
            aplicables_valor = hdc.valor - recogida.valor
    aplicables = _derivado(aplicables_valor, nombre="cuotas_hdc_aplicables",
        origen=OR.ACLARACION_NEGOCIO, estado=EO.INFORMACION_INCONSISTENTE if inconsistencias else
        EO.RECOGIDA_PENDIENTE_VALIDACION if correspondencia else EO.NO_DISPONIBLE,
        motivo="; ".join(inconsistencias) if inconsistencias else correspondencia or
               "HDC bruto menos cuota recogida con inclusion certificada en ese agregado")
    if cuotas_aplicables_explicitas is not None:
        if validacion.estado_validacion != EV.NO_APLICA or not hdc.disponible or inconsistencias:
            raise ValueError("Aplicables explicitas requieren agregado y escenario sin recogida")
        aplicables = _operando(cuotas_aplicables_explicitas, origen=OR.ADAPTACION_CONGENTE,
            motivo="Operando explicito del contexto; no homologa filtros ni autoriza decision")
        if aplicables.disponible and not Decimal("0") <= aplicables.valor <= hdc.valor:
            raise ValueError("Aplicables explicitas fuera del agregado")
    errores_cuota = []
    if entrada.monto_solicitado is None:
        errores_cuota.append("MONTO_NO_DISPONIBLE")
    elif entrada.monto_solicitado <= 0:
        errores_cuota.append("MONTO_NO_POSITIVO")
    if entrada.plazo_meses is None or not 1 <= entrada.plazo_meses <= 48:
        errores_cuota.append("PLAZO_FUERA_DE_1_A_48_MESES")
    if entrada.periodicidad_pago != "MENSUAL":
        errores_cuota.append("PERIODICIDAD_NO_MENSUAL")
    if entrada.modalidad_codigo is not None and entrada.modalidad_codigo != "111":
        errores_cuota.append("MODALIDAD_NUEVA_FUERA_DE_UNICO")
    cuota = _derivado(None if errores_cuota else trunc_cuota(entrada.monto_solicitado, entrada.plazo_meses),
        nombre="cuota_nuevo_credito", origen=OR.ADAPTACION_CONGENTE, estado=EO.PARAMETRO_INVALIDO,
        motivo="; ".join(errores_cuota) if errores_cuota else
               "TRUNC(monto/plazo mensual); Excel dice Periodicidad, divisor digital es numero de periodos")
    gastos = _derivado(trunc_producto(ingresos.valor, Decimal("0.25"))
        if ingresos.disponible and ingresos.valor >= 0 else None,
        nombre="gastos_familiares", origen=OR.REGLA_DURA_EXCEL,
        motivo="TRUNC(ingresosMensuales * 0.25) hacia cero; ingreso negativo no utilizable")
    operandos = dict(ingresos_mensuales=ingresos, cuotas_deudas_hdc_brutas=hdc,
        cuota_credihoy_recogido=recogida, cuotas_deudas_hdc_aplicables=aplicables,
        cuota_nuevo_credito=cuota, gastos_familiares=gastos)
    faltantes = tuple(nombre for nombre, op in operandos.items() if not op.disponible)
    valor = None
    if not faltantes and not inconsistencias:
        with localcontext() as ctx:
            ctx.prec = precision_exacta(ingresos.valor, aplicables.valor, cuota.valor, gastos.valor)
            valor = ingresos.valor - aplicables.valor - cuota.valor - gastos.valor
    estado = (EstadoCapacidadUnico.INFORMACION_INCONSISTENTE if inconsistencias else
              EstadoCapacidadUnico.NO_EVALUABLE if faltantes else EstadoCapacidadUnico.EVALUABLE)
    capacidad = _derivado(valor, nombre="capacidad_credihoy_unico", origen=OR.ADAPTACION_CONGENTE,
        estado=EO.INFORMACION_INCONSISTENTE if inconsistencias else EO.NO_DISPONIBLE,
        motivo="ingresos - cuotasHdcAplicables - cuotaNuevoCredito - gastosFamiliares; sin decision crediticia")
    return CapacidadCredihoyUnicoV2(**operandos, capacidad_pago=capacidad,
        validacion_recogida=validacion, estado=estado, operandos_faltantes=faltantes,
        inconsistencias=tuple(inconsistencias), cuotas_aplicables_explicitas=cuotas_aplicables_explicitas)


def construir_capacidad_unico_v2(entrada: EntradaCredihoyUnicoV2, *,
        hechos_internos: HechosInternosCrediHoyV2,
        hechos_externos: HechosExternosCrediHoyV2 | None = None,
        modalidad_permitida: ModalidadCredihoyPermitidaV2 | None = None) -> CapacidadCredihoyUnicoV2:
    if not isinstance(hechos_internos, HechosInternosCrediHoyV2):
        raise TypeError("Requiere hechos internos V2")
    from .normativa import ingreso_computable_congente
    ingresos=ingreso_computable_congente(hechos_internos.financieros)
    return evaluar_capacidad_unico_v2(entrada, ingresos_mensuales=ingresos,
        cuotas_deudas_hdc_brutas=materializar_cuotas_hdc_brutas(hechos_externos),
        modalidad_permitida=modalidad_permitida)


def partidas_hdc_conciliadas(preliminar):
    """Reconciliation is not approval of the debt policy or the credit."""
    return bool(preliminar and preliminar.get('reservas') == []
        and preliminar.get('conciliacionPartidasCompleta') is not False)


def calcular_preliminar_congente(entrada, ingresos, conciliacion):
    """Reuse the existing method; never promote a subtotal into complete debt."""
    from .normativa import POLITICA_FINANCIERA_CONGENTE
    from .conciliacion import reservas_para_revision
    if (conciliacion is None or not conciliacion.cobertura_completa
            or (not any(p.tratamiento=='INCLUIDA' for p in conciliacion.partidas)
                and not conciliacion.partidas_resueltas)
            or ingresos.ruta_origen!='adaptacionCongente.ingresoComputableAp014'
            or ingresos.referencia_evidencia!=POLITICA_FINANCIERA_CONGENTE
            or entrada.recogida is None or entrada.recogida.aplica):
        return None
    subtotal=conciliacion.subtotal_reconocido
    calculated=evaluar_capacidad_unico_v2(entrada,ingresos_mensuales=ingresos,
        cuotas_deudas_hdc_brutas=subtotal,cuotas_aplicables_explicitas=subtotal)
    def money(h):return {'disponible':h.disponible,'valor':str(h.valor) if h.disponible else None}
    return {'estado':'PRELIMINAR_NO_DECISORIA','versionPolitica':POLITICA_FINANCIERA_CONGENTE,
        'criterioEndeudamiento':conciliacion.criterio_funcional,
        'aprobacionRiesgosEndeudamiento':conciliacion.aprobacion_riesgos,
        'ingresoComputable':money(calculated.ingresos_mensuales),
        'subtotalMensualHdcReconocido':money(subtotal),
        'cuotaParaCapacidad':money(calculated.cuota_nuevo_credito),
        'gastosFamiliares':money(calculated.gastos_familiares),
        'capacidad':money(calculated.capacidad_pago),
        'procedencia':{'ingreso':ingresos.a_dict(),'subtotalHdc':subtotal.a_dict()},
        'reservas':[p.a_dict() for p in conciliacion.partidas if p.tratamiento=='PENDIENTE'],
        'pendientesConciliacion':reservas_para_revision(conciliacion),
        'conciliacionPartidasCompleta':conciliacion.partidas_resueltas,
        'endeudamientoCompleto':conciliacion.cuotas_aplicables.disponible,
        'autorizadoDecisionCrediticia':False,
        'observacion':('Obligaciones HDC conciliadas; criterio pendiente de validacion institucional. '
            if conciliacion.partidas_resueltas else 'Subtotal HDC, no endeudamiento completo. ')
            +'Capacidad preliminar, no aprobacion. Cuota para capacidad no es cuota contractual LINIX.'}


def aplicar_cuota_observada_preliminar(preliminar, cuota, referencia, *, fuente='LINIX_EJECUCION_MANUAL'):
    """Replace only the quota operand; preserve income, debt reserves and policy."""
    from copy import deepcopy
    if (type(cuota) is not Decimal or not cuota.is_finite() or cuota <= 0
            or not isinstance(referencia, str) or not referencia.strip()
            or preliminar['estado'] != 'PRELIMINAR_NO_DECISORIA'
            or preliminar['autorizadoDecisionCrediticia'] is not False
            or fuente not in {'LINIX_EJECUCION_MANUAL','LINIX_CALCULO_CUOTA'}):
        raise ValueError('CAPACIDAD_PRELIMINAR_INVALIDA')
    names=('ingresoComputable','subtotalMensualHdcReconocido','gastosFamiliares','cuotaParaCapacidad','capacidad')
    values={}
    for name in names:
        h=preliminar[name]
        if h['disponible'] is not True or type(h['valor']) is not str:
            raise ValueError('OPERANDO_PRELIMINAR_NO_DISPONIBLE')
        try:
            values[name]=Decimal(h['valor'])
        except InvalidOperation:
            raise ValueError('OPERANDO_PRELIMINAR_INVALIDO') from None
        if (not values[name].is_finite()
                or (name != 'capacidad' and values[name] < 0)):
            raise ValueError('OPERANDO_PRELIMINAR_INVALIDO')
    with localcontext() as ctx:
        ctx.prec=precision_exacta(cuota,*values.values())
        base=values['ingresoComputable']-values['subtotalMensualHdcReconocido']-values['gastosFamiliares']
        if base-values['cuotaParaCapacidad'] != values['capacidad']:
            raise ValueError('CAPACIDAD_PRELIMINAR_NO_CONCILIA')
        residual=base-cuota
    result=deepcopy(preliminar)
    result.update(cuotaParaCapacidad={'disponible':True,'valor':str(cuota)},
        capacidad={'disponible':True,'valor':str(residual)},
        versionFuenteCuota='CREDIHOY_CUOTA_'+('INSTITUCIONAL_MANUAL' if fuente=='LINIX_EJECUCION_MANUAL' else 'DINAMICA_LINIX')+'_PRELIMINAR_V1',
        cuotaAnterior=deepcopy(preliminar['cuotaParaCapacidad']),
        observacion='Cuota institucional de referencia LINIX aplicada solo a capacidad preliminar; '
            'no oferta contractual aceptada. '
            +('Obligaciones HDC conciliadas; criterio pendiente de validacion institucional. '
                if partidas_hdc_conciliadas(preliminar) else 'Deuda HDC incompleta; ')
            +'no autoriza aprobacion.')
    result['procedencia']['cuota']={'fuente':fuente,'referenciaEvidencia':referencia}
    return result
