"""Intent-specific central-bureau authorization; never a PDF signature or sender."""

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
from io import BytesIO
import json
import re
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from pypdf import PdfReader

from apps.documentos.formularios_credihoy import FORMATOS, recuperar_formato
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import ConsentimientoConsumo, SolicitudConsumo
from apps.xcore_consumo.services.consentimiento_integridad import build_consent_hash
from apps.xcore_consumo.services.otp_crypto import OTPCryptoError, decrypt_text, encrypt_text
from .autoservicio import leer_complemento_personal, validar_vinculo_consumo
from .consentimiento_real import obtener_autogestion, verificar_autorizacion_crediticia_personal

KEY = 'credihoy_autorizacion_centrales_v1'
VERSION = 'CENTRALES_OTP_V1'
PROPOSITO = 'CONSULTA_CENTRALES'
TEXTO_VERSION = 'COLO-FO.023:v10'
SALT_PRESENTACION = 'credihoy-centrales-presentacion-v1'
SALT_INTENCION = 'credihoy-centrales-intencion-v1'
SALT_PROVEEDOR = 'centrales-otp-proveedor-v1'
DOMINIO_EVIDENCIA = b'credihoy-centrales-evidencia-v1'
MAX_AGE = 600
VINCULO_KEYS = frozenset({
    'version', 'proposito', 'solicitudId', 'operacionId', 'sesionReferencia',
    'titularSha256', 'documentoOperacionId', 'pdfSha256', 'textoVersion',
    'textoSha256', 'aceptadoEn', 'vigenteHasta',
})
RESULTADO_KEYS = frozenset({
    'vinculo', 'estado', 'verificationReferencia', 'operacionVerify',
    'confirmadoEn', 'estadoProveedor',
})
ESTADOS = {'ENVIADO', 'CONFIRMADA', 'RECHAZADA', 'INDETERMINADA'}
FINALES = {'OTP_CONFIRMADA', 'RECHAZADA'}
EXPIRACION_EVENTO = 'credihoy_centrales_expiracion_conciliada_v1'
ARCHIVO_KEY = 'credihoy_centrales_intentos_conservados_v1'
CONCILIACIONES_KEY = 'credihoy_centrales_conciliaciones_v1'


@dataclass(frozen=True)
class AutorizacionCentralesVerificada:
    metodo: str
    referencia_verificacion: str
    texto_sha256: str
    pdf_sha256: str
    confirmado_en: datetime
    vigente_hasta: datetime


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError('CENTRALES_UUID_INVALIDO')
    return value


def _hash(value):
    if type(value) is not str or re.fullmatch('[a-f0-9]{64}', value) is None:
        raise ValueError('CENTRALES_HASH_INVALIDO')
    return value


def _fecha(value):
    if type(value) is not str:
        raise ValueError('CENTRALES_FECHA_INVALIDA')
    result = datetime.fromisoformat(value)
    if not timezone.is_aware(result):
        raise ValueError('CENTRALES_FECHA_SIN_ZONA')
    return result


def _habilitar():
    if getattr(settings, 'CREDIHOY_CENTRALES_OTP_ENABLED', False) is not True:
        raise OperacionError('CENTRALES_OTP_NO_HABILITADO')


def _limite(identity, now):
    _uuid(identity['_sesionReferencia'])
    limit = min(_fecha(identity['_sesionExpiraAt']),
                _fecha(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA))
    # The responsible explicitly authorized an independent purpose scope. The
    # existing authorization service enforces the own UUID and signed live session.
    if (identity.get('_scopeAutorizacion')!='solicitudes:autorizar'
            or not getattr(settings,'CREDIHOY_AUTORIZACION_TITULAR_ENABLED',False)):
        raise ValueError('CENTRALES_ALCANCE_PERSONAL_REQUERIDO')
    if limit <= now:
        raise ValueError('CENTRALES_SESION_VENCIDA')
    return min(limit, now + timedelta(seconds=MAX_AGE))


def _cargar_token(token, salt):
    if type(token) is not str or not token or len(token) > 16384:
        raise ValueError('CENTRALES_TOKEN_REQUERIDO')
    try:
        value = signing.loads(token, key=settings.CREDIHOY_TITULAR_SECRET,
                              salt=salt, max_age=MAX_AGE, fallback_keys=[])
    except signing.BadSignature:
        raise ValueError('CENTRALES_TOKEN_INVALIDO_O_VENCIDO') from None
    if type(value) is not dict:
        raise ValueError('CENTRALES_TOKEN_INVALIDO')
    return value


def _token(payload, salt):
    return signing.dumps(payload, key=settings.CREDIHOY_TITULAR_SECRET, salt=salt)


def _validar_vinculo(binding, now, *, vigente=True):
    if type(binding) is not dict or set(binding) != VINCULO_KEYS:
        raise ValueError('CENTRALES_VINCULO_INVALIDO')
    if (binding['version'] != VERSION or binding['proposito'] != PROPOSITO
            or binding['textoVersion'] != TEXTO_VERSION):
        raise ValueError('CENTRALES_PROPOSITO_INVALIDO')
    for name in ('solicitudId', 'operacionId', 'sesionReferencia', 'documentoOperacionId'):
        _uuid(binding[name])
    for name in ('titularSha256', 'pdfSha256', 'textoSha256'):
        _hash(binding[name])
    accepted, until = _fecha(binding['aceptadoEn']), _fecha(binding['vigenteHasta'])
    if (not accepted <= now or not accepted < until <= accepted + timedelta(seconds=MAX_AGE)
            or vigente and now >= until):
        raise ValueError('CENTRALES_VINCULO_VENCIDO_O_INVALIDO')


def _base(case, consent):
    """Public dossier checks also usable by a backend without the owner's AES key."""
    validar_vinculo_consumo(case)
    evidence = consent.evidencia
    if (str(consent.solicitud_id) != str(case.pk) or consent.aceptado is not True
            or consent.otp_id is not None or consent.version != TEXTO_VERSION
            or type(consent.text_snapshot) is not str or not consent.text_snapshot.strip()
            or consent.text_hash != build_consent_hash(consent.version, consent.text_snapshot)
            or not timezone.is_aware(consent.fecha_aceptacion)
            or consent.fecha_aceptacion > timezone.now()
            or evidence['workflow'] != 'COLO023_AUTOGESTION_V1'
            or evidence['proposito'] != 'AUTORIZACION_CENTRALES_PRECONSULTA'
            or evidence['otp9AReutilizado'] is not False
            or evidence['originalSha256'] != FORMATOS['COLO-FO.023']['sha256']):
        raise ValueError('CENTRALES_CONSENTIMIENTO_NO_INTEGRO')
    doc = _uuid(evidence['documentoOperacionId'])
    _uuid(evidence['sesionReferencia'])
    _uuid(evidence['claveAceptacion'])
    metadata = case.consumo_detail.orchestration_data['credihoy_pdf_oficiales_v1'][doc]['metadata']
    holder = _sha(case.solicitante.numero_identificacion.encode('utf-8'))
    if (metadata['codigo'] != 'COLO-FO.023' or metadata['version'] != '10'
            or metadata['originalSha256'] != evidence['originalSha256']
            or metadata['pdfSha256'] != _hash(evidence['pdfSha256'])
            or metadata['identidadDocumentalReferencia'] != holder
            or metadata['contenidoNormativoModificado'] is not False
            or metadata['firmaAcreditada'] is not False):
        raise ValueError('CENTRALES_DOCUMENTO_NO_INTEGRO')
    return {'version': VERSION, 'proposito': PROPOSITO, 'solicitudId': _uuid(str(case.pk)),
            'titularSha256': holder, 'documentoOperacionId': doc,
            'pdfSha256': evidence['pdfSha256'], 'textoVersion': consent.version,
            'textoSha256': _sha(consent.text_snapshot.encode('utf-8'))}


def _contexto(sid, identity, *, lock=False):
    case, alias = obtener_autogestion(sid, identity, lock=lock)
    query = SolicitudConsumo.objects.using(alias)
    if lock:
        query = query.select_for_update()
    detail = query.get(solicitud=case)
    case.consumo_detail = detail
    consent = ConsentimientoConsumo.objects.using(alias).get(solicitud=case)
    base = _base(case, consent)
    if consent.firmado is not False:
        raise ValueError('CENTRALES_NO_SUSTITUYE_FIRMA_EXISTENTE')
    return case, alias, detail, consent, base


def _pdf_original(case, consent, identity):
    evidence = consent.evidencia
    pdf, metadata = recuperar_formato(case.pk, actor=None, titular=identity,
                                     operacion_id=evidence['documentoOperacionId'])
    stored = case.consumo_detail.orchestration_data['credihoy_pdf_oficiales_v1']
    if (metadata != stored[evidence['documentoOperacionId']]['metadata']
            or _sha(pdf) != evidence['pdfSha256']):
        raise ValueError('CENTRALES_PDF_CAMBIO')
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(pdf)).pages)
    if text != consent.text_snapshot:
        raise ValueError('CENTRALES_SNAPSHOT_NO_CORRESPONDE_AL_PDF')
    return pdf


def _validar_presentacion(presentation, base, operation, identity, now):
    expected = (VINCULO_KEYS - {'aceptadoEn'}) | {'fechaPresentacion'}
    if type(presentation) is not dict or set(presentation) != expected:
        raise ValueError('CENTRALES_PRESENTACION_INVALIDA')
    if (any(presentation[k] != v for k, v in base.items())
            or presentation['operacionId'] != operation
            or presentation['sesionReferencia'] != _uuid(identity['_sesionReferencia'])):
        raise ValueError('CENTRALES_PRESENTACION_DE_OTRA_INTENCION')
    presented, until = _fecha(presentation['fechaPresentacion']), _fecha(presentation['vigenteHasta'])
    if not presented <= now < until <= min(presented + timedelta(seconds=MAX_AGE), _limite(identity, now)):
        raise ValueError('CENTRALES_PRESENTACION_VENCIDA')


def obtener_clave_publica_owner_b64():
    """Explicit owner provisioning helper. Never returns or persists private material."""
    return base64.b64encode(_clave_owner().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii')


def _clave_owner():
    secret = getattr(settings, 'CREDIHOY_TITULAR_SECRET', '')
    if type(secret) is not str or not secret:
        raise ValueError('CENTRALES_SECRETO_OWNER_REQUERIDO')
    seed = hmac.new(secret.encode('utf-8'), DOMINIO_EVIDENCIA, hashlib.sha256).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def _clave_publica(*, owner=False):
    encoded = getattr(settings, 'CREDIHOY_CENTRALES_CLAVE_PUBLICA_B64', '')
    if not encoded and owner:
        return _clave_owner().public_key()
    if type(encoded) is not str or not encoded:
        raise ValueError('CENTRALES_CLAVE_PUBLICA_REQUERIDA')
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded, validate=True))


def _mensaje_evidencia(payload):
    return DOMINIO_EVIDENCIA + b'\x00' + _canonical(payload)


def _sellar(case, binding, result):
    until = _fecha(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA)
    if until <= timezone.now():
        raise ValueError('CENTRALES_VIGENCIA_AUTORIZACION_VENCIDA')
    payload = {**binding, 'operacionEnvioOriginal': case.metadatos['envio']['operacionId'],
               'autorizacionVigenteHasta': until.isoformat(),
               **{k: result[k] for k in RESULTADO_KEYS - {'vinculo'}}}
    signature = _clave_owner().sign(_mensaje_evidencia(payload))
    # A configured public key must agree with the owner's derivation.
    _clave_publica(owner=True).verify(signature, _mensaje_evidencia(payload))
    return {'algoritmo': 'Ed25519', 'payload': payload,
            'firmaBase64': base64.b64encode(signature).decode('ascii')}


def _verificar_comprobante(case, consent, journal, *, vigente=True, owner=False):
    proof = journal['comprobante']
    if (type(proof) is not dict or set(proof) != {'algoritmo', 'payload', 'firmaBase64'}
            or proof['algoritmo'] != 'Ed25519'):
        raise ValueError('CENTRALES_COMPROBANTE_INVALIDO')
    payload = proof['payload']
    expected = VINCULO_KEYS | {'operacionEnvioOriginal', 'autorizacionVigenteHasta'} | (RESULTADO_KEYS - {'vinculo'})
    if type(payload) is not dict or set(payload) != expected:
        raise ValueError('CENTRALES_COMPROBANTE_INVALIDO')
    signature = base64.b64decode(proof['firmaBase64'], validate=True)
    _clave_publica(owner=owner).verify(signature, _mensaje_evidencia(payload))
    now = timezone.now()
    binding = {k: payload[k] for k in VINCULO_KEYS}
    _validar_vinculo(binding, now, vigente=False)
    authorization_until = _fecha(payload['autorizacionVigenteHasta'])
    if (journal['version'] != VERSION or journal['estado'] != 'OTP_CONFIRMADA'
            or journal['vinculo'] != binding or consent.firmado is not False
            or any(payload[k] != v for k, v in _base(case, consent).items())
            or payload['operacionEnvioOriginal'] != case.metadatos['envio']['operacionId']
            or payload['estado'] != 'CONFIRMADA' or payload['estadoProveedor'] != 'approved'
            or authorization_until <= _fecha(payload['confirmadoEn'])
            or vigente and authorization_until <= now
            or not consent.fecha_aceptacion <= _fecha(binding['aceptadoEn'])
            or not _fecha(binding['aceptadoEn']) <= _fecha(payload['confirmadoEn']) <= now
            or _fecha(payload['confirmadoEn']) > _fecha(binding['vigenteHasta'])):
        raise ValueError('CENTRALES_CONFIRMACION_NO_ACREDITADA')
    _uuid(payload['operacionVerify'])
    _hash(payload['verificationReferencia'])
    return AutorizacionCentralesVerificada(
        metodo='OTP', referencia_verificacion=payload['verificationReferencia'],
        texto_sha256=payload['textoSha256'], pdf_sha256=payload['pdfSha256'],
        confirmado_en=_fecha(payload['confirmadoEn']), vigente_hasta=authorization_until)


def _estado(result):
    return 'OTP_CONFIRMADA' if result['estado'] == 'CONFIRMADA' else result['estado']


def _validar_resultado(result, binding, now):
    if type(result) is not dict or set(result) != RESULTADO_KEYS or result['vinculo'] != binding:
        raise ValueError('CENTRALES_RESULTADO_DE_OTRA_INTENCION')
    if type(result['estado']) is not str or result['estado'] not in ESTADOS:
        raise ValueError('CENTRALES_ESTADO_INVALIDO')
    _validar_vinculo(binding, now, vigente=False)
    _uuid(result['operacionVerify'])
    if result['verificationReferencia'] is not None or result['estado'] != 'INDETERMINADA':
        _hash(result['verificationReferencia'])
    if result['estado'] == 'CONFIRMADA':
        if (result['estadoProveedor'] != 'approved'
                or not _fecha(binding['aceptadoEn']) <= _fecha(result['confirmadoEn']) <= now
                or _fecha(result['confirmadoEn']) > _fecha(binding['vigenteHasta'])):
            raise ValueError('CENTRALES_PROVEEDOR_NO_CONFIRMADO')
    elif (result['confirmadoEn'] is not None or result['estadoProveedor'] not in ('pending', None)
          or result['estado'] == 'ENVIADO' and result['estadoProveedor'] != 'pending'):
        raise ValueError('CENTRALES_RESULTADO_NO_CONFIRMADO_INVALIDO')


def _transicion(previous, following):
    if previous in FINALES or following == 'PENDIENTE_OTP':
        raise ValueError('CENTRALES_ESTADO_NO_PUEDE_RETROCEDER')


def _anexar(journal, state, result, now):
    entries = journal['registros']
    event = {'vinculo': deepcopy(journal['vinculo']), 'estado': state,
             'registradoEn': now.isoformat(), 'resultado': deepcopy(result),
             'anteriorSha256': entries[-1]['sha256'] if entries else None}
    raw = _canonical(event)
    entries.append({'sha256': _sha(raw), 'contenidoCifrado': encrypt_text(raw.decode('utf-8'))})
    journal['estado'] = state


def _leer_journal(case, consent):
    journal = case.consumo_detail.orchestration_data.get(KEY)
    if journal is None:
        return None, []
    try:
        if (type(journal) is not dict or set(journal) != {'version', 'vinculo', 'estado',
                'registros', 'comprobante', 'evidenciaCifrada'} or journal['version'] != VERSION
                or type(journal['registros']) is not list or not 1 <= len(journal['registros']) <= 20):
            raise ValueError
        now = timezone.now()
        _validar_vinculo(journal['vinculo'], now, vigente=False)
        if any(journal['vinculo'][k] != v for k, v in _base(case, consent).items()):
            raise ValueError
        events, previous, provider = [], None, None
        for entry in journal['registros']:
            if type(entry) is not dict or set(entry) != {'sha256', 'contenidoCifrado'}:
                raise ValueError
            raw = decrypt_text(entry['contenidoCifrado'])
            event = json.loads(raw)
            if (type(event) is not dict or set(event) != {'vinculo', 'estado', 'registradoEn',
                    'resultado', 'anteriorSha256'} or _canonical(event).decode('utf-8') != raw
                    or not hmac.compare_digest(_hash(entry['sha256']), _sha(raw.encode('utf-8')))
                    or event['anteriorSha256'] != previous or event['vinculo'] != journal['vinculo']
                    or not _fecha(journal['vinculo']['aceptadoEn']) <= _fecha(event['registradoEn']) <= now
                    or events and _fecha(event['registradoEn']) < _fecha(events[-1]['registradoEn'])):
                raise ValueError
            if not events:
                presentation = event['resultado']['presentacion']
                if (event['estado'] != 'PENDIENTE_OTP' or set(event['resultado']) != {'presentacion'}
                        or set(presentation) != (VINCULO_KEYS - {'aceptadoEn'}) | {'fechaPresentacion'}
                        or {k: v for k, v in presentation.items() if k != 'fechaPresentacion'}
                        != {k: v for k, v in journal['vinculo'].items() if k != 'aceptadoEn'}
                        or not _fecha(presentation['fechaPresentacion']) <= _fecha(journal['vinculo']['aceptadoEn'])
                        or _fecha(presentation['vigenteHasta']) > _fecha(presentation['fechaPresentacion']) + timedelta(seconds=MAX_AGE)):
                    raise ValueError
            else:
                _validar_resultado(event['resultado'], journal['vinculo'], now)
                if event['estado'] != _estado(event['resultado']):
                    raise ValueError
                _transicion(events[-1]['estado'], event['estado'])
                current = event['resultado']['verificationReferencia']
                if provider is not None and current is not None and provider != current:
                    raise ValueError
                if current is not None:
                    provider = current
            events.append(event)
            previous = entry['sha256']
        if journal['estado'] != events[-1]['estado']:
            raise ValueError
        if journal['estado'] == 'OTP_CONFIRMADA':
            _verificar_comprobante(case, consent, journal, vigente=False, owner=True)
            if json.loads(decrypt_text(journal['evidenciaCifrada'])) != journal['comprobante']:
                raise ValueError
            payload = journal['comprobante']['payload']
            if any(payload[k] != v for k, v in events[-1]['resultado'].items() if k != 'vinculo'):
                raise ValueError
        elif journal['comprobante'] is not None or journal['evidenciaCifrada'] is not None:
            raise ValueError
        return deepcopy(journal), events
    except (KeyError, TypeError, ValueError, AttributeError, InvalidSignature, OTPCryptoError):
        raise ValueError('CENTRALES_JOURNAL_NO_INTEGRO') from None


def _dto(journal=None, *, presentation=None, base=None, operation=None, until=None):
    binding = journal['vinculo'] if journal else (base or {})
    proof = journal.get('comprobante') if journal else None
    payload = proof['payload'] if proof else {}
    return {'estado': journal['estado'] if journal else 'PENDIENTE_ACEPTACION',
            'operacionId': binding.get('operacionId', operation), 'proposito': PROPOSITO,
            'metodo': 'OTP', 'textoVersion': binding.get('textoVersion', TEXTO_VERSION),
            'textoSha256': binding.get('textoSha256'), 'pdfSha256': binding.get('pdfSha256'),
            'presentacion': presentation, 'confirmadoEn': payload.get('confirmadoEn'),
            'verificacionReferencia': payload.get('verificationReferencia'),
            'vigenteHasta': payload.get('autorizacionVigenteHasta', binding.get('vigenteHasta', until))}


def _aceptacion_response(journal):
    binding = deepcopy(journal['vinculo'])
    token = _token(binding, SALT_INTENCION) if journal['estado'] not in FINALES else None
    return {'vinculo': binding, 'presentacion': token,
            'centrales': _dto(journal, presentation=token)}


def _guardar(case, alias, detail, journal, event, now):
    detail.orchestration_data = {**detail.orchestration_data, KEY: journal}
    detail.save(using=alias, update_fields=['orchestration_data', 'updated_at'])
    AuditLog.objects.using(alias).create(
        solicitud=case, event=event, actor='titular:centrales',
        request_id=journal['vinculo']['operacionId'],
        payload={'estado': journal['estado'], 'metodo': 'OTP', 'firmado': False,
                 'registroSha256': journal['registros'][-1]['sha256'], 'registradoEn': now.isoformat()})


def _expiracion_conciliada(case, journal, alias, now):
    if (journal['estado'] != 'ENVIADO' or journal['comprobante'] is not None
            or _fecha(journal['vinculo']['vigenteHasta']) > now):
        return None
    stored = case.consumo_detail.orchestration_data.get(CONCILIACIONES_KEY, {}).get(journal['vinculo']['operacionId'])
    if stored is None:
        return None
    if type(stored) is not dict or set(stored) != {'sha256', 'contenidoCifrado'}:
        raise ValueError('CENTRALES_CONCILIACION_NO_INTEGRA')
    raw = decrypt_text(stored['contenidoCifrado']).encode()
    if _sha(raw) != stored['sha256']:
        raise ValueError('CENTRALES_CONCILIACION_NO_INTEGRA')
    proof = json.loads(raw)
    if (type(proof) is not dict or proof.get('version') != 1
            or proof.get('estadoProveedor') != 'expired'
            or proof.get('origen') != 'TWILIO_CONSOLE_CONFIRMADO_RESPONSABLE'
            or proof.get('solicitudId') != str(case.pk)
            or proof.get('operacionId') != journal['vinculo']['operacionId']
            or proof.get('journalSha256') != _sha(_canonical(journal))
            or proof.get('nuevoIntentoMaximo') != 1):
        raise ValueError('CENTRALES_CONCILIACION_NO_INTEGRA')
    return proof


def registrar_expiracion_conciliada(case, alias, evidencia):
    """Internal operator reconciliation, never an authorization or provider check."""
    expected = {'solicitudId', 'operacionId', 'verificationSha256', 'cuentaSid',
                'servicioSid', 'estadoProveedor', 'origen', 'referenciaFuente',
                'creadaProveedorEn', 'journalSha256'}
    if (type(evidencia) is not dict or set(evidencia) != expected
            or evidencia['solicitudId'] != str(case.pk)
            or evidencia['estadoProveedor'] != 'expired'
            or evidencia['origen'] != 'TWILIO_CONSOLE_CONFIRMADO_RESPONSABLE'
            or re.fullmatch(r'AC[a-fA-F0-9]{32}', evidencia['cuentaSid']) is None
            or re.fullmatch(r'VA[a-fA-F0-9]{32}', evidencia['servicioSid']) is None
            or not evidencia['referenciaFuente'].startswith('https://1console.twilio.com/account/'+evidencia['cuentaSid']+'/')):
        raise ValueError('CENTRALES_EXPIRACION_EVIDENCIA_INVALIDA')
    _hash(evidencia['verificationSha256']);_hash(evidencia['journalSha256'])
    source = re.fullmatch(r'https://1console\.twilio\.com/account/'+re.escape(evidencia['cuentaSid'])+
        r'/us1/verify/logs/(VE[a-fA-F0-9]{32})', evidencia['referenciaFuente'])
    if source is None or _sha(source.group(1).encode()) != evidencia['verificationSha256']:
        raise ValueError('CENTRALES_EXPIRACION_REFERENCIA_DIFIERE')
    with transaction.atomic(using=alias):
        detail = SolicitudConsumo.objects.using(alias).select_for_update().get(solicitud=case)
        case.consumo_detail = detail
        consent = ConsentimientoConsumo.objects.using(alias).get(solicitud=case)
        journal, events = _leer_journal(case, consent)
        now = timezone.now()
        if (not journal or journal['estado'] != 'ENVIADO' or journal['comprobante'] is not None
                or journal['vinculo']['operacionId'] != evidencia['operacionId']
                or _sha(_canonical(journal)) != evidencia['journalSha256']
                or _fecha(journal['vinculo']['vigenteHasta']) > now
                or not _fecha(journal['vinculo']['aceptadoEn']) - timedelta(seconds=5)
                    <= _fecha(evidencia['creadaProveedorEn']) <= _fecha(journal['vinculo']['vigenteHasta'])
                or not events[1:] or any(e['estado'] != 'ENVIADO' for e in events[1:])
                or any(e['resultado']['verificationReferencia'] != evidencia['verificationSha256'] for e in events[1:])):
            raise ValueError('CENTRALES_EXPIRACION_NO_CONCILIA')
        prior = _expiracion_conciliada(case, journal, alias, now)
        if prior is not None:
            if any(prior.get(k) != v for k, v in evidencia.items()):
                raise ValueError('CENTRALES_CONCILIACION_EXISTENTE_DIFIERE')
            return prior
        proof = {**evidencia, 'version': 1, 'nuevoIntentoMaximo': 1, 'conciliadoEn': now.isoformat()}
        raw = _canonical(proof)
        records = deepcopy(detail.orchestration_data.get(CONCILIACIONES_KEY, {}))
        records[journal['vinculo']['operacionId']] = {'sha256': _sha(raw),
            'contenidoCifrado': encrypt_text(raw.decode())}
        detail.orchestration_data = {**detail.orchestration_data, CONCILIACIONES_KEY: records}
        detail.save(using=alias, update_fields=['orchestration_data', 'updated_at'])
        AuditLog.objects.using(alias).create(solicitud=case, event=EXPIRACION_EVENTO,
            actor='operador:conciliacion-proveedor', request_id=journal['vinculo']['operacionId'], payload=proof)
        return proof


def _reintento_personal(case, journal, identity, alias, now):
    proof = _expiracion_conciliada(case, journal, alias, now)
    if proof is None or identity['_sesionReferencia'] == journal['vinculo']['sesionReferencia']:
        return False
    _limite(identity, now)
    return True


def presentar(sid, identity, operacion_id):
    """Read the already accepted original; the presentation is not a signature."""
    _habilitar()
    operation = _uuid(str(operacion_id))
    case, alias, _, consent, base = _contexto(sid, identity)
    pdf = _pdf_original(case, consent, identity)
    now = timezone.now()
    journal, events = _leer_journal(case, consent)
    if journal and _reintento_personal(case, journal, identity, alias, now):
        if operation == journal['vinculo']['operacionId']:
            raise ValueError('CENTRALES_REINTENTO_REQUIERE_NUEVA_OPERACION')
        journal = None
    if journal:
        if journal['vinculo']['operacionId'] != operation:
            raise ValueError('CENTRALES_OTRA_OPERACION_EXISTENTE')
        presentation = events[0]['resultado']['presentacion']
    else:
        presentation = {**base, 'operacionId': operation,
                        'sesionReferencia': _uuid(identity['_sesionReferencia']),
                        'fechaPresentacion': now.isoformat(), 'vigenteHasta': _limite(identity, now).isoformat()}
    _validar_presentacion(presentation, base, operation, identity, timezone.now())
    _habilitar()
    token = _token(presentation, SALT_PRESENTACION)
    documento = {'solicitudId': str(case.pk), 'codigo': 'COLO-FO.023', 'version': '10',
                 'operacionId': base['documentoOperacionId'], 'pdfSha256': base['pdfSha256'],
                 'pdfBase64': base64.b64encode(pdf).decode('ascii'), 'texto': consent.text_snapshot,
                 'presentacion': token, 'proposito': 'AUTORIZACION_CENTRALES_PRECONSULTA',
                 'firmado': False, 'consultasHabilitadas': False}
    return {'presentacion': token, 'documento': documento,
            'centrales': _dto(journal, presentation=token, base=base,
                              operation=operation, until=presentation['vigenteHasta'])}


def aceptar(sid, identity, operacion_id, presentacion, acepta):
    """Internal binding is for relay only; HTTP must project the centrales DTO."""
    _habilitar()
    if acepta is not True:
        raise ValueError('CENTRALES_ACEPTACION_EXPLICITA_REQUERIDA')
    operation = _uuid(str(operacion_id))
    presentation = _cargar_token(presentacion, SALT_PRESENTACION)
    _, alias = obtener_autogestion(sid, identity)
    with transaction.atomic(using=alias):
        case, _, detail, consent, base = _contexto(sid, identity, lock=True)
        _habilitar()
        now = timezone.now()
        _validar_presentacion(presentation, base, operation, identity, now)
        _pdf_original(case, consent, identity)
        if leer_complemento_personal(case) is None:
            from .autoservicio import validar_procedencia_formulario
            try:validar_procedencia_formulario(case,detail.form_data)
            except (ValueError,KeyError,TypeError,AttributeError):
                raise ValueError('CENTRALES_COMPLEMENTO_PERSONAL_REQUERIDO') from None
        journal, events = _leer_journal(case, consent)
        if journal and _reintento_personal(case, journal, identity, alias, now):
            previous = journal['vinculo']['operacionId']
            if operation == previous:
                raise ValueError('CENTRALES_REINTENTO_REQUIERE_NUEVA_OPERACION')
            archive = deepcopy(detail.orchestration_data.get(ARCHIVO_KEY, {}))
            if type(archive) is not dict or previous in archive or len(archive) >= 5:
                raise ValueError('CENTRALES_ARCHIVO_EXISTENTE_CONCILIAR')
            archive[previous] = {'journal': deepcopy(journal), 'journalSha256': _sha(_canonical(journal)),
                'conciliacion': _expiracion_conciliada(case, journal, alias, now), 'nuevoIntento': operation}
            detail.orchestration_data = {**detail.orchestration_data, ARCHIVO_KEY: archive}
            journal = None
        if journal:
            if (journal['vinculo']['operacionId'] != operation
                    or events[0]['resultado']['presentacion'] != presentation):
                raise ValueError('CENTRALES_ACEPTACION_EXISTENTE_NO_SOBRESCRIBIR')
            return _aceptacion_response(journal)
        if (detail.estado != 'CONSENTIMIENTO_PENDIENTE'
                or any(k in detail.orchestration_data for k in ('credihoy_unico_hdc_actual', 'credihoy_unico_funcional'))):
            raise ValueError('CENTRALES_ETAPA_NO_DISPONIBLE')
        now = timezone.now()
        _validar_presentacion(presentation, base, operation, identity, now)
        binding = {k: v for k, v in presentation.items() if k != 'fechaPresentacion'}
        binding['aceptadoEn'] = now.isoformat()
        _validar_vinculo(binding, now)
        journal = {'version': VERSION, 'vinculo': binding, 'estado': 'PENDIENTE_OTP',
                   'registros': [], 'comprobante': None, 'evidenciaCifrada': None}
        _anexar(journal, 'PENDIENTE_OTP', {'presentacion': presentation}, now)
        _guardar(case, alias, detail, journal, 'credihoy_centrales_aceptadas', now)
        return _aceptacion_response(journal)


def registrar_resultado(sid, identity, comprobacion):
    """Trusted relay token only. No browser booleans, provider IO or access OTP."""
    _habilitar()
    result = _cargar_token(comprobacion, SALT_PROVEEDOR)
    _, alias = obtener_autogestion(sid, identity)
    with transaction.atomic(using=alias):
        case, _, detail, consent, _ = _contexto(sid, identity, lock=True)
        _habilitar()
        _pdf_original(case, consent, identity)
        journal, events = _leer_journal(case, consent)
        if journal is None:
            raise ValueError('CENTRALES_ACEPTACION_REQUERIDA')
        now = timezone.now()
        binding = journal['vinculo']
        _validar_vinculo(binding, now)
        if (binding['sesionReferencia'] != identity['_sesionReferencia']
                or _fecha(binding['vigenteHasta']) > _limite(identity, now)):
            raise ValueError('CENTRALES_RESULTADO_DE_OTRA_SESION')
        _validar_resultado(result, binding, now)
        if any(event['resultado'] == result for event in events[1:]):
            return {'centrales': _dto(journal)}
        following = _estado(result)
        _transicion(journal['estado'], following)
        if len(events) >= 20:
            raise ValueError('CENTRALES_LIMITE_EVENTOS')
        for previous in (event['resultado'] for event in events[1:]):
            if (previous['verificationReferencia'] is not None and result['verificationReferencia'] is not None
                    and previous['verificationReferencia'] != result['verificationReferencia']):
                raise ValueError('CENTRALES_OTRO_OTP_NO_ADMITIDO')
        _anexar(journal, following, result, now)
        if following == 'OTP_CONFIRMADA':
            journal['comprobante'] = _sellar(case, binding, result)
            journal['evidenciaCifrada'] = encrypt_text(_canonical(journal['comprobante']).decode('utf-8'))
            _verificar_comprobante(case, consent, journal, owner=True)
        _guardar(case, alias, detail, journal, 'credihoy_centrales_resultado_registrado', now)
        if following == 'OTP_CONFIRMADA':
            from .continuacion_eventos import registrar
            registrar(case, alias, journal)
        return {'centrales': _dto(journal)}


def consultar(sid, identity):
    """Safe DTO with a resumable same-session intent, never provider tokens or PII."""
    case, alias, _, consent, base = _contexto(sid, identity)
    journal, _ = _leer_journal(case, consent)
    if journal and _reintento_personal(case, journal, identity, alias, timezone.now()):
        journal = None
    token = None
    if journal and journal['estado'] not in FINALES:
        binding = journal['vinculo']
        now = timezone.now()
        if (binding['sesionReferencia'] == identity['_sesionReferencia']
                and now < _fecha(binding['vigenteHasta']) <= _limite(identity, now)):
            token = _token(binding, SALT_INTENCION)
    dto = _dto(journal, base=base, presentation=token)
    if journal is None:
        dto['estado'] = 'SIN_ACEPTACION'
    return {'centrales': dto}


def verificar_autorizacion_centrales_personal(case, consent):
    """Read-only HDC integration contract; this function does not open its gate."""
    try:
        journal = case.consumo_detail.orchestration_data.get(KEY)
        if type(journal) is dict and journal.get('estado') == 'OTP_CONFIRMADA':
            return _verificar_comprobante(case, consent, journal)
        verified = verificar_autorizacion_crediticia_personal(case, consent)
        return AutorizacionCentralesVerificada(
            metodo='AUTENTIC', referencia_verificacion=verified.referencia_verificacion,
            texto_sha256=_sha(consent.text_snapshot.encode('utf-8')),
            pdf_sha256=verified.original_sha256, confirmado_en=verified.firmado_en,
            vigente_hasta=verified.vigente_hasta)
    except (KeyError, TypeError, ValueError, AttributeError, InvalidSignature):
        raise OperacionError('AUTORIZACION_CENTRALES_PERSONAL_VERIFICABLE_REQUERIDA') from None
