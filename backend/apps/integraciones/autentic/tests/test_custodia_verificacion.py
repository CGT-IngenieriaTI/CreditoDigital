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


@override_settings(OTP_AES_KEY_B64=base64.b64encode(b's'*32).decode())
class ContractRecoveryTests(TransactionTestCase):
    setUp=CustodyTests.setUp

    def contract(self):
        self.entry['vinculo']['codigo']='COLO-FO.001'
        native={'solicitudId':str(self.case.pk),'SC':'90002','K_FLUJO':'90001','intencionId':str(uuid4()),
            'ofertaSha256':'a'*64,'aceptacionSha256':'b'*64,'calendarioSha256':'c'*64}
        native['sha256']=sha256(json.dumps(native,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.entry['formalizacion']=native
        self.detail.orchestration_data['credihoy_autentic_envios_v1'][self.key]=self.entry
        self.detail.save(update_fields=['orchestration_data'])

    def provider_client(self):
        from ..cliente import AutenticDEVClient
        from ..contratos import ConfiguracionDEV
        return AutenticDEVClient(ConfiguracionDEV('SYNTHETIC','SYNTHETIC_SECRET',permitir_documentos=True,
            hosts_descarga=('synthetic.example.invalid',)))

    def recover(self):
        from apps.documentos.formularios_credihoy import continuar_formalizacion_autentic
        client=self.provider_client()
        try:
            with patch('requests.sessions.Session.request',side_effect=AssertionError('NO_PROVIDER')):
                return continuar_formalizacion_autentic(self.case.pk,actor=self.actor,envio_id=self.key,
                    cliente=client,referencia_autorizacion='SYNTHETIC_ONLY')
        finally:client.close()

    def test_indeterminate_delivery_is_persisted_once_without_get_or_resend(self):
        self.entry.update(estado='INDETERMINADA',reciboCifrado=None)
        self.contract()
        first=self.recover();second=self.recover()
        self.assertEqual(first,second)
        self.assertEqual(first['estado'],'PENDIENTE_CONCILIACION_PROVEEDOR')
        self.assertFalse(first['firmaAcreditada'])
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_FORMALIZACION_PENDIENTE').count(),1)
        self.assertFalse(ConsentimientoConsumo.objects.get(solicitud=self.case).firmado)

    def test_unsigned_download_is_recoverable_pending_not_contractual_success(self):
        self.contract()
        result=self.recover()
        self.assertEqual(result['estado'],'PENDIENTE_VERIFICACION')
        self.assertFalse(result['firmaAcreditada'])
        self.assertEqual(result['START'],0)

    def test_existing_contract_delivery_replays_without_requoting_after_its_original_window(self):
        import sqlite3
        from apps.documentos import formularios_credihoy as forms
        from ..contratos import preparar_firma
        from ..operaciones import RegistroFirmas
        metadata={'names':'SYNTHETIC','lastNames':'ONLY','docId':'SYNTHETIC-ID','secureKey':'SYNTHETIC_ONLY'}
        request=preparar_firma(metadata,{'COLO-FO.001.pdf':self.pdf})
        self.entry['vinculo'].update(codigo='COLO-FO.001',fingerprint=request.fingerprint,
            referenciaAutorizacion='SYNTHETIC_ONLY')
        self.detail.orchestration_data['credihoy_autentic_envios_v1'][self.key]=self.entry
        self.detail.save(update_fields=['orchestration_data'])
        client=self.provider_client()
        document={**self.entry['vinculo'],'identidadDocumentalReferencia':sha256(b'SYNTHETIC-ID').hexdigest()}
        journal=sqlite3.connect(':memory:')
        try:
            with patch.object(forms,'recuperar_formato',return_value=(self.pdf,document)),\
                    patch.object(forms,'_exigir_condiciones_definitivas',side_effect=AssertionError('NO_NEW_QUOTE')),\
                    patch.object(client,'firmar',side_effect=AssertionError('NO_NEW_SIGNATURE')):
                result=forms.enviar_formato_custodiado(self.case.pk,actor=self.actor,
                    operacion_id=self.context['documentoOperacionId'],metadata=metadata,clave_idempotencia=self.key,
                    cliente=client,registro=RegistroFirmas(journal),referencia_autorizacion='SYNTHETIC_ONLY')
            self.assertEqual(result['estado'],'FIRMA_REPORTADA_NO_VERIFICADA')
            self.assertFalse(result['reenvioPermitido'])
        finally:journal.close();client.close()

    def signed_contract(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from .test_verificacion_documental import CryptographyTests
        CryptographyTests.setUpClass();fixture=CryptographyTests()
        original=fixture.original;signed=fixture.timestamped();now=datetime.now(timezone.utc)
        ctx=context(original,signed,now)
        for value in (ctx,ctx['vinculo'],ctx['descarga']):value['solicitudId']=str(self.case.pk)
        doc=ctx['documentoOperacionId'];self.key=ctx['envioId']
        meta={'codigo':'COLO-FO.001','solicitudId':str(self.case.pk),'pdfSha256':sha256(original).hexdigest(),
            'identidadDocumentalReferencia':sha256(b'SYNTHETIC-ID').hexdigest()}
        envelope={'solicitudId':str(self.case.pk),'operacionId':doc,'metadata':meta,
            'pdfBase64':base64.b64encode(original).decode()}
        self.entry={'vinculo':ctx['vinculo'],'estado':ctx['estado'],'preparadoEn':ctx['preparadoEn'],
            'reciboCifrado':encrypt_text(json.dumps(ctx['recibo'])),
            'descargaCifrada':encrypt_text(json.dumps({**ctx['descarga'],'pdfBase64':base64.b64encode(signed).decode()}))}
        self.detail.orchestration_data={'credihoy_pdf_oficiales_v1':{doc:{'metadata':meta,
            'contenidoCifrado':encrypt_text(json.dumps(envelope))}},'credihoy_autentic_envios_v1':{}}
        self.contract()
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        paths=[]
        for name,raw in [('root.der',fixture.roots[0]),('revocation.crl',fixture.crls[0])]:
            path=root/name;path.write_bytes(raw);paths.append({'ruta':str(path),'sha256':sha256(raw).hexdigest()})
        opts=override_settings(CREDIHOY_AUTENTIC_PDF_TRUST_ROOTS=[paths[0]],CREDIHOY_AUTENTIC_PDF_CRLS=[paths[1]],
            CREDIHOY_AUTENTIC_PDF_POLITICAS_TITULAR=[{'raizSha256':paths[0]['sha256'],
                'politicaOid':'1.3.6.1.4.1.55555.9','referencia':'SYNTHETIC_CPS_NOT_INSTITUTIONAL'}])
        opts.enable();self.addCleanup(opts.disable)
        return signed

    def test_signed_document_is_cryptographically_verified_custodied_and_recovered_after_restart(self):
        self.signed_contract()
        first=self.recover();second=self.recover()
        self.assertEqual(first,second)
        self.assertTrue(first['firmaAcreditada'],first)
        self.assertEqual(first['estado'],'FIRMA_CONTRACTUAL_VERIFICADA')
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_FORMALIZACION_FIRMA_VERIFICADA').count(),1)
        from apps.documentos.formularios_credihoy import presentar_formalizacion_autentic
        public=presentar_formalizacion_autentic(self.case)
        self.assertEqual(public['estado'],'DOCUMENTO_VERIFICADO')
        self.assertTrue(public['continuacionInstitucionalPendiente'])
        self.assertNotIn('urlDocuments',json.dumps(public))
        self.assertFalse(ConsentimientoConsumo.objects.get(solicitud=self.case).firmado)
        self.assertEqual(EvaluacionConsumo.objects.count(),0)

    def test_supervised_technical_recovery_is_scoped_and_cannot_be_reused_as_document_access(self):
        from django.db import connection
        from django.contrib.auth.models import Permission
        from apps.documentos.formularios_credihoy import recuperar_firmas_contractuales,recuperar_formato
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL required for JSONB and durable recovery')
        self.signed_contract()
        operator=get_user_model().objects.create(username='synthetic-autentic-recovery',is_staff=True)
        operator.set_unusable_password();operator.save()
        operator.user_permissions.add(*Permission.objects.filter(codename__in=[
            'view_solicitud','add_eventodocumental','view_versiondocumental']))
        client=self.provider_client()
        try:
            with override_settings(CREDIHOY_AUTENTIC_RECUPERACION_ENABLED=True,
                    CREDIHOY_AUTENTIC_OPERADOR=operator.username,CREDIHOY_AUTENTIC_AMBIENTE='STAGING',
                    CREDIHOY_AUTENTIC_CONFIG_FILE='SYNTHETIC_SECRET_REFERENCES_ONLY',
                    CREDIHOY_AUTENTIC_HOSTS_DESCARGA=('synthetic.example.invalid',),
                    CREDIHOY_AUTENTIC_REFERENCIA='SYNTHETIC_ONLY'),\
                    patch('requests.sessions.Session.request',side_effect=AssertionError('NO_PROVIDER')),\
                    patch('apps.integraciones.autentic.cliente.cargar_cliente_recuperacion',return_value=client):
                from apps.xcore_consumo.tasks import recuperar_firmas_contractuales as task
                result=task.run();repeat=task.run()
            self.assertEqual(result['estados'],['FIRMA_CONTRACTUAL_VERIFICADA'],result)
            self.assertEqual(repeat['procesadas'],0)
            with self.assertRaises(PermissionDenied):
                recuperar_formato(self.case.pk,actor=operator,operacion_id=self.context['documentoOperacionId'])
        finally:client.close()

    def test_closed_task_neither_loads_credentials_nor_creates_a_delivery(self):
        from apps.xcore_consumo.tasks import recuperar_firmas_contractuales as task
        with override_settings(CREDIHOY_AUTENTIC_RECUPERACION_ENABLED=False),\
                patch('apps.integraciones.autentic.cliente.cargar_cliente_recuperacion',
                    side_effect=AssertionError('NO_CREDENTIAL_LOAD')):
            self.assertEqual(task.run(),{'estado':'RECUPERACION_NO_HABILITADA','procesadas':0})
        self.assertEqual(AuditLog.objects.count(),0)
