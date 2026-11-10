"""Local preparation of confirmed 390/111/CFA rules; no provider or core calls."""
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

from ..base import Hecho, EstadoCertificacion as EC, instante
from .normativa import limite_monetario
import json
from pathlib import Path

VERSION = 'CREDIHOY_UNICO_DEFINICIONES_RESPONSABLE_20261026_V1'
PLAZO_MINIMO, PLAZO_MAXIMO = 1, 48
DIA_MINIMO, DIA_MAXIMO = 1, 20
CALENDARIO_VERSION = 'CREDIHOY_DIA_PAGO_30_CALENDARIO_20261028_V2'
CALENDARIO_FUENTE = 'DECISION_RESPONSABLE_9a03493a-016a-45b1-a636-e8d1e8445718'


def politica_digital():
    return json.loads(Path(__file__).with_name('politica_digital.json').read_text(encoding='utf-8'))


def plazos_por_monto(monto, *, fecha, configuracion=None):
    """Exact cross products, not a rounded quotient for range assignment."""
    from decimal import localcontext
    from .normativa import smmlv_al_corte
    from ..aritmetica import precision_exacta
    config = politica_digital() if configuracion is None else configuracion
    h = smmlv_al_corte(fecha, modalidad='111')
    if type(monto) is not Decimal or not monto.is_finite() or monto <= 0 or not h.disponible:
        raise ValueError('MONTO_O_SMMLV_NO_ACREDITADO')
    matches=[]
    for r in config['rangosSMMLV']:
        lower,upper=Decimal(r['desde']),Decimal(r['hasta'])
        if (not lower.is_finite() or not upper.is_finite() or not 0<lower<upper
                or type(r['plazoMaximo']) is not int or not 1<=r['plazoMaximo']<=48
                or type(r['incluyeDesde']) is not bool or type(r['incluyeHasta']) is not bool):
            raise ValueError('RANGOS_PLAZO_INVALIDOS')
        with localcontext() as ctx:
            ctx.prec=precision_exacta(monto,h.valor,lower,upper)
            low,high=h.valor*lower,h.valor*upper
            if (monto>=low if r['incluyeDesde'] else monto>low) and (monto<=high if r['incluyeHasta'] else monto<high):
                matches.append(r['plazoMaximo'])
    return {'version':config['version'],'smmlv':h.a_dict(),'rangosSMMLV':config['rangosSMMLV'],
        'plazoMinimo':config['plazoMinimo'],'plazoMaximo':min(matches) if matches else None,
        'estado':'SUPERPOSICION_MENOR_PLAZO_COMUN' if len(matches)>1 else 'RANGO_DEFINIDO' if matches else 'SIN_RANGO_NO_AUTORIZADO',
        'precisionNormativaPendiente':len(matches)!=1,'montoCOP':format(monto,'f'),
        'relacionExacta':{'numeradorCOP':format(monto,'f'),'denominadorSMMLV':format(h.valor,'f')}}


def exigir_plazo_por_monto(monto, plazo, *, fecha):
    result=plazos_por_monto(monto,fecha=fecha)
    if type(plazo) is not int or result['plazoMaximo'] is None or not result['plazoMinimo']<=plazo<=result['plazoMaximo']:
        raise ValueError('PLAZO_NO_AUTORIZADO_PARA_MONTO_SMMLV')
    return result


def convertir_namv_a_ea(tasa_porcentaje):
    """Rate-unit conversion only; not a quota, insurance or local amortizer."""
    from decimal import localcontext
    if type(tasa_porcentaje) is not Decimal or not tasa_porcentaje.is_finite() or tasa_porcentaje<0:
        raise ValueError('TASA_NAMV_INVALIDA')
    with localcontext() as ctx:
        ctx.prec=50
        ea=((Decimal(1)+tasa_porcentaje/Decimal(1200))**12-Decimal(1))*Decimal(100)
    return {'tasaNamvPorcentaje':format(tasa_porcentaje,'f'),'tasaEaPorcentaje':format(ea,'f'),
        'precisionDecimal':50,'redondeoContractualAplicado':False,'esOfertaContractual':False}


def limite_por_aportes(hecho, *, fecha, limite_preaplicado=False):
    """Only current, evidenced and reconciled funds; never infer availability."""
    instante(fecha, 'fecha')
    if type(limite_preaplicado) is not bool:
        raise ValueError('INDICADOR_LIMITE_INVALIDO')
    corte = fecha.astimezone(ZoneInfo('America/Bogota')).date()
    day = hecho.fecha_corte if isinstance(hecho, Hecho) else None
    if isinstance(day, datetime):
        day = day.astimezone(ZoneInfo('America/Bogota')).date()
    if (not isinstance(hecho, Hecho) or not hecho.disponible
            or hecho.estado_certificacion != EC.CERTIFICADO
            or not hecho.fuente or not hecho.ruta_origen or not hecho.referencia_evidencia
            or day != corte or hecho.fecha_consulta is None or hecho.fecha_consulta > fecha
            or type(hecho.valor) is not Decimal or not hecho.valor.is_finite() or hecho.valor < 0):
        raise ValueError('APORTES_UTILIZABLES_Y_CORTE_NO_ACREDITADOS')
    limit = hecho.valor if limite_preaplicado else limite_monetario(hecho.valor, 14)
    # Monetary request bounds round down, never above the exact 90% ceiling.
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec = max(38, len(limit.as_tuple().digits) + 8)
        maximum = limit.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    return {'version': VERSION, 'montoMaximo': format(maximum, '.2f'),
        'limiteExacto': format(limit, 'f'), 'porcentajeAportes': '90',
        'fuente': hecho.fuente, 'rutaOrigen': hecho.ruta_origen,
        'referenciaEvidencia': hecho.referencia_evidencia, 'fechaCorte': day.isoformat(),
        'fechaConsulta': hecho.fecha_consulta.isoformat(), 'sujetoACapacidad': True,
        'esCupoAprobado': False, 'limiteYaAplicado': limite_preaplicado}


def limite_desde_evaluacion(resultado, *, fecha):
    """Reuse the persisted row-14 operand; ignore browser-provided balances."""
    try:
        rows = [r for r in resultado['reglas'] if r['codigo'] == 'UNICO_FILA_14']
        if len(rows) != 1:
            raise ValueError
        evidence = rows[0]['evidencia']; raw = evidence['hecho']
        preapplied = evidence['operando'] == 'limite_aportes_90'
        if evidence['operando'] not in {'limite_aportes_90', 'saldo_aportes_utilizable'}:
            raise ValueError
        raw_day = raw['fecha_corte']
        day = datetime.fromisoformat(raw_day) if 'T' in raw_day else date.fromisoformat(raw_day)
        fact = Hecho(valor=Decimal(raw['valor']), disponible=raw['disponible'],
            fuente=raw['fuente'], referencia_evidencia=raw['referencia_evidencia'],
            ruta_origen=raw['ruta_origen'], fecha_corte=day,
            fecha_consulta=datetime.fromisoformat(raw['fecha_consulta']),
            estado_certificacion=EC(raw['estado_certificacion']))
        limit = limite_por_aportes(fact, fecha=fecha, limite_preaplicado=preapplied)
        if evidence['unidad'] != 'COP' or Decimal(evidence['limiteCOP']) != Decimal(limit['limiteExacto']):
            raise ValueError
        return limit
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise ValueError('APORTES_UTILIZABLES_Y_CORTE_NO_ACREDITADOS') from None


def parametros_financieros(*, seguro_mensual_porcentaje=Decimal('0.090')):
    if (type(seguro_mensual_porcentaje) is not Decimal or not seguro_mensual_porcentaje.is_finite()
            or not Decimal(0) <= seguro_mensual_porcentaje <= Decimal(100)):
        raise ValueError('SEGURO_MENSUAL_INVALIDO')
    return {'version': VERSION, 'perfil': ['390', '111', 'CFA'], 'plazoMinimo': 1, 'plazoMaximo': 48,
        'seguroVidaPorcentajeMensual': format(seguro_mensual_porcentaje, 'f'),
        'seguroBaseMonetaria': None, 'seguroRedondeo': None, 'tasaComercial': 'PARAMETRIZACION_EXISTENTE',
        'interesesIniciales': 'DESCUENTO_EN_DESEMBOLSO', 'coexistencia111Permitida': False,
        'evaluacionCuruba': 'MATRIZ_UNICO_SIN_SCORE_ADICIONAL', 'modoAprobacion': 'AUTOMATICA_ORDINARIA_CON_EXCEPCIONES_ESTAMENTOS',
        'autorizaDecision': False}


def preparar_calendario(fecha_desembolso, dia_pago, *, medio_pago, primer_vencimiento=None,
        tipo_base='PROYECTADO', referencia_base=None):
    """Monthly date rule only. No amortization, monetary interest or native task."""
    if (type(fecha_desembolso) is not date or type(dia_pago) is not int or not 1 <= dia_pago <= 20
            or medio_pago not in {'VENTANILLA', 'NOMINA'} or tipo_base not in {'PROYECTADO','EFECTIVO'}):
        raise ValueError('CALENDARIO_ENTRADA_INVALIDA')
    if primer_vencimiento is not None and (type(primer_vencimiento) is not date
            or primer_vencimiento <= fecha_desembolso):
        raise ValueError('PRIMER_VENCIMIENTO_INVALIDO')
    def siguiente(day):
        return date(day.year+day.month//12,day.month%12+1,dia_pago)
    boundary=fecha_desembolso.replace(day=dia_pago)
    if boundary<=fecha_desembolso:boundary=siguiente(boundary)
    first=boundary
    while first<fecha_desembolso+timedelta(days=30):first=siguiente(first)
    return {'version': CALENDARIO_VERSION, 'fuenteRegla': CALENDARIO_FUENTE,
        'fechaBaseDesembolso': fecha_desembolso.isoformat(), 'tipoBase': tipo_base,
        'referenciaBase': referencia_base, 'diaSeleccionado': dia_pago,
        'medioPago': medio_pago, 'diaReferencia': dia_pago, 'particularidad': None,
        'primerVencimientoProyectado': first.isoformat(),
        'primerVencimientoInformado': primer_vencimiento.isoformat() if primer_vencimiento else None,
        'coincideFechaNativa': primer_vencimiento==first if primer_vencimiento else None,
        'interesesAnticipados': {'desde':fecha_desembolso.isoformat(),'hasta':boundary.isoformat(),
            'importe':None,'liquidacion':'LINIX','descuentoEnDesembolso':True},
        'diasHastaPrimeraCuota': (first-fecha_desembolso).days,
        'calendarioContractualAcreditado': False, 'mesInferido': False,
        'interesesIniciales': 'DESCUENTO_EN_DESEMBOLSO', 'autorizaDesembolso': False}


def revalidar_calendario(aceptado, fecha_efectiva, *, primer_vencimiento_nativo=None):
    """Recompute on the real base; never carry consent onto altered conditions."""
    previous=preparar_calendario(date.fromisoformat(aceptado['fechaBaseDesembolso']),
        aceptado['diaSeleccionado'],medio_pago=aceptado['medioPago'],tipo_base=aceptado['tipoBase'],
        referencia_base=aceptado['referenciaBase'])
    if any(aceptado.get(k)!=v for k,v in previous.items()
            if k not in {'primerVencimientoInformado','coincideFechaNativa'}):
        raise ValueError('CALENDARIO_ACEPTADO_ALTERADO')
    current=preparar_calendario(fecha_efectiva,aceptado['diaSeleccionado'],
        medio_pago=aceptado['medioPago'],tipo_base='EFECTIVO',
        primer_vencimiento=primer_vencimiento_nativo)
    changed=any(previous[k]!=current[k] for k in ('primerVencimientoProyectado','interesesAnticipados'))
    native_ok=current['coincideFechaNativa'] is True
    return {'version':CALENDARIO_VERSION,'anterior':aceptado,'actual':current,
        'condicionesMaterialmenteDistintas':changed,'requiereNuevaAceptacion':changed,
        'fechaNativaConciliada':native_ok,'continuidadPermitida':not changed and native_ok,
        'autorizaDesembolso':False}


def conciliar_composicion_contractual(payload, cotizacion, evidencia, *, titular_referencia,
        ahora, seguro_mensual_porcentaje=Decimal('0.090')):
    """Internal LINIX evidence reconciliation, NOT a calculator or provider DTO.

    Only an authenticated institutional producer may supply this evidence. No
    endpoint accepts it from the browser. Partial scalar output alone cannot pass.
    """
    from copy import deepcopy
    from decimal import localcontext
    from hashlib import sha256
    import re
    from ..cotizacion_cliente import validar_respuesta
    from ..aritmetica import precision_exacta
    instante(ahora,'ahora')
    def amount(v):
        if type(v) is not str or not re.fullmatch(r'[0-9]{1,38}(\.[0-9]{1,38})?',v):
            raise ValueError('IMPORTE_COMPOSICION_NO_ACREDITADO')
        return Decimal(v)
    try:
        q=validar_respuesta(cotizacion,payload,cotizacion['requestId'])
        c=payload['condiciones'];e=evidencia
        params=parametros_financieros(seguro_mensual_porcentaje=seguro_mensual_porcentaje)
        amount(e['tasaEaPorcentaje'])
        observed=datetime.fromisoformat(e['fechaConsulta'])
        cutoff=ahora.astimezone(ZoneInfo('America/Bogota')).date()
        if (not q['disponible'] or q['fuente']!='LINIX_CALCULO_CUOTA'
                or type(e) is not dict or e['fuente']!='LINIX' or e['verificado'] is not True
                or not e['referencia'] or e['solicitudId']!=payload['solicitudId']
                or e['versionCondiciones']!=payload['versionCondiciones']
                or e['requestId']!=q['requestId'] or not re.fullmatch('[a-f0-9]{64}',titular_referencia)
                or e['titularReferencia']!=titular_referencia
                or observed.utcoffset() is None or not 0<=(ahora-observed).total_seconds()<=300
                or date.fromisoformat(e['fechaCorte'])!=cutoff
                or e['otrosConceptosCompletos'] is not True or not e['referenciaRedondeo']):
            raise ValueError()
        exigir_plazo_por_monto(amount(c['monto']),c['numeroCuotas'],fecha=ahora)
        seguro=e['seguro']
        if (seguro['incluidoEnCuota'] is not True or not seguro['baseReferencia']
                or amount(seguro['porcentajeMensual'])!=Decimal(params['seguroVidaPorcentajeMensual'])):
            raise ValueError()
        rows=e['calendario'];initial=e['interesesIniciales']
        start=date.fromisoformat(e['fechaDesembolso'])
        if (type(rows) is not list or len(rows)!=c['numeroCuotas'] or not rows
                or start>cutoff or initial['descuentoEnDesembolso'] is not True
                or not initial['referencia']):raise ValueError()
        calendario=preparar_calendario(start,e['diaPago'],medio_pago=e['medioPago'],
            primer_vencimiento=date.fromisoformat(rows[0]['fecha']),tipo_base='EFECTIVO',referencia_base=e['referencia'])
        if not calendario['coincideFechaNativa']:raise ValueError()
        initial_amount=amount(initial['valor'])
        if (initial['desde']!=calendario['interesesAnticipados']['desde']
                or initial['hasta']!=calendario['interesesAnticipados']['hasta']):
            raise ValueError()
        numbers=[amount(v[k]) for v in rows for k in ('capital','IC','IV','SV','otros','total')]
        with localcontext() as ctx:
            ctx.prec=precision_exacta(*numbers,amount(c['monto']))
            previous=start;capital=Decimal(0)
            for n,row in enumerate(rows,1):
                day=date.fromisoformat(row['fecha'])
                if (type(row['numero']) is not int or row['numero']!=n or day<=previous
                        or sum((amount(row[k]) for k in ('capital','IC','IV','SV','otros')),Decimal(0))!=amount(row['total'])
                        or amount(row['total'])<=0
                        or (n<len(rows) and amount(row['total'])!=amount(q['cuotaInstitucional']))):
                    raise ValueError()
                previous=day;capital+=amount(row['capital'])
            if capital!=amount(c['monto']):raise ValueError()
        body={'versionContrato':'credihoy-composicion-conciliada-v1',
            'solicitudId':payload['solicitudId'],'versionCondiciones':payload['versionCondiciones'],
            'titularReferencia':titular_referencia,'requestId':q['requestId'],
            'fuente':'LINIX','referencia':e['referencia'],'fechaConsulta':e['fechaConsulta'],
            'cuotaInstitucional':q['cuotaInstitucional'],'condiciones':deepcopy(c),
            'tasaEaPorcentaje':e['tasaEaPorcentaje'],
            'calendario':deepcopy(rows),'seguro':deepcopy(seguro),
            'interesesIniciales':deepcopy(initial),'referenciaRedondeo':e['referenciaRedondeo'],
            'preparacionCalendario':calendario,'composicionConciliada':True,
            'primaAdicionalCalculada':False,'redondeoLocalAplicado':False,
            'interesesInicialesDescuento':format(initial_amount,'f'),
            'aceptacionTitular':False,'autorizaDecision':False,'autorizaDesembolso':False}
        body['sha256']=sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return body
    except (KeyError,TypeError,ValueError,AttributeError,ArithmeticError):
        raise ValueError('COMPOSICION_CONTRACTUAL_NO_CONCILIA') from None
