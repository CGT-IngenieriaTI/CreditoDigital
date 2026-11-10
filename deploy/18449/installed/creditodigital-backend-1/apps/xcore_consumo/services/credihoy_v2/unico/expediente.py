"""Checklist no ejecutable del contrato existente de radicacion controlada."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from uuid import UUID
from xml.etree import ElementTree as ET


def preparar_expediente_unico(*, identidad_linix_disponible=False):
    # Mirrors digital_operations/contracts.py + credihoy.normalize_context in api_data_v1.
    pendientes = [
        "credito.tasaNominalAnual", "credito.cuotaContractual",
        "evidenciaDecision.decisionId", "evidenciaDecision.autorizacionId", "evidenciaDecision.versionPolitica",
        "contextoLinix.sucursal", "contextoLinix.fechaSolicitud", "contextoLinix.fechaCuotaOrdinaria",
        "contextoLinix.parametros.amortizacion", "contextoLinix.parametros.formaAplicarTasa",
        "contextoLinix.parametros.medioPago", "contextoLinix.parametros.seguroVida",
        "contextoLinix.parametros.modalidadCapital", "contextoLinix.parametros.porcentajeCapitalizacion",
        "contextoLinix.parametros.valorTotalCapitalizacion", "contextoLinix.parametros.periodoPagoCapital",
        "contextoLinix.parametros.periodoPagoInteres", "contextoLinix.parametros.actividadEconomica",
        "contextoLinix.datosRadicacion.cliente.nombre", "contextoLinix.datosRadicacion.cliente.email",
        "contextoLinix.datosRadicacion.cliente.numeroCelular", "contextoLinix.datosRadicacion.promotor.codigo",
        "contextoLinix.datosRadicacion.promotor.kIdTerc", "contextoLinix.datosRadicacion.promotor.nombre",
    ]
    if not identidad_linix_disponible:
        pendientes.append("contextoLinix.kIdTerc")
    return {"estado": "BLOQUEADA", "contratoObjetivo": "credihoy-radicacion-controlada-v1",
        "producto": {"codigo": "390", "modalidad": "111", "destino": "CFA",
                     "certificacionId": "CREDIHOY_390_111_CFA_CD4D"},
        "camposPendientes": pendientes, "requestRadicacion": None,
        "autorizadoEnvioOracle": False, "decisionCrediticia": "NO_AUTORIZADA",
        "radicacionLinix": "NO_EJECUTADA"}


def preparar_request_offline(solicitud_id, acreditados, *, normalizar=None, construir_xml=None,
                             administrativa=False, fuentes=None, borrador_previo=None):
    """Borrador privado: ningun default contractual, reserva, decision o envio."""
    solicitud_id = str(UUID(str(solicitud_id)))
    base = preparar_expediente_unico()
    request = {
        "versionContrato": base["contratoObjetivo"], "solicitudId": solicitud_id,
        "intencionId": None, "producto": deepcopy(base["producto"]),
        "titular": {"tipoDocumento": None, "numeroDocumento": None},
        "credito": {"montoSolicitado": None, "plazoMeses": None, "periodicidad": None,
                    "moneda": None, "tasaNominalAnual": None, "cuotaContractual": None},
        "evidenciaDecision": {"decisionId": None, "autorizacionId": None, "versionPolitica": None},
        "contextoLinix": {"kIdTerc": None, "sucursal": None, "fechaSolicitud": None,
            "fechaCuotaOrdinaria": None, "parametros": {},
            "datosRadicacion": {"cliente": {"nombre": None, "email": None, "numeroCelular": None},
                                "promotor": {"codigo": None, "kIdTerc": None, "nombre": None}}},
    }
    if administrativa:
        request["versionContrato"] = "credihoy-radicacion-administrativa-v1"
        del request["evidenciaDecision"]
        request["autorizacionAdministrativa"] = {
            "autorizacionId": None, "referencia": None,
            "modelo": "CREDIHOY_VAL_EMPLEADO", "alcance": "RADICACION_ADMINISTRATIVA_PENDIENTE",
        }
        request["evidenciaTasa"] = {"unidad": None, "campoXml": None, "referenciaCertificacion": None}
    for path in base["camposPendientes"]:
        if path.startswith("contextoLinix.parametros."):
            request["contextoLinix"]["parametros"][path.rsplit(".", 1)[1]] = None
    if borrador_previo is not None:
        prior = borrador_previo["requestBorrador"]
        if (prior.get("solicitudId") != solicitud_id or prior.get("producto") != request["producto"]
                or prior.get("versionContrato") not in
                {base["contratoObjetivo"], request["versionContrato"]}):
            raise ValueError("Borrador previo no corresponde al expediente/producto/contrato")
        def preserve(target, origin):
            for name, value in target.items():
                if name in {"versionContrato", "autorizacionAdministrativa", "evidenciaTasa"}:
                    continue
                old = origin.get(name)
                if isinstance(value, dict):
                    if isinstance(old, dict):
                        preserve(value, old)
                elif old is not None:
                    target[name] = deepcopy(old)
        preserve(request, prior)
    fixed = {"versionContrato", "solicitudId", "producto"}
    fixed_paths = {"autorizacionAdministrativa.modelo", "autorizacionAdministrativa.alcance"}
    for path, value in acreditados.items():
        parts = path.split(".")
        if parts[0] in fixed or path in fixed_paths:
            raise ValueError("No se permite cambiar identidad del expediente o producto")
        node = request
        for name in parts[:-1]:
            if name not in node or not isinstance(node[name], dict):
                raise ValueError("Campo fuera del contrato")
            node = node[name]
        if parts[-1] not in node:
            raise ValueError("Campo fuera del contrato")
        if borrador_previo is not None and node[parts[-1]] is not None and node[parts[-1]] != value:
            raise ValueError("No sobrescribir un valor del borrador previo")
        node[parts[-1]] = deepcopy(value)

    matrix = []
    def visit(node, prefix=""):
        for name, value in node.items():
            path = prefix + name
            if isinstance(value, dict):
                visit(value, path + ".")
                continue
            if path.startswith("evidenciaDecision."):
                owner = "AUTORIDAD_DECISION_CONTRATO_ACTUAL"
            elif path.startswith("autorizacionAdministrativa."):
                owner = "AUTORIDAD_ADMINISTRATIVA_SIN_DECISION_CREDITICIA"
            elif path.startswith("evidenciaTasa."):
                owner = "CERTIFICACION_CREDITO_UNIDAD_Y_VIGENCIA"
            elif path == "credito.cuotaContractual":
                owner = "CALCULO_OFICIAL_LINIX"
            elif path.startswith("contextoLinix.") or path == "credito.tasaNominalAnual":
                owner = "CREDITO_Y_FUENTE_LINIX"
            elif path.startswith("titular.") or path.startswith("credito."):
                owner = "EXPEDIENTE_Y_CONFIRMACION_TITULAR"
            else:
                owner = "CONTRATO_E_IDENTIFICADORES_TECNICOS"
            available = value is not None and value != ""
            source, pending = _fuente_contractual(path)
            supplied = (fuentes or {}).get(path)
            matrix.append({"campo": path, "responsableFuente": owner,
                           "disponible": available, "fuenteRequerida": source,
                           "referenciaVerificada": supplied,
                           "estadoDato": ("OBTENIDO_ACREDITADO" if available and supplied else
                                          "DISPONIBLE_REQUIERE_CONFIRMACION" if available else pending)})
    visit(request)
    missing = [row["campo"] for row in matrix if not row["disponible"]]
    result = {"estado": "RADICACION_PREPARADA_SIN_EJECUTAR", "requestBorrador": request,
              "matrizParametros": matrix, "camposPendientes": missing,
              "esquemaValido": False, "xmlValido": False, "xml": None,
              "reservaCreada": False, "autorizadoEnvioOracle": False,
              "autoridadDecisionValidada": False, "radicacionLinix": "NO_EJECUTADA"}
    if administrativa:
        result.update(estado="RADICACION_ADMINISTRATIVA_6964_PREPARADA_OFFLINE",
                      autoridadAdministrativaValidada=False, decisionCrediticia="NO_AUTORIZADA",
                      actividadLimite=6964, estadoTramite="PENDIENTE_REVISION")
    if borrador_previo is not None:
        result["procedenciaBorrador"] = "DERIVACION_OFFLINE_ORIGINAL_INTACTO"
    result["preflightDatos"] = {
        "camposTotales": len(matrix),
        "camposPoblados": len(matrix) - len(missing),
        "camposAcreditados": sum(row["estadoDato"] == "OBTENIDO_ACREDITADO" for row in matrix),
        "camposFaltantes": len(missing),
        "camposPorConfirmar": sum(row["estadoDato"] == "DISPONIBLE_REQUIERE_CONFIRMACION" for row in matrix),
        "productoValidado": request["producto"] == base["producto"],
        "solicitudValidada": request["solicitudId"] == solicitud_id,
        "intencionProductivaCreada": False,
        "checkpointEsperado": [113, 3883, 6964] if administrativa else None,
        "decisionFinalCreada": False,
        "bloqueadoPorCamposAusentes": bool(missing),
    }
    if not missing and normalizar is not None and construir_xml is not None:
        normalized = normalizar(deepcopy(request))
        xml = construir_xml(normalized)
        ET.fromstring(xml)
        result.update(requestBorrador=normalized, esquemaValido=True, xmlValido=True, xml=xml)
    return result


def acreditar_agencia_asociado(k_idterc, fila_cliente):
    """Solo AP014 del mismo tercero; nunca agencia de nomina/promotor/domicilio."""
    try:
        tercero = Decimal(str(k_idterc))
        asociado = Decimal(str(fila_cliente["K_IDTERC"]))
        agencia = str(fila_cliente["K_SUCURS"])
        if (not tercero.is_finite() or tercero <= 0 or tercero != tercero.to_integral_value()
                or asociado != tercero or not re.fullmatch(r"[0-9]{1,10}", agencia)):
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError("AGENCIA_ASOCIADO_NO_ACREDITADA") from None
    return agencia


def _fuente_contractual(path):
    if path == "intencionId":
        return "RadicationOperation.intent_id; prepare con unicidad por solicitud/intencion/clave", "PENDIENTE_RESERVA_DURABLE"
    if path.startswith("autorizacionAdministrativa."):
        return "Autoridad administrativa identificada y vigente, vinculada al fingerprint", "PENDIENTE_AUTORIZACION_ADMINISTRATIVA"
    if path == "credito.cuotaContractual":
        return "CALCULO_CUOTA_CREDITO y dependencias: efectos y parametros oficiales por certificar", "CALCULO_OFICIAL_LINIX_PENDIENTE"
    if path == "credito.tasaNominalAnual" or path.startswith("evidenciaTasa."):
        return "CONDICION_TASA_111.csv; PK_CA_CONDICION eval6 N.A.M.V.; XML V_TASA_NOMINAL sin conversion", "DEFINICION_CREDITO_PENDIENTE"
    if path == "contextoLinix.parametros.medioPago":
        return ("Colaborador Congente vigente acreditado institucionalmente -> Nomina; externo acreditado -> "
                "Ventanilla; codigo AP016 aplicable, no inferir vinculo de EM ni generalizar100/999; "
                "declaracion no es mandato de descuento"), "VINCULO_Y_CATALOGO_MEDIO_PAGO_PENDIENTES"
    if path in {"contextoLinix.kIdTerc", "contextoLinix.datosRadicacion.cliente.nombre"}:
        return ("Precarga preservada: primerNombre/segundoNombre/primerApellido/segundoApellido; "
                "K_IDTERC del request hechos V2, contrastar GR001MTERCERO; idSocio no se presume equivalente"), "FUENTE_EXISTENTE_PENDIENTE_DE_TRAZAR"
    if path == "contextoLinix.sucursal":
        return ("LINIX.AP014MCLIENTE.K_SUCURS del mismo K_IDTERC: agencia oficial del asociado; "
                "regla Congente, no agencia fija/domicilio/operador/promotor"), "AGENCIA_ASOCIADO_PENDIENTE"
    if path.startswith("contextoLinix.datosRadicacion.promotor."):
        return "Promotor autorizado para canal; no inferir de usuario Django, asociado o empleador", "DEFINICION_CREDITO_PENDIENTE"
    if path.startswith("contextoLinix.fecha"):
        return "Regla operativa de fechaSolicitud/primera cuota; no usar SYSDATE del wrapper como seleccion", "DEFINICION_CREDITO_PENDIENTE"
    if path.startswith("contextoLinix.parametros."):
        fields = {
            "amortizacion": "A_AMORTIZACION",
            "formaAplicarTasa": "A_FORMA_APLICAR_TASA; seleccion eval6 fija N.A.M.V.",
            "seguroVida": "A_SEGURO_VIDA; wrapper CALCULO_CUOTA_CREDITO lee CA084/DR039/DR092",
            "modalidadCapital": "A_MODALCAPI", "porcentajeCapitalizacion": "V_PORCAPITA",
            "valorTotalCapitalizacion": "V_VALTOTCAP", "periodoPagoCapital": "A_PERIODO_PAGO_CAPITAL",
            "periodoPagoInteres": "A_PERIODO_PAGO_INTERES",
            "actividadEconomica": "A_ACTIVIDAD_ECONOMICA; consultarSocio no la devuelve",
        }
        target = fields[path.rsplit(".", 1)[1]]
        return (target + "; contexto oficial 390/111/CFA pendiente de certificar, "
                "no sustituir por defaults de xml_builder"), "DEFINICION_CREDITO_PENDIENTE"
    return "Borrador cifrado y expediente persistido de la misma solicitud", "FUENTE_EXISTENTE_PENDIENTE_DE_TRAZAR"
