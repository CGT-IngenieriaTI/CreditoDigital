"""Explicacion de resultados guardados; no modifica hechos ni ejecuta reglas."""
from pathlib import Path
import json
from .motor import partidas_hdc_conciliadas

FUENTES = {
    'ingresos_mensuales': ('Ingreso mensual computable', 'LINIX V2 y soporte de ingresos del titular',
        'Corroborar los componentes mensuales utilizables; el salario observado no equivale al ingreso total.'),
    'cuotas_deudas_hdc_brutas': ('Agregado de cuotas HDC', 'HDC persistida del mismo expediente',
        'Recuperar el agregado oficial con unidad, moneda y fecha; no sumar cuentas individuales ni consultar otra vez.'),
    'cuotas_deudas_hdc_aplicables': ('Cuotas HDC aplicables', 'HDC persistida y politica de obligaciones',
        'Ratificar vigencia, precedencia de estados, tratamiento CTC y exclusion institucional Congente.'),
    'gastos_familiares': ('Gastos familiares', 'Ingreso computable y politica de capacidad',
        'Acreditar ingreso computable y el criterio institucional aplicable.'),
    'capacidad_pago': ('Capacidad de pago', 'Operandos acreditados y politica de capacidad',
        'Completar los operandos faltantes y ratificar el umbral y su efecto; no repetir scoring.'),
    'cuentas_embargadas': ('Embargos', 'HDC/Datacredito del mismo titular',
        'Acreditar el hecho de embargos y la cobertura del informe al corte; una cuenta individual no prueba ausencia global.'),
    'edad_anios': ('Edad', 'Nacimiento LINIX conservado', 'Verificar la edad al corte de la evaluacion.'),
    'smmlv_vigente': ('SMMLV vigente', 'Parametro institucional vigente',
        'Acreditar importe, unidad COP y vigencia del SMMLV aplicable al corte.'),
    'saldo_aportes_utilizable': ('Aportes utilizables', 'LINIX V2 y politica de aportes',
        'Certificar el saldo utilizable y su corte; el saldo observado no acredita disponibilidad.'),
    'calificacion_interna': ('Calificacion interna global', 'Cartera LINIX V2',
        'Certificar la calificacion global; no sustituirla por la de una obligacion individual.'),
    'persona_natural': ('Persona natural', 'Tercero oficial LINIX',
        'Recuperar la clasificacion explicita del tercero; el contrato V2 actual no la entrega. No inferirla del documento o modalidad.'),
    'consulta_central_realizada': ('Consulta HDC', 'Operacion HDC persistida del expediente',
        'Verificar el vinculo existente; no realizar otra consulta.'),
    'monto_solicitado': ('Monto', 'Solicitud persistida', 'Verificar monto positivo sin modificar el expediente.'),
    'periodicidad_pago': ('Periodicidad', 'Solicitud persistida', 'Verificar la seleccion conservada.'),
    'plazo_meses': ('Plazo', 'Solicitud persistida', 'Verificar el plazo conservado.'),
}
POLITICAS = {
    'UNICO_FILA_9': 'Aplicar el mes calendario inclusivo ya definido, con fecha real de actualizacion y corte acreditados; no usar afiliacion ni renovar fechas desde el formulario.',
    'UNICO_FILA_31': 'Definir aplicabilidad a un credito nuevo frente a renovacion, identificacion del credito y calculo del promedio. No excluir por no recoger sin politica expresa.',
    'UNICO_FILA_32': 'Si existe CrediHoy 111 vigente, debe recogerse el propio credito: no se permite coexistencia. Acreditar obligacion, titular y saldo liquidable; no recoger otros productos ni aprobar por ausencia de saldo.',
    'UNICO_FILA_2': 'Ratificar el umbral y efecto de capacidad con todos los operandos acreditados.',
}


def matriz_documental():
    raw = json.loads(Path(__file__).with_name('reglas_excel.json').read_text(encoding='utf-8'))
    return {'version': 'EXCEL_UNICO_' + raw['sha256'][:8], 'sha256': raw['sha256'],
        'archivo': raw['archivo'], 'autorizacionEfectosAcreditada': False}


def propuesta_fila32():
    """Functional proposal only; no facts or policy are inferred from the case."""
    raw=json.loads(Path(__file__).with_name('reglas_excel.json').read_text(encoding='utf-8'))
    row=next(r for r in raw['reglas_unico'] if r['fila']==32)
    return {'version':'CREDIHOY_UNICO_PROPUESTA_FILA32_20261026_V3',
        'condicionFuncionalConfirmada':True,'coexistencia111Permitida':False,
        'estadoVigente':'RECOGIDA_OBLIGATORIA_CONFIRMADA','estadoMotorPersistido':'PENDIENTE_POLITICA',
        'aprobada':False,'alcanceAprobada':'NO_ACTIVA_DECISION_FINAL','responsable':'Credito y Cartera',
        'fuente':{'archivo':raw['archivo'],'sha256':raw['sha256'],'hoja':raw['hoja'],'celda':'D32',
            'texto':row['celdas_originales']['D'],'advertencia':row['celdas_originales']['G']},
        'escenarios':[
            {'caso':'SIN_RECOGIDA_SIN_CREDIHOY_VIGENTE','propuesta':'NO_APLICA_CON_EVIDENCIA_DE_AUSENCIA',
             'evidenciaRequerida':['TITULAR_Y_CORTE','COBERTURA_MODALIDAD_111','AUSENCIA_EXPLICITA','SIN_RECOGIDA'],
             'validacionesMotor':['NO_INFERIR_AUSENCIA_POR_FALTA_DE_DATOS','NO_EXTENDER_A_OTROS_PRODUCTOS'],
             'impactoElegibilidad':'Solo excluir esta regla con aplicabilidad autorizada; las otras reglas siguen vigentes.',
             'impactoCapacidad':'Sin sumas ni descuentos por recogida; conservar cuotas HDC conciliadas.',
             'requiere':'Acreditar ausencia vigente, cobertura y corte; no requiere otra decision sobre coexistencia.'},
            {'caso':'SIN_RECOGIDA_CON_CREDIHOY_VIGENTE','propuesta':'REQUIERE_RECOGIDA_OBLIGATORIA_NO_COEXISTENCIA',
             'evidenciaRequerida':['TITULAR_Y_CORTE','OBLIGACION_111_VIGENTE','SALDO_Y_CUOTA_CONCILIADOS','SIN_RECOGIDA'],
             'validacionesMotor':['IDENTIFICAR_OBLIGACION','NO_DUPLICAR_CUOTA_LINIX_Y_HDC','NO_ASUMIR_RECOGIDA'],
             'impactoElegibilidad':'No permite continuar sin recoger el propio CrediHoy 111; no es rechazo crediticio final.',
             'impactoCapacidad':'No liberar la cuota vigente ni sumar nuevamente la observada en LINIX.',
             'requiere':'Seleccion y evidencia autenticas de recogida del propio CrediHoy 111 vigente.'},
            {'caso':'RECOGIDA_CREDIHOY_MISMA_MODALIDAD','propuesta':'VERIFICAR_OBLIGACION_SALDO_Y_TITULAR',
             'evidenciaRequerida':['TITULAR_Y_CORTE','ID_OBLIGACION_111','SALDO_RECOGIDA','CUOTA_HDC_CONCILIADA','SOLICITUD_RECOGIDA'],
             'validacionesMotor':['MISMO_TITULAR_Y_MODALIDAD','CONCILIAR_IDENTIDAD_HDC_LINIX','NO_LIBERAR_DOS_VECES_LA_CUOTA',
                 'ACREDITAR_INCLUSION_PREVIA_EN_BASE_HDC','VERIFICAR_COBERTURA_FINANCIERA_DE_LA_RECOGIDA'],
             'impactoElegibilidad':'Recogida obligatoria confirmada; verificar su contrato y evidencia, sin aprobacion final.',
             'impactoCapacidad':'Solo liberar una cuota previamente incluida en la base HDC; una cuota Congente excluida no se suma ni se libera. Sin efecto nuevo en el motor.',
             'requiere':'Obligacion y titular propios, saldo liquidable y evidencia de recogida; compensacion solo por LINIX.'},
            {'caso':'HECHO_O_COBERTURA_INSUFICIENTE','propuesta':'NO_EVALUABLE',
             'requiere':'Hecho institucional del mismo titular y corte, no inferir cero.'}],
        'decisionPendiente':None,'integracionPendiente':'EVIDENCIA_RECOGIDA_Y_EXCEPCIONES_ESTAMENTOS',
        'gate':'EVIDENCIA_DE_RECOGIDA_PROPIA_SIN_DECISION_FINAL',
        'autorizaDecision':False,'modificaEvaluacion':False}


def evaluar_propuesta_fila32(existe_vigente, entrada, *, misma_titularidad, solicitud_id=None):
    """Confirmed functional condition, separate from persisted matrix decisions."""
    from ..base import Hecho, EstadoCertificacion as EC
    from .dto import ValidacionRecogidaUnicoV2, EstadoValidacionRecogida as EV
    result={'estado':'NO_EVALUABLE','autorizaDecision':False,'modificaEvaluacion':False,
        'coexistenciaPermitida':False,'compensacionEjecutada':False,'solicitudId':str(solicitud_id) if solicitud_id else None}
    def known(h):
        return (isinstance(h,Hecho) and h.disponible and type(h.valor) is bool
            and h.estado_certificacion==EC.CERTIFICADO
            and all((h.fuente,h.ruta_origen,h.referencia_evidencia,h.fecha_corte)))
    if not known(existe_vigente):return result
    result['evidencia']={'existencia111':existe_vigente.a_dict(),
        'titularidad':misma_titularidad.a_dict() if isinstance(misma_titularidad,Hecho) else None,
        'entrada':entrada.a_dict(),'version':'CREDIHOY_RECOGIDA_OBLIGATORIA_20261026'}
    if entrada.recogida is None:return result
    if not existe_vigente.valor:
        return {**result,'estado':'RECOGIDA_INCONSISTENTE' if entrada.recogida.aplica else 'NO_APLICA'}
    if not entrada.recogida.aplica:return {**result,'estado':'REQUIERE_RECOGIDA_OBLIGATORIA'}
    if not known(misma_titularidad) or misma_titularidad.fecha_corte!=existe_vigente.fecha_corte:return result
    if not misma_titularidad.valor:return {**result,'estado':'TITULAR_RECOGIDA_DISTINTO'}
    validation=ValidacionRecogidaUnicoV2(entrada=entrada)
    if validation.estado_validacion==EV.NO_VALIDA:return {**result,'estado':'RECOGIDA_NO_VALIDA'}
    balance=entrada.recogida.saldo_pendiente
    if (validation.estado_validacion!=EV.VALIDA or not balance.disponible
            or balance.estado_certificacion!=EC.CERTIFICADO or balance.valor<0
            or not all((balance.fuente,balance.ruta_origen,balance.referencia_evidencia))
            or balance.fecha_corte!=existe_vigente.fecha_corte):return result
    return {**result,'estado':'CONDICION_RECOGIDA_ACREDITADA_NO_COMPENSADA'}


def estado_scoring_unico(preselecta=None):
    """A provider score is not the institution's approved CrediHoy methodology."""
    observed=getattr(preselecta,'score',None)
    return {'estado':'METODOLOGIA_MATRIZ_UNICO_CONFIRMADA','scoreParaDecision':None,
        'datoProveedorDisponible':bool(observed.disponible) if observed is not None else None,
        'procedenciaObservada':observed.a_dict() if observed else None,
        'metodologiaImplementada':'MATRIZ_UNICO_Y_CAPACIDAD_NO_DECISORIA',
        'modeloNumericoCurubaAcreditado':False,
        'scoreNumericoAdicionalRequerido':False,
        'modoAprobacionOrdinaria':'AUTOMATICA','excepciones':['COLABORADOR','FAMILIAR_COLABORADOR'],
        'requisitos':['DATOS_COMPLETOS_Y_VIGENTES','CLASIFICACION_INSTITUCIONAL_ESTAMENTOS'],
        'responsable':'Riesgos','autorizaDecision':False}


def detalle_regla(row, inventario, version):
    estado = row['estado']
    fuentes = [{'campo': k, 'nombre': FUENTES[k][0], 'fuenteRequerida': FUENTES[k][1],
                'accion': FUENTES[k][2]} for k in row['datosFaltantes'] if k in FUENTES]
    accion = (POLITICAS.get(row['codigo'], 'Ratificar el tratamiento de esta regla en la politica vigente.')
        if estado in ('PENDIENTE_POLITICA', 'INACTIVA', 'NO_APLICA') else
        ' '.join(f['accion'] for f in fuentes) if fuentes else
        'Revisar el hecho y su evidencia en la evaluacion conservada.' if estado == 'NO_EVALUABLE' else
        'Aplicar el efecto autorizado de la regla.' if estado == 'NO_CUMPLE' else 'Sin datos adicionales reportados por esta regla.')
    if row['codigo'] in ('UNICO_FILA_12', 'UNICO_FILA_17') and estado == 'NO_EVALUABLE':
        accion += (' Criterio de la matriz: ' + inventario.observacion
            + '. El rango tecnico LINIX no sustituye este criterio ni acredita capacidad o cupo autorizado.')
    if row['codigo'] == 'UNICO_FILA_17' and estado == 'NO_CUMPLE':
        e = row.get('evidencia', {})
        accion = ('Monto solicitado COP ' + str(e.get('montoSolicitadoCOP', 'no informado'))
            + ' inferior al minimo de matriz COP ' + str(e.get('limiteCOP', 'ver evidencia'))
            + ' (20% del SMMLV). No permite radicacion ordinaria positiva con estas condiciones'
            + ' y sin excepcion institucional documentada. No modificar automaticamente el monto.')
    if row['codigo'] == 'UNICO_FILA_12' and estado == 'CUMPLE':
        accion = 'Cumple solo el techo de 40 SMMLV; no acredita capacidad, aportes ni cupo aprobado. El limite tecnico LINIX se valida por separado.'
    if row['codigo'] == 'UNICO_FILA_9' and estado == 'NO_CUMPLE':
        from .normativa import MENSAJE_ACTUALIZACION
        accion = MENSAJE_ACTUALIZACION
    if row['codigo'] == 'UNICO_FILA_31' and estado == 'NO_APLICA':
        accion = 'GER-PO.068 6.2.3.7: excepcion de mora exclusivamente para renovacion; solicitud declarada sin recogida.'
    if row['codigo']=='UNICO_FILA_2' and isinstance(row.get('evidencia'),dict):
        preliminary=row['evidencia'].get('capacidadPreliminar')
        if preliminary and preliminary['capacidad']['disponible']:
            labels={'SALDO_CERO_Y_CUOTA_POSITIVA_SIN_APLICABILIDAD_CERTIFICADA':'saldo cero y cuota positiva',
                'TABLA_7_NO_CERTIFICA_PERIODICIDAD_DE_TARJETA':'tarjeta sin periodicidad',
                'PERIODICIDAD_NO_MENSUAL_TABLA_7':'periodicidad no mensual'}
            accion=('Capacidad preliminar COP '+preliminary['capacidad']['valor']+'. Ingreso LINIX COP '
                +str(preliminary['ingresoComputable']['valor'])+'; subtotal HDC COP '
                +str(preliminary['subtotalMensualHdcReconocido']['valor'])+'. No autoriza aprobacion. '
                +('Obligaciones HDC conciliadas; criterio pendiente de validacion institucional.'
                    if partidas_hdc_conciliadas(preliminary) else 'Deuda incompleta: '
                    +'; '.join(str(p['cuota_cop'])+' COP: '+labels.get(p['motivo'],'ver reserva HDC')
                        for p in (preliminary.get('reservas') or [])[:4])))
    return {**row, **({'propuestaRiesgos':propuesta_fila32()} if row['codigo']=='UNICO_FILA_32' else {}),
        'fuente': {'evaluacion': 'RESULTADO_PERSISTIDO', 'versionMatriz': version,
        'referenciaDocumental': inventario.referencia_original,
        'limitacionDocumental': inventario.fuente_original}, 'accionPendiente': accion,
        'fuentesPendientes': fuentes}
