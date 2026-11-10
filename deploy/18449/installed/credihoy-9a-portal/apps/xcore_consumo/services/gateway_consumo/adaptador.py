from dataclasses import dataclass, field
from decimal import Decimal

from apps.integraciones.risk_gateway.contratos import HDCNormalizado, HDCRequest, PreselectaNormalizado, PreselectaRequest
from .errores import EvaluacionGatewayBloqueada


# Estos operandos tienen filtros/cobertura propios en extractor.py: no son
# equivalentes al agregado Principal del Gateway por semejanza de nombre.
METRICAS_CONSUMO_NO_HOMOLOGADAS = (
    "valor_pasivos", "valor_pasivos_recoge", "saldo_creditos",
    "cupos_tarjetas_rotativos", "cuotas_creditos_egresos",
    "cuotas_creditos_codeudor", "valor_cuotas_recoge_per", "pasivos",
)


@dataclass(frozen=True, slots=True)
class RequestsGatewayConsumo:
    preselecta: PreselectaRequest = field(repr=False)
    hdc: HDCRequest = field(repr=False)

    def __post_init__(self):
        if type(self.preselecta) is not PreselectaRequest or type(self.hdc) is not HDCRequest:
            raise EvaluacionGatewayBloqueada("REQUESTS_GATEWAY_INVALIDOS")
        PreselectaRequest.desde_payload(self.preselecta.a_dict())
        HDCRequest.desde_payload(self.hdc.a_dict())


@dataclass(frozen=True, slots=True)
class CampoGatewayConsumo:
    valor: str | Decimal | None = field(repr=False)
    disponible: bool
    estado: str
    ruta_origen: str
    fuente: str | None = None
    referencia: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if type(self.disponible) is not bool or self.disponible != (self.valor is not None):
            raise EvaluacionGatewayBloqueada("CAMPO_GATEWAY_INCONSISTENTE")
        if self.valor is not None and type(self.valor) not in (str, Decimal):
            raise EvaluacionGatewayBloqueada("CAMPO_GATEWAY_TIPO_INVALIDO")
        if type(self.valor) is Decimal and not self.valor.is_finite():
            raise EvaluacionGatewayBloqueada("IMPORTE_GATEWAY_INVALIDO")


@dataclass(frozen=True, slots=True)
class PreselectaConsumo:
    estado_tecnico: str
    decision: CampoGatewayConsumo = field(repr=False)
    riesgo: CampoGatewayConsumo = field(repr=False)
    score: CampoGatewayConsumo = field(repr=False)
    evidencia: PreselectaNormalizado = field(repr=False, compare=False)

    def para_evaluador(self):
        # Resultado del proveedor, nunca DecisionFinal o autorizacion.
        return {"decision": self.decision.valor, "risk_level": self.riesgo.valor, "score": self.score.valor}


@dataclass(frozen=True, slots=True)
class HDCConsumo:
    estado_tecnico: str
    estado_consistencia: str
    agregado_disponible: bool
    cuota_mensual_principal: CampoGatewayConsumo = field(repr=False)
    saldo_total_principal: CampoGatewayConsumo = field(repr=False)
    saldo_mora_principal: CampoGatewayConsumo = field(repr=False)
    evidencia: HDCNormalizado = field(repr=False, compare=False)

    @property
    def obligaciones(self):
        # Evidencia original inmutable, sin declarar cuentas abiertas o elegibles.
        return self.evidencia["resultado"]["obligaciones"]

    def para_evaluador(self):
        return {"metrics": {key: None for key in METRICAS_CONSUMO_NO_HOMOLOGADAS},
                "tiene_novedad": None, "novedad_descripcion": None}


def _campo_preselecta(resultado, key, codigo, *, numerico=False):
    nodo = resultado["resultado_proveedor"][key]
    ruta = "resultado.resultado_proveedor." + key
    valor = None
    estado = "NO_DISPONIBLE"
    if resultado["estado_tecnico"] == "SIN_INFORMACION":
        estado = "SIN_INFORMACION"
    elif nodo is not None:
        estado = "NO_HOMOLOGADO"
        if nodo["codigo_original"] == codigo and nodo["fuente"] != "LEGACY_NO_NORMATIVO":
            candidate = nodo["valor_homologado"] if numerico else nodo["valor_original"]
            if (numerico and type(candidate) is Decimal) or (not numerico and type(candidate) is str and candidate.strip()):
                valor, estado = candidate, "OBSERVADO_PROVEEDOR"
    return CampoGatewayConsumo(valor, valor is not None, estado, ruta,
                              nodo["fuente"] if nodo is not None else None,
                              nodo["referencia"] if nodo is not None else None)


def adaptar_preselecta(dto):
    if type(dto) is not PreselectaNormalizado:
        raise EvaluacionGatewayBloqueada("DTO_PRESELECTA_INVALIDO")
    PreselectaNormalizado.desde_payload(dto.a_dict())
    r = dto["resultado"]
    return PreselectaConsumo(r["estado_tecnico"],
        _campo_preselecta(r, "decision", "DECISION"),
        _campo_preselecta(r, "riesgo", "RIESGO_SCORE"),
        _campo_preselecta(r, "score", "SCORE", numerico=True), dto)


def adaptar_hdc(dto):
    if type(dto) is not HDCNormalizado:
        raise EvaluacionGatewayBloqueada("DTO_HDC_INVALIDO")
    HDCNormalizado.desde_payload(dto.a_dict())
    r = dto["resultado"]
    agregado = r.get("endeudamiento_actual")
    estado = "AUSENTE" if agregado is None else agregado["estado_consistencia"]
    utilizable = bool(r["estado_tecnico"] == "COMPLETADO" and agregado is not None
        and agregado["disponible"] is True and estado == "CONSISTENTE"
        and agregado["moneda_homologada"] == "COP")
    def campo(key):
        valor = agregado[key] if utilizable else None
        if valor is not None and type(valor) is not Decimal:
            raise EvaluacionGatewayBloqueada("IMPORTE_GATEWAY_INVALIDO")
        estado_campo = ("SIN_INFORMACION" if r["estado_tecnico"] == "SIN_INFORMACION" else
                        "NORMALIZADO_COP" if valor is not None else "NO_DISPONIBLE")
        return CampoGatewayConsumo(valor, valor is not None, estado_campo,
            "resultado.endeudamiento_actual." + key,
            agregado["fuente"] if agregado is not None else None,
            agregado["referencia"] if agregado is not None else None)
    return HDCConsumo(r["estado_tecnico"], estado, utilizable,
                     campo("cuota_mensual_pesos"), campo("saldo_total_pesos"), campo("saldo_mora_pesos"), dto)


@dataclass(frozen=True, slots=True)
class DatosGatewayConsumo:
    preselecta: PreselectaConsumo = field(repr=False)
    hdc: HDCConsumo = field(repr=False)
    autorizado_decision_crediticia: bool = field(default=False, init=False)
    autorizado_radicacion: bool = field(default=False, init=False)

    def entrada_evaluador(self, form_data):
        if self.preselecta.estado_tecnico == "SIN_INFORMACION" or self.hdc.estado_tecnico == "SIN_INFORMACION":
            raise EvaluacionGatewayBloqueada("GATEWAY_SIN_INFORMACION")
        if not self.preselecta.decision.disponible:
            raise EvaluacionGatewayBloqueada("PRESELECTA_RESULTADO_NO_DISPONIBLE")
        if self.hdc.estado_consistencia not in {"CONSISTENTE", "AUSENTE"}:
            raise EvaluacionGatewayBloqueada("HDC_EVIDENCIA_NO_UTILIZABLE")
        # Ni siquiera un agregado valido acredita los filtros HC2 de Consumo.
        # No entregar None al evaluador: to_float(None) lo convertiriria en cero.
        raise EvaluacionGatewayBloqueada("GATEWAY_METRICAS_CONSUMO_NO_HOMOLOGADAS")
