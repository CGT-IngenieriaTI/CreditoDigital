"""Reuse institutional HTTP and OTP transports. No Oracle/public credentials."""
import hmac
import unicodedata
from datetime import datetime
from decimal import Decimal
import requests
from django.conf import settings
from django.utils import timezone
from apps.xcore_consumo.services.otp import normalize_sms_destination
from apps.xcore_consumo.services.twilio_verify import TwilioVerifyClient
from ..cliente import ApiDataCrediHoyV2Client, ConfiguracionApiDataV2
from .precarga import _texto, _entero_exacto


def verificar_bot(token, request_id):
    from . import publico_restringido as restringido
    if restringido.activo():
        return restringido.comprobar(token, request_id)
    # Tokens are single-use; hostname/action are checked, not just success.
    # https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
    with requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data={'secret': settings.CREDIHOY_PUBLICO_BOT_SECRET, 'response': token,
                  'idempotency_key': request_id}, timeout=5, allow_redirects=False) as r:
        if r.status_code != 200:
            return False
        p = r.json()
    return (p.get('success') is True and p.get('hostname') == settings.CREDIHOY_PUBLICO_HOST
            and p.get('action') == 'credihoy_acceso')


def _apellido(text):
    return unicodedata.normalize('NFC', text).strip().casefold()


def cotejar(datos, request_id):
    client = ApiDataCrediHoyV2Client(ConfiguracionApiDataV2.desde_settings())
    raw = client.consultar_socio(numero_documento=datos['numeroDocumento'], request_id=request_id)
    return cotejar_respuesta(datos, raw)


def proyectar_identidad(raw):
    """Read only the identity fields; do not depend on the pilot's legacy projection."""
    if type(raw) is not dict:raise ValueError('RESPUESTA_IDENTIDAD_INVALIDA')
    issued=_texto(raw,'fechaExpedicion')
    try:issued=datetime.strptime(issued,'%d/%m/%Y').date().isoformat() if issued else None
    except ValueError:raise ValueError('FECHA_INSTITUCIONAL_INVALIDA') from None
    if issued and issued>timezone.localdate().isoformat():raise ValueError('FECHA_INSTITUCIONAL_INVALIDA')
    phone=raw.get('movilTelefono')
    if type(phone) in (int,Decimal):phone=str(_entero_exacto(phone,'movilTelefono'))
    if phone is not None and (type(phone) is not str or len(phone)>32):raise ValueError('CONTACTO_INSTITUCIONAL_INVALIDO')
    return {'primerApellido':_texto(raw,'primerApellido'),'fechaExpedicion':issued,
        'primerNombre':_texto(raw,'primerNombre'),'celular':phone,'correo':_texto(raw,'email')}


def cotejar_respuesta(datos, raw):
    # Also used by the restricted operator check: one institutional read, no OTP.
    projected = proyectar_identidad(raw)
    surname, issued = projected['primerApellido'], projected['fechaExpedicion']
    if (not surname or not issued or not hmac.compare_digest(_apellido(surname).encode(),
            _apellido(datos['primerApellido']).encode()) or issued != datos['fechaExpedicion']):
        raise ValueError('IDENTIDAD_NO_COTEJADA')
    if datos['canal'] == 'SMS':
        contact = normalize_sms_destination(projected['celular'])
    else:
        from django.core.validators import validate_email
        contact = projected['correo']
        if not contact:
            raise ValueError('CONTACTO_NO_DISPONIBLE')
        validate_email(contact)
    return {'numeroDocumento': datos['numeroDocumento'], 'tipoDocumento': 'CC',
        'primerApellido': surname, 'fechaExpedicion': issued,
        'celular': projected['celular'], 'correo': projected['correo'],
        'nombre': projected['primerNombre'], 'contacto': contact, 'canal': datos['canal'],
        'fuente': 'API_DATA.consultarSocio', 'fechaConsulta': timezone.now().isoformat(),
        'avisoVersion': datos['avisoVersion']}


def enviar(contacto, canal, *, acceso_id=None):
    if getattr(settings, 'CREDIHOY_VERIFY_TRANSPORTE', 'SDK') == 'RELE_HTTP':
        from .publico_institucional import enviar as relay
        return relay(acceso_id)
    return TwilioVerifyClient(service_sid=settings.CREDIHOY_PUBLICO_VERIFY_SID).start_verification(
        contacto, channel='sms' if canal == 'SMS' else 'email')


def comprobar(contacto, codigo, verification_sid, *, acceso_id=None, numero_intento=None, canal='SMS'):
    if getattr(settings, 'CREDIHOY_VERIFY_TRANSPORTE', 'SDK') == 'RELE_HTTP':
        from .publico_institucional import comprobar as relay
        return relay(acceso_id,numero_intento,codigo,contacto,canal,verification_sid)
    return TwilioVerifyClient(service_sid=settings.CREDIHOY_PUBLICO_VERIFY_SID).check_verification(
        contacto, codigo, verification_sid=verification_sid)
