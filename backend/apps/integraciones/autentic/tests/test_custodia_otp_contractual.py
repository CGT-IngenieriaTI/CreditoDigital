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
