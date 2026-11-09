"""Public identity regression on disposable databases; institutional IO is mocked."""
import base64
import json
from hashlib import sha256
from io import StringIO
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command, CommandError
from django.test import TransactionTestCase,override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.solicitudes.models import Solicitud
from apps.xcore_consumo.public_access_models import AccesoPublicoCredihoy as Acceso
from apps.xcore_consumo.services.credihoy_v2.unico import publico as service
from apps.xcore_consumo.services.credihoy_v2.unico import publico_proveedores as providers
from apps.xcore_consumo.services.credihoy_v2.unico import publico_controles as controls
from apps.xcore_consumo.services.credihoy_v2.unico import publico_privacidad as privacy
from tools import test_credihoy_cotizacion as quote_fixture

BASE='/api/v1/credihoy/publico/'
SID='VE'+'a'*32


@override_settings(ROOT_URLCONF='core.urls_credihoy_publico',CREDIHOY_PUBLICO_ENABLED=True,DEBUG=False,
    OTP_AES_KEY_B64=base64.b64encode(b'a'*32).decode(),
    SESSION_COOKIE_NAME='credihoy_publico_session',CSRF_COOKIE_NAME='credihoy_publico_csrf',
    SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',
    CSRF_COOKIE_SECURE=True,CSRF_COOKIE_HTTPONLY=True,CSRF_COOKIE_SAMESITE='Strict',
    SESSION_ENGINE='django.contrib.sessions.backends.db',
    MIDDLEWARE=['django.contrib.sessions.middleware.SessionMiddleware','django.middleware.csrf.CsrfViewMiddleware'],
    CREDIHOY_PUBLICO_AVISO_VERSION='SYNTHETIC_ONLY',CREDIHOY_PUBLICO_AVISO_TEXTO='Synthetic notice, not legal policy.',
    CREDIHOY_PUBLICO_BOT_SITEKEY='synthetic',CREDIHOY_PUBLICO_BOT_SECRET='synthetic',
    CREDIHOY_PUBLICO_HOST='testserver',CREDIHOY_PUBLICO_CANALES=('SMS','EMAIL'),
    CREDIHOY_PUBLICO_RESTRINGIDO=True,CREDIHOY_PUBLICO_REDES=('127.0.0.1/32',),
    CREDIHOY_PUBLICO_PROXIES=(),CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA='SYNTHETIC_ONLY',
    CREDIHOY_PUBLICO_RETENCION_DIAS=30,OTP_PROVIDER_MODE='real',ALLOWED_HOSTS=['testserver'])
@override_settings(CREDIHOY_PUBLICO_VERIFY_SID='VA'+'b'*32,TWILIO_VERIFY_SID='VA'+'a'*32)
class PublicAccessTests(TransactionTestCase):
    def setUp(self):
        quote_fixture.QuoteFlowTests.setUp(self)
        restricted=override_settings(CREDIHOY_PUBLICO_IDENTIDADES_HMAC=(service.huella(
            'identidad','CC:'+self.s.solicitante.numero_identificacion),),
            CREDIHOY_PUBLICO_VALIDACION={'estado':'VALIDADO','referenciaPrivacidad':'SYNTHETIC',
                'referenciaSeguridad':'SYNTHETIC','politicaSha256':privacy.POLITICA['sha256'],
                'avisoVersion':'SYNTHETIC_ONLY','avisoSha256':sha256(b'Synthetic notice, not legal policy.').hexdigest(),
                'sesionSegundos':900,'otpSegundos':300,'limpiezaTemporalMaxSegundos':60,'ventanaSeguimientoDias':30},
            CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(timezone.now()+timedelta(hours=1)).isoformat())
        restricted.enable();self.addCleanup(restricted.disable)
        self.client=APIClient(enforce_csrf_checks=True)
        self.boot=self.client.get(BASE+'inicio/',secure=True).json()
        self.payload={'numeroDocumento':self.s.solicitante.numero_identificacion,'primerApellido':'PRUEBA',
            'fechaExpedicion':'2001-08-20','canal':'SMS','avisoVersion':'SYNTHETIC_ONLY',
            'aceptaTratamiento':True,'botToken':'synthetic-token'}
        self.raw={'primerNombre':'ASOCIADO SINTETICO','primerApellido':'PRUEBA','fechaExpedicion':'20/08/2001',
            'movilTelefono':'3000000000','email':'synthetic@example.invalid'}
        self.sent=SimpleNamespace(sid=SID,status='pending')
        self.checked=SimpleNamespace(sid=SID,status='approved',to='+573000000000',channel='sms')
        self.queue=patch('apps.xcore_consumo.tasks.preparar_acceso_publico.apply_async').start()
        self.original_bot=providers.verificar_bot
        self.bot=patch.object(providers,'verificar_bot',return_value=True).start()
        self.api=patch.object(providers.ApiDataCrediHoyV2Client,'consultar_socio',return_value=self.raw).start()
        patch.object(providers.ConfiguracionApiDataV2,'desde_settings',return_value=SimpleNamespace()).start()
        # Keep the existing HTTP client constructor contract; no real configuration is read.
        patch.object(providers,'ApiDataCrediHoyV2Client',return_value=SimpleNamespace(consultar_socio=self.api)).start()
        self.original_send=providers.enviar;self.original_check=providers.comprobar
        self.send=patch.object(providers,'enviar',return_value=self.sent).start()
        self.check=patch.object(providers,'comprobar',return_value=self.checked).start()
        self.addCleanup(patch.stopall)

    def post(self,path,payload,rid=None,client=None):
        return (client or self.client).post(BASE+path,payload,format='json',secure=True,
            HTTP_X_CSRFTOKEN=self.boot['csrfToken'],HTTP_ORIGIN='https://testserver',HTTP_X_REQUEST_ID=rid or str(uuid4()))

    def begin(self,**updates):
        rid=str(uuid4());r=self.post('acceso/',{**self.payload,**updates},rid)
        self.assertEqual(r.status_code,202,r.content)
        return rid,r.json()

    def login(self):
        rid,_=self.begin();self.entregar(rid)
        r=self.post('verificar/',{'intento':rid,'codigo':'123456'})
        self.assertEqual(r.status_code,200,r.content)
        self.client.credentials(HTTP_X_ACCESO_REFERENCIA=rid)
        return rid

    def entregar(self,rid,expected='ENVIADO'):
        self.assertEqual(service.preparar(rid),'IDENTIFICADO')
        self.assertEqual(self.post('acceso/',{'intento':rid,'canal':'SMS'}).status_code,202)
        self.assertEqual(service.preparar(rid),'ENVIO_PROCESADO')
        self.assertEqual(Acceso.objects.get(pk=rid).estado,expected)

    def test_complete_flow_rotates_cookie_without_staff_login_or_credit_creation(self):
        before=self.client.cookies['credihoy_publico_session'].value
        users=get_user_model().objects.count();requests=Solicitud.objects.count()
        rid=self.login()
        self.assertNotEqual(before,self.client.cookies['credihoy_publico_session'].value)
        self.assertNotIn('_auth_user_id',self.client.session)
        status=self.client.get(BASE+'sesion/',secure=True)
        self.assertTrue(status.json()['autenticado']);self.assertEqual(status.json()['nombre'],'ASOCIADO SINTETICO')
        self.assertEqual(Solicitud.objects.count(),requests);self.assertEqual(get_user_model().objects.count(),users)
        self.assertEqual(self.s.consentimiento_consumo.text_hash,self.firma['text_hash'])
        self.assertNotIn(self.payload['numeroDocumento'],status.content.decode())
        self.assertEqual(status.headers['Cache-Control'],'no-store, private')
        self.check.assert_called_once_with('+573000000000','123456',SID,
            acceso_id=rid,numero_intento=1,canal='SMS')

    @override_settings(CREDIHOY_COTIZACION_TITULAR_ENABLED=True)
    def test_quote_route_enforces_cookie_csrf_reference_and_revocation_before_relay(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        body={'solicitudId':str(self.s.pk),'operacion':'consultar'}
        with patch.object(wire,'call') as relay:
            self.assertEqual(self.post('cotizacion/',body).status_code,401)
            rid=self.login()
            self.assertEqual(self.client.post(BASE+'cotizacion/',body,format='json',secure=True).status_code,403)
            self.client.credentials(HTTP_X_ACCESO_REFERENCIA=str(uuid4()))
            self.assertEqual(self.post('cotizacion/',body).status_code,401)
            relay.assert_not_called()

            self.client.credentials(HTTP_X_ACCESO_REFERENCIA=rid)
            relay.return_value={'sesionReferencia':rid,'cotizacion':{
                'versionContrato':'credihoy-cotizacion-titular-v1','solicitudId':str(self.s.pk),
                'estado':'SIN_COTIZACION','cuota':None,'oferta':None,
                'aceptacion':{'aceptada':False,'fecha':None},'autorizaStart':False,'autorizaDesembolso':False}}
            self.assertEqual(self.post('cotizacion/',body).status_code,200)
            self.assertEqual(relay.call_args.args[1]['cotizacion'],body)
            self.assertEqual(self.client.get(BASE+'cotizacion/',secure=True).status_code,405)
            Acceso.objects.filter(pk=rid).update(revocado=True)
            relay.reset_mock()
            self.assertEqual(self.post('cotizacion/',body).status_code,401)
            relay.assert_not_called()

    @override_settings(CREDIHOY_COTIZACION_TITULAR_ENABLED=False)
    def test_published_closed_quote_route_never_transports_or_enables_simulation(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        self.login()
        count=Solicitud.objects.count()
        with patch.object(wire,'call') as relay:
            self.assertEqual(self.client.get(BASE+'cotizacion/',secure=True).status_code,405)
            self.assertEqual(self.post('cotizacion/',{'solicitudId':str(self.s.pk),'operacion':'cotizar','diaPago':10}).status_code,503)
            self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['acciones']['simular'])
            relay.assert_not_called()
        self.assertEqual(Solicitud.objects.count(),count)
        from apps.utils.models import AuditLog
        self.assertFalse(AuditLog.objects.filter(event='CREDIHOY_COTIZACION_SOLICITADA').exists())

    @override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=True,CREDIHOY_AUTOSERVICIO_DB_ALIAS='default',
        CREDIHOY_SEGUIMIENTO_ENABLED=False,CREDIHOY_TITULAR_ENABLED=True,CREDIHOY_INSTITUCIONAL_ENABLED=True,
        CREDIHOY_INSTITUCIONAL_SECRET='SYNTHETIC_RELAY_'+'r'*64,
        CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'o'*64,
        CREDIHOY_TITULAR_URL='https://owner.example.invalid',CREDIHOY_INSTITUCIONAL_URL='https://relay.example.invalid')
    def test_self_service_cookie_csrf_relay_owner_chain_has_no_staff_or_implicit_credit_actions(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_seguimiento as owner, publico_institucional as wire
        from apps.xcore_consumo.services.credihoy_v2.unico import progreso_privacidad as progress_notice
        from apps.solicitudes.models import ProgresoTemporalCredihoy
        import base64
        self.login()
        sends=self.send.call_count;queries=self.api.call_count;before=Solicitud.objects.count()
        scope=override_settings(CREDIHOY_AUTOSERVICIO_ALCANCE={
            'sesiones':[self.client.session[service.GRANT]],
            'hasta':(timezone.now()+timedelta(minutes=5)).isoformat()})
        scope.enable();self.addCleanup(scope.disable)
        users=get_user_model().objects.count();calls=[]
        with patch.object(wire,'seguimiento',side_effect=wire.NoDisponible):
            self.assertEqual(self.post('solicitudes/',{'operacion':'borrador','accion':'retomar'}).status_code,503)
        def transport(origin,path,body,rid,headers):
            config='core.urls_credihoy_titular' if path==owner.PATH else 'core.urls_credihoy_institucional'
            calls.append(path)
            client=APIClient()
            extra={'HTTP_'+name.upper().replace('-','_'):value for name,value in headers.items()
                   if name.lower() not in {'content-type','accept'}}
            with override_settings(ROOT_URLCONF=config):
                r=client.post(path,body,content_type='application/json',secure=True,**extra)
            if r.status_code!=200:raise wire.NoDisponible
            return r.json()
        data={'operacion':'borrador','accion':'guardar','clave':str(uuid4()),'operacionId':str(uuid4()),
              'version':0,'datos':{},'promotor':None}
        validation={'version':progress_notice.VERSION,'sha256':progress_notice.SHA256,'consistenciaPoliticaReferencia':'LOCAL_ONLY',
            'respaldoCustodiaReferencia':'LOCAL_ONLY','limpiezaMaxSegundos':60}
        with override_settings(CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[owner.document_hmac(self.payload['numeroDocumento'])],
                CREDIHOY_PROGRESO_VALIDACION=validation,CREDIHOY_PROGRESO_AES_KEY_B64=base64.b64encode(b'p'*32).decode()),\
                patch.object(owner,'claim_delegation',return_value=True),patch.object(wire,'request_packet',side_effect=transport):
            shown=self.post('solicitudes/',{'operacion':'borrador','accion':'retomar'})
            self.assertEqual(shown.status_code,200,shown.content)
            notice=shown.json()['avisoTemporal']
            data['aceptacion']={k:notice[k] for k in ('version','sha256','presentacion')}|{'acepta':True}
            r=self.post('solicitudes/',data)
            self.assertEqual(r.status_code,200,r.content)
            self.assertEqual(r.json()['borrador']['referencia'],data['clave'])
            self.assertEqual(self.post('solicitudes/',data).status_code,200)
            self.assertTrue(ProgresoTemporalCredihoy.objects.filter(pk=data['clave']).exists())
            self.assertEqual(Solicitud.objects.count(),before)
            self.assertNotIn('_auth_user_id',self.client.session)
            self.assertEqual(get_user_model().objects.count(),users)
            self.assertEqual(self.send.call_count,sends);self.assertEqual(self.api.call_count,queries)
            bad=APIClient(enforce_csrf_checks=True);bad.cookies=self.client.cookies
            self.assertEqual(bad.post(BASE+'solicitudes/',data,format='json',secure=True).status_code,403)
            grant=Acceso.objects.get(pk=self.client.session[service.GRANT])
            grant.revocado=True;grant.save(update_fields=['revocado'])
            count=len(calls)
            self.assertEqual(self.post('solicitudes/',data).status_code,401)
            self.assertEqual(len(calls),count)
    @override_settings(CREDIHOY_SEGUIMIENTO_ENABLED=False,CREDIHOY_PUBLICO_SOLICITUDES_POR_API=True)
    def test_authentication_logout_and_reload_do_not_require_tracking_source(self):
        self.login()
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional.seguimiento',
                side_effect=AssertionError('SEGUIMIENTO_NO_PERMITIDO')) as owner:
            session=self.client.get(BASE+'sesion/',secure=True)
            self.assertTrue(session.json()['autenticado'])
            self.assertFalse(session.json()['acciones']['seguimiento'])
            response=self.client.get(BASE+'solicitudes/',secure=True)
            self.assertEqual(response.status_code,503)
            self.assertNotIn('solicitudes',response.json())
            self.assertTrue(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])
            self.assertEqual(self.post('salir/',{}).status_code,204)
            self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])
            owner.assert_not_called()

    @override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=False,CREDIHOY_AUTORIZACION_TITULAR_ENABLED=True)
    def test_personal_authorization_keeps_cookie_csrf_and_revocation_without_enabling_capture(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        rid=self.login()
        body={'operacion':'autorizacion','accion':'consultar','solicitudId':str(self.s.pk)}
        packet={'sesionReferencia':rid,'autorizacion':{'versionContrato':'credihoy-autorizacion-titular-v1',
            'solicitudId':str(self.s.pk),'estado':'AUTORIZACION_AUSENTE','documento':None,
            'firmado':False,'consultasHabilitadas':False,'evaluacionHabilitada':False}}
        with patch.object(wire,'seguimiento',return_value=packet) as transport:
            self.assertEqual(self.post('solicitudes/',body).status_code,200)
            self.assertEqual(transport.call_args.kwargs['autorizacion'],body)
            self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['acciones']['solicitar'])
            csrf=APIClient(enforce_csrf_checks=True);csrf.cookies=self.client.cookies
            self.assertEqual(csrf.post(BASE+'solicitudes/',body,format='json',secure=True).status_code,403)
            count=transport.call_count
            Acceso.objects.filter(pk=rid).update(revocado=True)
            self.assertEqual(self.post('solicitudes/',body).status_code,401)
            self.assertEqual(transport.call_count,count)

    @override_settings(CREDIHOY_VERIFY_TRANSPORTE='RELE_HTTP',
        CREDIHOY_INSTITUCIONAL_ENABLED=True,CREDIHOY_INSTITUCIONAL_SECRET='SYNTHETIC_ONLY_'+'s'*64,
        CREDIHOY_INSTITUCIONAL_URL='https://institutional.example.invalid',
        CREDIHOY_PUBLICO_AUDITORIA_AISLADA=True,CREDIHOY_PUBLICO_SOLICITUDES_POR_API=True,
        CREDIHOY_SEGUIMIENTO_DB_ALIAS='default',CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE='SYNTHETIC_ONLY')
    def test_http_relay_login_followup_reload_and_logout_without_staff_session(self):
        import requests
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        routes=[]
        def transport(url,**kwargs):
            path=url.removeprefix('https://institutional.example.invalid');routes.append(path)
            with override_settings(ROOT_URLCONF='core.urls_credihoy_institucional'):
                result=APIClient().post(path,kwargs['data'],content_type='application/json',secure=True,
                    **{'HTTP_'+k.upper().replace('-','_'):v for k,v in kwargs['headers'].items()
                       if k.lower()!='content-type'})
            reply=requests.Response();reply.status_code=result.status_code;reply._content=result.content
            reply._content_consumed=True;reply.headers.update(result.headers)
            return reply
        self.send.side_effect=self.original_send;self.check.side_effect=self.original_check
        with override_settings(CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[str(self.s.pk)]}),\
                patch.object(wire.requests,'post',side_effect=transport),\
                patch('apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional_http.TwilioVerifyClient') as sdk,\
                patch('apps.xcore_consumo.services.credihoy_v2.unico.publico_http.simulacion_publica',return_value=None):
            sdk.return_value.start_verification.return_value=self.sent
            sdk.return_value.check_verification.side_effect=[
                SimpleNamespace(sid=SID,status='pending'),self.checked]
            before=self.client.cookies['credihoy_publico_session'].value
            rid,_=self.begin();self.entregar(rid)
            self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'000000'}).status_code,401)
            self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'123456'}).status_code,200)
            self.client.credentials(HTTP_X_ACCESO_REFERENCIA=rid)
            self.assertNotEqual(before,self.client.cookies['credihoy_publico_session'].value)
            self.assertNotIn('_auth_user_id',self.client.session)
            self.assertTrue(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])
            listing=self.client.get(BASE+'solicitudes/',secure=True)
            self.assertEqual(listing.status_code,200,listing.content)
            self.assertEqual([v['referencia'] for v in listing.json()['solicitudes']],[str(self.s.pk)])
            self.assertEqual(self.client.get(BASE+'solicitudes/'+str(uuid4())+'/',secure=True).status_code,404)
            self.assertEqual(self.client.get(BASE+'solicitudes/'+str(self.s.pk)+'/',secure=True).status_code,200)
            self.assertTrue(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])
            self.assertEqual(self.post('salir/',{}).status_code,204)
            self.assertEqual(self.client.get(BASE+'solicitudes/',secure=True).status_code,401)
            self.assertTrue(Acceso.objects.get(pk=rid).revocado)
            sdk.return_value.start_verification.assert_called_once()
            self.assertEqual(sdk.return_value.check_verification.call_count,2)
            self.assertIn(wire.BASE+'seguimiento/',routes)

    def test_no_personal_data_or_contact_before_identity_and_uniform_start(self):
        self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])
        self.assertEqual(self.client.get(BASE+'solicitudes/',secure=True).status_code,401)
        rid,result=self.begin(primerApellido='NO COINCIDE')
        self.assertEqual(service.preparar(rid),'NO_COMPLETADO');self.send.assert_not_called()
        self.assertEqual(set(result),{'versionContrato','intento','estado'})
        self.assertNotIn('PRUEBA',json.dumps(result))

    def test_csrf_cookie_and_origin_are_required_even_without_django_auth(self):
        for options in ({},{'HTTP_X_CSRFTOKEN':self.boot['csrfToken'],'HTTP_ORIGIN':'https://evil.invalid'}):
            r=self.client.post(BASE+'acceso/',self.payload,format='json',secure=True,**options)
            self.assertEqual(r.status_code,403)
        self.queue.assert_not_called();self.api.assert_not_called()

    def test_browser_cannot_override_contact_and_duplicate_does_not_send_twice(self):
        self.assertEqual(self.post('acceso/',{**self.payload,'celular':'3111111111'}).status_code,400)
        rid,_=self.begin();self.post('acceso/',self.payload,rid)
        self.assertEqual(self.queue.call_count,1)
        self.entregar(rid);self.assertEqual(service.preparar(rid),'NO_EJECUTADO')
        self.send.assert_called_once_with('+573000000000','SMS',acceso_id=rid)

    def test_wrong_otp_persists_attempts_and_lockout_without_retrying_send(self):
        rid,_=self.begin();self.entregar(rid);self.checked.status='pending'
        for n in range(5):
            self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'000000'}).status_code,401)
        row=Acceso.objects.get(pk=rid);self.assertEqual(row.intentos,5);self.assertEqual(row.estado,'BLOQUEADO')
        self.post('verificar/',{'intento':rid,'codigo':'123456'});self.assertEqual(self.check.call_count,5)
        self.send.assert_called_once()

    def test_other_browser_cannot_use_challenge_or_session(self):
        rid,_=self.begin();self.entregar(rid)
        other=APIClient(enforce_csrf_checks=True);boot=other.get(BASE+'inicio/',secure=True).json()
        r=other.post(BASE+'verificar/',{'intento':rid,'codigo':'123456'},format='json',secure=True,
            HTTP_X_CSRFTOKEN=boot['csrfToken'],HTTP_ORIGIN='https://testserver')
        self.assertEqual(r.status_code,401);self.check.assert_not_called()

    def test_wrong_provider_verification_identity_fails_closed(self):
        rid,_=self.begin();self.entregar(rid);self.checked.sid='VE'+'b'*32
        self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'123456'}).status_code,401)
        self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])

    def test_expiry_revocation_and_logout_invalidate_session(self):
        rid=self.login()
        Acceso.objects.filter(pk=rid).update(sesion_expira_at=timezone.now()-timedelta(seconds=1))
        self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])
        Acceso.objects.filter(pk=rid).update(sesion_expira_at=timezone.now()+timedelta(seconds=60),revocado=True)
        self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])
        Acceso.objects.filter(pk=rid).update(revocado=False)
        self.assertEqual(self.post('salir/',{}).status_code,204)
        self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])

    def test_limits_bot_failure_and_unknown_send_never_retry(self):
        rid,_=self.begin();self.bot.return_value=False
        service.preparar(rid);self.api.assert_not_called();self.send.assert_not_called()
        self.bot.return_value=True;self.send.side_effect=TimeoutError('DO NOT EXPOSE')
        rid,_=self.begin();self.entregar(rid,expected='ERROR');service.preparar(rid)
        self.assertEqual(self.send.call_count,1)
        self.begin();self.begin();self.assertEqual(self.queue.call_count,4)

    def test_owned_failed_attempt_is_not_preparing_and_does_not_expose_failure_or_identity(self):
        rid,_=self.begin();self.bot.return_value=False
        self.assertEqual(service.preparar(rid),'NO_COMPLETADO')
        row=Acceso.objects.get(pk=rid)
        self.assertEqual(row.motivo_interno,'BOT_NO_VERIFICADO');self.assertEqual(row.datos_cifrados,'')
        result=self.client.get(BASE+'acceso/',{'intento':rid},secure=True)
        self.assertEqual(result.status_code,400)
        self.assertNotIn('BOT_NO_VERIFICADO',result.content.decode())
        self.assertNotIn(self.payload['numeroDocumento'],result.content.decode())
        other=APIClient();other.get(BASE+'inicio/',secure=True)
        hidden=other.get(BASE+'acceso/',{'intento':rid},secure=True)
        self.assertEqual(hidden.status_code,200);self.assertEqual(hidden.json()['estado'],'PREPARANDO')
        self.send.assert_not_called();self.api.assert_not_called()

    def test_cotejo_failure_records_only_safe_code_and_never_requests_otp(self):
        rid,_=self.begin()
        self.api.side_effect=ValueError('IDENTIDAD_NO_COTEJADA')
        service.preparar(rid)
        row=Acceso.objects.get(pk=rid)
        self.assertEqual(row.motivo_interno,'IDENTIDAD_NO_COTEJADA')
        self.assertEqual(row.datos_cifrados,'');self.send.assert_not_called()

    def test_unknown_provider_exception_never_persists_its_private_message(self):
        rid,_=self.begin();self.api.side_effect=RuntimeError('PRIVATE_CONTACT_AND_TOKEN')
        service.preparar(rid)
        row=Acceso.objects.get(pk=rid)
        self.assertEqual(row.motivo_interno,'VERIFICACION_NO_COMPLETADA')
        self.assertEqual(row.datos_cifrados,'');self.send.assert_not_called()

    def test_public_routes_exclude_admin_otp_hdc_and_quote_endpoints(self):
        for path in ('/api/v1/auth/login/','/api/v1/credihoy/canal/',
                '/api/v1/credihoy/unico/solicitudes/'+str(self.s.pk)+'/cotizacion/',
                '/api/v1/consumo/solicitudes/','/api/internal/v1/hdc/consultar/'):
            self.assertEqual(self.client.get(path,secure=True).status_code,404)
        with override_settings(CREDIHOY_PUBLICO_ENABLED=False):
            self.assertEqual(self.client.get(BASE+'inicio/',secure=True).status_code,404)

    def test_tracking_filters_owner_and_hides_technical_details(self):
        self.login()
        from apps.usuarios.models import Solicitante
        person=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='99999999',
            primer_apellido='OTRO',fecha_expedicion='2001-08-20',celular='3000000001',email='other@example.invalid')
        other=Solicitud.objects.create(solicitante=person,producto=self.s.producto,monto_solicitado=900000,
            metadatos={'modo':'CONSULTA_REAL_CONTROLADA'})
        result={'resultado':{'reglas':[{'codigo':'UNICO_FILA_9','estadoEvaluacion':'NO_CUMPLE'},
            {'codigo':'UNICO_FILA_32','estadoEvaluacion':'PENDIENTE_POLITICA'}]}}
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.funcional.leer_estado_funcional',return_value=result):
            r=self.client.get(BASE+'solicitudes/',secure=True)
        self.assertEqual(r.status_code,200);self.assertEqual(len(r.json()['solicitudes']),1)
        text=r.content.decode();self.assertIn('Debes actualizar tus datos',text)
        for word in ('NO_CUMPLE','NO_AUTORIZADA','LINIX','Oracle','PENDIENTE_POLITICA','verification_sid'):
            self.assertNotIn(word,text)
        self.assertEqual(self.client.get(BASE+'solicitudes/'+str(uuid4())+'/',secure=True).status_code,404)
        self.assertEqual(self.client.get(BASE+'solicitudes/'+str(other.pk)+'/',secure=True).status_code,404)
        self.client.credentials(HTTP_X_ACCESO_REFERENCIA=str(uuid4()))
        self.assertEqual(self.client.get(BASE+'solicitudes/',secure=True).status_code,401)

    def test_staff_session_does_not_authenticate_public_identity(self):
        self.client.force_authenticate(self.user)
        self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])
        self.assertEqual(self.client.get(BASE+'solicitudes/',secure=True).status_code,401)

    def test_plain_http_or_consent_verification_service_cannot_enable_access(self):
        self.assertEqual(self.client.get(BASE+'inicio/').status_code,401)
        with override_settings(CREDIHOY_PUBLICO_VERIFY_SID='VA'+'a'*32):
            self.assertEqual(self.client.get(BASE+'inicio/',secure=True).status_code,401)
        self.queue.assert_not_called()

    def test_public_transport_uses_dedicated_service_and_exact_challenge_sid(self):
        # Do not contact Twilio: verify the real adapter's internal call contract.
        with patch.object(providers,'TwilioVerifyClient') as client:
            self.original_send('+573000000000','SMS')
            self.original_check('+573000000000','123456',SID)
            self.assertEqual(client.call_count,2)
            client.assert_called_with(service_sid='VA'+'b'*32)
            client.return_value.start_verification.assert_called_once_with('+573000000000',channel='sms')
            client.return_value.check_verification.assert_called_once_with('+573000000000','123456',verification_sid=SID)

    def test_shared_twilio_transport_preserves_legacy_default_contract(self):
        from unittest.mock import Mock
        from apps.xcore_consumo.services.twilio_verify import TwilioVerifyClient
        client=object.__new__(TwilioVerifyClient);client.verify_sid='VA'+'a'*32;client.client=Mock()
        check=client.client.verify.v2.services.return_value.verification_checks.create
        client.check_verification('+573000000000','123456')
        check.assert_called_once_with(to='+573000000000',code='123456');check.reset_mock()
        client.check_verification('+573000000000','123456',verification_sid=SID)
        check.assert_called_once_with(verification_sid=SID,code='123456')

    def test_unlisted_identity_gets_same_response_without_lookup_or_otp(self):
        rid,reply=self.begin(numeroDocumento='98765432')
        self.assertEqual(reply,{'versionContrato':service.VERSION,'intento':rid,'estado':'VERIFICACION_SOLICITADA'})
        self.assertFalse(Acceso.objects.filter(pk=rid).exists())
        self.queue.assert_not_called();self.api.assert_not_called();self.send.assert_not_called()

    def test_draft_unvalidated_notice_or_test_antibot_cannot_enable_https_access(self):
        for kw in ({'CREDIHOY_PUBLICO_VALIDACION':{}},
                {'CREDIHOY_PUBLICO_AVISO_TEXTO':privacy.BORRADOR},
                {'CREDIHOY_PUBLICO_BOT_SITEKEY':'1x00000000000000000000AA'},
                {'CREDIHOY_PUBLICO_BOT_SECRET':'1x0000000000000000000000000000000AA'}):
            with override_settings(**kw):
                self.assertEqual(self.client.get(BASE+'inicio/',secure=True).status_code,401)
        self.api.assert_not_called();self.send.assert_not_called()

    def test_acceptance_audit_records_policy_text_hash_and_explicit_action(self):
        from apps.utils.models import AuditLog
        rid,_=self.begin()
        audit=AuditLog.objects.get(event='credihoy_publico_solicitado',request_id=rid)
        self.assertEqual(audit.payload['politica'],privacy.POLITICA)
        self.assertTrue(audit.payload['aceptacionExpresa'])
        self.assertEqual(audit.payload['avisoSha256'],sha256(b'Synthetic notice, not legal policy.').hexdigest())
        self.assertIsNotNone(audit.created_at)

    def test_scheduled_cleanup_works_after_authorization_expires_without_provider_calls(self):
        from apps.xcore_consumo.tasks import limpiar_accesos_publicos
        rid,_=self.begin()
        Acceso.objects.filter(pk=rid).update(expira_at=timezone.now()-timedelta(seconds=1))
        with override_settings(CREDIHOY_PUBLICO_AUTORIZACION_HASTA='2001-01-01T00:00:00+00:00'):
            self.assertEqual(limpiar_accesos_publicos(),1)
        self.assertFalse(Acceso.objects.get(pk=rid).datos_cifrados)
        self.api.assert_not_called();self.send.assert_not_called()

    def test_bot_requires_success_exact_hostname_and_action(self):
        with patch.object(providers.requests,'post') as post:
            response=post.return_value.__enter__.return_value
            response.status_code=200
            for p,expected in [({'success':True,'hostname':'testserver','action':'credihoy_acceso'},True),
                    ({'success':True,'hostname':'evil.invalid','action':'credihoy_acceso'},False),
                    ({'success':True,'hostname':'testserver','action':'other'},False),
                    ({'success':False,'hostname':'testserver','action':'credihoy_acceso'},False)]:
                response.json.return_value=p
                self.assertIs(self.original_bot('synthetic',str(uuid4())),expected)
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
            self.assertEqual(post.call_args.kwargs['timeout'],5)

    def test_expired_or_unrestricted_authorization_fails_closed(self):
        for kw in ({'CREDIHOY_PUBLICO_AUTORIZACION_HASTA':'2001-01-01T00:00:00+00:00'},
                {'CREDIHOY_PUBLICO_AUTORIZACION_HASTA':'2099-01-01T00:00:00'},
                {'CREDIHOY_PUBLICO_RESTRINGIDO':False},
                {'CREDIHOY_PUBLICO_IDENTIDADES_HMAC':()},
                {'CREDIHOY_PUBLICO_REDES':('0.0.0.0/0',)},
                {'CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA':''}):
            with override_settings(**kw):
                self.assertEqual(self.client.get(BASE+'inicio/',secure=True).status_code,401)
        self.queue.assert_not_called()

    def test_proxy_headers_are_not_trusted_from_arbitrary_peers(self):
        for kw in ({'REMOTE_ADDR':'203.0.113.4'},
                {'HTTP_X_FORWARDED_FOR':'127.0.0.1'},
                {'HTTP_X_FORWARDED_PROTO':'https'}, {'HTTP_X_REAL_IP':'127.0.0.1'}):
            self.assertEqual(self.client.get(BASE+'inicio/',secure=True,**kw).status_code,401)
        with override_settings(CREDIHOY_PUBLICO_PROXIES=('192.0.2.1/32',)):
            options={'REMOTE_ADDR':'192.0.2.1','HTTP_X_FORWARDED_FOR':'127.0.0.1',
                'HTTP_X_FORWARDED_PROTO':'https'}
            self.assertEqual(self.client.get(BASE+'inicio/',secure=True,**options).status_code,200)
            options['HTTP_X_FORWARDED_FOR']='127.0.0.1, 203.0.113.4'
            self.assertEqual(self.client.get(BASE+'inicio/',secure=True,**options).status_code,401)

    def test_removal_from_allowlist_cancels_queued_access_and_existing_grant(self):
        rid,_=self.begin()
        with override_settings(CREDIHOY_PUBLICO_IDENTIDADES_HMAC=('a'*64,)):
            self.assertEqual(service.preparar(rid),'NO_EJECUTADO')
        self.send.assert_not_called()
        rid=self.login()
        with override_settings(CREDIHOY_PUBLICO_IDENTIDADES_HMAC=('b'*64,)):
            self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])

    def test_revocation_invalidates_pending_code_without_provider_check(self):
        rid,_=self.begin();self.entregar(rid)
        self.assertTrue(controls.revocar(rid,actor='SYNTHETIC',referencia='TEST'))
        self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'123456'}).status_code,401)
        self.check.assert_not_called()
        row=Acceso.objects.get(pk=rid);self.assertFalse(row.datos_cifrados);self.assertTrue(row.revocado)
        self.assertFalse(controls.revocar(rid,actor='SYNTHETIC',referencia='TEST'))

    def test_purge_preserves_active_access_cases_consent_and_audit(self):
        from apps.utils.models import AuditLog
        active=self.login()
        rid,_=self.begin()
        Acceso.objects.filter(pk=rid).update(expira_at=timezone.now()-timedelta(seconds=1))
        before=(Solicitud.objects.count(),Acceso.objects.count(),AuditLog.objects.count())
        self.assertEqual(controls.purgar_temporales(actor='TEST',referencia='SYNTHETIC'),1)
        self.assertFalse(Acceso.objects.get(pk=rid).datos_cifrados)
        self.assertTrue(Acceso.objects.get(pk=active).datos_cifrados)
        self.assertEqual(Solicitud.objects.count(),before[0]);self.assertEqual(Acceso.objects.count(),before[1])
        self.assertGreater(AuditLog.objects.count(),before[2])
        self.assertEqual(self.s.consentimiento_consumo.text_hash,self.firma['text_hash'])

    def test_maintenance_requires_permission_and_explicit_apply(self):
        rid=self.login();out=StringIO()
        kw={'operador':self.user.username,'referencia':'SYNTHETIC','stdout':out}
        with self.assertRaises(CommandError):call_command('credihoy_publico_mantener',revocar=rid,**kw)
        self.user.user_permissions.add(Permission.objects.get(codename='revocar_accesopublicocredihoy'))
        call_command('credihoy_publico_mantener',revocar=rid,**kw)
        self.assertFalse(Acceso.objects.get(pk=rid).revocado)
        call_command('credihoy_publico_mantener',revocar=rid,aplicar=True,**kw)
        self.assertTrue(Acceso.objects.get(pk=rid).revocado)
        self.assertFalse(self.client.get(BASE+'sesion/',secure=True).json()['autenticado'])

    def test_mask_needs_cotejo_and_code_needs_explicit_confirmation(self):
        rid,_=self.begin()
        self.assertIsNone(self.client.get(BASE+'acceso/',{'intento':rid},secure=True).json()['contactoEnmascarado'])
        self.assertEqual(service.preparar(rid),'IDENTIFICADO');self.send.assert_not_called()
        data=self.client.get(BASE+'acceso/',{'intento':rid},secure=True).json()
        self.assertEqual(data['contactoEnmascarado'],'***00');self.assertEqual(data['canal'],'SMS')
        self.assertNotIn('3000000000',json.dumps(data));self.assertNotIn('ASOCIADO',json.dumps(data))
        other=APIClient();other.get(BASE+'inicio/',secure=True)
        self.assertIsNone(other.get(BASE+'acceso/',{'intento':rid},secure=True).json()['contactoEnmascarado'])
        self.assertEqual(self.post('acceso/',{'intento':rid,'canal':'EMAIL'}).status_code,401)
        for _ in range(2):self.assertEqual(self.post('acceso/',{'intento':rid,'canal':'SMS'}).status_code,202)
        service.preparar(rid);service.preparar(rid);self.send.assert_called_once()

    def test_expired_or_cancelled_code_never_reaches_provider(self):
        rid,_=self.begin();self.entregar(rid)
        self.assertEqual(self.post('acceso/',{'intento':rid,'cancelar':True}).status_code,200)
        self.assertTrue(Acceso.objects.get(pk=rid).revocado)
        self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'123456'}).status_code,401)
        rid,_=self.begin();self.entregar(rid)
        Acceso.objects.filter(pk=rid).update(expira_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'123456'}).status_code,401)
        self.check.assert_not_called()

    def test_configurable_approved_durations_preserve_existing_safety_ceilings(self):
        from django.conf import settings
        approval={**settings.CREDIHOY_PUBLICO_VALIDACION,'sesionSegundos':600,'otpSegundos':120}
        with override_settings(CREDIHOY_PUBLICO_VALIDACION=approval):
            boot=self.client.get(BASE+'inicio/',secure=True).json()
            self.assertEqual(boot['duracionSesionSegundos'],600);self.assertEqual(boot['duracionOtpSegundos'],120)
            rid,_=self.begin();self.entregar(rid)
            self.assertAlmostEqual((Acceso.objects.get(pk=rid).expira_at-timezone.now()).total_seconds(),120,delta=2)
            self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'123456'}).status_code,200)
            self.assertAlmostEqual((Acceso.objects.get(pk=rid).sesion_expira_at-timezone.now()).total_seconds(),600,delta=2)
        with override_settings(CREDIHOY_PUBLICO_VALIDACION={**approval,'otpSegundos':301}):
            self.assertEqual(self.client.get(BASE+'inicio/',secure=True).status_code,401)

    def test_identity_reads_registered_date_not_legacy_stub_or_visitor_fallback(self):
        p=providers.proyectar_identidad({**self.raw,'fechaNacimiento':'INVALID_UNUSED'})
        self.assertEqual(p['fechaExpedicion'],'2001-08-20')
        self.assertEqual(providers.cotejar_respuesta(self.payload,self.raw)['contacto'],'+573000000000')
        for raw in ({**self.raw,'fechaExpedicion':None},{**self.raw,'fechaExpedicion':'99/99/2001'}):
            with self.assertRaises(ValueError):providers.cotejar_respuesta(self.payload,raw)

    def test_tracking_reuses_stored_quote_with_no_motor_claim_or_financial_write(self):
        from django.db import connection, transaction
        from django.test.utils import CaptureQueriesContext
        from apps.xcore_consumo.models import SolicitudConsumo
        from apps.xcore_consumo.services.credihoy_v2.unico import cotizacion as quotes
        from apps.utils.models import AuditLog
        self.login()
        with transaction.atomic():detail,payload=quotes._contexto(self.s)
        rid=str(uuid4());p=quote_fixture.response(payload,rid,available=True)
        p.update(fuente='LINIX_CALCULO_CUOTA',estado='DISPONIBLE_PARCIAL',ambiente='PRODUCCION',
            parametrosAplicados={'periodicidadInterna':'1','factorAdicional':'0','tasaNominalNamv':'16.67'})
        detail.orchestration_data={quotes.KEY:{'requestId':rid,'versionCondiciones':payload['versionCondiciones'],'resultado':p}}
        detail.save()
        before=SolicitudConsumo.objects.values().get(pk=detail.pk);audits=AuditLog.objects.count()
        with patch.object(quotes,'ApiDataCotizacionClient') as motor,CaptureQueriesContext(connection) as sql:
            result=self.client.get(BASE+'solicitudes/',secure=True)
        self.assertEqual(result.status_code,200)
        q=result.json()['solicitudes'][0]['simulacion']
        self.assertEqual(q['cuota'],'204448');self.assertEqual(q['tasaNominalNamv'],'16.67')
        self.assertFalse(q['ofertaContractual']);self.assertFalse(q['calendarioDisponible'])
        self.assertNotIn('LINIX',json.dumps(q));self.assertNotIn('requestId',q)
        motor.assert_not_called();self.assertEqual(AuditLog.objects.count(),audits)
        self.assertEqual(SolicitudConsumo.objects.values().get(pk=detail.pk),before)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in sql))
        # A changed scenario or price can never inherit the old quote.
        self.commercial['tasaComercialNamv']='17.00'
        self.assertIsNone(self.client.get(BASE+'solicitudes/',secure=True).json()['solicitudes'][0]['simulacion'])

    def test_other_case_cannot_reuse_quote_or_mutate_tracking(self):
        self.login()
        from apps.usuarios.models import Solicitante
        person=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='99999999',
            primer_apellido='OTRO',fecha_expedicion='2001-08-20',celular='3000000001',email='other@example.invalid')
        other=Solicitud.objects.create(solicitante=person,producto=self.s.producto,monto_solicitado=900000,
            metadatos={'modo':'CONSULTA_REAL_CONTROLADA'})
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_http
        with patch.object(publico_http,'simulacion_publica',return_value=None) as reader:
            rows=self.client.get(BASE+'solicitudes/',secure=True).json()['solicitudes']
            self.assertEqual([r['referencia'] for r in rows],[str(self.s.pk)])
            self.assertEqual(reader.call_count,1);self.assertEqual(reader.call_args.args[0].pk,self.s.pk)
            self.assertEqual(self.client.get(BASE+'solicitudes/'+str(other.pk)+'/',secure=True).status_code,404)
            self.assertEqual(reader.call_count,1)
        self.assertEqual(self.post('solicitudes/',{'monto':'1'}).status_code,405)

    def test_restricted_edge_and_browser_proof_do_not_relax_public_profile(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_restringido as private
        cfg={'modo':private.MODE,'aperturaPublica':False,'avisoFormalAprobado':False,
            'referenciaOperativa':'SYNTHETIC_PRIVATE_AUTHORIZATION','solicitudes':[str(self.s.pk)],'operadores':['probador9a']}
        with override_settings(CREDIHOY_PUBLICO_MODO=private.MODE,SETTINGS_MODULE='core.settings_credihoy_restringido',
                CREDIHOY_PUBLICO_ENSAYO=cfg,CREDIHOY_PUBLICO_EDGE_SECRET='x'*64,
                CREDIHOY_PUBLICO_BOT_SITEKEY='',CREDIHOY_PUBLICO_BOT_SECRET=''):
            self.assertEqual(self.client.get(BASE+'inicio/',secure=True).status_code,401)
            self.client.defaults.update(HTTP_X_CREDIHOY_ENSAYO='x'*64,HTTP_X_CREDIHOY_PROBADOR='probador9a')
            self.boot=self.client.get(BASE+'inicio/',secure=True).json()
            self.assertEqual(self.boot['proteccion'],'OPERADOR_RESTRINGIDO')
            self.payload['botToken']=self.boot['controlAcceso'];self.bot.side_effect=self.original_bot
            rid,_=self.begin();self.entregar(rid)
            self.assertEqual(self.post('verificar/',{'intento':rid,'codigo':'123456'}).status_code,200)
            self.client.credentials(HTTP_X_ACCESO_REFERENCIA=rid)
            self.assertEqual(self.client.get(BASE+'solicitudes/',secure=True).status_code,200)
            with override_settings(SETTINGS_MODULE='core.settings_credihoy_publico'):
                self.assertEqual(self.client.get(BASE+'inicio/',secure=True).status_code,401)

    def test_custody_router_rejects_writes_and_allows_only_private_migrations(self):
        from django.core.exceptions import PermissionDenied
        from apps.utils.models import AuditLog
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_restringido import AlmacenamientoAisladoRouter
        router=AlmacenamientoAisladoRouter()
        with self.assertRaises(PermissionDenied):router.db_for_read(Solicitud)
        with self.assertRaises(PermissionDenied):router.db_for_write(Solicitud)
        self.assertEqual(router.db_for_write(Acceso),'default')
        self.assertEqual(router.db_for_write(AuditLog),'default')
        self.assertFalse(router.allow_migrate('custodia','solicitudes'))

    def test_missing_institutional_case_api_is_explicit_without_pilot_db_fallback(self):
        self.login()
        with override_settings(CREDIHOY_PUBLICO_SOLICITUDES_POR_API=True):
            with patch.object(Solicitud.objects,'select_related',side_effect=AssertionError('NO_PILOT_DB')):
                self.assertEqual(self.client.get(BASE+'solicitudes/',secure=True).status_code,503)
                self.assertEqual(self.client.get(BASE+'solicitudes/'+str(self.s.pk)+'/',secure=True).status_code,503)

    def test_private_cleanup_does_not_require_public_notice_or_delete_cases(self):
        from apps.xcore_consumo.tasks import limpiar_accesos_publicos
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_restringido as private
        cfg={'modo':private.MODE,'aperturaPublica':False,'avisoFormalAprobado':False,
            'referenciaOperativa':'SYNTHETIC_PRIVATE_AUTHORIZATION','solicitudes':[str(self.s.pk)],'operadores':['probador9a']}
        with override_settings(CREDIHOY_PUBLICO_MODO=private.MODE,SETTINGS_MODULE='core.settings_credihoy_restringido',
                CREDIHOY_PUBLICO_ENSAYO=cfg,CREDIHOY_PUBLICO_EDGE_SECRET='x'*64,
                CREDIHOY_PUBLICO_VALIDACION={'estado':'BORRADOR','sesionSegundos':900,
                    'otpSegundos':300,'limpiezaTemporalMaxSegundos':60}):
            with patch.object(controls,'purgar_temporales',return_value=0) as cleanup:
                self.assertEqual(limpiar_accesos_publicos.run(),0)
                cleanup.assert_called_once()
        with override_settings(CREDIHOY_PUBLICO_MODO='',
                CREDIHOY_PUBLICO_VALIDACION={'estado':'BORRADOR','sesionSegundos':900,
                    'otpSegundos':300,'limpiezaTemporalMaxSegundos':60}):
            with self.assertRaises(service.AccesoDenegado):limpiar_accesos_publicos.run()
