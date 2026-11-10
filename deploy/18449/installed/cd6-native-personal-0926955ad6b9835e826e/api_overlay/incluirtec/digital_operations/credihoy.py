"""CrediHoy contract and simulated bridge. No executable Oracle transport."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from xml.etree import ElementTree as ET

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from incluirtec.services.credit_line_config import CREDIHOY_DIGITAL_CONFIG, CreditModalityConfig
from incluirtec.services.xml_builder import build_credihoy_digital_radication_xml

from .contracts import (
    CREDIHOY_VERSION, CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION, MONEY, Observation, ProductCertificate, ProtocolError,
    exact_object, identifier,
)


LINE = replace(CREDIHOY_DIGITAL_CONFIG, workflow_model="CREDIHOY_VAL_EMPLEADO",
               modalities={"111": CreditModalityConfig("111", "Credihoy", ("CFA",))})
CERTIFICATE = ProductCertificate("CREDIHOY_390_111_CFA_CD4D", "390", "111", "CFA", LINE.workflow_model)
RADICATION_ACTIVITY = 113
RADICATION_PROCEDURE = "PK_CA_WF_RADICA_SOL_CREDITO.PR_RADICAR_SOLICITUD"
MAX_XML_BYTES = 1024 * 1024


def lab_certificate():
    # Mapping is real; synthetic only identifies the laboratory execution boundary.
    return replace(CERTIFICATE, synthetic=True)


def numeric_id(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,30}", value) or int(value) <= 0:
        raise ProtocolError("CREDIHOY_IDENTIFICADOR_INVALIDO")
    return value


def nonnegative_decimal(value):
    if not isinstance(value, str) or not MONEY.fullmatch(value):
        raise ProtocolError("REQUEST_INVALIDO")
    return format(Decimal(value), ".2f")


def linix_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value):
        raise ProtocolError("REQUEST_INVALIDO")
    try:
        datetime.strptime(value, "%m/%d/%Y")
    except ValueError:
        raise ProtocolError("REQUEST_INVALIDO") from None
    return value


def normalize_context(context, *, allow_without_promoter_offline=False, strict_promoter=False):
    if not isinstance(context, dict):
        raise ProtocolError("REQUEST_INVALIDO")
    if allow_without_promoter_offline and "datosRadicacion" not in context:
        raise ProtocolError("CREDIHOY_DATOS_RADICACION_REQUERIDOS")
    fields = ("kIdTerc", "sucursal", "fechaSolicitud", "fechaCuotaOrdinaria", "parametros")
    exact_object(context, fields + tuple(key for key in ("datosRadicacion", "entrada113") if key in context))
    parameters = context["parametros"]
    fields = ("amortizacion", "formaAplicarTasa", "medioPago", "seguroVida", "modalidadCapital",
              "porcentajeCapitalizacion", "valorTotalCapitalizacion", "periodoPagoCapital",
              "periodoPagoInteres", "actividadEconomica")
    exact_object(parameters, fields)
    normalized_parameters = {key: identifier(value) for key, value in parameters.items()}
    for key in ("porcentajeCapitalizacion", "valorTotalCapitalizacion"):
        normalized_parameters[key] = nonnegative_decimal(parameters[key])
    result = {
        "kIdTerc": numeric_id(context["kIdTerc"]), "sucursal": numeric_id(context["sucursal"]),
        "fechaSolicitud": linix_date(context["fechaSolicitud"]),
        "fechaCuotaOrdinaria": linix_date(context["fechaCuotaOrdinaria"]),
        "parametros": normalized_parameters,
    }
    if "datosRadicacion" in context:
        result["datosRadicacion"] = normalize_radication_data(
            context["datosRadicacion"], allow_without_promoter_offline=allow_without_promoter_offline,
            strict_promoter=strict_promoter,
        )
    if "entrada113" in context:
        entry = context["entrada113"]
        exact_object(entry, ("tipoConsulta", "modalidadSimular", "datoCalcularSimulacion", "valorCuota"))
        # Observed input shape, not a default or a certification for another dossier.
        if ((entry["tipoConsulta"], entry["modalidadSimular"], entry["datoCalcularSimulacion"])
                != ("2", "M", "C") or Decimal(nonnegative_decimal(entry["valorCuota"])) != 0):
            raise ProtocolError("CREDIHOY_ENTRADA_113_NO_SOPORTADA")
        result["entrada113"] = {**entry, "valorCuota": "0.00"}
    return result


def contact_text(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ProtocolError("CREDIHOY_DATOS_RADICACION_INVALIDOS")
    return value.strip()


def normalize_radication_data(value, *, allow_without_promoter_offline=False, strict_promoter=False):
    if type(allow_without_promoter_offline) is not bool:
        raise ProtocolError("REQUEST_INVALIDO")
    absent_key = isinstance(value, dict) and "promotor" not in value
    exact_object(value, ("cliente",) if allow_without_promoter_offline and absent_key
                 else ("cliente", "promotor"))
    customer, promoter = value["cliente"], value.get("promotor")
    exact_object(customer, ("nombre", "email", "numeroCelular"))
    if promoter is not None or not allow_without_promoter_offline:
        exact_object(promoter, ("codigo", "kIdTerc", "nombre"))
    if promoter is not None and (strict_promoter or allow_without_promoter_offline):
        forbidden = {"0", "CGTE", "SIN_PROMOTOR", "NULL"}
        if any(str(item).strip().upper() in forbidden for item in promoter.values()):
            raise ProtocolError("CREDIHOY_PROMOTOR_INVALIDO")
    email = contact_text(customer["email"], 254)
    phone = contact_text(customer["numeroCelular"], 15)
    try:
        validate_email(email)
    except ValidationError:
        raise ProtocolError("CREDIHOY_DATOS_RADICACION_INVALIDOS") from None
    if not re.fullmatch(r"\+?[0-9]{7,15}", phone):
        raise ProtocolError("CREDIHOY_DATOS_RADICACION_INVALIDOS")
    return {
        "cliente": {"nombre": contact_text(customer["nombre"], 200),
                    "email": email, "numeroCelular": phone},
        "promotor": None if promoter is None else {"codigo": identifier(promoter["codigo"]),
                     "kIdTerc": numeric_id(promoter["kIdTerc"]),
                     "nombre": contact_text(promoter["nombre"], 200)},
    }


def validate_request(payload, *, allow_without_promoter_offline=False):
    if payload["versionContrato"] not in (CREDIHOY_VERSION, CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION) or not CERTIFICATE.matches(payload):
        raise ProtocolError("PRODUCTO_MODALIDAD_INCOMPATIBLE")
    administrative = payload["versionContrato"] in (CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION)
    context = normalize_context(payload["contextoLinix"], strict_promoter=administrative,
        allow_without_promoter_offline=administrative and allow_without_promoter_offline)
    if (payload["credito"]["periodicidad"] != "MENSUAL"
            or context["parametros"]["periodoPagoCapital"] != "1"
            or context["parametros"]["periodoPagoInteres"] != "1"):
        raise ProtocolError("CREDIHOY_PERIODICIDAD_NO_COMPATIBLE")
    if datetime.strptime(context["fechaCuotaOrdinaria"], "%m/%d/%Y") <= datetime.strptime(context["fechaSolicitud"], "%m/%d/%Y"):
        raise ProtocolError("CREDIHOY_FECHAS_NO_COMPATIBLES")


def build_radication_xml(payload, *, allow_without_promoter_offline=False, references_for_preparation=None):
    # Only local preparation supplies this argument. Existing gateway calls keep
    # their sealed payload/intent unchanged; no HTTP transport accepts references.
    references = []
    if references_for_preparation is not None:
        from .references import normalize_references
        references = normalize_references(references_for_preparation)
    if payload.get("versionContrato") in (CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION):
        from .contracts import normalize_request
        payload = normalize_request(payload, allow_without_promoter_offline=allow_without_promoter_offline)
    validate_request(payload, allow_without_promoter_offline=allow_without_promoter_offline)
    credit, context = payload["credito"], payload["contextoLinix"]
    existing_payload = {
        "nit": payload["titular"]["numeroDocumento"], "kIdTerc": context["kIdTerc"],
        "sucursal": context["sucursal"],
        "cliente": {"identificacion": payload["titular"]["numeroDocumento"],
                    "actividadEconomica": context["parametros"]["actividadEconomica"]},
        "credito": {
            **context["parametros"], "producto": CERTIFICATE.product,
            "modalidadCredito": CERTIFICATE.modality, "destinacion": CERTIFICATE.destination,
            "montoSolicitado": credit["montoSolicitado"], "numeroCuotas": credit["plazoMeses"],
            "valorCuota": credit["cuotaContractual"], "valorCuotaCal": credit["cuotaContractual"],
            "tasaNominal": credit["tasaNominalAnual"], "fechaSolicitud": context["fechaSolicitud"],
            "fechaCuotaOrdinaria": context["fechaCuotaOrdinaria"],
        },
        "referencias": references,
    }
    if "entrada113" in context:
        existing_payload["credito"].update(context["entrada113"])
    if "datosRadicacion" in context:
        data = context["datosRadicacion"]
        existing_payload["cliente"].update(data["cliente"])
        if data["promotor"] is not None:
            existing_payload["credito"].update(
                promotor=data["promotor"]["codigo"], promotorNombre=data["promotor"]["nombre"],
            )
    without_promoter = (payload["versionContrato"] in (CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION)
        and allow_without_promoter_offline and "datosRadicacion" in context
        and context["datosRadicacion"]["promotor"] is None)
    xml = build_credihoy_digital_radication_xml(
        existing_payload, without_promoter_offline=without_promoter)
    if payload["versionContrato"] in (CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION):
        # The legacy builder's descriptive defaults do not certify selected codes.
        root = ET.fromstring(xml)
        for tag in ("A_MODALCAPI_D", "A_AMORTIZACION_D", "A_FORMA_APLICAR_TASA_D",
                    "A_MEDIO_PAGO_D", "A_ACTIVIDAD_ECONOMICA_D"):
            for node in root.findall("./Datos/" + tag):
                node.text = None
        xml = ET.tostring(root, encoding="unicode")
    return xml


def clob_text(value):
    if hasattr(value, "read"):
        value = value.read()
    if value is None:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_XML_BYTES:
        raise ProtocolError("CREDIHOY_XML_INVALIDO")
    return value


def xml_fields(value, *, additional_keys=()):
    value = clob_text(value)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError("CREDIHOY_XML_INCOMPLETO")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", value, re.I):
        raise ProtocolError("CREDIHOY_XML_INVALIDO")
    try:
        root = ET.fromstring(value)
    except (ET.ParseError, ValueError):
        raise ProtocolError("CREDIHOY_XML_INVALIDO") from None
    keys = {"A_TIPO_SOLI_CREDITO", "A_NUMERO_SOLI_CREDITO", "A_PRODUCTO",
            "A_MODALIDAD_CREDITO", "A_DESTINACION", "V_CODIGOINTERNO", "A_CODIGO_CLIENTE"}
    if not set(additional_keys)<={'A_CODIGO_RESPUESTA','A_PAGARE'}:
        raise ProtocolError('CREDIHOY_XML_CAMPOS_NO_ADMITIDOS')
    keys.update(additional_keys)
    values = {key: set() for key in keys}
    for node in root.iter():
        key = node.tag.rsplit("}", 1)[-1]
        if key in keys and node.text and node.text.strip():
            values[key].add(node.text.strip())
        for key in keys:
            value = node.attrib.get(key)
            if value is not None and value.strip():
                values[key].add(value.strip())
    if any(len(items) > 1 for items in values.values()):
        raise ProtocolError("CREDIHOY_XML_CONTRADICTORIO", 409)
    return {key: next(iter(items)) for key, items in values.items() if items}


@dataclass(frozen=True)
class ScReference:
    application_type: str
    application_number: str


def reference_from_fields(fields):
    if not fields.get("A_TIPO_SOLI_CREDITO") or not fields.get("A_NUMERO_SOLI_CREDITO"):
        raise ProtocolError("CREDIHOY_XML_INCOMPLETO")
    if fields["A_TIPO_SOLI_CREDITO"] != "SC":
        raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)
    return ScReference("SC", numeric_id(fields["A_NUMERO_SOLI_CREDITO"]))


def validate_profile_fields(fields, payload, *, required=False):
    expected = {
        "A_PRODUCTO": "390", "A_MODALIDAD_CREDITO": "111", "A_DESTINACION": "CFA",
        "V_CODIGOINTERNO": payload["contextoLinix"]["kIdTerc"],
        "A_CODIGO_CLIENTE": payload["titular"]["numeroDocumento"],
    }
    for key, value in expected.items():
        if required and key not in fields:
            raise ProtocolError("CREDIHOY_XML_INCOMPLETO")
        if key in fields and fields[key] != value:
            raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)


def reference_from_reply(xml_new, xml_output, payload):
    new, output = xml_fields(xml_new), xml_fields(xml_output)
    validate_profile_fields(new, payload)
    validate_profile_fields(output, payload)
    first, second = reference_from_fields(new), reference_from_fields(output)
    if first != second:
        raise ProtocolError("CREDIHOY_XML_CONTRADICTORIO", 409)
    return first


@dataclass(frozen=True)
class FlowRadicationRecord:
    flow_id: str
    workflow: str
    series_id: str | None
    task_state: str | None
    xml_input: str | None
    xml_new: str | None
    xml_output: str | None


@dataclass(frozen=True)
class TaskContext:
    flow_id: str
    workflow: str
    activity_id: int
    series_id: str


class CredihoyEvidenceValidator:
    """Shared SELECT/XML validation; does not start flows or execute activities."""
    validate_request = staticmethod(validate_request)

    def __init__(self, gateway):
        self.gateway = gateway

    def _flow_records(self, operation, flow_id, *, records=None):
        records = self.gateway.radication_records(flow_id) if records is None else records
        if not records:
            raise ProtocolError("CREDIHOY_SIN_EVIDENCIA_RADICACION")
        for record in records:
            if record.flow_id != flow_id or record.workflow != LINE.workflow_model:
                raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)
            validate_profile_fields(xml_fields(record.xml_input), operation.payload, required=True)
        return records

    def _reference_by_flow(self, operation, flow_id, *, records=None):
        references = set()
        for record in self._flow_records(operation, flow_id, records=records):
            references.add(reference_from_reply(record.xml_new, record.xml_output, operation.payload))
        if len(references) != 1:
            raise ProtocolError("CREDIHOY_XML_CONTRADICTORIO", 409)
        return next(iter(references))

    def _confirmed_observation(self, operation, flow_id, reference):
        rows = self.gateway.application_rows(reference)
        if not rows:
            raise ProtocolError("CREDIHOY_SC_NO_EXISTE")
        if len(rows) != 1:
            raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)
        row = rows[0]
        expected = {
            "K_TIPODR": "SC", "K_NUMDOC": reference.application_number,
            "K_IDTERC": operation.payload["contextoLinix"]["kIdTerc"],
            "K_MODALI": "111", "K_DESTIN": "CFA",
            "A_NUMNIT": operation.payload["titular"]["numeroDocumento"],
        }
        if any(str(row.get(key)) != value for key, value in expected.items()):
            raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)
        try:
            amount = Decimal(str(row.get("V_MONTO")))
            valid_amount = amount.is_finite() and amount == Decimal(operation.payload["credito"]["montoSolicitado"])
        except InvalidOperation:
            valid_amount = False
        if not valid_amount:
            raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)
        obligation = row.get("A_OBLIGA")
        obligation = numeric_id(obligation) if obligation not in (None, "") else None
        return Observation("RADICATED", str(operation.intent_id), operation.fingerprint,
                           flow_id, "SC", reference.application_number, obligation)

class CredihoyLabAdapter(CredihoyEvidenceValidator):
    """Existing CD-4D simulation; not the start-only Oracle gateway."""

    synthetic = True

    def __init__(self, gateway):
        if getattr(gateway, "synthetic", None) is not True:
            raise ProtocolError("CREDIHOY_TRANSPORTE_REAL_BLOQUEADO", 503)
        super().__init__(gateway)

    def submit(self, operation, *, checkpoint_flow):
        validate_request(operation.payload)
        if operation.flow_id is not None:
            # A persisted remote identity is never permission to start another flow.
            return self.lookup(operation)
        xml = build_radication_xml(operation.payload)
        flow_id = numeric_id(self.gateway.start_workflow(xml, LINE))
        checkpoint_flow(flow_id)
        records = self._flow_records(operation, flow_id)
        # Startup itself may already have executed 113. Never execute it twice.
        has_result = any(record.xml_new or record.xml_output or record.task_state == "E"
                         or xml_fields(record.xml_input).get("A_NUMERO_SOLI_CREDITO") for record in records)
        if has_result:
            reference = self._reference_by_flow(operation, flow_id, records=records)
        else:
            task = self.gateway.active_task(flow_id)
            if (not isinstance(task, TaskContext) or task.flow_id != flow_id
                    or task.workflow != LINE.workflow_model or task.activity_id != RADICATION_ACTIVITY):
                raise ProtocolError("CREDIHOY_113_NO_EJECUTABLE")
            numeric_id(task.series_id)
            result = self.gateway.execute_radication113(flow_id, task.series_id, xml)
            from incluirtec.services.procedures import extract_credihoy_radication_reference
            reference = extract_credihoy_radication_reference(result, operation.payload)
            persisted = self._reference_by_flow(operation, flow_id)
            if reference != persisted:
                raise ProtocolError("CREDIHOY_XML_CONTRADICTORIO", 409)
        observation = self._confirmed_observation(operation, flow_id, reference)
        if self.gateway.safe_stop_after113(flow_id) is not True:
            raise ProtocolError("CREDIHOY_CORTE_NO_VERIFICADO", 409)
        return observation

    def lookup(self, operation):
        if operation.flow_id is None:
            return Observation("UNKNOWN", str(operation.intent_id), operation.fingerprint)
        flow_id = numeric_id(operation.flow_id)
        reference = self._reference_by_flow(operation, flow_id)
        return self._confirmed_observation(operation, flow_id, reference)


class BlockedCredihoyOracleTransport:
    """Hard gate: no connection/import of Oracle services, even if flags change."""

    synthetic = False
    blocked_reason = "CREDIHOY_CORTE_TRANSACCIONAL_NO_CERTIFICADO"

    def submit(self, operation, *, checkpoint_flow=None):
        raise ProtocolError(self.blocked_reason, 503)

    def lookup(self, operation):
        raise ProtocolError(self.blocked_reason, 503)
