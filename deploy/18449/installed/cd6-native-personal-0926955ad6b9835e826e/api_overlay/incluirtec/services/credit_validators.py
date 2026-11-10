import re
from datetime import datetime

from incluirtec.services.microcredito_plan import (
    MicrocreditoPlanDateError,
    calculate_initial_payment_date,
)


DATE_MM_DD_YYYY_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
MAX_MICROCREDITO_CODEBTORS = 3
LINIX_CODEBTOR_DOCUMENT_TYPES = frozenset(
    {"A", "C", "D", "E", "M", "N", "O", "P", "Q", "R", "S", "T", "U"}
)
LINIX_CODEBTOR_GENDERS = frozenset({"F", "M"})


class CreditValidationError(ValueError):
    pass


def _normalized_text(value):
    return "" if value is None else str(value).strip()


def normalize_microcredit_codebtors(codebtors):
    if codebtors in (None, ""):
        return []
    if not isinstance(codebtors, list):
        raise CreditValidationError("codeudores debe ser una lista")

    normalized = []
    for index, codebtor in enumerate(codebtors, start=1):
        if not isinstance(codebtor, dict):
            raise CreditValidationError(f"Codeudor #{index} debe ser un objeto")
        normalized.append(
            {
                "identificacion": _normalized_text(codebtor.get("identificacion")),
                "tipoIdentificacion": _normalized_text(
                    codebtor.get("tipoDocumento") or codebtor.get("tipoIdentificacion")
                ).upper(),
                "tipoIdentificacionDescripcion": _normalized_text(
                    codebtor.get("tipoDocumentoDescripcion")
                    or codebtor.get("tipoIdentificacionDescripcion")
                ),
                "primerNombre": _normalized_text(codebtor.get("primerNombre")),
                "segundoNombre": _normalized_text(codebtor.get("segundoNombre")),
                "primerApellido": _normalized_text(codebtor.get("primerApellido")),
                "segundoApellido": _normalized_text(codebtor.get("segundoApellido")),
                "email": _normalized_text(codebtor.get("email")),
                "telefono": _normalized_text(codebtor.get("telefono")),
                "genero": _normalized_text(codebtor.get("genero")).upper(),
                "fechaNacimiento": _normalized_text(codebtor.get("fechaNacimiento")),
                "deudorSolidario": _normalized_text(codebtor.get("deudorSolidario") or "Y").upper(),
                "parentesco": _normalized_text(codebtor.get("parentesco")),
            }
        )
    return normalized


def validate_microcredit_codebtors(data):
    codebtors = normalize_microcredit_codebtors(data.get("codeudores"))
    if len(codebtors) > MAX_MICROCREDITO_CODEBTORS:
        raise CreditValidationError(
            f"Microcredito permite maximo {MAX_MICROCREDITO_CODEBTORS} codeudores"
        )

    holder_document = _normalized_text(
        data.get("cliente", {}).get("identificacion") or data.get("nit")
    )
    seen_documents = set()

    for index, codebtor in enumerate(codebtors, start=1):
        required_fields = {
            "identificacion": codebtor["identificacion"],
            "tipoDocumento": codebtor["tipoIdentificacion"],
            "primerNombre": codebtor["primerNombre"],
            "primerApellido": codebtor["primerApellido"],
            "email": codebtor["email"],
            "fechaNacimiento": codebtor["fechaNacimiento"],
            "genero": codebtor["genero"],
        }
        missing = [name for name, value in required_fields.items() if not value]
        if missing:
            raise CreditValidationError(
                f"Codeudor #{index} incompleto: faltan {', '.join(missing)}"
            )

        document = codebtor["identificacion"]
        if document == holder_document:
            raise CreditValidationError(
                f"Codeudor #{index} debe tener identificacion distinta del titular"
            )
        if document in seen_documents:
            raise CreditValidationError(
                f"La identificacion del codeudor #{index} esta repetida"
            )
        seen_documents.add(document)

        document_type = codebtor["tipoIdentificacion"]
        if document_type not in LINIX_CODEBTOR_DOCUMENT_TYPES:
            allowed = ", ".join(sorted(LINIX_CODEBTOR_DOCUMENT_TYPES))
            raise CreditValidationError(
                f"Codeudor #{index} tiene tipoDocumento invalido; permitidos: {allowed}"
            )

        gender = codebtor["genero"]
        if gender not in LINIX_CODEBTOR_GENDERS:
            raise CreditValidationError(
                f"Codeudor #{index} tiene genero invalido; permitidos: F, M"
            )

        birth_date = codebtor["fechaNacimiento"]
        if not DATE_MM_DD_YYYY_RE.match(birth_date):
            raise CreditValidationError(
                f"Codeudor #{index}.fechaNacimiento debe usar formato MM/DD/YYYY"
            )
        try:
            datetime.strptime(birth_date, "%m/%d/%Y")
        except ValueError as exc:
            raise CreditValidationError(
                f"Codeudor #{index}.fechaNacimiento no es una fecha valida"
            ) from exc

    return codebtors


def normalize_credit_radication_payload(data):
    cliente = data.get("cliente", {})
    solicitud = data.get("solicitud") or data.get("credito") or {}

    normalized = {
        "cliente": {
            "identificacion": cliente.get("identificacion") or data.get("nit"),
            "email": cliente.get("email") or solicitud.get("email"),
            "numeroCelular": cliente.get("numeroCelular") or solicitud.get("numeroCelular"),
        },
        "solicitud": {
            "producto": solicitud.get("producto") or data.get("modelo"),
            "modalidadCredito": solicitud.get("modalidadCredito"),
            "destinacion": solicitud.get("destinacion"),
            "montoSolicitado": solicitud.get("montoSolicitado"),
            "montoMaximo": solicitud.get("montoMaximo") or solicitud.get("montoSolicitado"),
            "montoGiro": solicitud.get("montoGiro") or solicitud.get("montoSolicitado"),
            "numeroCuotas": solicitud.get("numeroCuotas"),
            "valorCuota": solicitud.get("valorCuota"),
            "periodoPagoCapital": solicitud.get("periodoPagoCapital"),
            "periodoPagoInteres": solicitud.get("periodoPagoInteres"),
            "tasaNominalCrea": solicitud.get("tasaNominalCrea") or solicitud.get("tasaNominal"),
            "medioPago": solicitud.get("medioPago") or data.get("laboral", {}).get("medioPago"),
            "fechaSolicitud": solicitud.get("fechaSolicitud"),
            "sucursal": solicitud.get("sucursal") or data.get("sucursal"),
            "pagare": solicitud.get("pagare"),
            "observacion": solicitud.get("observacion"),
            "promotor": solicitud.get("promotor"),
            "factorContribucion": solicitud.get("factorContribucion") or solicitud.get("factorComision"),
        },
        "formasDesembolso": data.get("formasDesembolso") or data.get("formas_desembolso") or [],
        "obligacionesRecogidas": data.get("obligacionesRecogidas") or data.get("obligaciones_recogidas") or [],
        "codeudores": data.get("codeudores") or [],
        "contexto": {
            "codigoInterno": data.get("contexto", {}).get("codigoInterno") or data.get("kIdTerc"),
            "funcionalidad": data.get("contexto", {}).get("funcionalidad") or "19",
        },
    }
    return normalized


def validate_credit_radication_payload(payload, line_config):
    solicitud = payload["solicitud"]
    cliente = payload["cliente"]
    contexto = payload["contexto"]

    required_fields = {
        "contexto.codigoInterno": contexto.get("codigoInterno"),
        "cliente.identificacion": cliente.get("identificacion"),
        "solicitud.modalidadCredito": solicitud.get("modalidadCredito"),
        "solicitud.destinacion": solicitud.get("destinacion"),
        "solicitud.montoSolicitado": solicitud.get("montoSolicitado"),
        "solicitud.numeroCuotas": solicitud.get("numeroCuotas"),
        "solicitud.valorCuota": solicitud.get("valorCuota"),
        "solicitud.periodoPagoCapital": solicitud.get("periodoPagoCapital"),
        "solicitud.periodoPagoInteres": solicitud.get("periodoPagoInteres"),
        "solicitud.tasaNominalCrea": solicitud.get("tasaNominalCrea"),
        "solicitud.medioPago": solicitud.get("medioPago"),
        "solicitud.fechaSolicitud": solicitud.get("fechaSolicitud"),
        "solicitud.sucursal": solicitud.get("sucursal"),
        "solicitud.pagare": solicitud.get("pagare"),
    }

    missing = [field_name for field_name, value in required_fields.items() if value in (None, "")]
    if missing:
        raise CreditValidationError(
            f"Faltan campos obligatorios para radicacion documentada: {', '.join(missing)}"
        )

    fecha_solicitud = str(solicitud["fechaSolicitud"]).strip()
    if not DATE_MM_DD_YYYY_RE.match(fecha_solicitud):
        raise CreditValidationError(
            "solicitud.fechaSolicitud debe usar formato MM/DD/YYYY segun la documentacion oficial"
        )

    validate_modality_destination(payload, line_config)
    validate_disbursement(payload, line_config)


def validate_microcredito_payload(data):
    cliente = data.get("cliente", {})
    credito = data.get("credito", {})
    codigo_interno = data.get("kIdTerc") or data.get("k_idterc") or credito.get("codigoInterno")

    fecha_plan = data.get("fechaInicialPlanPagos")
    if fecha_plan in (None, "") and isinstance(credito, dict):
        fecha_plan = credito.get("fechaInicialPlanPagos")
    if fecha_plan not in (None, ""):
        try:
            calculate_initial_payment_date(fecha_plan)
        except MicrocreditoPlanDateError as exc:
            raise CreditValidationError(
                "fechaInicialPlanPagos debe ser un día entre 1 y 31"
            ) from exc

    required_fields = {
        "kIdTerc": codigo_interno,
        "cliente.identificacion": cliente.get("identificacion") or data.get("nit"),
        "credito.modalidadCredito": credito.get("modalidadCredito"),
        "credito.destinacion": credito.get("destinacion"),
        "credito.montoSolicitado": credito.get("montoSolicitado"),
        "credito.numeroCuotas": credito.get("numeroCuotas"),
        "credito.valorCuota": credito.get("valorCuota"),
        "credito.periodoPagoCapital": credito.get("periodoPagoCapital"),
        "credito.periodoPagoInteres": credito.get("periodoPagoInteres"),
        "credito.tasaNominal": credito.get("tasaNominalCrea") or credito.get("tasaNominal"),
        "credito.medioPago": credito.get("medioPago"),
        "credito.fechaSolicitud": credito.get("fechaSolicitud"),
        "credito.factorComision": credito.get("factorContribucion") or credito.get("factorComision"),
    }

    missing = [field_name for field_name, value in required_fields.items() if value in (None, "")]
    if missing:
        raise CreditValidationError(
            f"Faltan campos obligatorios para radicacion de microcredito: {', '.join(missing)}"
        )

    fecha_solicitud = str(credito["fechaSolicitud"]).strip()
    if not DATE_MM_DD_YYYY_RE.match(fecha_solicitud):
        raise CreditValidationError(
            "credito.fechaSolicitud debe usar formato MM/DD/YYYY"
        )

    disbursements = data.get("formasDesembolso") or data.get("formas_desembolso") or []
    if not disbursements:
        raise CreditValidationError(
            "Se requiere al menos una forma de desembolso para radicacion de microcredito"
        )

    for index, record in enumerate(disbursements, start=1):
        required_disbursement_fields = {
            "tipoLiquidacion": record.get("tipoLiquidacion"),
            "codigoContCredito": record.get("codigoContCredito"),
            "tipoMovimiento": record.get("tipoMovimiento"),
            "sucursal": record.get("sucursal"),
        }
        missing_disbursement = [
            field_name
            for field_name, value in required_disbursement_fields.items()
            if value in (None, "")
        ]
        if missing_disbursement:
            raise CreditValidationError(
                f"Forma de desembolso #{index} incompleta: faltan {', '.join(missing_disbursement)}"
            )

    validate_microcredit_codebtors(data)


def validate_modality_destination(payload, line_config):
    solicitud = payload["solicitud"]
    modalidad = str(solicitud.get("modalidadCredito") or "").strip().upper()
    destinacion = str(solicitud.get("destinacion") or "").strip().upper()

    modality_config = line_config.get_modality(modalidad)
    if modality_config is None:
        raise CreditValidationError(
            f"La modalidad {modalidad or '<vacia>'} no esta habilitada para la linea {line_config.key}"
        )

    if destinacion not in modality_config.valid_destinations:
        raise CreditValidationError(
            f"La destinacion {destinacion or '<vacia>'} no corresponde a la modalidad {modalidad}"
        )


def validate_disbursement(payload, line_config):
    disbursements = payload["formasDesembolso"]
    if line_config.required_disbursement and not disbursements:
        raise CreditValidationError(
            "Se requiere al menos una forma de desembolso para la radicacion documentada"
        )

    for index, record in enumerate(disbursements, start=1):
        required_fields = {
            "tipoLiquidacion": record.get("tipoLiquidacion"),
            "codigoContCredito": record.get("codigoContCredito"),
            "tipoMovimiento": record.get("tipoMovimiento"),
            "sucursal": record.get("sucursal"),
        }
        missing = [
            field_name for field_name, value in required_fields.items() if value in (None, "")
        ]
        if missing:
            raise CreditValidationError(
                f"Forma de desembolso #{index} incompleta: faltan {', '.join(missing)}"
            )
