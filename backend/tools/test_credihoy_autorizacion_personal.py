"""Official document on disposable dossiers, no identity/provider operations."""
import base64
import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
from django.conf import settings
from django.http import Http404
from django.test import TestCase,override_settings
from django.test import TransactionTestCase
from django.utils import timezone
from apps.solicitudes.models import Solicitud
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import ConsentimientoConsumo,EvaluacionConsumo,SolicitudConsumo
from apps.xcore_consumo.services.credihoy_v2.unico import autoservicio,progreso_privacidad as privacy
from apps.xcore_consumo.services.credihoy_v2.unico import consentimiento_real as service,publico_institucional as wire
from apps.xcore_consumo.services.credihoy_v2.unico.publico_seguimiento import document_hmac
from apps.documentos.formularios_credihoy import recuperar_formato,custodiar_formato
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError


class DocumentaryJournalTests(TransactionTestCase):
    def test_colo023_rendering_does_not_hold_a_database_transaction(self):
        from django.db import connection
        from apps.documentos import formularios_credihoy as forms
        config=override_settings(**PersonalAuthorizationTests._overridden_settings)
        config.enable();self.addCleanup(config.disable)
        fixture=PersonalAuthorizationTests(methodName='test_own_secure_key_preparation_is_read_only_bound_and_does_not_declare_signing_otp')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        original=forms._diligenciar
        calls=[]
        def render(*args,**kwargs):
            calls.append(connection.in_atomic_block)
            self.assertFalse(connection.in_atomic_block)
            return original(*args,**kwargs)
        with patch.object(forms,'_diligenciar',side_effect=render):
            result=fixture.present()
        self.assertEqual(calls,[False])
        self.assertEqual(result['documento']['codigo'],'COLO-FO.023')
        self.assertFalse(ConsentimientoConsumo.objects.exists())

    def test_postgresql_concurrent_identity_checks_record_one_evidence_without_signature(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connection,connections
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL local requerido')
        fixture,pdf=self.personal_fixture();key=str(uuid4())
        view=service.operar_autogestion(fixture.identity,{'operacion':'autorizacion','accion':'ver_firma',
            'solicitudId':fixture.reference,'clave':key})
        identity={**fixture.identity,'_firmanteInstitucional':fixture.signer_token(pdf['pdfSha256'],key)}
        body={'operacion':'autorizacion','accion':'verificar_firmante','solicitudId':fixture.reference,
            'clave':key,'presentacion':view['documento']['presentacion'],'autorizaFirma':True}
        def attempt(_):
            try:return service.operar_autogestion(identity,body)['firma']['identidadVerificada']
            finally:connections['default'].close()
        with patch('apps.integraciones.autentic.cliente.AutenticDEVClient.firmar',side_effect=AssertionError('NO_SIGNATURE')):
            with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(list(pool.map(attempt,range(2))),[True,True])
        self.assertEqual(AuditLog.objects.filter(event='credihoy_colo023_identidad_firma_acreditada').count(),1)
        data=SolicitudConsumo.objects.get().orchestration_data
        self.assertEqual(len(data['credihoy_autentic_identidad_v1']),1)
        self.assertNotIn('credihoy_autentic_envios_v1',data)

    def personal_fixture(self):
        config=override_settings(**PersonalAuthorizationTests._overridden_settings)
        config.enable();self.addCleanup(config.disable)
        fixture=PersonalAuthorizationTests(methodName='test_own_secure_key_preparation_is_read_only_bound_and_does_not_declare_signing_otp')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        presentation=fixture.present()['documento']
        service.aceptar_autogestion(fixture.reference,fixture.identity,presentacion=presentation['presentacion'],
            acepta=True,clave=str(uuid4()))
        return fixture,presentation

    def sender_arguments(self):
        from apps.integraciones.autentic.cliente import AutenticDEVClient
        from apps.integraciones.autentic.contratos import ConfiguracionDEV
        from apps.integraciones.autentic.operaciones import RegistroFirmasExpediente
        fixture,pdf=self.personal_fixture();key=str(uuid4())
        client=AutenticDEVClient(ConfiguracionDEV('SYNTHETIC_ID','SYNTHETIC_SECRET',permitir_documentos=True))
        self.addCleanup(client.close)
        return fixture,pdf,{'actor':None,'operacion_id':fixture.key,'metadata':None,'clave_idempotencia':key,
            'cliente':client,'registro':RegistroFirmasExpediente(fixture.reference,key),
            'referencia_autorizacion':'SYNTHETIC-NO-REAL-SEND','titular':fixture.identity,
            'identidad_firmante':fixture.signer_token(pdf['pdfSha256'],key),'autoriza_firma':True}

    def test_personal_sender_is_closed_and_rejects_browser_identity_and_foreign_journal_before_claim(self):
        from apps.documentos.formularios_credihoy import enviar_formato_custodiado
        from apps.integraciones.autentic.operaciones import RegistroFirmasExpediente
        fixture,pdf,args=self.sender_arguments()
        with patch.object(args['cliente'],'firmar') as send:
            with self.assertRaises(ValueError):enviar_formato_custodiado(fixture.reference,**args)
            with override_settings(CREDIHOY_AUTENTIC_TITULAR_ENVIO_ENABLED=True):
                for bad in ({'metadata':{'names':'BROWSER','lastNames':'UNVERIFIED'}},
                        {'identidad_firmante':'FORGED'}, {'autoriza_firma':False},
                        {'registro':RegistroFirmasExpediente(uuid4(),args['clave_idempotencia'])},
                        {'registro':RegistroFirmasExpediente(fixture.reference,args['clave_idempotencia'],seccion='journalCertificacion')}):
                    with self.assertRaises(ValueError):enviar_formato_custodiado(fixture.reference,**{**args,**bad})
            send.assert_not_called()
        detail=SolicitudConsumo.objects.get(solicitud_id=fixture.reference)
        self.assertNotIn('credihoy_autentic_envios_v1',detail.orchestration_data)

    def test_personal_sender_commits_dossier_and_journal_before_intercepted_http_and_never_certifies(self):
        from django.db import connection
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL local requerido por el journal durable')
        from apps.documentos.formularios_credihoy import enviar_formato_custodiado
        from apps.integraciones.autentic.cliente import ReciboFirma
        fixture,pdf,args=self.sender_arguments()
        def intercepted(request,**kwargs):
            entry=SolicitudConsumo.objects.get(solicitud_id=fixture.reference).orchestration_data['credihoy_autentic_envios_v1'][args['clave_idempotencia']]
            self.assertEqual(entry['estado'],'ENVIANDO')
            self.assertEqual(entry['pruebaIdentidadFirma']['nombresFuente'],'API_DATA.consultarSocio')
            self.assertNotIn('names',entry['pruebaIdentidadFirma'])
            self.assertEqual(request.payload['metadata']['names'],'TITULAR')
            kwargs['registro'].preparar(kwargs['clave'],request.fingerprint)
            kwargs['registro'].iniciar(kwargs['clave'])
            kwargs['registro'].terminar(kwargs['clave'],'FIRMA_REPORTADA_NO_VERIFICADA',transaccion='SYNTHETIC_TX')
            return ReciboFirma('SYNTHETIC_TX','2026-10-29T12:00:00Z','https://example.invalid/never-downloaded.zip')
        with override_settings(CREDIHOY_AUTENTIC_TITULAR_ENVIO_ENABLED=True),\
                patch.object(args['cliente'],'firmar',side_effect=intercepted) as send:
            first=enviar_formato_custodiado(fixture.reference,**args)
            second=enviar_formato_custodiado(fixture.reference,**args)
            self.assertEqual(first,second);self.assertEqual(send.call_count,1)
        self.assertFalse(first['firmaAcreditada']);self.assertFalse(ConsentimientoConsumo.objects.get().firmado)
        entry=SolicitudConsumo.objects.get().orchestration_data['credihoy_autentic_envios_v1'][args['clave_idempotencia']]
        self.assertEqual(entry['journalFirma']['estado'],'FIRMA_REPORTADA_NO_VERIFICADA')
        self.assertNotIn('SYNTHETIC_TX',entry['reciboCifrado'])
        self.assertFalse(EvaluacionConsumo.objects.exists())

    def test_personal_uncertain_send_never_repeats_same_or_new_intent(self):
        from apps.documentos.formularios_credihoy import enviar_formato_custodiado
        fixture,pdf,args=self.sender_arguments()
        with override_settings(CREDIHOY_AUTENTIC_TITULAR_ENVIO_ENABLED=True),\
                patch.object(args['cliente'],'firmar',side_effect=TimeoutError('INTERCEPTED')) as send:
            result=enviar_formato_custodiado(fixture.reference,**args)
            self.assertEqual(result['estado'],'INDETERMINADA')
            self.assertEqual(enviar_formato_custodiado(fixture.reference,**args),result)
            key=str(uuid4())
            with self.assertRaises(ValueError):enviar_formato_custodiado(fixture.reference,**{**args,
                'clave_idempotencia':key,'identidad_firmante':fixture.signer_token(pdf['pdfSha256'],key)})
            self.assertEqual(send.call_count,1)
        self.assertEqual(len(SolicitudConsumo.objects.get().orchestration_data['credihoy_autentic_envios_v1']),1)
        self.assertFalse(ConsentimientoConsumo.objects.get().firmado)

    def test_personal_download_is_private_cached_bound_and_not_a_verified_signature(self):
        from hashlib import sha256
        from apps.documentos.formularios_credihoy import enviar_formato_custodiado,recuperar_documento_autentic
        from apps.integraciones.autentic.cliente import ReciboFirma
        from apps.integraciones.autentic.evidencia import EvidenciaPDF
        fixture,pdf,args=self.sender_arguments()
        content=base64.b64decode(pdf['pdfBase64']);file=EvidenciaPDF(sha256(content).hexdigest(),content)
        download={'actor':None,'envio_id':args['clave_idempotencia'],'cliente':args['cliente'],
            'referencia_autorizacion':args['referencia_autorizacion'],'titular':fixture.identity}
        with self.assertRaises(ValueError):recuperar_documento_autentic(fixture.reference,**download)
        receipt=ReciboFirma('SYNTHETIC_TX','2026-10-29T12:00:00Z','https://example.invalid/no-real-download.zip')
        with override_settings(CREDIHOY_AUTENTIC_TITULAR_ENVIO_ENABLED=True),\
                patch.object(args['cliente'],'firmar',return_value=receipt),\
                patch.object(args['cliente'],'recuperar_documentos',return_value={'COLO-FO.023.pdf':file}) as get:
            enviar_formato_custodiado(fixture.reference,**args)
            first,meta=recuperar_documento_autentic(fixture.reference,**download)
            second,again=recuperar_documento_autentic(fixture.reference,**download)
            self.assertEqual(first,content);self.assertEqual(first,second);self.assertEqual(meta,again)
            self.assertEqual(get.call_count,1)
            with self.assertRaises(OperacionError):recuperar_documento_autentic(fixture.reference,**{**download,
                'titular':{**fixture.identity,'_scopeAutorizacion':'solicitudes:leer'}})
            self.assertEqual(get.call_count,1)
        self.assertFalse(meta['firmaAcreditada']);self.assertFalse(meta['correspondenciaContenidoFirmadoVerificada'])
        self.assertFalse(ConsentimientoConsumo.objects.get().firmado)
        entry=SolicitudConsumo.objects.get().orchestration_data['credihoy_autentic_envios_v1'][args['clave_idempotencia']]
        self.assertNotIn('pdfBase64',entry)
        self.assertNotIn(base64.b64encode(content).decode(),entry['descargaCifrada'])

    def test_postgresql_existing_dossier_journal_is_durable_and_concurrent_without_new_database(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connection,connections
        from apps.usuarios.models import Solicitante
        from apps.integraciones.autentic.operaciones import RegistroFirmasExpediente
        from apps.integraciones.autentic.contratos import AutenticError
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL local requerido')
        person=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='0000000099',
            primer_apellido='SINTETICO',fecha_expedicion='2005-01-01',celular='+573000000000',email='synthetic@example.invalid')
        case=Solicitud.objects.create(solicitante=person,producto='CREDIHOY UNICO (NUEVO)',
            monto_solicitado='400000',plazo_solicitado=2,metadatos={'modo':'AUTOGESTION_ENVIADA'})
        key=str(uuid4());fingerprint='a'*64
        SolicitudConsumo.objects.create(solicitud=case,orchestration_data={
            'credihoy_autentic_envios_v1':{key:{'vinculo':{'solicitudId':str(case.pk)},'estado':'ENVIANDO'}}})
        journal=RegistroFirmasExpediente(case.pk,key)
        self.assertEqual(journal.preparar(key,fingerprint).estado,'PREPARADA')
        def attempt(_):
            try:
                RegistroFirmasExpediente(case.pk,key).iniciar(key)
                return True
            except AutenticError:return False
            finally:connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(results.count(True),1)
        journal.terminar(key,'INDETERMINADA')
        again=RegistroFirmasExpediente(case.pk,key)
        self.assertEqual(again.preparar(key,fingerprint).estado,'INDETERMINADA')
        with self.assertRaises(AutenticError):again.iniciar(key)
        with self.assertRaises(AutenticError):again.preparar(str(uuid4()),fingerprint)
        with self.assertRaises(AutenticError):again.preparar(key,'b'*64)
        self.assertFalse(ConsentimientoConsumo.objects.filter(solicitud=case).exists())
        self.assertFalse(EvaluacionConsumo.objects.filter(solicitud=case).exists())


@override_settings(CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'s'*64,
    CREDIHOY_AUTOSERVICIO_ENABLED=False,CREDIHOY_AUTORIZACION_TITULAR_ENABLED=True,
    CREDIHOY_AUTORIZACION_DB_ALIAS='default',CREDIHOY_AUTOSERVICIO_DB_ALIAS='default',
    CREDIHOY_TITULAR_FUENTE_ENABLED=True,CREDIHOY_PUBLICO_RESTRINGIDO=False,
    CREDIHOY_PROGRESO_AES_KEY_B64=base64.b64encode(b'p'*32).decode())
class PersonalAuthorizationTests(TestCase):
    def test_personal_identity_proof_cannot_outlive_a_row_lock_wait(self):
        p=self.present()['documento'];key=str(uuid4())
        service.aceptar_autogestion(self.reference,self.identity,presentacion=p['presentacion'],acepta=True,clave=str(uuid4()))
        view=service.ver_documento_para_firma(self.reference,self.identity,clave=key)
        identity={**self.identity,'_firmanteInstitucional':self.signer_token(p['pdfSha256'],key)}
        original=service.obtener_autogestion;expired=timezone.now()+timedelta(seconds=31)
        def delayed(*args,**kwargs):
            out=original(*args,**kwargs)
            if kwargs.get('lock'):
                clock=patch('django.utils.timezone.now',return_value=expired);clock.start();self.addCleanup(clock.stop)
            return out
        with patch.object(service,'obtener_autogestion',side_effect=delayed),self.assertRaises(ValueError):
            service.acreditar_firma_sin_envio(self.reference,identity,clave=key,
                presentacion=view['documento']['presentacion'],autoriza_firma=True)
        self.assertNotIn('credihoy_autentic_identidad_v1',SolicitudConsumo.objects.get().orchestration_data)
        self.assertFalse(AuditLog.objects.filter(event='credihoy_colo023_identidad_firma_acreditada').exists())

    def test_existing_pdf_identity_preflight_is_idempotent_and_never_sends(self):
        from django.core import signing
        p=self.present()['documento'];key=str(uuid4())
        service.aceptar_autogestion(self.reference,self.identity,presentacion=p['presentacion'],acepta=True,clave=str(uuid4()))
        base={'operacion':'autorizacion','solicitudId':self.reference}
        before=AuditLog.objects.count()
        with patch('apps.integraciones.autentic.cliente.AutenticDEVClient.firmar',side_effect=AssertionError('NO_SIGNATURE')) as send:
            view=service.operar_autogestion(self.identity,{**base,'accion':'ver_firma','clave':key})
            self.assertEqual(view['documento']['pdfBase64'],p['pdfBase64'])
            self.assertEqual(wire.project_authorization(view),view)
            self.assertEqual(AuditLog.objects.count(),before)
            request={**base,'accion':'verificar_firmante','clave':key,'presentacion':view['documento']['presentacion'],'autorizaFirma':True}
            identity={**self.identity,'_firmanteInstitucional':self.signer_token(p['pdfSha256'],key)}
            for _ in range(2):result=service.operar_autogestion(identity,request)
            self.assertTrue(result['firma']['identidadVerificada']);self.assertFalse(result['firma']['envioHabilitado'])
            self.assertEqual(wire.project_authorization(result),result)
            for changed in ({'accion':'firmar'},{'autorizaFirma':False},{'presentacion':'FORGED'},
                    {'names':'BROWSER'},{'clave':str(uuid4())}):
                with self.assertRaises((ValueError,TypeError,signing.BadSignature)):
                    service.operar_autogestion(identity,{**request,**changed})
            send.assert_not_called()
        self.assertEqual(ConsentimientoConsumo.objects.count(),1);self.assertFalse(ConsentimientoConsumo.objects.get().firmado)
        self.assertEqual(AuditLog.objects.filter(event='credihoy_colo023_identidad_firma_acreditada').count(),1)
        self.assertNotIn('credihoy_autentic_envios_v1',SolicitudConsumo.objects.get().orchestration_data)
        self.assertFalse(EvaluacionConsumo.objects.exists())

    def test_signed_identity_preflight_delegation_rejects_changed_request(self):
        from types import SimpleNamespace
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_seguimiento as owner
        payload={'accesoId':self.identity['_sesionReferencia'],'navegador':'f'*64,
            'autorizacion':{'operacion':'autorizacion','accion':'verificar_firmante','solicitudId':self.reference}}
        grant=SimpleNamespace(estado='VERIFICADO',revocado=False,pk=self.identity['_sesionReferencia'],
            sesion_expira_at=timezone.now()+timedelta(minutes=5))
        with patch.object(wire,'request_packet',return_value={}) as transport:
            owner.consult(grant,self.identity,payload,self.identity['_requestId'],firmante='SYNTHETIC-ATTESTATION')
        args=transport.call_args.args
        request=SimpleNamespace(method='POST',is_secure=lambda:True,path=owner.PATH,content_type='application/json',
            body=args[2],data=payload,headers={'Authorization':args[4]['Authorization'],'X-Request-ID':self.identity['_requestId']})
        with override_settings(CREDIHOY_TITULAR_ENABLED=True):
            rid,identity=owner.authorize(request)
            self.assertEqual(identity['_firmanteInstitucional'],'SYNTHETIC-ATTESTATION')
            self.assertEqual(rid,self.identity['_requestId'])
            request.headers={**request.headers,'X-Request-ID':str(uuid4())}
            with self.assertRaises(ValueError):owner.authorize(request)

    def signer_token(self,pdf,key,**changes):
        from django.core import signing
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_seguimiento import key as secret
        now=timezone.now()
        proof={'version':'COLO023_IDENTIDAD_INSTITUCIONAL_V1','fuente':'API_DATA.consultarSocio',
            'requestId':self.identity['_requestId'],'consultadoEn':now.isoformat(),
            'sesionReferencia':self.identity['_sesionReferencia'],'sesionExpiraAt':self.identity['_sesionExpiraAt'],
            'verificadoEn':(now-timedelta(seconds=30)).isoformat(),'verificacionReferencia':'a'*64,
            'solicitudId':self.reference,'pdfSha256':pdf,'operacionFirmaId':key,
            'numeroDocumento':self.identity['numeroDocumento'],'names':'TITULAR','lastNames':'SINTETICO',
            'actoPersonalFirma':True,'OTP9AEsFirma':False,**changes}
        return signing.dumps(proof,key=secret(),salt='credihoy-firmante-institucional-v1')

    def test_own_secure_key_preparation_is_read_only_bound_and_does_not_declare_signing_otp(self):
        p=self.present()['documento']
        service.aceptar_autogestion(self.reference,self.identity,presentacion=p['presentacion'],acepta=True,clave=str(uuid4()))
        before=AuditLog.objects.count();key=str(uuid4())
        signer=self.signer_token(p['pdfSha256'],key)
        with patch('requests.sessions.Session.request',side_effect=AssertionError('NO_HTTP')) as http:
            request,evidence=service.preparar_envio_autogestion(self.reference,self.identity,
                identidad_firmante=signer,clave=key,autoriza_firma=True)
            repeated,_=service.preparar_envio_autogestion(self.reference,self.identity,
                identidad_firmante=signer,clave=key,autoriza_firma=True)
            http.assert_not_called()
        self.assertEqual(request.fingerprint,repeated.fingerprint)
        self.assertEqual(evidence['solicitudId'],self.reference)
        self.assertEqual(evidence['pdfSha256'],p['pdfSha256'])
        self.assertEqual(evidence['sesionReferencia'],self.identity['_sesionReferencia'])
        self.assertFalse(evidence['OTP9AEsFirma'])
        self.assertNotIn('identityValidation',request.payload['metadata'])
        self.assertEqual(request.payload['metadata']['secureKey'],
            'CREDIHOY-COLO023:'+service.huella('firma-personal',evidence))
        self.assertEqual(AuditLog.objects.count(),before)
        self.assertFalse(ConsentimientoConsumo.objects.get().firmado)

    def test_preparation_rejects_forged_metadata_missing_act_foreign_pdf_and_uncertain_previous_intent(self):
        p=self.present()['documento'];key=str(uuid4())
        service.aceptar_autogestion(self.reference,self.identity,presentacion=p['presentacion'],acepta=True,clave=str(uuid4()))
        for changes,act in (({},False),({'names':'OTHER','lastNames':'PERSON'},True),
                ({'secureKey':'FORGED'},True),({'solicitudId':str(uuid4())},True),
                ({'sesionReferencia':str(uuid4())},True),({'requestId':str(uuid4())},True),
                ({'operacionFirmaId':str(uuid4())},True),({'pdfSha256':'b'*64},True),
                ({'numeroDocumento':'0000000002'},True),({'fuente':'NAVEGADOR'},True),
                ({'consultadoEn':(timezone.now()-timedelta(minutes=1)).isoformat()},True),
                ({'verificadoEn':(timezone.now()-timedelta(minutes=11)).isoformat()},True),
                ({'consultadoEn':(timezone.now()+timedelta(minutes=1)).isoformat()},True),
                ({'OTP9AEsFirma':True},True)):
            with self.assertRaises(ValueError):service.preparar_envio_autogestion(self.reference,self.identity,
                identidad_firmante=self.signer_token(p['pdfSha256'],key,**changes),clave=key,autoriza_firma=act)
        with self.assertRaises(ValueError):service.preparar_envio_autogestion(self.reference,self.identity,
            identidad_firmante='FORGED',clave=key,autoriza_firma=True)
        with self.assertRaises(TypeError):service.preparar_envio_autogestion(self.reference,self.identity,
            metadata={'names':'TITULAR','lastNames':'SINTETICO'},clave=key,autoriza_firma=True)
        detail=SolicitudConsumo.objects.get(solicitud_id=self.reference)
        detail.orchestration_data['credihoy_autentic_envios_v1']={str(uuid4()):{
            'estado':'INDETERMINADA','vinculo':{'pdfSha256':p['pdfSha256']}}}
        detail.save(update_fields=['orchestration_data'])
        with self.assertRaises(ValueError):service.preparar_envio_autogestion(self.reference,self.identity,
            identidad_firmante=self.signer_token(p['pdfSha256'],key),clave=key,autoriza_firma=True)

    def test_relay_canonical_identity_uses_verified_grant_not_browser_names_and_checks_revocation(self):
        from apps.xcore_consumo.public_access_models import AccesoPublicoCredihoy as Access,OperacionVerifyCredihoy
        from apps.xcore_consumo.services.credihoy_v2.unico import publico,publico_proveedores as provider
        from apps.xcore_consumo.services.otp_crypto import encrypt_text
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_seguimiento import verificar_firmante
        now=timezone.now();sid='VE'+'a'*32
        identity={**self.identity,'avisoVersion':'SYNTHETIC','nombre':'BROWSER CANNOT OVERRIDE'}
        grant=Access.objects.create(estado='VERIFICADO',verificado_at=now,sesion_expira_at=now+timedelta(minutes=5),
            expira_at=now+timedelta(minutes=2),verification_sid=sid,verification_check_sid=sid,
            datos_cifrados=encrypt_text(json.dumps(identity)),navegador_hmac='b'*64,peticion_hmac='c'*64,
            identidad_hmac=publico.huella('identidad','CC:'+identity['numeroDocumento']))
        op=OperacionVerifyCredihoy.objects.create(pk=uuid4(),acceso=grant,accion='COMPROBAR',
            estado='FINALIZADA',peticion_hmac='d'*64,resultado={'estadoProveedor':'approved','verificationSid':sid})
        self.identity.update(_sesionReferencia=str(grant.pk),_sesionExpiraAt=grant.sesion_expira_at.isoformat())
        key=str(uuid4());args={'solicitud_id':self.reference,'pdf_sha256':'a'*64,'clave':key,
            'request_id':self.identity['_requestId'],'autoriza_firma':True}
        raw={'primerNombre':'TITULAR','segundoNombre':'REAL','primerApellido':'SINTETICO',
            'segundoApellido':'INSTITUCIONAL','fechaExpedicion':'01/01/2005','movilTelefono':'3000000000'}
        with patch.object(publico.controles,'identidad_permitida',return_value=True),\
                patch.object(provider,'ApiDataCrediHoyV2Client') as factory,\
                patch.object(provider.ConfiguracionApiDataV2,'desde_settings'):
            client=factory.return_value.consultar_socio;client.return_value=raw
            token=provider.acreditar_firmante(grant,**args)
            proof=verificar_firmante(token,self.identity,solicitud_id=self.reference,pdf_sha256='a'*64,clave=key)
            self.assertEqual(proof['names'],'TITULAR REAL');self.assertEqual(proof['lastNames'],'SINTETICO INSTITUCIONAL')
            client.assert_called_once_with(numero_documento=self.identity['numeroDocumento'],request_id=args['request_id'])
            op.estado='INCIERTA';op.save(update_fields=['estado'])
            with self.assertRaises(ValueError):provider.acreditar_firmante(grant,**args)
            self.assertEqual(client.call_count,1)
            op.estado='FINALIZADA';op.save(update_fields=['estado'])
            def revoke(**kwargs):
                Access.objects.filter(pk=grant.pk).update(revocado=True)
                return raw
            client.side_effect=revoke
            with self.assertRaises(ValueError):provider.acreditar_firmante(grant,**args)
            count=client.call_count
            with self.assertRaises(ValueError):provider.acreditar_firmante(grant,**args)
            self.assertEqual(client.call_count,count)

    def signed_fixture(self):
        """Synthetic trusted-verifier seam; not a provider signature PASS."""
        from hashlib import sha256
        from apps.integraciones.autentic.evidencia import FirmaAutenticVerificada
        from apps.xcore_consumo.services.otp_crypto import encrypt_text,decrypt_text
        p=self.present()['documento']
        service.aceptar_autogestion(self.reference,self.identity,presentacion=p['presentacion'],acepta=True,clave=str(uuid4()))
        consent=ConsentimientoConsumo.objects.get(solicitud_id=self.reference)
        detail=SolicitudConsumo.objects.get(solicitud_id=self.reference)
        key=str(uuid4());signed=b'%PDF-SYNTHETIC-NOT-SIGNED'
        original=p['pdfSha256'];final=sha256(signed).hexdigest();transaction='synthetic-provider-transaction'
        detail.orchestration_data['credihoy_autentic_envios_v1']={key:{'estado':'FIRMA_REPORTADA_NO_VERIFICADA',
            'vinculo':{'solicitudId':self.reference,'documentoOperacionId':self.key,'pdfSha256':original,
                'firmanteReferencia':sha256(self.identity['numeroDocumento'].encode()).hexdigest(),'codigo':'COLO-FO.023'},
            'documentNumber':'SYNTHETIC-NUMBER',
            'reciboCifrado':encrypt_text(json.dumps({'transaccion':transaction})),
            'descargaCifrada':encrypt_text(json.dumps({'solicitudId':self.reference,'envioId':key,
                'originalSha256':original,'pdfSha256':final,'pdfBase64':base64.b64encode(signed).decode(),
                'transaccionReferencia':sha256(transaction.encode()).hexdigest()}))}}
        detail.save(update_fields=['orchestration_data'])
        now=timezone.now()
        proof=FirmaAutenticVerificada(transaction,'SYNTHETIC-NUMBER',
            sha256(self.identity['numeroDocumento'].encode()).hexdigest(),original,final,
            now,now+timedelta(hours=1),'SYNTHETIC-VERIFIER-NOT-INSTITUTIONAL')
        return consent,proof

    def test_signature_boolean_original_pdf_or_provider_hash_are_not_authorization(self):
        consent,proof=self.signed_fixture()
        case=Solicitud.objects.get(pk=self.reference)
        with self.assertRaises(OperacionError):service.verificar_autorizacion_crediticia_personal(case,consent)
        consent.firmado=True;consent.tipo_firma='AUTENTIC_VERIFICADA'
        consent.evidencia.update(estado='AUTORIZACION_FIRMADA_VERIFICADA',firmaReferencia=proof.referencia_verificacion)
        consent.save()
        with self.assertRaises(OperacionError):service.verificar_autorizacion_crediticia_personal(case,consent)
        with patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=proof):
            self.assertEqual(service.verificar_autorizacion_crediticia_personal(case,consent),proof)
        from dataclasses import replace
        for invalid in (replace(proof,firmante_sha256='f'*64),replace(proof,original_sha256='f'*64),
                replace(proof,vigente_hasta=timezone.now()-timedelta(seconds=1)),{'firmado':True}):
            with patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=invalid):
                with self.assertRaises(OperacionError):service.verificar_autorizacion_crediticia_personal(case,consent)

    def test_trusted_backend_signature_registration_is_idempotent_and_default_closed(self):
        consent,proof=self.signed_fixture()
        with self.assertRaises(OperacionError):service.registrar_firma_autogestion(self.reference)
        with override_settings(CREDIHOY_AUTORIZACION_REGISTRO_FIRMA_ENABLED=True),\
                patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=proof):
            for _ in range(2):self.assertTrue(service.registrar_firma_autogestion(self.reference).firmado)
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_COLO023_FIRMA_VERIFICADA').count(),1)
        case=Solicitud.objects.get(pk=self.reference)
        self.assertIsNone(case.asesor_id);self.assertEqual(case.metadatos['modo'],'AUTOGESTION_ENVIADA')
        self.assertFalse(EvaluacionConsumo.objects.exists())

    def hdc_fixture(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
        from apps.integraciones.risk_gateway.operaciones.servicio import ResultadoOperacion,_recibo
        from apps.integraciones.risk_gateway.contratos import HDCNormalizado
        from apps.integraciones.risk_gateway.tests.fixtures import hdc_agregado
        from apps.xcore_consumo.services.credihoy_v2.unico.reglas import HechosReglasUnicoV2
        from apps.xcore_consumo.services.credihoy_v2.base import Hecho
        autoservicio.operar_complemento_personal(self.reference,self.identity,{
            'operacion':'autorizacion','accion':'guardar_complemento','solicitudId':self.reference,
            'operacionId':str(uuid4()),'version':0,'confirma':True,
            'datos':{'periodicidadPago':'MENSUAL','recogerCredihoy':False,'obligacionRecoger':None}})
        consent,proof=self.signed_fixture();rid=uuid4();raw=hdc_agregado()
        raw['requestId']=str(rid);raw['resultado']['request_id']=str(rid)
        dto=HDCNormalizado.desde_payload(raw)
        case=Solicitud.objects.get(pk=self.reference)
        case.metadatos.update(consumoRealAutorizado=True,autorizacionEconomicaCredihoy={
            'referenciaCostos':'SYNTHETIC','responsableCondiciones':'SYNTHETIC',
            'referenciaAutorizacionConsumo':'SYNTHETIC','maximoConsultasPorProveedor':1,'proveedores':['HDC']})
        case.save(update_fields=['metadatos'])
        op=OperacionGateway.objects.create(solicitud=case,consentimiento=consent,proveedor='HDC',
            request_id=rid,estado='COMPLETADA',idempotency_key='synthetic-personal',
            consulta_id=dto['resultado']['consulta_id'],version_contrato=dto['versionContrato'],request_hmac='a'*64)
        op.refresh_from_db()
        op.recibo_hmac=_recibo(op);op.save(update_fields=['recibo_hmac'])
        result=ResultadoOperacion(op.pk,'HDC',op.estado,op.request_id,op.consulta_id,op.version_contrato,'',False,dto)
        actor=get_user_model().objects.create_user(username='synthetic-evaluator',is_staff=True)
        actor.user_permissions.add(Permission.objects.get(codename='add_evaluacionconsumo'))
        args={'operacion':result,'ingresos':Hecho(fuente='SYNTHETIC_MISSING'),
            'hechos_reglas':HechosReglasUnicoV2(),'autogestion':True}
        return case,op,actor,args,proof

    def test_hdc_and_real_rules_persist_only_after_verified_authorization_without_legacy_otp(self):
        from apps.integraciones.risk_gateway.contratos import HDCRequest
        from apps.integraciones.risk_gateway.operaciones.validacion import validar
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import registrar_resultado_consulta_hdc
        case,op,actor,args,proof=self.hdc_fixture()
        with override_settings(CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True):
            with self.assertRaises(OperacionError):registrar_resultado_consulta_hdc(case.pk,actor,**args)
            self.assertFalse(EvaluacionConsumo.objects.exists())
        with override_settings(CREDIHOY_AUTORIZACION_REGISTRO_FIRMA_ENABLED=True),\
                patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=proof):
            service.registrar_firma_autogestion(case.pk)
            detail=SolicitudConsumo.objects.get(solicitud=case)
            detail.form_data.update(periodicidadPago='MENSUAL',recogerCredihoy=False)
            detail.save(update_fields=['form_data'])
            request=HDCRequest.desde_payload({'persona':'natural','person_id_type':'1','person_id_number':self.identity['numeroDocumento'],
                'person_last_name':self.identity['primerApellido']})
            case=Solicitud.objects.get(pk=case.pk)
            self.assertIsNone(validar(case,request)[0].otp_id)
            with override_settings(CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True):
                for _ in range(2):out=registrar_resultado_consulta_hdc(case.pk,actor,**args)
            self.assertEqual(len(out['resultado']['reglas']),16)
            self.assertEqual(out['decisionCrediticia'],'NO_AUTORIZADA')
        self.assertEqual(EvaluacionConsumo.objects.filter(solicitud=case).count(),1)
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_RESULTADO_HDC_REAL_REGISTRADO').count(),1)
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import leer_estado_funcional,ConflictoFuncional
        type(op).objects.filter(pk=op.pk).update(recibo_hmac='f'*64)
        with patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=proof):
            with self.assertRaisesRegex(ConflictoFuncional,'RECIBO_HDC_NO_INTEGRO'):leer_estado_funcional(case)

    def test_real_evaluation_rejects_missing_or_changed_receipt_before_any_persistence(self):
        from dataclasses import replace
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import registrar_resultado_consulta_hdc,ConflictoFuncional
        case,op,actor,args,proof=self.hdc_fixture()
        with override_settings(CREDIHOY_AUTORIZACION_REGISTRO_FIRMA_ENABLED=True,
                CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True),\
                patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=proof):
            service.registrar_firma_autogestion(case.pk)
            for receipt in ('','f'*64):
                with self.subTest(receipt_present=bool(receipt)):
                    type(op).objects.filter(pk=op.pk).update(recibo_hmac=receipt)
                    with self.assertRaisesRegex(ConflictoFuncional,'RECIBO_HDC_NO_INTEGRO'):
                        registrar_resultado_consulta_hdc(case.pk,actor,**args)
                    self.assertFalse(EvaluacionConsumo.objects.exists())
            type(op).objects.filter(pk=op.pk).update(recibo_hmac=op.recibo_hmac)
            for invalid in (replace(args['operacion'],version_contrato='OTHER'),
                    replace(args['operacion'],proveedor='PRESELECTA')):
                with self.assertRaisesRegex(ConflictoFuncional,'OPERACION_HDC_NO_CORRESPONDE'):
                    registrar_resultado_consulta_hdc(case.pk,actor,**{**args,'operacion':invalid})
            self.assertFalse(EvaluacionConsumo.objects.exists())
            self.assertFalse(AuditLog.objects.filter(event='CREDIHOY_RESULTADO_HDC_REAL_REGISTRADO').exists())

    def test_native_classification_requires_trusted_permission_binding_and_real_signature_gate(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.estamentos import actualizar_estamentos
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import ConflictoFuncional
        from apps.xcore_consumo.models import ConsultaEstamentoOracle
        from unittest.mock import Mock
        case,op,actor,args,proof=self.hdc_fixture();client=Mock()
        with self.assertRaises(ConflictoFuncional):
            actualizar_estamentos(case.pk,actor=actor,autogestion=True,cliente=client)
        with override_settings(CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True):
            actor.is_staff=False
            with self.assertRaises(ConflictoFuncional):
                actualizar_estamentos(case.pk,actor=actor,autogestion=True,cliente=client)
            actor.is_staff=True
            with self.assertRaises(OperacionError):
                actualizar_estamentos(case.pk,actor=actor,autogestion=True,cliente=client)
        client.consultar_estamentos.assert_not_called()
        self.assertFalse(ConsultaEstamentoOracle.objects.exists())
        self.assertFalse(EvaluacionConsumo.objects.exists())

    def test_hdc_without_typed_response_does_not_create_an_evaluation(self):
        from dataclasses import replace
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import registrar_resultado_consulta_hdc
        case,op,actor,args,proof=self.hdc_fixture()
        with override_settings(CREDIHOY_AUTORIZACION_REGISTRO_FIRMA_ENABLED=True,
                CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True),\
                patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=proof):
            service.registrar_firma_autogestion(case.pk)
            detail=SolicitudConsumo.objects.get(solicitud=case)
            detail.form_data.update(periodicidadPago='MENSUAL',recogerCredihoy=False)
            detail.save(update_fields=['form_data'])
            for invalid in (replace(args['operacion'],respuesta=None),replace(args['operacion'],reutilizada=True)):
                with self.assertRaisesRegex(ValueError,'RESPUESTA_NO_RECUPERABLE_O_PENDIENTE_CONCILIACION'):
                    registrar_resultado_consulta_hdc(case.pk,actor,**{**args,'operacion':invalid})
        self.assertFalse(EvaluacionConsumo.objects.exists())
        self.assertFalse(AuditLog.objects.filter(event='CREDIHOY_RESULTADO_HDC_REAL_REGISTRADO').exists())

    def test_personal_commercial_and_calendar_preparation_reuses_services_after_signature_without_quote(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import consultar_condiciones
        from apps.xcore_consumo.services.credihoy_v2.unico.cotizacion import registrar_base_calendario,KEY,KEY_CALENDAR
        from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
        from apps.xcore_consumo.services.credihoy_v2.base import Hecho,EstadoCertificacion as EC
        case,op,actor,args,proof=self.hdc_fixture();rid=str(uuid4())
        client=Mock();client.consultar.return_value=SimpleNamespace(pagaduria=None,metadatos=SimpleNamespace(request_id=rid))
        date=timezone.localdate()
        base=Hecho(valor=date,disponible=True,estado_certificacion=EC.CERTIFICADO,fuente='SYNTHETIC',
            ruta_origen='fixture.fechaProyectada',referencia_evidencia='SYNTHETIC',fecha_corte=date,fecha_consulta=timezone.now())
        with override_settings(CREDIHOY_AUTORIZACION_REGISTRO_FIRMA_ENABLED=True,
                CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True,CREDIHOY_TASAS_ENABLED=True),\
                patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=proof),\
                patch('apps.xcore_consumo.services.credihoy_v2.cliente.ConfiguracionApiDataV2.desde_settings',return_value=object()),\
                patch('apps.xcore_consumo.services.credihoy_v2.cliente.ApiDataCrediHoyV2Client',return_value=client):
            service.registrar_firma_autogestion(case.pk)
            for _ in range(2):
                out=consultar_condiciones(case.pk,actor,clave=rid,request_id=rid,autogestion=True)
                registered=registrar_base_calendario(case.pk,actor,base,autogestion=True)
            self.assertEqual(out['decisionCrediticia'],'NO_AUTORIZADA')
            self.assertEqual(registered['tipo'],'PROYECTADO')
        self.assertEqual(SeleccionTarifaCredihoy.objects.filter(solicitud=case).count(),1)
        self.assertEqual(SeleccionTarifaCredihoy.objects.get().resultado['estado'],'TARIFA_NO_DETERMINADA')
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_CONDICIONES_CONSULTADAS').count(),1)
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_BASE_CALENDARIO_REGISTRADA').count(),1)
        data=SolicitudConsumo.objects.get(solicitud=case).orchestration_data
        self.assertIn(KEY_CALENDAR,data);self.assertNotIn(KEY,data)
        self.assertFalse(EvaluacionConsumo.objects.exists());client.consultar.assert_called_once()

    def test_native_classification_reuses_select_reader_and_never_infers_ordinary(self):
        from hashlib import sha256
        from unittest.mock import Mock
        from apps.xcore_consumo.services.credihoy_v2.unico.estamentos import (
            actualizar_estamentos,clasificar_novedades,contrato_familiar,COLABORADOR)
        from apps.xcore_consumo.models import ConsultaEstamentoOracle
        case,op,actor,args,proof=self.hdc_fixture()
        def original(**kw):
            return {'versionContrato':'credihoy-estamentos-select-v1',
                'fuente':'API_DATA.SELECT_FUENTES_SP_CONSULTAFAMILIAR','fuenteSPSha256':contrato_familiar()['fuenteSha256'],
                'solicitudId':str(case.pk),'requestId':kw['request_id'],'consultaReferencia':kw['request_id'],
                'titularReferencia':sha256(self.identity['numeroDocumento'].encode()).hexdigest(),
                'soloLectura':True,'autorizaAprobacion':False,'ejecutaStart':False,
                'lectorSelectExclusivo':True,'contratoAcreditado':True,'mocked':False,'completa':True,'estado':'OK',
                'fuentesVerificadas':['GR001MTERCERO','AP579DCARGXASO','NO223MEMPLEADO','GR1068MGRUPFAM'],
                'selectSha256':'a'*64,'fechaConsulta':timezone.now().isoformat(),
                'filas':[{'resultado':'SI','tipofamiliar':'Trabajador'}]}
        # Synthetic port response, not an institutional classification for the real case.
        client=Mock();client.consultar_estamentos.side_effect=original
        with override_settings(CREDIHOY_AUTORIZACION_REGISTRO_FIRMA_ENABLED=True,
                CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True),\
                patch('apps.integraciones.autentic.evidencia.verificar_firma_documental',return_value=proof):
            service.registrar_firma_autogestion(case.pk)
            evidence=actualizar_estamentos(case.pk,actor=actor,autogestion=True,cliente=client)
        classified=clasificar_novedades(evidence,solicitud_id=case.pk,
            titular_referencia=evidence['titularReferencia'],ahora=timezone.now(),monto=case.monto_solicitado)
        self.assertEqual(classified['estado'],COLABORADOR);self.assertFalse(classified['autorizaAutomatizacion'])
        self.assertEqual(ConsultaEstamentoOracle.objects.get().estado,'OK')
        self.assertFalse(EvaluacionConsumo.objects.exists());client.consultar_estamentos.assert_called_once()

    def test_quote_ownership_recognizes_original_submission_but_never_bypasses_signature_or_flags(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.cotizacion import _obtener,_contexto
        p=self.present()['documento']
        service.aceptar_autogestion(self.reference,self.identity,presentacion=p['presentacion'],acepta=True,clave=str(uuid4()))
        with self.assertRaises(wire.NoDisponible):_obtener(self.reference,None,titular=self.identity)
        with override_settings(CREDIHOY_COTIZACION_TITULAR_ENABLED=True,CREDIHOY_COTIZACION_ENABLED=True,
                CREDIHOY_TITULAR_FUENTE_ENABLED=True,CREDIHOY_SEGUIMIENTO_DB_ALIAS='default',
                CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE='SYNTHETIC',
                CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[self.reference]}):
            case=_obtener(self.reference,None,titular=self.identity)
            self.assertEqual(str(case.pk),self.reference)
            with self.assertRaises(OperacionError):_contexto(case,lock=False)
            with self.assertRaises(Http404):
                _obtener(self.reference,None,titular={**self.identity,'numeroDocumento':'0000000002'})
            case.metadatos['envio']['operacionId']=str(uuid4());case.save(update_fields=['metadatos'])
            with self.assertRaises(Http404):_obtener(self.reference,None,titular=self.identity)
        self.assertFalse(EvaluacionConsumo.objects.exists())

    @override_settings(ROOT_URLCONF='core.urls_credihoy_publico',CREDIHOY_PUBLICO_ENABLED=True)
    def test_personal_http_cookie_csrf_and_revocation_without_loading_identity_worker(self):
        from rest_framework.test import APIClient
        from apps.xcore_consumo.public_access_models import AccesoPublicoCredihoy as Access
        from apps.xcore_consumo.services.credihoy_v2.unico import publico
        from apps.xcore_consumo.services.otp_crypto import encrypt_text
        client=APIClient(enforce_csrf_checks=True);token='SYNTHETIC_BROWSER_'+'a'*48
        grant=Access.objects.create(navegador_hmac=publico.huella('navegador',token),
            identidad_hmac=publico.huella('identidad','CC:'+self.identity['numeroDocumento']),peticion_hmac='a'*64,
            datos_cifrados=encrypt_text(json.dumps({'numeroDocumento':self.identity['numeroDocumento']})),
            estado='VERIFICADO',expira_at=timezone.now()+timedelta(minutes=5),sesion_expira_at=timezone.now()+timedelta(minutes=15))
        session=client.session;session[publico.NAVEGADOR]=token;session[publico.GRANT]=str(grant.pk);session.save()
        body={'operacion':'autorizacion','accion':'consultar','solicitudId':self.reference}
        packet={'sesionReferencia':str(grant.pk),'autorizacion':{'versionContrato':'credihoy-autorizacion-titular-v1',
            'solicitudId':self.reference,'estado':'AUTORIZACION_AUSENTE','documento':None,
            'firmado':False,'consultasHabilitadas':False,'evaluacionHabilitada':False}}
        # Access policy/relay are separate ports; cookie binding and CSRF run here.
        with patch.object(publico,'habilitado'),patch.object(publico,'inicializar',return_value={}),\
                patch.object(publico.controles,'ip_cliente'),patch.object(publico.controles,'identidad_permitida',return_value=True),\
                patch.object(wire,'seguimiento',return_value=packet) as relay:
            boot=client.get('/api/v1/credihoy/publico/inicio/',secure=True).json()
            path='/api/v1/credihoy/publico/solicitudes/'
            self.assertEqual(client.post(path,body,format='json',secure=True).status_code,403)
            headers={'HTTP_X_CSRFTOKEN':boot['csrfToken'],'HTTP_X_ACCESO_REFERENCIA':str(grant.pk),
                'HTTP_ORIGIN':'https://testserver'}
            self.assertEqual(client.post(path,body,format='json',secure=True,**headers).status_code,200)
            count=relay.call_count;Access.objects.filter(pk=grant.pk).update(revocado=True)
            self.assertEqual(client.post(path,body,format='json',secure=True,**headers).status_code,401)
            self.assertEqual(relay.call_count,count)

    def setUp(self):
        self.identity={'numeroDocumento':'0000000001','primerApellido':'SINTETICO','fechaExpedicion':'2005-01-01',
            'contacto':'+573000000000','canal':'SMS','correo':None,'_sesionReferencia':str(uuid4()),
            '_sesionExpiraAt':(timezone.now()+timedelta(minutes=15)).isoformat(),
            '_scopeAutorizacion':'solicitudes:autorizar','_requestId':str(uuid4())}
        cfg=override_settings(CREDIHOY_PROGRESO_VALIDACION={'version':privacy.VERSION,'sha256':privacy.SHA256,
            'consistenciaPoliticaReferencia':'SYNTHETIC',
            'respaldoCustodiaReferencia':'SYNTHETIC','limpiezaMaxSegundos':60},
            CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[document_hmac(self.identity['numeroDocumento'])],
            CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA='SYNTHETIC_PERSONAL',
            CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(timezone.now()+timedelta(hours=1)).isoformat())
        cfg.enable();self.addCleanup(cfg.disable)
        with override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=True):
            notice=privacy.presentar(self.identity)
            body={'operacion':'borrador','accion':'guardar','clave':str(uuid4()),'operacionId':str(uuid4()),
                'version':0,'datos':{'monto':'400000','plazo':2,'email':'synthetic@example.invalid'},'promotor':None,
                'aceptacion':{k:notice[k] for k in ('version','sha256','presentacion')}|{'acepta':True}}
            autoservicio.operar(self.identity,body)
            sent=autoservicio.operar(self.identity,{**body,'accion':'enviar','version':1,'operacionId':str(uuid4())})
        self.reference=sent['referencia'];self.key=str(uuid4())
        scope=override_settings(CREDIHOY_AUTORIZACION_SOLICITUDES=[self.reference])
        scope.enable();self.addCleanup(scope.disable)

    def present(self):
        return service.operar_autogestion(self.identity,{'operacion':'autorizacion','accion':'presentar',
            'solicitudId':self.reference,'clave':self.key,'campos':{'nombre':'TITULAR SINTETICO','ciudad':'SINTETICA'}})

    def test_official_document_bound_to_same_dossier_and_encrypted_without_staff_or_credit_consent(self):
        first=self.present();self.assertEqual(wire.project_authorization(first),first)
        self.assertEqual(first['documento']['codigo'],'COLO-FO.023');self.assertEqual(first['documento']['version'],'10')
        self.assertEqual(first['documento']['pdfSha256'],self.present()['documento']['pdfSha256'])
        case=Solicitud.objects.get(pk=self.reference)
        self.assertIsNone(case.asesor_id);self.assertEqual(case.metadatos['modo'],'AUTOGESTION_ENVIADA')
        stored=SolicitudConsumo.objects.get(solicitud=case).orchestration_data['credihoy_pdf_oficiales_v1'][self.key]
        self.assertNotIn('TITULAR SINTETICO',stored['contenidoCifrado'])
        self.assertNotIn('pdfBase64',stored)
        pdf,meta=recuperar_formato(case.pk,actor=None,titular=self.identity,operacion_id=self.key)
        self.assertTrue(pdf.startswith(b'%PDF-'));self.assertFalse(meta['firmaAcreditada'])
        self.assertFalse(ConsentimientoConsumo.objects.exists());self.assertFalse(EvaluacionConsumo.objects.exists())

    def test_personal_acceptance_is_idempotent_and_never_creates_signature_or_evaluation(self):
        p=self.present()['documento'];key=str(uuid4())
        request={'operacion':'autorizacion','accion':'aceptar','solicitudId':self.reference,
            'presentacion':p['presentacion'],'acepta':True,'clave':key}
        result=service.operar_autogestion(self.identity,request)
        self.assertEqual(result,service.operar_autogestion(self.identity,request))
        c=ConsentimientoConsumo.objects.get();self.assertIsNone(c.otp_id)
        self.assertTrue(c.aceptado);self.assertFalse(c.firmado)
        self.assertEqual(c.tipo_firma,'AUTENTIC_PENDIENTE');self.assertFalse(c.evidencia['otp9AReutilizado'])
        self.assertFalse(service.preflight_autorizacion_autogestion(self.reference,self.identity)['evaluacionHabilitada'])
        self.assertFalse(EvaluacionConsumo.objects.exists())
        self.assertEqual(AuditLog.objects.filter(event='credihoy_colo023_aceptado_personalmente').count(),1)
        self.assertEqual(SolicitudConsumo.objects.get().estado,'CONSENTIMIENTO_PENDIENTE')
        self.assertEqual(result,service.operar_autogestion(self.identity,{**request,'clave':str(uuid4())}))

    def test_new_session_recovers_existing_acceptance_without_overwriting_original_act(self):
        document=self.present()['documento']
        service.aceptar_autogestion(self.reference,self.identity,presentacion=document['presentacion'],acepta=True,clave=str(uuid4()))
        before=ConsentimientoConsumo.objects.values().get(solicitud_id=self.reference)
        detail=SolicitudConsumo.objects.values().get(solicitud_id=self.reference)
        count=AuditLog.objects.count()
        identity={**self.identity,'_sesionReferencia':str(uuid4())}
        case,_=service.obtener_autogestion(self.reference,identity)
        current=service.presentar_documento_existente(case,identity,service.documento_personal_existente(case,identity))
        result=service.aceptar_autogestion(self.reference,identity,presentacion=current['presentacion'],acepta=True,clave=str(uuid4()))
        self.assertEqual(result['estado'],'PENDIENTE_FIRMA_AUTENTIC')
        self.assertEqual(ConsentimientoConsumo.objects.values().get(solicitud_id=self.reference),before)
        self.assertEqual(SolicitudConsumo.objects.values().get(solicitud_id=self.reference),detail)
        self.assertEqual(AuditLog.objects.count(),count)

    def test_recovery_rejects_changed_purpose_text_or_original_custody(self):
        from copy import deepcopy
        document=self.present()['documento']
        service.aceptar_autogestion(self.reference,self.identity,presentacion=document['presentacion'],acepta=True,clave=str(uuid4()))
        original=ConsentimientoConsumo.objects.get(solicitud_id=self.reference)
        evidence=deepcopy(original.evidencia)
        for field,value in (('proposito','OTHER_PURPOSE'),('originalSha256','0'*64),('sesionReferencia',str(uuid4()))):
            original.evidencia={**evidence,field:value};original.save(update_fields=['evidencia'])
            with self.assertRaises(ValueError):service.aceptar_autogestion(self.reference,self.identity,
                presentacion=document['presentacion'],acepta=True,clave=str(uuid4()))
        original.evidencia=evidence;original.text_snapshot+='CHANGED';original.save(update_fields=['evidencia','text_snapshot'])
        with self.assertRaises(ValueError):service.aceptar_autogestion(self.reference,self.identity,
            presentacion=document['presentacion'],acepta=True,clave=str(uuid4()))

    def test_new_verified_session_recovers_same_pdf_without_generation_or_acceptance(self):
        first=self.present()['documento'];before=AuditLog.objects.count()
        new_identity={**self.identity,'_sesionReferencia':str(uuid4())}
        with patch('apps.documentos.formularios_credihoy.custodiar_formato',side_effect=AssertionError('NO_NEW_PDF')):
            result=service.operar_autogestion(new_identity,{'operacion':'autorizacion','accion':'consultar','solicitudId':self.reference})
        self.assertEqual(result['estado'],'PENDIENTE_ACEPTACION_PERSONAL')
        self.assertEqual(result['documento']['pdfBase64'],first['pdfBase64'])
        self.assertEqual(result['documento']['pdfSha256'],first['pdfSha256'])
        self.assertEqual(wire.project_authorization(result),result)
        self.assertEqual(AuditLog.objects.count(),before);self.assertFalse(ConsentimientoConsumo.objects.exists())

    def test_explicit_acceptance_reaccredits_same_bytes_to_current_session_atomically(self):
        first=self.present()['documento'];new_identity={**self.identity,'_sesionReferencia':str(uuid4())}
        result=service.operar_autogestion(new_identity,{'operacion':'autorizacion','accion':'consultar','solicitudId':self.reference})
        before=SolicitudConsumo.objects.get(solicitud_id=self.reference).orchestration_data['credihoy_pdf_oficiales_v1'][self.key]
        body={'operacion':'autorizacion','accion':'aceptar','solicitudId':self.reference,
            'presentacion':result['documento']['presentacion'],'acepta':True,'clave':str(uuid4())}
        with patch('apps.documentos.formularios_credihoy.custodiar_formato',side_effect=AssertionError('NO_NEW_PDF')):
            service.operar_autogestion(new_identity,body);service.operar_autogestion(new_identity,body)
        consent=ConsentimientoConsumo.objects.get(solicitud_id=self.reference)
        history=SolicitudConsumo.objects.get(solicitud_id=self.reference).orchestration_data['credihoy_pdf_oficiales_v1']
        self.assertEqual(history[self.key],before)
        current=history[consent.evidencia['documentoOperacionId']]
        self.assertEqual(current['actorId'],'titular:'+new_identity['_sesionReferencia'])
        self.assertEqual(current['metadata']['pdfSha256'],first['pdfSha256'])
        self.assertEqual(consent.evidencia['sesionReferencia'],new_identity['_sesionReferencia'])
        self.assertEqual(ConsentimientoConsumo.objects.count(),1);self.assertFalse(consent.firmado)

    def test_foreign_dossier_changed_session_rejected_and_disabled_flag_stops_before_document(self):
        p=self.present()['documento']
        with self.assertRaises(Http404):service.obtener_autogestion(str(uuid4()),self.identity)
        with self.assertRaises(ValueError):service.aceptar_autogestion(self.reference,
            {**self.identity,'_sesionReferencia':str(uuid4())},presentacion=p['presentacion'],acepta=True,clave=str(uuid4()))
        with override_settings(CREDIHOY_AUTORIZACION_TITULAR_ENABLED=False):
            with self.assertRaises(OperacionError):self.present()
        self.assertFalse(ConsentimientoConsumo.objects.exists())
        with self.assertRaises(ValueError):custodiar_formato(self.reference,actor=None,titular=self.identity,
            codigo='COLO-FO.001',campos={},clave_idempotencia=str(uuid4()))

    def test_false_signature_cannot_cross_public_contract_and_no_provider_is_called(self):
        with patch('requests.sessions.Session.request',side_effect=AssertionError('NO_PROVIDER')):
            p=self.present()
            with self.assertRaises(wire.NoDisponible):wire.project_authorization({**p,'firmado':True})
            with self.assertRaises(ValueError):service.aceptar_autogestion(self.reference,self.identity,
                presentacion=p['documento']['presentacion'],acepta=False,clave=str(uuid4()))
        self.assertFalse(ConsentimientoConsumo.objects.exists())
