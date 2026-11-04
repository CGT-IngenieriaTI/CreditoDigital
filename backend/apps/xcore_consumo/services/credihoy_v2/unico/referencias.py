"""Personal reference capture; encrypted, consent-bound and never sent to LINIX."""

import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import ConsentimientoConsumo
from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text
from .consentimiento_real import obtener, validar_documento
from .linix_estado import KEY as LINIX_STATE_KEY


VERSION = "credihoy-referencias-captura-v1"
CONSENT_VERSION = "CREDIHOY-REFERENCIAS-1"
CONSENT_TEXT = "Confirmo que cuento con autorizacion para registrar y contactar las referencias personales indicadas para esta solicitud de credito."
KEY = "referencias_personales_v1"
FIELDS = ("tipoReferencia", "nombre", "direccion", "ciudad", "ciudadDescripcion", "codigoCiudad",
          "codigoDepartamento", "codigoPais", "telefono", "email", "observacion", "parentesco", "tipoReferenciaDescripcion")


def catalogos():
    envelope = json.loads(Path(__file__).with_name("referencias_catalogos.json").read_text(encoding="utf-8"))
    raw = gzip.decompress(base64.b64decode(envelope["datos"], validate=True))
    if len(raw) != envelope["bytesSinComprimir"] or hashlib.sha256(raw).hexdigest() != envelope["sha256"]:
        raise OperacionError("CATALOGO_REFERENCIAS_NO_DISPONIBLE")
    data = json.loads(raw)
    return {**data, "sha256": envelope["sha256"]}


def validar(references):
    catalog = catalogos()
    types = {item["codigo"]: item["descripcion"] for item in catalog["tipos"]}
    relatives = {item["codigo"] for item in catalog["parentescos"]}
    cities = {(a, b, c): name for a, b, c, name in catalog["ciudades"]}
    if not isinstance(references, list):
        raise OperacionError("REFERENCIAS_INVALIDAS")
    records = []
    for value in references:
        if not isinstance(value, dict) or set(value) - set(FIELDS):
            raise OperacionError("REFERENCIAS_INVALIDAS")
        row = {key: value.get(key) if value.get(key) is not None else "" for key in FIELDS}
        if any(not isinstance(text, str) or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in text)
               or key in catalog["limites"] and len(text) > catalog["limites"][key] for key, text in row.items()):
            raise OperacionError("REFERENCIAS_INVALIDAS")
        location = (row["codigoPais"], row["codigoDepartamento"], row["codigoCiudad"])
        if (row["tipoReferencia"] not in types or not row["nombre"].strip() or location not in cities
                or row["parentesco"] and row["parentesco"] not in relatives):
            raise OperacionError("REFERENCIAS_CATALOGO_INVALIDO")
        if (not row["telefono"] and not row["email"] or row["telefono"]
                and (not re.fullmatch(r"\+?[0-9 ()-]{7,20}", row["telefono"])
                     or not re.search(r"[0-9]", row["telefono"]))):
            raise OperacionError("REFERENCIAS_CONTACTO_INVALIDO")
        if row["email"]:
            try:
                validate_email(row["email"])
            except ValidationError:
                raise OperacionError("REFERENCIAS_CONTACTO_INVALIDO") from None
        for key, expected in (("tipoReferenciaDescripcion", types[row["tipoReferencia"]]),
                              ("ciudad", cities[location]), ("ciudadDescripcion", cities[location])):
            if row[key] and row[key] != expected:
                raise OperacionError("REFERENCIAS_DESCRIPCION_INCONSISTENTE")
            row[key] = expected
        records.append(row)
    return records


def _context(solicitud_id, operador, *, lock=False):
    if getattr(settings, "CREDIHOY_REFERENCIAS_CAPTURE_ENABLED", False) is not True:
        raise OperacionError("REFERENCIAS_CAPTURA_NO_HABILITADA")
    s = obtener(solicitud_id, operador, lock=lock)
    consent = ConsentimientoConsumo.objects.get(solicitud=s)
    validar_documento(s, consent)
    if consent.aceptado is not True or consent.firmado is not True:
        raise OperacionError("CONSENTIMIENTO_FIRMADO_REQUERIDO")
    return s, consent


def _identity(s, consent):
    person = s.solicitante
    return hashlib.sha256(json.dumps([str(s.pk), str(person.pk), person.tipo_identificacion,
        person.numero_identificacion, str(consent.pk), consent.text_hash], ensure_ascii=True).encode()).hexdigest()


def leer(solicitud_id, operador):
    s, consent = _context(solicitud_id, operador)
    stored = s.consumo_detail.form_data.get(KEY)
    references, accepted = [], False
    if stored:
        packet = json.loads(decrypt_text(stored["cifrado"]))
        if packet["identidad"] != _identity(s, consent) or packet["consentimientoVersion"] != CONSENT_VERSION:
            raise OperacionError("REFERENCIAS_INTEGRIDAD_INVALIDA")
        references, accepted = packet["referencias"], True
    return {"versionContrato": VERSION, "solicitudId": str(s.pk), "referencias": references,
            "consentimiento": {"version": CONSENT_VERSION, "texto": CONSENT_TEXT, "aceptado": accepted},
            "envioOracleHabilitado": False, "estadoContrato3883": "PENDIENTE_VALIDACION"}


def guardar(solicitud_id, operador, payload, *, request_id=""):
    if (not isinstance(payload, dict) or set(payload) != {"referencias", "consentimiento"}
            or not isinstance(payload["consentimiento"], dict)
            or set(payload["consentimiento"]) != {"version", "aceptado"}
            or payload["consentimiento"]["version"] != CONSENT_VERSION
            or payload["consentimiento"]["aceptado"] is not True):
        raise OperacionError("REFERENCIAS_CONSENTIMIENTO_REQUERIDO")
    records = validar(payload["referencias"])
    with transaction.atomic():
        s, consent = _context(solicitud_id, operador, lock=True)
        detail = s.consumo_detail
        if detail.orchestration_data.get(LINIX_STATE_KEY):
            raise OperacionError("EXPEDIENTE_CON_ENVIO_LINIX_NO_MODIFICABLE")
        packet = {"identidad": _identity(s, consent), "referencias": records,
                  "consentimientoVersion": CONSENT_VERSION, "textoConsentimiento": CONSENT_TEXT,
                  "aceptadoAt": timezone.now().isoformat(), "operadorId": str(operador.pk),
                  "catalogoSha256": catalogos()["sha256"]}
        detail.form_data = {**detail.form_data, KEY: {"version": VERSION,
                            "cifrado": encrypt_text(json.dumps(packet, ensure_ascii=True))}}
        detail.save(update_fields=["form_data", "updated_at"])
        AuditLog.objects.create(solicitud=s, event="credihoy_referencias_guardadas",
            actor=f"user:{operador.pk}", request_id=request_id,
            payload={"cantidad": len(records), "envioOracle": False})
    return leer(solicitud_id, operador)


def preparar_local(solicitud_id, operador, mapper, *, payload_radicacion=None):
    """Trusted in-process mapper only; no network client or grant is created."""
    data = leer(solicitud_id, operador)
    if data["referencias"] and data["consentimiento"]["aceptado"] is not True:
        raise OperacionError("REFERENCIAS_CONSENTIMIENTO_REQUERIDO")
    if payload_radicacion is not None:
        s, _ = _context(solicitud_id, operador)
        if s.consumo_detail.orchestration_data.get(LINIX_STATE_KEY):
            raise OperacionError("EXPEDIENTE_CON_ENVIO_LINIX_NO_MODIFICABLE")
        if (not isinstance(payload_radicacion, dict)
                or payload_radicacion.get("solicitudId") != str(s.pk)
                or not isinstance(payload_radicacion.get("titular"), dict)
                or payload_radicacion.get("titular", {}).get("numeroDocumento") != s.solicitante.numero_identificacion
                or payload_radicacion.get("titular", {}).get("tipoDocumento") != s.solicitante.tipo_identificacion):
            raise OperacionError("REFERENCIAS_EXPEDIENTE_NO_CORRESPONDE")
        return mapper(payload_radicacion, data["referencias"])
    return mapper(data["referencias"])


def preparar_ensayo_productivo(solicitud_id, operador, payload, *, evidencias, autorizacion, preparador):
    """Export the owned, signed case to the shared preparer; no Oracle transport.

    References and their proof come from encrypted capture, never from caller
    input. Commercial/simulation/administrative evidence must be supplied by
    the institutional operator; this function does not issue authorization.
    """
    s, consent = _context(solicitud_id, operador)
    if str(s.pk) == 'a402121e-81d0-4759-8c4c-485f0787d45c':
        raise OperacionError('PILOTO_PROTEGIDO_SOLO_CONCILIACION')
    if s.consumo_detail.orchestration_data.get(LINIX_STATE_KEY):
        raise OperacionError('EXPEDIENTE_CON_ENVIO_LINIX_NO_MODIFICABLE')
    if (not isinstance(payload, dict) or payload.get('solicitudId') != str(s.pk)
            or not isinstance(payload.get('titular'), dict)
            or payload['titular'].get('numeroDocumento') != s.solicitante.numero_identificacion
            or payload['titular'].get('tipoDocumento') != s.solicitante.tipo_identificacion):
        raise OperacionError('REFERENCIAS_EXPEDIENTE_NO_CORRESPONDE')
    try:
        if (Decimal(payload['credito']['montoSolicitado']) != s.monto_solicitado
                or payload['credito']['plazoMeses'] != s.plazo_solicitado):
            raise ValueError
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise OperacionError('CONDICIONES_NO_CORRESPONDEN_AL_EXPEDIENTE') from None
    if not isinstance(evidencias, dict) or set(evidencias) != {'condicionesComerciales', 'simulacion', 'elegibilidadAdministrativa'}:
        raise OperacionError('EVIDENCIAS_INSTITUCIONALES_DEL_CASO_REQUERIDAS')
    captured = leer(solicitud_id, operador)
    if captured['referencias'] and captured['consentimiento']['aceptado'] is not True:
        raise OperacionError('REFERENCIAS_CONSENTIMIENTO_REQUERIDO')
    reference_hash = hashlib.sha256(json.dumps(captured['referencias'], sort_keys=True,
        separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    proofs = {**evidencias, 'consentimiento': {'referencia': str(consent.pk), 'sha256': consent.text_hash},
        'referencias': {'referencia': ('captura-referencias:' if captured['consentimiento']['aceptado']
            else 'expediente-sin-referencias:') + str(s.pk), 'sha256': reference_hash}}
    return preparador(payload, captured['referencias'], evidence=proofs, authorization=autorizacion)
