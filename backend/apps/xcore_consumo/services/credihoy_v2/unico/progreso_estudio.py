"""Owner-filtered study state. A read never schedules work or calls a provider."""
from datetime import datetime

VERSION='credihoy-progreso-estudio-v1'
STATES={'AUTORIZACION_CONFIRMADA','VALIDACION_ADICIONAL','RESULTADO_DISPONIBLE'}


def project(value):
    if (type(value) is not dict or set(value)!={'version','estado','confirmadoEn','observadoEn','autorizacionVigente','etapaActiva','etapasCompletadas'}
            or value['version']!=VERSION or value['estado'] not in STATES
            or type(value['autorizacionVigente']) is not bool or value['etapaActiva'] is not None
            or value['etapasCompletadas']!=['AUTORIZACION']):
        raise ValueError('PROGRESO_ESTUDIO_NO_ACREDITADO')
    confirmed=datetime.fromisoformat(value['confirmadoEn']);observed=datetime.fromisoformat(value['observadoEn'])
    if confirmed.utcoffset() is None or observed.utcoffset() is None or confirmed>observed:
        raise ValueError('PROGRESO_ESTUDIO_FECHAS_INVALIDAS')
    return dict(value)


def read(case,alias,study=None):
    from django.utils import timezone
    from apps.xcore_consumo.models import SolicitudConsumo,ConsentimientoConsumo
    from . import centrales_personal as central
    detail=SolicitudConsumo.objects.using(alias).filter(solicitud=case).first()
    if detail is None:return None
    journal=(detail.orchestration_data or {}).get(central.KEY,{})
    if journal.get('estado')!='OTP_CONFIRMADA':return None
    # Bind ORM relations only within the authorization connection's alias.
    if case._state.db != detail._state.db:
        case=type(case).objects.using(alias).select_related('solicitante').get(pk=case.pk)
    case.consumo_detail=detail
    consent=ConsentimientoConsumo.objects.using(alias).get(solicitud=case)
    now=timezone.now()
    # A technical capture deadline is not a documentary-purpose deadline.
    central._verificar_comprobante(case,consent,journal,vigente=False,owner=True)
    proof=journal['comprobante']['payload']
    from .consentimiento_uso import evaluar,validar_consumo
    try:
        evaluar(case,consent,owner=True)
        valid=True
    except (ValueError,KeyError,TypeError,AttributeError):valid=False
    snapshot=(detail.orchestration_data or {}).get('credihoy_progreso_estudio_v1',{})
    state='RESULTADO_DISPONIBLE' if study is not None else 'VALIDACION_ADICIONAL' if (
        not valid or validar_consumo(case) or snapshot.get('estado')=='VALIDACION_ADICIONAL') else 'AUTORIZACION_CONFIRMADA'
    return project({'version':VERSION,'estado':state,'confirmadoEn':proof['confirmadoEn'],
        'observadoEn':now.isoformat(),'autorizacionVigente':valid,'etapaActiva':None,
        'etapasCompletadas':['AUTORIZACION']})
