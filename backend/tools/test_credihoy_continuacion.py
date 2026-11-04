"""Native PostgreSQL continuation; provider/authorization fixtures are SIMULATED."""
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TransactionTestCase,override_settings
from django.utils import timezone
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import ConsentimientoConsumo,EvaluacionConsumo,SolicitudConsumo
from apps.xcore_consumo.services.orchestration import continuar_credihoy_unico
from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from tools.test_credihoy_autoservicio import AutoservicioTests


@override_settings(RISK_GATEWAY_ORCHESTRATION_ENABLED=True,CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True,
    CREDIHOY_AUTOSERVICIO_ENABLED=True,CREDIHOY_AUTOSERVICIO_DB_ALIAS='default',
    CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'o'*64)
class ContinuacionTests(TransactionTestCase):
    reautenticar=AutoservicioTests.reautenticar
    aceptacion=AutoservicioTests.aceptacion
    guardar=AutoservicioTests.guardar
    enviar=AutoservicioTests.enviar

    def setUp(self):
        import base64
        scope=override_settings(CREDIHOY_PROGRESO_AES_KEY_B64=base64.b64encode(b'p'*32).decode())
        scope.enable();self.addCleanup(scope.disable)
        AutoservicioTests.setUp(self)
        sent,_=self.enviar();self.sid=sent['referencia']
        self.operator=get_user_model().objects.create_user(username='synthetic-worker',is_staff=True,is_superuser=True)
        self.consent=ConsentimientoConsumo.objects.create(solicitud_id=self.sid,aceptado=True,firmado=False,
            fecha_aceptacion=timezone.now(),text_snapshot='SYNTHETIC ONLY',text_hash='a'*64,evidencia={})
        self.config=patch('apps.integraciones.risk_gateway.configuracion.GatewayConfig.from_settings',
            return_value=SimpleNamespace(validar=lambda:SimpleNamespace(internal_client='SYNTHETIC')))
        self.authorization=patch('apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal.verificar_autorizacion_centrales_personal')
        self.validation=patch('apps.integraciones.risk_gateway.operaciones.validacion.validar')
        for p in (self.config,self.authorization,self.validation):p.start();self.addCleanup(p.stop)

    def run_case(self):return continuar_credihoy_unico(self.sid,operador=self.operator)

    def prior(self,state):
        return OperacionGateway.objects.create(solicitud_id=self.sid,consentimiento=self.consent,proveedor='HDC',
            estado=state,idempotency_key='synthetic:'+uuid4().hex,identidad_hmac='a'*64,
            consentimiento_hmac='b'*64,request_hmac='c'*64)

    def test_missing_consent_remains_pending_without_paid_query(self):
        self.consent.delete()
        with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            self.assertEqual(self.run_case()['estado'],'CONSENTIMIENTO_PENDIENTE');gateway.assert_not_called()

    def test_invalid_authorization_never_calls_gateway(self):
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal.verificar_autorizacion_centrales_personal',side_effect=ValueError),patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            self.assertEqual(self.run_case()['estado'],'CONSENTIMIENTO_PENDIENTE');gateway.assert_not_called()

    @override_settings(RISK_GATEWAY_ORCHESTRATION_ENABLED=False)
    def test_closed_flags_are_not_overridden_and_duplicate_events_are_one(self):
        with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            first=self.run_case();self.assertEqual(self.run_case(),first);gateway.assert_not_called()
        self.assertEqual(AuditLog.objects.filter(event='credihoy_continuacion_automatica').count(),1)
        self.assertEqual(first['codigo'],'CONTINUACION_OPERATIVA_NO_HABILITADA')

    def test_missing_institutional_consumption_authorization_is_a_block_not_a_grant(self):
        with patch('apps.integraciones.risk_gateway.operaciones.validacion.validar',side_effect=OperacionError('AUTORIZACION_INSTITUCIONAL_CONSUMO_PENDIENTE')),patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            self.assertEqual(self.run_case()['codigo'],'AUTORIZACION_INSTITUCIONAL_CONSUMO_PENDIENTE');gateway.assert_not_called()

    def test_previous_completed_uncertain_or_running_operation_is_never_resent(self):
        for state in ('COMPLETADA','EN_EJECUCION','PENDIENTE_CONCILIACION','ERROR_DEFINITIVO'):
            with self.subTest(state=state):
                op=self.prior(state)
                with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
                    self.assertEqual(self.run_case()['codigo'],'HDC_EXISTENTE_NO_REENVIAR');gateway.assert_not_called()
                op.delete()

    def test_valid_native_result_calls_existing_evaluator_and_never_approves(self):
        result=SimpleNamespace(operacion_id=uuid4(),respuesta=object(),reutilizada=False,estado='COMPLETADA')
        with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar',return_value=result) as gateway,patch('apps.xcore_consumo.services.credihoy_v2.unico.funcional.registrar_resultado_consulta_hdc') as evaluate:
            packet=self.run_case();self.assertEqual(packet['estado'],'EVALUACION_COMPLETADA')
            self.assertEqual(packet['decisionCrediticia'],'NO_AUTORIZADA');self.assertEqual(packet['START'],0)
            gateway.assert_called_once();evaluate.assert_called_once()
            self.assertEqual(evaluate.call_args.kwargs['operacion'],result)
            self.assertTrue(evaluate.call_args.kwargs['autogestion'])

    def test_ambiguous_response_does_not_run_evaluator(self):
        result=SimpleNamespace(operacion_id=uuid4(),respuesta=None,reutilizada=True,estado='COMPLETADA')
        with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar',return_value=result),patch('apps.xcore_consumo.services.credihoy_v2.unico.funcional.registrar_resultado_consulta_hdc') as evaluate:
            self.assertEqual(self.run_case()['estado'],'PENDIENTE_CONCILIACION');evaluate.assert_not_called()

    def test_failed_evaluation_is_journaled_without_silently_retrying_hdc(self):
        result=SimpleNamespace(operacion_id=uuid4(),respuesta=object(),reutilizada=False,estado='COMPLETADA')
        with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar',return_value=result),patch('apps.xcore_consumo.services.credihoy_v2.unico.funcional.registrar_resultado_consulta_hdc',side_effect=ValueError('SYNTHETIC')):
            with self.assertRaises(ValueError):self.run_case()
        self.assertTrue(AuditLog.objects.filter(event='credihoy_continuacion_automatica',payload__codigo='EVALUACION_PERSISTENCIA_NO_CONFIRMADA').exists())

    def test_no_durable_external_call_inside_request_transaction(self):
        with transaction.atomic():
            with self.assertRaisesMessage(ValueError,'CONTINUACION_REQUIERE_COMMIT_DURABLE'):self.run_case()

    def test_unprivileged_actor_cannot_start_process(self):
        self.operator.is_superuser=False;self.operator.is_staff=False
        with self.assertRaisesMessage(ValueError,'OPERADOR_TECNICO_CONTINUACION_NO_AUTORIZADO'):self.run_case()

    @override_settings(RISK_GATEWAY_ORCHESTRATION_ENABLED=False)
    def test_worker_remains_inert_when_operational_profile_is_closed(self):
        from apps.xcore_consumo.tasks import continuar_credihoy_unico_pendientes
        with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico') as continue_case:
            self.assertEqual(continuar_credihoy_unico_pendientes.run(operador_id=self.operator.pk),'CONTINUACION_OPERATIVA_NO_HABILITADA')
            continue_case.assert_not_called()

    def test_worker_uses_existing_persisted_authorization_not_browser_financial_commands(self):
        from apps.xcore_consumo.tasks import continuar_credihoy_unico_pendientes
        detail=SolicitudConsumo.objects.get(solicitud_id=self.sid)
        self.assertEqual(continuar_credihoy_unico_pendientes.run(operador_id=self.operator.pk)['revisadas'],0)
        detail.orchestration_data={**detail.orchestration_data,'credihoy_autorizacion_centrales_v1':{'estado':'OTP_CONFIRMADA'}}
        detail.save(update_fields=['orchestration_data'])
        with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico',return_value={'estado':'SIMULATED'}) as continue_case:
            result=continuar_credihoy_unico_pendientes.run(operador_id=self.operator.pk)
            self.assertEqual(result['revisadas'],1)
            continue_case.assert_called_once();self.assertEqual(continue_case.call_args.args,(self.sid,))
