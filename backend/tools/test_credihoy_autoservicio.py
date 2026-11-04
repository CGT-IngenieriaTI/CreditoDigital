"""Temporal 61.9 contract on disposable databases; never institutional evidence."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.db.models.query import QuerySet
from django.http import Http404
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from apps.solicitudes.models import Solicitud, ProgresoTemporalCredihoy as Progreso
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import SolicitudConsumo, ConsentimientoConsumo, EvaluacionConsumo
from apps.xcore_consumo.services.credihoy_v2.unico import autoservicio as service, progreso_temporal as temporal
from apps.xcore_consumo.services.credihoy_v2.unico import progreso_privacidad as privacy, publico_institucional as wire


@override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=True,CREDIHOY_AUTOSERVICIO_DB_ALIAS='default',
    CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'o'*64,
    CREDIHOY_PROGRESO_AES_KEY_B64=base64.b64encode(b'p'*32).decode())
class AutoservicioTests(TransactionTestCase):
    @override_settings(SESSION_ENGINE='django.contrib.sessions.backends.db',SESSION_COOKIE_NAME='credihoy_publico_session')
    def test_anonymous_probe_during_session_rotation_can_expire_the_new_browser_cookie(self):
        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.http import HttpResponse
        from django.test import RequestFactory
        cookie='credihoy_publico_session';store=SessionStore()
        store['synthetic_browser']='old';store.save();old_key=store.session_key
        middleware=SessionMiddleware(lambda r:HttpResponse())
        stale=RequestFactory().get('/api/v1/credihoy/publico/sesion/',HTTP_COOKIE=cookie+'='+old_key)
        middleware.process_request(stale)
        verification=RequestFactory().post('/api/v1/credihoy/publico/verificar/',HTTP_COOKIE=cookie+'='+old_key)
        middleware.process_request(verification)
        verification.session['synthetic_browser']='new';verification.session.cycle_key()
        verification.session['synthetic_grant']='verified'
        authenticated=middleware.process_response(verification,HttpResponse())
        self.assertTrue(authenticated.cookies[cookie].value)
        self.assertIsNone(stale.session.get('synthetic_grant'))
        anonymous=middleware.process_response(stale,HttpResponse())
        self.assertEqual(anonymous.cookies[cookie]['max-age'],0)

    def setUp(self):
        self.start=timezone.now()
        self.identity={'numeroDocumento':'0000000001','primerApellido':'SINTETICO','fechaExpedicion':'2005-01-01',
            'contacto':'+573000000000','canal':'SMS','correo':None}
        self.identity=self.reautenticar(self.identity,self.start)
        self.other=self.reautenticar({**self.identity,'numeroDocumento':'0000000002'},self.start)
        cfg=override_settings(CREDIHOY_PROGRESO_VALIDACION={'version':privacy.VERSION,'sha256':privacy.SHA256,
            'consistenciaPoliticaReferencia':'SYNTHETIC_ONLY','respaldoCustodiaReferencia':'LOCAL_DUMP_ONLY',
            'limpiezaMaxSegundos':60},CREDIHOY_TITULAR_FUENTE_ENABLED=True,
            CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[temporal.document_hmac(i['numeroDocumento']) for i in (self.identity,self.other)],
            CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA='SYNTHETIC_READ_ONLY',
            CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(self.start+timedelta(hours=1)).isoformat())
        cfg.enable();self.addCleanup(cfg.disable)
        self.body={'operacion':'borrador','accion':'guardar','clave':str(uuid4()),'operacionId':str(uuid4()),
            'version':0,'datos':{},'promotor':None,'aceptacion':self.aceptacion(self.identity)}

    def reautenticar(self, identity, now):
        return {**identity,'_sesionReferencia':str(uuid4()),'_sesionExpiraAt':(now+timedelta(minutes=15)).isoformat(),
            '_scopeLectura':'solicitudes:leer','_requestId':str(uuid4())}

    def aceptacion(self, identity):
        notice=privacy.presentar(identity)
        return {k:notice[k] for k in ('version','sha256','presentacion')}|{'acepta':True}

    def guardar(self, body=None, identity=None):return service.operar(identity or self.identity,body or self.body)

    def test_first_save_preserves_all_values_and_monthly_periodicity(self):
        values={'monto':'400000','plazo':2,'email':'synthetic@example.test','recogerCredihoy':False}
        saved=self.guardar({**self.body,'datos':values})
        self.assertEqual(saved['datos'],{**values,'monto':'400000.00','periodicidadPago':'MENSUAL'})
        restored=self.guardar({'operacion':'borrador','accion':'retomar'})
        self.assertEqual(restored['referencia'],saved['referencia'])
        self.assertEqual(restored['datos'],saved['datos'])
        updated=self.guardar({**self.body,'version':1,'operacionId':str(uuid4()),'datos':{'plazo':3}})
        self.assertEqual(updated['datos'],{**saved['datos'],'plazo':3})
        self.assertEqual(Progreso.objects.count(),1)

    def test_non_monthly_periodicity_is_rejected_without_persisting(self):
        with self.assertRaises(Exception):self.guardar({**self.body,'datos':{'periodicidadPago':'ANUAL'}})
        self.assertFalse(Progreso.objects.exists())

    def enviar(self):
        self.guardar()
        body={**self.body,'accion':'enviar','version':1,'operacionId':str(uuid4()),'datos':{'monto':'400000','plazo':2,'recogerCredihoy':False}}
        return self.guardar(body),body

    def test_restricted_scope_requires_exact_session_and_unexpired_window_before_any_write(self):
        session=self.identity['_sesionReferencia'];now=timezone.now()
        valid={'sesiones':[session],'hasta':(now+timedelta(minutes=5)).isoformat()}
        for scope in ({}, {'sesiones':[],'hasta':valid['hasta']},
                {**valid,'sesiones':[str(uuid4())]}, {**valid,'sesiones':[session,str(uuid4())]},
                {**valid,'hasta':now.isoformat()}, {**valid,'hasta':(now+timedelta(hours=2)).isoformat()},
                {**valid,'hasta':'invalid'}, {**valid,'extra':True}):
            with override_settings(CREDIHOY_PUBLICO_RESTRINGIDO=True,CREDIHOY_AUTOSERVICIO_ALCANCE=scope,
                    CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(now+timedelta(minutes=10)).isoformat()):
                self.assertFalse(service.sesion_habilitada(session))
                with self.assertRaises(wire.NoDisponible):self.guardar()
        self.assertFalse(Progreso.objects.exists());self.assertFalse(Solicitud.objects.exists())
        with override_settings(CREDIHOY_PUBLICO_RESTRINGIDO=True,CREDIHOY_AUTOSERVICIO_ALCANCE=valid,
                CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(now+timedelta(minutes=10)).isoformat()):
            self.assertTrue(service.sesion_habilitada(session));self.guardar()
            with self.assertRaises(wire.NoDisponible):self.guardar(identity=self.other)
        self.assertEqual(Progreso.objects.count(),1)

    def test_submitted_request_is_read_from_same_alias_without_evaluation_or_history(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_seguimiento as owner
        sent,_=self.enviar();key=sent['referencia']
        rows=service.leer_enviadas(self.identity,{'solicitudId':key})
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['referencia'],key)
        self.assertEqual(rows[0]['procedencia']['origen'],'FUENTE_OPERATIVA')
        self.assertFalse(rows[0]['ofertaDisponible']);self.assertIsNone(rows[0]['institucional'])
        self.assertIsNone(rows[0]['simulacion']);self.assertIsNone(rows[0]['requisitosCumplidos'])
        self.assertEqual(service.leer_enviadas(self.other,{'solicitudId':key}),[])
        with override_settings(CREDIHOY_TITULAR_FUENTE_ENABLED=True):
            self.assertEqual(owner.read_owned(self.identity,{'solicitudId':key})[0]['referencia'],key)
        self.assertFalse(EvaluacionConsumo.objects.exists())

    def test_read_after_capture_closure_preserves_own_case_and_cannot_write(self):
        from django.test.utils import CaptureQueriesContext
        from apps.xcore_consumo.services.credihoy_v2.unico import publico_seguimiento as owner
        sent,_=self.enviar();key=sent['referencia']
        with override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=False):
            fresh=self.reautenticar(self.identity,timezone.now())
            with CaptureQueriesContext(connection) as queries:
                rows=owner.read_owned(fresh,{'solicitudId':key})
            self.assertEqual([r['referencia'] for r in rows],[key])
            self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE'} for q in queries))
            self.assertEqual(service.leer_enviadas(self.other,{'solicitudId':key}),[])
            self.assertFalse(service.sesion_habilitada(fresh['_sesionReferencia']))
            with self.assertRaises(wire.NoDisponible):self.guardar(identity=fresh)
        self.assertEqual(SolicitudConsumo.objects.count(),1)
        self.assertFalse(ConsentimientoConsumo.objects.exists());self.assertFalse(EvaluacionConsumo.objects.exists())

    def test_read_requires_its_own_scope_window_cohort_and_privacy_even_when_capture_closed(self):
        self.enviar()
        for changes in ({'_scopeLectura':'solicitudes:borrador'},{'_requestId':'invalid'},
                {'_sesionExpiraAt':timezone.now().isoformat()}, {'numeroDocumento':'99999999'}):
            with self.subTest(changes=changes),override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=False):
                with self.assertRaises(wire.NoDisponible):service.leer_enviadas({**self.identity,**changes},{})
        for cfg in ({'CREDIHOY_PROGRESO_VALIDACION':{}},{'CREDIHOY_TITULAR_FUENTE_ENABLED':False},
                {'CREDIHOY_PUBLICO_AUTORIZACION_HASTA':timezone.now().isoformat()}):
            with self.subTest(cfg=cfg),override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=False,**cfg):
                with self.assertRaises(wire.NoDisponible):service.leer_enviadas(self.identity,{})

    def test_authorized_save_is_encrypted_temporary_without_applicant_or_dossier(self):
        result=self.guardar(body={**self.body,'datos':{'email':'synthetic@example.invalid'}})
        row=Progreso.objects.get()
        self.assertNotIn('synthetic@example.invalid',row.datos_cifrados)
        self.assertNotIn(self.identity['numeroDocumento'],row.titular_hmac)
        self.assertFalse(Solicitante.objects.exists());self.assertFalse(Solicitud.objects.exists())
        evidence=AuditLog.objects.get()
        self.assertEqual(evidence.event,'credihoy_progreso_aceptado')
        self.assertNotIn('synthetic@example.invalid',str(evidence.payload));self.assertNotIn('datos',evidence.payload)
        for model in (SolicitudConsumo,ConsentimientoConsumo,EvaluacionConsumo):self.assertFalse(model.objects.exists())
        self.assertEqual(row.vence_at-row.actividad_at,timedelta(minutes=60))
        self.assertFalse(result['solicitudRegistrada']);self.assertEqual(wire.project_draft(result),result)

    def test_resume_at_59_minutes_requires_reauthentication_and_renews_only_progress(self):
        self.guardar();now=self.start+timedelta(minutes=59)
        again=self.reautenticar(self.identity,now)
        with patch.object(temporal.timezone,'now',return_value=now):
            result=service.operar(again,{'operacion':'borrador','accion':'retomar','actividad':True})
        self.assertEqual(result['referencia'],self.body['clave'])
        self.assertEqual(Progreso.objects.get().vence_at,now+timedelta(minutes=60))
        self.assertEqual(again['_sesionExpiraAt'],(now+timedelta(minutes=15)).isoformat())

    def test_automatic_reads_do_not_refresh_progress_deadline(self):
        self.guardar();deadline=Progreso.objects.get().vence_at
        now=self.start+timedelta(minutes=59)
        with patch.object(temporal.timezone,'now',return_value=now):
            result=service.operar(self.reautenticar(self.identity,now),{'operacion':'borrador','accion':'retomar'})
        self.assertEqual(Progreso.objects.get().vence_at,deadline)
        self.assertEqual(result['consultadoAt'],now.isoformat())
        self.assertEqual(result['venceAt'],deadline.isoformat())

    def test_cleanup_does_not_delete_progress_refreshed_by_concurrent_committed_activity(self):
        if connection.vendor!='postgresql':self.skipTest('Row-lock concurrency requires PostgreSQL')
        self.guardar();selected=Event();original=QuerySet.delete
        def deleting(query):
            if query.model is Progreso:selected.set()
            return original(query)
        def cleanup():
            close_old_connections()
            try:return temporal.limpiar(ahora=self.start+timedelta(minutes=60))
            finally:close_old_connections()
        with patch.object(QuerySet,'delete',deleting),ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Progreso.objects.update(actividad_at=self.start+timedelta(minutes=59),vence_at=self.start+timedelta(minutes=119))
                future=pool.submit(cleanup)
                self.assertTrue(selected.wait(5))
            self.assertEqual(future.result(timeout=10),0)
        self.assertEqual(Progreso.objects.count(),1)

    def test_cleanup_failure_does_not_log_database_details_or_form_content(self):
        with patch('apps.solicitudes.management.commands.credihoy_limpiar_progreso.limpiar',
                side_effect=RuntimeError('SYNTHETIC_PRIVATE_CONTENT')):
            with self.assertRaisesMessage(Exception,'LIMPIEZA_NO_COMPLETADA') as error:
                call_command('credihoy_limpiar_progreso')
        self.assertNotIn('SYNTHETIC_PRIVATE_CONTENT',str(error.exception))

    def test_exact_60_minute_boundary_is_unavailable_and_physically_deleted(self):
        self.guardar();row=Progreso.objects.get();now=row.vence_at
        with patch.object(temporal.timezone,'now',return_value=now):
            result=service.operar(self.reautenticar(self.identity,now),{'operacion':'borrador','accion':'retomar'})
        self.assertIsNone(result);self.assertEqual(Progreso.objects.count(),0)

    def test_unauthorized_expired_session_cannot_read_or_extend_progress(self):
        self.guardar();deadline=Progreso.objects.get().vence_at
        with patch.object(temporal.timezone,'now',return_value=self.start+timedelta(minutes=16)):
            with self.assertRaises(wire.NoDisponible):service.operar(self.identity,{'operacion':'borrador','accion':'retomar'})
        self.assertEqual(Progreso.objects.get().vence_at,deadline)

    def test_presentation_is_bound_to_session_and_explicit_acceptance(self):
        for ack in (None,{**self.body['aceptacion'],'acepta':False},
                {**self.body['aceptacion'],'sha256':'f'*64},self.aceptacion(self.other)):
            with self.assertRaises(Exception):self.guardar({**self.body,'aceptacion':ack})
        self.assertFalse(Progreso.objects.exists())

    def test_replay_update_version_and_foreign_uuid_are_not_new_dossiers(self):
        self.guardar();self.guardar()
        self.assertEqual(Progreso.objects.count(),1);self.assertEqual(Progreso.objects.get().version,1)
        update={**self.body,'version':1,'operacionId':str(uuid4()),'datos':{'monto':'400000'}}
        self.assertEqual(self.guardar(update)['version'],2)
        with self.assertRaises(ValueError):self.guardar({**update,'datos':{'monto':'500000'}})
        with self.assertRaises(ValueError):self.guardar({**update,'operacionId':str(uuid4())})
        with self.assertRaises(Http404):self.guardar({**self.body,'aceptacion':self.aceptacion(self.other)},self.other)
        self.assertFalse(Solicitud.objects.exists())

    def test_two_holders_have_isolated_progress_and_aad_prevents_ciphertext_swaps(self):
        self.guardar();other={**self.body,'clave':str(uuid4()),'aceptacion':self.aceptacion(self.other)}
        self.guardar(other,self.other)
        a,b=list(Progreso.objects.order_by('id'))
        Progreso.objects.filter(pk=b.pk).update(datos_cifrados=a.datos_cifrados)
        b.refresh_from_db()
        with self.assertRaises(wire.NoDisponible):temporal.descifrar(b)
        self.assertEqual(Progreso.objects.count(),2)

    def test_free_document_scores_credentials_and_invalid_values_are_rejected(self):
        for extra in ({'numeroDocumento':'99999999'},{'asesor':'CGTE'},{'OTP':'123456'},{'aprobar':True}):
            with self.assertRaises(ValueError):self.guardar({**self.body,**extra})
        for values in ({'plazo':49},{'monto':'0'},{'email':'invalid'},{'password':'PRIVATE'},{'score':999}):
            with self.assertRaises(Exception):self.guardar({**self.body,'datos':values})
        self.assertFalse(Progreso.objects.exists())

    def test_disabled_feature_missing_backup_policy_or_wrong_key_stays_closed(self):
        for cfg in ({'CREDIHOY_AUTOSERVICIO_ENABLED':False},{'CREDIHOY_PROGRESO_VALIDACION':{}},
                {'CREDIHOY_PROGRESO_AES_KEY_B64':'invalid'}):
            with override_settings(**cfg),self.assertRaises(Exception):self.guardar()
        self.assertFalse(Progreso.objects.exists())

    def test_send_is_durable_idempotent_without_credit_or_provider_side_effects(self):
        result,body=self.enviar()
        self.assertTrue(result['solicitudRegistrada']);self.assertEqual(result['fase'],'ENVIADA')
        self.assertIsNone(result['venceAt']);self.assertEqual(wire.project_draft(result),result)
        self.assertFalse(Progreso.objects.exists());self.assertEqual(Solicitud.objects.count(),1)
        self.assertIsNone(Solicitud.objects.get().asesor_id)
        self.assertEqual(Solicitud.objects.get().metadatos['modo'],'AUTOGESTION_ENVIADA')
        self.assertEqual(self.guardar(body),result);self.assertEqual(AuditLog.objects.count(),2)
        self.assertEqual(service.operar(self.identity,{'operacion':'borrador','accion':'retomar'}),result)
        detail=SolicitudConsumo.objects.get(solicitud_id=result['referencia'])
        self.assertEqual(detail.estado,'CONSENTIMIENTO_PENDIENTE')
        self.assertEqual(detail.form_data['monto'],'400000.00');self.assertEqual(detail.form_data['plazoMeses'],2)
        projected=service.formulario_desde_envio(Solicitud.objects.get())
        self.assertEqual(projected['periodicidadPago'],'MENSUAL');self.assertIs(projected['recogerCredihoy'],False)
        self.assertEqual(detail.orchestration_data[service.VINCULO_CONSUMO]['operacionEnvioOriginal'],body['operacionId'])
        self.assertEqual(SolicitudConsumo.objects.count(),1)
        for model in (ConsentimientoConsumo,EvaluacionConsumo):self.assertFalse(model.objects.exists())

    def test_failed_audit_rolls_back_submission_and_preserves_temporary_progress(self):
        self.guardar();original=QuerySet.create
        def create(q,**kw):
            if q.model is AuditLog:raise RuntimeError('LOCAL_AUDIT_FAILURE')
            return original(q,**kw)
        with patch.object(QuerySet,'create',new=create),self.assertRaises(RuntimeError):
            self.guardar({**self.body,'accion':'enviar','version':1,'operacionId':str(uuid4()),'datos':{'monto':'400000','plazo':2,'recogerCredihoy':False}})
        self.assertFalse(Solicitud.objects.exists());self.assertFalse(Solicitante.objects.exists())
        self.assertEqual(Progreso.objects.get().version,1)

    def test_failed_consumption_link_rolls_back_entire_submission(self):
        self.guardar()
        with patch.object(service,'vincular_consumo_enviada',side_effect=RuntimeError('LINK_FAILURE')):
            with self.assertRaisesRegex(RuntimeError,'LINK_FAILURE'):
                self.guardar({**self.body,'accion':'enviar','version':1,'operacionId':str(uuid4()),'datos':{'monto':'400000','plazo':2,'recogerCredihoy':False}})
        self.assertFalse(Solicitud.objects.exists());self.assertFalse(SolicitudConsumo.objects.exists())
        self.assertFalse(Solicitante.objects.exists());self.assertFalse(EvaluacionConsumo.objects.exists())
        self.assertEqual(Progreso.objects.get().version,1)

    def test_same_authenticated_replay_recovers_missing_link_without_new_dossier_or_acceptance(self):
        result,body=self.enviar();s=Solicitud.objects.get();metadata=s.metadatos.copy();created=s.created_at
        SolicitudConsumo.objects.all().delete()
        self.assertEqual(self.guardar(body),result)
        s.refresh_from_db()
        self.assertEqual(s.metadatos,metadata);self.assertEqual(s.created_at,created)
        self.assertEqual(Solicitud.objects.count(),1);self.assertEqual(SolicitudConsumo.objects.count(),1)
        self.assertEqual(AuditLog.objects.count(),2)
        self.assertFalse(ConsentimientoConsumo.objects.exists());self.assertFalse(EvaluacionConsumo.objects.exists())

    def test_replay_preserves_existing_conditions_calendar_journal_and_evaluation(self):
        _,body=self.enviar();detail=SolicitudConsumo.objects.get()
        detail.form_data={**detail.form_data,'periodicidadPago':'MENSUAL','recogerCredihoy':True}
        detail.orchestration_data={**detail.orchestration_data,'calendario':{'dia':10},
            'tarifa':{'version':3},'journal':{'estado':'INCIERTA'}}
        detail.estado='CORE_CONSULTADO';detail.save()
        evaluated=EvaluacionConsumo.objects.create(solicitud=detail.solicitud,decision_final='NO_AUTORIZADA',
            resultados={'fixture':'SYNTHETIC_PRESERVATION_ONLY'})
        before=(detail.form_data.copy(),detail.orchestration_data.copy(),detail.updated_at)
        self.guardar(body);detail.refresh_from_db();evaluated.refresh_from_db()
        self.assertEqual((detail.form_data,detail.orchestration_data,detail.updated_at),before)
        self.assertEqual(detail.estado,'CORE_CONSULTADO');self.assertEqual(EvaluacionConsumo.objects.count(),1)
        self.assertEqual(evaluated.decision_final,'NO_AUTORIZADA')

    def test_link_refuses_historical_or_closed_dossier_without_relabeling_it(self):
        self.enviar();s=Solicitud.objects.get();detail=SolicitudConsumo.objects.get();detail.delete()
        s.metadatos={**s.metadatos,'modo':'CONSULTA_REAL_CONTROLADA'};s.save()
        with transaction.atomic(),self.assertRaisesRegex(ValueError,'ENVIO_AUTOGESTION'):
            service.vincular_consumo_enviada(s,'default')
        self.assertFalse(SolicitudConsumo.objects.exists());s.refresh_from_db()
        self.assertEqual(s.metadatos['modo'],'CONSULTA_REAL_CONTROLADA')
        s.metadatos={**s.metadatos,'modo':'AUTOGESTION_ENVIADA'};s.estado='FINALIZADA';s.save()
        with transaction.atomic(),self.assertRaisesRegex(ValueError,'ANTECEDENTE_CERRADO'):
            service.vincular_consumo_enviada(s,'default')
        self.assertFalse(SolicitudConsumo.objects.exists())

    def test_other_holder_cannot_recover_consumption_link_or_submit_foreign_key(self):
        _,body=self.enviar()
        with self.assertRaises(Http404):self.guardar({**body,'aceptacion':self.aceptacion(self.other)},self.other)
        self.assertEqual(Solicitud.objects.count(),1);self.assertEqual(SolicitudConsumo.objects.count(),1)

    def test_consumption_link_requires_same_alias_and_submission_transaction(self):
        self.enviar();s=Solicitud.objects.get();before=SolicitudConsumo.objects.get().updated_at
        with self.assertRaisesRegex(ValueError,'TRANSACCION_ENVIO'):service.vincular_consumo_enviada(s,'default')
        s._state.db='historico'
        with transaction.atomic(),self.assertRaisesRegex(ValueError,'TRANSACCION_ENVIO'):
            service.vincular_consumo_enviada(s,'default')
        self.assertEqual(SolicitudConsumo.objects.get().updated_at,before)

    def test_direct_submission_outside_progress_transaction_fails_before_any_write(self):
        with self.assertRaisesRegex(ValueError,'TRANSACCION_PROGRESO'):
            service.registrar_enviada(self.identity,'default',uuid4(),1,{'monto':'400000','plazo':2},None,{},uuid4(),'a'*64)
        self.assertFalse(Solicitud.objects.exists());self.assertFalse(SolicitudConsumo.objects.exists())
        self.assertFalse(Solicitante.objects.exists());self.assertFalse(AuditLog.objects.exists())

    def test_concurrent_same_send_has_one_request_one_detail_and_no_evaluation(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL concurrency contract')
        self.guardar();barrier=Barrier(2)
        body={**self.body,'accion':'enviar','version':1,'operacionId':str(uuid4()),'datos':{'monto':'400000','plazo':2,'recogerCredihoy':False}}
        def send(_):
            close_old_connections();barrier.wait(timeout=10)
            try:return self.guardar(body)
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(Solicitud.objects.count(),1)
        self.assertEqual(SolicitudConsumo.objects.count(),1);self.assertFalse(EvaluacionConsumo.objects.exists())
        self.assertFalse(ConsentimientoConsumo.objects.exists());self.assertFalse(Progreso.objects.exists())

    def test_incomplete_send_does_not_fabricate_conditions_or_create_applicant(self):
        self.guardar()
        with self.assertRaisesRegex(ValueError,'SOLICITUD_INCOMPLETA'):
            self.guardar({**self.body,'accion':'enviar','version':1,'operacionId':str(uuid4())})
        self.assertFalse(Solicitud.objects.exists());self.assertEqual(Progreso.objects.count(),1)

    def test_cleanup_runs_with_writes_disabled_and_never_deletes_sent_dossier(self):
        self.enviar();self.guardar({**self.body,'clave':str(uuid4()),'aceptacion':self.aceptacion(self.other)},self.other)
        Progreso.objects.update(vence_at=self.start-timedelta(seconds=1))
        out=StringIO()
        with override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=False,CREDIHOY_PROGRESO_VALIDACION={}):
            call_command('credihoy_limpiar_progreso',stdout=out)
        self.assertIn('ELIMINADO=1',out.getvalue());self.assertFalse(Progreso.objects.exists())
        self.assertEqual(Solicitud.objects.count(),1);self.assertEqual(AuditLog.objects.count(),3)

    def test_connection_restart_recovers_progress_without_a_process_cache(self):
        first=self.guardar();close_old_connections()
        second=service.operar(self.identity,{'operacion':'borrador','accion':'retomar'})
        self.assertEqual(first['referencia'],second['referencia']);self.assertEqual(first['datos'],second['datos'])

    def test_concurrent_same_save_has_one_row_and_one_version(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL concurrency contract')
        barrier=Barrier(2)
        def save(_):
            close_old_connections();barrier.wait(timeout=10)
            try:return self.guardar()
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,range(2)))
        self.assertEqual({r['referencia'] for r in results},{self.body['clave']})
        self.assertEqual({r['version'] for r in results},{1});self.assertEqual(Progreso.objects.count(),1)

    def test_pg_temp_shadowing_does_not_redirect_progress_or_submission(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL search_path contract')
        with connection.cursor() as q:
            q.execute('CREATE TEMP TABLE solicitudes_progresotemporalcredihoy (LIKE public.solicitudes_progresotemporalcredihoy INCLUDING ALL)')
            q.execute('CREATE TEMP TABLE solicitudes_solicitud (LIKE public.solicitudes_solicitud INCLUDING ALL)')
        try:
            self.enviar()
            with connection.cursor() as q:
                q.execute('SELECT count(*) FROM public.solicitudes_solicitud');self.assertEqual(q.fetchone(),(1,))
                q.execute('SELECT count(*) FROM pg_temp.solicitudes_solicitud');self.assertEqual(q.fetchone(),(0,))
                q.execute('SELECT count(*) FROM pg_temp.solicitudes_progresotemporalcredihoy');self.assertEqual(q.fetchone(),(0,))
        finally:
            with connection.cursor() as q:
                q.execute('DROP TABLE pg_temp.solicitudes_solicitud,pg_temp.solicitudes_progresotemporalcredihoy')

    def test_postgres_table_is_unlogged_not_identity_storage(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL unlogged contract')
        with connection.cursor() as q:
            q.execute("SELECT relpersistence FROM pg_class WHERE oid='public.solicitudes_progresotemporalcredihoy'::regclass")
            self.assertEqual(q.fetchone(),('u',))
