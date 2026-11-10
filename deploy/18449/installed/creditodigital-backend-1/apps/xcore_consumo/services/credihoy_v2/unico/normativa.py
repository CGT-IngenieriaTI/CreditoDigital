"""Parametro legal versionado; sin red, auxilio de transporte ni autorizacion crediticia."""
from dataclasses import replace
from datetime import date, datetime
from calendar import monthrange
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from ..base import EstadoCertificacion as EC, Hecho, instante
from ..aritmetica import precision_exacta

POLITICA_ACTUALIZACION = 'CREDIHOY_UNICO_MES_CALENDARIO_INCLUSIVO_20261019_V1'
MENSAJE_ACTUALIZACION = 'Debes actualizar tus datos para continuar con tu solicitud.'
POLITICA_FINANCIERA_CONGENTE = 'CREDIHOY_UNICO_CONGENTE_AP014_SUBTOTAL_HDC_PRELIMINAR_20261020_V1'


def ingreso_computable_congente(financieros):
    """Institutional adaptation: only recorded monthly salary and variable income."""
    missing=Hecho(fuente='LINIX.AP014MCLIENTE',
        referencia_evidencia=POLITICA_FINANCIERA_CONGENTE,
        ruta_origen='adaptacionCongente.ingresoComputableAp014',
        observacion='V_SUELDO + V_INGVAR; ambos componentes acreditados, sin ingresos externos')
    if financieros is None:return missing
    salary,variable=financieros.sueldo_basico_mensual,financieros.otros_ingresos_mensuales
    for h,source in ((salary,'AP014MCLIENTE.V_SUELDO'),(variable,'AP014MCLIENTE.V_INGVAR')):
        if (not h.disponible or h.estado_certificacion not in {EC.OBSERVADO,EC.CERTIFICADO}
                or h.fuente!=source or not h.ruta_origen or not h.referencia_evidencia
                or not isinstance(h.valor,Decimal) or not h.valor.is_finite() or h.valor<0):
            return missing
    if (salary.fecha_corte is None or salary.fecha_consulta is None
            or salary.fecha_corte!=variable.fecha_corte or salary.fecha_consulta!=variable.fecha_consulta):
        return missing
    with localcontext() as context:
        context.prec=precision_exacta(salary.valor,variable.valor)
        total=salary.valor+variable.valor
    return replace(missing,valor=total,disponible=True,estado_certificacion=EC.OBSERVADO,
        fecha_corte=salary.fecha_corte,fecha_consulta=salary.fecha_consulta,
        observacion='ADAPTACION_INSTITUCIONAL_CONGENTE; V_SUELDO + V_INGVAR; '
            'sin V_DTONOM adicional; no renueva fecha LINIX; componentes: '
            +salary.referencia_evidencia+';'+variable.referencia_evidencia)


def actualizacion_al_corte(hecho, fecha):
    """One calendar month inclusive; clamp dates only at the end of the next month."""
    if fecha is None or not hecho.disponible or hecho.estado_certificacion not in {EC.CERTIFICADO, EC.OBSERVADO}:
        return None
    if not hecho.fuente or not hecho.ruta_origen:
        return None
    if isinstance(fecha, datetime):
        instante(fecha, 'fecha_evaluacion')
        fecha = fecha.astimezone(ZoneInfo('America/Bogota')).date()
    updated = hecho.valor
    if isinstance(updated, datetime):
        updated = updated.astimezone(ZoneInfo('America/Bogota')).date()
    if not isinstance(updated, date) or not isinstance(fecha, date) or updated > fecha:
        return None
    year, month = (updated.year+1, 1) if updated.month == 12 else (updated.year, updated.month+1)
    deadline = date(year, month, min(updated.day, monthrange(year, month)[1]))
    return {'cumple': fecha <= deadline, 'fechaActualizacion': updated.isoformat(),
        'fechaEvaluacion': fecha.isoformat(), 'limiteInclusive': deadline.isoformat(),
        'versionPolitica': POLITICA_ACTUALIZACION, 'fuente': hecho.fuente,
        'referenciaEvidencia': hecho.referencia_evidencia,
        'mensaje': None if fecha <= deadline else MENSAJE_ACTUALIZACION}


def factor_monetario(fila):
    return {12: Decimal('40'), 14: Decimal('0.90'), 17: Decimal('0.20')}[fila]


def limite_monetario(valor, fila):
    factor = factor_monetario(fila)
    with localcontext() as ctx:
        ctx.prec = precision_exacta(valor, factor)
        return valor * factor


def smmlv_al_corte(fecha, *, modalidad, documento=None):
    instante(fecha, 'fecha_evaluacion')
    corte = fecha.astimezone(ZoneInfo('America/Bogota')).date()
    raw = Path(__file__).with_name('smmlv.json').read_bytes() if documento is None else documento
    p = json.loads(raw)
    if (modalidad != p['perfil'][1] or not date.fromisoformat(p['desde']) <= corte
            < date.fromisoformat(p['hastaExclusivo'])):
        return Hecho(fuente='NORMATIVA_SMMLV', observacion='CORTE_O_MODALIDAD_SIN_PARAMETRO_VERIFICADO')
    if p['unidad'] != 'COP' or p['incluyeAuxilioTransporte'] is not False:
        raise ValueError('UNIDAD_SMMLV_INVALIDA')
    valor = Decimal(p['valor'])
    if not valor.is_finite() or valor <= 0:
        raise ValueError('SMMLV_INVALIDO')
    return Hecho(valor=valor, disponible=True, fuente=p['fuente'],
        referencia_evidencia=p['version'] + ':sha256:' + hashlib.sha256(raw).hexdigest(),
        ruta_origen='Decreto1469/2025.Art1;Decreto159/2026.Art1;controlJudicial20260717',
        fecha_corte=corte, estado_certificacion=EC.CERTIFICADO,
        observacion='COP SIN AUXILIO DE TRANSPORTE; verificadoAl=' + p['verificadoAl']
            + '; ' + p['controlJudicial'])


def incorporar_smmlv(hechos, *, fecha, modalidad, documento=None):
    """No sustituye hechos conflictivos ni completa hechos personales del titular."""
    legal = smmlv_al_corte(fecha, modalidad=modalidad, documento=documento)
    actual = hechos.smmlv_vigente
    if not legal.disponible:
        return hechos
    if actual.disponible and actual.valor != legal.valor:
        return replace(hechos, smmlv_vigente=Hecho(fuente='NORMATIVA_SMMLV',
            estado_certificacion=EC.AMBIGUO, observacion='SMMLV_CONFLICTIVO_NO_SUSTITUIR'))
    return replace(hechos, smmlv_vigente=legal)


def evidencia_monetaria(entrada, hechos):
    """Expone operandos de la misma comparacion, no cupo aprobado ni otro motor."""
    from .reglas import _certificado
    rows = {}
    for fila, nombre, operador in (
            (12, 'smmlv_vigente', '<='),
            (14, 'saldo_aportes_utilizable', '<='),
            (17, 'smmlv_vigente', '>=')):
        h = getattr(hechos, nombre)
        limite_preaplicado = fila == 14 and hechos.limite_aportes_90.disponible
        if limite_preaplicado:
            nombre = 'limite_aportes_90'
            h = hechos.limite_aportes_90
        factor = str(factor_monetario(fila))
        if limite_preaplicado:
            factor = '1'
        limite = ((h.valor if limite_preaplicado else limite_monetario(h.valor, fila)) if _certificado(h)
            and h.valor >= 0 and (fila == 14 or h.valor > 0) else None)
        rows[f'UNICO_FILA_{fila}'] = {'montoSolicitadoCOP': str(entrada.monto_solicitado),
            'operando': nombre, 'hecho': h.a_dict(), 'factor': factor,
            'limiteCOP': str(limite) if limite is not None else None,
            'formula': 'monto_solicitado ' + operador + ' ' + factor + ' * ' + nombre,
            'unidad': 'COP', 'esCupoAutorizado': False,
            'versionMatriz': 'EXCEL_UNICO_a0339ce8',
            'alcance': ('LIMITE_MATRIZ_NO_SUSTITUYE_CAPACIDAD_APORTES_NI_LIMITE_TECNICO'
                if fila == 12 else 'COMPARACION_DOCUMENTAL_SIN_DECISION_CREDITICIA')}
    return rows
