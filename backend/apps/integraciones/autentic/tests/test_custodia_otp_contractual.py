"""Disposable PostgreSQL custody and fake Verify only; never real identities/SMS."""

import base64
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import json
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core import signing
from django.db import connection, connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from reportlab.pdfgen import canvas

from apps.documentos.formularios_credihoy import FORMATOS, MAPA
from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import OtpChallenge, SolicitudConsumo, ConsentimientoConsumo, EstadoOtp
from apps.xcore_consumo.services import otp
from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text
from apps.xcore_consumo.services.credihoy_v2.unico import consentimiento_real as service


SIGNING_KEY = 'synthetic-contractual-signing-key-not-for-real-use-'*2
HMAC_KEY = 'synthetic-contractual-hmac-key-not-for-real-use-'*2
SERVICE_SID = 'VA'+'a'*32
VERIFICATION_SID = 'VE'+'b'*32


@override_settings(OTP_AES_KEY_B64=base64.b64encode(b'e'*32).decode())
class ContractualStagingDocumentTests(TransactionTestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
        from apps.xcore_consumo.services.credihoy_v2.unico.cotizacion import KEY,_huella_simulacion
        self.actor=get_user_model().objects.create_user('synthetic-contractual-custodian',is_staff=True,is_superuser=True)
        self.holder=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='900000001',
            primer_apellido='PERSON',fecha_expedicion=date(2000,1,1))
        self.case=Solicitud.objects.create(solicitante=self.holder,producto=service.PRODUCTO,metadatos={'datosSinteticos':True})
        now=timezone.now();self.session=str(uuid4());self.intent=str(uuid4())
        sim=dict(version='credihoy-simulacion-aceptable-v1',solicitudId=str(self.case.pk),revisionReferencia=str(uuid4()),
            versionCondiciones='a'*64,monto='400000.00',plazoMeses=2,tasaNominalNamv='16.670000',periodicidad='MENSUAL',
            cuota='204448',calculadoEn=(now-timedelta(minutes=5)).isoformat(),vigenteHasta=(now+timedelta(minutes=5)).isoformat(),
            planAproximado=True,firmaContractual=False,aprobacionInstitucional=False)
        sim['sha256']=_huella_simulacion(sim)
        accepted=dict(solicitudId=str(self.case.pk),simulacionSha256=sim['sha256'],versionCondiciones=sim['versionCondiciones'],
            revisionReferencia=sim['revisionReferencia'],titularReferencia=sha256(self.holder.numero_identificacion.encode()).hexdigest(),
            sesionReferencia=self.session,fecha=(now-timedelta(minutes=1)).isoformat(),requestId=str(uuid4()),acepta=True,
            textoVersion='credihoy-aceptacion-simulacion-v1',firmaContractual=False,autorizaStart=False,autorizaDesembolso=False)
        accepted['sha256']=_huella_simulacion(accepted)
        audit=AuditLog.objects.create(solicitud=self.case,event='CREDIHOY_SIMULACION_ACEPTADA_TITULAR',
            actor='titular:'+self.session,request_id=accepted['requestId'],payload={'simulacionSha256':sim['sha256'],
                'aceptacionSha256':accepted['sha256'],'autorizaStart':False,'autorizaDesembolso':False})
        confirmation=AuditLog.objects.create(solicitud=self.case,event='CREDIHOY_RADICACION_PERSONAL_CONFIRMADA',
            request_id=str(uuid4()),payload={'intencionId':self.intent,'comprobante':{'aceptacionAuditoriaId':audit.pk,
                'simulacionSha256':sim['sha256'],'aceptacionSha256':accepted['sha256']}})
        tariff=SeleccionTarifaCredihoy.objects.create(solicitud=self.case,responsable=self.actor,request_id=str(uuid4()),
            evidencia={'fuente':'SYNTHETIC_ONLY'},evidencia_sha256='c'*64,
            resultado={'tasaComercialNamv':'16.670000','tasaComercialEa':'18.01'})
        self.detail=SolicitudConsumo.objects.create(solicitud=self.case,orchestration_data={KEY:{'motor':'PROPIO_390_111_CFA',
            'simulacion':sim,'aceptacionesSimulacion':{sim['sha256']:accepted}},'originalPreservado':{'hash':'d'*64}})
        self.source=dict(version='CREDIHOY_FUENTE_DOCUMENTAL_CD6_V1',solicitudId=str(self.case.pk),
            titularReferencia=accepted['titularReferencia'],ambienteFirma='STAGING',estadoJournal='RADICADA',
            autorizaDesembolso=False,intencionId=self.intent,fingerprint='f'*64,SC='SYNTHETIC_SC',K_FLUJO='SYNTHETIC_FLOW',
            aceptacionAuditoriaId=audit.pk,confirmacionAuditoriaId=confirmation.pk,seleccionComercialId=tariff.pk,
            perfilCore={'tipoNomina':'N'},payload={'solicitudId':str(self.case.pk),'intencionId':self.intent,
                'titular':{'tipoDocumento':'CC','numeroDocumento':self.holder.numero_identificacion},
                'producto':{'codigo':'390','modalidad':'111','destino':'CFA'},
                'credito':{'montoSolicitado':'400000.00','tasaNominalAnual':'16.67','cuotaContractual':'204448.00',
                    'plazoMeses':2,'periodicidad':'MENSUAL'},'contextoLinix':{'sucursal':'1','fechaSolicitud':'11/08/2026',
                    'fechaCuotaOrdinaria':'01/01/2027','datosRadicacion':{'cliente':{'nombre':'SYNTHETIC PERSON',
                        'email':'test@example.invalid','numeroCelular':'3000000001'}}}})
        self.scope=dict(solicitudId=str(self.case.pk),titularReferencia=accepted['titularReferencia'],
            fuenteSha256=sha256(json.dumps(self.source,sort_keys=True,separators=(',',':')).encode()).hexdigest())

    def test_official_three_pages_custody_is_idempotent_without_productive_offer_or_personal_act(self):
        from apps.documentos import formularios_credihoy as forms
        from pypdf import PdfReader
        key=str(uuid4());original=deepcopy(self.detail.orchestration_data)
        with override_settings(CREDIHOY_AUTENTIC_STAGING_DOCUMENTAL=self.scope),patch.object(
                forms,'_exigir_condiciones_definitivas',side_effect=AssertionError('MUST_NOT_FAKE_PRODUCTIVE_OFFER')):
            one=forms.custodiar_colo001_staging(self.case.pk,actor=self.actor,fuente=self.source,clave_idempotencia=key)
            two=forms.custodiar_colo001_staging(self.case.pk,actor=self.actor,fuente=self.source,clave_idempotencia=key)
        self.assertEqual(one,two)
        pdf,meta=forms.recuperar_formato(self.case.pk,actor=self.actor,operacion_id=key)
        reader=PdfReader(BytesIO(pdf));self.assertEqual(len(reader.pages),3)
        fields=reader.get_fields()
        self.assertEqual(fields[MAPA['COLO-FO.001']['tasaEA']]['/V'],'18.01')
        self.assertEqual(fields[MAPA['COLO-FO.001']['monto']]['/V'],'400000.00')
        self.assertEqual(fields['Check Box4']['/V'],'/S\u00ed')
        for page in reader.pages:
            for reference in page.get('/Annots',[]):
                widget=reference.get_object()
                if widget.get('/FT')=='/Tx' and widget.get('/T') in meta['camposDiligenciados']:
                    self.assertEqual(widget.get('/DA'),'/Helv 10 Tf 0 g')
        self.assertEqual(meta['ambienteFirma'],'STAGING');self.assertFalse(meta['firmaProductivaAcreditada'])
        self.assertEqual(meta['cuotaAceptada'],'204448');self.assertFalse(meta['autorizaDesembolso'])
        self.detail.refresh_from_db()
        self.assertEqual({k:v for k,v in self.detail.orchestration_data.items() if k!='credihoy_pdf_oficiales_v1'},original)
        self.assertFalse(OtpChallenge.objects.exists())
        self.assertFalse(AuditLog.objects.filter(event__startswith='credihoy_contractual_').exists())
        for original_page,final_page in zip(PdfReader(BytesIO(forms.original('COLO-FO.001')[0])).pages,reader.pages):
            self.assertEqual(original_page.extract_text(),final_page.extract_text())

    def test_unconfigured_or_other_holder_cannot_prepare_staging(self):
        from apps.documentos import formularios_credihoy as forms
        for scope in (None,{**self.scope,'solicitudId':str(uuid4())},{**self.scope,'titularReferencia':'f'*64}):
            with override_settings(CREDIHOY_AUTENTIC_STAGING_DOCUMENTAL=scope):
                with self.assertRaisesRegex(ValueError,'FUENTE_DOCUMENTAL_STAGING_NO_ACREDITADA'):
                    forms.custodiar_colo001_staging(self.case.pk,actor=self.actor,fuente=self.source,clave_idempotencia=str(uuid4()))

    def test_rectification_keeps_previous_bytes_and_blocks_after_a_personal_act(self):
        from apps.documentos import formularios_credihoy as forms
        original_key,corrected_key=map(str,(uuid4(),uuid4()))
        with override_settings(CREDIHOY_AUTENTIC_STAGING_DOCUMENTAL=self.scope):
            forms.custodiar_colo001_staging(self.case.pk,actor=self.actor,fuente=self.source,clave_idempotencia=original_key)
            previous=forms.recuperar_formato(self.case.pk,actor=self.actor,operacion_id=original_key)
            corrected=forms.custodiar_colo001_staging(self.case.pk,actor=self.actor,fuente=self.source,
                clave_idempotencia=corrected_key,preparacion_anterior=original_key)
            self.assertEqual(corrected['rectificacionDe'],original_key)
            self.assertEqual(previous,forms.recuperar_formato(self.case.pk,actor=self.actor,operacion_id=original_key))
            self.detail.refresh_from_db()
            self.detail.orchestration_data['credihoy_otp_contractual_v1']={'synthetic-personal-act':{}}
            self.detail.save(update_fields=['orchestration_data'])
            with self.assertRaisesRegex(ValueError,'DOCUMENTO_CON_ACTO_PERSONAL_NO_RECTIFICAR'):
                forms.custodiar_colo001_staging(self.case.pk,actor=self.actor,fuente=self.source,
                    clave_idempotencia=str(uuid4()),preparacion_anterior=corrected_key)

    def test_changed_financial_source_cannot_override_accepted_conditions(self):
        from apps.documentos import formularios_credihoy as forms
        for key,value in (('montoSolicitado','1'),('tasaNominalAnual','1'),('cuotaContractual','1')):
            changed=deepcopy(self.source);changed['payload']['credito'][key]=value
            # A changed deployment hash cannot override accepted financial evidence.
            scope={**self.scope,'fuenteSha256':sha256(json.dumps(changed,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
            with override_settings(CREDIHOY_AUTENTIC_STAGING_DOCUMENTAL=scope):
                with self.assertRaisesRegex(ValueError,'CONDICIONES_DOCUMENTALES_STAGING_NO_CONCORDANTES'):
                    forms.custodiar_colo001_staging(self.case.pk,actor=self.actor,fuente=changed,clave_idempotencia=str(uuid4()))


class ContractualOffTests(SimpleTestCase):
    def test_flag_off_blocks_before_models_or_provider(self):
        with override_settings(CREDIHOY_OTP_CONTRACTUAL_ENABLED=False), patch.object(service, 'Solicitud') as query:
            with self.assertRaisesRegex(ValueError, 'OTP_CONTRACTUAL_NO_HABILITADO'):
                service._contexto_contractual(str(uuid4()), {})
            query.objects.assert_not_called()

    def test_send_flag_off_prevents_provider_even_if_candidate_enabled(self):
        with override_settings(CREDIHOY_OTP_CONTRACTUAL_ENVIO_ENABLED=False), patch.object(service, '_cliente_otp_contractual') as client:
            with self.assertRaisesRegex(ValueError, 'ENVIO_OTP_CONTRACTUAL_NO_HABILITADO'):
                service.enviar_otp_contractual(str(uuid4()), {}, clave=str(uuid4()))
            client.assert_not_called()

    def test_institutional_scope_cannot_be_replaced_by_a_browser_boolean(self):
        with override_settings(CREDIHOY_OTP_CONTRACTUAL_ENABLED=True):
            for identity in ({'acepta':True}, {'_scopeAutorizacion':'solicitudes:autorizar'},
                    {'_scopeContractual':service.CONTRACTUAL_SCOPE, '_sesionReferencia':'NOT-A-SESSION'}):
                with self.assertRaisesRegex(ValueError, 'SESION_CONTRACTUAL_NO_ACREDITADA'):
                    service._contexto_contractual(str(uuid4()), identity)

    def test_http_info_logging_is_blocked_before_twilio_client(self):
        with patch('logging.Logger.isEnabledFor', return_value=True), patch(
                'apps.xcore_consumo.services.twilio_verify.TwilioVerifyClient') as client:
            with self.assertRaisesRegex(ValueError, 'OTP_CONTRACTUAL_LOG_HTTP_NO_AISLADO'):
                service._cliente_otp_contractual(SERVICE_SID)
            client.assert_not_called()

    def test_dedicated_verify_profile_is_required_before_constructor_despite_general_token(self):
        valid = dict(TWILIO_VERIFY_API_KEY_SID='SK'+'a'*32, TWILIO_VERIFY_API_KEY_SECRET='s'*32,
            TWILIO_ACCOUNT_SID='AC'+'b'*32, TWILIO_AUTH_TOKEN='SYNTHETIC-GENERAL')
        invalid = ({'TWILIO_VERIFY_API_KEY_SID':''}, {'TWILIO_VERIFY_API_KEY_SID':'AC'+'a'*32},
            {'TWILIO_VERIFY_API_KEY_SID':'SK'+'a'*31}, {'TWILIO_VERIFY_API_KEY_SECRET':''},
            {'TWILIO_VERIFY_API_KEY_SECRET':'s'*31}, {'TWILIO_VERIFY_API_KEY_SECRET':' '*32},
            {'TWILIO_VERIFY_API_KEY_SECRET':None}, {'TWILIO_ACCOUNT_SID':''},
            {'TWILIO_ACCOUNT_SID':'SK'+'b'*32},
            {'TWILIO_VERIFY_API_KEY_SID':'', 'TWILIO_VERIFY_API_KEY_SECRET':''})
        with patch('logging.Logger.isEnabledFor', return_value=False), patch(
                'apps.xcore_consumo.services.twilio_verify.TwilioVerifyClient') as client:
            for change in invalid:
                with self.subTest(fields=tuple(change)), override_settings(**{**valid, **change}):
                    with self.assertRaisesRegex(ValueError, '^PERFIL_VERIFY_CONTRACTUAL_DEDICADO_REQUERIDO$'):
                        service._cliente_otp_contractual(SERVICE_SID)
                    client.assert_not_called()
            with override_settings(**valid):
                self.assertIs(service._cliente_otp_contractual(SERVICE_SID), client.return_value)
            client.assert_called_once_with(service_sid=SERVICE_SID)


@override_settings(CREDIHOY_OTP_CONTRACTUAL_ENABLED=True, CREDIHOY_OTP_CONTRACTUAL_ENVIO_ENABLED=True,
    CREDIHOY_OTP_CONTRACTUAL_DB_ALIAS='default', CREDIHOY_OTP_CONTRACTUAL_VERIFY_SID=SERVICE_SID,
    CREDIHOY_OTP_CONTRACTUAL_HMAC_KEY=HMAC_KEY, CREDIHOY_TITULAR_SECRET=SIGNING_KEY,
    CREDIHOY_INSTITUCIONAL_SECRET='different-synthetic-institutional-key',
    CREDIHOY_PUBLICO_VERIFY_SID='VA'+'c'*32, TWILIO_VERIFY_SID='VA'+'d'*32,
    OTP_AES_KEY_B64=base64.b64encode(b'c'*32).decode())
class ContractualOTPTests(TransactionTestCase):
    databases = {'default'}

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires the disposable PostgreSQL harness; SQLite is not equivalent.')
        for target in ('requests.sessions.Session.request', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('NO_REAL_NETWORK'))
            guard.start(); self.addCleanup(guard.stop)
        self.holder = Solicitante.objects.create(tipo_identificacion='CC', numero_identificacion='900000001',
            primer_apellido='PERSON', fecha_expedicion=date(2000, 1, 1), celular='3000000001',
            email='synthetic-only@example.invalid')
        original_send = str(uuid4())
        self.case = Solicitud.objects.create(solicitante=self.holder, producto=service.PRODUCTO,
            metadatos={'modo':'AUTOGESTION_ENVIADA', 'origen':'AUTOGESTION_9A', 'datosSinteticos':True,
                'productoLinix':'390','modalidadLinix':'111','destinacionLinix':'CFA',
                'envio':{'operacionId':original_send}})
        self.send, self.doc, self.session = map(str, (uuid4(), uuid4(), uuid4()))
        stream = BytesIO(); document = canvas.Canvas(stream)
        document.drawString(20, 800, 'SYNTHETIC CONTRACT OTP TEST ONLY')
        document.acroForm.textfield(name=MAPA['COLO-FO.001']['nombre'], value='SYNTHETIC PERSON',
            x=20, y=740, width=300, height=20)
        document.showPage(); document.save(); self.pdf = stream.getvalue()
        self.pdf_sha = sha256(self.pdf).hexdigest()
        self.meta = dict(codigo='COLO-FO.001', version=FORMATOS['COLO-FO.001']['version'],
            originalSha256=FORMATOS['COLO-FO.001']['sha256'], solicitudId=str(self.case.pk),
            pdfSha256=self.pdf_sha, identidadDocumentalReferencia=sha256(self.holder.numero_identificacion.encode()).hexdigest())
        envelope = dict(solicitudId=str(self.case.pk), operacionId=self.doc, metadata=self.meta,
            pdfBase64=base64.b64encode(self.pdf).decode())
        self.detail = SolicitudConsumo.objects.create(solicitud=self.case, estado='CONSENTIMIENTO_PENDIENTE',
            orchestration_data={'credihoy_autogestion_vinculo_v1':{'solicitudId':str(self.case.pk),
                'operacionEnvioOriginal':original_send}, 'credihoy_pdf_oficiales_v1':{
                self.doc:{'metadata':self.meta, 'contenidoCifrado':encrypt_text(json.dumps(envelope))}}})
        AuditLog.objects.create(solicitud=self.case, event='CREDIHOY_PDF_OFICIAL_CUSTODIADO', request_id=self.doc,
            payload={'codigo':self.meta['codigo'], 'version':self.meta['version'], 'sha256':self.pdf_sha})
        now = timezone.now()
        self.identity = {'numeroDocumento':self.holder.numero_identificacion, '_scopeContractual':service.CONTRACTUAL_SCOPE,
            '_sesionReferencia':self.session, '_sesionExpiraAt':(now+timedelta(minutes=20)).isoformat(), '_requestId':str(uuid4())}
        signer = dict(version='COLO023_IDENTIDAD_INSTITUCIONAL_V1', fuente='API_DATA.consultarSocio',
            numeroDocumento=self.identity['numeroDocumento'], solicitudId=str(self.case.pk), pdfSha256=self.pdf_sha,
            operacionFirmaId=self.send, requestId=self.identity['_requestId'], sesionReferencia=self.session,
            sesionExpiraAt=self.identity['_sesionExpiraAt'], verificadoEn=now.isoformat(), consultadoEn=now.isoformat(),
            names='SYNTHETIC', lastNames='PERSON', actoPersonalFirma=True, OTP9AEsFirma=False,
            verificacionReferencia='a'*64)
        self.identity['_firmanteInstitucional'] = signing.dumps(signer, key=SIGNING_KEY, salt='credihoy-firmante-institucional-v1')
        self.client = Mock()
        def start(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            challenge = OtpChallenge.objects.filter(validation_result__startswith='contractual:').get(solicitud=self.case)
            self.assertEqual(challenge.validation_result, 'contractual:send_reserved')
            self.assertTrue(AuditLog.objects.filter(event='credihoy_contractual_otp_reservado', request_id=self.send).exists())
            return SimpleNamespace(sid=VERIFICATION_SID, status='pending')
        def check(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(kwargs, {'verification_sid':VERIFICATION_SID})
            challenge = OtpChallenge.objects.filter(validation_result__startswith='contractual:').get(solicitud=self.case)
            self.assertEqual(challenge.validation_result, 'contractual:check_reserved')
            return SimpleNamespace(sid=VERIFICATION_SID, status='approved')
        self.client.start_verification.side_effect = start
        self.client.check_verification.side_effect = check
        provider = patch.object(service, '_cliente_otp_contractual', return_value=self.client)
        provider.start(); self.addCleanup(provider.stop)

    def present(self):
        return service.presentar_documento_contractual(self.case.pk, self.identity,
            documento_operacion_id=self.doc, clave=self.send)

    def staging_scope(self):
        return dict(version='CREDIHOY_CONTRACTUAL_STAGING_V1',ambiente='STAGING',solicitudId=str(self.case.pk),
            titularReferencia=sha256(self.holder.numero_identificacion.encode()).hexdigest(),
            documentoOperacionId=self.doc,pdfSha256=self.pdf_sha,venceEn=(timezone.now()+timedelta(hours=1)).isoformat())

    def test_contractual_dispatcher_preserves_separate_personal_presentation_acceptance_and_otp(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        proof=signing.loads(self.identity['_firmanteInstitucional'],key=SIGNING_KEY,salt='credihoy-firmante-institucional-v1')
        proof.update(version='CONTRACTUAL_IDENTIDAD_INSTITUCIONAL_V1',actoPersonalFirma=False)
        self.identity['_firmanteInstitucional']=signing.dumps(proof,key=SIGNING_KEY,salt='credihoy-firmante-institucional-v1')
        base={'operacion':'autorizacion','solicitudId':str(self.case.pk),'clave':self.send}
        with override_settings(CREDIHOY_CONTRACTUAL_AMBIENTE='STAGING',CREDIHOY_CONTRACTUAL_STAGING_ALCANCE=self.staging_scope()):
            p=wire.project_contractual(service.operar_contractual(self.identity,{**base,'accion':'contractual_presentar'}))
            self.assertEqual(p['estado'],'PRESENTADA');self.assertFalse(OtpChallenge.objects.exists())
            self.assertFalse(AuditLog.objects.filter(event='credihoy_contractual_aceptado').exists())
            p=wire.project_contractual(service.operar_contractual(self.identity,{**base,'accion':'contractual_aceptar',
                'presentacion':p['documento']['presentacion'],'acepta':True}))
            self.assertEqual(p['estado'],'ACEPTADA_PENDIENTE_OTP');self.client.start_verification.assert_not_called()
            p=service.operar_contractual(self.identity,{**base,'accion':'contractual_enviar_otp'})
            self.assertEqual(p['estado'],'OTP_ENVIADA')
            p=wire.project_contractual(service.operar_contractual(self.identity,{**base,'accion':'contractual_comprobar_otp',
                'codigo':'123456','comprobacionId':str(uuid4())}))
            self.assertTrue(p['otpVerificado']);self.assertFalse(p['firmaProductivaAcreditada'])
            self.assertFalse(p['autorizaDesembolso']);self.assertIsNone(p['firmado'])
            audits=AuditLog.objects.count()
            restored=wire.project_contractual(service.operar_contractual(self.identity,{**base,'accion':'contractual_consultar'}))
            self.assertEqual(restored['documento']['pdfSha256'],self.pdf_sha)
            self.assertTrue(restored['otpVerificado']);self.assertEqual(AuditLog.objects.count(),audits)

    def test_contractual_cohort_expiry_foreign_holder_and_tampered_pdf_fail_before_provider(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        for changed in ({'venceEn':(timezone.now()-timedelta(seconds=1)).isoformat()},
                {'titularReferencia':'f'*64},{'solicitudId':str(uuid4())}):
            with override_settings(CREDIHOY_CONTRACTUAL_STAGING_ALCANCE={**self.staging_scope(),**changed}):
                with self.assertRaises(wire.NoDisponible):
                    service.operar_contractual(self.identity,{'operacion':'autorizacion','solicitudId':str(self.case.pk),
                        'clave':self.send,'accion':'contractual_presentar'})
        self.client.start_verification.assert_not_called()
        with self.assertRaises(wire.NoDisponible):
            wire.project_contractual({'versionContrato':'credihoy-contractual-staging-v1','ambiente':'PRODUCCION'})

    def test_existing_relay_adapter_uses_integer_challenge_id_and_document_bound_signed_body(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional_http import _autorizar_verify_contractual
        self.accept();self.send_otp()
        case,_=service._contexto_contractual(self.case.pk,self.identity)
        record=service._leer_contractual(case,self.send)
        self.assertRegex(record['otpId'],'^[0-9]+$')
        observed=[]
        def relay(origin,path,body,rid,headers):
            self.assertEqual(origin,'https://relay.example.invalid')
            request=SimpleNamespace(method='POST',is_secure=lambda:True,content_type='application/json',
                path=path,body=body,headers=headers)
            self.assertEqual(_autorizar_verify_contractual(request),rid)
            packet=json.loads(body);observed.append(packet)
            self.assertEqual(packet['otpId'],record['otpId'])
            self.assertEqual(packet['pdfSha256'],self.pdf_sha)
            self.assertEqual(packet['aceptacionSha256'],record['aceptacionSha256'])
            return {'verifyContractual':{'verificationSid':VERIFICATION_SID,'estadoProveedor':'pending'}}
        with override_settings(CREDIHOY_CONTRACTUAL_STAGING_ALCANCE=self.staging_scope(),
                CREDIHOY_CONTRACTUAL_RELE_URL='https://relay.example.invalid',CREDIHOY_CONTRACTUAL_RELE_ENABLED=True),\
                patch.object(wire,'request_packet',side_effect=relay):
            client=wire.cliente_verify_contractual(SERVICE_SID,self.identity,record,comprobacion_id=str(uuid4()))
            self.assertEqual(client.start_verification('+573000000001',channel='sms').sid,VERIFICATION_SID)
            client.check_verification('+573000000001','123456',verification_sid=VERIFICATION_SID)
        self.assertNotIn('codigo',observed[0]);self.assertEqual(observed[1]['codigo'],'123456')

    def test_staging_signature_uses_native_durable_journal_without_productive_formalization_or_resend(self):
        from apps.documentos import formularios_credihoy as forms
        from apps.integraciones.autentic.contratos import ConfiguracionSTAGING
        from apps.integraciones.autentic.cliente import AutenticDEVClient
        from apps.integraciones.autentic.operaciones import RegistroFirmasExpediente
        from .test_cliente import Session,Response,recibo,token
        self.full_act()
        self.detail.refresh_from_db();data=self.detail.orchestration_data
        meta=data['credihoy_pdf_oficiales_v1'][self.doc]['metadata']
        meta.update(ambienteFirma='STAGING',firmaProductivaAcreditada=False,simulacionSha256='d'*64,
            aceptacionSimulacionSha256='e'*64,cuotaAceptada='204448',autorizaDesembolso=False)
        envelope=json.loads(decrypt_text(data['credihoy_pdf_oficiales_v1'][self.doc]['contenidoCifrado']))
        envelope['metadata']=meta;data['credihoy_pdf_oficiales_v1'][self.doc]['contenidoCifrado']=encrypt_text(json.dumps(envelope))
        self.detail.orchestration_data=data;self.detail.save(update_fields=['orchestration_data'])
        session=Session(Response({**token(),'scope':'signingcore-stag:fullaccess'}),Response(recibo(),201))
        client=AutenticDEVClient(ConfiguracionSTAGING('SYNTHETIC','SECRET',permitir_documentos=True,
            hosts_descarga=('descarga.example.test',)),session=session);self.addCleanup(client.close)
        params=dict(actor=None,titular=self.identity,operacion_id=self.doc,metadata=None,clave_idempotencia=self.send,
            cliente=client,registro=RegistroFirmasExpediente(self.case.pk,self.send),
            referencia_autorizacion='SYNTHETIC-STAGING-PERSONAL',autoriza_firma=True)
        with override_settings(CREDIHOY_AUTENTIC_TITULAR_ENVIO_ENABLED=False,
                CREDIHOY_AUTENTIC_CONTRACTUAL_STAGING_ENVIO_ENABLED=True,CREDIHOY_CONTRACTUAL_STAGING_ALCANCE=self.staging_scope()),\
                patch('apps.xcore_consumo.services.credihoy_v2.unico.cotizacion.comprobante_simulacion_aceptada',
                    return_value={'simulacionSha256':'d'*64,'aceptacionSha256':'e'*64,'cuota':'204448'}),\
                patch.object(forms,'_exigir_condiciones_definitivas',side_effect=AssertionError('NO_FAKE_PRODUCTIVE_APPROVAL')):
            first=forms.enviar_formato_custodiado(self.case.pk,**params)
            second=forms.enviar_formato_custodiado(self.case.pk,**params)
        self.assertEqual(len(session.calls),2)
        self.assertEqual(first['estado'],'FIRMA_REPORTADA_NO_VERIFICADA');self.assertFalse(first['firmaAcreditada'])
        self.assertFalse(second['reenvioPermitido'])
        self.detail.refresh_from_db();entry=self.detail.orchestration_data['credihoy_autentic_envios_v1'][self.send]
        self.assertEqual(entry['vinculo']['ambienteFirma'],'STAGING');self.assertNotIn('formalizacion',entry)
        self.assertNotIn('credihoy_formalizacion_autentic_v1',self.detail.orchestration_data)

    def test_native_contractual_scope_can_custody_official_three_page_form_without_signing(self):
        from apps.documentos import formularios_credihoy as forms
        fields={k:{'valor':v,'referencia':'SYNTHETIC_VERIFIED_CONDITIONS','verificado':True}
            for k,v in {'identificacion':self.holder.numero_identificacion,'nombre':'SYNTHETIC PERSON',
                'monto':'400000','plazo':'2','tasaEA':'18.01','periodicidad':'MENSUAL',
                'primerVencimiento':'2026-12-10'}.items()}
        conditions={'condicionesSolicitadas':{'monto':'400000','numeroCuotas':2,'periodicidad':'MENSUAL'},
            'tasaEaPorcentaje':'18.01','primeraFechaPago':'2026-12-10','composicionSha256':'a'*64}
        key=str(uuid4())
        with patch.object(forms,'_exigir_condiciones_definitivas',return_value=conditions):
            first=forms.custodiar_formato(self.case.pk,actor=None,titular=self.identity,codigo='COLO-FO.001',
                campos=fields,clave_idempotencia=key)
            repeated=forms.custodiar_formato(self.case.pk,actor=None,titular=self.identity,codigo='COLO-FO.001',
                campos=fields,clave_idempotencia=key)
        pdf,meta=forms.recuperar_formato(self.case.pk,actor=None,titular=self.identity,operacion_id=key)
        from pypdf import PdfReader
        self.assertEqual(len(PdfReader(BytesIO(pdf)).pages),3)
        self.assertEqual(first,repeated);self.assertEqual(meta['pdfSha256'],sha256(pdf).hexdigest())
        self.assertFalse(meta['firmaAcreditada']);self.assertFalse(meta['envioHabilitado'])
        self.assertEqual(self.client.mock_calls,[]);self.assertFalse(OtpChallenge.objects.exists())

    def test_document_scope_cannot_replace_centrals_contract_or_expired_session(self):
        from apps.documentos import formularios_credihoy as forms
        with self.assertRaisesRegex(ValueError,'FORMATO_NO_CORRESPONDE'):
            forms.diligenciar_para_expediente(self.case.pk,actor=None,titular=self.identity,
                codigo='COLO-FO.023',campos={})
        for identity in ({**self.identity,'_sesionExpiraAt':(timezone.now()-timedelta(seconds=1)).isoformat()},
                {**self.identity,'numeroDocumento':'900000002'}):
            with self.assertRaises((ValueError,Solicitud.DoesNotExist)):
                forms.diligenciar_para_expediente(self.case.pk,actor=None,titular=identity,
                    codigo='COLO-FO.001',campos={})

    def accept(self):
        presented = self.present()
        return service.aceptar_documento_contractual(self.case.pk, self.identity,
            presentacion=presented['presentacion'], acepta=True, clave=self.send)

    def send_otp(self):
        return service.enviar_otp_contractual(self.case.pk, self.identity, clave=self.send)

    def verify(self, operation=None):
        return service.verificar_otp_contractual(self.case.pk, self.identity, clave=self.send,
            codigo='963852', clave_verificacion=operation or str(uuid4()))

    def full_act(self):
        self.accept(); self.send_otp(); return self.verify()

    def record(self):
        self.case.consumo_detail = SolicitudConsumo.objects.get(pk=self.detail.pk)
        return service._leer_contractual(self.case, self.send)

    def test_presentation_and_acceptance_are_bound_without_creating_legacy_consent(self):
        accepted = self.accept()
        self.assertEqual(accepted['estado'], 'ACEPTADA_PENDIENTE_OTP')
        self.assertEqual(OtpChallenge.objects.count(), 0)
        self.assertEqual(ConsentimientoConsumo.objects.count(), 0)
        self.assertEqual(self.client.mock_calls, [])
        self.case.refresh_from_db(); self.detail.refresh_from_db()
        self.assertEqual(self.case.estado, 'INICIADA')
        self.assertEqual(self.detail.estado, 'CONSENTIMIENTO_PENDIENTE')

    def test_acceptance_requires_explicit_true_and_authentic_presentation(self):
        presented = self.present()
        for value in (False, 1, 'true', None):
            with self.assertRaises(ValueError):
                service.aceptar_documento_contractual(self.case.pk, self.identity,
                    presentacion=presented['presentacion'], acepta=value, clave=self.send)
        with self.assertRaises(ValueError):
            service.aceptar_documento_contractual(self.case.pk, self.identity,
                presentacion=presented['presentacion']+'tampered', acepta=True, clave=self.send)
        self.assertFalse(AuditLog.objects.filter(event='credihoy_contractual_aceptado').exists())

    def test_session_scope_holder_and_send_are_not_interchangeable(self):
        presented = self.present()
        for changes in ({'_sesionReferencia':str(uuid4())}, {'_scopeContractual':'solicitudes:autorizar'},
                {'numeroDocumento':'900000002'}):
            with self.assertRaises(Exception):
                service.aceptar_documento_contractual(self.case.pk, {**self.identity, **changes},
                    presentacion=presented['presentacion'], acepta=True, clave=self.send)
        with self.assertRaises(ValueError):
            service.aceptar_documento_contractual(self.case.pk, self.identity,
                presentacion=presented['presentacion'], acepta=True, clave=str(uuid4()))
        self.assertEqual(self.client.mock_calls, [])

    def test_institutional_assertion_keeps_existing_exact_keys_act_time_and_reference_checks(self):
        token = self.identity['_firmanteInstitucional']
        original = signing.loads(token, key=SIGNING_KEY, salt='credihoy-firmante-institucional-v1')
        cases = ({'actoPersonalFirma':False}, {'OTP9AEsFirma':True}, {'verificacionReferencia':'invalid'},
            {'extra':'unexpected'}, {'consultadoEn':(timezone.now()-timedelta(seconds=31)).isoformat()},
            {'sesionExpiraAt':(timezone.now()-timedelta(seconds=1)).isoformat()})
        for change in cases:
            with self.subTest(change=change):
                self.identity['_firmanteInstitucional'] = signing.dumps({**original, **change},
                    key=SIGNING_KEY, salt='credihoy-firmante-institucional-v1')
                with self.assertRaises(ValueError):self.present()
        self.identity['_firmanteInstitucional'] = token
        self.assertEqual(self.client.mock_calls, [])

    def test_presentation_ttl_is_600_and_expiry_cannot_be_repaired_by_boolean(self):
        presented = self.present()
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp()+601):
            with self.assertRaisesRegex(ValueError, 'PRESENTACION_CONTRACTUAL_INVALIDA_O_VENCIDA'):
                service.aceptar_documento_contractual(self.case.pk, self.identity,
                    presentacion=presented['presentacion'], acepta=True, clave=self.send)

    def test_changed_original_pdf_or_document_identity_blocks_acceptance(self):
        presented = self.present()
        data = deepcopy(self.detail.orchestration_data)
        data['credihoy_pdf_oficiales_v1'][self.doc]['metadata']['pdfSha256'] = 'f'*64
        self.detail.orchestration_data = data; self.detail.save(update_fields=['orchestration_data'])
        with self.assertRaises(ValueError):
            service.aceptar_documento_contractual(self.case.pk, self.identity,
                presentacion=presented['presentacion'], acepta=True, clave=self.send)

    def test_prepare_cannot_create_or_promote_acceptance_before_verified_otp(self):
        self.accept()
        with self.assertRaisesRegex(ValueError, 'ACEPTACION_CONTRACTUAL_OTP_NO_ACREDITADA'):
            service.preparar_envio_contractual(self.case.pk, self.identity, self.send)
        self.assertEqual(OtpChallenge.objects.count(), 0)
        self.assertEqual(ConsentimientoConsumo.objects.count(), 0)

    def test_send_reservation_is_durable_and_replay_sends_once(self):
        self.accept(); first = self.send_otp(); self.assertEqual(self.send_otp(), first)
        self.assertEqual(self.client.start_verification.call_count, 1)
        challenge = OtpChallenge.objects.get(solicitud=self.case)
        self.assertEqual(challenge.max_intentos, 3)
        self.assertEqual(challenge.expira_at-service._fecha_contractual(self.record()['reservadoEn']), timedelta(seconds=600))

    def test_send_uncertainty_never_blindly_retries(self):
        self.accept(); self.client.start_verification.side_effect = TimeoutError('PRIVATE_PROVIDER_DETAILS')
        with self.assertRaisesRegex(ValueError, 'INDETERMINADO_NO_REPETIR'):self.send_otp()
        with self.assertRaisesRegex(ValueError, 'INCIERTA_NO_REENVIAR'):self.send_otp()
        self.assertEqual(self.client.start_verification.call_count, 1)
        self.assertNotIn('PRIVATE_PROVIDER_DETAILS', json.dumps(self.record()))

    def test_check_uses_exact_sid_and_replay_never_calls_provider_again(self):
        self.accept(); self.send_otp(); operation = str(uuid4())
        first = self.verify(operation); self.assertEqual(self.verify(operation), first)
        self.assertEqual(self.client.check_verification.call_count, 1)
        self.assertEqual(OtpChallenge.objects.get(solicitud=self.case).verification_check_sid, VERIFICATION_SID)

    def test_foreign_check_sid_or_uncertain_result_cannot_validate_or_retry(self):
        self.accept(); self.send_otp()
        self.client.check_verification.side_effect = None
        self.client.check_verification.return_value = SimpleNamespace(sid='VE'+'e'*32, status='approved')
        with self.assertRaises(ValueError):self.verify()
        with self.assertRaises(ValueError):self.verify()
        self.assertEqual(self.client.check_verification.call_count, 1)
        self.assertNotEqual(OtpChallenge.objects.get(solicitud=self.case).estado, EstadoOtp.VALIDADA)

    def test_three_failed_checks_block_a_fourth_and_idempotency_key_cannot_be_reused(self):
        self.accept(); self.send_otp()
        self.client.check_verification.side_effect = None
        self.client.check_verification.return_value = SimpleNamespace(sid=VERIFICATION_SID, status='pending')
        first = str(uuid4())
        with self.assertRaises(ValueError):self.verify(first)
        with self.assertRaises(ValueError):self.verify(first)
        self.assertEqual(self.client.check_verification.call_count, 1)
        for _ in range(2):
            with self.assertRaises(ValueError):self.verify()
        with self.assertRaises(ValueError):self.verify()
        challenge = OtpChallenge.objects.get(solicitud=self.case)
        self.assertEqual((challenge.intentos, challenge.estado), (3, EstadoOtp.BLOQUEADA))
        self.assertEqual(self.client.check_verification.call_count, 3)

    def test_expired_challenge_never_calls_check(self):
        self.accept(); self.send_otp()
        OtpChallenge.objects.filter(solicitud=self.case).update(expira_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValueError):self.verify()
        self.client.check_verification.assert_not_called()

    def test_legacy_challenge_and_metadata_boolean_cannot_substitute_contractual_otp(self):
        self.accept(); self.send_otp()
        challenge = OtpChallenge.objects.get(solicitud=self.case)
        challenge.estado = EstadoOtp.VALIDADA; challenge.validation_result = 'approved'
        challenge.save(update_fields=['estado','validation_result'])
        with self.assertRaises(ValueError):self.verify()
        with self.assertRaises(ValueError):service.validar_aceptacion_contractual(self.case, self.send, self.pdf_sha)

    def test_prepared_request_uses_hmac_not_otp_and_requested_identity_metadata(self):
        self.full_act()
        request, proof = service.preparar_envio_contractual(self.case.pk, self.identity, self.send)
        challenge = OtpChallenge.objects.get(solicitud=self.case)
        metadata = request.payload['metadata']
        tested_code = self.client.check_verification.call_args.args[1]
        self.assertEqual(metadata['identityValidation'], 'NONE')
        self.assertEqual(metadata['identityValidationId'], challenge.transaction_uuid)
        self.assertEqual(sha256(metadata['secureKey'].encode()).hexdigest(), proof['secureKeySha256'])
        self.assertNotIn(tested_code, json.dumps(metadata))
        self.assertEqual(proof['codigo'], 'COLO-FO.001')
        self.assertNotIn(tested_code, json.dumps(proof))
        for field in ('codigo','otp_hash','otp_code_encrypted','otp_masked'):
            self.assertEqual(getattr(challenge, field), '')
        self.assertNotIn('otp', {key.lower() for key in proof})
        self.detail.refresh_from_db()
        ciphertext = self.detail.orchestration_data[service.CONTRACTUAL_HISTORY][self.send]['contenidoCifrado']
        plaintext = decrypt_text(ciphertext)
        self.assertNotEqual(ciphertext, plaintext)
        self.assertEqual(json.loads(plaintext)['prueba'], proof)
        self.assertNotIn(tested_code, plaintext)
        self.assertEqual(ConsentimientoConsumo.objects.count(), 0)

    def test_historical_validator_is_readonly_and_ignores_current_windows_and_flag(self):
        self.full_act()
        before = SolicitudConsumo.objects.get(pk=self.detail.pk).orchestration_data
        count = AuditLog.objects.count()
        future = timezone.now()+timedelta(days=2)
        with override_settings(CREDIHOY_OTP_CONTRACTUAL_ENABLED=False, CREDIHOY_OTP_CONTRACTUAL_DOCUMENTOS=()),\
                patch.object(service.timezone, 'now', return_value=future), CaptureQueriesContext(connection) as queries:
            first = service.validar_aceptacion_contractual(self.case, self.send, self.pdf_sha)
            second = service.validar_aceptacion_contractual(self.case, self.send, self.pdf_sha)
        self.assertIsInstance(first, service.AceptacionContractualOTPVerificada)
        self.assertEqual(first, second)
        self.assertEqual(AuditLog.objects.count(), count)
        self.assertEqual(SolicitudConsumo.objects.get(pk=self.detail.pk).orchestration_data, before)
        self.assertFalse(any(re.match(r'\s*(INSERT|UPDATE|DELETE|ALTER|CREATE)', item['sql'], re.I) for item in queries))
        self.assertFalse(any('FOR UPDATE' in item['sql'].upper() for item in queries))

    def test_each_original_audit_is_required_and_duplicate_audit_is_not_accepted(self):
        self.full_act()
        event = AuditLog.objects.get(event='credihoy_contractual_otp_validado', request_id=self.send)
        event.payload = {**event.payload, 'referencia':'f'*64}; event.save(update_fields=['payload'])
        with self.assertRaises(ValueError):service.validar_aceptacion_contractual(self.case, self.send, self.pdf_sha)

    def test_tampered_hmac_bound_pdf_or_holder_cannot_validate_from_json_flags(self):
        self.full_act()
        for digest in ('f'*64, ''):
            with self.assertRaises(ValueError):service.validar_aceptacion_contractual(self.case, self.send, digest)
        record = self.record(); record['prueba']['verificadoEn'] = timezone.now().isoformat()
        service._guardar_contractual(self.case, 'default', self.send, record)
        with self.assertRaises(ValueError):service.validar_aceptacion_contractual(self.case, self.send, self.pdf_sha)

    def test_legacy_selection_and_cancellation_do_not_touch_contractual_challenge(self):
        self.accept(); self.send_otp()
        contractual = OtpChallenge.objects.get(solicitud=self.case)
        login = OtpChallenge.objects.create(solicitud=self.case, provider='twilio_verify', estado=EstadoOtp.ENVIADA)
        self.assertEqual(otp.get_active_otp_challenge(self.case).pk, login.pk)
        otp._invalidate_pending_challenges(self.case)
        contractual.refresh_from_db(); login.refresh_from_db()
        self.assertEqual(contractual.estado, EstadoOtp.ENVIADA)
        self.assertEqual(login.estado, EstadoOtp.CANCELADA)

    def test_two_concurrent_send_calls_cannot_reserve_another_provider_request(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        self.accept(); entered, release = Event(), Event()
        def start(*args, **kwargs):
            self.assertFalse(connections['default'].in_atomic_block)
            entered.set()
            if not release.wait(5):raise AssertionError('TEST_BARRIER_TIMEOUT')
            return SimpleNamespace(sid=VERIFICATION_SID, status='pending')
        self.client.start_verification.side_effect = start
        def attempt():
            try:return self.send_otp()
            finally:connections['default'].close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(attempt)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(ValueError):self.send_otp()
            finally:release.set()
            self.assertEqual(pending.result()['estado'], 'OTP_ENVIADA')
        self.assertEqual(self.client.start_verification.call_count, 1)

    def test_direct_active_property_ignores_newest_contractual_and_preserves_legacy_status_order(self):
        legacy = OtpChallenge.objects.create(solicitud=self.case, provider='twilio_verify', estado=EstadoOtp.ENVIADA)
        self.accept(); self.send_otp()
        contractual = OtpChallenge.objects.exclude(pk=legacy.pk).get(solicitud=self.case)
        self.assertEqual(self.case.latest_otp_challenge.pk, contractual.pk)
        self.assertEqual(self.case.active_otp_challenge.pk, legacy.pk)
        legacy.estado = EstadoOtp.CANCELADA
        legacy.save(update_fields=['estado'])
        self.assertIsNone(self.case.active_otp_challenge)
        newest_legacy = OtpChallenge.objects.create(solicitud=self.case, provider='twilio_verify', estado=EstadoOtp.VALIDADA)
        self.assertEqual(self.case.active_otp_challenge.pk, newest_legacy.pk)
        contractual.refresh_from_db()
        self.assertEqual(contractual.estado, EstadoOtp.ENVIADA)
