"""Independent document over the existing personal OTP/custody/sender protocol."""
import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

from django.core import signing
from django.db import transaction
from django.test import TransactionTestCase,override_settings
from django.utils import timezone
from reportlab.pdfgen import canvas

from apps.documentos import formularios_credihoy as forms
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import SolicitudConsumo
from apps.xcore_consumo.services.credihoy_v2.unico import consentimiento_real as service
from . import test_custodia_otp_contractual as fixtures


@override_settings(CREDIHOY_OTP_CONTRACTUAL_ENABLED=True,CREDIHOY_OTP_CONTRACTUAL_ENVIO_ENABLED=True,
    CREDIHOY_OTP_CONTRACTUAL_DB_ALIAS='default',CREDIHOY_OTP_CONTRACTUAL_VERIFY_SID=fixtures.SERVICE_SID,
    CREDIHOY_OTP_CONTRACTUAL_HMAC_KEY=fixtures.HMAC_KEY,CREDIHOY_TITULAR_SECRET=fixtures.SIGNING_KEY,
    CREDIHOY_INSTITUCIONAL_SECRET='different-synthetic-institutional-key',
    CREDIHOY_PUBLICO_VERIFY_SID='VA'+'c'*32,TWILIO_VERIFY_SID='VA'+'d'*32,
    OTP_AES_KEY_B64=base64.b64encode(b'c'*32).decode())
class IndependentDocumentTests(TransactionTestCase):
    present=fixtures.ContractualOTPTests.present
    accept=fixtures.ContractualOTPTests.accept
    send_otp=fixtures.ContractualOTPTests.send_otp
    verify=fixtures.ContractualOTPTests.verify
    full_act=fixtures.ContractualOTPTests.full_act
    record=fixtures.ContractualOTPTests.record

    def setUp(self):
        fixtures.ContractualOTPTests.setUp(self)
        self.original_doc=self.doc;self.original_sha=self.pdf_sha;self.ambiguous=str(uuid4())
        self.original_entry={'vinculo':{'solicitudId':str(self.case.pk),'documentoOperacionId':self.original_doc,
            'pdfSha256':self.original_sha,'codigo':'COLO-FO.001','ambienteFirma':'STAGING',
            'firmaProductivaAcreditada':False},'estado':'INDETERMINADA','diagnosticoHttpCifrado':'PRESERVED'}
        self.detail.orchestration_data['credihoy_autentic_envios_v1']={self.ambiguous:deepcopy(self.original_entry)}
        self.detail.save(update_fields=['orchestration_data']);self.original_data=deepcopy(self.detail.orchestration_data)
        self.doc=str(uuid4());stream=BytesIO();writer=canvas.Canvas(stream)
        writer.drawString(20,800,'INDEPENDENT SYNTHETIC TEST - NO CREDIT EFFECT');writer.showPage();writer.save()
        self.pdf=stream.getvalue();self.pdf_sha=sha256(self.pdf).hexdigest()
        self.meta=dict(codigo='PRUEBA-FIRMA-STAGING',version='1',originalSha256=self.pdf_sha,pdfSha256=self.pdf_sha,
            solicitudId=str(self.case.pk),identidadDocumentalReferencia=sha256(self.holder.numero_identificacion.encode()).hexdigest(),
            proposito='PRUEBA_INTEGRACION_AUTENTIC',ambienteFirma='STAGING',firmaProductivaAcreditada=False,
            autorizaDesembolso=False,paginas=1,preparadoEn=timezone.now().isoformat())
        with transaction.atomic():
            forms._custodiar_pdf(self.case,actor=None,titular=None,alias='default',actor_ref='sistema:prueba-staging',
                key=self.doc,pdf=self.pdf,meta=self.meta,event='credihoy_contractual_documento_prueba_custodiado')
        proof=signing.loads(self.identity['_firmanteInstitucional'],key=fixtures.SIGNING_KEY,salt='credihoy-firmante-institucional-v1')
        proof['pdfSha256']=self.pdf_sha
        self.identity['_firmanteInstitucional']=signing.dumps(proof,key=fixtures.SIGNING_KEY,salt='credihoy-firmante-institucional-v1')

    def staging_scope(self):
        scope=fixtures.ContractualOTPTests.staging_scope(self)
        scope['documentoPrueba']={'codigo':'PRUEBA-FIRMA-STAGING','version':'1','paginas':1}
        return scope

    def test_independent_custody_does_not_mutate_dossier_or_official_document_history(self):
        self.detail.refresh_from_db();self.assertEqual(self.detail.orchestration_data,self.original_data)
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_PDF_OFICIAL_CUSTODIADO').count(),1)
        with override_settings(CREDIHOY_CONTRACTUAL_AMBIENTE='STAGING',CREDIHOY_CONTRACTUAL_STAGING_ALCANCE=self.staging_scope()):
            doc=self.present();self.assertEqual(doc['codigo'],'PRUEBA-FIRMA-STAGING')
            self.assertEqual(doc['pdfSha256'],self.pdf_sha)
        self.client.start_verification.assert_not_called()

    def test_unscoped_foreign_or_productive_trial_cannot_be_presented(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional import NoDisponible
        scope=self.staging_scope()
        for changed in ({'documentoPrueba':{'codigo':'COLO-FO.001','version':'1','paginas':1}},
                {'documentoOperacionId':self.original_doc},{'pdfSha256':self.original_sha}):
            with override_settings(CREDIHOY_CONTRACTUAL_AMBIENTE='STAGING',CREDIHOY_CONTRACTUAL_STAGING_ALCANCE={**scope,**changed}):
                with self.assertRaises((ValueError,NoDisponible)):self.present()
        self.client.start_verification.assert_not_called()

    def test_personal_trial_reuses_otp_and_sender_without_quote_or_original_replay(self):
        from unittest.mock import patch
        from ..cliente import AutenticDEVClient
        from ..contratos import ConfiguracionSTAGING
        from ..operaciones import RegistroFirmasExpediente
        from .test_cliente import Session,Response,recibo,token
        session=Session(Response(token()),Response(recibo(),201))
        client=AutenticDEVClient(ConfiguracionSTAGING('SYNTHETIC','SECRET',permitir_documentos=True),session=session)
        params=dict(actor=None,titular=self.identity,operacion_id=self.doc,metadata=None,clave_idempotencia=self.send,
            cliente=client,registro=RegistroFirmasExpediente(self.case.pk,self.send),referencia_autorizacion='SYNTHETIC-INDEPENDENT',autoriza_firma=True)
        try:
            with override_settings(CREDIHOY_CONTRACTUAL_AMBIENTE='STAGING',CREDIHOY_CONTRACTUAL_STAGING_ALCANCE=self.staging_scope(),
                    CREDIHOY_AUTENTIC_CONTRACTUAL_STAGING_ENVIO_ENABLED=True),\
                    patch('apps.xcore_consumo.services.credihoy_v2.unico.cotizacion.comprobante_simulacion_aceptada',
                        side_effect=AssertionError('NO_FINANCIAL_REEVALUATION')):
                self.full_act()
                first=forms.enviar_formato_custodiado(self.case.pk,**params)
                second=forms.enviar_formato_custodiado(self.case.pk,**params)
            self.assertEqual(first['estado'],'FIRMA_REPORTADA_NO_VERIFICADA');self.assertFalse(second['reenvioPermitido'])
            self.assertEqual(len(session.calls),2)
            self.client.start_verification.assert_called_once();self.client.check_verification.assert_called_once()
            self.detail.refresh_from_db();data=self.detail.orchestration_data
            self.assertEqual(data['credihoy_autentic_envios_v1'][self.ambiguous],self.original_entry)
            entry=data['credihoy_autentic_envios_v1'][self.send]
            self.assertEqual(entry['vinculo']['codigo'],'PRUEBA-FIRMA-STAGING');self.assertFalse(entry['vinculo']['firmaProductivaAcreditada'])
            self.assertNotIn('formalizacion',entry);self.assertNotIn('credihoy_formalizacion_autentic_v1',data)
        finally:client.close()
