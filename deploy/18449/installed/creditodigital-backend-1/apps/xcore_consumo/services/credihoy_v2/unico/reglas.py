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
    if regla.motivos in {("REGLA_INACTIVA_NO_APLICADA",), ("SOLO_RENOVACION_GER_PO_068_6_2_3_7",),
            ('AUSENCIA_CREDIHOY_111_CERTIFICADA_SIN_RECOGIDA',)}:
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
        fecha_evaluacion: date | datetime | None = None,
        capacidad_disponible=None, titularidad_recogida: Hecho[bool] | None = None) -> ResultadoGlobalCrediHoyV2:
    if not isinstance(entrada, EntradaCredihoyUnicoV2) or not isinstance(hechos, HechosReglasUnicoV2):
        raise TypeError("Requiere entrada y hechos tipados UNICO")
    if capacidad is not None:
        if not isinstance(capacidad, CapacidadCredihoyUnicoV2):
            raise TypeError("Requiere capacidad UNICO, no decision Consumo")
        if capacidad.validacion_recogida.entrada != entrada:
            raise ValueError("Capacidad de otra entrada no puede reutilizarse")
    comparacion_capacidad = evaluar_capacidad_disponible_regla_unico(capacidad_disponible)
    definicion = definicion_funcional_reglas_unico_v2()
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
            resultado, motivo, refs = comparacion_capacidad
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
            resultado, motivo, refs = evaluar_condicion_recogida_regla_unico(entrada,
                hechos.existe_credihoy_111_registrado, titularidad_recogida, fecha_evaluacion)
            if motivo == 'AUSENCIA_CREDIHOY_111_CERTIFICADA_SIN_RECOGIDA':
                efecto = EfectoVariable.INFORMATIVO
        codigo = f"UNICO_FILA_{regla.fila}"
        evaluada = ResultadoVariableCrediHoyV2(codigo=codigo, resultado=resultado,
            version_regla=(definicion['version'] if regla.fila in (2, 32) else
                "actualizacion-mes-calendario-v1" if regla.fila == 9 else "cd5a4-1"),
            version_politica=(definicion['procedencia'] if regla.fila in (2, 32) else
                POLITICA_ACTUALIZACION if regla.fila == 9 else "EXCEL_UNICO_a0339ce8"),
            efecto=efecto, motivos=(motivo,), operandos_referenciados=refs)
        resultados.append(evaluada)
        if estado_evaluacion_regla(evaluada) == "PENDIENTE_POLITICA":
            pendientes.append(codigo + ":" + motivo)
    return ResultadoGlobalCrediHoyV2(resultados=tuple(resultados), pendientes=tuple(pendientes))

def definicion_funcional_reglas_unico_v2():
    """Communicated functional scope, not a formal policy or production grant."""
    return {'version': 'UNICO_CAPACIDAD_Y_RECOGIDA_COMUNICADA_20261103_V1',
        'procedencia': 'COMUNICACION_USUARIO_RESPONSABLE_TECNICO_20261103',
        'adjunto': '9e00d122-000e-4d16-9557-e9d96bc0fdf3',
        'adjuntoSha256': 'b061d8d3f8e072e1b475d7d3291be70135638481ed8e1e6c92c946109c944145',
        'alcanceComunicado': 'CREDITO_Y_CARTERA_METODOLOGIA; AVAL_FUNCIONAL_NANCY_20261026',
        'comparacion': 'capacidadDisponible >= cuotaNuevaEstimada',
        'referenciaRatificacionFormal': None,
        'autorizacionPoliticaProductiva': 'NO_ACREDITADA_POR_ESTE_CALCULO',
        'autorizaDecisionFinal': False, 'ejecutaCompensacion': False}


def evaluar_capacidad_disponible_regla_unico(escenario):
    """Compare exact available funds, never the historical post-quota residual."""
    from .dto import EscenarioCapacidadDisponibleV2
    refs = ('capacidadDisponible', 'cuotaNuevaEstimada')
    if escenario is None:
        return RV.NO_EVALUABLE, 'CAPACIDAD_DISPONIBLE_O_CUOTA_ESTIMADA_NO_ACREDITADAS', refs
    if not isinstance(escenario, EscenarioCapacidadDisponibleV2):
        raise TypeError('Requiere escenario tipado de capacidad disponible')
    if escenario.cuota_nueva_estimada is None:
        return RV.NO_EVALUABLE, 'CUOTA_ESTIMADA_NO_ACREDITADA', refs
    cumple = escenario.capacidad_disponible >= escenario.cuota_nueva_estimada
    return (RV.CUMPLE if cumple else RV.NO_CUMPLE,
        'CAPACIDAD_DISPONIBLE_CUBRE_CUOTA_ESTIMADA' if cumple else 'CAPACIDAD_DISPONIBLE_INSUFICIENTE', refs)


def evaluar_condicion_recogida_regla_unico(entrada, existencia, titularidad, fecha_evaluacion=None):
    """Reuse evidenced 111 applicability; zero balance never proves absence."""
    from .pendientes import evaluar_propuesta_fila32
    from zoneinfo import ZoneInfo
    refs = ('existe_credihoy_111_registrado', 'recogida', 'titularidad_recogida')
    if fecha_evaluacion is not None:
        def day(value):
            if isinstance(value, datetime):
                return value.astimezone(ZoneInfo('America/Bogota')).date() if value.utcoffset() is not None else None
            return value if type(value) is date else None
        if day(fecha_evaluacion) is None or day(existencia.fecha_corte) != day(fecha_evaluacion):
            return RV.NO_EVALUABLE, 'CORTE_EXISTENCIA_111_NO_ACREDITADO', refs
    result = evaluar_propuesta_fila32(existencia, entrada,
        misma_titularidad=titularidad if titularidad is not None else _ausente())
    state = result['estado']
    if state == 'NO_APLICA':
        return RV.NO_EVALUABLE, 'AUSENCIA_CREDIHOY_111_CERTIFICADA_SIN_RECOGIDA', refs
    if state == 'CONDICION_RECOGIDA_ACREDITADA_NO_COMPENSADA':
        return RV.CUMPLE, 'RECOGIDA_111_PROPIA_ACREDITADA_SIN_COMPENSACION', refs
    if state in {'REQUIERE_RECOGIDA_OBLIGATORIA', 'RECOGIDA_INCONSISTENTE',
            'TITULAR_RECOGIDA_DISTINTO', 'RECOGIDA_NO_VALIDA'}:
        return RV.NO_CUMPLE, state, refs
    return RV.NO_EVALUABLE, 'HECHOS_RECOGIDA_111_NO_ACREDITADOS', refs

