"""Institutional reference and DRAFT access notice, never an approved consent."""
POLITICA = {
    'codigo': 'GER-PO.085', 'version': '03', 'fechaAprobacion': '2025-08-23',
    'url': 'https://www.congente.coop/images/Balancesocial/GER-PO.085-Politica_tratamiento_datos_personales.pdf',
    'sha256': '76e019da0891ef0213d646898d81df02d314ba8844ed70ea028adf29c39cd86d',
}
BORRADOR = '''BORRADOR: pendiente de validacion institucional. No constituye un aviso aprobado.
La Cooperativa de Ahorro y Credito Congente es responsable del tratamiento.
Para acceder a CrediHoy Digital trataremos tu documento, primer apellido y fecha
de expedicion para cotejar tu identidad con los registros institucionales, enviar
un codigo al celular o correo ya registrado, autenticar tu acceso y permitirte
consultar exclusivamente tus solicitudes. Conservaremos evidencias de aceptacion,
autenticacion y seguridad para prevenir fraude y accesos no autorizados.
Intervienen los proveedores institucionales de mensajeria y proteccion antibots;
su participacion y las condiciones de conservacion deben validarse antes de habilitar.
No se solicitan datos biometricos ni datos sensibles para este cotejo. Las respuestas
sobre datos sensibles o de menores son facultativas; este acceso no los solicita.
Puedes conocer, actualizar y rectificar tus datos, solicitar prueba de autorizacion,
conocer su uso, presentar reclamos y solicitar revocatoria o supresion en los
terminos de la politica institucional y las obligaciones de conservacion aplicables.
Canal para consultas: servicio.asociado@congente.coop y oficinas de Congente.
Consulta la politica GER-PO.085 version 03 mediante el vinculo adjunto.
Esta autorizacion solo cubre acceso e identidad; no autoriza consultar centrales
de riesgo, firmar documentos, aprobar creditos, radicar o desembolsar.
Los plazos del acceso digital y de sus datos temporales estan pendientes de validacion.
'''
# Engineering proposals, not institutional retention decisions.
PROPUESTA_PLAZOS = {'sesionSegundos': 900, 'otpSegundos': 300,
    'limpiezaTemporalMaxSegundos': 60, 'ventanaSeguimientoDias': None,
    'retencionAuditoriaDias': None}


def validar_plazos(approved):
    """Pure configuration validator, also safe during Django settings import."""
    result = {}
    for key in ('sesionSegundos', 'otpSegundos', 'limpiezaTemporalMaxSegundos'):
        value = approved.get(key) if type(approved) is dict else None
        if type(value) is not int or not 0 < value <= PROPUESTA_PLAZOS[key]:
            raise ValueError('PLAZOS_ACCESO_INVALIDOS')
        result[key] = value
    return result


def plazos():
    """Configured and separately validated; proposals are not runtime defaults."""
    from django.conf import settings
    from .publico import AccesoDenegado
    try:return validar_plazos(getattr(settings, 'CREDIHOY_PUBLICO_VALIDACION', {}))
    except ValueError:raise AccesoDenegado from None


def validar_aviso():
    from hashlib import sha256
    from django.conf import settings
    from .publico import AccesoDenegado
    approval = getattr(settings, 'CREDIHOY_PUBLICO_VALIDACION', {})
    if not isinstance(approval, dict):
        raise AccesoDenegado
    plazos()
    if (approval.get('estado') != 'VALIDADO'
            or not approval.get('referenciaPrivacidad') or not approval.get('referenciaSeguridad')
            or approval.get('politicaSha256') != POLITICA['sha256']
            or approval.get('avisoVersion') != settings.CREDIHOY_PUBLICO_AVISO_VERSION
            or approval.get('avisoSha256') != sha256(settings.CREDIHOY_PUBLICO_AVISO_TEXTO.encode()).hexdigest()
            or settings.CREDIHOY_PUBLICO_AVISO_TEXTO == BORRADOR
            or type(approval.get('ventanaSeguimientoDias')) is not int
            or approval['ventanaSeguimientoDias'] != getattr(settings,'CREDIHOY_PUBLICO_RETENCION_DIAS',None)):
        raise AccesoDenegado


def validar_antibot():
    from django.conf import settings
    from .publico import AccesoDenegado
    # Official dummy keys are allowed only in isolated tests, never the HTTPS profile.
    for name in ('CREDIHOY_PUBLICO_BOT_SITEKEY', 'CREDIHOY_PUBLICO_BOT_SECRET'):
        value = getattr(settings, name, '')
        if value.startswith(('1x00000000000000000000', '2x00000000000000000000', '3x00000000000000000000')):
            raise AccesoDenegado
