"""Reconcile the existing HDC response; no provider calls or LINIX installments."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from hashlib import sha256
import json

from apps.integraciones.risk_gateway.contratos import HDCNormalizado
from ..base import DTO, EstadoCertificacion as EC, Hecho
from ..aritmetica import precision_exacta


# DHWS3 v1.9, table 4 pp27-29: only Estados/EstadoPago for credit/card.
OPEN = frozenset({'01', '45', '47', '60'} | {str(i) for i in range(13, 42)})
CLOSED = frozenset({'02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12', '46', '49'})
CRITERIO_FUNCIONAL = 'CONGENTE_UNICO_HDC_ACLARACION_RESPONSABLE_20261020_V2'
CRITERIO_CUOTA_MES = 'CONGENTE_UNICO_CAB_CUOTAMES_COTEJADO_20261020_V1'


@dataclass(frozen=True, kw_only=True)
class PartidaCuotaHDC(DTO):
    referencia: str
    posicion: int
    tipo: str
    estado_pago: str | None
    periodicidad: str | None
    fecha_valor: date | None
    saldo_cop: Decimal | None
    cuota_cop: Decimal | None
    cuota_reconocida_cop: Decimal | None
    tratamiento: str
    motivo: str
    ruta_cuota_mes: str | None = None
    cuota_mes_original: str | None = None
    ruta_periodicidad_detalle: str | None = None
    fuente_escala: str | None = None
    xml_cotejado_sha256: str | None = None


@dataclass(frozen=True, kw_only=True)
class ConciliacionCuotasHDC(DTO):
    partidas: tuple[PartidaCuotaHDC, ...]
    subtotal_reconocido: Hecho[Decimal]
    cuotas_aplicables: Hecho[Decimal]
    cobertura_completa: bool
    criterio_funcional: str | None = None
    aprobacion_riesgos: bool | None = None
    partidas_resueltas: bool = False


def _code(value):
    return None if value is None else value['codigo_original']


def _money(value):
    if (value is None or not value['disponible'] or value['moneda_homologada'] not in {'COP', 'Moneda legal'}
            or value['unidad_original'] != 'COP' or value['estado_conversion'] != 'CONVERTIDO_MONEDA_LEGAL'
            or not value['referencia']):
        return None
    amount = value['valor_monetario']
    return amount if isinstance(amount, Decimal) and amount.is_finite() and amount >= 0 else None


def _cuota_mes_cab(evidence, xml):
    """Reconcile a unique CAB summary with its detailed record, not table 7."""
    if xml is None:return {}
    from lxml import etree
    if not isinstance(xml, bytes) or not 0 < len(xml) <= 16 * 1024 * 1024:
        raise ValueError('XML_CUSTODIADO_INVALIDO')
    root=etree.fromstring(xml,etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))
    if root.getroottree().docinfo.doctype:raise ValueError('XML_DTD_PROHIBIDO')
    reports=[root] if root.tag=='Informe' else root.findall('Informe') if root.tag=='Informes' else []
    if len(reports)!=1:return {}
    report=reports[0];result=evidence['resultado']
    if report.get('fechaConsulta')!=result['fecha_consulta']:return {}
    detailed=[n for n in report.findall('CuentaCartera') if n.find('Caracteristicas') is not None
        and n.find('Caracteristicas').get('tipoCuenta')=='CAB']
    rows=[o for o in result['obligaciones'] if o['tipo_estructural']=='CuentaCartera'
        and o['tipoCuenta']['abreviatura']=='CAB']
    summaries=report.findall('InfoAgregadaMicrocredito/Resumen/EndeudamientoActual/Sector/TipoCuenta[@tipoCuenta="CAB"]/Usuario/Cuenta')
    # No account number exists in this summary: accept only a one-to-one type match.
    if len(detailed)!=1 or len(rows)!=1 or len(summaries)!=1:return {}
    node,o,summary=detailed[0],rows[0],summaries[0]
    attrs=dict(o['datos_originales']['atributos'])
    identity=('numero','identificacion','codSuscriptor','entidad','sector','fechaApertura','fechaVencimiento')
    if (any(not attrs.get(k) or attrs.get(k)!=node.get(k) for k in identity)
            or node.get('numero')!=o['identificador']['numero']
            or node.get('identificacion')!=o['identificador']['identificacion']
            or node.get('entidad')!=o['entidad'] or node.get('codSuscriptor')!=o['codSuscriptor']
            or node.get('sector')!=_code(o['sector'])):return {}
    if (summary.getparent().getparent().getparent().get('codSector')!=node.get('sector')
            or summary.get('estadoActual')!='Al d\u00eda'
            or len(o['valores'])!=1 or len(node.findall('Valores/Valor'))!=1):return {}
    v=o['valores'][0];raw=node.find('Valores/Valor')
    if (raw.get('fecha')!=v['fecha'] or raw.get('periodicidad')!=_code(v['periodicidad'])
            or raw.get('moneda')!=_code(v['moneda'])):return {}
    # The aggregate is in thousands COP; prove its scale against three COP detail amounts.
    for field,aggregate in (('cuota','cuotaMes'),('saldoActual','saldoActual'),('valorInicial','valorInicial')):
        amount=_money(v[field])
        try:original=Decimal(summary.get(aggregate));detail=Decimal(raw.get(field))
        except (TypeError,ValueError,ArithmeticError):return {}
        if amount is None or not original.is_finite() or original < 0 or not detail.is_finite():return {}
        with localcontext() as ctx:
            ctx.prec=precision_exacta(original,detail,amount)+4
            if original * 1000 != amount or detail != amount:return {}
    path=root.getroottree().getpath
    return {o['posicion_original']:{'ruta_cuota_mes':path(summary)+'/@cuotaMes',
        'cuota_mes_original':summary.get('cuotaMes'),
        'ruta_periodicidad_detalle':path(raw)+'/@periodicidad',
        'fuente_escala':CRITERIO_CUOTA_MES+'; resumen MILES_COP, detalle COP; cuota/saldo/inicial cotejados',
        'xml_cotejado_sha256':sha256(xml).hexdigest()}}


def conciliar_cuotas_hdc(evidence: HDCNormalizado, *, criterio_funcional=False, informe_xml=None) -> ConciliacionCuotasHDC:
    """Unknown roles, periods, current-value selectors and conflicts stay pending."""
    if type(evidence) is not HDCNormalizado:
        raise TypeError('Requiere HDC normalizado validado, no agregados ni overrides')
    if type(criterio_funcional) is not bool:raise TypeError('Criterio interno booleano requerido')
    result = evidence['resultado']
    monthly=_cuota_mes_cab(evidence,informe_xml) if criterio_funcional else {}
    ref = result['consulta_id']
    rows = [o for o in result['obligaciones'] if o['tipo_estructural'] in {'CuentaCartera', 'TarjetaCredito'}]
    counts = Counter(o['tipo_estructural'] for o in rows)
    coverage = (counts['CuentaCartera'] == result['cobertura']['total_carteras']
        and counts['TarjetaCredito'] == result['cobertura']['total_tarjetas'])
    date_raw = result['fecha_consulta']
    try:
        cutoff = datetime.fromisoformat(date_raw.replace('Z', '+00:00')).date()
    except (TypeError, ValueError, AttributeError):
        cutoff = None
    keys, signatures = [], []
    for o in rows:
        number = o['identificador']['numero']
        entity = ('CODIGO',o['codSuscriptor']) if o['codSuscriptor'] else ('NOMBRE_LITERAL',o['entidad']) if o['entidad'] else None
        key = (o['tipo_estructural'], entity, number) if entity and number else None
        keys.append(key)
        # Identical copies only. Conflicting records are never resolved by order.
        signatures.append(json.dumps({k: o.a_dict()[k] for k in
            ('estadoPago', 'estadoCuenta', 'calidadDeudor', 'valores', 'valor_actual')}, sort_keys=True))
    conflicts = {key for key in keys if key is not None
        and len({signatures[i] for i, other in enumerate(keys) if other == key}) > 1}
    seen, parts = set(), []
    for i, o in enumerate(rows):
        key = keys[i]
        anonymous = sha256(json.dumps([ref, key if key else ['SIN_IDENTIDAD', i]]).encode()).hexdigest()[:20]
        state, period, balance, quota = _code(o['estadoPago']), None, None, None
        treatment, reason = 'PENDIENTE', 'ESTADO_PAGO_NO_DOCUMENTADO'
        selection = o['valor_actual']
        selected = (list(o['valores']) if len(o['valores']) == 1 else
            [v for v in o['valores'] if selection['disponible']
            and v['posicion_original'] == selection['posicion_original']])
        v = selected[0] if len(selected) == 1 else None
        at = None
        if v is not None:
            balance, quota, period = _money(v['saldoActual']), _money(v['cuota']), _code(v['periodicidad'])
            try:
                at = date.fromisoformat(v['fecha'])
            except (TypeError, ValueError):
                pass
        account_state = _code(o['estadoCuenta'])
        issuer = ' '.join((o['entidad'] or '').upper().split())
        if key in conflicts:
            reason = 'DUPLICADO_CONFLICTIVO'
        elif key is not None and key in seen:
            treatment, reason = 'EXCLUIDA', 'COPIA_EXACTA_YA_CONTABILIZADA'
        elif criterio_funcional and issuer in {'CONGENTE', 'CONGENTE ANTES COOTRADEPMETA'}:
            treatment, reason = 'EXCLUIDA', 'CONGENTE_EXCLUIDA_REGLA_EXCEL_UNICO'
        elif criterio_funcional and _code(o['sector']) == '4' and issuer == 'CLARO SERV FIJ':
            treatment, reason = 'EXCLUIDA', 'SERVICIO_TELCOS_ACREDITADO_FUERA_REGLA_CREDITO'
        elif state in CLOSED:
            if account_state in {'01', '02', '05', '06'}:
                reason = 'ESTADO_CUENTA_Y_ESTADO_PAGO_CONTRADICTORIOS'
            elif balance is not None and balance > 0:
                reason = 'CIERRE_CON_SALDO_POSITIVO_REQUIERE_CONCILIACION'
            else:
                treatment, reason = 'EXCLUIDA', 'ESTADO_PAGO_CERRADO_TABLA_4'
        elif state in OPEN:
            if account_state in {'03', '04', '07', '08', '09', '10', '11', '12', '13', '14', '15', '16', '17'}:
                reason = 'ESTADO_CUENTA_CIERRE_O_ESPECIAL_REQUIERE_CONCILIACION'
            elif key is None:
                reason = 'IDENTIDAD_OBLIGACION_INSUFICIENTE_PARA_RECONCILIAR'
            elif _code(o['calidadDeudor']) != '00':
                reason = 'ROL_NO_PRINCIPAL_REQUIERE_POLITICA'
            elif v is None:
                reason = 'OBSERVACION_ACTUAL_NO_IDENTIFICADA_POR_GATEWAY'
            else:
                if cutoff is None or at is None or at > cutoff:
                    reason = 'FECHA_DEL_VALOR_NO_APLICABLE_AL_CORTE'
                elif criterio_funcional and (quota is None or balance is None):
                    reason = 'CUOTA_O_SALDO_SIN_CONVERSION_COP_ACREDITADA'
                elif criterio_funcional and o['tipo_estructural'] == 'TarjetaCredito':
                    treatment, reason = 'INCLUIDA', 'CUOTA_REPORTADA_TARJETA_ABIERTA_CRITERIO_FUNCIONAL'
                elif o['tipo_estructural'] == 'TarjetaCredito':
                    reason = 'TABLA_7_NO_CERTIFICA_PERIODICIDAD_DE_TARJETA'
                elif o['posicion_original'] in monthly and balance is not None and balance > 0:
                    treatment, reason = 'INCLUIDA', 'CUOTA_MES_CAB_COTEJADA_CON_DETALLE_COP'
                elif period != '1':
                    reason = 'PERIODICIDAD_NO_MENSUAL_TABLA_7'
                elif quota is None or balance is None:
                    reason = 'CUOTA_O_SALDO_SIN_CONVERSION_COP_ACREDITADA'
                elif balance == 0 and quota > 0:
                    reason = 'SALDO_CERO_Y_CUOTA_POSITIVA_SIN_APLICABILIDAD_CERTIFICADA'
                else:
                    treatment, reason = 'INCLUIDA', 'VIGENTE_PRINCIPAL_MENSUAL_HDC'
        if key is not None:
            seen.add(key)
        parts.append(PartidaCuotaHDC(referencia=anonymous, posicion=o['posicion_original'],
            tipo=o['tipo_estructural'], estado_pago=state, periodicidad=period, fecha_valor=at,
            saldo_cop=balance, cuota_cop=quota, cuota_reconocida_cop=quota if treatment == 'INCLUIDA' else None,
            tratamiento=treatment, motivo=reason,**(monthly.get(o['posicion_original'],{}) if treatment=='INCLUIDA' else {})))
    amounts = [p.cuota_reconocida_cop for p in parts if p.tratamiento == 'INCLUIDA']
    with localcontext() as context:
        context.prec = precision_exacta(*amounts) + len(str(len(amounts))) if amounts else 28
        subtotal = sum(amounts, Decimal(0))
    complete = coverage and cutoff is not None and not any(p.tratamiento == 'PENDIENTE' for p in parts)
    def fact(value, path, note):
        return Hecho(valor=value, disponible=value is not None, fuente='HDC/DATACREDITO',
            referencia_evidencia=ref, ruta_origen=path, fecha_corte=cutoff,
            estado_certificacion=EC.OBSERVADO if value is not None else EC.NO_CERTIFICADO,
            observacion=note)
    return ConciliacionCuotasHDC(partidas=tuple(parts), cobertura_completa=coverage,
        criterio_funcional=CRITERIO_FUNCIONAL if criterio_funcional else None,
        aprobacion_riesgos=False if criterio_funcional else None,
        partidas_resueltas=complete,
        subtotal_reconocido=fact(subtotal, 'conciliacionHdc.subtotalReconocido',
            'Subtotal informativo; no usar como total cuando existan partidas pendientes'),
        cuotas_aplicables=fact(subtotal if complete and not criterio_funcional else None, 'conciliacionHdc.cuotasAplicables',
            'Al corte del informe; no acredita saldo/cuota de hoy ni vigencia para decision. ' +
            ('Criterio funcional del responsable; aprobacion normativa de Riesgos pendiente. '
             'Excluye Congente y servicio Telcos acreditado; sin adicionar LINIX. ' if criterio_funcional else
             'Solo cuotas HDC; incluye reportes Congente, sin adicionar cuotas LINIX. ')
            + ('Cobertura completa y partidas resueltas' if complete else 'CONCILIACION_INCOMPLETA')))


def reservas_para_revision(conciliacion):
    """Actionable anonymous reservations; classification does not settle the debt."""
    if type(conciliacion) is not ConciliacionCuotasHDC:raise TypeError('Conciliacion tipada requerida')
    requirements={
        'SALDO_CERO_Y_CUOTA_POSITIVA_SIN_APLICABILIDAD_CERTIFICADA':(
            'DATO_INSTITUCIONAL_FALTANTE','Acreedor / responsable de conciliacion HDC',
            'Confirmar pago exigible al mismo corte: saldo cero no certifica cuota exigible ni cancelacion.'),
        'TABLA_7_NO_CERTIFICA_PERIODICIDAD_DE_TARJETA':(
            'DATO_INSTITUCIONAL_FALTANTE','Responsable de datos HDC',
            'Acreditar periodicidad y pago exigible de la tarjeta; tabla 7 de cartera no basta.'),
        'PERIODICIDAD_NO_MENSUAL_TABLA_7':(
            'DEFINICION_RIESGOS_PENDIENTE','Riesgos',
            'Definir tratamiento mensual de periodicidad no mensual; no dividir ni multiplicar sin norma.'),
    }
    result=[]
    for row in conciliacion.partidas:
        if row.tratamiento!='PENDIENTE':continue
        category,owner,action=requirements.get(row.motivo,('DATO_INSTITUCIONAL_FALTANTE',
            'Responsable de datos HDC','Conciliar la causa individual sin usar el subtotal como deuda completa.'))
        result.append({'referenciaAnonima':row.referencia,'motivo':row.motivo,'clasificacion':category,
            'responsable':owner,'accion':action,'cuotaObservadaCOP':str(row.cuota_cop) if row.cuota_cop is not None else None,
            'fechaCorte':row.fecha_valor.isoformat() if row.fecha_valor else None,
            'estado':'PENDIENTE','incluidaEnSubtotal':False,'autorizaDecision':False})
    return result
