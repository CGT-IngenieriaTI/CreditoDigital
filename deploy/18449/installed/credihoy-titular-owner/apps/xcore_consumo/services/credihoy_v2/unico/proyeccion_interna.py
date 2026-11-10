"""Proyeccion pura del contrato interno existente; no consulta ni decide."""

from dataclasses import replace
from datetime import datetime

from ..base import Hecho, instante, EstadoCertificacion as EC
from ..hechos import HechosInternosCrediHoyV2
from .reglas import HechosReglasUnicoV2


def existencia_credihoy_registrado(codigos, *, completo, fecha_corte, referencia):
    if not completo or any(not h.disponible or h.estado_certificacion!=EC.CERTIFICADO for h in codigos):
        return Hecho(fuente='CA090MGSOLCRED.K_MODALI',observacion='INVENTARIO_MODALIDADES_INCOMPLETO')
    return Hecho(valor=any(h.valor=='111' for h in codigos),disponible=True,
        fuente='CA090MGSOLCRED.K_MODALI',ruta_origen='obligacionesActuales.obligaciones[].modalidad.codigo',
        referencia_evidencia=referencia,fecha_corte=fecha_corte,estado_certificacion=EC.CERTIFICADO,
        observacion='Existencia en registros del titular al consultar; no saldo cero ni credito recogido ni decision')


def proyectar_reglas_internas(hechos: HechosInternosCrediHoyV2, *, fecha: datetime):
    instante(fecha, "fecha_evaluacion")
    if not isinstance(hechos, HechosInternosCrediHoyV2):
        raise TypeError("Requiere HechosInternosCrediHoyV2")
    if hechos.metadatos is not None and hechos.metadatos.fecha_evaluacion != fecha:
        raise ValueError("Hechos de otra fecha de evaluacion")
    nacimiento = hechos.fecha_nacimiento
    edad = Hecho(fuente="LINIX_FECHA_NACIMIENTO_NO_DISPONIBLE")
    if nacimiento.disponible and nacimiento.valor <= fecha.date():
        valor = fecha.year - nacimiento.valor.year - (
            (fecha.month, fecha.day) < (nacimiento.valor.month, nacimiento.valor.day))
        edad = replace(nacimiento, valor=valor, fecha_corte=fecha.date(),
            observacion="Anos cumplidos al corte; conserva certificacion de nacimiento")
    # Ni una cartera A individual ni un documento203 acreditan los hechos globales.
    result = HechosReglasUnicoV2(edad_anios=edad,
        saldo_aportes_utilizable=hechos.saldo_aportes_utilizable)
    actuales=hechos.obligaciones_actuales
    if actuales is not None and actuales.estado is not None:
        result=replace(result,existe_credihoy_111_registrado=existencia_credihoy_registrado(
            tuple(o.modalidad.codigo for o in actuales.obligaciones),completo=True,
            fecha_corte=actuales.fecha_corte,referencia=hechos.metadatos.request_id if hechos.metadatos else 'INVENTARIO_CA090'))
    if hechos.financieros is not None:
        f = hechos.financieros
        result = replace(result, persona_natural=f.persona_natural,
            calificacion_interna=f.calificacion_cliente, limite_aportes_90=f.limite_aportes_90,
            fecha_actualizacion_datos=f.fecha_actualizacion_cliente)
    return result
