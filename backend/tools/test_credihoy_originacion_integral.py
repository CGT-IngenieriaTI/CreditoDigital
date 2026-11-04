"""Native public/relay/owner/PG integration; all identities and providers synthetic."""
import base64
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.test import override_settings
from django.utils import timezone
from apps.solicitudes.models import Solicitud
from apps.xcore_consumo.models import ConsentimientoConsumo,EvaluacionConsumo,SolicitudConsumo
from apps.xcore_consumo.services.credihoy_v2.unico import autoservicio as auto,progreso_privacidad as notice
from tools.test_credihoy_simulador_local import SimuladorLocalTests,BASE


class OriginacionIntegralTests(SimuladorLocalTests):
    def setUp(self):
        super().setUp()
        self.until=(timezone.now()+timedelta(hours=1)).isoformat()
        cfg=override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=True,CREDIHOY_AUTOSERVICIO_DB_ALIAS='default',
            CREDIHOY_PUBLICO_SOLICITUDES_POR_API=True,CREDIHOY_AUTORIZACION_DB_ALIAS='default',
            CREDIHOY_AUTORIZACION_TITULAR_ENABLED=True,CREDIHOY_AUTORIZACION_SOLICITUDES=[],
            CREDIHOY_PROGRESO_AES_KEY_B64=base64.b64encode(b'd'*32).decode(),
            CREDIHOY_AUTOSERVICIO_ALCANCE={'modo':'SESION_PERSONAL_VERIFICADA','hasta':self.until},
            CREDIHOY_ADMISION_ENSAYO={'version':'ADMISION_IDENTIDADES_ENSAYO_V1','hasta':self.until,
                'referencia':'ORDEN_RESPONSABLE_20261103_TODAS_IDENTIFICACIONES'},
            CREDIHOY_PUBLICO_AUTORIZACION_HASTA=self.until,
            CREDIHOY_PROGRESO_VALIDACION={'version':notice.VERSION,'sha256':notice.SHA256,
                'consistenciaPoliticaReferencia':'SYNTHETIC','respaldoCustodiaReferencia':'SYNTHETIC','limpiezaMaxSegundos':60})
        cfg.enable();self.addCleanup(cfg.disable)
        from django.db import connection
        with connection.cursor() as c:
            c.execute('CREATE OR REPLACE VIEW public.credihoy_estudio_publico AS SELECT '
                'NULL::jsonb AS resumen_tecnico,NULL::uuid AS solicitud_id,'
                'NULL::timestamptz AS registrado_en,NULL::bigint AS registro_id WHERE false')

    def command(self,body):
        return self.client.post(BASE+'solicitudes/',body,format='json',secure=True,
            HTTP_ORIGIN='https://testserver',HTTP_X_CSRFTOKEN=self.boot['csrfToken'],
            HTTP_X_ACCESO_REFERENCIA=str(self.grant.pk))

    def save(self):
        packet=self.command({'operacion':'borrador','accion':'retomar'})
        self.assertEqual(packet.status_code,200,packet.data)
        n=packet.data['avisoTemporal'];reference=str(uuid4())
        self.save_body={'operacion':'borrador','accion':'guardar','clave':reference,'operacionId':str(uuid4()),
            'version':0,'datos':{'monto':'400000','plazo':4,'recogerCredihoy':False,'email':'proposed@example.test'},
            'promotor':None,'aceptacion':{k:n[k] for k in ('version','sha256','presentacion')}|{'acepta':True}}
        saved=self.command(self.save_body);self.assertEqual(saved.status_code,200,saved.data)
        self.assertEqual(saved.data['borrador']['datos']['periodicidadPago'],'MENSUAL')
        self.assertEqual(saved.data['borrador']['datos']['monto'],'400000.00')
        return reference

    def test_native_save_simulate_send_read_choices_same_dossier(self):
        ref=self.save()
        simulated=self.post({'borradorId':ref,'operacion':'simular','escenario':{'monto':'400000','plazoMeses':4,'linea':'VENTANILLA'}})
        self.assertEqual(simulated.status_code,200,simulated.data)
        self.assertEqual(simulated.data['borradorId'],ref);self.assertNotIn('solicitudId',simulated.data)
        self.assertEqual(simulated.data['resultado']['seguro']['valorMensualEstimado'],'360.00')
        self.assertGreater(Decimal(simulated.data['resultado']['cuotaEstimada']),Decimal('100000'))
        replay=self.command({'operacion':'borrador','accion':'retomar'})
        self.assertEqual(replay.data['borrador']['referencia'],ref)
        send={**self.save_body,'accion':'enviar','version':1,'operacionId':str(uuid4())}
        received=self.command(send);self.assertEqual(received.status_code,200,received.data)
        self.assertTrue(received.data['borrador']['solicitudRegistrada'])
        self.assertEqual(self.command(send).data['borrador'],received.data['borrador'])
        response=self.client.get(BASE+'solicitudes/',secure=True,HTTP_X_ACCESO_REFERENCIA=str(self.grant.pk))
        self.assertEqual(response.status_code,200,response.data)
        rows=[r for r in response.data['solicitudes'] if r['referencia']==ref]
        self.assertEqual(len(rows),1)

        self.assertEqual(rows[0]['envio']['operacionId'],send['operacionId'])
        self.assertEqual(rows[0]['envio']['solicitud']['datos']['plazo'],4)
        self.assertEqual(rows[0]['simulacionLocal']['resultado']['cuotaEstimada'],simulated.data['resultado']['cuotaEstimada'])
        # The disposable default role is not the institutional restricted writer.
        # Exercise the native complement/projection with its non-restricted test profile.
        from apps.xcore_consumo.services.credihoy_v2.unico import consentimiento_real as consent,publico_institucional as wire
        with override_settings(CREDIHOY_PUBLICO_RESTRINGIDO=False,CREDIHOY_AUTORIZACION_SOLICITUDES=[ref]):
            choice=wire.project_authorization(consent.operar_autogestion(
                {**self.identity,'canal':'SMS','_scopeAutorizacion':'solicitudes:autorizar','_requestId':str(uuid4())},
                {'operacion':'autorizacion','accion':'consultar_complemento','solicitudId':ref}))
        self.assertEqual(choice['complemento']['fuente'],'ELECCION_PERSONAL_ENVIO')
        payment=self.post({'solicitudId':ref,'operacion':'simular','escenario':{'monto':'400000','plazoMeses':4,'linea':'VENTANILLA'}})
        self.assertEqual(payment.status_code,200,payment.data)
        self.assertEqual(payment.data['resultado']['cuotaEstimada'],simulated.data['resultado']['cuotaEstimada'])
        self.assertEqual(Solicitud.objects.filter(pk=ref).count(),1)
        self.assertEqual(Solicitud.objects.get(pk=ref).estado,'INICIADA')
        self.assertFalse(ConsentimientoConsumo.objects.exists())

    @override_settings(CREDIHOY_CENTRALES_OTP_ENABLED=True)
    def test_native_submitted_choices_reach_bureau_intent_without_another_complement_or_provider(self):
        from unittest.mock import patch
        from apps.xcore_consumo.services.credihoy_v2.unico import consentimiento_real as consent,centrales_personal as bureau
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional_http import _identity
        ref=self.save()
        sent=self.command({**self.save_body,'accion':'enviar','version':1,'operacionId':str(uuid4())})
        self.assertEqual(sent.status_code,200,sent.data)
        identity={**self.identity,'canal':'SMS','_scopeAutorizacion':'solicitudes:autorizar',
            '_scopeLectura':'solicitudes:leer','_requestId':str(uuid4())}
        with override_settings(CREDIHOY_PUBLICO_RESTRINGIDO=False,CREDIHOY_AUTORIZACION_SOLICITUDES=[ref]),patch('requests.sessions.Session.request',side_effect=AssertionError('NO_PROVIDER')):
            doc=consent.presentar_autogestion(ref,identity,campos={'nombre':'SYNTHETIC HOLDER','ciudad':'SYNTHETIC CITY'},clave=str(uuid4()))
            consent.aceptar_autogestion(ref,identity,presentacion=doc['presentacion'],acepta=True,clave=str(uuid4()))
            operation=str(uuid4());view=bureau.presentar(ref,identity,operation)
            result=bureau.aceptar(ref,identity,operation,view['presentacion'],True)
            self.assertEqual(result['centrales']['estado'],'PENDIENTE_OTP')
        detail=SolicitudConsumo.objects.get(solicitud_id=ref)
        self.assertNotIn(auto.COMPLEMENTO_PERSONAL,detail.orchestration_data)
        self.assertFalse(EvaluacionConsumo.objects.filter(solicitud_id=ref).exists())

    def test_other_holder_cannot_simulate_draft_and_expired_draft_is_not_reused(self):
        from apps.solicitudes.models import ProgresoTemporalCredihoy
        ref=self.save()
        self.identity={**self.identity,'numeroDocumento':'0000000002'}
        from django.http import Http404
        from apps.xcore_consumo.services.credihoy_v2.unico import simulacion_lectura as sim
        with self.assertRaises(Http404):sim.operar_simulacion(self.identity,{'borradorId':ref,'operacion':'simular'},str(uuid4()))
        ProgresoTemporalCredihoy.objects.filter(pk=ref).update(vence_at=timezone.now()-timedelta(seconds=1))
        expired=self.post({'borradorId':ref,'operacion':'simular'})
        self.assertNotEqual(expired.status_code,200)

    @override_settings(CREDIHOY_CENTRALES_OTP_ENABLED=True)
    def test_expired_bureau_attempt_is_reconciled_then_replaced_only_by_a_new_personal_act_atomically(self):
        from copy import deepcopy
        from unittest.mock import patch
        from apps.utils.models import AuditLog
        from apps.xcore_consumo.services.credihoy_v2.unico import centrales_personal as bureau
        self.test_native_submitted_choices_reach_bureau_intent_without_another_complement_or_provider()
        sid=self.save_body['clave']
        identity={**self.identity,'canal':'SMS','_scopeAutorizacion':'solicitudes:autorizar',
            '_scopeLectura':'solicitudes:leer','_requestId':str(uuid4())}
        with override_settings(CREDIHOY_PUBLICO_RESTRINGIDO=False,CREDIHOY_AUTORIZACION_SOLICITUDES=[sid]):
            detail=SolicitudConsumo.objects.get(solicitud_id=sid)
            binding=detail.orchestration_data[bureau.KEY]['vinculo']
            verification='VE'+'2'*32;verification_hash=bureau._sha(verification.encode())
            result={'vinculo':binding,'estado':'ENVIADO','verificationReferencia':verification_hash,
                'operacionVerify':str(uuid4()),'confirmadoEn':None,'estadoProveedor':'pending'}
            bureau.registrar_resultado(sid,identity,bureau._token(result,bureau.SALT_PROVEEDOR))
            detail.refresh_from_db();old=deepcopy(detail.orchestration_data[bureau.KEY])
            later=timezone.now()+timedelta(minutes=11)
            with patch.object(bureau.timezone,'now',return_value=later):
                proof={'solicitudId':sid,'operacionId':binding['operacionId'],'verificationSha256':verification_hash,
                    'cuentaSid':'AC'+'0'*32,'servicioSid':'VA'+'1'*32,'estadoProveedor':'expired',
                    'origen':'TWILIO_CONSOLE_CONFIRMADO_RESPONSABLE',
                    'referenciaFuente':'https://1console.twilio.com/account/AC'+'0'*32+'/us1/verify/logs/'+verification,
                    'creadaProveedorEn':binding['aceptadoEn'],'journalSha256':bureau._sha(bureau._canonical(old))}
                case=Solicitud.objects.select_related('solicitante','consumo_detail').get(pk=sid)
                bureau.registrar_expiracion_conciliada(case,'default',proof)
                bureau.registrar_expiracion_conciliada(case,'default',proof)
                self.assertEqual(AuditLog.objects.filter(solicitud_id=sid,event=bureau.EXPIRACION_EVENTO).count(),1)
                detail.refresh_from_db();self.assertEqual(detail.orchestration_data[bureau.KEY],old)
                identity={**identity,'_sesionReferencia':str(uuid4()),'_sesionExpiraAt':(later+timedelta(minutes=15)).isoformat()}
                operation=str(uuid4())
                self.assertEqual(bureau.consultar(sid,identity)['centrales']['estado'],'SIN_ACEPTACION')
                presentation=bureau.presentar(sid,identity,operation)['presentacion']
                real_save=bureau._guardar
                def fail_after_save(*args):
                    real_save(*args)
                    raise RuntimeError('SYNTHETIC_COMMIT_FAILURE')
                with patch.object(bureau,'_guardar',side_effect=fail_after_save),self.assertRaises(RuntimeError):
                    bureau.aceptar(sid,identity,operation,presentation,True)
                detail.refresh_from_db()
                self.assertEqual(detail.orchestration_data[bureau.KEY],old)
                self.assertNotIn(bureau.ARCHIVO_KEY,detail.orchestration_data)
                bureau.aceptar(sid,identity,operation,presentation,True)
                detail.refresh_from_db()
                self.assertEqual(detail.orchestration_data[bureau.ARCHIVO_KEY][binding['operacionId']]['journal'],old)
                self.assertEqual(detail.orchestration_data[bureau.KEY]['vinculo']['operacionId'],operation)
                self.assertEqual(detail.orchestration_data[bureau.KEY]['estado'],'PENDIENTE_OTP')
                self.assertFalse(AuditLog.objects.filter(solicitud_id=sid,event='credihoy_continuacion_solicitada_v1').exists())
                self.assertFalse(EvaluacionConsumo.objects.filter(solicitud_id=sid).exists())

    def test_open_trial_does_not_admit_an_unlisted_historical_case(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import simulacion_lectura as sim
        from django.http import Http404
        with override_settings(CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[]}):
            with self.assertRaises(Http404):sim.operar_simulacion(self.identity,self.body(),str(uuid4()))

    def test_registered_email_is_not_automatically_verified_by_sms(self):
        import json
        from apps.xcore_consumo.services.otp_crypto import encrypt_text
        from apps.xcore_consumo.services.credihoy_v2.unico import publico as access
        self.grant.datos_cifrados=encrypt_text(json.dumps({'numeroDocumento':self.identity['numeroDocumento'],
            'nombre':'SINTETICO','correo':'registered@example.test','contacto':'+573000000000','canal':'SMS'}))
        self.grant.save(update_fields=['datos_cifrados'])
        response=self.client.get(BASE+'sesion/',secure=True)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['perfil']['correoRegistrado'],'registered@example.test')
        self.assertFalse(response.data['perfil']['correoVerificado'])
        self.assertEqual(response.data['perfil']['contactoEnmascarado'],'***00')

    def test_browser_bundle_native_api_and_pg_submission_recovery(self):
        import json,subprocess,threading,mimetypes
        from pathlib import Path
        from http.server import BaseHTTPRequestHandler,HTTPServer
        from urllib.parse import urlsplit
        from apps.xcore_consumo.services.otp_crypto import encrypt_text
        self.grant.datos_cifrados=encrypt_text(json.dumps({'numeroDocumento':self.identity['numeroDocumento'],
            'nombre':'SINTETICO','correo':'registered@example.test','contacto':'+573000000000','canal':'SMS'}))
        self.grant.save(update_fields=['datos_cifrados'])
        api=Path(__file__).resolve().parents[3]/'api_data_v1'
        import os
        artifact=os.environ.get('CREDIHOY_BROWSER_ARTIFACT','originacion-producto-v1')
        if artifact not in {'originacion-producto-v1','originacion-continuidad-v1'}:raise ValueError('ARTEFACTO_BROWSER_NO_AUTORIZADO')
        dist=api/'tools/artifacts'/artifact/'frontend/dist-asociado'
        client=self.client
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.handle_request()
            def do_POST(self):self.handle_request()
            def handle_request(self):
                if self.path.startswith('/api/'):
                    headers={'HTTP_ORIGIN':'https://testserver','HTTP_HOST':'testserver'}
                    for key,value in self.headers.items():
                        if key.lower().startswith('x-'):headers['HTTP_'+key.upper().replace('-','_')]=value
                    if self.command=='POST':
                        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
                        response=client.post(self.path,raw,content_type='application/json',secure=True,**headers)
                    else:response=client.get(self.path,secure=True,**headers)
                    raw=response.content;status=response.status_code;kind=response.get('Content-Type','application/json')
                else:
                    target=(dist/('index.html' if urlsplit(self.path).path=='/' else urlsplit(self.path).path.lstrip('/'))).resolve()
                    if not target.is_relative_to(dist.resolve()) or not target.is_file():self.send_error(404);return
                    raw=target.read_bytes();status=200;kind=mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
                try:
                    self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        server=HTTPServer(('127.0.0.1',0),Handler)
        def serve():
            from django.db import connections
            try:server.serve_forever()
            finally:connections.close_all()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        try:
            p=subprocess.run(['node',str(api/'tools/credihoy_producto_browser.cjs'),'http://127.0.0.1:'+str(server.server_port),artifact],cwd=api,capture_output=True,text=True,timeout=100)
            self.assertEqual(p.returncode,0,p.stderr[-5000:]+p.stdout[-2000:])
            cases=Solicitud.objects.filter(metadatos__modo=auto.MODO)
            self.assertEqual(cases.count(),1)
            self.assertEqual(cases.get().plazo_solicitado,4)
            self.assertNotIn('recogerCredihoy',cases.get().metadatos['envio']['datos'])
        finally:server.shutdown();thread.join(timeout=10);server.server_close()
