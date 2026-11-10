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
    'UNICO_FILA_9': 'Definir 30 dias transcurridos o mes calendario, su corte y limites; requiere fecha real de actualizacion, no afiliacion.',
    'UNICO_FILA_31': 'Definir aplicabilidad a un credito nuevo frente a renovacion, identificacion del credito y calculo del promedio. No excluir por no recoger sin politica expresa.',
    'UNICO_FILA_32': 'Definir si el saldo de la misma modalidad exige recogida, revision o solo informacion. No recoger otros productos ni aprobar por ausencia de saldo.',
    'UNICO_FILA_2': 'Ratificar el umbral y efecto de capacidad con todos los operandos acreditados.',
}


def matriz_documental():
    raw = json.loads(Path(__file__).with_name('reglas_excel.json').read_text(encoding='utf-8'))
    return {'version': 'EXCEL_UNICO_' + raw['sha256'][:8], 'sha256': raw['sha256'],
        'archivo': raw['archivo'], 'autorizacionEfectosAcreditada': False}


def propuesta_fila32():
    """Proposal for Risks, never an approved rule or an evaluation override."""
    raw=json.loads(Path(__file__).with_name('reglas_excel.json').read_text(encoding='utf-8'))
    row=next(r for r in raw['reglas_unico'] if r['fila']==32)
    return {'version':'CREDIHOY_UNICO_PROPUESTA_FILA32_20261020_V1',
        'estadoVigente':'PENDIENTE_POLITICA','aprobada':False,'responsable':'Riesgos',
        'fuente':{'archivo':raw['archivo'],'sha256':raw['sha256'],'hoja':raw['hoja'],'celda':'D32',
            'texto':row['celdas_originales']['D'],'advertencia':row['celdas_originales']['G']},
        'escenarios':[
            {'caso':'SIN_RECOGIDA_SIN_CREDIHOY_VIGENTE','propuesta':'NO_APLICA_CON_EVIDENCIA_DE_AUSENCIA',
             'requiere':'Aprobar aplicabilidad y cobertura/corte del hecho de inexistencia.'},
            {'caso':'SIN_RECOGIDA_CON_CREDIHOY_VIGENTE','propuesta':'REVISION_MANUAL_SIN_RECHAZO_AUTOMATICO',
             'requiere':'Definir efecto del saldo de modalidad 111; no excluir por productos distintos.'},
            {'caso':'RECOGIDA_CREDIHOY_MISMA_MODALIDAD','propuesta':'VERIFICAR_OBLIGACION_SALDO_Y_TITULAR',
             'requiere':'Definir comparacion, corte y efecto; solo CrediHoy 111, sin imponer recogida ajena.'},
            {'caso':'HECHO_O_COBERTURA_INSUFICIENTE','propuesta':'NO_EVALUABLE',
             'requiere':'Hecho institucional del mismo titular y corte, no inferir cero.'}],
        'gate':'REFERENCIA_VERSION_VIGENCIA_Y_EFECTOS_APROBADOS_POR_RIESGOS',
        'autorizaDecision':False,'modificaEvaluacion':False}


def estado_scoring_unico(preselecta=None):
    """A provider score is not the institution's approved CrediHoy methodology."""
    observed=getattr(preselecta,'score',None)
    return {'estado':'PENDIENTE_METODOLOGIA_UNICO','scoreParaDecision':None,
        'datoProveedorDisponible':bool(observed.disponible) if observed is not None else None,
        'procedenciaObservada':observed.a_dict() if observed else None,
        'requisitos':['FUENTE_Y_MODELO_UNICO','VERSION_Y_VIGENCIA','ESCALA_Y_UMBRALES',
            'TRATAMIENTO_NULOS_EXCEPCIONES_Y_EFECTO_APROBADO'],
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
