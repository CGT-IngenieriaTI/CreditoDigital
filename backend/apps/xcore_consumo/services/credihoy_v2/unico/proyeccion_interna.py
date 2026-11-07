"""Proyeccion pura del contrato interno existente; no consulta ni decide."""



from zoneinfo import ZoneInfo

from dataclasses import replace
from datetime import datetime

from ..base import Hecho, instante, EstadoCertificacion as EC
from ..hechos import HechosInternosCrediHoyV2
from .reglas import HechosReglasUnicoV2


def existencia_credihoy_registrado(obligaciones, *, completo, fecha_corte, referencia):
    from datetime import date
    from decimal import Decimal
    from ..hechos import ObligacionInternaCrediHoyV2
    source = 'CA090MGSOLCRED.K_MODALI+I_ESTSOL+I_APROBA+I_LIQUID+I_CANCEL+I_ANULAD+V_SALDO'

    def pending(code):
        return Hecho(fuente=source, observacion=code)
    if not completo or fecha_corte is None or (not referencia):
        return pending('INVENTARIO_CICLO_VIDA_INCOMPLETO')

    def recorded(fact, *, nullable=False):
        return isinstance(fact, Hecho) and all((fact.fuente, fact.ruta_origen, fact.referencia_evidencia)) and (fact.fecha_corte == fecha_corte) and (fact.disponible or (nullable and fact.valor is None))
    unknown = False
    active = False
    if isinstance(fecha_corte, datetime) and fecha_corte.utcoffset() is None:
        return pending('CORTE_CICLO_VIDA_INVALIDO')
    cut = fecha_corte.astimezone(ZoneInfo('America/Bogota')).date() if isinstance(fecha_corte, datetime) else fecha_corte
    if not isinstance(cut, date):
        return pending('CORTE_CICLO_VIDA_INVALIDO')
    for item in obligaciones:
        if not isinstance(item, ObligacionInternaCrediHoyV2):
            unknown = True
            continue
        modality = item.modalidad.codigo
        if not recorded(modality) or modality.estado_certificacion != EC.CERTIFICADO:
            unknown = True
            continue
        if modality.valor != '111':
            continue
        fields = (item.estado_solicitud, item.aprobada, item.liquidada, item.indicador_cancelacion, item.indicador_anulacion, item.saldo_registrado)
        if not all((recorded(f) for f in fields)):
            unknown = True
            continue
        state, approved, liquidated, cancelled, annulled, balance = (f.valor for f in fields)
        if approved not in {'Y', 'N'} or liquidated not in {'Y', 'N'} or cancelled not in {'Y', 'H', 'N'} or (annulled not in {'Y', 'N'}) or (type(balance) is not Decimal) or (not balance.is_finite()) or (balance < 0):
            unknown = True
            continue
        if balance == 0 and (liquidated == 'N' or cancelled in {'Y', 'H'} or annulled == 'Y' or (state in {'N', 'R'})):
            continue
        loan_date = item.fecha_prestamo
        loan_day = loan_date.valor.astimezone(ZoneInfo('America/Bogota')).date() if isinstance(loan_date.valor, datetime) and loan_date.valor.utcoffset() is not None else loan_date.valor.date() if isinstance(loan_date.valor, datetime) else loan_date.valor
        identity = (item.tipo_obligacion, item.numero_obligacion)
        if state == 'C' and approved == 'Y' and (liquidated == 'Y') and (cancelled == 'N') and (annulled == 'N') and (balance > 0) and recorded(loan_date) and isinstance(loan_day, date) and (loan_day <= cut) and recorded(item.fecha_cancelacion, nullable=True) and (item.fecha_cancelacion.valor is None) and all((recorded(f) and isinstance(f.valor, str) and f.valor.strip() for f in identity)):
            active = True
        else:
            unknown = True
    if unknown and (not active):
        return pending('CICLO_VIDA_111_NO_ACREDITADO_O_CONTRADICTORIO')
    return Hecho(valor=active, disponible=True, fuente=source, ruta_origen='obligacionesActuales.obligaciones[].cicloVida111', referencia_evidencia='CICLO_VIDA_111_V1:' + str(referencia), fecha_corte=fecha_corte, estado_certificacion=EC.CERTIFICADO, observacion='Predicado derivado del inventario completo y estados registrados al corte; SC o A_OBLIGA solas no prueban credito vigente. No certifica desembolso ni altera estados del core.')


def proyectar_reglas_internas(hechos: HechosInternosCrediHoyV2, *, fecha: datetime):
    instante(fecha, 'fecha_evaluacion')
    if not isinstance(hechos, HechosInternosCrediHoyV2):
        raise TypeError('Requiere HechosInternosCrediHoyV2')
    if hechos.metadatos is not None and hechos.metadatos.fecha_evaluacion != fecha:
        raise ValueError('Hechos de otra fecha de evaluacion')
    nacimiento = hechos.fecha_nacimiento
    corte = fecha.astimezone(ZoneInfo('America/Bogota')).date()
    edad = Hecho(fuente='LINIX_FECHA_NACIMIENTO_NO_DISPONIBLE')
    if nacimiento.disponible and nacimiento.valor <= corte:
        valor = corte.year - nacimiento.valor.year - ((corte.month, corte.day) < (nacimiento.valor.month, nacimiento.valor.day))
        edad = replace(nacimiento, valor=valor, fecha_corte=corte, observacion='Anos cumplidos al corte; conserva certificacion de nacimiento')
    result = HechosReglasUnicoV2(edad_anios=edad, saldo_aportes_utilizable=hechos.saldo_aportes_utilizable)
    actuales = hechos.obligaciones_actuales
    if actuales is not None and actuales.estado is not None:
        result = replace(result, existe_credihoy_111_registrado=existencia_credihoy_registrado(actuales.obligaciones, completo=actuales.temporalidad == 'REGISTROS_AL_CONSULTAR' and hechos.metadatos is not None and (actuales.fecha_corte == hechos.metadatos.fecha_consulta), fecha_corte=actuales.fecha_corte, referencia=hechos.metadatos.request_id if hechos.metadatos else 'INVENTARIO_CA090'))
    if hechos.financieros is not None:
        f = hechos.financieros
        result = replace(result, persona_natural=f.persona_natural, calificacion_interna=f.calificacion_cliente, limite_aportes_90=f.limite_aportes_90, fecha_actualizacion_datos=f.fecha_actualizacion_cliente)
    return result
