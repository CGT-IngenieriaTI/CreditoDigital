"""Pure local domain tests: synthetic owner, real AES/Ed25519, no DB or traffic."""

import base64
from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys
from types import ModuleType
from uuid import uuid4

from django.core import signing
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from pypdf import PdfReader
from io import BytesIO

from apps.documentos.formularios_credihoy import _diligenciar
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from apps.xcore_consumo.services.consentimiento_integridad import build_consent_hash
from apps.xcore_consumo.services.otp_crypto import encrypt_text
from apps.xcore_consumo.services.credihoy_v2.unico import autoservicio
from apps.xcore_consumo.services.credihoy_v2.unico import centrales_personal as service


@override_settings(TWILIO_ACCOUNT_SID='AC'+'0'*32,TWILIO_AUTH_TOKEN='',
    TWILIO_VERIFY_API_KEY_SID='SK'+'1'*32,TWILIO_VERIFY_API_KEY_SECRET='s'*40,
    TWILIO_VERIFY_SID='VA'+'2'*32)
class TwilioRuntimeContractTests(SimpleTestCase):
    def setUp(self):
        from apps.xcore_consumo.services.twilio_verify import TwilioVerifyClient
        self.factory=Mock();rest=ModuleType('twilio.rest');rest.Client=self.factory
        replacement=patch.dict(sys.modules,{'twilio.rest':rest})
        replacement.start();self.addCleanup(replacement.stop)
        self.client_type=TwilioVerifyClient

    def test_service_override_and_verification_sid_preserve_the_exact_binding(self):
        selected='VA'+'3'*32;verification='VE'+'4'*32
        client=self.client_type(service_sid=selected)
        client.check_verification('SYNTHETIC_CONTACT','SYNTHETIC_CODE',verification_sid=verification)
        self.factory.return_value.verify.v2.services.assert_called_once_with(selected)
        self.factory.return_value.verify.v2.services.return_value.verification_checks.create.assert_called_once_with(
            verification_sid=verification,code='SYNTHETIC_CODE')
        self.factory.assert_called_once_with('SK'+'1'*32,'s'*40,account_sid='AC'+'0'*32)

    def test_legacy_check_uses_contact_only_when_no_verification_is_supplied(self):
        client=self.client_type()
        client.check_verification('SYNTHETIC_CONTACT','SYNTHETIC_CODE')
        self.factory.return_value.verify.v2.services.assert_called_once_with('VA'+'2'*32)
        self.factory.return_value.verify.v2.services.return_value.verification_checks.create.assert_called_once_with(
            to='SYNTHETIC_CONTACT',code='SYNTHETIC_CODE')

    @override_settings(TWILIO_VERIFY_API_KEY_SECRET='')
    def test_partial_dedicated_credential_never_falls_back_to_another_client(self):
        from apps.xcore_consumo.services.twilio_verify import TwilioVerifyError
        with self.assertRaises(TwilioVerifyError):self.client_type()
        self.factory.assert_not_called()


class AuthorizationDiagnosticTests(SimpleTestCase):
    def test_rejected_documentary_operation_logs_only_sanitized_correlation(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_seguimiento_http import SolicitudesTitularView
        view=SolicitudesTitularView();view.rid=str(uuid4());view.identity={}
        request=SimpleNamespace(data={'autorizacion':{'accion':'aceptar','codigo':'PRIVATE_OTP_PAYLOAD'}})
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.publico_seguimiento.claim_delegation',return_value=True),patch('apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real.operar_autogestion',side_effect=ValueError('PRIVATE_CONTACT_AND_TOKEN')):
            with self.assertLogs('credito.publico',level='WARNING') as logs:
                with self.assertRaises(ValueError):view.post(request)
        text=' '.join(logs.output)
        self.assertIn('codigo=ERROR_TECNICO',text);self.assertIn('accion=aceptar',text)
        self.assertIn('http=403',text);self.assertIn(view.rid,text)
        self.assertNotIn('PRIVATE_',text)


@override_settings(CREDIHOY_TITULAR_SECRET='SYNTHETIC_RELAY_'+'r'*64)
class CentralesRelayBindingTests(SimpleTestCase):
    def setUp(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional_http as relay
        self.relay=relay;self.now=timezone.now();self.operation=str(uuid4())
        self.grant=SimpleNamespace(pk=uuid4(),estado='VERIFICADO',revocado=False,sesion_expira_at=self.now+timedelta(minutes=10))
        self.document='0000000099'
        self.binding={'version':'CENTRALES_OTP_V1','proposito':'CONSULTA_CENTRALES',
            'operacionId':self.operation,'sesionReferencia':str(self.grant.pk),
            'titularSha256':service._sha(self.document.encode()),'aceptadoEn':self.now.isoformat(),
            'vigenteHasta':(self.now+timedelta(minutes=5)).isoformat()}
        self.identity=patch.object(relay,'_identity',return_value={'numeroDocumento':self.document})
        self.identity.start();self.addCleanup(self.identity.stop)

    def token(self,value,salt=service.SALT_INTENCION):
        return signing.dumps(value,key=service.settings.CREDIHOY_TITULAR_SECRET,salt=salt)

    def test_same_session_purpose_and_operation_are_required(self):
        self.assertEqual(self.relay._centrales_binding(self.grant,self.token(self.binding),self.operation),self.binding)
        for change in ({'proposito':'ACCESO'},{'sesionReferencia':str(uuid4())},
                {'operacionId':str(uuid4())},{'titularSha256':'0'*64}):
            with self.assertRaises(self.relay.access.AccesoDenegado):
                self.relay._centrales_binding(self.grant,self.token({**self.binding,**change}),self.operation)

    def test_login_or_presentation_token_cannot_be_used_as_accepted_centrales_intent(self):
        for salt in ('credihoy-acceso-publico-v1',service.SALT_PRESENTACION):
            with self.assertRaises(signing.BadSignature):
                self.relay._centrales_binding(self.grant,self.token(self.binding,salt),self.operation)

    def test_expired_or_revoked_session_blocks_otp_operation(self):
        token=self.token(self.binding)
        self.grant.revocado=True
        with self.assertRaises(self.relay.access.AccesoDenegado):self.relay._centrales_binding(self.grant,token,self.operation)
        self.grant.revocado=False;self.grant.sesion_expira_at=self.now-timedelta(seconds=1)
        with self.assertRaises(self.relay.access.AccesoDenegado):self.relay._centrales_binding(self.grant,token,self.operation)


@override_settings(
    CREDIHOY_CENTRALES_OTP_ENABLED=True,
    CREDIHOY_AUTORIZACION_TITULAR_ENABLED=True,
    CREDIHOY_TITULAR_SECRET='SYNTHETIC_CENTRALES_OWNER_' + 'x' * 64,
    OTP_AES_KEY_B64=base64.b64encode(b't' * 32).decode('ascii'),
    CREDIHOY_CENTRALES_CLAVE_PUBLICA_B64='',
    CREDIHOY_PUBLICO_RESTRINGIDO=False,
    CREDIHOY_AUTOSERVICIO_ALCANCE=None,
)
class CentralesPersonalTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.sid, self.operation, self.session, self.document, self.send = [str(uuid4()) for _ in range(5)]
        self.identity = {'numeroDocumento': '0000000099', '_sesionReferencia': self.session,
                         '_scopeAutorizacion':'solicitudes:autorizar',
                         '_sesionExpiraAt': (self.now + timedelta(hours=1)).isoformat()}
        self.until = self.now + timedelta(hours=2)
        self.enter(override_settings(CREDIHOY_PUBLICO_AUTORIZACION_HASTA=self.until.isoformat()))
        self.enter(patch('socket.socket.connect', side_effect=AssertionError('NETWORK_FORBIDDEN')))
        self.enter(patch('socket.getaddrinfo', side_effect=AssertionError('DNS_FORBIDDEN')))
        self.pdf, metadata = _diligenciar('COLO-FO.023', {
            'identificacion': {'valor': self.identity['numeroDocumento'], 'referencia': 'SYNTHETIC', 'verificado': True}})
        text = '\n'.join(p.extract_text() or '' for p in PdfReader(BytesIO(self.pdf)).pages)
        self.consent = SimpleNamespace(
            solicitud_id=self.sid, aceptado=True, firmado=False, otp_id=None,
            version=service.TEXTO_VERSION, fecha_aceptacion=self.now - timedelta(minutes=1),
            text_snapshot=text, text_hash=build_consent_hash(service.TEXTO_VERSION, text),
            tipo_firma='AUTENTIC_PENDIENTE', evidencia={
                'workflow': 'COLO023_AUTOGESTION_V1', 'proposito': 'AUTORIZACION_CENTRALES_PRECONSULTA',
                'otp9AReutilizado': False, 'originalSha256': metadata['originalSha256'],
                'pdfSha256': metadata['pdfSha256'], 'documentoOperacionId': self.document,
                'claveAceptacion': str(uuid4()), 'sesionReferencia': self.session})
        data = {
            autoservicio.VINCULO_CONSUMO: {'version': 1, 'solicitudId': self.sid,
                'origen': autoservicio.ORIGEN, 'modo': autoservicio.MODO, 'producto': '390',
                'modalidad': '111', 'destinacion': 'CFA', 'operacionEnvioOriginal': self.send},
            'credihoy_pdf_oficiales_v1': {self.document: {'metadata': metadata, 'contenidoCifrado': 'SYNTHETIC'}},
        }
        self.detail = SimpleNamespace(orchestration_data=data, estado='CONSENTIMIENTO_PENDIENTE',
                                      form_data={'monto': '400000.00', 'plazoMeses': 2}, save=Mock())
        self.case = SimpleNamespace(pk=self.sid, asesor_id=None, producto=autoservicio.PRODUCTO,
            solicitante=SimpleNamespace(numero_identificacion=self.identity['numeroDocumento']),
            consumo_detail=self.detail, metadatos={
                'modo': autoservicio.MODO, 'origen': autoservicio.ORIGEN,
                'productoLinix': '390', 'modalidadLinix': '111', 'destinacionLinix': 'CFA',
                'envio': {'operacionId': self.send, 'datos': {'monto': '400000.00', 'plazo': 2}}})
        self.complement()
        self.obtain = self.enter(patch.object(service, 'obtener_autogestion', return_value=(self.case, 'cotizacion')))
        details = Mock()
        details.select_for_update.return_value = details
        details.get.return_value = self.detail
        self.detail_query = details
        self.enter(patch.object(service.SolicitudConsumo.objects, 'using', return_value=details))
        consents = Mock()
        consents.get.return_value = self.consent
        self.enter(patch.object(service.ConsentimientoConsumo.objects, 'using', return_value=consents))
        self.audit = Mock()
        self.enter(patch.object(service.AuditLog.objects, 'using', return_value=self.audit))
        self.atomic = self.enter(patch.object(service.transaction, 'atomic', side_effect=lambda **kw: nullcontext()))
        self.continuation_event = self.enter(patch(
            'apps.xcore_consumo.services.credihoy_v2.unico.continuacion_eventos.registrar'))
        self.recover = self.enter(patch.object(service, 'recuperar_formato', return_value=(self.pdf, deepcopy(metadata))))
        self.public_key = service.obtener_clave_publica_owner_b64()
        self.initial = deepcopy((self.case.metadatos, self.detail.form_data, vars(self.consent)))

    def enter(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def test_confirmed_authorization_registers_one_durable_intent_not_on_read_or_duplicate(self):
        self.confirm()
        self.continuation_event.assert_called_once()
        self.assertEqual(self.continuation_event.call_args.args[1],'cotizacion')
        self.assertEqual(self.continuation_event.call_args.args[2]['estado'],'OTP_CONFIRMADA')
        self.record(self.confirmation)
        service.consultar(self.sid,self.identity)
        self.continuation_event.assert_called_once()

    def complement(self):
        operation = str(uuid4())
        value = {'version': 1, 'solicitudId': self.sid, 'operacionEnvioOriginal': self.send,
                 'operacionId': operation, 'sesionReferencia': self.session,
                 'titularSha256': service._sha(self.identity['numeroDocumento'].encode()),
                 'confirmadoEn': self.now.isoformat(), 'peticionSha256': 'a' * 64,
                 'datos': {'periodicidadPago': 'MENSUAL', 'recogerCredihoy': False, 'obligacionRecoger': None}}
        raw = service._canonical(value)
        self.detail.orchestration_data[autoservicio.COMPLEMENTO_PERSONAL] = {
            'actual': operation, 'revisiones': {operation: {
                'sha256': service._sha(raw), 'contenidoCifrado': encrypt_text(raw.decode())}}}

    def present(self):
        return service.presentar(self.sid, self.identity, self.operation)

    def accept(self):
        self.presentation = self.present()['presentacion']
        self.accepted = service.aceptar(self.sid, self.identity, self.operation, self.presentation, True)
        return self.accepted

    def result(self, state='CONFIRMADA', **changes):
        return {'vinculo': deepcopy(self.accepted['vinculo']), 'estado': state,
                'verificationReferencia': 'b' * 64, 'operacionVerify': str(uuid4()),
                'confirmadoEn': timezone.now().isoformat() if state == 'CONFIRMADA' else None,
                'estadoProveedor': 'approved' if state == 'CONFIRMADA' else 'pending', **changes}

    def record(self, result):
        return service.registrar_resultado(self.sid, self.identity, service._token(result, service.SALT_PROVEEDOR))

    def confirm(self):
        self.accept()
        self.confirmation = self.result()
        return self.record(self.confirmation)

    def backend_verify(self):
        with override_settings(CREDIHOY_TITULAR_SECRET='', OTP_AES_KEY_B64='',
                               CREDIHOY_CENTRALES_CLAVE_PUBLICA_B64=self.public_key), \
                patch.object(service, 'decrypt_text', side_effect=AssertionError('NO_OWNER_AES')), \
                patch.object(service, '_clave_owner', side_effect=AssertionError('NO_OWNER_SECRET')), \
                patch.object(service, 'obtener_autogestion', side_effect=AssertionError('NO_SESSION_DELEGATION')):
            return service.verificar_autorizacion_centrales_personal(self.case, self.consent)

    def test_presentation_exact_binding_original_document_and_distinct_purposes(self):
        view = self.present()
        token = service._cargar_token(view['centrales']['presentacion'], service.SALT_PRESENTACION)
        self.assertEqual(set(token), service.VINCULO_KEYS - {'aceptadoEn'} | {'fechaPresentacion'})
        self.assertEqual(token['version'], 'CENTRALES_OTP_V1')
        self.assertEqual(token['proposito'], 'CONSULTA_CENTRALES')
        self.assertEqual(view['documento']['proposito'], 'AUTORIZACION_CENTRALES_PRECONSULTA')
        self.assertEqual(base64.b64decode(view['documento']['pdfBase64']), self.pdf)
        self.assertFalse(view['documento']['firmado'])
        self.assertLessEqual(service._fecha(token['vigenteHasta']), timezone.now() + timedelta(seconds=600))
        self.assertNotIn(service.KEY, self.detail.orchestration_data)
        self.detail.save.assert_not_called()

    def test_presentation_is_capped_by_session_and_owner_window(self):
        short = self.now + timedelta(seconds=30)
        self.identity['_sesionExpiraAt'] = short.isoformat()
        scope_until = self.now + timedelta(seconds=20)
        with override_settings(CREDIHOY_AUTOSERVICIO_ALCANCE={'sesiones': [self.session], 'hasta': scope_until.isoformat()}):
            view = self.present()
        self.assertEqual(service._fecha(view['centrales']['vigenteHasta']), short)
        with override_settings(CREDIHOY_PUBLICO_AUTORIZACION_HASTA=scope_until.isoformat()):
            self.assertEqual(service._fecha(self.present()['centrales']['vigenteHasta']),scope_until)

    def test_access_scope_or_disabled_authorization_cannot_replace_centrales_scope(self):
        self.identity['_scopeAutorizacion']='solicitudes:leer'
        with self.assertRaises(ValueError):self.present()
        self.identity['_scopeAutorizacion']='solicitudes:autorizar'
        with override_settings(CREDIHOY_AUTORIZACION_TITULAR_ENABLED=False):
            with self.assertRaises(ValueError):self.present()

    def test_acceptance_intent_contract_lock_journal_encryption_and_immutable_dossier(self):
        accepted = self.accept()
        intent = service._cargar_token(accepted['presentacion'], service.SALT_INTENCION)
        self.assertEqual(intent, accepted['vinculo'])
        self.assertEqual(set(intent), service.VINCULO_KEYS)
        self.assertEqual(set(accepted['centrales']), {'estado', 'operacionId', 'proposito', 'metodo',
            'textoVersion', 'textoSha256', 'pdfSha256', 'presentacion', 'confirmadoEn',
            'verificacionReferencia', 'vigenteHasta'})
        self.assertEqual(accepted['centrales']['estado'], 'PENDIENTE_OTP')
        self.atomic.assert_called_with(using='cotizacion')
        self.obtain.assert_any_call(self.sid, self.identity, lock=True)
        self.detail_query.select_for_update.assert_called()
        self.assertEqual(self.detail.save.call_args.kwargs, {
            'using': 'cotizacion', 'update_fields': ['orchestration_data', 'updated_at']})
        journal = self.detail.orchestration_data[service.KEY]
        self.assertNotIn('aceptadoEn', journal['registros'][0]['contenidoCifrado'])
        self.assertEqual(self.initial, (self.case.metadatos, self.detail.form_data, vars(self.consent)))

    def test_acceptance_requires_literal_true(self):
        token = self.present()['presentacion']
        for value in (False, None, 1, 'true', {'acepta': True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.aceptar(self.sid, self.identity, self.operation, token, value)
        self.detail.save.assert_not_called()

    def test_missing_or_corrupted_complement_prevents_acceptance(self):
        token = self.present()['presentacion']
        original = self.detail.orchestration_data.pop(autoservicio.COMPLEMENTO_PERSONAL)
        with self.assertRaisesMessage(ValueError, 'COMPLEMENTO_PERSONAL_REQUERIDO'):
            service.aceptar(self.sid, self.identity, self.operation, token, True)
        self.detail.orchestration_data[autoservicio.COMPLEMENTO_PERSONAL] = original
        original['revisiones'][original['actual']]['sha256'] = 'f' * 64
        with self.assertRaisesMessage(ValueError, 'COMPLEMENTO_PERSONAL_NO_INTEGRO'):
            service.aceptar(self.sid, self.identity, self.operation, token, True)
        self.detail.save.assert_not_called()

    def test_acceptance_idempotent_and_new_operation_conflicts(self):
        self.accept()
        again = service.aceptar(self.sid, self.identity, self.operation, self.presentation, True)
        self.assertEqual(again['vinculo'], self.accepted['vinculo'])
        self.assertEqual(self.audit.create.call_count, 1)
        self.assertEqual(self.detail.save.call_count, 1)
        with self.assertRaisesMessage(ValueError, 'OTRA_OPERACION_EXISTENTE'):
            service.presentar(self.sid, self.identity, str(uuid4()))
        payload = service._cargar_token(self.presentation, service.SALT_PRESENTACION)
        payload['operacionId'] = str(uuid4())
        with self.assertRaisesMessage(ValueError, 'EXISTENTE_NO_SOBRESCRIBIR'):
            service.aceptar(self.sid, self.identity, payload['operacionId'],
                            service._token(payload, service.SALT_PRESENTACION), True)

    def test_presentation_rejects_every_foreign_binding_even_if_signed(self):
        token = self.present()['presentacion']
        payload = service._cargar_token(token, service.SALT_PRESENTACION)
        changes = {'version': 'ACCESS_OTP', 'proposito': 'ACCESO', 'solicitudId': str(uuid4()),
            'operacionId': str(uuid4()), 'sesionReferencia': str(uuid4()), 'titularSha256': 'e' * 64,
            'documentoOperacionId': str(uuid4()), 'pdfSha256': 'e' * 64,
            'textoVersion': 'OTHER', 'textoSha256': 'e' * 64,
            'vigenteHasta': (self.now + timedelta(days=1)).isoformat(),
            'fechaPresentacion': (self.now + timedelta(days=1)).isoformat()}
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                altered = {**payload, field: value}
                service.aceptar(self.sid, self.identity, self.operation,
                                service._token(altered, service.SALT_PRESENTACION), True)
        self.detail.save.assert_not_called()

    def test_waiting_for_lock_or_pdf_read_does_not_extend_intent(self):
        token = self.present()['presentacion']
        future = self.now + timedelta(minutes=11)
        original = service._pdf_original
        def delayed(*args):
            pdf = original(*args)
            self.enter(patch.object(service.timezone, 'now', return_value=future))
            return pdf
        with patch.object(service, '_pdf_original', side_effect=delayed), self.assertRaises(ValueError):
            service.aceptar(self.sid, self.identity, self.operation, token, True)
        self.detail.save.assert_not_called()

    def test_all_token_types_have_distinct_salts_and_max_age(self):
        self.accept()
        result = self.result()
        for salt in (service.SALT_PRESENTACION, service.SALT_INTENCION, 'credihoy-publico-verify-v1'):
            with self.subTest(salt=salt), self.assertRaises(ValueError):
                service.registrar_resultado(self.sid, self.identity, service._token(result, salt))
        with patch('django.core.signing.time.time', return_value=100000):
            old = service._token(result, service.SALT_PROVEEDOR)
        with patch('django.core.signing.time.time', return_value=100601), self.assertRaises(ValueError):
            service.registrar_resultado(self.sid, self.identity, old)

    def test_browser_dict_boolean_and_access_proof_cannot_confirm(self):
        self.accept()
        for proof in ({'confirmado': True}, True, self.result(), 'unsigned', self.accepted['presentacion']):
            with self.subTest(proof=type(proof)), self.assertRaises(ValueError):
                service.registrar_resultado(self.sid, self.identity, proof)
        with self.assertRaises(OperacionError):
            self.backend_verify()

    def test_provider_confirmation_requires_complete_binding_and_approved_result(self):
        self.accept()
        result = self.result()
        changes = {'estadoProveedor': 'pending', 'verificationReferencia': None,
            'operacionVerify': 'not-a-uuid', 'confirmadoEn': None}
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.record({**result, field: value})
        for field in service.VINCULO_KEYS:
            with self.subTest(binding=field), self.assertRaises(ValueError):
                altered = deepcopy(result)
                altered['vinculo'][field] = 'foreign'
                self.record(altered)
        with self.assertRaises(ValueError):
            self.record({**result, 'confirmadoEn': (self.now - timedelta(days=1)).isoformat()})
        with self.assertRaises(ValueError):
            self.record({**result, 'confirmadoEn': (timezone.now() + timedelta(minutes=1)).isoformat()})
        self.assertEqual(len(self.detail.orchestration_data[service.KEY]['registros']), 1)

    def test_confirmation_is_append_only_idempotent_and_never_signs_pdf(self):
        self.accept()
        sent = self.result('ENVIADO')
        self.record(sent)
        confirmed = self.result()
        self.record(confirmed)
        before = deepcopy(self.detail.orchestration_data)
        self.record(confirmed)
        self.record(sent)
        self.assertEqual(self.detail.orchestration_data, before)
        self.assertEqual(self.audit.create.call_count, 3)
        self.assertEqual(len(before[service.KEY]['registros']), 3)
        self.assertFalse(self.consent.firmado)
        self.assertEqual(self.initial, (self.case.metadatos, self.detail.form_data, vars(self.consent)))
        for altered in (self.result('RECHAZADA'), self.result()):
            with self.assertRaises(ValueError):
                self.record(altered)
        journal, _ = service._leer_journal(self.case, self.consent)
        self.assertEqual(journal['estado'], 'OTP_CONFIRMADA')

    def test_indeterminate_unknown_sid_allows_later_known_verification(self):
        self.accept()
        self.record(self.result('INDETERMINADA', verificationReferencia=None, estadoProveedor=None))
        self.record(self.result())
        self.assertEqual(self.detail.orchestration_data[service.KEY]['estado'], 'OTP_CONFIRMADA')

    def test_null_reference_only_allowed_for_indeterminate_and_known_reference_cannot_change(self):
        self.accept()
        for state in ('ENVIADO', 'CONFIRMADA', 'RECHAZADA'):
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.record(self.result(state, verificationReferencia=None))
        self.record(self.result('ENVIADO'))
        self.record(self.result('INDETERMINADA', verificationReferencia=None))
        with self.assertRaises(ValueError):
            self.record(self.result(verificationReferencia='c' * 64))
        self.record(self.result())

    def test_confirmation_requires_intact_pdf_cipher_on_owner_mutations(self):
        self.accept()
        with patch.object(service, 'recuperar_formato', side_effect=ValueError('CIPHER_TAMPERED')):
            with self.assertRaises(ValueError):
                self.record(self.result())
        self.assertEqual(len(self.detail.orchestration_data[service.KEY]['registros']), 1)

    def test_backend_public_key_only_verification_returns_typed_authorization(self):
        self.confirm()
        verified = self.backend_verify()
        self.assertIs(type(verified), service.AutorizacionCentralesVerificada)
        self.assertEqual(verified.metodo, 'OTP')
        self.assertEqual(verified.referencia_verificacion, 'b' * 64)
        self.assertEqual(verified.pdf_sha256, service._sha(self.pdf))
        self.assertEqual(verified.vigente_hasta, self.until)

    def test_durable_authorization_outlives_intent_but_never_renews(self):
        self.confirm()
        with patch.object(service.timezone, 'now', return_value=self.now + timedelta(minutes=20)), \
                override_settings(CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(self.until + timedelta(days=1)).isoformat()):
            self.assertEqual(self.backend_verify().vigente_hasta, self.until)
        with patch.object(service.timezone, 'now', return_value=self.until), self.assertRaises(OperacionError):
            self.backend_verify()

    def test_backend_rejects_missing_or_different_public_key_without_owner_fallback(self):
        self.confirm()
        for public in ('', base64.b64encode(b'z' * 32).decode()):
            with self.subTest(public=bool(public)), override_settings(CREDIHOY_CENTRALES_CLAVE_PUBLICA_B64=public), \
                    self.assertRaises(OperacionError):
                service.verificar_autorizacion_centrales_personal(self.case, self.consent)

    def test_signature_rejects_every_altered_public_field(self):
        self.confirm()
        proof = self.detail.orchestration_data[service.KEY]['comprobante']
        original = deepcopy(proof)
        for key in list(proof['payload']):
            with self.subTest(key=key), self.assertRaises(OperacionError):
                proof['payload'][key] = 'ALTERED'
                self.backend_verify()
            proof['payload'] = deepcopy(original['payload'])
        proof['firmaBase64'] = base64.b64encode(b'0' * 64).decode()
        with self.assertRaises(OperacionError):
            self.backend_verify()

    def test_valid_signature_still_requires_purpose_holder_uuid_and_confirmation(self):
        self.confirm()
        journal = self.detail.orchestration_data[service.KEY]
        original = deepcopy(journal)
        changes = {'proposito': 'ACCESO', 'titularSha256': 'e' * 64,
            'solicitudId': str(uuid4()), 'estado': 'ENVIADO', 'estadoProveedor': 'pending',
            'confirmadoEn': None, 'operacionEnvioOriginal': str(uuid4())}
        for field, value in changes.items():
            altered = deepcopy(original)
            payload = altered['comprobante']['payload']
            payload[field] = value
            if field in service.VINCULO_KEYS:
                altered['vinculo'][field] = value
            altered['comprobante']['firmaBase64'] = base64.b64encode(
                service._clave_owner().sign(service._mensaje_evidencia(payload))).decode()
            self.detail.orchestration_data[service.KEY] = altered
            with self.subTest(field=field), self.assertRaises(OperacionError):
                self.backend_verify()
        self.detail.orchestration_data[service.KEY] = original

    def test_backend_rejects_changed_snapshot_pdf_metadata_holder_and_submission(self):
        self.confirm()
        original = deepcopy((vars(self.consent), self.detail.orchestration_data, self.case.metadatos))
        for kind in ('snapshot', 'pdf', 'holder', 'submission', 'signed'):
            with self.subTest(kind=kind):
                if kind == 'snapshot':
                    self.consent.text_snapshot += ' CHANGED'
                    self.consent.text_hash = build_consent_hash(self.consent.version, self.consent.text_snapshot)
                elif kind == 'pdf':
                    self.detail.orchestration_data['credihoy_pdf_oficiales_v1'][self.document]['metadata']['pdfSha256'] = 'e' * 64
                elif kind == 'holder':
                    self.case.solicitante.numero_identificacion = '0000000011'
                elif kind == 'submission':
                    self.case.metadatos['envio']['operacionId'] = str(uuid4())
                else:
                    self.consent.firmado = True
                with self.assertRaises(OperacionError):
                    self.backend_verify()
                self.consent.__dict__.update(deepcopy(original[0]))
                self.detail.orchestration_data = deepcopy(original[1])
                self.case.metadatos = deepcopy(original[2])
                self.case.solicitante.numero_identificacion = self.identity['numeroDocumento']

    def test_owner_journal_detects_cipher_hash_chain_and_evidence_tampering(self):
        self.confirm()
        original = deepcopy(self.detail.orchestration_data[service.KEY])
        for kind in ('hash', 'cipher', 'chain', 'proof_cipher'):
            altered = deepcopy(original)
            if kind == 'hash':
                altered['registros'][0]['sha256'] = 'f' * 64
            elif kind == 'cipher':
                altered['registros'][0]['contenidoCifrado'] = 'bad'
            elif kind == 'chain':
                altered['registros'].reverse()
            else:
                altered['evidenciaCifrada'] = encrypt_text('{}')
            self.detail.orchestration_data[service.KEY] = altered
            with self.subTest(kind=kind), self.assertRaisesMessage(ValueError, 'JOURNAL_NO_INTEGRO'):
                service.consultar(self.sid, self.identity)

    def test_consultar_resumes_same_session_without_resend_or_expiry_extension(self):
        self.accept()
        self.record(self.result('ENVIADO'))
        before = deepcopy(self.detail.orchestration_data)
        safe = service.consultar(self.sid, self.identity)['centrales']
        self.assertEqual(service._cargar_token(safe['presentacion'], service.SALT_INTENCION), self.accepted['vinculo'])
        self.assertEqual(before, self.detail.orchestration_data)
        safe['presentacion'] = None
        rendered = json.dumps(safe)
        self.assertNotIn(self.identity['numeroDocumento'], rendered)
        self.assertNotIn('Cifrado', rendered)
        self.identity['_sesionReferencia'] = str(uuid4())
        self.assertIsNone(service.consultar(self.sid, self.identity)['centrales']['presentacion'])

    def test_consultar_final_and_expired_intents_have_no_presentation(self):
        self.accept()
        with patch.object(service.timezone, 'now', return_value=self.now + timedelta(minutes=11)):
            self.assertIsNone(service.consultar(self.sid, self.identity)['centrales']['presentacion'])
        self.record(self.result())
        safe = service.consultar(self.sid, self.identity)['centrales']
        self.assertIsNone(safe['presentacion'])
        self.assertEqual(service._fecha(safe['vigenteHasta']), self.until)

    def test_feature_flag_is_literal_true_and_read_remains_available(self):
        for flag in (False, 1, 'true'):
            with self.subTest(flag=flag), override_settings(CREDIHOY_CENTRALES_OTP_ENABLED=flag):
                with self.assertRaises(OperacionError):
                    self.present()
                with self.assertRaises(OperacionError):
                    service.aceptar(self.sid, self.identity, self.operation, '', True)
                with self.assertRaises(OperacionError):
                    service.registrar_resultado(self.sid, self.identity, '')
                self.assertEqual(service.consultar(self.sid, self.identity)['centrales']['estado'], 'SIN_ACEPTACION')

    def test_autentic_delegates_to_existing_verifier_without_mutation(self):
        verified = SimpleNamespace(referencia_verificacion='REAL_VERIFIER_REFERENCE',
            original_sha256='e' * 64, firmado_en=self.now, vigente_hasta=self.until)
        with patch.object(service, 'verificar_autorizacion_crediticia_personal', return_value=verified) as verifier:
            result = service.verificar_autorizacion_centrales_personal(self.case, self.consent)
        verifier.assert_called_once_with(self.case, self.consent)
        self.assertEqual(result.metodo, 'AUTENTIC')
        self.assertEqual(self.initial, (self.case.metadatos, self.detail.form_data, vars(self.consent)))

    def test_rejected_state_is_final_and_cannot_be_replaced_by_confirmation(self):
        self.accept()
        rejected = self.result('RECHAZADA', estadoProveedor=None)
        self.record(rejected)
        self.record(rejected)
        with self.assertRaises(ValueError):
            self.record(self.result())
        self.assertIsNone(service.consultar(self.sid, self.identity)['centrales']['presentacion'])

    def test_owner_adapter_safe_projection_matches_relay_ui_contract(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import consentimiento_real, publico_institucional
        self.identity['canal'] = 'SMS'
        base = {'operacion': 'autorizacion', 'solicitudId': self.sid}
        view = consentimiento_real.operar_autogestion(self.identity, {
            **base, 'accion': 'centrales_presentar', 'operacionId': self.operation})
        self.assertEqual(publico_institucional.project_authorization(view), view)
        accepted = consentimiento_real.operar_autogestion(self.identity, {
            **base, 'accion': 'centrales_enviar', 'operacionId': self.operation,
            'presentacion': view['centrales']['presentacion'], 'acepta': True})
        self.assertEqual(publico_institucional.project_authorization(accepted), accepted)
        self.assertNotIn('vinculo', accepted)
        self.assertEqual(service._cargar_token(accepted['centrales']['presentacion'], service.SALT_INTENCION)['operacionId'], self.operation)
        safe = consentimiento_real.operar_autogestion(self.identity, {**base, 'accion': 'centrales_consultar'})
        self.assertEqual(publico_institucional.project_authorization(safe), safe)
        self.accepted = {'vinculo': service._cargar_token(accepted['centrales']['presentacion'], service.SALT_INTENCION)}
        token = service._token(self.result(), service.SALT_PROVEEDOR)
        final = consentimiento_real.operar_autogestion(self.identity, {
            **base, 'accion': 'centrales_resultado', 'comprobacion': token})
        self.assertEqual(publico_institucional.project_authorization(final), final)
        self.assertIsNone(final['centrales']['presentacion'])
        self.assertFalse(final['firmado'])
        self.assertFalse(final['consultasHabilitadas'])
        self.assertFalse(final['evaluacionHabilitada'])

    def test_reconciliation_after_unknown_send_preserves_reference_once_known(self):
        self.accept()
        self.record(self.result('INDETERMINADA', verificationReferencia=None))
        self.record(self.result('ENVIADO'))
        self.record(self.result('INDETERMINADA', verificationReferencia=None))
        self.record(self.result('ENVIADO'))
        with self.assertRaises(ValueError):
            self.record(self.result(verificationReferencia='c' * 64))
        self.record(self.result())
        service._leer_journal(self.case, self.consent)

    def test_confirmation_exactly_at_intent_limit_is_verifiable_afterwards(self):
        self.accept()
        self.record(self.result())
        journal = self.detail.orchestration_data[service.KEY]
        proof = journal['comprobante']
        proof['payload']['confirmadoEn'] = journal['vinculo']['vigenteHasta']
        proof['firmaBase64'] = base64.b64encode(
            service._clave_owner().sign(service._mensaje_evidencia(proof['payload']))).decode()
        with patch.object(service.timezone, 'now', return_value=self.now + timedelta(minutes=15)):
            self.assertEqual(self.backend_verify().confirmado_en, service._fecha(journal['vinculo']['vigenteHasta']))

    def test_owner_rejects_mismatched_configured_public_key_before_persisting_confirmation(self):
        self.accept()
        before = deepcopy(self.detail.orchestration_data)
        with override_settings(CREDIHOY_CENTRALES_CLAVE_PUBLICA_B64=base64.b64encode(b'z' * 32).decode()):
            from cryptography.exceptions import InvalidSignature
            with self.assertRaises(InvalidSignature):
                self.record(self.result())
        self.assertEqual(before, self.detail.orchestration_data)
        self.assertEqual(self.audit.create.call_count, 1)

    def test_presentation_rejects_recomputed_snapshot_not_matching_real_pdf(self):
        self.consent.text_snapshot += ' CHANGED'
        self.consent.text_hash = build_consent_hash(self.consent.version, self.consent.text_snapshot)
        with self.assertRaisesMessage(ValueError, 'SNAPSHOT_NO_CORRESPONDE_AL_PDF'):
            self.present()
        self.detail.save.assert_not_called()
