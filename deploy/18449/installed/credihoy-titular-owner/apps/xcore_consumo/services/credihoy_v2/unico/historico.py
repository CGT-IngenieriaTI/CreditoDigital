"""Replay local de archivos privados. No HTTP, Oracle ni datos favorables ficticios."""

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from ..base import EstadoCertificacion as EC, Hecho, instante
from ..externos import normalizar_hdc_v2
from ..gateway import adaptar_hdc_a_v2, adaptar_preselecta_a_v2, materializar_cuotas_hdc_brutas
from ..hechos import HechosExternosCrediHoyV2
from .dto import EntradaCredihoyUnicoV2, RecogidaCredihoyUnicoV2
from .laboratorio import PRODUCTO, serializar_evaluacion
from .motor import construir_capacidad_unico_v2, evaluar_capacidad_unico_v2
from .reglas import HechosReglasUnicoV2, evaluar_reglas_unico_v2
from .linix_historico import EvidenciaLinixHistorica, cargar_linix_historico
from .expediente import preparar_expediente_unico


class HistoricoInvalido(ValueError):
    """Solo codigos saneados, nunca contenido privado."""


def _pares(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise HistoricoInvalido("JSON_CLAVE_DUPLICADA")
        result[k] = v
    return result


def _archivo(path):
    from django.conf import settings
    try:
        p = Path(path).resolve(strict=True)
        repo = Path(settings.BASE_DIR).resolve().parent
        if p.is_relative_to(repo) or any((d / ".git").exists() for d in p.parents):
            raise HistoricoInvalido("EVIDENCIA_DENTRO_DE_GIT")
        if str(p).startswith(("\\\\", "//")) or not p.is_file() or p.stat().st_size > 2_000_000:
            raise HistoricoInvalido("ARCHIVO_NO_LOCAL_O_EXCESIVO")
        raw = p.read_bytes()
        data = json.loads(raw.decode("utf-8-sig"), parse_float=Decimal, object_pairs_hook=_pares,
            parse_constant=lambda _: (_ for _ in ()).throw(HistoricoInvalido("JSON_NO_FINITO")))
        if not isinstance(data, dict):
            raise HistoricoInvalido("SCHEMA_INVALIDO")
        return data, hashlib.sha256(raw).hexdigest()
    except HistoricoInvalido:
        raise
    except Exception:
        raise HistoricoInvalido("ARCHIVO_PRIVADO_INVALIDO") from None


@dataclass(frozen=True, repr=False)
class HistoricosCredihoy:
    documento: str
    tipo_documento: str
    apellido: str
    fecha_expedicion: date | None
    externos: HechosExternosCrediHoyV2
    fecha_pre_original: str
    fecha_hdc_original: str
    digest_pre: str
    digest_hdc: str
    estado_pre: str
    estado_hdc: str
    esquema_pre: str
    esquema_hdc: str
    advertencias: tuple[str, ...]
    digest_fecha: str | None = None
    apellidos_completos: str | None = None
    linix: EvidenciaLinixHistorica | None = None
    digest_xml: str | None = None

    @property
    def huella(self):
        return hashlib.sha256((self.digest_pre + self.digest_hdc + (self.digest_fecha or "")
                               + (self.linix.huella if self.linix else "") + (self.digest_xml or "")).encode()).hexdigest()

    def verificar_integridad(self, pre_path, hdc_path, fecha_path=None, linix_path=None, s14_path=None,
                            confirmacion_salarial_path=None, hdc_xml_path=None):
        _, a = _archivo(pre_path)
        _, b = _archivo(hdc_path)
        if (a, b) != (self.digest_pre, self.digest_hdc):
            raise HistoricoInvalido("HISTORICO_MODIFICADO")
        if self.digest_xml is not None and (hdc_xml_path is None or _xml_privado(hdc_xml_path)[1] != self.digest_xml):
            raise HistoricoInvalido("XML_HISTORICO_MODIFICADO")
        if self.digest_fecha is not None and (fecha_path is None or _archivo(fecha_path)[1] != self.digest_fecha):
            raise HistoricoInvalido("FECHA_VERIFICADA_MODIFICADA")
        if self.linix is not None:
            from .linix_historico import _leer
            if linix_path is None or _leer(linix_path)[1] != self.linix.digest:
                raise HistoricoInvalido("EVIDENCIA_LINIX_MODIFICADA")
            if self.linix.digest_s14 is not None and (s14_path is None or _archivo(s14_path)[1] != self.linix.digest_s14):
                raise HistoricoInvalido("EVIDENCIA_S14_MODIFICADA")
            componente = self.linix.componente_salarial_piloto
            if componente is not None and (confirmacion_salarial_path is None
                    or _archivo(confirmacion_salarial_path)[1] != componente.digest_confirmacion):
                raise HistoricoInvalido("CONFIRMACION_SALARIAL_MODIFICADA")


def _xml_privado(path):
    from django.conf import settings
    p = Path(path).resolve(strict=True)
    if (not p.is_file() or p.stat().st_size > 2_000_000 or str(p).startswith(("\\\\", "//"))
            or p.is_relative_to(Path(settings.BASE_DIR).resolve().parent)
            or any((d / ".git").exists() for d in p.parents)):
        raise HistoricoInvalido("XML_PRIVADO_FUERA_DE_GIT_REQUERIDO")
    raw = p.read_bytes()
    return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()


def cargar_historicos(pre_path, hdc_path, *, fecha_verificada_path=None, vincular_fecha_contextual=False,
                     linix_path=None, fecha_evaluacion=None, fecha_consulta_linix=None, s14_path=None,
                     confirmacion_salarial_path=None, hdc_xml_path=None):
    """PRESELECTA: normalizador oficial puro; HDC detallado: mapper V2 existente."""
    etapa = "ARCHIVOS"
    try:
        p, dp = _archivo(pre_path)
        h, dh = _archivo(hdc_path)
        if p["schema"] != "credihoy-piloto-preselecta-v1" or h["schema"] != "credihoy-piloto-hdc-v1":
            raise HistoricoInvalido("SCHEMA_HISTORICO_NO_SOPORTADO")
        etapa = "IDENTIDAD_Y_FECHAS"
        pr, hr = p["request_payload"], h["request_payload"]
        document = pr["idNumber"]
        tipo = {"1": "CC", "2": "CE"}.get(str(pr["idType"]), pr["idType"])
        apellido = pr["firstLastName"]
        if (type(document) is not str or not re.fullmatch(r"[0-9]{1,30}", document)
                or tipo not in {"CC", "CE"} or type(apellido) is not str or not apellido.strip() or len(apellido) > 80):
            raise HistoricoInvalido("IDENTIDAD_INVALIDA")
        if (hr["numero_identificacion"] != document or str(hr["tipo_identificacion"]) != str(pr["idType"])
                or hr["primer_apellido"] != apellido or h["consulta"]["identificacion_digitada"] != document
                or p["response_payload"]["nationalPerson"]["identification"]["number"] != document):
            raise HistoricoInvalido("IDENTIDADES_INCONSISTENTES")
        fecha_pre = p["resultado_normalizado"]["fecha_consulta"]
        fecha_hdc = h["consulta"]["fecha_consulta"]
        fp, fh = datetime.fromisoformat(fecha_pre), datetime.fromisoformat(fecha_hdc)
        if "T" not in fecha_pre or "T" not in fecha_hdc or p["response_payload"]["inquiryDate"] != fecha_pre:
            raise HistoricoInvalido("FECHAS_ORIGINALES_INCONSISTENTES")
        original_exp = p["response_payload"]["nationalPerson"]["identification"].get("issueDate")
        expedicion = date.fromisoformat(original_exp) if original_exp else None
        digest_fecha = None
        if fecha_verificada_path is not None:
            verified, digest_fecha = _archivo(fecha_verificada_path)
            shape = set(verified)
            identity_bound = (shape == {"numeroDocumento", "fechaExpedicion"} and verified["numeroDocumento"] == document)
            contextual_bound = (shape == {"fechaExpedicion"} and vincular_fecha_contextual is True)
            if (not (identity_bound or contextual_bound)
                    or type(verified["fechaExpedicion"]) is not str
                    or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", verified.get("fechaExpedicion", ""))):
                raise HistoricoInvalido("FECHA_VERIFICADA_O_IDENTIDAD_INVALIDA")
            expedicion = date.fromisoformat(verified["fechaExpedicion"])
            if original_exp and expedicion.isoformat() != original_exp:
                raise HistoricoInvalido("FECHA_VERIFICADA_CONTRADICE_PRESELECTA")
        # Imports ONLY pure contracts/projections from the read-only provider checkout.
        etapa = "PRESELECTA_OFICIAL"
        from integrations.services.preselecta_normalizer import normalize_preselecta, is_legacy_strategy_response
        from integrations.services.query_results import QueryContext, TechnicalOutcome, TechnicalStatus, classify_provider_code
        from apps.integraciones.risk_gateway.contratos import PreselectaNormalizado
        rid = uuid5(NAMESPACE_URL, dp)
        raw = p["response_payload"]
        segundo_apellido = raw["nationalPerson"].get("secondLastName")
        primero_persona = raw["nationalPerson"].get("firstLastName")
        if segundo_apellido is not None and type(segundo_apellido) is not str:
            raise HistoricoInvalido("APELLIDOS_HISTORICOS_INVALIDOS")
        apellidos_completos = None
        if segundo_apellido and segundo_apellido.strip():
            if type(primero_persona) is not str or primero_persona.strip().upper() != apellido.strip().upper():
                raise HistoricoInvalido("APELLIDOS_HISTORICOS_INCONSISTENTES")
            apellidos_completos = apellido.strip() + " " + segundo_apellido.strip()
        outcome = (TechnicalOutcome(TechnicalStatus.COMPLETED) if is_legacy_strategy_response(raw) else
                   classify_provider_code(raw.get("typeResponse"), product="PRESELECTA"))
        if raw.get("Fault") or outcome.status not in {TechnicalStatus.COMPLETED, TechnicalStatus.NO_INFORMATION}:
            raise HistoricoInvalido("PRESELECTA_NO_RECUPERABLE")
        normalized = normalize_preselecta(raw, consulta_id=uuid5(NAMESPACE_URL, "pre:" + dp),
            context=QueryContext(request_id=rid, origin="HISTORICO_CONTROLADO", consumer="cd5a-local"),
            outcome=outcome, fecha_registro=datetime.now(timezone.utc))
        pre = PreselectaNormalizado.desde_payload({"requestId": str(rid),
            "versionContrato": "PRESELECTA_NORMALIZADO_V1", "resultado": normalized.to_dict()})
        outcome_hdc = classify_provider_code(h["consulta"]["respuesta"], product="HDC")
        if outcome_hdc.status not in {TechnicalStatus.COMPLETED, TechnicalStatus.NO_INFORMATION}:
            raise HistoricoInvalido("HDC_NO_RECUPERABLE")
        if outcome_hdc.status == TechnicalStatus.NO_INFORMATION and h["obligaciones"]:
            raise HistoricoInvalido("HDC_SIN_INFORMACION_CON_DETALLE_INCONSISTENTE")
        etapa = "HDC_DETALLADO_V2"
        if not isinstance(h["obligaciones"], list):
            raise HistoricoInvalido("HDC_OBLIGACIONES_REQUIERE_LISTA")
        digest_xml = None
        if hdc_xml_path is not None:
            etapa = "HDC_XML_OFICIAL"
            from integrations.services.evidence import parse_xml
            from api.services.hdc_normalizer import normalize_hdc
            from apps.integraciones.risk_gateway.contratos import HDCNormalizado
            xml, digest_xml = _xml_privado(hdc_xml_path)
            root = parse_xml(xml)
            informes = [] if root is None else root.findall("Informe")
            if len(informes) != 1:
                raise HistoricoInvalido("XML_INFORME_UNICO_REQUERIDO")
            informe = informes[0]
            if (informe.get("identificacionDigitada") != document
                    or informe.get("tipoIdDigitado") != str(hr["tipo_identificacion"])
                    or informe.get("fechaConsulta") != fecha_hdc
                    or informe.get("respuesta") != h["consulta"]["respuesta"]):
                raise HistoricoInvalido("XML_IDENTIDAD_O_CORTE_INCONSISTENTE")
            xml_ref = uuid5(NAMESPACE_URL, "replay-xml:" + digest_xml)
            normalizado = normalize_hdc(xml, consulta_id=xml_ref, request_id=xml_ref)
            if len(normalizado.obligaciones) != len(h["obligaciones"]):
                raise HistoricoInvalido("XML_COBERTURA_NO_COINCIDE_HISTORICO")
            oficial = HDCNormalizado.desde_payload({"requestId": str(xml_ref),
                "versionContrato": "HDC_NORMALIZADO_V1", "resultado": normalizado.to_dict()})
            externos = adaptar_hdc_a_v2(oficial)
        else:
            externos = normalizar_hdc_v2(h, referencia_evidencia=str(uuid5(NAMESPACE_URL, "hdc:" + dh)),
                fecha_consulta=fh if fh.utcoffset() is not None else None, fecha_consulta_original=fecha_hdc)
        externos = replace(externos, preselecta=adaptar_preselecta_a_v2(pre))
        warnings = ["VIGENCIA_PARA_DECISION_NO_CERTIFICADA", "INGRESO_INTERNO_NO_DISPONIBLE",
                    "AGREGADO_OFICIAL_HDC_NO_RECUPERADO", "HECHOS_INTERNOS_C1_C4_NO_DISPONIBLES"]
        if materializar_cuotas_hdc_brutas(externos).disponible:
            warnings.remove("AGREGADO_OFICIAL_HDC_NO_RECUPERADO")
            warnings.append("CUOTAS_APLICABLES_VIGENCIA_CDC_Y_EXCLUSION_CONGENTE_PENDIENTES")
        elif digest_xml:
            warnings.remove("AGREGADO_OFICIAL_HDC_NO_RECUPERADO")
            warnings.append("AGREGADO_OFICIAL_HDC_NO_UTILIZABLE")
        for warning in pre["resultado"]["cobertura"]["advertencias"]:
            code = warning["codigo"]
            if code in {"SCORE_LEGACY_NO_NORMATIVO", "CAMPO_PROVEEDOR_AMBIGUO", "SCORE_SIN_HOMOLOGACION_CERTIFICADA", "SEMANTICA_ESTRATEGIA_NO_CERTIFICADA"}:
                if "PRESELECTA_" + code not in warnings:
                    warnings.append("PRESELECTA_" + code)
        if fp.utcoffset() is None or fh.utcoffset() is None:
            warnings.append("ZONA_HORARIA_ORIGINAL_NO_DECLARADA")
        if h["persona"]["identificacion"].get("numero") != document:
            warnings.append("FORMATO_IDENTIFICACION_PERSONA_HDC_NO_HOMOLOGADO")
        if expedicion is None:
            warnings.append("FECHA_EXPEDICION_VERIFICADA_REQUERIDA_PARA_SOLICITANTE")
        elif not original_exp:
            warnings.append("FECHA_EXPEDICION_VERIFICADA_SOLO_PARA_REGISTRO_LOCAL")
        elif h["persona"]["identificacion"].get("fechaExpedicion") != expedicion.isoformat():
            warnings.append("FECHA_EXPEDICION_HDC_NO_EQUIVALENTE_PRESELECTA")
        linix = None
        if (s14_path is not None or confirmacion_salarial_path is not None) and linix_path is None:
            raise HistoricoInvalido("S14_REQUIERE_IDENTIDAD_LINIX_VALIDADA")
        if linix_path is not None:
            if not isinstance(fecha_evaluacion, datetime):
                raise HistoricoInvalido("FECHA_EVALUACION_LINIX_REQUERIDA")
            linix = cargar_linix_historico(linix_path, documento=document, fecha_evaluacion=fecha_evaluacion,
                                         fecha_consulta=fecha_consulta_linix, s14_path=s14_path,
                                         confirmacion_salarial_path=confirmacion_salarial_path)
            warnings.remove("HECHOS_INTERNOS_C1_C4_NO_DISPONIBLES")
            warnings.append("HECHOS_INTERNOS_LINIX_PARCIALES")
            warnings.extend(linix.advertencias)
        return HistoricosCredihoy(document, tipo, apellido, expedicion, externos, fecha_pre, fecha_hdc,
            dp, dh, outcome.status.value, outcome_hdc.status.value, p["schema"], h["schema"], tuple(warnings),
            digest_fecha, apellidos_completos, linix, digest_xml)
    except HistoricoInvalido:
        raise
    except Exception as exc:
        raise HistoricoInvalido("HISTORICO_INCOMPATIBLE_" + etapa + "_" + type(exc).__name__) from None


def hechos_reglas_historicos(datos, fecha):
    """Proyecta solo hechos comprobables del expediente ya validado por el loader."""
    instante(fecha, "fecha_evaluacion")
    from .proyeccion_interna import proyectar_reglas_internas
    internos = (proyectar_reglas_internas(datos.linix.hechos, fecha=fecha)
                if datos.linix is not None else HechosReglasUnicoV2())
    consulta = Hecho(fuente="HISTORICO_CONSULTA_NO_COMPROBADA")
    fuentes = [("PRESELECTA", datos.estado_pre, datos.fecha_pre_original, datos.digest_pre, "response_payload.inquiryDate")]
    if datos.digest_xml:
        fuentes.insert(0, ("HDC", datos.estado_hdc, datos.fecha_hdc_original, datos.digest_xml, "Informe/@respuesta"))
    elif "FORMATO_IDENTIFICACION_PERSONA_HDC_NO_HOMOLOGADO" not in datos.advertencias:
        fuentes.append(("HDC", datos.estado_hdc, datos.fecha_hdc_original, datos.digest_hdc, "consulta.respuesta"))
    for proveedor, estado, fecha_original, digest, ruta in fuentes:
        original = datetime.fromisoformat(fecha_original)
        # Sin zona no se inventa un instante; un mismo dia no permite ordenar las horas.
        anterior = (original <= fecha if original.utcoffset() is not None
                    else original.date() < fecha.date())
        if estado in {"COMPLETADO", "SIN_INFORMACION"} and anterior:
            consulta = Hecho(valor=True, disponible=True, fuente=proveedor + "_HISTORICO_VALIDADO",
                referencia_evidencia=digest, ruta_origen=ruta, fecha_corte=original.date(),
                fecha_consulta=original if original.utcoffset() is not None else None,
                estado_certificacion=EC.CERTIFICADO,
                observacion="Consulta historica realizada con identidad validada; no certifica vigencia ni completitud financiera")
            break
    return replace(internos, consulta_central_realizada=consulta)


def evaluar_historico(datos, solicitud, request_id, fecha):
    entrada = EntradaCredihoyUnicoV2(monto_solicitado=Decimal(solicitud["monto"]),
        plazo_meses=solicitud["plazoMeses"], periodicidad_pago=solicitud["periodicidadPago"],
        modalidad_codigo="111", recogida=RecogidaCredihoyUnicoV2(aplica=solicitud["recogerCredihoy"]))
    # Declaraciones frontend and PRESELECTA scores are NOT certified monthly income.
    capacidad = (construir_capacidad_unico_v2(entrada, hechos_internos=datos.linix.hechos,
        hechos_externos=datos.externos) if datos.linix else
        evaluar_capacidad_unico_v2(entrada, ingresos_mensuales=Hecho(fuente="API_DATA_V2_NO_CONSUMIDA"),
            cuotas_deudas_hdc_brutas=materializar_cuotas_hdc_brutas(datos.externos)))
    if datos.digest_xml:
        # El agregado consistente no certifica los filtros normativos de aplicabilidad.
        from .dto import EstadoCapacidadUnico, EstadoOperandoUnico
        pendiente = replace(capacidad.cuotas_deudas_hdc_aplicables, valor=None, disponible=False,
            estado=EstadoOperandoUnico.HOMOLOGACION_HDC_PENDIENTE,
            observacion="VIGENCIA_CDC_Y_EXCLUSION_CONGENTE_NO_CERTIFICADAS")
        capacidad = replace(capacidad, cuotas_deudas_hdc_aplicables=pendiente,
            capacidad_pago=replace(capacidad.capacidad_pago, valor=None, disponible=False,
                estado=EstadoOperandoUnico.NO_DISPONIBLE), estado=(capacidad.estado if capacidad.inconsistencias
                    else EstadoCapacidadUnico.NO_EVALUABLE),
            operandos_faltantes=tuple(n for n in ("ingresos_mensuales", "cuotas_deudas_hdc_brutas",
                "cuota_credihoy_recogido", "cuotas_deudas_hdc_aplicables", "cuota_nuevo_credito", "gastos_familiares")
                if n == "cuotas_deudas_hdc_aplicables" or not getattr(capacidad, n).disponible))
    reglas = evaluar_reglas_unico_v2(entrada, hechos=hechos_reglas_historicos(datos, fecha), capacidad=capacidad)
    result = serializar_evaluacion(capacidad, reglas, request_id, fuente="HISTORICO_CONTROLADO", fecha=fecha)
    result["evidenciaHistorica"] = {"PRESELECTA": {"fechaOriginal": datos.fecha_pre_original, "estado": datos.estado_pre,
        "versionOrigen": datos.esquema_pre, "versionAdaptada": "PRESELECTA_NORMALIZADO_V1",
        "camposDisponibles": {k: getattr(datos.externos.preselecta, k).disponible for k in ("decision", "riesgo_score", "score")}},
        "HDC": {"fechaOriginal": datos.fecha_hdc_original, "estado": datos.estado_hdc,
            "versionOrigen": datos.esquema_hdc, "versionAdaptada": "credihoy-externos-v2",
            "cuentas": len(datos.externos.cuentas), "posiblesDuplicados": len(datos.externos.posibles_duplicados),
            "agregadoOficialDisponible": materializar_cuotas_hdc_brutas(datos.externos).disponible,
            "coberturaCertificada": False},
        "vigenciaParaDecision": "NO_CERTIFICADA", "advertencias": list(datos.advertencias)}
    if datos.linix is not None:
        result["evidenciaHistorica"]["LINIX"] = datos.linix.resumen()
        result["preparacionRadicacion"] = preparar_expediente_unico(identidad_linix_disponible=True)
    return result


def revisar_capacidad_historica(datos, solicitud, request_id, fecha, *, declaracion_ingresos):
    """Revision no persistente. Las hipotesis no sustituyen hechos homologados."""
    from .ingresos_piloto import ingreso_utilizable_piloto
    resultado = evaluar_historico(datos, solicitud, request_id, fecha)
    bruto = materializar_cuotas_hdc_brutas(datos.externos)
    componente = datos.linix.componente_salarial_piloto if datos.linix else None
    salario = Hecho(fuente="COMPONENTE_SALARIAL_NO_DISPONIBLE")
    if componente is not None:
        # Valida identidad, hashes y fechas; no concede habilitacion de politica.
        ingreso_utilizable_piloto(componente, declaracion_ingresos,
            documento=datos.documento, fecha=fecha)
        if (componente.salario_mensual.fecha_consulta <= fecha
                and componente.fecha_confirmacion <= fecha.date()
                and declaracion_ingresos.fecha_declaracion <= fecha.date()
                and declaracion_ingresos.fecha_declaracion >= componente.fecha_confirmacion):
            salario = componente.salario_mensual
    escenarios = []
    condiciones = ["SALARIO_COMO_UNICO_COMPONENTE_COMPUTABLE_SOLO_EN_ESCENARIO",
        "OTROS_INGRESOS_EXISTENTES_EXCLUIDOS_SIN_ASIGNAR_CERO",
        "HDC_HISTORICO_Y_SALARIO_DE_OTRO_CORTE_NO_CERTIFICAN_VIGENCIA_CONJUNTA",
        "OBLIGACIONES_ABIERTAS_Y_EXCLUSION_CONGENTE_PENDIENTES_CERTIFICACION"]
    entrada = EntradaCredihoyUnicoV2(monto_solicitado=Decimal(solicitud["monto"]),
        plazo_meses=solicitud["plazoMeses"], periodicidad_pago=solicitud["periodicidadPago"],
        modalidad_codigo="111", recogida=RecogidaCredihoyUnicoV2(aplica=solicitud["recogerCredihoy"]))
    if datos.digest_xml and bruto.disponible and salario.disponible and not entrada.recogida.aplica:
        agregado = datos.externos.evidencia_gateway_hdc["resultado"]["endeudamiento_actual"]
        cdc = [r for r in agregado["desglose_tipo_cuenta"]
               if r["codigoTipo"] == "CDC" and r["calidadDeudor"] == "Principal"]
        hipotesis = [("CDC_INCLUIDO", bruto.valor)]
        if len(cdc) == 1 and cdc[0]["cuota_pesos"] is not None and Decimal("0") <= cdc[0]["cuota_pesos"] <= bruto.valor:
            from ..aritmetica import precision_exacta
            from decimal import localcontext
            with localcontext() as ctx:
                ctx.prec = precision_exacta(bruto.valor, cdc[0]["cuota_pesos"])
                hipotesis.append(("CDC_EXCLUIDO", bruto.valor - cdc[0]["cuota_pesos"]))
        for codigo, valor in hipotesis:
            aplicable = Hecho(valor=valor, disponible=True, fuente="HIPOTESIS_CONDICIONADA_NO_POLITICA",
                estado_certificacion=EC.OBSERVADO, referencia_evidencia=datos.digest_xml,
                ruta_origen="InfoAgregada/Resumen/Saldos/@cuotaMensual",
                observacion=codigo + "; no cuota aplicable certificada")
            capacidad = evaluar_capacidad_unico_v2(entrada, ingresos_mensuales=salario,
                cuotas_deudas_hdc_brutas=bruto, cuotas_aplicables_explicitas=aplicable)
            def dinero(h):
                return {"disponible": h.disponible, "valor": str(h.valor) if h.valor is not None else None}
            escenarios.append({"codigo": codigo, "naturaleza": "CONDICIONADO_NO_DECISORIO",
                "condiciones": condiciones, "ingresoUtilizado": dinero(salario),
                "cuotaHdcAplicableHipotetica": dinero(aplicable), "gastosFamiliares": dinero(capacidad.gastos_familiares),
                "cuotaNuevaParaCapacidad": dinero(capacidad.cuota_nuevo_credito),
                "capacidadMatematica": dinero(capacidad.capacidad_pago), "autorizadoDecisionCrediticia": False})
    resultado["revisionHistorica"] = {"noPersistida": True, "version": "REPLAY_XML_9FB_1",
        "huellaFuentes": datos.huella, "fechaHdcOriginal": datos.fecha_hdc_original,
        "fechaSalarioObservado": salario.fecha_consulta.isoformat() if salario.fecha_consulta else None,
        "ingresoObservado": {"disponible": salario.disponible,
            "valor": str(salario.valor) if salario.valor is not None else None},
        "cuotaHdcBruta": {"disponible": bruto.disponible, "valor": str(bruto.valor) if bruto.valor is not None else None},
        "estadoConsistencia": (datos.externos.evidencia_gateway_hdc["resultado"]["endeudamiento_actual"]["estado_consistencia"]
            if datos.externos.evidencia_gateway_hdc else "NO_DISPONIBLE"),
        "cuotaHdcAplicable": {"disponible": False, "valor": None},
        "bloqueosAplicabilidad": condiciones[-2:], "escenarios": escenarios}
    return resultado
