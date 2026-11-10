"""Adaptacion shadow pura del contrato gateway; no consulta ni calcula cuotas."""

from datetime import datetime
from decimal import Decimal

from apps.integraciones.risk_gateway.contratos import HDCNormalizado, PreselectaNormalizado

from .base import EstadoCertificacion as EC, Hecho
from .hechos import (
    CoberturaHDCV2, CuentaHDCV2, EstadoCuentaHDCV2, HechosExternosCrediHoyV2,
    ImporteHDCV2, ObservacionHDCV2, PreselectaV2,
)


def _hecho(valor, referencia, ruta):
    return Hecho(valor=valor, disponible=valor is not None, fuente="RISK_GATEWAY",
        referencia_evidencia=referencia, ruta_origen=ruta,
        estado_certificacion=EC.OBSERVADO if valor is not None else EC.NO_DISPONIBLE)


def _importe(origen, referencia, ruta):
    if origen is None:
        return ImporteHDCV2(valor_original=None, unidad_original=None, moneda_original=None,
            estado_homologacion=EC.NO_DISPONIBLE, referencia_evidencia=referencia, ruta_origen=ruta)
    return ImporteHDCV2(valor_original=origen["valor_original"], unidad_original=origen["unidad_original"],
        moneda_original=origen["moneda_codigo"], valor_normalizado=origen["valor_monetario"],
        estado_homologacion=EC.OBSERVADO, referencia_homologacion=origen["referencia"],
        referencia_evidencia=referencia, ruta_origen=ruta, evidencia_gateway=origen)


def adaptar_preselecta_a_v2(evidencia: PreselectaNormalizado) -> PreselectaV2:
    if type(evidencia) is not PreselectaNormalizado:
        raise TypeError("Requiere PRESELECTA_NORMALIZADO_V1 validado")
    r = evidencia["resultado"]
    proveedor = r["resultado_proveedor"]
    referencia = r["consulta_id"]
    def campo(nombre):
        origen = proveedor[nombre]
        valor = None if origen is None else origen["valor_original"]
        # El contrato admite escalares no textuales: se conservan en la evidencia,
        # no se convierten a etiquetas de negocio para satisfacer el DTO V2.
        return _hecho(valor if type(valor) is str else None, referencia,
                      f"resultado.resultado_proveedor.{nombre}.valor_original")
    score = proveedor["score"]
    return PreselectaV2(decision=campo("decision"), riesgo_score=campo("riesgo"),
        score=_hecho(None if score is None else score["valor_homologado"], referencia,
                     "resultado.resultado_proveedor.score.valor_homologado"), evidencia_gateway=evidencia)


def adaptar_hdc_a_v2(evidencia: HDCNormalizado, *, preselecta=None) -> HechosExternosCrediHoyV2:
    if type(evidencia) is not HDCNormalizado:
        raise TypeError("Requiere HDC_NORMALIZADO_V1 validado")
    r = evidencia["resultado"]
    ref = r["consulta_id"]
    cuentas = []
    for i, o in enumerate(r["obligaciones"]):
        ruta = f"resultado.obligaciones[{i}]"
        def h(valor, campo):
            return _hecho(valor, ref, f"{ruta}.{campo}")
        estados = []
        for campo in ("estadoPago", "estadoCuenta", "estadoOrigen", "estadoPlastico"):
            origen = o[campo]
            if origen is not None:
                estados.append(EstadoCuentaHDCV2(tipo=h(campo, campo),
                    codigo=h(origen["codigo_original"], f"{campo}.codigo_original"),
                    fecha=h(None, f"{campo}.fecha"), meses=h(None, f"{campo}.meses"),
                    descripcion=h(origen["descripcion"], f"{campo}.descripcion"), ruta_origen=f"{ruta}.{campo}"))
        observaciones = []
        for j, v in enumerate(o["valores"]):
            vr = f"{ruta}.valores[{j}]"
            def vh(valor, campo):
                return _hecho(valor, ref, f"{vr}.{campo}")
            observaciones.append(ObservacionHDCV2(fecha=vh(v["fecha"], "fecha"),
                moneda_original=vh(v["moneda"]["codigo_original"], "moneda.codigo_original"),
                unidad_original=vh(None, "unidad_original"),
                periodicidad_original=vh(None if v["periodicidad"] is None else v["periodicidad"]["codigo_original"],
                                         "periodicidad.codigo_original"),
                saldo_actual=_importe(v["saldoActual"], ref, f"{vr}.saldoActual"),
                saldo_mora=_importe(v["saldoMora"], ref, f"{vr}.saldoMora"),
                cuota=_importe(v["cuota"], ref, f"{vr}.cuota"), dias_mora=vh(None, "dias_mora"),
                valor_inicial=_importe(v["valorInicial"], ref, f"{vr}.valorInicial"),
                cupo_total=_importe(v["cupoTotal"], ref, f"{vr}.cupoTotal"),
                posicion_original=v["posicion_original"], ruta_origen=vr))
        cuentas.append(CuentaHDCV2(referencia_local=h(o["identificador"]["numero"], "identificador.numero"),
            tipo_cuenta=h(o["tipo_estructural"], "tipo_estructural"),
            entidad_codigo=h(o["codSuscriptor"], "codSuscriptor"),
            rol_original=h(None if o["calidadDeudor"] is None else o["calidadDeudor"]["codigo_original"], "calidadDeudor.codigo_original"),
            sector_codigo_original=h(o["sector"]["codigo_original"], "sector.codigo_original"),
            embargo=h(None, "embargo"), tipo_embargo=h(None, "tipo_embargo"),
            estados_originales=tuple(estados), observaciones=tuple(observaciones),
            entidad=h(o["entidad"], "entidad"),
            subtipo_cuenta_original=h(o["tipoCuenta"]["subtipo_original"], "tipoCuenta.subtipo_original"),
            fecha_apertura=h(o["fechas"]["apertura"], "fechas.apertura"),
            fecha_vencimiento=h(o["fechas"]["vencimiento"], "fechas.vencimiento"),
            posicion_original=o["posicion_original"], ruta_origen=ruta))
    fecha_original = r["fecha_consulta"]
    fecha = None
    if fecha_original is not None:
        try:
            candidata = datetime.fromisoformat(fecha_original.replace("Z", "+00:00"))
            if candidata.utcoffset() is not None:
                fecha = candidata
        except ValueError:
            pass  # El contrato conserva fechas originales sin certificar su formato.
    cobertura = tuple(CoberturaHDCV2(tipo_cuenta=tipo, registros_observados=r["cobertura"][campo])
        for campo, tipo in (("total_carteras", "CuentaCartera"), ("total_tarjetas", "TarjetaCredito"),
                            ("total_ahorros", "CuentaAhorro"), ("total_corrientes", "CuentaCorriente"),
                            ("total_otros", "Otros")))
    return HechosExternosCrediHoyV2(referencia_evidencia=ref, fecha_consulta=fecha,
        fecha_consulta_original=fecha_original, cuentas=tuple(cuentas), cobertura_por_tipo=cobertura,
        campos_faltantes=r["cobertura"]["faltantes"], evidencia_gateway_hdc=evidencia,
        preselecta=PreselectaV2() if preselecta is None else adaptar_preselecta_a_v2(preselecta))


def materializar_cuotas_hdc_brutas(externos: HechosExternosCrediHoyV2 | None) -> Hecho[Decimal]:
    """Proyecta solo el agregado controlado 9F-B; nunca reconstruye el detalle."""
    if externos is not None and not isinstance(externos, HechosExternosCrediHoyV2):
        raise TypeError("Requiere hechos externos V2")
    evidencia = None if externos is None else externos.evidencia_gateway_hdc
    ruta = "resultado.endeudamiento_actual.cuota_mensual_pesos"
    motivo = "ENDEUDAMIENTO_ACTUAL_AUSENTE"
    valor = None
    if evidencia is not None:
        agregado = evidencia["resultado"].get("endeudamiento_actual")
        if agregado is not None:
            if agregado["estado_consistencia"] != "CONSISTENTE":
                motivo = "ENDEUDAMIENTO_ACTUAL_" + agregado["estado_consistencia"]
            elif not agregado["disponible"]:
                motivo = "ENDEUDAMIENTO_ACTUAL_NO_DISPONIBLE"
            elif agregado["cuota_mensual_pesos"] is None:
                motivo = "CUOTA_MENSUAL_PESOS_AUSENTE"
            else:
                valor = agregado["cuota_mensual_pesos"]
                motivo = "ADAPTACION_CONGENTE; " + agregado["referencia"]
    return Hecho(valor=valor, disponible=valor is not None, fuente="HDC/DATACREDITO",
        referencia_evidencia=None if evidencia is None else evidencia["resultado"]["consulta_id"],
        ruta_origen=ruta, fecha_consulta=None if externos is None else externos.fecha_consulta,
        estado_certificacion=EC.OBSERVADO if valor is not None else EC.NO_DISPONIBLE,
        observacion=motivo)
