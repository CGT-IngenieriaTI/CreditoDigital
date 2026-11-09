"""Condiciones oficiales aportadas por operador; no calcula cuotas ni tasas."""

import base64
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re
from zoneinfo import ZoneInfo


SID = 'a402121e-81d0-4759-8c4c-485f0787d45c'
UNIT = 'PORCENTAJE_NOMINAL_ANUAL_MES_VENCIDO'
MODEL = 'CREDIHOY_VAL_EMPLEADO'
SCOPE = 'RADICACION_ADMINISTRATIVA_PENDIENTE'
VERSION = 'CD6-CONDICIONES-1'
SIM_VERSION = 'CD6-CONDICIONES-SIMULACION-2'


class FichaError(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def huella(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instante():
    return datetime.now(ZoneInfo('America/Bogota'))


def number(value, field, *, zero=False):
    if type(value) is not str or not re.fullmatch(r'[0-9]{1,12}(\.[0-9]{1,2})?', value):
        raise FichaError(field + ':DECIMAL_EXPLICITO_REQUERIDO')
    result = Decimal(value)
    if result < 0 or (not zero and result == 0):
        raise FichaError(field + ':VALOR_INVALIDO')
    return format(result, '.2f')


def codigo(value, field):
    from incluirtec.digital_operations.contracts import ProtocolError, identifier
    try:
        return identifier(value)
    except ProtocolError:
        raise FichaError(field + ':REFERENCIA_O_CODIGO_INVALIDO') from None


def iso_date(value, field):
    try:
        if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise FichaError(field + ':FECHA_ISO_REQUERIDA') from None


def contexto(base):
    if (base['solicitudId'] != SID or base['producto']['codigo'] != '390'
            or base['producto']['modalidad'] != '111' or base['producto']['destino'] != 'CFA'
            or base['contextoLinix']['sucursal'] != '101'
            or base['contextoLinix']['parametros']['medioPago'] != '100'
            or base['credito']['periodicidad'] != 'MENSUAL'):
        raise FichaError('EXPEDIENTE_O_PERFIL_NO_COINCIDE')
    return {'solicitudId': SID, 'producto': '390', 'modalidad': '111', 'destino': 'CFA',
        'agencia': '101', 'nomina': '100', 'tipoNomina': 'N',
        'montoCOP': base['credito']['montoSolicitado'], 'plazoMeses': base['credito']['plazoMeses'],
        'periodicidad': 'MENSUAL', 'huellaBorrador': huella(base)}


def validar(ficha, base, dominios, *, now=None):
    now = now or instante()
    today = now.astimezone(ZoneInfo('America/Bogota')).date()
    expected = {'version', 'contexto', 'tasaNAMV', 'unidad', 'referenciaTasa',
        'cuotaContractual', 'periodoPagoInteres', 'capitalizacionTotal',
        'primeraCuota', 'evidencia', 'registroAdministrativoPendienteAdmitido'}
    version = ficha.get('version') if type(ficha) is dict else None
    expected |= ({'fechaSimulacion', 'claseReferenciaTasa', 'certificacionComercial'}
                 if version == SIM_VERSION else {'vigenteDesde', 'vigenteHasta'})
    if type(ficha) is not dict or set(ficha) != expected or version not in (VERSION, SIM_VERSION):
        raise FichaError('ESQUEMA_FICHA_INVALIDO')
    if ficha['contexto'] != contexto(base):
        raise FichaError('FICHA_NO_CORRESPONDE_AL_EXPEDIENTE')
    if ficha['unidad'] != UNIT:
        raise FichaError('UNIDAD_NAMV_NO_ACREDITADA')
    if ficha['registroAdministrativoPendienteAdmitido'] is not True:
        raise FichaError('ALCANCE_INSTITUCIONAL_PENDIENTE_NO_ADMITIDO')
    number(ficha['tasaNAMV'], 'tasaNAMV')
    if Decimal(ficha['tasaNAMV']) > 100:
        raise FichaError('tasaNAMV:FUERA_DE_RANGO')
    number(ficha['cuotaContractual'], 'cuotaContractual')
    number(ficha['capitalizacionTotal'], 'capitalizacionTotal', zero=True)
    codigo(ficha['referenciaTasa'], 'referenciaTasa')
    if version == VERSION:
        begin = iso_date(ficha['vigenteDesde'], 'vigenteDesde')
        end = iso_date(ficha['vigenteHasta'], 'vigenteHasta')
        if not begin <= today <= end:
            raise FichaError('CONDICION_DE_TASA_NO_VIGENTE')
    elif (iso_date(ficha['fechaSimulacion'], 'fechaSimulacion') > today
            or ficha['claseReferenciaTasa'] != 'SIMULACION_OFICIAL_LINIX'
            or ficha['certificacionComercial'] is not False):
        raise FichaError('SIMULACION_NO_ES_TARIFARIO_COMERCIAL')
    if (type(dominios) is not dict or ficha['periodoPagoInteres'] not in dominios
            or ficha['periodoPagoInteres'] != '1'):
        raise FichaError('PERIODICIDAD_NO_ADMITIDA_DM_PERPAG_Y_CONTRATO_MENSUAL')
    if iso_date(ficha['primeraCuota'], 'primeraCuota') <= today:
        raise FichaError('PRIMERA_CUOTA_NO_POSTERIOR_A_SOLICITUD')
    evidence = ficha['evidencia']
    if (type(evidence) is not dict or set(evidence) != {'referencia', 'operadorId', 'nombre',
            'sha256', 'contenidoBase64', 'oficialMismoExpediente', 'seguroYConceptosIncluidos'}
            or evidence['oficialMismoExpediente'] is not True
            or evidence['seguroYConceptosIncluidos'] is not True):
        raise FichaError('EVIDENCIA_OFICIAL_O_COBERTURA_DE_CONCEPTOS_FALTANTE')
    codigo(evidence['referencia'], 'evidencia.referencia')
    codigo(evidence['operadorId'], 'evidencia.operadorId')
    try:
        content = base64.b64decode(evidence['contenidoBase64'], validate=True)
    except (ValueError, TypeError):
        raise FichaError('EVIDENCIA_BASE64_INVALIDA') from None
    if (not 0 < len(content) <= 8_000_000 or hashlib.sha256(content).hexdigest() != evidence['sha256']
            or type(evidence['nombre']) is not str or not evidence['nombre']
            or any(c in evidence['nombre'] for c in '/\\\x00')):
        raise FichaError('EVIDENCIA_INTEGRIDAD_INVALIDA')
    return ficha


def request_final(base, ficha, dominios, intent, authorization, reference, *, now=None):
    now = now or instante()
    validar(ficha, base, dominios, now=now)
    result = deepcopy(base)
    credit, context = result['credito'], result['contextoLinix']
    credit['tasaNominalAnual'] = number(ficha['tasaNAMV'], 'tasaNAMV')
    credit['cuotaContractual'] = number(ficha['cuotaContractual'], 'cuotaContractual')
    context['fechaSolicitud'] = now.astimezone(ZoneInfo('America/Bogota')).strftime('%m/%d/%Y')
    context['fechaCuotaOrdinaria'] = iso_date(ficha['primeraCuota'], 'primeraCuota').strftime('%m/%d/%Y')
    context['parametros']['periodoPagoInteres'] = ficha['periodoPagoInteres']
    context['parametros']['valorTotalCapitalizacion'] = number(ficha['capitalizacionTotal'], 'capitalizacionTotal', zero=True)
    context['datosRadicacion']['promotor'] = None
    result['evidenciaTasa'].update(unidad=ficha['unidad'], referenciaCertificacion=ficha['referenciaTasa'])
    result['intencionId'] = intent
    result['autorizacionAdministrativa'].update(autorizacionId=authorization, referencia=reference)
    from incluirtec.digital_operations.contracts import normalize_request
    from incluirtec.digital_operations.credihoy import build_radication_xml
    normalized = normalize_request(result, allow_without_promoter_offline=True)
    xml = build_radication_xml(normalized, allow_without_promoter_offline=True)
    if 'evidenciaDecision' in normalized or '<A_CODIGO_PROMOTOR>' in xml or '<V_PROMOTOR>' in xml:
        raise FichaError('CONTRATO_ADMINISTRATIVO_O_SIN_PROMOTOR_INVALIDO')
    # Only formerly empty fields and the explicitly authorized promoter omission change.
    def preserved(old, new, prefix=''):
        for key, value in old.items():
            path = prefix + key
            if path == 'contextoLinix.datosRadicacion.promotor':
                continue
            if isinstance(value, dict):
                preserved(value, new[key], path + '.')
            elif value is not None and value != new[key]:
                # The existing normalizer emits "0.00" for the accredited "0".
                # Only this numeric representation may differ, never the value.
                if (path == 'contextoLinix.parametros.porcentajeCapitalizacion'
                        and number(value, path, zero=True) == number(new[key], path, zero=True)):
                    continue
                raise FichaError('VALOR_ACREDITADO_MODIFICADO:' + path)
    preserved(base, normalized)
    return normalized, xml
