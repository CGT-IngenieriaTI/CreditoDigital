"""Disposable ORM evidence only; no provider operations, real identities or signatures."""
import base64
from datetime import date,datetime,timezone
from hashlib import sha256
import json
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase,TransactionTestCase,override_settings
from apps.documentos.formularios_credihoy import inspeccionar_evidencia_custodiada
from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import ConsentimientoConsumo,SolicitudConsumo,EvaluacionConsumo
from apps.xcore_consumo.services.otp_crypto import encrypt_text,decrypt_text
from .test_verificacion_documental import pdf,context


@override_settings(OTP_AES_KEY_B64=base64.b64encode(b's'*32).decode())
class CustodyTests(TestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create(username='synthetic-reviewer',is_staff=True,is_superuser=True)
        holder=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='SYNTHETIC-ID',
            primer_apellido='SYNTHETIC',fecha_expedicion=date(2000,1,1),celular='SYNTHETIC',email='synthetic@example.invalid')
        self.case=Solicitud.objects.create(solicitante=holder,producto='CREDIHOY UNICO (NUEVO)',
            metadatos={'modo':'AUTOGESTION_ENVIADA'},asesor=None)
        self.pdf=pdf();self.context=context(self.pdf,self.pdf,datetime.now(timezone.utc))
        self.context['solicitudId']=str(self.case.pk)
        self.context['vinculo']['solicitudId']=str(self.case.pk)
        self.context['descarga']['solicitudId']=str(self.case.pk)
        self.key=self.context['envioId'];doc=self.context['documentoOperacionId']
        meta={'codigo':'COLO-FO.023','solicitudId':str(self.case.pk),'pdfSha256':sha256(self.pdf).hexdigest(),
            'identidadDocumentalReferencia':sha256(b'SYNTHETIC-ID').hexdigest()}
        envelope={'solicitudId':str(self.case.pk),'operacionId':doc,'metadata':meta,
            'pdfBase64':base64.b64encode(self.pdf).decode()}
        download={**self.context['descarga'],'pdfBase64':base64.b64encode(self.pdf).decode()}
        self.entry={'vinculo':self.context['vinculo'],'estado':self.context['estado'],
            'preparadoEn':self.context['preparadoEn'],'reciboCifrado':encrypt_text(json.dumps(self.context['recibo'])),
            'descargaCifrada':encrypt_text(json.dumps(download))}
        self.detail=SolicitudConsumo.objects.create(solicitud=self.case,estado='CONSENTIMIENTO_PENDIENTE',
            orchestration_data={'credihoy_pdf_oficiales_v1':{doc:{'metadata':meta,
                'contenidoCifrado':encrypt_text(json.dumps(envelope))}},'credihoy_autentic_envios_v1':{self.key:self.entry}})
        ConsentimientoConsumo.objects.create(solicitud=self.case,aceptado=True,firmado=False,
            fecha_aceptacion=datetime.now(timezone.utc),text_snapshot='SYNTHETIC',text_hash='synthetic')

    def inspect(self):
        with patch('requests.sessions.Session.request',side_effect=AssertionError('NO_HTTP')):
            return inspeccionar_evidencia_custodiada(self.case.pk,actor=self.actor,envio_id=self.key)

    def test_audit_and_encrypted_result_are_idempotent_without_changing_consent_or_case(self):
        first=self.inspect();second=self.inspect()
        self.assertEqual(first['estado'],'PENDIENTE_VERIFICACION')
        self.detail.refresh_from_db();self.case.refresh_from_db()
        entry=self.detail.orchestration_data['credihoy_autentic_envios_v1'][self.key]
        self.assertNotIn('PENDIENTE_VERIFICACION',entry['verificacionCifrada'])
        self.assertEqual(json.loads(decrypt_text(entry['verificacionCifrada']))['estado'],first['estado'])
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_AUTENTIC_EVIDENCIA_INSPECCIONADA').count(),1)
        self.assertFalse(ConsentimientoConsumo.objects.get(solicitud=self.case).firmado)
        self.assertIsNone(self.case.asesor_id);self.assertEqual(self.case.metadatos['modo'],'AUTOGESTION_ENVIADA')
        self.assertEqual(EvaluacionConsumo.objects.count(),0)
        self.assertEqual(entry['estado'],self.entry['estado'])

    def test_foreign_signed_document_is_audited_invalid_not_signed(self):
        value=json.loads(decrypt_text(self.entry['descargaCifrada']));value['pdfSha256']='f'*64
        self.entry['descargaCifrada']=encrypt_text(json.dumps(value))
        self.detail.orchestration_data['credihoy_autentic_envios_v1'][self.key]=self.entry
        self.detail.save(update_fields=['orchestration_data'])
        self.assertEqual(self.inspect()['estado'],'INVALIDA')
        self.assertFalse(ConsentimientoConsumo.objects.get(solicitud=self.case).firmado)

    def test_missing_response_or_download_is_pending_and_does_not_call_provider(self):
        self.entry['reciboCifrado']=None
        self.detail.orchestration_data['credihoy_autentic_envios_v1'][self.key]=self.entry
        self.detail.save(update_fields=['orchestration_data'])
        self.assertEqual(self.inspect()['estado'],'PENDIENTE_VERIFICACION')
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_AUTENTIC_EVIDENCIA_INSPECCIONADA').count(),1)

    def test_without_documentary_permissions_cannot_read_or_persist_evidence(self):
        actor=get_user_model().objects.create(username='synthetic-no-permissions',is_staff=True)
        with self.assertRaises(PermissionDenied):
            inspeccionar_evidencia_custodiada(self.case.pk,actor=actor,envio_id=self.key)
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_AUTENTIC_EVIDENCIA_INSPECCIONADA').count(),0)


@override_settings(OTP_AES_KEY_B64=base64.b64encode(b's'*32).decode())
class DiagnosticCustodyTests(TransactionTestCase):
    setUp=CustodyTests.setUp

    def attempt(self,*,cipher_failure=False):
        import sqlite3
        from contextlib import closing
        from apps.documentos.formularios_credihoy import enviar_formato_custodiado
        from apps.integraciones.autentic.cliente import AutenticDEVClient
        from apps.integraciones.autentic.contratos import ConfiguracionDEV
        from apps.integraciones.autentic.operaciones import RegistroFirmas
        from .test_cliente import Session,Response,token
        doc=self.context['documentoOperacionId']
        self.detail.orchestration_data['credihoy_autentic_envios_v1']={}
        self.detail.save(update_fields=['orchestration_data'])
        session=Session(Response(token()),Response({'operationCode':2000,
            'operationMsg':'PDF header signature not found.','docId':'PRIVATE_ID'},400))
        client=AutenticDEVClient(ConfiguracionDEV('SYNTHETIC','SYNTHETIC_SECRET',permitir_documentos=True),session=session)
        key=str(uuid4())
        with closing(sqlite3.connect(':memory:')) as journal:
            kwargs={'actor':self.actor,'operacion_id':doc,'metadata':{'names':'SYNTHETIC','lastNames':'ONLY',
                'docId':'SYNTHETIC-ID','secureKey':'SYNTHETIC_NOT_REAL'},'clave_idempotencia':key,
                'cliente':client,'registro':RegistroFirmas(journal),'referencia_autorizacion':'SYNTHETIC_ONLY'}
            if cipher_failure:
                with patch('apps.xcore_consumo.services.otp_crypto.encrypt_text',side_effect=ValueError('PRIVATE_KEY_ERROR')):
                    result=enviar_formato_custodiado(self.case.pk,**kwargs)
            else:result=enviar_formato_custodiado(self.case.pk,**kwargs)
            replay=enviar_formato_custodiado(self.case.pk,**kwargs)
        self.assertEqual(len(session.calls),2)
        self.assertFalse(result['firmaAcreditada']);self.assertFalse(replay['reenvioPermitido'])
        self.detail.refresh_from_db()
        return self.detail.orchestration_data['credihoy_autentic_envios_v1'][key]

    def test_http_error_is_encrypted_in_existing_dossier_without_retry_or_fake_signature(self):
        entry=self.attempt()
        self.assertEqual(entry['estado'],'INDETERMINADA')
        self.assertEqual(entry['diagnosticoEstado'],'CUSTODIADO_REDACTADO')
        private=json.loads(decrypt_text(entry['diagnosticoHttpCifrado']))
        self.assertEqual(private['eventos'][-1]['http'],400)
        self.assertNotIn('PRIVATE_ID',json.dumps(private))
        self.assertFalse(ConsentimientoConsumo.objects.get(solicitud=self.case).firmado)

    def test_cipher_failure_remains_explicitly_pending_without_reopening_operation(self):
        entry=self.attempt(cipher_failure=True)
        self.assertEqual(entry['estado'],'INDETERMINADA')
        self.assertEqual(entry['diagnosticoEstado'],'CUSTODIA_DIAGNOSTICO_PENDIENTE')
        self.assertIsNone(entry['diagnosticoHttpCifrado'])


@override_settings(OTP_AES_KEY_B64=base64.b64encode(b's'*32).decode())
class PostgreSQLJournalTests(TransactionTestCase):
    setUp=CustodyTests.setUp

    def require_postgres(self):
        from django.db import connection
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL local requerido; no acredita SQLite')

    def test_concurrent_durable_claims_admit_only_one_attempt(self):
        self.require_postgres()
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connections
        from apps.integraciones.autentic.operaciones import RegistroFirmasExpediente
        from apps.integraciones.autentic.contratos import AutenticError
        key=self.key;reference=str(self.case.pk)
        def attempt(_):
            try:
                registry=RegistroFirmasExpediente(reference,key)
                registry.preparar(key,'a'*64)
                try:registry.iniciar(key)
                except AutenticError:return 'BLOQUEADA'
                registry.terminar(key,'INDETERMINADA');return 'RESERVADA'
            finally:connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(sorted(results),['BLOQUEADA','RESERVADA'])
        self.detail.refresh_from_db()
        state=self.detail.orchestration_data['credihoy_autentic_envios_v1'][key]['journalFirma']
        self.assertEqual(state['estado'],'INDETERMINADA')
        self.assertFalse(ConsentimientoConsumo.objects.get(solicitud=self.case).firmado)

    def test_new_journal_instance_or_new_key_cannot_reopen_indeterminate_send(self):
        self.require_postgres()
        from apps.integraciones.autentic.operaciones import RegistroFirmasExpediente
        from apps.integraciones.autentic.contratos import AutenticError
        registry=RegistroFirmasExpediente(self.case.pk,self.key)
        registry.preparar(self.key,'a'*64);registry.iniciar(self.key);registry.terminar(self.key,'INDETERMINADA')
        fresh=RegistroFirmasExpediente(self.case.pk,self.key)
        self.assertEqual(fresh.preparar(self.key,'a'*64).estado,'INDETERMINADA')
        with self.assertRaises(AutenticError):fresh.iniciar(self.key)
        with self.assertRaises(AutenticError):fresh.preparar(str(uuid4()),'a'*64)
