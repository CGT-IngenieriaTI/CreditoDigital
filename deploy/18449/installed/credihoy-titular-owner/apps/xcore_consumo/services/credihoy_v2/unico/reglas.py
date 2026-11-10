"""Cumplimiento documental UNICO, separado de capacidad y decision crediticia."""

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date, datetime

from ..base import DTO, EstadoCertificacion as EC, Hecho
from ..contratos import (
    EfectoVariable, ResultadoGlobalCrediHoyV2, ResultadoVariable as RV,
    ResultadoVariableCrediHoyV2,
)
from .dto import CapacidadCredihoyUnicoV2, EntradaCredihoyUnicoV2
from .inventario import inventario_reglas_unico_v2
from .normativa import limite_monetario, actualizacion_al_corte, POLITICA_ACTUALIZACION, MENSAJE_ACTUALIZACION


def _ausente():
    return Hecho(fuente="CONTEXTO_UNICO")


@dataclass(frozen=True, kw_only=True)
class HechosReglasUnicoV2(DTO):
    """Hechos explicitos; no se completan desde saldos, codigos o listas HDC.

    El proveedor del contexto debe certificar corte, identidad y vigencia del
    SMMLV. Estos operandos no se obtienen de contratos que aun no los certifican.
    """

    edad_anios: Hecho[int] = field(default_factory=_ausente)
    persona_natural: Hecho[bool] = field(default_factory=_ausente)
    consulta_central_realizada: Hecho[bool] = field(default_factory=_ausente)
    cuentas_embargadas: Hecho[bool] = field(default_factory=_ausente)
    calificacion_interna: Hecho[str] = field(default_factory=_ausente)
    smmlv_vigente: Hecho[Decimal] = field(default_factory=_ausente)
    saldo_aportes_utilizable: Hecho[Decimal] = field(default_factory=_ausente)
    limite_aportes_90: Hecho[Decimal] = field(default_factory=_ausente)
    fecha_actualizacion_datos: Hecho[date | datetime] = field(default_factory=_ausente)
    existe_credihoy_111_registrado: Hecho[bool] = field(default_factory=_ausente)


def _certificado(hecho):
    return (hecho.disponible and hecho.estado_certificacion == EC.CERTIFICADO
            and bool(hecho.fuente and hecho.ruta_origen))


def estado_evaluacion_regla(regla):
    """La comparacion y su efecto crediticio pendiente son dimensiones distintas."""
    if regla.motivos in {("REGLA_INACTIVA_NO_APLICADA",), ("SOLO_RENOVACION_GER_PO_068_6_2_3_7",)}:
        return "NO_APLICA"
    if regla.resultado != RV.NO_EVALUABLE:
        return regla.resultado.value
    if regla.motivos[0] in {
        "UMBRAL_Y_EFECTO_CAPACIDAD_PENDIENTES_APROBACION",
        "CORTE_ACTUALIZACION_Y_SEMANTICA_MES_PENDIENTES",
        "PROMEDIO_ULTIMO_CREDITO_Y_EXCEPCION_RENOVACION_PENDIENTES",
        "EFECTO_SALDO_PENDIENTE_NO_ESTABLECIDO_EN_POLITICA",
        "DEFINICION_FUNCIONAL_PENDIENTE",
    }:
        return "PENDIENTE_POLITICA"
    return "NO_EVALUABLE"


def datos_faltantes_regla(regla):
    if estado_evaluacion_regla(regla) != "NO_EVALUABLE":
        return ()
    if regla.motivos in {("HECHO_NO_CERTIFICADO",), ("PARAMETRO_MONETARIO_INVALIDO",)}:
        return regla.operandos_referenciados[-1:]
    if regla.motivos == ("MONTO_NO_POSITIVO_O_AUSENTE",):
        return ("monto_solicitado",)
    return regla.operandos_referenciados


def evaluar_reglas_unico_v2(entrada: EntradaCredihoyUnicoV2, *,
        hechos: HechosReglasUnicoV2,
        capacidad: CapacidadCredihoyUnicoV2 | None = None,
        fecha_evaluacion: date | datetime | None = None) -> ResultadoGlobalCrediHoyV2:
    if not isinstance(entrada, EntradaCredihoyUnicoV2) or not isinstance(hechos, HechosReglasUnicoV2):
        raise TypeError("Requiere entrada y hechos tipados UNICO")
    if capacidad is not None:
        if not isinstance(capacidad, CapacidadCredihoyUnicoV2):
            raise TypeError("Requiere capacidad UNICO, no decision Consumo")
        if capacidad.validacion_recogida.entrada != entrada:
            raise ValueError("Capacidad de otra entrada no puede reutilizarse")
    resultados = []
    pendientes = []
    monto_valido = entrada.monto_solicitado is not None and entrada.monto_solicitado > 0
    for regla in inventario_reglas_unico_v2():
        resultado, motivo, refs = RV.NO_EVALUABLE, "DEFINICION_FUNCIONAL_PENDIENTE", ()
        efecto = EfectoVariable.PENDIENTE_DEFINICION

        def comparar(ok):
            return (RV.CUMPLE if ok else RV.NO_CUMPLE), "COMPARACION_DOCUMENTADA"

        if not regla.activa:
            motivo, efecto = "REGLA_INACTIVA_NO_APLICADA", EfectoVariable.INFORMATIVO
        elif regla.fila == 2:
            refs = ("capacidad_pago",)
            motivo = ("CAPACIDAD_NO_CALCULABLE" if capacidad is None or not capacidad.capacidad_pago.disponible
                      else "UMBRAL_Y_EFECTO_CAPACIDAD_PENDIENTES_APROBACION")
        elif regla.fila in (4, 6, 10, 21, 24):
            nombre = {4: "consulta_central_realizada", 6: "cuentas_embargadas", 10: "edad_anios",
                      21: "calificacion_interna", 24: "persona_natural"}[regla.fila]
            refs = (nombre,)
            hecho = getattr(hechos, nombre)
            motivo = "HECHO_NO_CERTIFICADO"
            if _certificado(hecho):
                valor = hecho.valor
                if (regla.fila == 10 and valor < 0) or (regla.fila == 21 and not valor.strip()):
                    motivo = "HECHO_INVALIDO"
                else:
                    ok = (valor >= 18 if regla.fila == 10 else valor == "A" if regla.fila == 21
                          else not valor if regla.fila == 6 else valor)
                    resultado, motivo = comparar(ok)
        elif regla.fila in (12, 14, 17):
            nombre = "saldo_aportes_utilizable" if regla.fila == 14 else "smmlv_vigente"
            refs = ("monto_solicitado", nombre)
            hecho = getattr(hechos, nombre)
            limite_aplicado = regla.fila == 14 and hechos.limite_aportes_90.disponible
            if limite_aplicado:
                hecho = hechos.limite_aportes_90
                refs = ("monto_solicitado", "limite_aportes_90")
            motivo = "MONTO_NO_POSITIVO_O_AUSENTE" if not monto_valido else "HECHO_NO_CERTIFICADO"
            if monto_valido and _certificado(hecho):
                if hecho.valor < 0 or (regla.fila != 14 and hecho.valor == 0):
                    motivo = "PARAMETRO_MONETARIO_INVALIDO"
                else:
                    limite = hecho.valor if limite_aplicado else limite_monetario(hecho.valor, regla.fila)
                    resultado, motivo = comparar(entrada.monto_solicitado >= limite if regla.fila == 17
                                                  else entrada.monto_solicitado <= limite)
        elif regla.fila == 23:
            refs = ("periodicidad_pago",)
            motivo = "PERIODICIDAD_AUSENTE"
            if entrada.periodicidad_pago is not None:
                resultado, motivo = comparar(entrada.periodicidad_pago == "MENSUAL")
        elif regla.fila in (27, 28):
            refs = ("plazo_meses",)
            motivo = "PLAZO_AUSENTE"
            if entrada.plazo_meses is not None:
                resultado, motivo = comparar(entrada.plazo_meses <= 48 if regla.fila == 27
                                              else entrada.plazo_meses >= 1)
        elif regla.fila == 9:
            refs = ('fecha_actualizacion_datos',)
            validity = actualizacion_al_corte(hechos.fecha_actualizacion_datos, fecha_evaluacion)
            motivo = 'FECHA_ACTUALIZACION_O_CORTE_NO_DISPONIBLE'
            efecto = EfectoVariable.BLOQUEA
            if validity is not None:
                resultado = RV.CUMPLE if validity['cumple'] else RV.NO_CUMPLE
                motivo = 'ACTUALIZACION_DENTRO_DE_MES_CALENDARIO' if validity['cumple'] else MENSAJE_ACTUALIZACION
        elif regla.fila == 31:
            motivo = "PROMEDIO_ULTIMO_CREDITO_Y_EXCEPCION_RENOVACION_PENDIENTES"
            if entrada.recogida is not None and entrada.recogida.aplica is False:
                motivo, efecto = 'SOLO_RENOVACION_GER_PO_068_6_2_3_7', EfectoVariable.INFORMATIVO
        elif regla.fila == 32:
            motivo = "EFECTO_SALDO_PENDIENTE_NO_ESTABLECIDO_EN_POLITICA"
        codigo = f"UNICO_FILA_{regla.fila}"
        evaluada = ResultadoVariableCrediHoyV2(codigo=codigo, resultado=resultado,
            version_regla="actualizacion-mes-calendario-v1" if regla.fila == 9 else "cd5a4-1",
            version_politica=POLITICA_ACTUALIZACION if regla.fila == 9 else "EXCEL_UNICO_a0339ce8",
            efecto=efecto, motivos=(motivo,), operandos_referenciados=refs)
        resultados.append(evaluada)
        if estado_evaluacion_regla(evaluada) == "PENDIENTE_POLITICA":
            pendientes.append(codigo + ":" + motivo)
    return ResultadoGlobalCrediHoyV2(resultados=tuple(resultados), pendientes=tuple(pendientes))
