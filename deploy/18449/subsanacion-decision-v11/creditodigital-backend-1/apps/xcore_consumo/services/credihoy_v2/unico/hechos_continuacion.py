"""Existing institutional adapters, with an encrypted source receipt before HDC."""

from dataclasses import asdict, dataclass
from datetime import datetime
import re
from .reglas import HechosReglasUnicoV2

from dataclasses import asdict
import hashlib
import json

from django.db import transaction
from django.utils import timezone

from apps.utils.models import AuditLog
from apps.xcore_consumo.models import SolicitudConsumo
from apps.xcore_consumo.services.otp_crypto import encrypt_text
from ..cliente import ApiDataCrediHoyV2Client, ConfiguracionApiDataV2
from .normativa import ingreso_computable_congente
from .proyeccion_interna import proyectar_reglas_internas
from ..base import Hecho


def obtener(case, request_id, *, con_contexto=False):
    if type(con_contexto) is not bool:
        raise ValueError('CONTEXTO_CONTINUACION_INVALIDO')
    if transaction.get_connection().in_atomic_block:
        raise ValueError('HECHOS_EXTERNOS_REQUIEREN_AUTOCOMMIT')
    fecha = timezone.now().replace(microsecond=0)
    client = ApiDataCrediHoyV2Client(ConfiguracionApiDataV2.desde_settings())
    facts = client.consultar(numero_documento=case.solicitante.numero_identificacion, fecha_evaluacion=fecha, request_id=str(request_id))
    rules = proyectar_reglas_internas(facts, fecha=fecha)
    income = ingreso_computable_congente(facts.financieros)
    estimate = None
    if con_contexto:
        from django.core.exceptions import ObjectDoesNotExist
        from .simulacion_lectura import estimar_cuota_expediente
        try:
            estimate = estimar_cuota_expediente(case, facts, fecha=fecha)
        except (ValueError, ObjectDoesNotExist) as exc:
            code = str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{1,95}', str(exc)) else type(exc).__name__
            estimate = Hecho(fuente='SIMULADOR_INSTITUCIONAL_CREDIHOY', observacion='CUOTA_ESTIMADA_NO_RECUPERADA:' + code)
    raw = json.dumps(asdict(facts), sort_keys=True, default=str, separators=(',', ':'), ensure_ascii=True).encode()
    receipt = {'version': 1, 'fuente': 'API_DATA_HECHOS_INTERNOS_V2', 'fechaEvaluacion': fecha.isoformat(), 'requestId': str(request_id), 'sha256': hashlib.sha256(raw).hexdigest(), 'hechosCifrados': encrypt_text(raw.decode()), 'ingresoDisponible': income.disponible}
    if estimate is not None:
        receipt['cuotaEstimada'] = estimate.a_dict()
    with transaction.atomic(durable=True):
        SolicitudConsumo.objects.select_for_update().get(solicitud=case)
        AuditLog.objects.create(solicitud=case, event='credihoy_hechos_continuacion_v1', request_id=str(request_id), actor='servicio:continuacion', payload=receipt)
    payroll = facts.financieros.otros_descuentos_nomina if facts.financieros is not None else Hecho(fuente='LINIX.AP014MCLIENTE.V_DTONOM', observacion='DESGLOSE_NOMINA_NO_DISPONIBLE')
    if con_contexto:
        return ContextoHechosContinuacion(ingresos=income, reglas=rules, descuentos_nomina=payroll, cuota_estimada=estimate, fecha_evaluacion=fecha)
    return (income, rules, payroll)


@dataclass(frozen=True, kw_only=True)
class ContextoHechosContinuacion:
    ingresos: Hecho
    reglas: HechosReglasUnicoV2
    descuentos_nomina: Hecho
    cuota_estimada: Hecho
    fecha_evaluacion: datetime | None = None
