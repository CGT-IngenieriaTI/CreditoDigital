"""Public identity boundary. Never creates a credit request or staff identity."""
import hmac
import json
import re
import secrets
from hashlib import sha256
from datetime import date, timedelta
from uuid import UUID
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from apps.utils.models import AuditLog
from apps.xcore_consumo.public_access_models import AccesoPublicoCredihoy as Acceso
from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text
from . import publico_proveedores as providers
from . import publico_controles as controles
from .publico_controles import AccesoDenegado, limite
from . import publico_privacidad as privacidad

NAVEGADOR = 'credihoy_publico_navegador'
GRANT = 'credihoy_publico_acceso'
VERSION = 'credihoy-acceso-publico-v1'


def habilitado():
    from . import publico_restringido as restringido
    if getattr(settings, 'CREDIHOY_PUBLICO_ENABLED', False) is not True:
        raise AccesoDenegado
    if (settings.DEBUG is not False or settings.SESSION_COOKIE_NAME != 'credihoy_publico_session'
            or settings.ROOT_URLCONF != 'core.urls_credihoy_publico'
            or settings.CSRF_COOKIE_NAME != 'credihoy_publico_csrf'
            or settings.SESSION_COOKIE_SECURE is not True
            or settings.SESSION_COOKIE_HTTPONLY is not True
            or settings.SESSION_COOKIE_SAMESITE != 'Strict'
            or settings.SESSION_COOKIE_DOMAIN is not None or settings.CSRF_COOKIE_DOMAIN is not None
            or settings.CSRF_COOKIE_SECURE is not True or settings.CSRF_COOKIE_HTTPONLY is not True
            or settings.CSRF_COOKIE_SAMESITE != 'Strict'
            or settings.SESSION_ENGINE != 'django.contrib.sessions.backends.db'
            or getattr(settings, 'OTP_PROVIDER_MODE', None) != 'real'
            or any(not getattr(settings, k, '') for k in ('CREDIHOY_PUBLICO_AVISO_VERSION',
                'CREDIHOY_PUBLICO_AVISO_TEXTO','CREDIHOY_PUBLICO_HOST'))
            or (not restringido.activo() and any(not getattr(settings,k,'') for k in (
                'CREDIHOY_PUBLICO_BOT_SITEKEY','CREDIHOY_PUBLICO_BOT_SECRET')))):
        raise AccesoDenegado
    # Access OTP must not share a verification service with consent/signature OTP.
    sid=getattr(settings,'CREDIHOY_PUBLICO_VERIFY_SID','')
    if (type(sid) is not str or not re.fullmatch(r'VA[a-fA-F0-9]{32}',sid)
            or sid==getattr(settings,'TWILIO_VERIFY_SID',None)):
        raise AccesoDenegado
    controles.validar_habilitacion()
    if restringido.activo():
        restringido.validar()
    else:
        privacidad.validar_aviso()
        privacidad.validar_antibot()


def huella(uso, valor):
    return salted_hmac('credihoy-publico:'+uso, valor, algorithm='sha256').hexdigest()


def navegador(request):
    token = request.session.get(NAVEGADOR)
    if type(token) is not str or len(token) < 40:
        raise AccesoDenegado
    return huella('navegador', token)


def inicializar(request):
    habilitado()
    if not request.session.get(NAVEGADOR):
        request.session[NAVEGADOR] = secrets.token_urlsafe(32)
        request.session.set_expiry(privacidad.plazos()['sesionSegundos'])
    result = {'versionContrato': VERSION, 'avisoVersion':settings.CREDIHOY_PUBLICO_AVISO_VERSION,
        'avisoTexto':settings.CREDIHOY_PUBLICO_AVISO_TEXTO,
        'politica':dict(privacidad.POLITICA),
        'botSitekey':settings.CREDIHOY_PUBLICO_BOT_SITEKEY,
        'canales':list(getattr(settings,'CREDIHOY_PUBLICO_CANALES',('SMS',))),
        'duracionSesionSegundos':privacidad.plazos()['sesionSegundos'],
        'duracionOtpSegundos':privacidad.plazos()['otpSegundos']}
    from . import publico_restringido as restringido
    if restringido.activo():
        result.update(proteccion='OPERADOR_RESTRINGIDO', controlAcceso=restringido.emitir(request))
    return result


def auditar(a, evento, **extra):
    if getattr(settings, 'CREDIHOY_PUBLICO_AUDITORIA_AISLADA', False):
        from apps.xcore_consumo.public_access_models import EventoAccesoCredihoy
        EventoAccesoCredihoy.objects.using(a._state.db or 'default').create(
            acceso_id=a.pk, evento=evento, datos=extra)
        return
    AuditLog.objects.create(event='credihoy_publico_'+evento, actor='asociado:'+a.identidad_hmac[:24],
        request_id=str(a.pk), payload={'acceso':str(a.pk), **extra})


def iniciar(request, payload, request_id):
    habilitado()
    rid=UUID(request_id); browser=navegador(request)
    keys={'numeroDocumento','primerApellido','fechaExpedicion','canal','avisoVersion','aceptaTratamiento','botToken'}
    if (type(payload) is not dict or set(payload)!=keys
            or type(payload['numeroDocumento']) is not str or not re.fullmatch(r'[0-9]{6,15}',payload['numeroDocumento'])
            or type(payload['primerApellido']) is not str or not 1<=len(payload['primerApellido'].strip())<=80
            or type(payload['fechaExpedicion']) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',payload['fechaExpedicion'])
            or payload['canal'] not in getattr(settings,'CREDIHOY_PUBLICO_CANALES',('SMS',))
            or payload['avisoVersion']!=settings.CREDIHOY_PUBLICO_AVISO_VERSION
            or payload['aceptaTratamiento'] is not True or type(payload['botToken']) is not str
            or not 1<=len(payload['botToken'])<=2048):
        raise ValueError('DATOS_ACCESO_INVALIDOS')
    issued=date.fromisoformat(payload['fechaExpedicion'])
    if issued>timezone.localdate():raise ValueError('DATOS_ACCESO_INVALIDOS')
    identity=huella('identidad','CC:'+payload['numeroDocumento'])
    digest=huella('peticion',json.dumps(payload,sort_keys=True))
    reply={'versionContrato':VERSION,'intento':str(rid),'estado':'VERIFICACION_SOLICITADA'}
    # Same public response for absent, mismatched, rate-limited or unknown outcomes.
    if not controles.identidad_permitida(identity):return reply
    if Acceso.objects.filter(pk=rid).exists():return reply
    if not limite([('global','acceso',500),('ip',controles.ip_cliente(request),20),('identidad',identity,3)]):return reply
    with transaction.atomic():
        a,created=Acceso.objects.get_or_create(pk=rid,defaults={'navegador_hmac':browser,
            'identidad_hmac':identity,'peticion_hmac':digest,'datos_cifrados':encrypt_text(json.dumps(payload)),
            'expira_at':timezone.now()+timedelta(seconds=privacidad.plazos()['otpSegundos'])})
        if created:
            auditar(a,'solicitado',avisoVersion=payload['avisoVersion'],
                avisoSha256=sha256(settings.CREDIHOY_PUBLICO_AVISO_TEXTO.encode('utf-8')).hexdigest(),
                politica=dict(privacidad.POLITICA),aceptacionExpresa=True,
                validacion=dict(settings.CREDIHOY_PUBLICO_VALIDACION))
            from apps.xcore_consumo.tasks import preparar_acceso_publico
            def enqueue():
                try:preparar_acceso_publico.apply_async(kwargs={'acceso_id':str(a.pk)},queue='credihoy_publico',retry=False)
                except Exception:
                    Acceso.objects.filter(pk=a.pk,estado='PENDIENTE').update(estado='ERROR',
                        datos_cifrados='',motivo_interno='COLA_NO_DISPONIBLE')
            transaction.on_commit(enqueue)
    return reply


def preparar(acceso_id):
    habilitado()
    with transaction.atomic():
        a=Acceso.objects.select_for_update().get(pk=acceso_id)
        if a.estado=='ENVIO_SOLICITADO':return _enviar_codigo(a)
        if a.estado!='PENDIENTE' or a.revocado or a.expira_at<=timezone.now():return 'NO_EJECUTADO'
        if not controles.identidad_permitida(a.identidad_hmac):return 'NO_EJECUTADO'
        a.estado='PROCESANDO';a.save(update_fields=['estado','updated_at'])
        packet=json.loads(decrypt_text(a.datos_cifrados))
    try:
        if packet.get('avisoVersion')!=settings.CREDIHOY_PUBLICO_AVISO_VERSION:
            raise ValueError('AVISO_REQUIERE_NUEVA_ACEPTACION')
        if not hmac.compare_digest(a.peticion_hmac,huella('peticion',json.dumps(packet,sort_keys=True))):
            raise ValueError('PETICION_NO_VINCULADA')
        if not providers.verificar_bot(packet['botToken'],str(a.pk)):
            raise ValueError('BOT_NO_VERIFICADO')
        data=providers.cotejar(packet,str(a.pk))
        if huella('identidad','CC:'+data['numeroDocumento'])!=a.identidad_hmac:
            raise ValueError('IDENTIDAD_NO_VINCULADA')
        habilitado()
        a.refresh_from_db()
        if a.revocado or a.expira_at<=timezone.now() or not controles.identidad_permitida(a.identidad_hmac):
            raise ValueError('ACCESO_NO_VIGENTE')
        with transaction.atomic():
            a=Acceso.objects.select_for_update().get(pk=acceso_id)
            if a.revocado or a.expira_at<=timezone.now():raise ValueError('ACCESO_EXPIRADO')
            a.datos_cifrados=encrypt_text(json.dumps(data));a.estado='IDENTIFICADO'
            a.save(update_fields=['datos_cifrados','estado','updated_at'])
            auditar(a,'contacto_cotejado',fuente=data['fuente'])
        return 'IDENTIFICADO'
    except Exception:
        Acceso.objects.filter(pk=acceso_id).update(estado='ERROR',datos_cifrados='',motivo_interno='VERIFICACION_NO_COMPLETADA')
        auditar(a,'no_completado')
        return 'NO_COMPLETADO'


def _enviar_codigo(a):
    # Invoked with the access row locked; persist the claim BEFORE institutional IO.
    if a.revocado or a.expira_at<=timezone.now():return 'NO_EJECUTADO'
    if not controles.identidad_permitida(a.identidad_hmac):return 'NO_EJECUTADO'
    a.estado='ENVIANDO';a.save(update_fields=['estado','updated_at'])
    transaction.on_commit(lambda: _entregar_codigo(str(a.pk)))
    return 'ENVIO_PROCESADO'


def _entregar_codigo(key):
    a=Acceso.objects.get(pk=key)
    try:
        habilitado()
        if a.estado!='ENVIANDO' or a.revocado or a.expira_at<=timezone.now():return 'NO_EJECUTADO'
        data=json.loads(decrypt_text(a.datos_cifrados))
        if huella('identidad','CC:'+data['numeroDocumento'])!=a.identidad_hmac:raise AccesoDenegado
        if not limite([('contacto',huella('contacto',data['contacto']),3)]):raise AccesoDenegado
        sent=providers.enviar(data['contacto'],data['canal'],acceso_id=str(a.pk))
        if sent.status!='pending' or not re.fullmatch(r'VE[a-fA-F0-9]{32}',sent.sid or ''):
            raise ValueError('ENVIO_NO_CONFIRMADO')
        with transaction.atomic():
            a=Acceso.objects.select_for_update().get(pk=key)
            if a.revocado or a.expira_at<=timezone.now():raise AccesoDenegado
            a.verification_sid=sent.sid;a.estado='ENVIADO'
            a.save(update_fields=['verification_sid','estado','updated_at'])
            auditar(a,'otp_enviado',fuente=data['fuente'])
        return 'ENVIADO'
    except Exception:
        Acceso.objects.filter(pk=key).update(estado='ERROR',datos_cifrados='',motivo_interno='ENVIO_OTP_NO_CONFIRMADO')
        auditar(a,'no_completado')
        return 'NO_COMPLETADO'


def _intento_propio(request, key):
    try:a=Acceso.objects.get(pk=UUID(key))
    except (Acceso.DoesNotExist,ValueError,TypeError,AttributeError):raise AccesoDenegado from None
    if (a.revocado or a.expira_at<=timezone.now() or a.estado=='VERIFICADO'
            or not hmac.compare_digest(a.navegador_hmac,navegador(request))
            or not controles.identidad_permitida(a.identidad_hmac)):raise AccesoDenegado
    return a


def estado_intento(request, key):
    habilitado()
    reply={'versionContrato':VERSION,'intento':key,'estado':'PREPARANDO','contactoEnmascarado':None,'canal':None,'expiraAt':None}
    try:a=_intento_propio(request,key)
    except AccesoDenegado:return reply
    if a.estado in {'IDENTIFICADO','ENVIO_SOLICITADO','ENVIANDO','ENVIADO'}:
        data=json.loads(decrypt_text(a.datos_cifrados))
        contact=data['contacto']
        # Do not reveal a destination, name or account before a successful cotejo.
        mask='***'+contact[-2:] if data['canal']=='SMS' else '***@***'
        reply.update(estado='CONTACTO_DISPONIBLE' if a.estado=='IDENTIFICADO' else
            'CODIGO_ENVIADO' if a.estado=='ENVIADO' else 'ENVIO_PENDIENTE',
            contactoEnmascarado=mask,canal=data['canal'],expiraAt=a.expira_at.isoformat())
    return reply


def confirmar_envio(request, payload):
    habilitado()
    if type(payload) is not dict or set(payload)!={'intento','canal'}:raise ValueError
    with transaction.atomic():
        a=_intento_propio(request,payload['intento'])
        a=Acceso.objects.select_for_update().get(pk=a.pk)
        if a.revocado or a.expira_at<=timezone.now():raise AccesoDenegado
        data=json.loads(decrypt_text(a.datos_cifrados))
        if payload['canal']!=data.get('canal'):raise AccesoDenegado
        if a.estado=='IDENTIFICADO' and not a.revocado and a.expira_at>timezone.now():
            a.estado='ENVIO_SOLICITADO'
            a.expira_at=timezone.now()+timedelta(seconds=privacidad.plazos()['otpSegundos'])
            a.save(update_fields=['estado','expira_at','updated_at'])
            auditar(a,'envio_confirmado',canal=data['canal'])
            from apps.xcore_consumo.tasks import preparar_acceso_publico
            def enqueue():
                try:preparar_acceso_publico.apply_async(kwargs={'acceso_id':str(a.pk)},queue='credihoy_publico',retry=False)
                except Exception:
                    Acceso.objects.filter(pk=a.pk,estado='ENVIO_SOLICITADO').update(estado='ERROR',datos_cifrados='',motivo_interno='COLA_NO_DISPONIBLE')
            transaction.on_commit(enqueue)
    return {'versionContrato':VERSION,'intento':str(a.pk),'estado':'ENVIO_SOLICITADO'}


def cancelar_intento(request, payload):
    habilitado()
    if type(payload) is not dict or set(payload)!={'intento','cancelar'} or payload['cancelar'] is not True:raise ValueError
    a=_intento_propio(request,payload['intento'])
    controles.revocar(a.pk,actor='titular',referencia='NUEVO_COTEJO_SOLICITADO')
    return {'versionContrato':VERSION,'intento':str(a.pk),'estado':'CANCELADO'}


def verificar(request, payload):
    habilitado(); browser=navegador(request)
    if (type(payload) is not dict or set(payload)!={'intento','codigo'}
            or type(payload['codigo']) is not str or not re.fullmatch(r'[0-9]{6}',payload['codigo'])):
        raise AccesoDenegado
    if not limite([('verificar_ip',controles.ip_cliente(request),30)]):raise AccesoDenegado
    with transaction.atomic():
        try:a=Acceso.objects.select_for_update().get(pk=UUID(payload['intento']))
        except (Acceso.DoesNotExist,ValueError,TypeError,AttributeError):raise AccesoDenegado from None
        if (not hmac.compare_digest(a.navegador_hmac,browser) or a.revocado or a.expira_at<=timezone.now()
                or a.estado!='ENVIADO' or a.intentos>=5
                or not controles.identidad_permitida(a.identidad_hmac)):raise AccesoDenegado
        a.intentos+=1;a.estado='VERIFICANDO';a.save(update_fields=['intentos','estado','updated_at'])
        packet=json.loads(decrypt_text(a.datos_cifrados))
        if huella('identidad','CC:'+packet['numeroDocumento'])!=a.identidad_hmac:raise AccesoDenegado
    try:checked=providers.comprobar(packet['contacto'],payload['codigo'],a.verification_sid,
        acceso_id=str(a.pk),numero_intento=a.intentos,canal=packet['canal'])
    except Exception:
        Acceso.objects.filter(pk=a.pk).update(estado='ERROR',motivo_interno='RESULTADO_OTP_INCIERTO')
        raise AccesoDenegado from None
    with transaction.atomic():
        a=Acceso.objects.select_for_update().get(pk=a.pk)
        approved=(checked.status=='approved' and checked.sid==a.verification_sid
                and checked.to==packet['contacto'] and checked.channel==('sms' if packet['canal']=='SMS' else 'email')
                and not a.revocado and a.expira_at>timezone.now())
        if not approved:
            a.estado='BLOQUEADO' if a.intentos>=5 else 'ENVIADO';a.save(update_fields=['estado','updated_at'])
            auditar(a,'otp_no_validado')
    if not approved:raise AccesoDenegado
    with transaction.atomic():
        a=Acceso.objects.select_for_update().get(pk=a.pk)
        if a.revocado or a.expira_at<=timezone.now() or a.estado!='VERIFICANDO':raise AccesoDenegado
        # A fresh, public-only cookie is the grant; no Django user is logged in.
        request.session.cycle_key();request.session[NAVEGADOR]=secrets.token_urlsafe(32)
        ttl=privacidad.plazos()['sesionSegundos']
        request.session[GRANT]=str(a.pk);request.session.set_expiry(ttl)
        a.navegador_hmac=navegador(request);a.estado='VERIFICADO';a.verification_check_sid=checked.sid
        a.verificado_at=timezone.now();a.sesion_expira_at=a.verificado_at+timedelta(seconds=ttl)
        a.save(update_fields=['navegador_hmac','estado','verification_check_sid','verificado_at','sesion_expira_at','updated_at'])
        auditar(a,'identidad_verificada')
    return {'versionContrato':VERSION,'autenticado':True}


def acceso(request):
    habilitado()
    try:
        a=Acceso.objects.get(pk=UUID(request.session[GRANT]),estado='VERIFICADO',revocado=False,
            sesion_expira_at__gt=timezone.now())
        if not hmac.compare_digest(a.navegador_hmac,navegador(request)):raise AccesoDenegado
        if not controles.identidad_permitida(a.identidad_hmac):raise AccesoDenegado
        data=json.loads(decrypt_text(a.datos_cifrados))
        if huella('identidad','CC:'+data['numeroDocumento'])!=a.identidad_hmac:raise AccesoDenegado
        return a,data
    except (Acceso.DoesNotExist,KeyError,ValueError,TypeError):raise AccesoDenegado from None


def cerrar(request):
    a,_=acceso(request)
    if request.headers.get('X-Acceso-Referencia')!=str(a.pk):raise AccesoDenegado
    controles.revocar(a.pk,actor='titular',referencia='CIERRE_SESION')
    auditar(a,'sesion_cerrada');request.session.flush()
