"""Contratos de observaciones, nunca decisiones ni saldos inferidos."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import re

from apps.integraciones.risk_gateway.contratos import (
    HDCNormalizado, NodoContrato, PreselectaNormalizado, validar_fragmento,
)
from .base import DTO, EstadoCertificacion, Hecho, decimal_finito, instante, pendiente, texto
from .pagaduria import PagaduriaLinix


@dataclass(frozen=True, kw_only=True)
class SolicitudEvaluadaV2(DTO):
    tipo: str
    numero: str

    def __post_init__(self):
        super().__post_init__()
        texto(self.tipo, "tipo")
        texto(self.numero, "numero")


@dataclass(frozen=True, kw_only=True)
class RequestHechosInternosV2(DTO):
    numero_documento: str
    fecha_evaluacion: datetime
    solicitud_evaluada: SolicitudEvaluadaV2 | None = None

    def __post_init__(self):
        super().__post_init__()
        if not re.fullmatch(r"[0-9]{1,30}", self.numero_documento):
            raise ValueError("Documento incompatible con request V2")
        instante(self.fecha_evaluacion, "fecha_evaluacion")
        if self.fecha_evaluacion.microsecond or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}",
                self.fecha_evaluacion.isoformat(timespec="seconds")):
            raise ValueError("Fecha de evaluacion requiere segundos y zona sin fracciones")

    def a_payload_http(self):
        payload = {"numeroDocumento": self.numero_documento,
                   "fechaEvaluacion": self.fecha_evaluacion.isoformat(timespec="seconds")}
        if self.solicitud_evaluada is not None:
            payload["solicitudEvaluada"] = {"tipo": self.solicitud_evaluada.tipo,
                                           "numero": self.solicitud_evaluada.numero}
        return payload


@dataclass(frozen=True, kw_only=True)
class EntradaUsuarioCrediHoyV2(DTO):
    identificacion: str
    modalidad: str
    monto: Decimal
    plazo: int
    periodicidad_pago: str
    obligacion_recogida: str | None = None

    def __post_init__(self):
        super().__post_init__()
        for nombre in ("identificacion", "modalidad", "periodicidad_pago"):
            texto(getattr(self, nombre), nombre)
        decimal_finito(self.monto, "monto")
        if self.monto <= 0 or type(self.plazo) is not int or self.plazo <= 0:
            raise ValueError("monto y plazo deben ser positivos")
        if self.obligacion_recogida is not None:
            texto(self.obligacion_recogida, "obligacion_recogida")


@dataclass(frozen=True, kw_only=True)
class ObligacionCierreV2(DTO):
    referencia: Hecho[str]
    clasificacion: Hecho[str]
    dias_mora: Hecho[int]
    dias_vencidos: Hecho[int]
    saldo_mora: Hecho[Decimal]
    saldo_capital: Hecho[Decimal]
    tipo_documento: Hecho[str] = field(default_factory=pendiente)


@dataclass(frozen=True, kw_only=True)
class DocumentoAporte203V2(DTO):
    referencia: Hecho[str]
    saldo_observado: Hecho[Decimal]
    estados_originales: tuple[Hecho[str], ...] = ()
    tipo_documento: Hecho[str] = field(default_factory=pendiente)
    cancelacion_registrada: Hecho[str] = field(default_factory=pendiente)
    fecha_vigencia_registrada: Hecho[date | datetime] = field(default_factory=pendiente)


@dataclass(frozen=True, kw_only=True)
class CarteraAlCierreV2(DTO):
    periodo_anterior: Hecho[str]
    estado_cierre_operativo: EstadoCertificacion
    estado: str
    obligaciones: tuple[ObligacionCierreV2, ...]


@dataclass(frozen=True, kw_only=True)
class AportesSociales203V2(DTO):
    estado: str
    estado_consolidacion: str
    documentos: tuple[DocumentoAporte203V2, ...]
    saldo_aportes_utilizable: Hecho[Decimal]


@dataclass(frozen=True, kw_only=True)
class SolicitudOrigenInternaV2(DTO):
    tipo: Hecho[str]
    numero: Hecho[str]


@dataclass(frozen=True, kw_only=True)
class CatalogoInternoV2(DTO):
    codigo: Hecho[str]
    descripcion: Hecho[str]


@dataclass(frozen=True, kw_only=True)
class CandidatoCuotaInternaCrediHoyV2(DTO):
    serie_plan: Hecho[str]
    reestructuracion: Hecho[str]
    tipo_cuota: Hecho[str]
    cuota_contractual: Hecho[Decimal]
    numero_cuotas: Hecho[int]
    periodicidad: Hecho[str]
    fecha_cuota: Hecho[date | datetime]


@dataclass(frozen=True, kw_only=True)
class ObligacionInternaCrediHoyV2(DTO):
    tipo_obligacion: Hecho[str]
    numero_obligacion: Hecho[str]
    solicitud_origen: SolicitudOrigenInternaV2
    modalidad: CatalogoInternoV2
    linea: CatalogoInternoV2
    estado_solicitud: Hecho[str]
    aprobada: Hecho[str]
    liquidada: Hecho[str]
    indicador_cancelacion: Hecho[str]
    indicador_anulacion: Hecho[str]
    fecha_prestamo: Hecho[date | datetime]
    fecha_cancelacion: Hecho[date | datetime]
    saldo_registrado: Hecho[Decimal]
    cuota_contractual: Hecho[Decimal]
    numero_cuotas: Hecho[int]
    periodicidad: Hecho[str]
    fecha_cuota: Hecho[date | datetime]
    estado_plan: EstadoCertificacion
    cuotas_candidatas: tuple[CandidatoCuotaInternaCrediHoyV2, ...]
    es_solicitud_evaluada: bool | None = None

    def __post_init__(self):
        super().__post_init__()
        cantidad = len(self.cuotas_candidatas)
        esperado = (EstadoCertificacion.AMBIGUO if cantidad > 1 else
                    EstadoCertificacion.OBSERVADO if cantidad else EstadoCertificacion.NO_DISPONIBLE)
        if self.estado_plan != esperado:
            raise ValueError("Estado del plan incompatible con multiplicidad contractual")
        if cantidad != 1:
            for h in (self.cuota_contractual, self.numero_cuotas, self.periodicidad, self.fecha_cuota):
                if h.disponible or h.estado_certificacion != esperado:
                    raise ValueError("Hecho general incompatible con multiplicidad contractual")


@dataclass(frozen=True, kw_only=True)
class ObligacionesActualesV2(DTO):
    estado_certificacion: EstadoCertificacion
    disponible: bool
    obligaciones: tuple[ObligacionInternaCrediHoyV2, ...]
    observacion: str
    estado: str | None = None
    temporalidad: str | None = None
    fecha_corte: date | datetime | None = None
    solicitud_evaluada_no_identificada: bool | None = None

    def __post_init__(self):
        super().__post_init__()
        if not self.disponible and self.obligaciones:
            raise ValueError("Lista no disponible no admite obligaciones")
        if self.estado is None:
            if (self.estado_certificacion != EstadoCertificacion.NO_CERTIFICADO or self.disponible
                    or self.obligaciones or any(v is not None for v in (
                        self.temporalidad, self.fecha_corte, self.solicitud_evaluada_no_identificada))):
                raise ValueError("Placeholder anterior de obligaciones incompatible")
        else:
            if self.temporalidad != "REGISTROS_AL_CONSULTAR" or self.solicitud_evaluada_no_identificada is None:
                raise ValueError("Metadatos de obligaciones incompletos")
            instante(self.fecha_corte, "fecha_corte")
            con_registros = bool(self.obligaciones)
            if (self.disponible != con_registros
                    or self.estado != ("REGISTROS_OBSERVADOS" if con_registros else "SIN_REGISTROS")
                    or self.estado_certificacion != (EstadoCertificacion.OBSERVADO if con_registros
                                                      else EstadoCertificacion.NO_DISPONIBLE)):
                raise ValueError("Estado de obligaciones incompatible con registros observados")


@dataclass(frozen=True, kw_only=True)
class ConceptoEconomicoSolicitudV2(DTO):
    codigo: Hecho[str]
    descripcion: Hecho[str]
    naturaleza: Hecho[str]
    automatico: Hecho[str]
    porcentaje_aplicacion: Hecho[Decimal]
    devengado_mensual: Hecho[Decimal]
    deducido_mensual: Hecho[Decimal]
    devengado_extraordinario: Hecho[Decimal]
    deducido_extraordinario: Hecho[Decimal]
    fecha_modificacion: Hecho[date | datetime]


@dataclass(frozen=True, kw_only=True)
class MatrizEconomicaSolicitudV2(DTO):
    disponible: bool
    estado_certificacion: EstadoCertificacion
    temporalidad: str
    fecha_corte: datetime
    solicitud_evaluada_no_identificada: bool
    solicitud: SolicitudOrigenInternaV2
    conceptos: tuple[ConceptoEconomicoSolicitudV2, ...]
    total_devengado_mensual: Hecho[Decimal]
    total_deducido_mensual: Hecho[Decimal]
    total_devengado_extraordinario: Hecho[Decimal]
    total_deducido_extraordinario: Hecho[Decimal]
    fecha_modificacion_maxima: Hecho[date | datetime]
    observacion: str

    def __post_init__(self):
        super().__post_init__()
        instante(self.fecha_corte, "fecha_corte")
        if (self.temporalidad != "REGISTROS_AL_CONSULTAR"
                or self.disponible != bool(self.conceptos)
                or self.estado_certificacion != (EstadoCertificacion.OBSERVADO if self.conceptos
                                                  else EstadoCertificacion.NO_DISPONIBLE)):
            raise ValueError("Disponibilidad de matriz incompatible con contrato V2")


@dataclass(frozen=True, kw_only=True)
class MetadatosHechosInternosV2(DTO):
    version_contrato: str
    request_id: str
    fecha_consulta: datetime
    fecha_evaluacion: datetime
    habilitado_decision_productiva: bool

    def __post_init__(self):
        super().__post_init__()
        if self.version_contrato != "credihoy-hechos-v2" or self.habilitado_decision_productiva is not False:
            raise ValueError("Contrato incompatible con consumidor V2")
        texto(self.request_id, "request_id")
        instante(self.fecha_consulta, "fecha_consulta")
        instante(self.fecha_evaluacion, "fecha_evaluacion")


@dataclass(frozen=True, kw_only=True)
class HechosFinancierosV2(DTO):
    ingreso_mensual_registrado: Hecho[Decimal]
    sueldo_basico_mensual: Hecho[Decimal]
    otros_ingresos_mensuales: Hecho[Decimal]
    otros_egresos_mensuales: Hecho[Decimal]
    otros_descuentos_nomina: Hecho[Decimal]
    calificacion_cliente: Hecho[str]
    persona_natural: Hecho[bool]
    naturaleza_registrada: Hecho[str]
    fecha_actualizacion_cliente: Hecho[date | datetime]
    fecha_actualizacion_tercero: Hecho[date | datetime]
    dias_actualizacion_cliente: Hecho[int]
    meses_calendario_cliente: Hecho[int]
    saldo_aportes_elegible: Hecho[Decimal]
    limite_aportes_90: Hecho[Decimal]
    estados_obligaciones: tuple[Hecho[str], ...] = ()


@dataclass(frozen=True, kw_only=True)
class HechosInternosCrediHoyV2(DTO):
    fecha_nacimiento: Hecho[date] = field(default_factory=pendiente)
    estado_civil: Hecho[str] = field(default_factory=pendiente)
    personas_a_cargo: Hecho[int] = field(default_factory=pendiente)
    periodo_anterior: Hecho[str] = field(default_factory=pendiente)
    cartera_al_cierre: CarteraAlCierreV2 | Hecho[tuple[ObligacionCierreV2, ...]] = field(default_factory=pendiente)
    aportes_documentos_203: tuple[DocumentoAporte203V2, ...] | Hecho[tuple[DocumentoAporte203V2, ...]] = field(default_factory=pendiente)
    fecha_afiliacion: Hecho[date] = field(default_factory=pendiente)
    estrato: Hecho[str] = field(default_factory=pendiente)
    profesion: Hecho[str] = field(default_factory=pendiente)
    sueldo_registrado: Hecho[Decimal] = field(default_factory=pendiente)
    agencia: Hecho[str] = field(default_factory=pendiente)
    tipo_vivienda: Hecho[str] = field(default_factory=pendiente)
    ingreso_operacional: Hecho[Decimal] = field(default_factory=pendiente)
    deducciones_linix: Hecho[Decimal] = field(default_factory=pendiente)
    obligaciones_vigentes: Hecho[tuple[str, ...]] = field(default_factory=pendiente)
    cuotas_creditos_internos: Hecho[tuple[Hecho[Decimal], ...]] = field(default_factory=pendiente)
    cuota_credito_recogido: Hecho[Decimal] = field(default_factory=pendiente)
    saldo_aportes_utilizable: Hecho[Decimal] = field(default_factory=pendiente)
    cuota_vigente_interna: Hecho[Decimal] = field(default_factory=pendiente)
    obligacion_seleccionada: Hecho[str] = field(default_factory=pendiente)
    aportes_sociales_203: AportesSociales203V2 | None = None
    obligaciones_actuales: ObligacionesActualesV2 | None = None
    matriz_economica_solicitud: MatrizEconomicaSolicitudV2 | None = None
    metadatos: MetadatosHechosInternosV2 | None = None
    pagaduria: PagaduriaLinix | None = None
    financieros: HechosFinancierosV2 | None = None


@dataclass(frozen=True, kw_only=True)
class ImporteHDCV2(DTO):
    valor_original: str | int | Decimal | None
    unidad_original: str | None
    moneda_original: str | None
    valor_normalizado: Decimal | None = None
    estado_homologacion: EstadoCertificacion = EstadoCertificacion.SEMANTICA_PENDIENTE
    referencia_homologacion: str | None = None
    referencia_evidencia: str | None = None
    ruta_origen: str | None = None
    evidencia_gateway: NodoContrato | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.evidencia_gateway is not None:
            validar_fragmento(self.evidencia_gateway, "ImporteHDC")
            origen = self.evidencia_gateway
            if (self.valor_original != origen["valor_original"]
                    or self.unidad_original != origen["unidad_original"]
                    or self.moneda_original != origen["moneda_codigo"]
                    or self.valor_normalizado != origen["valor_monetario"]
                    or self.referencia_homologacion != origen["referencia"]
                    or self.estado_homologacion != EstadoCertificacion.OBSERVADO):
                raise ValueError("Importe gateway debe preservar homologacion sin certificacion local")
            return
        if self.valor_original is not None and type(self.valor_original) not in (str, int, Decimal):
            raise TypeError("Importe original requiere string, int o Decimal")
        if not isinstance(self.estado_homologacion, EstadoCertificacion):
            raise TypeError("estado_homologacion invalido")
        for nombre in ("unidad_original", "moneda_original"):
            valor = getattr(self, nombre)
            if valor is not None and not isinstance(valor, str):
                raise TypeError(f"{nombre} requiere string")
        if self.valor_normalizado is not None:
            decimal_finito(self.valor_normalizado, "valor_normalizado")
            if (self.valor_original is None or not self.unidad_original or not self.moneda_original
                    or self.estado_homologacion != EstadoCertificacion.CERTIFICADO
                    or not self.referencia_homologacion):
                raise ValueError("Normalizacion requiere origen y homologacion certificada")
        elif self.estado_homologacion == EstadoCertificacion.CERTIFICADO:
            raise ValueError("Importe certificado requiere valor normalizado")
        if self.valor_original is None and self.estado_homologacion != EstadoCertificacion.NO_DISPONIBLE:
            raise ValueError("Importe ausente requiere NO_DISPONIBLE")
        if self.valor_original is not None and self.estado_homologacion == EstadoCertificacion.NO_DISPONIBLE:
            raise ValueError("Importe presente no es NO_DISPONIBLE")


def _hdc_ausente():
    return Hecho(fuente="HDC")


def _importe_hdc_ausente():
    return ImporteHDCV2(valor_original=None, unidad_original=None, moneda_original=None,
                        estado_homologacion=EstadoCertificacion.NO_DISPONIBLE)


def _posicion_hdc(posicion):
    if posicion is not None and (type(posicion) is not int or posicion < 0):
        raise ValueError("Posicion original requiere indice no negativo")


@dataclass(frozen=True, kw_only=True)
class AtributoOriginalHDCV2(DTO):
    nombre: str
    hecho: Hecho[str | int | Decimal | bool]

    def __post_init__(self):
        super().__post_init__()
        texto(self.nombre, "nombre")


@dataclass(frozen=True, kw_only=True)
class EstadoCuentaHDCV2(DTO):
    tipo: Hecho[str]
    codigo: Hecho[str]
    fecha: Hecho[str]
    meses: Hecho[str]
    descripcion: Hecho[str] = field(default_factory=_hdc_ausente)
    atributos_originales: tuple[AtributoOriginalHDCV2, ...] = ()
    posicion_original: int | None = None
    ruta_origen: str | None = None

    def __post_init__(self):
        super().__post_init__()
        _posicion_hdc(self.posicion_original)


@dataclass(frozen=True, kw_only=True)
class ObservacionHDCV2(DTO):
    fecha: Hecho[str]
    moneda_original: Hecho[str]
    unidad_original: Hecho[str]
    periodicidad_original: Hecho[str]
    saldo_actual: ImporteHDCV2
    saldo_mora: ImporteHDCV2
    dias_mora: Hecho[str | int]
    cuota: ImporteHDCV2
    valor_inicial: ImporteHDCV2 = field(default_factory=_importe_hdc_ausente)
    cupo_inicial: ImporteHDCV2 = field(default_factory=_importe_hdc_ausente)
    cupo_total: ImporteHDCV2 = field(default_factory=_importe_hdc_ausente)
    atributos_originales: tuple[AtributoOriginalHDCV2, ...] = ()
    posicion_original: int | None = None
    ruta_origen: str | None = None

    def __post_init__(self):
        super().__post_init__()
        _posicion_hdc(self.posicion_original)


@dataclass(frozen=True, kw_only=True)
class CuentaHDCV2(DTO):
    referencia_local: Hecho[str]
    tipo_cuenta: Hecho[str]
    entidad_codigo: Hecho[str]
    rol_original: Hecho[str]
    sector_codigo_original: Hecho[str]
    embargo: Hecho[bool]
    tipo_embargo: Hecho[str]
    estados_originales: tuple[EstadoCuentaHDCV2, ...]
    observaciones: tuple[ObservacionHDCV2, ...]
    entidad: Hecho[str] = field(default_factory=_hdc_ausente)
    subtipo_cuenta_original: Hecho[str] = field(default_factory=_hdc_ausente)
    caracteristicas_originales: tuple[AtributoOriginalHDCV2, ...] = ()
    fecha_apertura: Hecho[str] = field(default_factory=_hdc_ausente)
    fecha_vencimiento: Hecho[str] = field(default_factory=_hdc_ausente)
    fecha_actualizacion: Hecho[str] = field(default_factory=_hdc_ausente)
    fecha_corte_original: Hecho[str] = field(default_factory=_hdc_ausente)
    posicion_original: int | None = None
    ruta_origen: str | None = None

    def __post_init__(self):
        super().__post_init__()
        _posicion_hdc(self.posicion_original)


@dataclass(frozen=True, kw_only=True)
class CoberturaHDCV2(DTO):
    tipo_cuenta: str
    registros_observados: int
    completa: bool | None = None

    def __post_init__(self):
        super().__post_init__()
        texto(self.tipo_cuenta, "tipo_cuenta")
        if type(self.registros_observados) is not int or self.registros_observados < 0:
            raise ValueError("Conteo invalido")
        if self.completa is not None and type(self.completa) is not bool:
            raise TypeError("completa requiere bool o None")


@dataclass(frozen=True, kw_only=True)
class PreselectaV2(DTO):
    decision: Hecho[str] = field(default_factory=lambda: Hecho(fuente="PRESELECTA"))
    riesgo_score: Hecho[str] = field(default_factory=lambda: Hecho(fuente="PRESELECTA"))
    score: Hecho[Decimal] = field(default_factory=lambda: Hecho(fuente="PRESELECTA"))
    evidencia_gateway: PreselectaNormalizado | None = None


@dataclass(frozen=True, kw_only=True)
class HechosExternosCrediHoyV2(DTO):
    referencia_evidencia: str
    fecha_consulta: datetime | None
    fecha_consulta_original: str | None = None
    cuentas: tuple[CuentaHDCV2, ...] = ()
    cobertura_por_tipo: tuple[CoberturaHDCV2, ...] = ()
    campos_faltantes: tuple[str, ...] = ()
    identidades_ambiguas: tuple[str, ...] = ()
    posibles_duplicados: tuple[tuple[int, ...], ...] = ()
    preselecta: PreselectaV2 = field(default_factory=PreselectaV2)
    evidencia_gateway_hdc: HDCNormalizado | None = None
    version_contrato: str = field(default="credihoy-externos-v2", init=False)

    def __post_init__(self):
        super().__post_init__()
        texto(self.referencia_evidencia, "referencia_evidencia")
        if self.evidencia_gateway_hdc is not None:
            original = self.evidencia_gateway_hdc["resultado"]["fecha_consulta"]
            if self.fecha_consulta_original != original:
                raise ValueError("Fecha original debe corresponder a la evidencia gateway")
            if self.fecha_consulta is not None:
                instante(self.fecha_consulta, "fecha_consulta")
                if original is None or datetime.fromisoformat(original.replace("Z", "+00:00")) != self.fecha_consulta:
                    raise ValueError("Fechas gateway incompatibles")
            return
        if self.fecha_consulta is not None:
            instante(self.fecha_consulta, "fecha_consulta")
        if self.fecha_consulta_original is not None:
            texto(self.fecha_consulta_original, "fecha_consulta_original")
            original = datetime.fromisoformat(self.fecha_consulta_original)
            if "T" not in self.fecha_consulta_original:
                raise ValueError("fecha_consulta_original requiere fecha y hora ISO")
            if self.fecha_consulta is None:
                if original.utcoffset() is not None:
                    raise ValueError("Fecha con zona requiere fecha_consulta")
            elif original != self.fecha_consulta:
                raise ValueError("Fechas de consulta incompatibles")
        elif self.fecha_consulta is None:
            raise ValueError("Consulta sin instante requiere fecha original sin zona")
