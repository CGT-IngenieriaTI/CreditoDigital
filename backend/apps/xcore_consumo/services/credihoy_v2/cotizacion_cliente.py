"""Institutional quote transport. No direct Oracle connection or retries."""


from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import re
from .cliente import ApiDataCrediHoyV2Client

VERSION = 'credihoy-cotizacion-institucional-v1'
ENDPOINT = '/api/v2/incluirtec/credihoy-digital/cotizacion/'
ESTADOS = {'NO_HABILITADA', 'PARAMETROS_FALTANTES', 'NO_DISPONIBLE',
           'DISPONIBLE_HOMOLOGACION', 'DISPONIBLE_PARCIAL', 'EVIDENCIA_INSTITUCIONAL_MANUAL', 'ERROR_TEMPORAL'}


def _calendar_date(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('COTIZACION_RESPUESTA_INVALIDA')
    return date.fromisoformat(value)


def validar_respuesta(p, payload, request_id):
    try:
        if (type(p) is not dict or p['versionContrato'] != VERSION
                or p['requestId'] != request_id or p['solicitudId'] != payload['solicitudId']
                or p['versionCondiciones'] != payload['versionCondiciones']
                or p['condicionesSolicitadas'] != payload['condiciones']
                or p['habilitadoDecisionProductiva'] is not False
                or p['estado'] not in ESTADOS or type(p['disponible']) is not bool
                or p['cotizacionIntegral'] is not False
                or p['fuente'] not in {'LINIX_MOTOR_FORMS', 'LINIX_CALCULO_CUOTA', 'LINIX_EJECUCION_MANUAL'}
                or p['calendario'] != {'disponible': False, 'cuotas': None}
                or p['seguroVida']['valorPorCuota'] is not None
                or p['seguroVida']['disponible'] is not False
                or type(p['camposFaltantes']) is not list
                or any(type(v) is not str or not re.fullmatch(r'[a-z_]{1,64}', v) for v in p['camposFaltantes'])
                or datetime.fromisoformat(p['fechaConsulta']).utcoffset() is None
                or datetime.fromisoformat(p['fechaConsulta']) > datetime.now(timezone.utc)):
            raise ValueError
        if p.get('simulacion') != payload.get('simulacion'):
            raise ValueError
        if p['disponible']:
            manual = p['fuente'] == 'LINIX_EJECUCION_MANUAL'
            scalar = p['fuente'] in {'LINIX_CALCULO_CUOTA', 'LINIX_EJECUCION_MANUAL'}
            expected_state = 'EVIDENCIA_INSTITUCIONAL_MANUAL' if manual else 'DISPONIBLE_PARCIAL' if scalar else 'DISPONIBLE_HOMOLOGACION'
            if (p['estado'] != expected_state
                    or p['ambiente'] not in ({None} if manual else {'HOMOLOGACION', 'PRODUCCION'} if scalar else {'HOMOLOGACION'})
                    or type(p['cuotaInstitucional']) is not str
                    or not Decimal(p['cuotaInstitucional']).is_finite()
                    or Decimal(p['cuotaInstitucional']) < 0
                    or (scalar and Decimal(p['cuotaInstitucional']) == 0)):
                raise ValueError
            if manual:
                evidence = p['evidenciaManual']
                if (p['vigenteHasta'] is not None or evidence['version'] != 'credihoy-cuota-linix-manual-v1'
                        or not re.fullmatch(r'[a-f0-9]{64}', evidence['sha256'])
                        or evidence['coberturaContractualCompleta'] is not False
                        or evidence['aceptacionAsociado'] is not False
                        or evidence['ejecucionAutomaticaHabilitada'] is not False
                        or evidence['origen'] != 'DECLARACION_RESPONSABLE_Y_CAPTURA_DBEAVER'
                        or type(evidence['calendarioObservado']) is not list
                        or len(evidence['calendarioObservado']) != payload['condiciones']['numeroCuotas']
                        or p['primeraFechaPago'] != evidence['calendarioObservado'][0]['fecha']):
                    raise ValueError
                _calendar_date(evidence['fechaRecepcion'])
                if evidence['fechaEjecucion'] is not None:
                    execution = datetime.fromisoformat(evidence['fechaEjecucion'])
                    if execution.utcoffset() is None or execution > datetime.now(timezone.utc): raise ValueError
                previous = _calendar_date(evidence['fechaInicialObservada'])
                # Reconcile supplied amounts; this is not an amortization calculation.
                with localcontext() as context:
                    context.prec = 38
                    capital = Decimal(0)
                    for number, row in enumerate(evidence['calendarioObservado'], 1):
                        current = _calendar_date(row['fecha'])
                        if (set(row) != {'numero', 'fecha', 'capital', 'IC', 'IV', 'SV', 'total'}
                                or type(row['numero']) is not int or row['numero'] != number or current <= previous
                                or any(type(row[k]) is not str or not re.fullmatch(
                                    r'[0-9]{1,15}(\.[0-9]{1,8})?', row[k]) for k in ('capital', 'IC', 'IV', 'SV', 'total'))
                                or Decimal(row['total']) != Decimal(p['cuotaInstitucional'])
                                or sum((Decimal(row[k]) for k in ('capital', 'IC', 'IV', 'SV')), Decimal(0))
                                    != Decimal(row['total'])):
                            raise ValueError
                        previous = current
                        capital += Decimal(row['capital'])
                    if capital != Decimal(payload['condiciones']['monto']): raise ValueError
            else:
                expiry = datetime.fromisoformat(p['vigenteHasta'])
                if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                    raise ValueError
        elif p['cuotaInstitucional'] is not None or p['estado'] in {'DISPONIBLE_HOMOLOGACION', 'DISPONIBLE_PARCIAL', 'EVIDENCIA_INSTITUCIONAL_MANUAL'}:
            raise ValueError
        if p['primeraFechaPago'] is not None:
            datetime.fromisoformat(p['primeraFechaPago'])
        factor = p['seguroVida']['factorAplicado']
        if factor is not None and (type(factor) is not str or not Decimal(factor).is_finite() or Decimal(factor) < 0):
            raise ValueError
        params = p['parametrosAplicados']
        if params is not None:
            scalar = p['fuente'] in {'LINIX_CALCULO_CUOTA', 'LINIX_EJECUCION_MANUAL'}
            keys = {'periodicidadInterna', 'factorAdicional', 'tasaNominalNamv'} if scalar else {'tasaFija', 'tasaVariable', 'P_EXTRAO'}
            if set(params) != keys:
                raise ValueError
            for value in params.values():
                if type(value) is not str or not Decimal(value).is_finite() or Decimal(value) < 0:
                    raise ValueError
            if scalar:
                if (params['periodicidadInterna'] != '1' or params['factorAdicional'] != '0'
                        or Decimal(params['tasaNominalNamv']) != Decimal(payload['condiciones']['tasaNominalNamv'])):
                    raise ValueError
            elif Decimal(params['tasaFija']) + Decimal(params['tasaVariable']) != Decimal(payload['condiciones']['tasaNominalNamv']):
                raise ValueError
        elif p['disponible']:
            raise ValueError
        if p['fuente'] == 'LINIX_CALCULO_CUOTA' and (p['primeraFechaPago'] is not None or factor is not None):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
        raise ValueError('COTIZACION_RESPUESTA_INVALIDA') from None
    keys = ('versionContrato', 'requestId', 'solicitudId', 'versionCondiciones', 'fechaConsulta',
            'fechaEvaluacion', 'habilitadoDecisionProductiva', 'estado', 'disponible', 'cuotaInstitucional',
            'condicionesSolicitadas', 'parametrosAplicados', 'seguroVida', 'primeraFechaPago', 'calendario',
            'cotizacionIntegral', 'ambiente', 'fuente', 'camposFaltantes', 'vigenteHasta')
    result = {k: p[k] for k in keys}
    if 'simulacion' in payload:
        if p['fuente'] not in {'LINIX_CALCULO_CUOTA','LINIX_MOTOR_FORMS'} or p['fuente']=='LINIX_MOTOR_FORMS' and p['disponible']:
            raise ValueError('COTIZACION_RESPUESTA_INVALIDA')
        result['simulacion']=p['simulacion']
    if p['fuente'] == 'LINIX_EJECUCION_MANUAL':
        result['evidenciaManual'] = {k:p['evidenciaManual'][k] for k in (
            'sha256','version','origen','referencia','fechaRecepcion','fechaEjecucion',
            'fechaInicialObservada','calendarioObservado','coberturaContractualCompleta',
            'aceptacionAsociado','ejecucionAutomaticaHabilitada')}
    result['seguroVida'] = {k: p['seguroVida'][k] for k in ('factorAplicado', 'valorPorCuota', 'disponible')}
    return result


class ApiDataCotizacionClient(ApiDataCrediHoyV2Client):

    def vincular_radicacion(self, binding):
        from uuid import UUID
        if type(binding) is not dict or set(binding) != {'solicitudId', 'revisionReferencia', 'titularSha256', 'consentimientoSha256', 'versionCondiciones', 'payloadSha256'} or any((type(binding[k]) is not str or str(UUID(binding[k])) != binding[k] for k in ('solicitudId', 'revisionReferencia'))) or any((type(binding[k]) is not str or not re.fullmatch('[a-f0-9]{64}', binding[k]) for k in ('titularSha256', 'consentimientoSha256', 'versionCondiciones', 'payloadSha256'))):
            raise ValueError('COTIZACION_VINCULO_INVALIDO')
        self._radicacion_binding = dict(binding)
        self._cotizacion_cache = None

    def _token_cotizacion(self, payload, request_id):
        import hashlib, json, time
        from uuid import UUID
        binding = getattr(self, '_radicacion_binding', None)
        if binding is None:
            return self._token(request_id)
        if payload.get('solicitudId') != binding['solicitudId'] or type(payload.get('versionCondiciones')) is not str or (not re.fullmatch('[a-f0-9]{64}', payload['versionCondiciones'])) or (str(UUID(request_id)) != request_id):
            raise ValueError('COTIZACION_VINCULO_INVALIDO')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if binding['versionCondiciones'] != payload['versionCondiciones'] or binding['payloadSha256'] != digest:
            raise ValueError('COTIZACION_VINCULO_INVALIDO')
        claim = {**binding, 'requestId': request_id}
        cache = getattr(self, '_cotizacion_cache', None)
        if cache and cache[0] == claim and (time.monotonic() < cache[2]):
            return cache[1]
        token = self._token(request_id, cotizacion=claim)
        self._cotizacion_cache = (claim, token, time.monotonic() + 20)
        return token

    def cotizar(self, payload, request_id):
        response = self.session.post(self.configuracion.base_url.rstrip('/') + ENDPOINT, json=payload, headers={'Authorization': 'Bearer ' + self._token_cotizacion(payload, request_id), 'X-Request-ID': request_id, 'Accept': 'application/json'}, timeout=self.configuracion.timeout, allow_redirects=False, verify=True)
        try:
            if response.status_code not in (200, 503) or response.headers.get('X-Request-ID') != request_id:
                raise ValueError('COTIZACION_HTTP_NO_DISPONIBLE')
            result = validar_respuesta(self._json(response), payload, request_id)
            if response.status_code == 503 and result['disponible']:
                raise ValueError('COTIZACION_RESPUESTA_INVALIDA')
            return result
        finally:
            response.close()
