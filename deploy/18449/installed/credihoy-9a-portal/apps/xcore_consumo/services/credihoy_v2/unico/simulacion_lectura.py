"""Proyeccion financiera cifrada externa, solo lectura y sin datos de identidad."""
from pathlib import Path
import json

AVISO_SIMULACION = ('Cuota estimada. El valor definitivo y los conceptos aplicables seran informados '
    'en el plan de pagos antes de la aceptacion del credito.')
CONCEPTOS_PENDIENTES = ['CAPITALIZACION_APORTES_APLICABLE', 'OTROS_CONCEPTOS_APLICABLES',
    'INTERESES_INICIALES_Y_CALENDARIO_LINIX']


def simulador_habilitado():
    return getattr(settings, 'CREDIHOY_SIMULADOR_LOCAL_ENABLED', False) is True


def es_simulacion(payload):
    return type(payload) is dict and payload.get('operacion') == 'simular'


def _seguro(monto):
    from decimal import Decimal
    from apps.xcore_consumo.services.calculadora import calcular_vida_deudores
    raw = getattr(settings, 'CREDIHOY_SIMULADOR_SEGURO_MENSUAL', '0.0009')
    if type(raw) is not str:
        raise ValueError('SEGURO_MENSUAL_INVALIDO')
    factor = Decimal(raw)
    if not factor.is_finite() or not Decimal(0) <= factor <= Decimal('0.01'):
        raise ValueError('SEGURO_MENSUAL_INVALIDO')
    value = (calcular_vida_deudores(monto) if factor == Decimal('0.0009')
        else round(float(monto) * float(factor), 2))
    return {'tasaMensualDecimal': format(factor, 'f'), 'base': 'CAPITAL_INICIAL',
        'baseCOP': format(monto, '.2f'), 'valorMensualEstimado': format(Decimal(str(value)), '.2f'),
        'fuente': 'calculadora.calcular_vida_deudores',
        'comparacionLinix': 'BASE_Y_REDONDEO_NO_CONCILIADOS'}


def _limites():
    from decimal import Decimal
    import re
    config = getattr(settings, 'CREDIHOY_SIMULADOR_LIMITES', None)
    if config is None:
        from django.utils import timezone
        from .condiciones_producto import politica_digital
        from .normativa import smmlv_al_corte, limite_monetario
        policy=politica_digital();legal=smmlv_al_corte(timezone.now(),modalidad='111')
        if not legal.disponible or policy['perfil']!=['390','111','CFA']:
            raise ValueError('LIMITES_PRODUCTO_NO_ACREDITADOS')
        config={'version':policy['version'],'referencia':policy['referencia'],'modalidad':'111',
            'montoMinimo':format(limite_monetario(legal.valor,17),'.2f'),
            'montoMaximo':format(limite_monetario(legal.valor,12),'.2f'),
            'cuotasMinimas':policy['plazoMinimo'],
            'cuotasMaximas':max(r['plazoMaximo'] for r in policy['rangosSMMLV'])}
    fields = {'version', 'referencia', 'modalidad', 'montoMinimo', 'montoMaximo',
        'cuotasMinimas', 'cuotasMaximas'}
    if (type(config) is not dict or set(config) != fields or config['modalidad'] != '111'
            or any(type(config[k]) is not str or not config[k].strip() for k in ('version', 'referencia'))
            or any(type(config[k]) is not str or not re.fullmatch(r'[0-9]{1,10}(\.[0-9]{1,2})?', config[k])
                for k in ('montoMinimo', 'montoMaximo'))
            or not 0 < Decimal(config['montoMinimo']) <= Decimal(config['montoMaximo'])
            or any(type(config[k]) is not int for k in ('cuotasMinimas', 'cuotasMaximas'))
            or not 1 <= config['cuotasMinimas'] <= config['cuotasMaximas'] <= 48):
        raise ValueError('REGLAS_PRODUCTO_NO_CONFIGURADAS')
    # Generic product bounds, NOT a personal approved amount or an eligibility test.
    return {k: v for k, v in {**config, 'montoMaximo': format(Decimal(config['montoMaximo']), '.2f')}.items() if k != 'modalidad'}


def operar_simulacion(identity, payload, rid):
    """Read-only owner operation. No quote/consent/decision/offer side effects."""
    from datetime import datetime
    from decimal import Decimal
    import re
    from uuid import UUID
    from django.http import Http404
    from django.utils import timezone
    from apps.solicitudes.models import Solicitud
    from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy, TasaComercialCredihoy
    from . import publico_institucional as wire, tasas
    from .publico_restringido import fuente_cotizacion, alias_cotizacion
    from .condicion_comercial import pagaduria_seleccion, resolver_condicion
    if not simulador_habilitado():
        raise wire.NoDisponible
    if (not es_simulacion(payload) or set(payload) not in ({'solicitudId', 'operacion'},
            {'solicitudId', 'operacion', 'escenario'})):
        raise ValueError('REQUEST_INVALIDO')
    sid = str(UUID(payload['solicitudId']))
    rid = str(UUID(rid))
    expires = datetime.fromisoformat(identity['_sesionExpiraAt'])
    if (expires.utcoffset() is None or expires <= timezone.now()
            or str(UUID(identity['_sesionReferencia'])) != identity['_sesionReferencia']):
        raise wire.NoDisponible
    with fuente_cotizacion():
        alias = alias_cotizacion()
        if alias == 'default':
            if (getattr(settings, 'CREDIHOY_TITULAR_FUENTE_ENABLED', True) is not True
                    or getattr(settings, 'CREDIHOY_SEGUIMIENTO_DB_ALIAS', '') != 'default'
                    or not getattr(settings, 'CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE', '')):
                raise wire.NoDisponible
            permitted = getattr(settings, 'CREDIHOY_PUBLICO_ENSAYO', {}).get('solicitudes', [])
        else:
            if not getattr(settings, 'CREDIHOY_COTIZACION_SOURCE_REFERENCE', ''):
                raise wire.NoDisponible
            permitted = getattr(settings, 'CREDIHOY_COTIZACION_SOLICITUDES', [])
            if (not permitted and getattr(settings,'CREDIHOY_AUTORIZACION_TITULAR_ENABLED',False) is True
                    and getattr(settings,'CREDIHOY_AUTORIZACION_DB_ALIAS','')==alias):
                # Same already authorized holder/UUID; no contractual quote scope is opened.
                permitted=getattr(settings,'CREDIHOY_AUTORIZACION_SOLICITUDES',[])
        if sid not in permitted:
            raise Http404
        try:
            s = Solicitud.objects.select_related('solicitante').get(pk=sid, producto=wire.PRODUCTO,
                metadatos__modo__in=['CONSULTA_REAL_CONTROLADA', 'AUTOGESTION_ENVIADA'],
                solicitante__tipo_identificacion='CC',
                solicitante__numero_identificacion=identity['numeroDocumento'])
        except Solicitud.DoesNotExist:
            raise Http404 from None
        if s.metadatos.get('modo') == 'AUTOGESTION_ENVIADA':
            from .autoservicio import validar_vinculo_consumo
            validar_vinculo_consumo(s)
        limits = _limites()
        selected = SeleccionTarifaCredihoy.objects.filter(solicitud=s).order_by('-creado_at', '-pk').first()
        scenario=payload.get('escenario',{})
        if type(scenario) is not dict:raise ValueError('ESCENARIO_INVALIDO')
        line=scenario.get('linea')
        if line is not None and line not in {'VENTANILLA','LIBRANZA'}:raise ValueError('LINEA_ESTIMACION_INVALIDA')
        current = resolver_condicion(pagaduria_seleccion(selected)) if (selected is not None and line is None
            and getattr(settings,'CREDIHOY_TASAS_ENABLED',False) is True) else None
        if current is not None and line is None and (not current.get('tarifaId') or not current.get('asignacionId')
                or any(current.get(k) != selected.resultado.get(k) for k in (
                    'tarifaId', 'versionTarifa', 'asignacionId', 'versionAsignacion',
                    'soporteTarifaSha256', 'soporteAsignacionSha256', 'tasaComercialNamv'))):
            raise ValueError('CONDICIONES_COMERCIALES_NO_VIGENTES')
        if current is not None and line is None:
            tariff = TasaComercialCredihoy.objects.select_related('perfil').prefetch_related('eventos').get(pk=current['tarifaId'])
        else:
            # Generic scenario line, not an assignment or a persisted commercial choice.
            line=line or 'VENTANILLA'
            candidates=[t for t in TasaComercialCredihoy.objects.filter(perfil__producto='390',
                perfil__modalidad='111',linea=line).select_related('perfil').prefetch_related('eventos')
                if tasas.vigente(t,tasas.hoy()) and tasas.condiciones_aprobadas(t)]
            if len(candidates)!=1:raise ValueError('TARIFA_ACTIVA_NO_UNIVOCA')
            tariff=candidates[0]
        if (tariff.linea not in {'VENTANILLA', 'LIBRANZA'}
                or tariff.tasa_namv != {'VENTANILLA': Decimal('17.69'), 'LIBRANZA': Decimal('16.67')}[tariff.linea]):
            raise ValueError('TARIFA_ORIENTATIVA_NO_ACREDITADA')
        amount, term = Decimal(limits['montoMinimo']), limits['cuotasMinimas']
        if 'escenario' in payload:
            e = payload['escenario']
            if (type(e) is not dict or set(e) not in ({'monto', 'plazoMeses'},{'monto','plazoMeses','linea'})
                    or type(e['monto']) is not str or not re.fullmatch(r'[0-9]{1,10}(\.[0-9]{1,2})?', e['monto'])
                    or type(e['plazoMeses']) is not int):
                raise ValueError('ESCENARIO_INVALIDO')
            amount, term = Decimal(e['monto']), e['plazoMeses']
            if (not Decimal(limits['montoMinimo']) <= amount <= Decimal(limits['montoMaximo'])
                    or not limits['cuotasMinimas'] <= term <= limits['cuotasMaximas']):
                raise ValueError('ESCENARIO_FUERA_LIMITES')
        subtotal = orientar_capital_interes(amount, term, tarifa=tariff, fecha=tasas.hoy())
        result = orientar_simulacion(subtotal) if 'escenario' in payload else None
        if expires <= timezone.now():
            raise wire.NoDisponible
        return {'versionContrato': 'credihoy-simulacion-local-v1', 'solicitudId': sid, 'requestId': rid,
            'alcance': 'ESTIMACION_GENERICA_NO_ELEGIBILIDAD',
            'limites': limits, 'tasa': {**subtotal['tasa'], 'linea': tariff.linea},
            'seguro': {k: v for k, v in _seguro(amount).items() if k not in {'baseCOP', 'valorMensualEstimado'}},
            'resultado': result, 'aviso': AVISO_SIMULACION, 'ofertable': False,
            'autorizaDecision': False, 'autorizaStart': False, 'autorizaDesembolso': False}


def orientar_simulacion(subtotal):
    """Expand the existing PMT subtotal, without producing a native payment plan."""
    from decimal import Decimal, ROUND_HALF_UP
    from django.utils import timezone
    from .condiciones_producto import exigir_plazo_por_monto, convertir_namv_a_ea
    amount, term = Decimal(subtotal['monto']), subtotal['plazoMeses']
    plazo = exigir_plazo_por_monto(amount, term, fecha=timezone.now())
    rate = Decimal(subtotal['tasa']['valor']) / Decimal(1200)
    payment = Decimal(subtotal['cuotaCapitalInteres'])
    insurance = _seguro(amount)
    premium = Decimal(insurance['valorMensualEstimado'])
    balance, total_interest = amount, Decimal(0)
    rows = []
    money = lambda value: format(value, '.2f')
    for number in range(1, term + 1):
        interest = (balance * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        capital = balance if number == term else payment - interest
        end = balance - capital
        if capital <= 0 or end < 0:
            raise ValueError('AMORTIZACION_ORIENTATIVA_INVALIDA')
        rows.append({'numero': number, 'saldoInicial': money(balance), 'capital': money(capital),
            'intereses': money(interest), 'subtotalCapitalInteres': money(capital + interest),
            'seguroEstimado': money(premium), 'cuotaEstimada': money(capital + interest + premium),
            'saldoFinal': money(end)})
        balance = end
        total_interest += interest
    return {'estado': 'ORIENTATIVA', 'unidad': 'COP', 'monto': money(amount), 'plazoMeses': term,
        'periodicidad': 'MENSUAL', 'tasaMensualDecimal': format(rate, 'f'),
        'tasaEaPorcentaje': convertir_namv_a_ea(Decimal(subtotal['tasa']['valor']))['tasaEaPorcentaje'],
        'plazoMaximoParaMonto': plazo['plazoMaximo'],
        'cuotaCapitalInteres': money(payment), 'seguro': insurance,
        'cuotaEstimada': money(payment + premium), 'totalCapital': money(amount),
        'totalIntereses': money(total_interest), 'totalSeguroEstimado': money(premium * term),
        'totalEstimado': money(amount + total_interest + premium * term), 'amortizacion': rows,
        'conceptosNoAcreditados': CONCEPTOS_PENDIENTES, 'fuente': subtotal['fuente'],
        'precision': 'PMT_LEGACY_2_DECIMALES_INTERES_HALF_UP_AJUSTE_ULTIMA_CUOTA_NO_CONTRACTUAL'}

from django.conf import settings
from apps.xcore_consumo.services.otp_crypto import decrypt_text, OTPCryptoError


def leer_evidencia(configuracion='CREDIHOY_SIMULACION_EVIDENCIA_PATH'):
    path = getattr(settings, configuracion, None)
    if not path:
        return None
    try:
        p = Path(path)
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 20000:
            return None
        return json.loads(decrypt_text(p.read_text(encoding='utf-8').strip()))
    except (OSError, ValueError, TypeError, OTPCryptoError):
        return None


def orientar_capital_interes(monto, plazo_meses, *, tarifa, fecha):
    """Internal subtotal from the existing calculator, not an Oracle quote.

    The tariff is a server-selected catalog model, never request-supplied JSON.
    Unknown insurance/contributions are excluded explicitly, not valued at zero.
    This helper does not enable any HTTP action or create an offer.
    """
    from datetime import date
    from decimal import Decimal
    import math
    import re
    from . import tasas
    from apps.xcore_consumo.tasas_models import TasaComercialCredihoy

    if (type(monto) is not Decimal or not monto.is_finite() or monto <= 0
            or type(plazo_meses) is not int or not 1 <= plazo_meses <= 48
            or type(fecha) is not date):
        raise ValueError('ENTRADA_ORIENTATIVA_INVALIDA')
    if (not isinstance(tarifa,TasaComercialCredihoy) or not tarifa.pk
            or tarifa.perfil.producto!='390' or tarifa.perfil.modalidad!='111'
            or not tasas.vigente(tarifa,fecha)):
        raise ValueError('TARIFA_ORIENTATIVA_NO_ACREDITADA')
    support=tasas.condiciones_aprobadas(tarifa)
    if (type(support) is not dict or not all(isinstance(support.get(k),str) and support[k].strip()
            for k in ('documento','versionDocumento','referencia'))
            or not isinstance(support.get('soporteSha256'),str)
            or not re.fullmatch('[a-f0-9]{64}',support['soporteSha256'])
            or type(tarifa.tasa_namv) is not Decimal
            or not tarifa.tasa_namv.is_finite() or tarifa.tasa_namv<=0):
        raise ValueError('TARIFA_ORIENTATIVA_NO_ACREDITADA')
    from apps.xcore_consumo.services.calculadora import calcular_valor_cuota
    # The legacy argument is named EA, but its implemented unit is NAMV / 12.
    subtotal=calcular_valor_cuota(monto,plazo_meses,tarifa.tasa_namv,0,0)
    if not math.isfinite(subtotal) or subtotal<=0:
        raise ValueError('SUBTOTAL_ORIENTATIVO_INVALIDO')
    return {'estado':'ORIENTATIVA_PARCIAL','unidad':'COP',
        'monto':format(monto,'f'),'plazoMeses':plazo_meses,'periodicidad':'MENSUAL',
        'cuotaCapitalInteres':format(Decimal(str(subtotal)),'.2f'),
        'cuotaTotal':None,'cuotaContractual':None,'capacidadPago':None,
        'tasa':{'valor':str(tarifa.tasa_namv),'unidad':'PORCENTAJE_NAMV',
            'tarifaId':tarifa.pk,'version':tarifa.numero_version,
            **{k:support[k] for k in ('documento','versionDocumento','referencia','soporteSha256')}},
        'corte':fecha.isoformat(),
        'fuente':'calculadora.calcular_valor_cuota/numpy_financial.pmt',
        'precision':'REDONDEO_ORIENTATIVO_LEGACY_2_DECIMALES_NO_CONTRACTUAL',
        'incluyeSeguroYCargos':False,'ofertable':False,'autorizaDecision':False,
        'faltantes':['SEGURO_VIDA_CREDIHOY','CAPITALIZACION_APORTES_APLICABLE',
            'OTROS_CONCEPTOS_APLICABLES','CUOTA_CONTRACTUAL_LINIX_CONCILIADA']}
