"""Additive financial block in the existing internal facts contract."""
from decimal import Decimal, localcontext
from .hechos import HechosFinancierosV2
from .internos_http import (objeto, adaptar_hecho_http, decimal_exacto, texto, booleano,
                           entero, fecha_registrada, respuesta_invalida, lista)


def adaptar_financieros(raw, *, consulta):
    p = objeto(raw, {'version','cliente','personaNatural','naturalezaRegistrada',
        'ingresoMensualRegistrado','actualizacionCliente','actualizacionTercero',
        'moraMensualPorObligacion','historialMora','promedioMora','aportes'} |
        ({'estadosObligaciones'} if isinstance(raw,dict) and 'estadosObligaciones' in raw else set()))
    if p['version'] != 'credihoy-financieros-v1':respuesta_invalida()
    def h(raw, parser):return adaptar_hecho_http(raw, parser, consulta=consulta)
    c = objeto(p['cliente'], {'sueldoBasicoMensual','otrosIngresosMensuales',
        'otrosEgresosMensuales','otrosDescuentosNomina','calificacionCliente'})
    updates = []
    for name in ('actualizacionCliente','actualizacionTercero'):
        item=objeto(p[name], {'fecha','diasTranscurridos','mesesCalendario','politicaAplicada','discrepancia'})
        if item['politicaAplicada'] is not None:respuesta_invalida()
        texto(item['discrepancia'])
        updates.append((h(item['fecha'],fecha_registrada),h(item['diasTranscurridos'],entero),h(item['mesesCalendario'],entero)))
    # Preserve these raw histories in the encrypted HTTP evidence, never turn them into a current average.
    for row in lista(p['moraMensualPorObligacion']):
        row=objeto(row,{'tipo','numero','anio','meses','fuente','fechaCorte','observacion'})
        entero(row['anio']);texto(row['fuente']);fecha_registrada(row['fechaCorte'])
        for month in lista(row['meses']):
            month=objeto(month,{'mes','dias'})
            if not 1<=entero(month['mes'])<=12:respuesta_invalida()
            if month['dias'] is not None:decimal_exacto(str(month['dias']))
    for row in lista(p['historialMora']):
        row=objeto(row,{'tipo','numero','fecha','comportamiento','valorMora','dias','fuente'})
        fecha_registrada(row['fecha']);texto(row['fuente'])
        if row['valorMora'] is not None:decimal_exacto(row['valorMora'])
        if row['dias'] is not None:decimal_exacto(str(row['dias']))
    h(p['promedioMora'],decimal_exacto)
    a=objeto(p['aportes'],{'documentos','porcentaje','saldoAportesElegible','saldoAportesUtilizable'})
    if a['porcentaje']!='0.90':respuesta_invalida()
    for d in lista(a['documentos']):
        d=objeto(d,{'tipo','numero','cancelacion','vigencia','saldoRegistrado','fuente','excluidoPorCancelacion'})
        if d['tipo']!='203' or booleano(d['excluidoPorCancelacion'])!=(d['cancelacion']=='Y'):respuesta_invalida()
        if d['saldoRegistrado'] is not None:decimal_exacto(d['saldoRegistrado'])
    base,limit=h(a['saldoAportesElegible'],decimal_exacto),h(a['saldoAportesUtilizable'],decimal_exacto)
    if base.disponible!=limit.disponible:respuesta_invalida()
    if base.disponible:
        with localcontext() as ctx:
            ctx.prec=max(50,len(base.valor.as_tuple().digits)+abs(base.valor.as_tuple().exponent)+8)
            if base.valor<0 or limit.valor!=base.valor*Decimal('0.90'):respuesta_invalida()
    values={key:h(c[name],decimal_exacto) for key,name in (
        ('sueldo_basico_mensual','sueldoBasicoMensual'),('otros_ingresos_mensuales','otrosIngresosMensuales'),
        ('otros_egresos_mensuales','otrosEgresosMensuales'),('otros_descuentos_nomina','otrosDescuentosNomina'))}
    income=h(p['ingresoMensualRegistrado'],decimal_exacto)
    if income.disponible:
        components=[values[k] for k in ('sueldo_basico_mensual','otros_ingresos_mensuales')]
        if not all(v.disponible for v in components):respuesta_invalida()
        with localcontext() as ctx:
            ctx.prec=max(50,sum(len(v.valor.as_tuple().digits)+abs(v.valor.as_tuple().exponent) for v in components)+4)
            if income.valor!=sum((v.valor for v in components),Decimal(0)):respuesta_invalida()
    lifecycle=[]
    allowed={'ANULADA','RECHAZADA','CANCELADA_DEFINITIVA','PRECANCELADA',
        'SOLICITUD_EN_ESTUDIO','APROBADA_NO_LIQUIDADA','OBLIGACION_VIGENTE_REGISTRADA'}
    for row in lista(p.get('estadosObligaciones',[])):
        row=objeto(row,{'solicitud','modalidad','esCredihoy111','estado'})
        identity=objeto(row['solicitud'],{'tipo','numero'})
        texto(identity['tipo']);texto(identity['numero'])
        if booleano(row['esCredihoy111'])!=(row['modalidad']=='111'):respuesta_invalida()
        status=h(row['estado'],texto)
        if status.disponible and status.valor not in allowed:respuesta_invalida()
        lifecycle.append(status)
    return HechosFinancierosV2(**values,ingreso_mensual_registrado=income,
        calificacion_cliente=h(c['calificacionCliente'],texto),persona_natural=h(p['personaNatural'],booleano),
        naturaleza_registrada=h(p['naturalezaRegistrada'],texto),
        fecha_actualizacion_cliente=updates[0][0],fecha_actualizacion_tercero=updates[1][0],
        dias_actualizacion_cliente=updates[0][1],meses_calendario_cliente=updates[0][2],
        saldo_aportes_elegible=base,limite_aportes_90=limit,estados_obligaciones=tuple(lifecycle))
