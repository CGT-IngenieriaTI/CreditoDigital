"""Synthetic offer/PDF binding; no provider or bank operations."""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from pypdf import PdfReader
from apps.documentos import formularios_credihoy as documents
from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import SolicitudConsumo


class OfertaPdfLocalTests(TestCase):
    def setUp(self):
        self.actor=get_user_model().objects.create(username='synthetic-pdf',is_staff=True,is_superuser=True)
        holder=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='0000000000',
            primer_apellido='SYNTHETIC',fecha_expedicion=date(2000,1,1))
        self.case=Solicitud.objects.create(solicitante=holder,producto='CREDIHOY UNICO (NUEVO)',asesor=self.actor,
            monto_solicitado='400000',plazo_solicitado=2)
        SolicitudConsumo.objects.create(solicitud=self.case,orchestration_data={'baseline':'UNCHANGED'})
        self.fields={k:{'valor':v,'referencia':'SYNTHETIC_ONLY','verificado':True} for k,v in {
            'identificacion':'0000000000','nombre':'ASOCIADO SINTETICO','monto':'400000','plazo':'2',
            'tasaEA':'18.01','periodicidad':'MENSUAL','primerVencimiento':'2026-12-10'}.items()}
        now=timezone.now()
        self.offer={'sha256':'a'*64,'versionContrato':'credihoy-oferta-cuota-nativa-v1',
            'solicitudId':str(self.case.pk),'titularReferencia':sha256(b'0000000000').hexdigest(),
            'versionCondiciones':'b'*64,'evaluacionSha256':'c'*64,'calculoRequestId':str(uuid4()),
            'condiciones':{'monto':'400000','numeroCuotas':2,'periodicidad':'MENSUAL','tasaNominalNamv':'16.67'},
            'cuotaContractual':'204448.1234567','desde':now.isoformat(),'hasta':(now+timedelta(hours=1)).isoformat(),
            'seguroVida':{'porcentajeMensualParametrizado':'0.090','gestionImporte':'MOTOR_LINIX_SIN_PRIMA_ADICIONAL_DEL_CANAL'}}
        self.quote={'solicitudId':str(self.case.pk),'versionCondiciones':'b'*64,
            'condicionesSolicitadas':self.offer['condiciones'],'tasaEaPorcentaje':'18.01',
            'primeraFechaPago':'2026-12-10','composicionSha256':'a'*64,'ofertaVersionada':self.offer}
        p=patch.object(documents,'_exigir_condiciones_definitivas',side_effect=lambda case:deepcopy(self.quote))
        p.start();self.addCleanup(p.stop)
        p=patch('requests.sessions.Session.request',side_effect=AssertionError('NO_NETWORK'))
        p.start();self.addCleanup(p.stop)

    def render(self,fields=None):
        return documents.diligenciar_para_expediente(self.case.pk,actor=self.actor,codigo='COLO-FO.001',
            campos=self.fields if fields is None else fields)

    def custody(self,key):
        return documents.custodiar_formato(self.case.pk,actor=self.actor,codigo='COLO-FO.001',
            campos=self.fields,clave_idempotencia=key)

    def test_direct_generation_rejects_amount_different_from_offer(self):
        fields=deepcopy(self.fields);fields['monto']['valor']='400001'
        with self.assertRaisesRegex(ValueError,'CAMPOS_NO_CORRESPONDEN'):self.render(fields)

    def test_official_contract_stays_three_pages_with_offer_binding_outside_pdf(self):
        pdf,meta=self.render()
        reader=PdfReader(BytesIO(pdf))
        self.assertEqual(len(reader.pages),3)
        fields=reader.get_fields()
        self.assertEqual(str(fields[documents.MAPA['COLO-FO.001']['identificacion']]['/V']),'0000000000')
        self.assertEqual(str(fields[documents.MAPA['COLO-FO.001']['monto']]['/V']),'400000')
        self.assertEqual(meta['ofertaSha256'],self.offer['sha256'])
        self.assertEqual(meta['calculoRequestId'],self.offer['calculoRequestId'])
        self.assertFalse(meta['firmaAcreditada']);self.assertFalse(meta['envioHabilitado'])
        self.assertEqual(meta['condicionesOferta']['cuotaContractual'],'204448.1234567')
        self.assertFalse(meta['anexoIncorporado']);self.assertNotIn('anexoOferta',meta)

    def test_graphical_signatures_cannot_enter_official_form_mapping(self):
        for name in ('firma','firmaImagen','firmaTitular'):
            fields=deepcopy(self.fields)
            fields[name]={'valor':'NOT_A_SIGNATURE','referencia':'SYNTHETIC_ONLY','verificado':True}
            with self.assertRaisesRegex(ValueError,'CAMPO_NO_MAPEADO'):self.render(fields)

    def test_custody_replay_is_byte_stable_and_has_one_audit(self):
        key=uuid4();first=self.custody(key);self.assertEqual(first,self.custody(key))
        pdf,meta=documents.recuperar_formato(self.case.pk,actor=self.actor,operacion_id=key)
        self.assertEqual(sha256(pdf).hexdigest(),meta['pdfSha256'])
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_PDF_OFICIAL_CUSTODIADO').count(),1)

    def test_new_offer_cannot_reuse_previous_document_key(self):
        key=uuid4();self.custody(key);self.offer['sha256']='d'*64;self.quote['composicionSha256']='d'*64
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCIA_DOCUMENTO_CONFLICTO'):self.custody(key)

    def test_custody_metadata_tampering_is_rejected(self):
        key=uuid4();self.custody(key);detail=SolicitudConsumo.objects.get(solicitud=self.case)
        detail.orchestration_data['credihoy_pdf_oficiales_v1'][str(key)]['metadata']['pdfSha256']='f'*64
        detail.save()
        with self.assertRaisesRegex(ValueError,'CUSTODIA_DOCUMENTAL_NO_INTEGRA'):
            documents.recuperar_formato(self.case.pk,actor=self.actor,operacion_id=key)

    def test_local_pdf_does_not_change_offer_decision_or_create_acceptance(self):
        from apps.xcore_consumo.models import ConsentimientoConsumo,EvaluacionConsumo
        before=deepcopy(self.offer);key=uuid4();self.custody(key)
        detail=SolicitudConsumo.objects.get(solicitud=self.case)
        self.assertEqual(detail.orchestration_data['baseline'],'UNCHANGED')
        self.assertEqual(self.offer,before)
        self.assertFalse(ConsentimientoConsumo.objects.exists());self.assertFalse(EvaluacionConsumo.objects.exists())
        stored=detail.orchestration_data['credihoy_pdf_oficiales_v1'][str(key)]
        self.assertNotIn('pdfBase64',stored)
        self.assertEqual(stored['metadata']['paginasInstitucionales'],3)
        self.assertFalse(stored['metadata']['anexoIncorporado'])

    def test_wrong_operator_and_offer_holder_are_rejected(self):
        from django.core.exceptions import PermissionDenied
        actor=get_user_model().objects.create(username='synthetic-foreign-pdf',is_staff=True)
        with self.assertRaises(PermissionDenied):
            documents.diligenciar_para_expediente(self.case.pk,actor=actor,codigo='COLO-FO.001',campos=self.fields)
        self.offer['titularReferencia']='f'*64
        with self.assertRaisesRegex(ValueError,'OFERTA_DOCUMENTAL_DE_OTRO_CONTEXTO'):self.render()

    def test_missing_or_stale_contract_does_not_fall_back_to_estimated_quota(self):
        with patch.object(documents,'_exigir_condiciones_definitivas',side_effect=ValueError('OFERTA_NO_VIGENTE')):
            with self.assertRaisesRegex(ValueError,'OFERTA_NO_VIGENTE'):self.render()
        self.assertNotIn('credihoy_pdf_oficiales_v1',SolicitudConsumo.objects.get(solicitud=self.case).orchestration_data)

    def test_existing_autentic_builder_uses_exact_custodied_pdf_without_sending(self):
        import base64
        key=uuid4();self.custody(key)
        pdf,meta=documents.recuperar_formato(self.case.pk,actor=self.actor,operacion_id=key)
        metadata={'names':'ASOCIADO','lastNames':'SINTETICO','docId':'0000000000','secureKey':'SYNTHETIC_NOT_OTP'}
        request,proof=documents.preparar_autentic_para_expediente(self.case.pk,actor=self.actor,codigo='COLO-FO.001',
            campos=self.fields,metadata=metadata)
        self.assertEqual(base64.b64decode(request.payload['files'][0]['fileContent']),pdf)
        self.assertEqual(proof['pdfSha256'],meta['pdfSha256'])
        self.assertEqual(proof['ofertaSha256'],meta['ofertaSha256'])
        self.assertEqual(proof['solicitudAutenticFingerprint'],request.fingerprint)
        self.assertFalse(proof['envioHabilitado']);self.assertFalse(proof['pagareIncluido'])


from django.test import TransactionTestCase


class OfertaPdfEnvioLocalTests(TransactionTestCase):
    setUp=OfertaPdfLocalTests.setUp
    custody=OfertaPdfLocalTests.custody

    def test_three_page_contract_cannot_be_sent_without_personal_act_and_contractual_otp(self):
        import sqlite3
        from unittest.mock import Mock
        from apps.integraciones.autentic.cliente import AutenticDEVClient
        from apps.integraciones.autentic.contratos import ConfiguracionDEV
        from apps.integraciones.autentic.operaciones import RegistroFirmas
        key=uuid4();self.custody(key)
        client=AutenticDEVClient(ConfiguracionDEV('SYNTHETIC','SYNTHETIC',permitir_documentos=True))
        self.addCleanup(client.close);client.firmar=Mock(side_effect=AssertionError('NO_SEND'))
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        with self.assertRaisesRegex(ValueError,'FIRMA_CONTRACTUAL_REQUIERE_ACTO_PERSONAL_Y_OTP'):
            documents.enviar_formato_custodiado(self.case.pk,actor=self.actor,operacion_id=key,
                metadata={'names':'ASOCIADO','lastNames':'SINTETICO','docId':'0000000000','secureKey':'SYNTHETIC_NOT_OTP'},
                clave_idempotencia=uuid4(),cliente=client,
                registro=RegistroFirmas(db),referencia_autorizacion='SYNTHETIC_ONLY')
        client.firmar.assert_not_called()
        self.assertNotIn('credihoy_autentic_envios_v1',SolicitudConsumo.objects.get(solicitud=self.case).orchestration_data)
