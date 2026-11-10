"""Mapping del contrato oficial credihoy-hechos-v1, sin autorizacion productiva."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from ..credihoy.contexto import Certificacion, DatosInternos, Hecho, ObligacionInterna
from .dto import (
    RespuestaApiDataDTO, booleano, decimal_exacto, entero_nullable,
    fecha_nullable, invalida, objeto, texto,
)


FUENTE = "API_DATA_V1"


@dataclass(frozen=True, kw_only=True)
class EvidenciaInterna:
    version_contrato: str | None = None
    request_id: str | None = None
    habilitado_decision_productiva: bool = False
    fecha_consulta: datetime | None = None
    fecha_evaluacion: datetime | None = None
    periodo_anterior_original: str | None = None
    estado_cierre_operativo: str | None = None
    estado_cartera: str | None = None
    cartera_disponible_fuente: bool = False
    estado_cuota: str | None = None
    estado_aportes: str | None = None
    estado_consolidacion: str | None = None
    estado_semantica_estados: str | None = None
    aportes_disponible_fuente: bool = False
    saldo_individual_observado: Decimal | None = field(default=None, repr=False)
    utilizable_decision: bool = field(default=False, init=False)
    motivo_aportes: str = "SEMANTICA_APORTES_PENDIENTE_CERTIFICACION"
    saldos_documentos: tuple[Decimal | None, ...] = field(default=(), repr=False)
    codigo_error: str | None = None
    pendientes: tuple[str, ...] = ("ESTADO_ASOCIADO_PENDIENTE_HOMOLOGACION",)


@dataclass(frozen=True, kw_only=True)
class HechosInternosAdaptados:
    datos: DatosInternos = field(repr=False)
    evidencia: EvidenciaInterna


def _hecho(valor, disponible, *, fecha=None, periodo=None, certificado=False):
    return Hecho(valor=valor if disponible else None, disponible=disponible,
                 fuente=FUENTE, fecha_corte=fecha, periodo=periodo,
                 estado=Certificacion.CERTIFICADO if certificado else Certificacion.NO_CERTIFICADO)


def _periodo(c1):
    disponible = booleano(c1.get("disponible"))
    valor = c1.get("valor")
    cierre = texto(c1.get("estadoCierreOperativo"))
    corte = fecha_nullable(c1.get("fechaCorte"))
    normalizado = None
    if valor is not None:
        if not disponible or not isinstance(valor, str) or len(valor) != 6 or not valor.isascii() or not valor.isdigit():
            invalida()
        try:
            normalizado = date(int(valor[:4]), int(valor[4:]), 1).strftime("%Y-%m")
        except ValueError:
            invalida()
    if disponible and (normalizado is None or corte is None):
        invalida()
    # Certificar el dato periodo no certifica el cierre operativo.
    return _hecho(normalizado, disponible, fecha=corte, periodo=normalizado,
                  certificado=disponible), valor, cierre


def _cartera(c2, periodo):
    disponible = booleano(c2.get("disponible"))
    estado = texto(c2.get("estado"))
    filas = c2.get("obligaciones")
    if not isinstance(filas, list) or estado not in {"DISPONIBLE", "PARCIAL", "SIN_REGISTROS_AL_CORTE"}:
        invalida()
    if estado == "SIN_REGISTROS_AL_CORTE" and (disponible or filas):
        invalida()
    if estado == "DISPONIBLE" and (not disponible or not filas):
        invalida()
    obligaciones = []
    for fila in filas:
        fila = objeto(fila)
        campos = ("tipoDocumento", "numeroObligacion", "clasificacionCierre", "diasMoraAlCierre",
                  "diasVencidosAlCierre", "saldoMoraRegistradoAlCierre", "saldoCapitalAlCierre")
        if any(campo not in fila for campo in campos):
            invalida()
        obligaciones.append(ObligacionInterna(
            a_tipodr=texto(fila["tipoDocumento"], nullable=True),
            a_obliga=texto(fila["numeroObligacion"], nullable=True),
            q_clasif=texto(fila["clasificacionCierre"], nullable=True),
            q_diamora=entero_nullable(fila["diasMoraAlCierre"]),
            q_diaven=entero_nullable(fila["diasVencidosAlCierre"]),
            v_salmor=decimal_exacto(fila["saldoMoraRegistradoAlCierre"]),
            v_salkap=decimal_exacto(fila["saldoCapitalAlCierre"]),
        ))
    # PARCIAL conserva filas observadas, sin certificar completitud de cartera.
    return _hecho(tuple(obligaciones), disponible or bool(obligaciones),
                  fecha=fecha_nullable(c2.get("fechaCorte")), periodo=periodo,
                  certificado=disponible and estado == "DISPONIBLE"), estado, disponible


def _cuota(c3):
    disponible = booleano(c3.get("disponible"))
    estado = texto(c3.get("estado"))
    valor = decimal_exacto(c3.get("valor"))
    if disponible or estado != "NO_CERTIFICADO" or valor is not None or "valor" not in c3:
        invalida()
    return _hecho(valor, disponible, fecha=fecha_nullable(c3.get("fechaCorte")),
                  certificado=disponible and estado == "CERTIFICADO"), estado


def _aportes(c4):
    disponible = booleano(c4.get("disponible"))
    documentos = c4.get("documentos")
    if not isinstance(documentos, list):
        invalida()
    for documento in documentos:
        documento = objeto(documento)
        if "saldo" not in documento:
            invalida()
        if "producto" in documento and documento["producto"] not in ("203", 203):
            invalida()
    saldos = tuple(decimal_exacto(d["saldo"]) for d in documentos)
    estado = texto(c4.get("estado"))
    consolidacion = texto(c4.get("estadoConsolidacion"))
    semantica = texto(c4.get("estadoSemanticaEstados"))
    if consolidacion != "NO_CONSOLIDADO" or semantica != "PENDIENTE_CERTIFICACION":
        invalida()
    if not documentos:
        esperado = "SIN_REGISTROS_203"
    elif len(documentos) > 1:
        esperado = "AMBIGUO"
    elif saldos[0] is None:
        esperado = "PARCIAL"
    else:
        esperado = "POSICIONES_DISPONIBLES"
    if estado != esperado or disponible != (esperado == "POSICIONES_DISPONIBLES"):
        invalida()
    observado = saldos[0] if disponible else None
    return (_hecho(None, False, fecha=fecha_nullable(c4.get("fechaCorte"))),
            estado, consolidacion, semantica, saldos, observado, disponible)


class AdaptadorHechosInternos:
    def adaptar(self, respuesta: RespuestaApiDataDTO) -> HechosInternosAdaptados:
        c1, c2 = respuesta.periodo_anterior, respuesta.cartera_interna
        c3, c4 = respuesta.cuota_vigente_interna, respuesta.aportes_sociales
        periodo, original, cierre = _periodo(c1)
        cartera, estado_cartera, disponible_cartera = _cartera(c2, periodo.valor)
        cuota, estado_cuota = _cuota(c3)
        aporte, estado_aportes, consolidacion, semantica, saldos, observado, disponible_aportes = _aportes(c4)
        return HechosInternosAdaptados(
            datos=DatosInternos(
                estado_asociado=Hecho(fuente="PENDIENTE_HOMOLOGACION"),
                periodo_anterior=periodo, obligaciones_internas=cartera,
                cuota_vigente_interna=cuota, aporte_social_203=aporte,
            ), evidencia=EvidenciaInterna(
                version_contrato=respuesta.version_contrato, request_id=respuesta.request_id,
                habilitado_decision_productiva=respuesta.habilitado_decision_productiva,
                fecha_consulta=respuesta.fecha_consulta, fecha_evaluacion=respuesta.fecha_evaluacion,
                periodo_anterior_original=original, estado_cierre_operativo=cierre,
                estado_cartera=estado_cartera, cartera_disponible_fuente=disponible_cartera,
                estado_cuota=estado_cuota, estado_aportes=estado_aportes,
                estado_consolidacion=consolidacion, estado_semantica_estados=semantica,
                saldos_documentos=saldos,
                saldo_individual_observado=observado, aportes_disponible_fuente=disponible_aportes,
                pendientes=("ESTADO_ASOCIADO_PENDIENTE_HOMOLOGACION",
                            "SEMANTICA_APORTES_PENDIENTE_CERTIFICACION"),
            ),
        )


def hechos_no_disponibles(codigo_error, request_id):
    ausente = Hecho(fuente=FUENTE)
    return HechosInternosAdaptados(
        datos=DatosInternos(estado_asociado=Hecho(fuente="PENDIENTE_HOMOLOGACION"),
                           periodo_anterior=ausente, obligaciones_internas=ausente,
                           cuota_vigente_interna=ausente, aporte_social_203=ausente),
        evidencia=EvidenciaInterna(codigo_error=codigo_error, request_id=request_id),
    )
