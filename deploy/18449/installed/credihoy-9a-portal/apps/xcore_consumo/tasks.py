from celery import shared_task
from django.conf import settings

@shared_task(name='credihoy.preparar_acceso_publico', ignore_result=True, acks_late=False, autoretry_for=(), max_retries=0)
def preparar_acceso_publico(*, acceso_id):
    from .services.credihoy_v2.unico.publico import preparar
    return preparar(acceso_id)

@shared_task(name='credihoy.limpiar_accesos_publicos', ignore_result=True, acks_late=False, autoretry_for=(), max_retries=0)
def limpiar_accesos_publicos():
    from .services.credihoy_v2.unico.publico_controles import purgar_temporales
    from .services.credihoy_v2.unico.publico_privacidad import validar_aviso
    from .services.credihoy_v2.unico import publico_restringido as restringido
    if settings.ROOT_URLCONF != 'core.urls_credihoy_publico':
        return 'PERFIL_NO_AUTORIZADO'
    if restringido.activo():
        restringido.validar()
    else:
        validar_aviso()
    return purgar_temporales(actor='servicio-9a', referencia=settings.CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA)
