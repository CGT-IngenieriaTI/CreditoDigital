"""Versioned durable contracts. Real execution and decision issuance stay blocked."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
from uuid import UUID

from django.db import models


VERSION = "credito-digital-radicacion-lab-v1"
CREDIHOY_VERSION = "credihoy-radicacion-controlada-v1"
CREDIHOY_ADMIN_VERSION = "credihoy-radicacion-administrativa-v1"
CREDIHOY_TECHNICAL_VERSION = "credihoy-radicacion-tecnica-v1"
ADMIN_SCOPE = "RADICACION_ADMINISTRATIVA_PENDIENTE"
ADMIN_MODEL = "CREDIHOY_VAL_EMPLEADO"
MANUAL_REVIEW_CODES = frozenset({
    "RESULTADOS_REMOTOS_CONTRADICTORIOS", "FLUJO_REMOTO_CONTRADICTORIO",
    "CREDIHOY_XML_CONTRADICTORIO", "CREDIHOY_CORRESPONDENCIA_INVALIDA",
    "CREDIHOY_CORTE_NO_VERIFICADO",
    "CREDIHOY_ACTIVIDAD_INESPERADA",
})
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
MONEY = re.compile(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,2})?\Z")


class State(models.TextChoices):
    PREPARED = "SOLICITUD_PREPARADA"
    EXECUTING = "RADICACION_EN_EJECUCION"
    RADICATED = "RADICADA"
    UNCERTAIN = "PENDIENTE_CONCILIACION"
    INVALID = "ERROR_VALIDACION"


class ProtocolError(Exception):
    def __init__(self, code, status=422):
        super().__init__(code)
        self.code = code
        self.status = status


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProtocolError("REQUEST_INVALIDO")
    return value


def uuid_text(value):
    if not isinstance(value, str):
        raise ProtocolError("REQUEST_INVALIDO")
    try:
        return str(UUID(value))
    except ValueError:
        raise ProtocolError("REQUEST_INVALIDO") from None


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def exact_object(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ProtocolError("REQUEST_INVALIDO")


@dataclass(frozen=True)
class DefinitiveDocumentEvidence:
    request_id: str
    intent_id: str
    flow_id: str
    sc: str
    holder_reference: str
    pagare: str
    calendar_sha256: str
    accepted_calendar_sha256: str
    terms_sha256: str
    accepted_terms_sha256: str
    prepared_at: str
    offer_sha256: str
    acceptance_sha256: str
    template_code: str


@dataclass(frozen=True)
class MainFirmaContractualVerificada:
    """Neutral copy of Main's private verifier output. No APP runtime imports."""
    solicitud_id: str
    envio_id: str
    original_sha256: str
    firmado_sha256: str
    transaccion_sha256: str
    firmado_en: datetime
    verificacion_sha256: str


@dataclass(frozen=True)
class SignatureVerificationEvidence:
    """Trusted verifier output, never an HTTP result or a transaction identifier.

    The offline port owns verification of identity, participation and signed
    content. This contract does not implement or certify a provider's format.
    """
    method: str
    verifier_reference: str
    identity_reference: str
    participation_reference: str
    signer_reference: str
    original_document_sha256: str
    signed_document_sha256: str
    verified_at: str
    evidence_sha256: str


@dataclass(frozen=True)
class RecoveredSigningEvidence:
    document: DefinitiveDocumentEvidence
    verification: SignatureVerificationEvidence
    receipt: dict
    main_verification: MainFirmaContractualVerificada
    original_document: bytes
    signed_document: bytes
    verification_payload: dict
    encrypted_custody_reference: str
    custody_sha256: str

    def record(self, operation, *, now):
        if (type(self.document) is not DefinitiveDocumentEvidence
                or type(self.verification) is not SignatureVerificationEvidence
                or type(self.receipt) is not dict
                or type(self.main_verification) is not MainFirmaContractualVerificada
                or type(self.original_document) is not bytes or not self.original_document
                or type(self.signed_document) is not bytes or not self.signed_document
                or type(self.verification_payload) is not dict or not self.verification_payload):
            raise ProtocolError('EVIDENCIA_FIRMA_INVALIDA', 409)
        verification = self.verification
        if (hashlib.sha256(self.original_document).hexdigest() != verification.original_document_sha256
                or hashlib.sha256(self.signed_document).hexdigest() != verification.signed_document_sha256
                or self.verification_payload.get('sha256') != verification.evidence_sha256
                or digest(canonical({k:v for k,v in self.verification_payload.items()
                                     if k not in {'sha256', 'verificadoEn'}}))
                    != verification.evidence_sha256):
            raise ProtocolError('INTEGRIDAD_DOCUMENTO_FIRMADO_INVALIDA', 409)
        main = asdict(self.main_verification)
        try:
            main['firmado_en'] = self.main_verification.firmado_en.isoformat()
        except AttributeError:
            raise ProtocolError('EVIDENCIA_FIRMA_INVALIDA', 409) from None
        body = {'versionContrato': 'credihoy-postfirma-offline-v1', 'simulada': True,
                'document': asdict(self.document), 'verification': asdict(verification),
                'receipt': self.receipt, 'main_verification': main,
                'encrypted_custody_reference': self.encrypted_custody_reference,
                'custody_sha256': self.custody_sha256}
        record = {**body, 'sha256': digest(canonical(body))}
        validate_signing_record(record, operation, now=now)
        return record


def validate_main_projection(projection, *, document, main, now):
    """Consume the exact Main entry, not a flattened or channel-made receipt.

    Main hashes the projection before adding sha256/registradoEn. pdfSha256 is
    the SIGNED PDF. The nested binding has its own independent compact digest.
    Neither checksum nor firmaAcreditada is a private cryptographic verifier.
    """
    try:
        required = {'versionContrato', 'solicitudId', 'envioId', 'estado', 'firmaAcreditada',
                    'pendientes', 'vinculo', 'verificacionSha256', 'pdfSha256',
                    'autorizaContinuacionFinanciera', 'START', 'SC', 'sha256', 'registradoEn'}
        if type(projection) is not dict or set(projection) != required:
            raise ValueError()
        link = projection['vinculo']
        expected = {'solicitudId': document.request_id, 'ofertaSha256': document.offer_sha256,
                    'aceptacionSha256': document.acceptance_sha256, 'SC': document.sc,
                    'K_FLUJO': document.flow_id, 'intencionId': document.intent_id,
                    'calendarioSha256': document.calendar_sha256}
        if (type(link) is not dict or set(link) != set(expected) | {'sha256'}
                or any(link[k] != v for k,v in expected.items())
                or link['sha256'] != digest(canonical(expected))
                or projection['sha256'] != digest(canonical({k:v for k,v in projection.items()
                                                             if k not in {'sha256', 'registradoEn'}}))
                or projection['versionContrato'] != 'credihoy-formalizacion-autentic-v1'
                or projection['estado'] != 'FIRMA_CONTRACTUAL_VERIFICADA'
                or projection['firmaAcreditada'] is not True or projection['pendientes'] != []
                or projection['autorizaContinuacionFinanciera'] is not False
                or type(projection['START']) is not int or projection['START'] != 0
                or type(projection['SC']) is not int or projection['SC'] != 0
                or projection['solicitudId'] != document.request_id
                or projection['solicitudId'] != main.solicitud_id
                or uuid_text(projection['envioId']) != main.envio_id
                or main.solicitud_id != uuid_text(main.solicitud_id)
                or projection['pdfSha256'] != main.firmado_sha256
                or projection['verificacionSha256'] != main.verificacion_sha256):
            raise ValueError()
        signed = main.firmado_en
        registered = datetime.fromisoformat(projection['registradoEn'])
        if (not isinstance(signed, datetime) or signed.utcoffset() is None
                or registered.utcoffset() is None or not signed <= registered <= now):
            raise ValueError()
        for value in (main.original_sha256, main.firmado_sha256, main.transaccion_sha256,
                      main.verificacion_sha256):
            if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
                raise ValueError()
        return projection
    except (KeyError, TypeError, ValueError, AttributeError, ProtocolError):
        raise ProtocolError('EVIDENCIA_FIRMA_NO_VINCULADA', 409) from None


def recover_main_projection(*, history, envio_id, document, original, firmado, verification_report,
                            private_verifier, encrypted_custody_reference, now):
    """Existing trusted port calls this connector; it has no transport or sender.

    private_verifier must INVOKE Main's verifier over the recovered bytes and
    normalize its seven-field result to MainFirmaContractualVerificada. Never
    deserialize the typed result from channel JSON. Only the integration/test
    owner imports APP; this module does not know that package or its services.
    """
    try:
        key = uuid_text(envio_id)
        projection = history[key]
        main = private_verifier(original=original, firmado=firmado)
        if type(main) is not MainFirmaContractualVerificada or main.envio_id != key:
            raise ProtocolError('EVIDENCIA_FIRMA_INVALIDA', 409)
        validate_main_projection(projection, document=document, main=main, now=now)
        if (verification_report['sha256'] != main.verificacion_sha256
                or verification_report['solicitudId'] != main.solicitud_id
                or verification_report['envioId'] != key
                or verification_report['originalSha256'] != main.original_sha256
                or verification_report['firmadoSha256'] != main.firmado_sha256
                or datetime.fromisoformat(verification_report['firmadoEn']) != main.firmado_en):
            raise ProtocolError('EVIDENCIA_FIRMA_NO_VINCULADA', 409)
        verification = SignatureVerificationEvidence(
            method='MAIN_PRIVATE_CONTRACT_VERIFIER', verifier_reference=main.verificacion_sha256,
            identity_reference=document.holder_reference, participation_reference=main.transaccion_sha256,
            signer_reference=document.holder_reference, original_document_sha256=main.original_sha256,
            signed_document_sha256=main.firmado_sha256, verified_at=verification_report['verificadoEn'],
            evidence_sha256=main.verificacion_sha256)
        return RecoveredSigningEvidence(document=document, verification=verification,
            receipt=projection, main_verification=main, original_document=original, signed_document=firmado,
            verification_payload=verification_report, encrypted_custody_reference=encrypted_custody_reference,
            custody_sha256=main.firmado_sha256)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProtocolError('EVIDENCIA_FIRMA_INVALIDA', 409) from None


def validate_signing_record(record, operation, *, now):
    """Recheck immutable journal evidence; a checksum is not authentication."""
    try:
        if (set(record) != {'versionContrato', 'simulada', 'document', 'verification', 'receipt', 'main_verification',
                            'encrypted_custody_reference', 'custody_sha256', 'sha256'}
                or record['versionContrato'] != 'credihoy-postfirma-offline-v1'
                or record['simulada'] is not True
                or record['sha256'] != digest(canonical({k:v for k,v in record.items() if k != 'sha256'}))):
            raise ValueError()
        document = DefinitiveDocumentEvidence(**record['document'])
        verification = SignatureVerificationEvidence(**record['verification'])
        main = MainFirmaContractualVerificada(**{**record['main_verification'],
            'firmado_en': datetime.fromisoformat(record['main_verification']['firmado_en'])})
        validate_main_projection(record['receipt'], document=document, main=main, now=now)
        if (document.request_id != str(operation.request_id)
                or document.intent_id != str(operation.intent_id)
                or document.flow_id != operation.flow_id or document.sc != operation.application_number
                or operation.application_type != 'SC'
                or document.holder_reference != digest(operation.payload['titular']['numeroDocumento'])
                or not isinstance(document.pagare, str) or not re.fullmatch('[1-9][0-9]*', document.pagare)
                or document.calendar_sha256 != document.accepted_calendar_sha256
                or document.terms_sha256 != document.accepted_terms_sha256
                or document.terms_sha256 != digest(canonical(operation.payload['credito']))
                or verification.signer_reference != document.holder_reference
                or document.template_code != 'COLO001'
                or main.original_sha256 != verification.original_document_sha256
                or main.firmado_sha256 != verification.signed_document_sha256
                or main.verificacion_sha256 != verification.evidence_sha256
                or main.transaccion_sha256 != verification.participation_reference
                or record['custody_sha256'] != verification.signed_document_sha256):
            raise ValueError()
        for value in (document.holder_reference, document.calendar_sha256, document.terms_sha256,
                      document.offer_sha256, document.acceptance_sha256, verification.evidence_sha256,
                      verification.original_document_sha256, verification.signed_document_sha256):
            if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
                raise ValueError()
        for value in (verification.method, verification.verifier_reference,
                      verification.identity_reference, verification.participation_reference,
                      record['encrypted_custody_reference']):
            identifier(value)
        prepared = datetime.fromisoformat(document.prepared_at)
        verified = datetime.fromisoformat(verification.verified_at)
        if (prepared.utcoffset() is None or verified.utcoffset() is None
                or now.utcoffset() is None or operation.attempted_at is None
                or not operation.attempted_at <= prepared <= main.firmado_en <= verified <= now):
            raise ValueError()
        return record
    except (KeyError, ValueError, TypeError, AttributeError, ProtocolError):
        raise ProtocolError('EVIDENCIA_FIRMA_NO_VINCULADA', 409) from None


def amount(value):
    if not isinstance(value, str) or not MONEY.fullmatch(value) or Decimal(value) <= 0:
        raise ProtocolError("REQUEST_INVALIDO")
    return format(Decimal(value), ".2f")


def normalize_request(value, *, allow_without_promoter_offline=False):
    if type(allow_without_promoter_offline) is not bool:
        raise ProtocolError("REQUEST_INVALIDO")
    if not isinstance(value, dict):
        raise ProtocolError("REQUEST_INVALIDO")
    version = value.get("versionContrato")
    administrative = version == CREDIHOY_ADMIN_VERSION
    technical = version == CREDIHOY_TECHNICAL_VERSION
    if version not in (VERSION, CREDIHOY_VERSION, CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION):
        raise ProtocolError("VERSION_NO_SOPORTADA")
    fields = ("versionContrato", "solicitudId", "intencionId", "producto",
              "titular", "credito")
    if not technical:
        fields += ("autorizacionAdministrativa" if administrative else "evidenciaDecision",)
    exact_object(value, fields + (("contextoLinix",) if version != VERSION else ())
                 + (("evidenciaTasa",) if administrative or technical else ()))
    product, person, credit = (value[key] for key in ("producto", "titular", "credito"))
    evidence = None if technical else value["autorizacionAdministrativa" if administrative else "evidenciaDecision"]
    exact_object(product, ("codigo", "modalidad", "destino", "certificacionId"))
    exact_object(person, ("tipoDocumento", "numeroDocumento"))
    exact_object(credit, ("montoSolicitado", "plazoMeses", "periodicidad", "moneda",
                          "tasaNominalAnual", "cuotaContractual"))
    if not technical:
        exact_object(evidence, ("autorizacionId", "referencia", "modelo", "alcance") if administrative
                     else ("decisionId", "autorizacionId", "versionPolitica"))
    if type(credit["plazoMeses"]) is not int or not 1 <= credit["plazoMeses"] <= 999:
        raise ProtocolError("REQUEST_INVALIDO")
    if credit["moneda"] != "COP":
        raise ProtocolError("REQUEST_INVALIDO")
    normalized = {
        "versionContrato": version,
        "solicitudId": uuid_text(value["solicitudId"]),
        "intencionId": uuid_text(value["intencionId"]),
        "producto": {key: identifier(item) for key, item in product.items()},
        "titular": {key: identifier(item) for key, item in person.items()},
        "credito": {**credit, "montoSolicitado": amount(credit["montoSolicitado"]),
                    "cuotaContractual": amount(credit["cuotaContractual"]),
                    "tasaNominalAnual": amount(credit["tasaNominalAnual"]),
                    "periodicidad": identifier(credit["periodicidad"])},
    }
    if not technical:
        normalized["autorizacionAdministrativa" if administrative else "evidenciaDecision"] = {
            key: identifier(item) for key, item in evidence.items()}
    if Decimal(normalized["credito"]["tasaNominalAnual"]) > 100:
        raise ProtocolError("REQUEST_INVALIDO")
    if administrative or technical:
        if administrative and (evidence["modelo"] != ADMIN_MODEL or evidence["alcance"] != ADMIN_SCOPE):
            raise ProtocolError("ALCANCE_ADMINISTRATIVO_INVALIDO", 403)
        rate = value["evidenciaTasa"]
        exact_object(rate, ("unidad", "campoXml") + (() if technical else ("referenciaCertificacion",)))
        if (rate["unidad"] != "PORCENTAJE_NOMINAL_ANUAL_MES_VENCIDO"
                or rate["campoXml"] != "V_TASA_NOMINAL"):
            raise ProtocolError("SEMANTICA_TASA_NO_CERTIFICADA")
        normalized["evidenciaTasa"] = {key: identifier(item) for key, item in rate.items()}
    if version != VERSION:
        from .credihoy import normalize_context
        normalized["contextoLinix"] = normalize_context(
            value["contextoLinix"], strict_promoter=administrative or technical,
            allow_without_promoter_offline=(administrative or technical) and allow_without_promoter_offline,
        )
    return normalized


def normalize_technical_request(value):
    """CD6 preparation: migrate legacy operands without inventing permission evidence."""
    from copy import deepcopy
    if not isinstance(value, dict) or value.get("versionContrato") not in (
            CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION):
        raise ProtocolError("VERSION_NO_SOPORTADA")
    candidate = deepcopy(value)
    if candidate["versionContrato"] == CREDIHOY_ADMIN_VERSION:
        candidate["versionContrato"] = CREDIHOY_TECHNICAL_VERSION
        candidate.pop("autorizacionAdministrativa", None)
        if isinstance(candidate.get("evidenciaTasa"), dict):
            candidate["evidenciaTasa"].pop("referenciaCertificacion", None)
    return normalize_request(candidate, allow_without_promoter_offline=True)


def decision_fingerprint(payload):
    # Approval covers the intention and terms, independently of the receipt ID.
    return digest(canonical({key: value for key, value in payload.items()
                             if key not in {"evidenciaDecision", "autorizacionAdministrativa"}}))


@dataclass(frozen=True)
class Principal:
    client: str
    scopes: frozenset[str]

    def __post_init__(self):
        identifier(self.client)
        if (type(self.scopes) is not frozenset
                or any(not isinstance(scope, str) for scope in self.scopes)):
            raise ProtocolError("NO_AUTENTICADO", 401)

    def require(self, scope):
        identifier(self.client)
        if scope not in self.scopes:
            raise ProtocolError("NO_AUTORIZADO", 403)


@dataclass(frozen=True)
class ProductCertificate:
    certificate_id: str
    product: str
    modality: str
    destination: str
    workflow: str
    synthetic: bool = False

    def matches(self, payload):
        return payload["producto"] == {
            "codigo": self.product, "modalidad": self.modality,
            "destino": self.destination, "certificacionId": self.certificate_id,
        }


@dataclass(frozen=True)
class DecisionGrant:
    client: str
    fingerprint: str
    decision_id: str
    authorization_id: str
    policy_version: str
    authorized: bool
    expires_at: datetime
    synthetic: bool = False


@dataclass(frozen=True)
class AdministrativeGrant:
    client: str
    fingerprint: str
    authorization_id: str
    reference: str
    issuer_id: str
    request_id: str
    intent_id: str
    workflow: str
    scope: str
    authorized: bool
    not_before: datetime
    expires_at: datetime
    synthetic: bool = False


@dataclass(frozen=True)
class Observation:
    outcome: str
    intent_id: str
    fingerprint: str
    flow_id: str | None = None
    application_type: str | None = None
    application_number: str | None = None
    obligation_number: str | None = None
    activity_current: int | None = None
    cut_verified: bool = False
    pending_reason: str = ""
    candidate_application_number: str | None = None
    workflow_error_count: int | None = None

    def validated(self, operation):
        if self.candidate_application_number is not None:
            identifier(self.candidate_application_number)
            if self.outcome != "STARTED":
                raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        if (self.workflow_error_count is not None and
                (type(self.workflow_error_count) is not int or self.workflow_error_count < 0)):
            raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        if (self.intent_id != str(operation.intent_id)
                or self.fingerprint != operation.fingerprint):
            raise ProtocolError("RESULTADO_REMOTO_NO_VINCULADO", 409)
        if self.outcome not in {"RADICATED", "STARTED", "REJECTED_WITHOUT_EFFECT", "UNKNOWN", "NOT_FOUND"}:
            raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        values = (self.flow_id, self.application_type, self.application_number, self.obligation_number)
        for value in values:
            if value is not None:
                identifier(value)
        if self.outcome == "RADICATED" and not all(values[:3]):
            raise ProtocolError("RADICACION_NO_CONFIRMADA")
        if (self.outcome == "RADICATED" and operation.flow_id is not None
                and self.flow_id != operation.flow_id):
            raise ProtocolError("FLUJO_REMOTO_CONTRADICTORIO", 409)
        if self.outcome == "STARTED" and (not self.flow_id or any(value is not None for value in values[1:])):
            raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        if self.outcome not in {"RADICATED", "STARTED"} and any(value is not None for value in values):
            raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        if (self.activity_current is not None and
                (type(self.activity_current) is not int or self.activity_current <= 0)):
            raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        if type(self.cut_verified) is not bool or (self.cut_verified and self.outcome != "RADICATED"):
            raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        if self.pending_reason not in {"", "CREDIHOY_SC_NO_CONFIRMADA",
                                      "CREDIHOY_CORTE_NO_VERIFICADO", "CREDIHOY_ACTIVIDAD_INESPERADA"}:
            raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        if self.pending_reason and self.outcome != "STARTED":
            raise ProtocolError("RESULTADO_REMOTO_INVALIDO")
        if (self.outcome == "STARTED" and operation.flow_id is not None
                and self.flow_id != operation.flow_id):
            raise ProtocolError("FLUJO_REMOTO_CONTRADICTORIO", 409)
        return self
