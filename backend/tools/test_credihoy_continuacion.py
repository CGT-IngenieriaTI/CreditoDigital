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
    CREDIHOY_CONTINUACION_MASIVA_ENABLED=True,
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
        from apps.xcore_consumo.services.credihoy_v2.base import Hecho
        from apps.xcore_consumo.services.credihoy_v2.unico.reglas import HechosReglasUnicoV2
        from apps.xcore_consumo.services.credihoy_v2.unico.hechos_continuacion import obtener, ContextoHechosContinuacion
        self.obtener_hechos_real=obtener
        facts=patch('apps.xcore_consumo.services.credihoy_v2.unico.hechos_continuacion.obtener',
            return_value=ContextoHechosContinuacion(ingresos=Hecho(fuente='SYNTHETIC_UNAVAILABLE'),
                reglas=HechosReglasUnicoV2(),descuentos_nomina=Hecho(fuente='SYNTHETIC_PAYROLL_UNAVAILABLE'),
                cuota_estimada=Hecho(fuente='SYNTHETIC_QUOTA_UNAVAILABLE')))
        self.hechos_mock=facts.start();self.addCleanup(facts.stop)

    def run_case(self):return continuar_credihoy_unico(self.sid,operador=self.operator)

    def prior(self,state):
        return OperacionGateway.objects.create(solicitud_id=self.sid,consentimiento=self.consent,proveedor='HDC',
            estado=state,idempotency_key='synthetic:'+uuid4().hex,identidad_hmac='a'*64,
            consentimiento_hmac='b'*64,request_hmac='c'*64)

    def test_missing_consent_remains_pending_without_paid_query(self):
        self.consent.delete()
        with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            self.assertEqual(self.run_case()['estado'],'CONSENTIMIENTO_PENDIENTE');gateway.assert_not_called()

    def event_fixture(self):
        from apps.solicitudes.models import Solicitud
        from apps.xcore_consumo.services.credihoy_v2.unico import centrales_personal,continuacion_eventos
        case=Solicitud.objects.get(pk=self.sid)
        journal={'estado':'OTP_CONFIRMADA','vinculo':{'operacionId':str(uuid4())},
                 'registros':[{'sha256':'b'*64}]}
        detail=SolicitudConsumo.objects.get(solicitud_id=self.sid)
        detail.orchestration_data={**detail.orchestration_data,centrales_personal.KEY:journal}
        detail.save(update_fields=['orchestration_data'])
        with patch.object(continuacion_eventos,'publicar') as publish:
            with transaction.atomic():
                eid=continuacion_eventos.registrar(case,'default',journal)
                publish.assert_not_called()
            publish.assert_called_once_with(eid)
        return eid

    def test_event_publishes_only_after_commit_and_is_durable(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        eid=self.event_fixture()
        self.assertEqual(AuditLog.objects.filter(event=events.EVENTO,request_id=eid).count(),1)

    def test_event_rollback_does_not_publish_or_leave_intent(self):
        from apps.solicitudes.models import Solicitud
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        journal={'estado':'OTP_CONFIRMADA','vinculo':{'operacionId':str(uuid4())},'registros':[{'sha256':'b'*64}]}
        with patch.object(events,'publicar') as publish:
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    events.registrar(Solicitud.objects.get(pk=self.sid),'default',journal)
                    raise ValueError('SYNTHETIC_ROLLBACK')
            publish.assert_not_called()
        self.assertFalse(AuditLog.objects.filter(event=events.EVENTO).exists())

    def test_event_out_of_scope_cannot_execute(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        eid=self.event_fixture()
        with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico') as run:
            self.assertEqual(events.ejecutar(eid)['estado'],'EXPEDIENTE_NO_AUTORIZADO')
            run.assert_not_called()

    def test_event_duplicate_deliveries_run_one_logical_continuation(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        eid=self.event_fixture()
        with override_settings(CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=[self.sid],
                CREDIHOY_CONTINUACION_OPERADOR_ID=self.operator.pk):
            with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico',
                    return_value={'estado':'EVALUACION_COMPLETADA'}) as run:
                first=events.ejecutar(eid)
                self.assertEqual(events.ejecutar(eid),first)
                run.assert_called_once()

    def test_event_worker_failure_can_recover_same_durable_intent(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        eid=self.event_fixture()
        with override_settings(CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=[self.sid],
                CREDIHOY_CONTINUACION_OPERADOR_ID=self.operator.pk):
            with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico',side_effect=ValueError):
                with self.assertRaises(ValueError):events.ejecutar(eid)
            with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico',
                    return_value={'estado':'EVALUACION_COMPLETADA'}):
                self.assertEqual(events.ejecutar(eid)['estado'],'EVALUACION_COMPLETADA')

    @override_settings(CELERY_BROKER_URL=None)
    def test_owner_without_broker_keeps_intent_without_guessing_transport(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        eid=self.event_fixture()
        self.assertFalse(events.publicar(eid))
        self.assertTrue(AuditLog.objects.filter(event=events.EVENTO,request_id=eid).exists())

    def test_missing_economic_service_blocks_before_paid_query(self):
        from apps.xcore_consumo.services.credihoy_v2.errores_http import ApiDataCrediHoyV2Error,CodigoErrorApiDataV2
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.hechos_continuacion.obtener',
                side_effect=ApiDataCrediHoyV2Error(CodigoErrorApiDataV2.CONFIGURACION_INCOMPLETA)),patch(
                'apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            self.assertEqual(self.run_case()['codigo'],'HECHOS_INSTITUCIONALES_NO_DISPONIBLES_NO_CONSUMIR_HDC')
            gateway.assert_not_called()

    @override_settings(CELERY_BROKER_URL=None)
    def test_recovery_appends_only_missing_intent_from_original_confirmation(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        eid=self.event_fixture()
        AuditLog.objects.filter(event=events.EVENTO,request_id=eid).delete()
        before=SolicitudConsumo.objects.get(solicitud_id=self.sid).orchestration_data
        with override_settings(CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=[self.sid]):
            self.assertEqual(events.recuperar_individual(self.sid),eid)
            self.assertEqual(events.recuperar_individual(self.sid),eid)
        self.assertEqual(AuditLog.objects.filter(event=events.EVENTO,request_id=eid).count(),1)
        self.assertEqual(SolicitudConsumo.objects.get(solicitud_id=self.sid).orchestration_data,before)

    def test_recovery_cannot_manufacture_authorization_or_process_other_case(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        with override_settings(CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=[]):
            with self.assertRaisesMessage(ValueError,'EXPEDIENTE_NO_AUTORIZADO'):
                events.recuperar_individual(self.sid)
        with override_settings(CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=[self.sid]):
            with self.assertRaisesMessage(ValueError,'CONFIRMACION_ORIGINAL_NO_RECUPERABLE'):
                events.recuperar_individual(self.sid)
        self.assertFalse(AuditLog.objects.filter(event=events.EVENTO).exists())

    def test_dispatcher_runs_registered_task_for_exact_scope_and_preserves_global_financial_flags(self):
        import json,tempfile
        from pathlib import Path
        from datetime import timedelta
        from django.db import connection
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_dispatcher as dispatcher
        eid=self.event_fixture()
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(),inet_server_addr()::text,inet_server_port(),current_schema()')
            db=list(cursor.fetchone())
        scope={'version':1,'solicitudId':self.sid,'operadorId':self.operator.pk,
            'hasta':(timezone.now()+timedelta(minutes=5)).isoformat(),'database':db,'referencia':'SYNTHETIC'}
        with tempfile.TemporaryDirectory() as folder,override_settings(
                CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=[],CREDIHOY_CONTINUACION_OPERADOR_ID=None,
                CREDIHOY_CONTINUACION_TRANSPORTE='',RISK_GATEWAY_ORCHESTRATION_ENABLED=False):
            path=Path(folder)/'scope.json';path.write_text(json.dumps(scope))
            with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico',
                    return_value={'estado':'EVALUACION_EXISTENTE_RECUPERADA','evaluacionConservada':{'version':8}}) as run:
                result=dispatcher.run(path)
                self.assertEqual(result['estado'],'EVENTO_INDIVIDUAL_CONSUMIDO')
                self.assertEqual(result['correlationId'],eid)
                run.assert_called_once()
                self.assertEqual(dispatcher.run(path),result)
                run.assert_called_once()
            from django.conf import settings
            self.assertFalse(settings.RISK_GATEWAY_ORCHESTRATION_ENABLED)

    def test_dispatcher_rejects_expired_or_wrong_database_scope(self):
        import json,tempfile
        from pathlib import Path
        from datetime import timedelta
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_dispatcher as dispatcher
        scope={'version':1,'solicitudId':self.sid,'operadorId':self.operator.pk,
            'hasta':(timezone.now()-timedelta(seconds=1)).isoformat(),'database':['other'],'referencia':'SYNTHETIC'}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'scope.json';path.write_text(json.dumps(scope))
            with self.assertRaisesMessage(ValueError,'ALCANCE_VENCIDO'):dispatcher.run(path)
            scope['hasta']=(timezone.now()+timedelta(minutes=5)).isoformat();path.write_text(json.dumps(scope))
            with self.assertRaisesMessage(ValueError,'OUTBOX_BASE_DATOS_DIFIERE'):dispatcher.run(path)

    def test_private_economic_profile_is_expiring_holder_bound_and_never_logged(self):
        import json,hashlib,tempfile
        from pathlib import Path
        from datetime import timedelta
        from apps.xcore_consumo.services.credihoy_v2.cliente import ConfiguracionApiDataV2,ApiDataCrediHoyV2Client
        from apps.xcore_consumo.services.credihoy_v2.errores_http import ApiDataCrediHoyV2Error
        profile={'base_url':'https://synthetic.invalid','client_id':'credihoy-hechos-synthetic',
            'client_secret':'SYNTHETIC_NOT_A_REAL_SECRET','timeout':15,
            'hasta':(timezone.now()+timedelta(minutes=5)).isoformat(),
            'documentoSha256':hashlib.sha256(b'0000000099').hexdigest(),'referencia':'SYNTHETIC'}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'client.json';path.write_text(json.dumps(profile));path.chmod(0o600)
            cfg=ConfiguracionApiDataV2.desde_settings(SimpleNamespace(API_DATA_CREDIHOY_CREDENTIAL_FILE=str(path)))
            self.assertNotIn(profile['client_secret'],repr(cfg))
            client=ApiDataCrediHoyV2Client(cfg,session=SimpleNamespace(post=lambda *a,**k:self.fail('NO_NETWORK')))
            with self.assertRaises(ApiDataCrediHoyV2Error):
                client.consultar(numero_documento='0000000088',fecha_evaluacion=timezone.now())
            profile['hasta']=(timezone.now()-timedelta(seconds=1)).isoformat();path.write_text(json.dumps(profile))
            with self.assertRaises(ApiDataCrediHoyV2Error):
                ConfiguracionApiDataV2.desde_settings(SimpleNamespace(API_DATA_CREDIHOY_CREDENTIAL_FILE=str(path)))

    def test_worker_process_death_releases_postgres_claim_without_hdc(self):
        import hashlib,json,subprocess,sys,time
        from django.db import connection
        from apps.xcore_consumo.services.credihoy_v2.unico import continuacion_eventos as events
        eid=self.event_fixture()
        lock=int(hashlib.sha256(eid.encode()).hexdigest()[:16],16)-2**63
        params={k:v for k,v in connection.get_connection_params().items()
            if k in {'dbname','host','port','user','password','sslmode'}}
        self.assertIn(params.get('host'),{'127.0.0.1','localhost'})
        program='import sys,json,psycopg,time\np=json.load(sys.stdin)\nc=psycopg.connect(**p["database"],autocommit=True)\nc.execute("SELECT pg_advisory_lock(%s)",[p["lock"]])\nprint("CLAIMED",flush=True)\ntime.sleep(120)\n'
        process=subprocess.Popen([sys.executable,'-B','-c',program],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            process.stdin.write(json.dumps({'database':params,'lock':lock}).encode());process.stdin.close()
            self.assertEqual(process.stdout.readline().strip(),b'CLAIMED')
            process.kill();process.wait(timeout=10)
            claimed=False
            for _ in range(30):
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_try_advisory_lock(%s)',[lock]);claimed=cursor.fetchone()[0]
                    if claimed:cursor.execute('SELECT pg_advisory_unlock(%s)',[lock])
                if claimed:break
                time.sleep(.1)
            self.assertTrue(claimed)
            with override_settings(CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=[self.sid],CREDIHOY_CONTINUACION_OPERADOR_ID=self.operator.pk):
                with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico',return_value={'estado':'EVALUACION_COMPLETADA'}):
                    self.assertEqual(events.ejecutar(eid)['estado'],'EVALUACION_COMPLETADA')
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
            process.stdout.close();process.stderr.close()

    def test_institutional_adapter_preserves_source_and_encrypts_full_receipt(self):
        from decimal import Decimal
        from apps.solicitudes.models import Solicitud
        from apps.xcore_consumo.services.credihoy_v2.hechos import HechosInternosCrediHoyV2
        from apps.xcore_consumo.services.credihoy_v2.financieros_http import adaptar_financieros
        from tools.test_credihoy_financieros import financial_fixture
        from apps.xcore_consumo.services.otp_crypto import decrypt_text
        import hashlib
        raw,now=financial_fixture()
        facts=HechosInternosCrediHoyV2(financieros=adaptar_financieros(raw,consulta=now))
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.hechos_continuacion.ConfiguracionApiDataV2.desde_settings'),patch(
                'apps.xcore_consumo.services.credihoy_v2.unico.hechos_continuacion.ApiDataCrediHoyV2Client') as client:
            client.return_value.consultar.return_value=facts
            income,rules,payroll=self.obtener_hechos_real(Solicitud.objects.select_related('solicitante').get(pk=self.sid),str(uuid4()))
        self.assertEqual(income.valor,Decimal('1020'))
        self.assertEqual(payroll.valor,Decimal('10'))
        self.assertEqual(payroll.fuente,'AP014MCLIENTE.V_DTONOM')
        receipt=AuditLog.objects.get(event='credihoy_hechos_continuacion_v1').payload
        decoded=decrypt_text(receipt['hechosCifrados'])
        self.assertEqual(hashlib.sha256(decoded.encode()).hexdigest(),receipt['sha256'])
        self.assertNotIn('financieros',receipt)
        self.assertFalse(rules.saldo_aportes_utilizable.disponible)

    def test_unavailable_income_is_never_replaced_by_form_or_synthetic_amount(self):
        from apps.solicitudes.models import Solicitud
        from apps.xcore_consumo.services.credihoy_v2.hechos import HechosInternosCrediHoyV2
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.hechos_continuacion.ConfiguracionApiDataV2.desde_settings'),patch(
                'apps.xcore_consumo.services.credihoy_v2.unico.hechos_continuacion.ApiDataCrediHoyV2Client') as client:
            client.return_value.consultar.return_value=HechosInternosCrediHoyV2()
            income,rules,payroll=self.obtener_hechos_real(Solicitud.objects.select_related('solicitante').get(pk=self.sid),str(uuid4()))
        self.assertFalse(income.disponible)
        self.assertFalse(payroll.disponible)
        self.assertIsNone(income.valor)

    def test_invalid_authorization_never_calls_gateway(self):
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal.verificar_autorizacion_centrales_personal',side_effect=ValueError),patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            self.assertEqual(self.run_case()['estado'],'CONSENTIMIENTO_PENDIENTE');gateway.assert_not_called()

    def test_typed_authorization_failure_is_durable_and_never_calls_gateway(self):
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal.verificar_autorizacion_centrales_personal',side_effect=OperacionError('AUTORIZACION_CENTRALES_PERSONAL_VERIFICABLE_REQUERIDA')),patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            self.assertEqual(self.run_case()['estado'],'CONSENTIMIENTO_PENDIENTE')
            gateway.assert_not_called()

    @override_settings(CREDIHOY_CONTINUACION_MASIVA_ENABLED=False)
    def test_bulk_worker_is_closed_independently_of_financial_flags(self):
        from apps.xcore_consumo.tasks import continuar_credihoy_unico_pendientes
        with patch('apps.xcore_consumo.services.orchestration.continuar_credihoy_unico') as run:
            self.assertEqual(continuar_credihoy_unico_pendientes.run(operador_id=self.operator.pk),'CONTINUACION_MASIVA_NO_HABILITADA')
            run.assert_not_called()

    @override_settings(RISK_GATEWAY_ORCHESTRATION_ENABLED=False,CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=False)
    def test_recovery_without_existing_evaluation_never_consumes(self):
        with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            result=continuar_credihoy_unico(self.sid,operador=self.operator,recuperar_existente=True)
        self.assertEqual(result['codigo'],'SIN_EVALUACION_EXISTENTE_NO_CONSUMIR')
        gateway.assert_not_called()

    @override_settings(RISK_GATEWAY_ORCHESTRATION_ENABLED=False,CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=False)
    def test_verified_native_evaluation_is_recovered_without_global_flags_or_mutation(self):
        evaluation=EvaluacionConsumo.objects.create(solicitud_id=self.sid,resultados={'immutable':'fixture'})
        native=self.native_result()
        rules=native['reglas']
        self.assertNotIn('estado',rules[0])
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.funcional.leer_estado_funcional',return_value={'resultado':native}),patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            first=continuar_credihoy_unico(self.sid,operador=self.operator,recuperar_existente=True)
            self.assertEqual(first,continuar_credihoy_unico(self.sid,operador=self.operator,recuperar_existente=True))
        self.assertEqual(first['estado'],'EVALUACION_EXISTENTE_RECUPERADA')
        self.assertEqual(len(first['evaluacionConservada']['reglas']),16)
        self.assertEqual(first['evaluacionConservada']['reglas'],[{k:r[k] for k in ('codigo','resultado','estadoEvaluacion')} for r in rules])
        self.assertFalse(first['evaluacionConservada']['evaluacionNueva'])
        evaluation.refresh_from_db();self.assertEqual(evaluation.resultados,{'immutable':'fixture'})
        self.assertEqual(AuditLog.objects.filter(event='credihoy_continuacion_automatica').count(),1)
        gateway.assert_not_called()

    def native_result(self):
        from decimal import Decimal
        from apps.xcore_consumo.services.credihoy_v2.base import Hecho
        from apps.xcore_consumo.services.credihoy_v2.unico.dto import EntradaCredihoyUnicoV2,RecogidaCredihoyUnicoV2
        from apps.xcore_consumo.services.credihoy_v2.unico.motor import evaluar_capacidad_unico_v2
        from apps.xcore_consumo.services.credihoy_v2.unico.reglas import evaluar_reglas_unico_v2,HechosReglasUnicoV2
        from apps.xcore_consumo.services.credihoy_v2.unico.presentacion import serializar_evaluacion
        entry=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('100'),plazo_meses=2,
            periodicidad_pago='MENSUAL',modalidad_codigo='111',recogida=RecogidaCredihoyUnicoV2(aplica=False))
        capacity=evaluar_capacidad_unico_v2(entry,ingresos_mensuales=Hecho(fuente='SYNTHETIC'),
            cuotas_deudas_hdc_brutas=Hecho(fuente='SYNTHETIC'))
        rules=evaluar_reglas_unico_v2(entry,hechos=HechosReglasUnicoV2(),capacidad=capacity)
        return serializar_evaluacion(capacity,rules,'SYNTHETIC_NO_PROVIDER')

    def test_unknown_rule_contract_does_not_create_a_fictitious_result(self):
        EvaluacionConsumo.objects.create(solicitud_id=self.sid,resultados={})
        malformed={'reglas':[{'codigo':'UNICO_FILA_'+str(n),'estado':'CUMPLE'} for n in range(16)]}
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.funcional.leer_estado_funcional',return_value={'resultado':malformed}),patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar') as gateway:
            result=continuar_credihoy_unico(self.sid,operador=self.operator,recuperar_existente=True)
        self.assertEqual(result['estado'],'PENDIENTE_CONCILIACION')
        self.assertNotIn('evaluacionConservada',result)
        gateway.assert_not_called()

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

    def test_native_continuation_transports_the_bound_estimate_to_the_evaluator(self):
        from dataclasses import replace
        from decimal import Decimal
        from apps.xcore_consumo.services.credihoy_v2.base import Hecho, EstadoCertificacion as EC
        quote=Hecho(valor=Decimal('70000.10'),disponible=True,fuente='SYNTHETIC_SIMULATOR',
            estado_certificacion=EC.OBSERVADO,referencia_evidencia='SYNTHETIC-ONLY',
            ruta_origen='simulacion.cuotaEstimada')
        self.hechos_mock.return_value=replace(self.hechos_mock.return_value,cuota_estimada=quote)
        result=SimpleNamespace(operacion_id=uuid4(),respuesta=object(),reutilizada=False,estado='COMPLETADA')
        with patch('apps.integraciones.risk_gateway.operaciones.servicio.OrquestadorGateway.ejecutar',return_value=result),patch(
                'apps.xcore_consumo.services.credihoy_v2.unico.funcional.registrar_resultado_consulta_hdc') as evaluate:
            self.assertEqual(self.run_case()['estado'],'EVALUACION_COMPLETADA')
            self.assertIs(evaluate.call_args.kwargs['cuota_estimada'],quote)
        self.hechos_mock.assert_called_once()
        self.assertTrue(self.hechos_mock.call_args.kwargs['con_contexto'])

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

    def version_fixture(self):
        import json,hashlib,base64
        from uuid import uuid5,NAMESPACE_URL
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from apps.solicitudes.models import Solicitud
        from apps.xcore_consumo.services.otp_crypto import encrypt_text
        from apps.xcore_consumo.services.credihoy_v2.unico import funcional
        canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,default=str).encode()
        digest=lambda value:hashlib.sha256(canonical(value)).hexdigest()
        self.private=Ed25519PrivateKey.generate()
        self.case=Solicitud.objects.select_related('solicitante').get(pk=self.sid)
        self.native=self.native_result()
        self.evaluation=EvaluacionConsumo.objects.create(solicitud=self.case,input_snapshot={},resultados={funcional.KEY:self.native})
        self.hdc=self.prior('COMPLETADA')
        self.saved_versions=[]
        for number in (7,8):
            reference=str(uuid4());result=json.loads(json.dumps(self.native));result['requestId']=reference
            for i,row in enumerate(result['reglas']):
                row['estadoEvaluacion']='CUMPLE' if i<12 else 'NO_APLICA' if i<15 else 'PENDIENTE_POLITICA'
                row['resultado']='CUMPLE' if i<12 else 'NO_EVALUABLE'
            payload={'version':number,'versionReferencia':reference,'solicitudId':self.sid,
                'evaluacionBaseId':self.evaluation.pk,'resultado':result,'fuentes':{'huellaSolicitud':{
                    'documentoSha256':hashlib.sha256(self.case.solicitante.numero_identificacion.encode()).hexdigest(),
                    'evaluacionSha256':hashlib.sha256(json.dumps({'input':self.evaluation.input_snapshot,'resultados':self.evaluation.resultados},sort_keys=True,default=str).encode()).hexdigest()}}}
            raw=json.dumps(payload)
            entry=AuditLog.objects.create(solicitud=self.case,event='CREDIHOY_ESTUDIO_VERSION_REGISTRADA',request_id=reference,
                payload={'resultadoCifrado':encrypt_text(raw),'resultadoSha256':hashlib.sha256(raw.encode()).hexdigest(),'recibo':{'operacionHdc':str(self.hdc.pk)}})
            body={'version':'credihoy-estudio-tecnico-firmado-v1','proposito':'LECTURA_ESTUDIO_NO_DECISION',
                'solicitudId':self.sid,'titularSha256':payload['fuentes']['huellaSolicitud']['documentoSha256'],
                'evaluacionBaseSha256':digest(EvaluacionConsumo.objects.values().get(pk=self.evaluation.pk)),
                'consentimientoSha256':digest(ConsentimientoConsumo.objects.values().get(pk=self.consent.pk)),
                'consumoSha256':digest(SolicitudConsumo.objects.values().get(solicitud=self.case)),
                'estudio':{'versionContrato':'credihoy-estudio-publico-v1','solicitudId':self.sid,'evaluacionBaseId':self.evaluation.pk,
                    'version':number,'referenciaVersion':reference,'autorizadoDecisionCrediticia':False,
                    'reglas':[{k:r[k] for k in ('codigo','nombre','resultado','estadoEvaluacion','motivos','datosFaltantes')} for r in result['reglas']]}}
            signature=self.private.sign(b'credihoy-estudio-publico-tecnico-v1\x00'+canonical(body))
            AuditLog.objects.create(solicitud=self.case,event='CREDIHOY_ESTUDIO_PUBLICO_VERIFICADO',
                request_id=str(uuid5(NAMESPACE_URL,'credihoy-estudio-publico:'+self.sid+':'+reference)),
                payload={'algoritmo':'Ed25519','payload':body,'firmaBase64':base64.b64encode(signature).decode()})
            self.saved_versions.append(entry)

    def read_version(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import funcional
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal._clave_publica',return_value=self.private.public_key()),patch.object(funcional,'_exigir_recibo_hdc'):
            return funcional.aplicar_revision_estudio_registrada(self.case,self.evaluation,{'resultado':self.native})

    def test_latest_signed_v8_is_selected_without_changing_base(self):
        self.version_fixture()
        original=self.evaluation.resultados.copy()
        result=self.read_version()
        self.assertEqual(result['revisionEstudio']['version'],8)
        self.assertEqual(result['revisionEstudio']['versionReferencia'],self.saved_versions[-1].request_id)
        from collections import Counter
        self.assertEqual(Counter(r['estadoEvaluacion'] for r in result['resultado']['reglas']),{'CUMPLE':12,'NO_APLICA':3,'PENDIENTE_POLITICA':1})
        self.evaluation.refresh_from_db();self.assertEqual(self.evaluation.resultados,original)

    def test_invalid_version_hash_never_falls_back_to_base(self):
        self.version_fixture()
        entry=self.saved_versions[-1];entry.payload['resultadoSha256']='0'*64;entry.save(update_fields=['payload'])
        with self.assertRaisesMessage(ValueError,'REVISION_ESTUDIO_VIGENTE_NO_VERIFICABLE'):self.read_version()

    def test_changed_conditions_invalidate_signed_summary(self):
        self.version_fixture()
        detail=SolicitudConsumo.objects.get(solicitud=self.case);detail.form_data={**detail.form_data,'monto':'999.00'}
        detail.save(update_fields=['form_data'])
        with self.assertRaisesMessage(ValueError,'REVISION_ESTUDIO_VIGENTE_NO_VERIFICABLE'):self.read_version()

    def test_changed_base_invalidates_registered_version(self):
        self.version_fixture()
        self.evaluation.input_snapshot={'changed':'SYNTHETIC'};self.evaluation.save(update_fields=['input_snapshot'])
        with self.assertRaisesMessage(ValueError,'REVISION_ESTUDIO_VIGENTE_NO_VERIFICABLE'):self.read_version()
