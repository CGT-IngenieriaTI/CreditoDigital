"""CREDIHOY_UNICO_REGLAS_DURAS: contratos sin decision crediticia."""

from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from enum import Enum

from ..base import DTO, EstadoCertificacion as EC, Hecho, texto
from ..aritmetica import precision_exacta


class OrigenReglaUnico(str, Enum):
    REGLA_DURA_EXCEL = "REGLA_DURA_EXCEL"
    ADAPTACION_CONGENTE = "ADAPTACION_CONGENTE"
    ACLARACION_NEGOCIO = "ACLARACION_NEGOCIO"
    EVIDENCIA_LEGACY_CURUBA = "EVIDENCIA_LEGACY_CURUBA"


class EstadoOperandoUnico(str, Enum):
    DISPONIBLE = "DISPONIBLE"
    NO_DISPONIBLE = "NO_DISPONIBLE"
    HOMOLOGACION_HDC_PENDIENTE = "HOMOLOGACION_HDC_PENDIENTE"
    RECOGIDA_PENDIENTE_VALIDACION = "RECOGIDA_PENDIENTE_VALIDACION"
    PARAMETRO_INVALIDO = "PARAMETRO_INVALIDO"
    INFORMACION_INCONSISTENTE = "INFORMACION_INCONSISTENTE"


class EstadoValidacionRecogida(str, Enum):
    NO_APLICA = "NO_APLICA"
    PENDIENTE_VALIDACION = "PENDIENTE_VALIDACION"
    VALIDA = "VALIDA"
    NO_VALIDA = "NO_VALIDA"


class EstadoCapacidadUnico(str, Enum):
    EVALUABLE = "EVALUABLE"
    NO_EVALUABLE = "NO_EVALUABLE"
    INFORMACION_INCONSISTENTE = "INFORMACION_INCONSISTENTE"


class OrigenCuotaRecogida(str, Enum):
    LINIX = "LINIX"
    DECLARADA_ASESOR = "DECLARADA_ASESOR"
    NO_IDENTIFICADO = "NO_IDENTIFICADO"


def _ausente():
    return Hecho(fuente="LINIX_API_DATA_V2")


@dataclass(frozen=True, kw_only=True)
class ReferenciaDocumentoUnicoV2(DTO):
    tipo: str
    numero: str

    def __post_init__(self):
        super().__post_init__()
        texto(self.tipo, "tipo")
        texto(self.numero, "numero")


@dataclass(frozen=True, kw_only=True)
class ModalidadCredihoyPermitidaV2(DTO):
    """Modalidad certificada de CrediHoy PN; codigo_producto es solo metadato."""

    codigo_modalidad: str
    referencia_certificacion: str
    codigo_producto: str | None = None

    def __post_init__(self):
        super().__post_init__()
        for nombre in ("codigo_modalidad", "referencia_certificacion"):
            texto(getattr(self, nombre), nombre)
        if self.codigo_producto is not None:
            texto(self.codigo_producto, "codigo_producto")
        if self.codigo_modalidad != "111":
            raise ValueError("La unica modalidad certificada para recogida UNICO es 111")


MODALIDAD_CREDIHOY_PN = ModalidadCredihoyPermitidaV2(
    codigo_modalidad="111",
    referencia_certificacion="unico/REGLAS_DURAS.md#equivalencia-linix-certificada",
)


@dataclass(frozen=True, kw_only=True)
class RecogidaCredihoyUnicoV2(DTO):
    aplica: bool
    solicitud_origen: ReferenciaDocumentoUnicoV2 | None = None
    obligacion: ReferenciaDocumentoUnicoV2 | None = None
    producto_origen: str | None = None
    codigo_producto: str | None = None
    modalidad_codigo: str | None = None
    linea_codigo: str | None = None
    periodicidad_cuota: str | None = None
    origen_cuota: OrigenCuotaRecogida = OrigenCuotaRecogida.NO_IDENTIFICADO
    cuota_actual: Hecho[Decimal] = field(default_factory=_ausente)
    saldo_pendiente: Hecho[Decimal] = field(default_factory=_ausente)
    correspondencia_hdc: "CorrespondenciaRecogidaHDCV2 | None" = None
    estado_validacion: EstadoValidacionRecogida = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "estado_validacion", EstadoValidacionRecogida.PENDIENTE_VALIDACION
                           if self.aplica else EstadoValidacionRecogida.NO_APLICA)
        super().__post_init__()
        for nombre in ("producto_origen", "codigo_producto", "modalidad_codigo", "linea_codigo", "periodicidad_cuota"):
            if getattr(self, nombre) is not None:
                texto(getattr(self, nombre), nombre)
        if not self.aplica and (any(v is not None for v in (
                self.solicitud_origen, self.obligacion, self.producto_origen, self.codigo_producto,
                self.modalidad_codigo, self.linea_codigo, self.periodicidad_cuota))
                or self.cuota_actual.disponible or self.saldo_pendiente.disponible
                or self.correspondencia_hdc is not None
                or self.origen_cuota != OrigenCuotaRecogida.NO_IDENTIFICADO):
            raise ValueError("Sin recogida no admite una obligacion seleccionada ni importes")


@dataclass(frozen=True, kw_only=True)
class CorrespondenciaRecogidaHDCV2(DTO):
    """Evidencia explicita de inclusion; no se obtiene por comparar importes."""

    referencia_agregado_hdc: str
    obligacion: ReferenciaDocumentoUnicoV2
    cuota_incluida: Hecho[Decimal]

    def __post_init__(self):
        super().__post_init__()
        texto(self.referencia_agregado_hdc, "referencia_agregado_hdc")


@dataclass(frozen=True, kw_only=True)
class EntradaCredihoyUnicoV2(DTO):
    monto_solicitado: Decimal | None = None
    plazo_meses: int | None = None
    periodicidad_pago: str | None = None
    codigo_producto: str | None = None
    modalidad_codigo: str | None = None
    recogida: RecogidaCredihoyUnicoV2 | None = None
    producto: str = field(default="CREDIHOY UNICO (NUEVO)", init=False)

    def __post_init__(self):
        super().__post_init__()
        for nombre in ("periodicidad_pago", "codigo_producto", "modalidad_codigo"):
            if getattr(self, nombre) is not None:
                texto(getattr(self, nombre), nombre)


@dataclass(frozen=True, kw_only=True)
class ValidacionRecogidaUnicoV2(DTO):
    entrada: EntradaCredihoyUnicoV2
    modalidad_permitida: ModalidadCredihoyPermitidaV2 | None = None
    estado_validacion: EstadoValidacionRecogida = field(init=False)
    motivos: tuple[str, ...] = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "estado_validacion", EstadoValidacionRecogida.PENDIENTE_VALIDACION)
        object.__setattr__(self, "motivos", ())
        super().__post_init__()
        recogida = self.entrada.recogida
        motivos, invalidos = [], []
        if recogida is None:
            motivos.append("ESCENARIO_RECOGIDA_NO_DEFINIDO")
        elif not recogida.aplica:
            object.__setattr__(self, "estado_validacion", EstadoValidacionRecogida.NO_APLICA)
            return
        else:
            if recogida.producto_origen is not None and recogida.producto_origen != "CREDIHOY":
                invalidos.append("PRODUCTO_ORIGEN_FUERA_DE_UNICO")
            permitida = self.modalidad_permitida or MODALIDAD_CREDIHOY_PN
            if recogida.modalidad_codigo is None:
                motivos.append("MODALIDAD_CODIGO_NO_IDENTIFICADO")
            elif recogida.modalidad_codigo != permitida.codigo_modalidad:
                invalidos.append("MODALIDAD_RECOGIDA_NO_PERMITIDA")
            if self.entrada.modalidad_codigo is None:
                motivos.append("MODALIDAD_NUEVA_NO_IDENTIFICADA")
            elif recogida.modalidad_codigo is not None and recogida.modalidad_codigo != self.entrada.modalidad_codigo:
                invalidos.append("MODALIDAD_CODIGO_DISTINTO")
            if recogida.solicitud_origen is None or recogida.obligacion is None:
                motivos.append("IDENTIDAD_RECOGIDA_INCOMPLETA")
            if not recogida.cuota_actual.disponible:
                motivos.append("CUOTA_RECOGIDA_NO_DISPONIBLE")
            elif recogida.cuota_actual.valor < 0:
                invalidos.append("CUOTA_RECOGIDA_NEGATIVA")
            if recogida.cuota_actual.estado_certificacion not in {EC.OBSERVADO, EC.CERTIFICADO}:
                motivos.append("SEMANTICA_CUOTA_RECOGIDA_NO_UTILIZABLE")
            if recogida.origen_cuota != OrigenCuotaRecogida.LINIX or not recogida.cuota_actual.fuente or not (
                    recogida.cuota_actual.referencia_evidencia and recogida.cuota_actual.ruta_origen):
                motivos.append("CUOTA_RECOGIDA_SIN_EVIDENCIA_LINIX")
            if recogida.periodicidad_cuota != "MENSUAL":
                motivos.append("CUOTA_RECOGIDA_NO_HOMOLOGADA_A_MENSUAL")
        estado = (EstadoValidacionRecogida.NO_VALIDA if invalidos else
                  EstadoValidacionRecogida.PENDIENTE_VALIDACION if motivos else EstadoValidacionRecogida.VALIDA)
        object.__setattr__(self, "estado_validacion", estado)
        object.__setattr__(self, "motivos", tuple(invalidos + motivos))


@dataclass(frozen=True, kw_only=True)
class OperandoCredihoyUnicoV2(Hecho[Decimal]):
    valor: Decimal | None = None
    estado: EstadoOperandoUnico
    origen_regla: OrigenReglaUnico
    formula_id: str | None = None
    formula_version: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.disponible != (self.estado == EstadoOperandoUnico.DISPONIBLE):
            raise ValueError("Estado y disponibilidad incompatibles")
        if self.disponible and self.estado_certificacion not in {EC.OBSERVADO, EC.CERTIFICADO}:
            raise ValueError("Operando calculable requiere semantica utilizable")
        if (self.formula_id is None) != (self.formula_version is None):
            raise ValueError("Formula y version deben declararse juntas")
        texto(self.observacion, "observacion")


@dataclass(frozen=True, kw_only=True)
class EscenarioCapacidadDisponibleV2(DTO):
    ingresos: Decimal
    cuotas_hdc: Decimal
    deducciones_nomina: Decimal
    gastos_familiares_exactos: Decimal
    capacidad_disponible: Decimal
    cuota_nueva_estimada: Decimal | None = None
    residual_posterior_estimado: Decimal | None = None
    version: str = field(default='credihoy-capacidad-disponible-v1', init=False)
    aprobacion_riesgos: bool = field(default=False, init=False)
    autorizado_decision_crediticia: bool = field(default=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        operands=(self.ingresos,self.cuotas_hdc,self.deducciones_nomina,self.gastos_familiares_exactos)
        if any(v<0 for v in operands):raise ValueError('OPERANDO_CAPACIDAD_DISPONIBLE_NEGATIVO')
        if (self.cuota_nueva_estimada is None)!=(self.residual_posterior_estimado is None):
            raise ValueError('RESIDUAL_ESTIMADO_REQUIERE_CUOTA')
        values=(*operands,self.capacidad_disponible)
        if self.cuota_nueva_estimada is not None:
            if self.cuota_nueva_estimada<=0:raise ValueError('CUOTA_ESTIMADA_NO_POSITIVA')
            values+=self.cuota_nueva_estimada,self.residual_posterior_estimado
        with localcontext() as ctx:
            ctx.prec=precision_exacta(*values,Decimal('0.25'))
            if self.gastos_familiares_exactos!=self.ingresos*Decimal('0.25'):
                raise ValueError('GASTOS_FAMILIARES_NO_EXACTOS')
            if self.ingresos-self.cuotas_hdc-self.deducciones_nomina-self.gastos_familiares_exactos!=self.capacidad_disponible:
                raise ValueError('CAPACIDAD_DISPONIBLE_NO_CONCILIA')
            if (self.cuota_nueva_estimada is not None
                    and self.capacidad_disponible-self.cuota_nueva_estimada!=self.residual_posterior_estimado):
                raise ValueError('RESIDUAL_POSTERIOR_NO_CONCILIA')


@dataclass(frozen=True, kw_only=True)
class CapacidadCredihoyUnicoV2(DTO):
    ingresos_mensuales: OperandoCredihoyUnicoV2
    cuotas_deudas_hdc_brutas: OperandoCredihoyUnicoV2
    cuota_credihoy_recogido: OperandoCredihoyUnicoV2
    cuotas_deudas_hdc_aplicables: OperandoCredihoyUnicoV2
    cuota_nuevo_credito: OperandoCredihoyUnicoV2
    gastos_familiares: OperandoCredihoyUnicoV2
    capacidad_pago: OperandoCredihoyUnicoV2
    validacion_recogida: ValidacionRecogidaUnicoV2
    estado: EstadoCapacidadUnico
    operandos_faltantes: tuple[str, ...]
    inconsistencias: tuple[str, ...] = ()
    cuotas_aplicables_explicitas: Hecho[Decimal] | None = None
    modelo: str = field(default="CREDIHOY_UNICO_REGLAS_DURAS", init=False)
    naturaleza_cuota_nuevo_credito: str = field(default="CUOTA_PARA_CAPACIDAD_SHADOW_NO_CONTRACTUAL", init=False)
    autorizado_decision_crediticia: bool = field(default=False, init=False)
    conclusion_funcional: str = field(default="NO_DETERMINADA", init=False)

    def __post_init__(self):
        super().__post_init__()
        evaluable = self.capacidad_pago.disponible
        if evaluable != (self.estado == EstadoCapacidadUnico.EVALUABLE):
            raise ValueError("Resultado incompatible con capacidad disponible")
        if evaluable and (self.operandos_faltantes or self.inconsistencias):
            raise ValueError("Capacidad evaluable no admite faltantes ni inconsistencias")
        if bool(self.inconsistencias) != (self.estado == EstadoCapacidadUnico.INFORMACION_INCONSISTENTE):
            raise ValueError("Estado de inconsistencia requiere motivos explicitos")
        nombres = ("ingresos_mensuales", "cuotas_deudas_hdc_brutas", "cuota_credihoy_recogido",
                   "cuotas_deudas_hdc_aplicables", "cuota_nuevo_credito", "gastos_familiares")
        if self.operandos_faltantes != tuple(n for n in nombres if not getattr(self, n).disponible):
            raise ValueError("Faltantes deben corresponder a la disponibilidad real")
        if evaluable:
            valores = tuple(getattr(self, n).valor for n in nombres)
            if any(v < 0 for v in valores):
                raise ValueError("Operandos negativos no permiten capacidad evaluable")
            ingreso, bruto, recogida, aplicables, cuota, gastos = valores
            with localcontext() as ctx:
                ctx.prec = precision_exacta(*valores, self.capacidad_pago.valor)
                explicitas = self.cuotas_aplicables_explicitas
                if explicitas is not None:
                    if (self.validacion_recogida.estado_validacion != EstadoValidacionRecogida.NO_APLICA
                            or not explicitas.disponible or explicitas.valor != aplicables
                            or explicitas.fuente != self.cuotas_deudas_hdc_aplicables.fuente
                            or explicitas.referencia_evidencia != self.cuotas_deudas_hdc_aplicables.referencia_evidencia
                            or not Decimal("0") <= aplicables <= bruto):
                        raise ValueError("Cuotas explicitas sin correspondencia de contexto")
                elif bruto - recogida != aplicables:
                    raise ValueError("Cuotas aplicables no concilian con bruto y recogida")
                if ingreso - aplicables - cuota - gastos != self.capacidad_pago.valor:
                    raise ValueError("Resultado financiero no concilia con sus operandos")
