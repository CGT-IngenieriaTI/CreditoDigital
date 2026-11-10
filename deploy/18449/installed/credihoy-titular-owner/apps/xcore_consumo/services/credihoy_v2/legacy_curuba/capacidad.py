"""LEGACY_SHADOW_CURUBA: evidencia historica, no normativa CrediHoy Unico."""

from dataclasses import dataclass, field, fields
from decimal import Decimal, localcontext
from enum import Enum

from ..aritmetica import precision_exacta, trunc_cuota, trunc_producto
from ..base import DTO, EstadoCertificacion as EC, Hecho, texto
from ..contratos import EstadoDefinicion
from ..hechos import HechosExternosCrediHoyV2, HechosInternosCrediHoyV2


MODELO = "LEGACY_SHADOW_CURUBA"


class EstadoOperandoCapacidadV2(str, Enum):
    DISPONIBLE = "DISPONIBLE"
    OBSERVADO_EN_CURUBA = "OBSERVADO_EN_CURUBA"
    NO_DISPONIBLE = "NO_DISPONIBLE"
    NO_EVALUABLE = "NO_EVALUABLE"
    POLITICA_PENDIENTE = "POLITICA_PENDIENTE"
    HOMOLOGACION_PENDIENTE = "HOMOLOGACION_PENDIENTE"


class EstadoCapacidadShadowV2(str, Enum):
    EVALUABLE = "EVALUABLE"
    NO_EVALUABLE = "NO_EVALUABLE"


@dataclass(frozen=True, kw_only=True)
class OperandoCapacidadV2(Hecho[Decimal]):
    """Hecho monetario con estado operativo separado de la certificacion."""

    valor: Decimal | None = None
    estado: EstadoOperandoCapacidadV2
    formula_id: str | None = None
    formula_version: str | None = None

    def __post_init__(self):
        super().__post_init__()
        presentes = {EstadoOperandoCapacidadV2.DISPONIBLE,
                     EstadoOperandoCapacidadV2.OBSERVADO_EN_CURUBA}
        if self.disponible != (self.estado in presentes):
            raise ValueError("Estado operativo incompatible con disponibilidad")
        texto(self.fuente, "fuente del operando")
        texto(self.observacion, "observacion del operando")
        if (self.formula_id is None) != (self.formula_version is None):
            raise ValueError("Formula y version deben declararse juntas")
        if self.formula_id is not None:
            texto(self.formula_id, "formula_id")
            texto(self.formula_version, "formula_version")
        if self.estado == EstadoOperandoCapacidadV2.OBSERVADO_EN_CURUBA:
            if self.estado_certificacion != EC.OBSERVADO or self.formula_id is None:
                raise ValueError("Formula observada requiere version y no certifica politica")


@dataclass(frozen=True, kw_only=True)
class EntradaCapacidadShadowV2(DTO):
    monto_solicitado: Decimal | None = None
    plazo: int | None = None
    periodicidad_pago: str | None = None
    cuota_credito_recogida_declarada: Decimal | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.periodicidad_pago is not None:
            texto(self.periodicidad_pago, "periodicidad_pago")


@dataclass(frozen=True, kw_only=True)
class OperandosCapacidadShadowV2(DTO):
    ingresos: OperandoCapacidadV2
    deducciones_dc: OperandoCapacidadV2
    deducciones_internas: OperandoCapacidadV2
    cuota_solicitada: OperandoCapacidadV2
    gastos_familiares: OperandoCapacidadV2
    cuota_credito_recogida: OperandoCapacidadV2

    @property
    def faltantes(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(OperandosCapacidadShadowV2)
                     if not getattr(self, f.name).disponible)


@dataclass(frozen=True, kw_only=True)
class CapacidadPagoShadowV2(OperandosCapacidadShadowV2):
    capacidad_calculada: OperandoCapacidadV2
    estado: EstadoCapacidadShadowV2 = field(init=False)
    operandos_faltantes: tuple[str, ...] = field(init=False)
    clasificacion_formula: EstadoDefinicion = field(
        default=EstadoDefinicion.OBSERVADA_EN_CURUBA, init=False)
    formula_id: str = field(default="capacidad_pago_curuba_shadow", init=False)
    formula_version: str = field(default="observada-7a-1", init=False)
    autorizado_decision_crediticia: bool = field(default=False, init=False)

    def __post_init__(self):
        object.__setattr__(self, "operandos_faltantes", ())
        object.__setattr__(self, "estado", EstadoCapacidadShadowV2.NO_EVALUABLE)
        super().__post_init__()
        object.__setattr__(self, "operandos_faltantes", self.faltantes)
        object.__setattr__(self, "estado", EstadoCapacidadShadowV2.NO_EVALUABLE
                           if self.faltantes else EstadoCapacidadShadowV2.EVALUABLE)
        if self.capacidad_calculada.disponible != (not self.faltantes):
            raise ValueError("Capacidad disponible requiere los seis operandos")
        esperado = (EstadoOperandoCapacidadV2.NO_EVALUABLE if self.faltantes
                    else EstadoOperandoCapacidadV2.OBSERVADO_EN_CURUBA)
        if self.capacidad_calculada.estado != esperado:
            raise ValueError("Estado de capacidad inconsistente con operandos")


def _formula(valor, *, formula_id, observacion, origen=None):
    return OperandoCapacidadV2(
        valor=valor, disponible=valor is not None, fuente="FORMULA_OBSERVADA_CURUBA",
        referencia_evidencia=origen.referencia_evidencia if origen else None,
        ruta_origen=origen.ruta_origen if origen else None,
        fecha_corte=origen.fecha_corte if origen else None,
        fecha_consulta=origen.fecha_consulta if origen else None,
        estado_certificacion=EC.OBSERVADO if valor is not None else EC.NO_DISPONIBLE,
        estado=(EstadoOperandoCapacidadV2.OBSERVADO_EN_CURUBA
                if valor is not None else EstadoOperandoCapacidadV2.NO_EVALUABLE),
        observacion=observacion, formula_id=formula_id, formula_version="observada-7a-1")


def _ingresos(hechos):
    matriz = hechos.matriz_economica_solicitud
    if matriz is None:
        return OperandoCapacidadV2(fuente="CORE_MATRIZ_ECONOMICA",
            ruta_origen="matrizEconomicaSolicitud.totalDevengadoMensual",
            estado=EstadoOperandoCapacidadV2.NO_DISPONIBLE,
            observacion="Matriz no disponible; sin fallback a sueldo u otra fuente")
    h = matriz.total_devengado_mensual
    datos = {f.name: getattr(h, f.name) for f in fields(Hecho)}
    datos["fuente"] = h.fuente or "CORE_MATRIZ_ECONOMICA"
    datos["observacion"] = ((h.observacion + "; ") if h.observacion else "") + (
        "Total mensual recibido sin recomputar conceptos" if h.disponible else
        "Total mensual no disponible; sin fallback a otra fuente")
    return OperandoCapacidadV2(**datos, estado=(EstadoOperandoCapacidadV2.DISPONIBLE
                                               if h.disponible else EstadoOperandoCapacidadV2.NO_DISPONIBLE))


def _gastos(ingresos):
    valor = None
    if ingresos.disponible:
        valor = trunc_producto(ingresos.valor, Decimal("0.25"))
    return _formula(valor, formula_id="gastos_familiares_curuba_shadow", origen=ingresos,
        observacion="TRUNC(ingresos * 0.25) hacia cero; observacion CURUBA, no politica certificada"
                    if valor is not None else "Ingresos no disponibles; gastos no calculables")


def _cuota_solicitada(entrada):
    motivos = []
    if entrada.monto_solicitado is None:
        motivos.append("MONTO_NO_DISPONIBLE")
    if entrada.plazo is None or entrada.plazo <= 0:
        motivos.append("PLAZO_NO_POSITIVO_O_AUSENTE")
    if entrada.periodicidad_pago != "MENSUAL":
        motivos.append("PERIODICIDAD_NO_HOMOLOGADA_A_MENSUAL")
    valor = None
    if not motivos:
        valor = trunc_cuota(entrada.monto_solicitado, entrada.plazo)
    return _formula(valor, formula_id="cuota_solicitada_mensual_curuba_shadow",
        observacion="; ".join(motivos) if motivos else
                    "TRUNC(monto / plazo) hacia cero, exclusivamente mensual homologado en entrada shadow")


def evaluar_operandos_capacidad_shadow_v2(
        operandos: OperandosCapacidadShadowV2) -> CapacidadPagoShadowV2:
    """Aritmetica pura. Permite operandos sinteticos, no obtiene ni homologa hechos."""
    if not isinstance(operandos, OperandosCapacidadShadowV2):
        raise TypeError("Requiere operandos shadow tipados")
    datos = {f.name: getattr(operandos, f.name) for f in fields(OperandosCapacidadShadowV2)}
    valor = None
    if not operandos.faltantes:
        with localcontext() as ctx:
            ctx.prec = precision_exacta(*(h.valor for h in datos.values()))
            valor = (operandos.ingresos.valor - operandos.deducciones_dc.valor
                     - operandos.deducciones_internas.valor - operandos.cuota_solicitada.valor
                     - operandos.gastos_familiares.valor + operandos.cuota_credito_recogida.valor)
    capacidad = _formula(valor, formula_id="capacidad_pago_curuba_shadow",
        observacion=("Operandos faltantes: " + ", ".join(operandos.faltantes)) if operandos.faltantes else
                    "ingresos - deducciones_dc - deducciones_internas - cuota_solicitada "
                    "- gastos_familiares + cuota_credito_recogida; sin redondeo ni decision")
    return CapacidadPagoShadowV2(**datos, capacidad_calculada=capacidad)


def construir_capacidad_shadow_v2(
        entrada: EntradaCapacidadShadowV2, *, hechos_internos: HechosInternosCrediHoyV2,
        hechos_externos: HechosExternosCrediHoyV2 | None = None) -> CapacidadPagoShadowV2:
    """Proyecta cuatro operandos y mantiene las dos deducciones pendientes, sin overrides."""
    if not isinstance(entrada, EntradaCapacidadShadowV2):
        raise TypeError("Requiere entrada shadow tipada")
    if not isinstance(hechos_internos, HechosInternosCrediHoyV2):
        raise TypeError("Requiere hechos internos V2")
    if hechos_externos is not None and not isinstance(hechos_externos, HechosExternosCrediHoyV2):
        raise TypeError("Requiere hechos externos V2 o None")
    ingresos = _ingresos(hechos_internos)
    recogida = entrada.cuota_credito_recogida_declarada
    return evaluar_operandos_capacidad_shadow_v2(OperandosCapacidadShadowV2(
        ingresos=ingresos, gastos_familiares=_gastos(ingresos),
        cuota_solicitada=_cuota_solicitada(entrada),
        cuota_credito_recogida=OperandoCapacidadV2(valor=recogida, disponible=recogida is not None,
            fuente="DECLARADO_ASESOR", ruta_origen="entrada_shadow.cuota_credito_recogida_declarada",
            estado_certificacion=EC.OBSERVADO if recogida is not None else EC.NO_DISPONIBLE,
            estado=(EstadoOperandoCapacidadV2.DISPONIBLE if recogida is not None
                    else EstadoOperandoCapacidadV2.NO_DISPONIBLE),
            observacion="Declaracion explicita del asesor, no recuperada de CA093 ni C3"
                        if recogida is not None else "Cuota recogida no suministrada; no se presume cero"),
        deducciones_internas=OperandoCapacidadV2(fuente="POLITICA_INTERNA_PENDIENTE",
            estado=EstadoOperandoCapacidadV2.POLITICA_PENDIENTE,
            observacion="Seleccion de obligaciones no certificada; no sumar CA093 ni matriz deducida"),
        deducciones_dc=OperandoCapacidadV2(fuente="HDC_V2",
            referencia_evidencia=hechos_externos.referencia_evidencia if hechos_externos else None,
            fecha_consulta=hechos_externos.fecha_consulta if hechos_externos else None,
            estado=EstadoOperandoCapacidadV2.HOMOLOGACION_PENDIENTE,
            observacion="Contrato HDC V2 sin magnitud agregada homologada monetaria y mensual; "
                        "no sumar cuotas, inferir moneda/escala/periodicidad ni asumir cobertura")))
