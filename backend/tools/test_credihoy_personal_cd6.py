"""Personal confirmation against the disposable local PostgreSQL test database."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import sys

from django.core import signing
from django.db import connection
from django.test import SimpleTestCase,TransactionTestCase,override_settings
from django.utils import timezone
from apps.utils.models import AuditLog
from apps.xcore_consumo.services.credihoy_v2.unico import cotizacion as q,publico_seguimiento as owner
from tools.test_credihoy_simulador_local import OwnQuoteTests
from tools.test_credihoy_titular import RadicationDelegationTests


class NativeDelegationCompositionTests(RadicationDelegationTests):
    def setUp(self):
        super().setUp()
        import ast
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'api_data_v1/tools'))
        from credihoy_source_delivery import APP,git
        from credihoy_personal_cd6 import add_personal_delegation
        path='backend/apps/xcore_consumo/services/credihoy_v2/unico/publico_seguimiento.py'
        old=git(APP,'show','dd1f2db^:'+path)
        changed=add_personal_delegation(old).decode();namespace=owner.__dict__.copy()
        for node in ast.parse(changed).body:
            if isinstance(node,ast.FunctionDef) and node.name in {'consult','authorize'}:
                exec(compile(ast.get_source_segment(changed,node),'native-delegation-composition','exec'),namespace)
        for name in ('consult','authorize'):
            mock=patch.object(owner,name,namespace[name]);mock.start();self.addCleanup(mock.stop)


@override_settings(CREDIHOY_RADICACION_TITULAR_ENABLED=True,
    CREDIHOY_RADICACION_PERSONAL_URL='https://api.example.invalid',
    CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'o'*64)
class PersonalConfirmationTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor!='postgresql':self.skipTest('Disposable PostgreSQL required')
        OwnQuoteTests.setUp(self)
        self.identity['_scopeRadicacion']='solicitudes:radicar'
        self.proof={'solicitudId':str(self.s.pk),'aceptacionAuditoriaId':708,'aceptacionSha256':'a'*64,
            'simulacionSha256':'b'*64,'revisionReferencia':self.ref,'decisionReferencia':str(uuid4()),
            'decisionSha256':'c'*64,'versionCondiciones':'d'*64,'cuota':'204448','autorizaStart':False}
        self.received=[]
        for mock in (patch.object(q,'comprobante_simulacion_aceptada',return_value=self.proof),
                patch.object(q,'fuente_radicacion_personal',return_value={'payload':{},'evidencias':{'calendarioPropuesto':{
                    'diaSeleccionado':10,'primerVencimientoProyectado':'2027-01-10','version':'CREDIHOY_DIA_PAGO_30_CALENDARIO_20261028_V2'}}})):
            mock.start();self.addCleanup(mock.stop)
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'api_data_v1/tools'))
        from credihoy_personal_cd6 import personal_confirmation_sql,PERSONAL_CONFIRMATION_ROLLBACK_SQL
        # No roles or global grants are created by tests.
        sql=personal_confirmation_sql([str(self.s.pk)]).replace('TO credihoy_titular_cotizacion;','TO CURRENT_USER;')
        with connection.cursor() as cursor:cursor.execute(sql)
        def clean():
            with connection.cursor() as cursor:cursor.execute(PERSONAL_CONFIRMATION_ROLLBACK_SQL)
        self.addCleanup(clean)

    def transport(self,body,proof,identity):
        self.received.append(deepcopy(body))
        if body['accion']=='confirmar':
            self.assertTrue(AuditLog.objects.filter(event='CREDIHOY_RADICACION_PERSONAL_CONFIRMADA').exists())
        return {'estado':'PREPARADA','puedeConfirmar':body['accion']=='preparar','fingerprint':'e'*64}

    def operate(self,operation,**extra):
        return q.operacion_radicacion_titular(self.identity,{'solicitudId':str(self.s.pk),
            'operacion':operation,**extra},str(uuid4()),transporte=self.transport)

    def prepared(self):return self.operate('preparar',diaPagoPropuesto=10)['presentacion']

    def test_prepare_does_not_confirm_and_confirmation_is_durable_before_transport(self):
        token=self.prepared()
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_RADICACION_PERSONAL_CONFIRMADA').count(),0)
        result=self.operate('confirmar',presentacion=token,aceptaRadicar=True)
        event=AuditLog.objects.get(event='CREDIHOY_RADICACION_PERSONAL_CONFIRMADA')
        self.assertNotEqual(event.pk,708)
        self.assertEqual(event.payload['confirmacionPersonal']['aceptacionAuditoriaId'],708)
        self.assertEqual(self.received[-1]['confirmacionPersonal']['auditoriaId'],event.pk)
        self.assertFalse(result['autorizaFirma']);self.assertFalse(result['autorizaDesembolso'])
        self.assertEqual(result['decisionInstitucional'],'PENDIENTE')

    def test_double_click_or_lost_response_recovers_same_intent_without_resending_confirmation(self):
        token=self.prepared();self.operate('confirmar',presentacion=token,aceptaRadicar=True)
        self.operate('confirmar',presentacion=token,aceptaRadicar=True)
        self.assertEqual([v['accion'] for v in self.received],['preparar','confirmar','consultar'])
        self.assertEqual(len({v['intencionId'] for v in self.received}),1)
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_RADICACION_PERSONAL_CONFIRMADA').count(),1)

    def test_modified_session_conditions_expiry_or_absent_consent_cannot_confirm(self):
        token=self.prepared()
        for bad in ({'sesion':str(uuid4())},{'comprobante':{**self.proof,'cuota':'1'}}):
            presentation=signing.loads(token,key=owner.key(),salt='credihoy-radicacion-personal-presentacion-v1')
            altered=signing.dumps({**presentation,**bad},key=owner.key(),salt='credihoy-radicacion-personal-presentacion-v1')
            with self.assertRaises(ValueError):self.operate('confirmar',presentacion=altered,aceptaRadicar=True)
        with self.assertRaises(ValueError):self.operate('confirmar',presentacion=token,aceptaRadicar=False)
        self.identity['_sesionExpiraAt']=(timezone.now()-timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):self.operate('consultar')
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_RADICACION_PERSONAL_CONFIRMADA').count(),0)

    def test_closed_permission_or_wrong_scope_is_rejected(self):
        self.identity['_scopeRadicacion']='solicitudes:cotizar'
        with self.assertRaises(ValueError):self.operate('consultar')
        self.assertEqual(self.received,[])

    def test_original_confirmation_date_view_preserves_scope_and_can_rollback(self):
        from credihoy_personal_cd6 import personal_history_view_change
        event=AuditLog.objects.create(solicitud=self.s,event='CREDIHOY_RADICACION_PERSONAL_CONFIRMADA',
            actor='SYNTHETIC',request_id=str(uuid4()),payload={})
        AuditLog.objects.create(event='CREDIHOY_RADICACION_PERSONAL_CONFIRMADA',actor='OTHER_SYNTHETIC',
            request_id=str(uuid4()),payload={})
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_viewdef('public.credihoy_radicacion_personal_publica'::regclass,true),current_user")
            definition,role=cursor.fetchone()
            change=personal_history_view_change(definition,role,[{'grantee':role,'privilege':'SELECT','grantable':False}])
            cursor.execute(change['forward'])
            cursor.execute('SELECT registro_id,confirmado_en FROM public.credihoy_radicacion_personal_publica')
            self.assertEqual(cursor.fetchall(),[(event.pk,event.created_at)])
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='credihoy_radicacion_personal_publica'")
            self.assertNotIn('payload',[r[0] for r in cursor.fetchall()])
            cursor.execute(change['rollback'])
            cursor.execute('SELECT * FROM public.credihoy_radicacion_personal_publica')
            self.assertEqual(len(cursor.description),3);self.assertEqual(len(cursor.fetchall()),1)


@override_settings(CREDIHOY_RADICACION_TITULAR_ENABLED=True)
class IntakeTrackingTests(SimpleTestCase):
    def test_reload_reads_only_existing_intent_and_hides_operational_identifiers(self):
        from contextlib import nullcontext
        from datetime import datetime,timezone as tz
        from unittest.mock import MagicMock
        from types import SimpleNamespace
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        sid=str(uuid4());row={'referencia':sid,'procedencia':{'origen':'FUENTE_OPERATIVA'}}
        proof={'cuota':'204448','aceptadaEn':'2026-11-07T21:00:28+00:00'}
        cursor=MagicMock();cursor.__enter__.return_value.fetchall.return_value=[(datetime(2026,11,9,3,59,tzinfo=tz.utc),)]
        with patch.object(q,'fuente_cotizacion',side_effect=lambda:nullcontext()),patch.object(q,'_obtener',return_value=SimpleNamespace(pk=sid)),\
                patch.object(q,'comprobante_simulacion_aceptada',return_value=proof),patch.object(q.SolicitudConsumo.objects,'using') as manager,\
                patch.object(q.transaction,'atomic',side_effect=lambda **kwargs:nullcontext()),\
                patch.object(q,'alias_cotizacion',return_value='default'),patch.object(q,'connections',{'default':SimpleNamespace(cursor=lambda:cursor)}),\
                patch.object(q,'transporte_cd6_personal',return_value={'estado':'RADICADA','K_FLUJO':'16119455','SC':'26101003935'}) as transport:
            manager.return_value.values_list.return_value.get.return_value={q.KEY:{'aceptacionesSimulacion':{'HASH':{}}}}
            for unused in range(2):
                result=q.seguimiento_radicacion_titular({'numeroDocumento':'SYNTHETIC'},[row])[0]['seguimientoRadicacion']
                self.assertTrue(result['confirmada']);self.assertEqual(result['cuotaAceptada'],'204448')
                self.assertEqual(result['radicadaEn'],'2026-11-09T03:59:00+00:00')
                self.assertNotIn('16119455',str(result));self.assertNotIn('26101003935',str(result))
            self.assertTrue(all(call.args[0]['accion']=='consultar' for call in transport.call_args_list))
            manager.return_value.values_list.return_value.get.return_value={}
            transport.reset_mock();self.assertEqual(q.seguimiento_radicacion_titular({},[row]),[row]);transport.assert_not_called()
        raw={'versionContrato':'credihoy-radicacion-personal-v1','solicitudId':sid,'intencionId':str(uuid4()),'estado':'RADICADA',
            'codigo':None,'puedeConfirmar':False,'presentacion':None,'K_FLUJO':'16119455','SC':'26101003935',
            'actividadesActivas':[{'actividad':6964}],'decisionInstitucional':'PENDIENTE','firmaContractual':'PENDIENTE',
            'autorizaFirma':False,'autorizaDesembolso':False}
        public=wire.project_radication(raw)
        self.assertIsNone(public['K_FLUJO']);self.assertIsNone(public['SC']);self.assertEqual(public['actividadesActivas'],[])
        self.assertEqual(raw['SC'],'26101003935')

    def test_incomplete_evidence_and_cross_holder_never_become_confirmed(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        value={'versionContrato':'credihoy-seguimiento-radicacion-v1','estado':'RADICADA','confirmada':True,
            'consultadoEn':timezone.now().isoformat(),'cuotaAceptada':'204448','aceptadaEn':'2026-11-07T21:00:28+00:00',
            'decisionInstitucional':'PENDIENTE','firmaContractual':'PENDIENTE'}
        for changes in ({'confirmada':False},{'estado':'PREPARADA'},{'SC':'123'},{'decisionInstitucional':'APROBADA'}):
            with self.assertRaises(wire.NoDisponible):wire.project_intake_tracking({**value,**changes})

    def test_history_uses_original_events_and_preserves_later_formalization_chronology(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
        row={'fechaCreacion':'2026-10-31T15:00:00+00:00','estudio':{'fechaEvaluacion':'2026-11-06T15:00:00+00:00'},
            'seguimientoRadicacion':{'confirmada':True,'aceptadaEn':'2026-11-07T21:00:28+00:00',
                'radicadaEn':'2026-11-09T03:59:00+00:00'},
            'formalizacionDocumental':{'firmaAcreditada':True,'actualizadoAt':'2026-11-09T05:00:00+00:00'}}
        history=wire.project_digital_history(row);by_code={r['codigo']:r for r in history}
        self.assertEqual(by_code['RADICACION']['fecha'],row['seguimientoRadicacion']['radicadaEn'])
        self.assertNotEqual(by_code['RADICACION']['fecha'],row['fechaCreacion'])
        self.assertLess(history.index(by_code['RADICACION']),history.index(by_code['FORMALIZACION']))
        for code in ('DECISION','DESEMBOLSO'):
            self.assertEqual(by_code[code]['estado'],'PENDIENTE');self.assertIsNone(by_code[code]['fecha'])
        other=wire.project_digital_history({'fechaCreacion':'2026-11-01T15:00:00+00:00'})
        self.assertEqual(next(r for r in other if r['codigo']=='RADICACION')['estado'],'PENDIENTE')


class PaymentDayTests(TransactionTestCase):
    def setUp(self):
        OwnQuoteTests.setUp(self)
        from apps.xcore_consumo.models import ConsentimientoConsumo
        self.context['condiciones'].update(tipoPagaduria='N',amortizacion='CI')
        sim=OwnQuoteTests.calculate(self)
        self.proof={k:sim[k] for k in ('monto','plazoMeses','periodicidad','tasaNominalNamv','cuota')}
        ConsentimientoConsumo.objects.create(solicitud=self.s,fecha_aceptacion=timezone.now(),
            aceptado=False,firmado=False,text_snapshot='SYNTHETIC_ONLY',text_hash='x'*64)

    def test_day_range_uses_existing_calendar_without_modifying_accepted_conditions(self):
        from datetime import datetime,timezone as tz
        from apps.xcore_consumo.models import SolicitudConsumo
        before=deepcopy(SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data)
        with patch.object(q.timezone,'now',return_value=datetime(2026,11,8,17,tzinfo=tz.utc)):
            for day,expected in ((10,'12/10/2026'),(1,'01/01/2027'),(20,'12/20/2026')):
                result=q.fuente_radicacion_personal(self.s,self.proof,day)
                self.assertEqual(result['payload']['contextoLinix']['fechaCuotaOrdinaria'],expected)
                calendar=result['evidencias']['calendarioPropuesto']
                self.assertEqual(calendar['diaSeleccionado'],day)
                self.assertFalse(calendar['calendarioContractualAcreditado']);self.assertEqual(calendar['tipoBase'],'PROYECTADO')
                self.assertGreaterEqual(calendar['diasHastaPrimeraCuota'],30)
        self.assertEqual(SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data,before)

    def test_invalid_day_and_browser_supplied_date_are_rejected(self):
        for day in (0,21,True,'10',1.5,'2027-01-10',None):
            with self.assertRaisesRegex(ValueError,'RADICACION_DIA_PAGO_ENTRE_1_Y_20_REQUERIDO'):
                q.fuente_radicacion_personal(self.s,self.proof,day)

