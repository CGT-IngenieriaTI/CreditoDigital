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
    from .publico_privacidad import compensacion_configurada
    if compensacion_configurada():return restringido.comprobar_continuo(token,request_id)
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
    accepted = (p.get('success') is True and p.get('hostname') == settings.CREDIHOY_PUBLICO_HOST
                and p.get('action') == 'credihoy_acceso')
    if accepted and _grants_enabled():
        from incluirtec.services.identity_credential import record_preotp_challenge
        accepted = record_preotp_challenge(request_id)
    return accepted


def _apellido(text):
    return unicodedata.normalize('NFC', text).strip().casefold()


def cotejar(datos, request_id):
    _authorize_identity(access_id=request_id, request_id=request_id, document=datos['numeroDocumento'], purpose='PRE_OTP_MATCH', payload=datos)
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
        'primerNombre':_texto(raw,'primerNombre'),'segundoNombre':_texto(raw,'segundoNombre'),
        'segundoApellido':_texto(raw,'segundoApellido'),'celular':phone,'correo':_texto(raw,'email')}


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


def acreditar_firmante(grant, *, solicitud_id, pdf_sha256, clave, request_id, autoriza_firma, presentacion_contractual=False):
    """Relay-only identity attestation. No signature, SMS or authorization granted.

    Called after a browser-bound personal intent, never from browser name fields.
    Re-read canonical identity using the existing institutional identity client.
    The resulting short-lived assertion is specific to one document and intent.
    """
    import hashlib
    import re
    from uuid import UUID
    from django.core import signing
    from .publico_institucional_http import _identity, state_alias
    from apps.xcore_consumo.public_access_models import OperacionVerifyCredihoy
    from .publico_seguimiento import key
    for value in (solicitud_id, clave, request_id):
        if type(value) is not str or str(UUID(value)) != value:raise ValueError('INTENCION_FIRMA_INVALIDA')
    if (type(presentacion_contractual) is not bool
            or autoriza_firma is not (False if presentacion_contractual else True)
            or not re.fullmatch('[a-f0-9]{64}',pdf_sha256)):
        raise ValueError('ACTO_PERSONAL_FIRMA_REQUERIDO')

    def vigente():
        grant.refresh_from_db(using=state_alias())
        now=timezone.now()
        if (grant.estado!='VERIFICADO' or grant.revocado or not grant.verificado_at
                or not grant.sesion_expira_at or grant.sesion_expira_at<=now
                or not 0<=(now-grant.verificado_at).total_seconds()<=600
                or not grant.verification_sid or grant.verification_check_sid!=grant.verification_sid):
            raise ValueError('IDENTIDAD_FIRMA_REQUIERE_SESION_PERSONAL_RECIENTE')
        return _identity(grant)

    identity=vigente()
    approved=OperacionVerifyCredihoy.objects.using(state_alias()).filter(acceso_id=grant.pk,
        accion='COMPROBAR',estado='FINALIZADA',resultado__estadoProveedor='approved',
        resultado__verificationSid=grant.verification_sid).exists()
    if not approved:raise ValueError('VERIFICACION_PERSONAL_NO_ACREDITADA')
    _authorize_identity(access_id=str(grant.pk), request_id=request_id,
                        document=identity['numeroDocumento'], purpose='VERIFIED_IDENTITY')
    client=ApiDataCrediHoyV2Client(ConfiguracionApiDataV2.desde_settings())
    raw=client.consultar_socio(numero_documento=identity['numeroDocumento'],request_id=request_id)
    current=cotejar_respuesta(identity,raw)
    if current['contacto']!=identity['contacto']:raise ValueError('CONTACTO_VERIFICADO_CAMBIO')
    projected=proyectar_identidad(raw)
    names=' '.join(v for v in (projected['primerNombre'],projected['segundoNombre']) if v)
    surnames=' '.join(v for v in (projected['primerApellido'],projected['segundoApellido']) if v)
    if not names or not surnames or max(len(names),len(surnames))>120:
        raise ValueError('NOMBRE_INSTITUCIONAL_INCOMPLETO')
    if vigente()['numeroDocumento']!=identity['numeroDocumento']:raise ValueError('TITULAR_CAMBIO')
    proof={'version':'CONTRACTUAL_IDENTIDAD_INSTITUCIONAL_V1' if presentacion_contractual else 'COLO023_IDENTIDAD_INSTITUCIONAL_V1','fuente':'API_DATA.consultarSocio',
        'requestId':request_id,'consultadoEn':timezone.now().isoformat(),
        'sesionReferencia':str(grant.pk),'sesionExpiraAt':grant.sesion_expira_at.isoformat(),
        'verificadoEn':grant.verificado_at.isoformat(),
        'verificacionReferencia':hashlib.sha256(grant.verification_sid.encode()).hexdigest(),
        'solicitudId':solicitud_id,'pdfSha256':pdf_sha256,'operacionFirmaId':clave,
        'numeroDocumento':identity['numeroDocumento'],'names':names,'lastNames':surnames,
        'actoPersonalFirma':not presentacion_contractual,'OTP9AEsFirma':False}
    return signing.dumps(proof,key=key(),salt='credihoy-firmante-institucional-v1')


def comprobar(contacto, codigo, verification_sid, *, acceso_id=None, numero_intento=None, canal='SMS'):
    if getattr(settings, 'CREDIHOY_VERIFY_TRANSPORTE', 'SDK') == 'RELE_HTTP':
        from .publico_institucional import comprobar as relay
        return relay(acceso_id,numero_intento,codigo,contacto,canal,verification_sid)
    return TwilioVerifyClient(service_sid=settings.CREDIHOY_PUBLICO_VERIFY_SID).check_verification(
        contacto, codigo, verification_sid=verification_sid)


def _grants_enabled():
    value = getattr(settings, 'CREDIHOY_IDENTIDAD_GRANTS_ENABLED', False)
    if type(value) is not bool:
        raise ValueError('IDENTITY_GRANTS_CONFIGURATION_INVALID')
    return value


def _authorize_identity(*, access_id, request_id, document, purpose, payload=None):
    if _grants_enabled():
        from incluirtec.services.identity_credential import authorize_access_request
        if not authorize_access_request(access_id=access_id, request_id=request_id, document=document, purpose=purpose, payload=payload):
            raise ValueError('IDENTITY_GRANT_DENIED')
