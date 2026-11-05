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
    target = 'borradorId' if 'borradorId' in payload else 'solicitudId'
    if not es_simulacion(payload) or set(payload) not in ({target, 'operacion'}, {target, 'operacion', 'escenario'}):
        raise ValueError('REQUEST_INVALIDO')
    sid = str(UUID(payload[target]))
    rid = str(UUID(rid))
    expires = datetime.fromisoformat(identity['_sesionExpiraAt'])
    if expires.utcoffset() is None or expires <= timezone.now() or str(UUID(identity['_sesionReferencia'])) != identity['_sesionReferencia']:
        raise wire.NoDisponible
    with fuente_cotizacion():
        alias = alias_cotizacion()
        if alias == 'default':
            if getattr(settings, 'CREDIHOY_TITULAR_FUENTE_ENABLED', True) is not True or getattr(settings, 'CREDIHOY_SEGUIMIENTO_DB_ALIAS', '') != 'default' or (not getattr(settings, 'CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE', '')):
                raise wire.NoDisponible
            permitted = getattr(settings, 'CREDIHOY_PUBLICO_ENSAYO', {}).get('solicitudes', [])
        else:
            if not getattr(settings, 'CREDIHOY_COTIZACION_SOURCE_REFERENCE', ''):
                raise wire.NoDisponible
            permitted = getattr(settings, 'CREDIHOY_COTIZACION_SOLICITUDES', [])
            if not permitted and getattr(settings, 'CREDIHOY_AUTORIZACION_TITULAR_ENABLED', False) is True and (getattr(settings, 'CREDIHOY_AUTORIZACION_DB_ALIAS', '') == alias):
                permitted = getattr(settings, 'CREDIHOY_AUTORIZACION_SOLICITUDES', [])
        draft = target == 'borradorId'
        if draft:
            from .progreso_temporal import alias_operativo
            from .publico_seguimiento import document_hmac
            from apps.solicitudes.models import ProgresoTemporalCredihoy
            from django.db import transaction, connections
            draft_alias = alias_operativo(lectura={**identity, '_scopeLectura': 'solicitudes:leer', '_requestId': rid})
            with transaction.atomic(using=draft_alias):
                with connections[draft_alias].cursor() as c:
                    c.execute('SET TRANSACTION READ ONLY')
                if not ProgresoTemporalCredihoy.objects.using(draft_alias).filter(pk=sid, titular_hmac=document_hmac(identity['numeroDocumento']), vence_at__gt=timezone.now()).exists():
                    raise Http404
        listed = sid in permitted
        if not listed and (not draft):
            from .publico_controles import admision_ensayo_vigente
            if not admision_ensayo_vigente():
                raise Http404
        s = None
        if not draft:
            try:
                s = Solicitud.objects.select_related('solicitante').get(pk=sid, producto=wire.PRODUCTO, metadatos__modo__in=['CONSULTA_REAL_CONTROLADA', 'AUTOGESTION_ENVIADA'], solicitante__tipo_identificacion='CC', solicitante__numero_identificacion=identity['numeroDocumento'])
            except Solicitud.DoesNotExist:
                raise Http404 from None
        if s is not None and s.metadatos.get('modo') == 'AUTOGESTION_ENVIADA':
            from .autoservicio import validar_vinculo_consumo, habilitar_lectura
            habilitar_lectura({**identity, '_scopeLectura': 'solicitudes:leer', '_requestId': rid})
            validar_vinculo_consumo(s)
        elif not listed and (not draft):
            raise Http404
        limits = _limites()
        selected = SeleccionTarifaCredihoy.objects.filter(solicitud=s).order_by('-creado_at', '-pk').first() if s is not None else None
        scenario = payload.get('escenario', {})
        if type(scenario) is not dict:
            raise ValueError('ESCENARIO_INVALIDO')
        line = scenario.get('linea')
        if line is not None and line not in {'VENTANILLA', 'LIBRANZA'}:
            raise ValueError('LINEA_ESTIMACION_INVALIDA')
        current = resolver_condicion(pagaduria_seleccion(selected) if selected is not None else _pagaduria_identidad(identity, rid))
        if not current.get('tarifaId') or not current.get('asignacionId'):
            raise ValueError('PAGADURIA_O_TARIFA_SIMULACION_NO_ACREDITADA')
        if line is not None and line != current.get('linea'):
            raise ValueError('LINEA_SIMULACION_NO_CORRESPONDE_A_PAGADURIA')
        if selected is not None and (not current.get('tarifaId') or not current.get('asignacionId') or any((current.get(k) != selected.resultado.get(k) for k in ('tarifaId', 'versionTarifa', 'asignacionId', 'versionAsignacion', 'soporteTarifaSha256', 'soporteAsignacionSha256', 'tasaComercialNamv')))):
            raise ValueError('CONDICIONES_COMERCIALES_NO_VIGENTES')
        tariff = TasaComercialCredihoy.objects.select_related('perfil').prefetch_related('eventos').get(pk=current['tarifaId'])
        if tariff.linea != current.get('linea') or tariff.numero_version != current.get('versionTarifa') or tariff.tasa_namv != Decimal(str(current.get('tasaComercialNamv'))):
            raise ValueError('CONDICIONES_COMERCIALES_CAMBIARON')
        if tariff.linea not in {'VENTANILLA', 'LIBRANZA'} or tariff.tasa_namv != {'VENTANILLA': Decimal('17.69'), 'LIBRANZA': Decimal('16.67')}[tariff.linea]:
            raise ValueError('TARIFA_ORIENTATIVA_NO_ACREDITADA')
        amount, term = (Decimal(limits['montoMinimo']), limits['cuotasMinimas'])
        if 'escenario' in payload:
            e = payload['escenario']
            if type(e) is not dict or set(e) not in ({'monto', 'plazoMeses'}, {'monto', 'plazoMeses', 'linea'}) or type(e['monto']) is not str or (not re.fullmatch('[0-9]{1,10}(\\.[0-9]{1,2})?', e['monto'])) or (type(e['plazoMeses']) is not int):
                raise ValueError('ESCENARIO_INVALIDO')
            amount, term = (Decimal(e['monto']), e['plazoMeses'])
            if not Decimal(limits['montoMinimo']) <= amount <= Decimal(limits['montoMaximo']) or not limits['cuotasMinimas'] <= term <= limits['cuotasMaximas']:
                raise ValueError('ESCENARIO_FUERA_LIMITES')
        subtotal = orientar_capital_interes(amount, term, tarifa=tariff, fecha=tasas.hoy())
        result = orientar_simulacion(subtotal) if 'escenario' in payload else None
        if expires <= timezone.now():
            raise wire.NoDisponible
        return {'versionContrato': 'credihoy-simulacion-local-v1', target: sid, 'requestId': rid, 'alcance': 'ESTIMACION_GENERICA_NO_ELEGIBILIDAD', 'limites': limits, 'tasa': {**subtotal['tasa'], 'linea': tariff.linea}, 'seguro': {k: v for k, v in _seguro(amount).items() if k not in {'baseCOP', 'valorMensualEstimado'}}, 'resultado': result, 'aviso': AVISO_SIMULACION, 'ofertable': False, 'autorizaDecision': False, 'autorizaStart': False, 'autorizaDesembolso': False}


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


def _pagaduria_identidad(identity, request_id):
    """Short simulation-only cache; the study always obtains its own fresh facts."""
    from hashlib import sha256
    from django.core.cache import cache
    from django.utils import timezone
    from ..cliente import ApiDataCrediHoyV2Client, ConfiguracionApiDataV2
    key = 'credihoy-pagaduria-simulacion:' + sha256((identity['_sesionReferencia'] + ':' + identity['numeroDocumento']).encode()).hexdigest()
    found = cache.get(key)
    if found is not None:
        return found
    facts = ApiDataCrediHoyV2Client(ConfiguracionApiDataV2.desde_settings()).consultar(numero_documento=identity['numeroDocumento'], fecha_evaluacion=timezone.now().replace(microsecond=0), request_id=request_id)
    if facts.pagaduria is None:
        raise ValueError('PAGADURIA_SIMULACION_NO_DISPONIBLE')
    cache.set(key, facts.pagaduria, timeout=60)
    return facts.pagaduria


def estimar_cuota_expediente(solicitud, hechos, *, fecha):
    """Estimate from the bound dossier and payroll source, never browser amounts."""
    from decimal import Decimal
    from datetime import datetime
    from hashlib import sha256
    from zoneinfo import ZoneInfo
    from apps.xcore_consumo.tasas_models import TasaComercialCredihoy
    from ..hechos import HechosInternosCrediHoyV2
    from ..base import Hecho, EstadoCertificacion as EC
    from .funcional import _formulario_persistido
    from .condicion_comercial import resolver_condicion
    if not isinstance(hechos, HechosInternosCrediHoyV2) or hechos.metadatos is None or hechos.metadatos.fecha_evaluacion != fecha:
        raise ValueError('CUOTA_HECHOS_DE_OTRO_CORTE')
    if hechos.pagaduria is not None and (hechos.pagaduria.requestId != hechos.metadatos.request_id or datetime.fromisoformat(hechos.pagaduria.fechaConsulta) != hechos.metadatos.fecha_consulta):
        raise ValueError('CUOTA_PAGADURIA_DE_OTRA_LECTURA')
    form = _formulario_persistido(solicitud, solicitud.consumo_detail.form_data)
    cutoff = fecha.astimezone(ZoneInfo('America/Bogota')).date()
    condition = resolver_condicion(hechos.pagaduria, corte=cutoff)
    if not condition.get('tarifaId') or condition.get('linea') not in {'LIBRANZA', 'VENTANILLA'}:
        raise ValueError('CUOTA_PAGADURIA_O_TARIFA_NO_DISPONIBLE')
    if condition['linea'] != {'N': 'LIBRANZA', 'V': 'VENTANILLA'}.get(hechos.pagaduria.tipo):
        raise ValueError('CUOTA_LINEA_NO_CORRESPONDE_A_PAGADURIA')
    tariff = TasaComercialCredihoy.objects.select_related('perfil').prefetch_related('eventos').get(pk=condition['tarifaId'])
    if tariff.linea != condition['linea'] or tariff.numero_version != condition.get('versionTarifa') or tariff.tasa_namv != Decimal(str(condition.get('tasaComercialNamv'))):
        raise ValueError('CUOTA_CONDICIONES_COMERCIALES_DIFIEREN')
    subtotal = orientar_capital_interes(Decimal(form['monto']), form['plazoMeses'], tarifa=tariff, fecha=cutoff)
    estimate = orientar_simulacion(subtotal)
    evidence = {'solicitudId': str(solicitud.pk), 'requestId': hechos.metadatos.request_id, 'linea': tariff.linea, 'condicion': condition, 'simulacion': estimate}
    digest = sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
    return Hecho(valor=Decimal(estimate['cuotaEstimada']), disponible=True, fuente='SIMULADOR_INSTITUCIONAL_CREDIHOY', referencia_evidencia=digest, ruta_origen='simulacion.cuotaEstimada', fecha_corte=cutoff, fecha_consulta=fecha, estado_certificacion=EC.OBSERVADO, observacion='Cuota orientativa, no contractual; linea=' + tariff.linea + '; tarifa=' + str(tariff.pk) + '; version=' + str(tariff.numero_version))
