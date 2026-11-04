"""Synthetic admission checks; no genuine signature or external consumption."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4

from django.test import SimpleTestCase,TransactionTestCase
from django.utils import timezone

from apps.integraciones.risk_gateway.contratos import HDCRequest
from apps.integraciones.risk_gateway.operaciones import validacion
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError,ConsentimientoInvalido


def synthetic_case():
    from apps.xcore_consumo.services.credihoy_v2.unico import autoservicio
    consent=SimpleNamespace(pk=1,evidencia={'workflow':'COLO023_AUTOGESTION_V1'},
        otp=None,version='SYNTHETIC',text_hash='a'*64,fecha_aceptacion=timezone.now())
    case=SimpleNamespace(pk=uuid4(),producto='CREDIHOY UNICO (NUEVO)',
        monto_solicitado=Decimal('1300000'),plazo_solicitado=12,asesor_id=None,
        solicitante=SimpleNamespace(pk=1,tipo_identificacion='CC',numero_identificacion='0000000000',primer_apellido='SINTETICO'),
        consentimiento_consumo=consent,consumo_detail=SimpleNamespace(form_data={'monto':'1300000.00','plazoMeses':12}),
        metadatos={'modo':'AUTOGESTION_ENVIADA','consumoRealAutorizado':True,
            'autorizacionEconomicaCredihoy':{'referenciaCostos':'SYNTHETIC','responsableCondiciones':'SYNTHETIC',
                'referenciaAutorizacionConsumo':'SYNTHETIC','maximoConsultasPorProveedor':1,'proveedores':['HDC']}})
    operation=str(uuid4())
    case.metadatos.update(origen=autoservicio.ORIGEN,productoLinix='390',modalidadLinix='111',destinacionLinix='CFA',
        envio={'operacionId':operation,'datos':{'monto':'1300000.00','plazo':12},
            'aceptacionTemporal':{'sesionReferencia':str(uuid4()),'aceptadoEn':timezone.now().isoformat()}})
    case.consumo_detail.orchestration_data={autoservicio.VINCULO_CONSUMO:{'version':1,'solicitudId':str(case.pk),
        'origen':autoservicio.ORIGEN,'modo':autoservicio.MODO,'producto':'390','modalidad':'111','destinacion':'CFA',
        'operacionEnvioOriginal':operation}}
    request=HDCRequest.desde_payload({'persona':'natural','person_id_type':'1',
        'person_id_number':'0000000000','person_last_name':'SINTETICO'})
    verified=SimpleNamespace(metodo='OTP',referencia_verificacion='SYNTHETIC',
        texto_sha256='a'*64,pdf_sha256='b'*64,confirmado_en=timezone.now())
    return case,request,verified


class AdmissionTests(SimpleTestCase):
    def setUp(self):
        self.case,self.request,verified=synthetic_case()
        for context in (patch.object(validacion,'assert_consent_granted'),
                patch('apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal.verificar_autorizacion_centrales_personal',return_value=verified)):
            context.start();self.addCleanup(context.stop)

    def test_missing_consent_is_rejected_with_the_domain_error_before_any_verification(self):
        from django.core.exceptions import ObjectDoesNotExist
        class CaseWithoutConsent:
            producto='CREDIHOY UNICO (NUEVO)'
            @property
            def consentimiento_consumo(self):raise ObjectDoesNotExist('SYNTHETIC_MISSING_CONSENT')
        with self.assertRaises(ConsentimientoInvalido):
            validacion.validar(CaseWithoutConsent(),self.request)

    def test_partial_submission_is_rejected_without_fabricating_collection_or_periodicity(self):
        before=deepcopy(self.case.consumo_detail.form_data)
        with self.assertRaisesRegex(OperacionError,'FORMULARIO_EVALUACION_INCOMPLETO_ANTES_HDC'):
            validacion.validar(self.case,self.request)
        self.assertEqual(self.case.consumo_detail.form_data,before)

    def test_each_missing_field_and_inconsistent_amount_blocks_admission(self):
        complete={**self.case.consumo_detail.form_data,'periodicidadPago':'MENSUAL','recogerCredihoy':False}
        for missing in ('periodicidadPago','recogerCredihoy'):
            self.case.consumo_detail.form_data={k:v for k,v in complete.items() if k!=missing}
            with self.assertRaisesRegex(OperacionError,'FORMULARIO_EVALUACION_INCOMPLETO'):
                validacion.validar(self.case,self.request)
        self.case.consumo_detail.form_data={**complete,'monto':'1.00'}
        with self.assertRaisesRegex(OperacionError,'FORMULARIO_EVALUACION_INCOMPLETO'):
            validacion.validar(self.case,self.request)

    def test_complete_explicit_form_passes_existing_binding_not_credit_approval(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.autoservicio import formulario_desde_envio
        self.case.metadatos['envio']['datos'].update(periodicidadPago='MENSUAL',recogerCredihoy=False)
        self.case.consumo_detail.form_data=formulario_desde_envio(self.case)
        before=deepcopy(self.case.consumo_detail.form_data)
        consent,*fingerprints=validacion.validar(self.case,self.request)
        self.assertIs(consent,self.case.consentimiento_consumo)
        self.assertEqual([len(v) for v in fingerprints],[64,64,64])
        self.assertEqual(self.case.consumo_detail.form_data,before)

    def test_invalid_signature_is_not_replaced_by_complete_form(self):
        self.case.consumo_detail.form_data.update(periodicidadPago='MENSUAL',recogerCredihoy=False)
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal.verificar_autorizacion_centrales_personal',
                side_effect=OperacionError('AUTORIZACION_CENTRALES_PERSONAL_VERIFICABLE_REQUERIDA')):
            with self.assertRaises(OperacionError):validacion.validar(self.case,self.request)


class BeforeReservationTests(TransactionTestCase):
    def test_partial_form_creates_no_journal_or_audit_and_never_calls_hdc(self):
        from apps.integraciones.risk_gateway.operaciones import servicio
        case,request,verified=synthetic_case();client=Mock()
        with patch.object(servicio,'_enabled'),patch.object(servicio.Solicitud.objects,'select_related') as query, \
                patch.object(validacion,'assert_consent_granted'), \
                patch('apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal.verificar_autorizacion_centrales_personal',return_value=verified), \
                patch.object(servicio.OperacionGateway.objects,'filter') as journal,patch.object(servicio,'_audit') as audit:
            query.return_value.get.return_value=case
            with self.assertRaisesRegex(OperacionError,'FORMULARIO_EVALUACION_INCOMPLETO'):
                servicio.OrquestadorGateway(hdc=client).ejecutar(case.pk,request)
            journal.assert_not_called();audit.assert_not_called();client.consultar.assert_not_called()
