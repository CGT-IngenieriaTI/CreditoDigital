"""Owner HTTP contract using isolated fixtures; no real case/provider/network."""
from datetime import timedelta
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from unittest import skipUnless
from uuid import uuid4
import json

import requests
from django.core import signing
from django.db import connection, connections
from django.http import Http404
from django.test import TestCase,TransactionTestCase,SimpleTestCase,override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire,publico_seguimiento as owner
from tools import test_credihoy_cotizacion as fixture


@override_settings(CREDIHOY_TITULAR_ENABLED=True,CREDIHOY_RADICACION_TITULAR_ENABLED=True,
    CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'o'*64,
    CREDIHOY_INSTITUCIONAL_SECRET='SYNTHETIC_RELAY_'+'r'*64,
    CREDIHOY_TITULAR_URL='https://owner.example.invalid')
class RadicationDelegationTests(SimpleTestCase):
    def setUp(self):
        self.document='0000000099';self.rid=str(uuid4())
        self.grant=SimpleNamespace(pk=uuid4(),estado='VERIFICADO',revocado=False,
            sesion_expira_at=timezone.now()+timedelta(minutes=10))
        self.payload={'accesoId':str(self.grant.pk),'navegador':'b'*64,
            'radicacion':{'solicitudId':str(uuid4()),'operacion':'preparar'}}
        self.options=override_settings(CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[owner.document_hmac(self.document)])
        self.options.enable();self.addCleanup(self.options.disable)

    def request(self):
        with patch.object(wire,'request_packet') as call:
            owner.consult(self.grant,{'numeroDocumento':self.document},self.payload,self.rid)
        args=call.call_args.args
        return SimpleNamespace(method='POST',is_secure=lambda:True,path=owner.PATH,
            content_type='application/json',body=args[2],data=deepcopy(self.payload),headers=args[4])

    def test_radication_delegation_is_distinct_bound_and_does_not_become_read_or_quote_scope(self):
        request=self.request();rid,identity=owner.authorize(request)
        self.assertEqual(rid,self.rid);self.assertEqual(identity['_scopeRadicacion'],'solicitudes:radicar')
        self.assertNotIn('_scopeLectura',identity);self.assertNotIn('_scopeAutorizacion',identity)
        token=request.headers['Authorization'][7:]
        claims=signing.loads(token,key=owner.key(),salt=owner.SALT,max_age=owner.TTL,fallback_keys=[])
        for scope in ('solicitudes:leer','solicitudes:cotizar','solicitudes:autorizar'):
            changed={**claims,'scope':scope}
            request.headers={**request.headers,'Authorization':'Bearer '+signing.dumps(changed,key=owner.key(),salt=owner.SALT)}
            with self.assertRaises(ValueError):owner.authorize(request)

    def test_radication_delegation_rejects_changed_body_insecure_transport_expired_session_and_closed_permission(self):
        request=self.request();request.body=wire.encode({**self.payload,'radicacion':{'operacion':'confirmar'}})
        with self.assertRaises(ValueError):owner.authorize(request)
        request=self.request();request.is_secure=lambda:False
        with self.assertRaises(ValueError):owner.authorize(request)
        self.grant.sesion_expira_at=timezone.now()-timedelta(seconds=1)
        with self.assertRaises(wire.NoDisponible):self.request()
        self.grant.sesion_expira_at=timezone.now()+timedelta(minutes=10)
        with override_settings(CREDIHOY_RADICACION_TITULAR_ENABLED=False):
            with self.assertRaises(wire.NoDisponible):self.request()


class NonceLimitTests(TestCase):
    def test_shared_limit_preserves_hmac_namespace_and_rejects_replay(self):
        from django.utils.crypto import salted_hmac
        from apps.xcore_consumo.public_access_models import LimiteAccesoCredihoy
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_controles import limite
        rid=str(uuid4());keys=[('titular_delegacion',rid,1)]
        self.assertTrue(limite(keys))
        expected=salted_hmac('credihoy-publico:limite:titular_delegacion',rid,algorithm='sha256').hexdigest()
        row=LimiteAccesoCredihoy.objects.get(pk=expected)
        self.assertEqual(row.contador,1)
        self.assertFalse(limite(keys))
        row.refresh_from_db();self.assertEqual(row.contador,1)
        self.assertTrue(limite([('titular_delegacion',str(uuid4()),1)]))


@override_settings(ROOT_URLCONF='core.urls_credihoy_titular',DEBUG=False,
    CREDIHOY_TITULAR_ENABLED=True,CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'o'*64,
    CREDIHOY_INSTITUCIONAL_SECRET='SYNTHETIC_RELAY_'+'r'*64,
    CREDIHOY_TITULAR_URL='https://owner.example.invalid',
    CREDIHOY_PUBLICO_RESTRINGIDO=True,CREDIHOY_PUBLICO_REDES=['127.0.0.1/32'],
    CREDIHOY_PUBLICO_PROXIES=[],CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA='SYNTHETIC_ONLY',
    CREDIHOY_SEGUIMIENTO_DB_ALIAS='default',CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE='SYNTHETIC_OWNER_ONLY')
class OwnerTests(TransactionTestCase):
    def setUp(self):
        fixture.QuoteFlowTests.setUp(self)
        self.real_claim=owner.claim_delegation
        self.claim=patch.object(owner,'claim_delegation',return_value=True).start()
        self.addCleanup(patch.stopall)
        self.client=APIClient();self.calls=[]
        self.document=self.s.solicitante.numero_identificacion
        self.grant=SimpleNamespace(pk=uuid4(),estado='VERIFICADO',revocado=False,
            sesion_expira_at=timezone.now()+timedelta(minutes=15))
        self.payload={'accesoId':str(self.grant.pk),'navegador':'b'*64}
        options=override_settings(CREDIHOY_PUBLICO_IDENTIDADES_HMAC=['a'*64],
            CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[owner.document_hmac(self.document)],
            CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[str(self.s.pk)]},
            CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(timezone.now()+timedelta(hours=1)).isoformat())
        options.enable();self.addCleanup(options.disable)

    def transport(self,url,**kwargs):
        self.assertEqual(url,'https://owner.example.invalid'+owner.PATH)
        self.assertTrue(kwargs['verify']);self.assertFalse(kwargs['allow_redirects'])
        self.calls.append(kwargs)
        reply=self.client.post(owner.PATH,kwargs['data'],content_type='application/json',secure=True,
            HTTP_AUTHORIZATION=kwargs['headers']['Authorization'],HTTP_X_REQUEST_ID=kwargs['headers']['X-Request-ID'])
        response=requests.Response();response.status_code=reply.status_code;response._content=reply.content
        response._content_consumed=True;response.headers.update(reply.headers);return response

    def consult(self,**updates):
        with patch.object(wire.requests,'post',side_effect=self.transport),\
                patch('apps.xcore_consumo.services.credihoy_v2.unico.publico_http.simulacion_publica',return_value=None):
            return owner.consult(self.grant,{'numeroDocumento':self.document},
                {**self.payload,**updates},str(uuid4()))

    def replay(self,body=None,authorization=None,rid=None,secure=True):
        sent=self.calls[-1]
        return self.client.post(owner.PATH,body or sent['data'],content_type='application/json',secure=secure,
            HTTP_AUTHORIZATION=authorization or sent['headers']['Authorization'],
            HTTP_X_REQUEST_ID=rid or sent['headers']['X-Request-ID'])

    def test_own_listing_and_detail_are_real_owner_queries_with_read_only_actions(self):
        before=self.s.__class__.objects.count()
        packet=self.consult()
        self.assertEqual([v['referencia'] for v in packet['solicitudes']],[str(self.s.pk)])
        self.assertEqual(self.consult(solicitudId=str(self.s.pk))['solicitudes'][0]['acciones'],
            {'radicar':False,'firmar':False})
        self.assertEqual(self.s.__class__.objects.count(),before)
        self.assertNotIn('numeroDocumento',json.loads(self.calls[-1]['data']))

    @override_settings(CREDIHOY_TITULAR_FUENTE_ENABLED=False)
    def test_closed_source_never_queries_cases_and_still_rejects_unsigned_access(self):
        from django.db import connection
        with patch.object(connection,'cursor',side_effect=AssertionError('SOURCE_MUST_STAY_CLOSED')):
            with self.assertRaises(wire.NoDisponible):owner.read_owned({'numeroDocumento':self.document},self.payload)
        self.assertEqual(self.client.post(owner.PATH,self.payload,format='json',secure=True).status_code,403)
        with self.assertRaises(wire.NoDisponible):self.consult()

    @override_settings(CREDIHOY_CONSENTIMIENTO_REAL_ENABLED=True, CREDIHOY_NUEVO_TITULAR_ENABLED=True,
        CREDIHOY_FIRMA_OPERADOR_USERNAME=None, CREDIHOY_FIRMA_SOLICITUD_ID=None)
    def test_normal_capture_keeps_canonical_owner_through_signed_read_without_external_calls(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import crear_para_titular
        canonical=str(uuid4())
        new=crear_para_titular({'clave':canonical,'tipo_identificacion':'CC',
            'numero_identificacion':'0000000001','primer_apellido':'SINTETICO',
            'fecha_expedicion':'2005-01-01','celular':'3000000000','email':'synthetic@example.invalid',
            'monto':'400000','plazo':2},self.user)
        with override_settings(CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[str(self.s.pk),canonical]},
                CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[owner.document_hmac(self.document),owner.document_hmac('0000000001')]):
            with self.assertRaises(wire.NoEncontrado):self.consult(solicitudId=canonical)
            self.document='0000000001'
            with CaptureQueriesContext(connection) as queries:result=self.consult(solicitudId=canonical)
            self.assertEqual(result['solicitudes'][0]['referencia'],canonical)
            self.assertIsNone(result['solicitudes'][0]['institucional'])
            self.assertEqual(new.asesor_id,self.user.pk)
            self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE') for q in queries))
            with self.assertRaises(wire.NoEncontrado):self.consult(solicitudId=str(self.s.pk))

    def test_uuid_is_selector_not_authorization_in_both_directions(self):
        from apps.usuarios.models import Solicitante
        person=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='99999999',
            primer_apellido='OTRO',fecha_expedicion='2001-08-20',celular='3000000001',email='other@example.invalid')
        other=self.s.__class__.objects.create(solicitante=person,producto=self.s.producto,
            monto_solicitado=900000,metadatos={'modo':'CONSULTA_REAL_CONTROLADA'})
        with override_settings(CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[str(self.s.pk),str(other.pk)]},
                CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[owner.document_hmac(self.document),owner.document_hmac('99999999')]):
            with self.assertRaises(wire.NoEncontrado):self.consult(solicitudId=str(other.pk))
            self.document='99999999'
            with self.assertRaises(wire.NoEncontrado):self.consult(solicitudId=str(self.s.pk))
            self.assertEqual([r['referencia'] for r in self.consult()['solicitudes']],[str(other.pk)])

    def institutional_snapshot(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.linix_estado import _hash
        now=timezone.now()-timedelta(seconds=1)
        p={'versionContrato':'credihoy-linix-estado-v2','solicitudId':str(self.s.pk),'intencionId':str(uuid4()),
            'kFlujo':'12345','numeroSc':'99000000001','estado':'APROBADA_EN_LINIX_PENDIENTE_FIRMA',
            'actualizadoAt':now.isoformat(),'startEjecutados':1,
            'actividad':{'codigo':99001065,'nombre':'REQUIERE_FIRMA ELECTRONICA_PAGARE','kSerie':'201',
                         'ruta':'10003122','estado':'A','tipoEjecucion':'H'},
            'aprobacion':{'estadoSolicitud':'A','aprobada':True,'fecha':now.date().isoformat(),
                'aprobadorReferencia':'ACTOR_TEST','actividad':6774,'kSerie':'200','estadoActividad':'E'},
            'liquidacion':{'liquidada':False,'fecha':None},'pagare':{'tipo':'10','sucursal':'101','numero':'90001','estado':'U'},
            'firma':{'estado':'PENDIENTE','acreditada':False},
            'incidencias':{'registradas':5,'enTareasActivas':0,'enTareasEjecutadas':5},
            'procedencia':{'sistema':'LINIX','metodo':'SELECT_CA090_WORKFLOW_CA662','perfil':['390','111','CFA']}}
        p['evidenciaSha256']=_hash(p)
        return p

    def store_snapshot(self,p):
        from apps.xcore_consumo.models import SolicitudConsumo
        from apps.xcore_consumo.services.credihoy_v2.unico.linix_estado import KEY
        detail=SolicitudConsumo.objects.get(solicitud=self.s)
        detail.orchestration_data={**detail.orchestration_data,KEY:p}
        detail.save(update_fields=['orchestration_data'])

    def test_approved_owner_projection_is_private_read_only_and_preserves_channel_evaluation(self):
        from apps.xcore_consumo.models import EvaluacionConsumo
        from apps.utils.models import AuditLog
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.store_snapshot(self.institutional_snapshot())
        before=list(EvaluacionConsumo.objects.filter(solicitud=self.s).values());audits=AuditLog.objects.count()
        with CaptureQueriesContext(connection) as queries:rows=owner.read_owned({'numeroDocumento':self.document},self.payload)
        self.assertEqual(rows[0]['institucional']['estado'],'APROBADA_PENDIENTE_FIRMA')
        self.assertEqual(rows[0]['institucional']['firma'],'PENDIENTE')
        for value in ('ACTOR_TEST','90001','99001065','kSerie','numeroSc','aprobadorReferencia','evidenciaSha256'):
            self.assertNotIn(value,json.dumps(rows))
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE') for q in queries))
        self.assertEqual(list(EvaluacionConsumo.objects.filter(solicitud=self.s).values()),before)
        self.assertEqual(AuditLog.objects.count(),audits)
        self.assertEqual(self.consult()['solicitudes'][0]['institucional'],rows[0]['institucional'])

    def test_unverified_rejected_or_tampered_approval_never_becomes_public_result(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.linix_estado import _hash
        self.assertIsNone(self.consult()['solicitudes'][0]['institucional'])
        p=self.institutional_snapshot();p['aprobacion']['aprobada']=False
        p['evidenciaSha256']=_hash({k:v for k,v in p.items() if k!='evidenciaSha256'})
        self.store_snapshot(p)
        with self.assertRaises(wire.NoDisponible):self.consult()
        p=self.institutional_snapshot();p['firma']['acreditada']=True;self.store_snapshot(p)
        with self.assertRaises(wire.NoDisponible):self.consult()

    def test_wire_rejects_forged_signature_internal_fields_and_invalid_decision_dates(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_http import estado_publico
        from copy import deepcopy
        self.store_snapshot(self.institutional_snapshot())
        self.s.refresh_from_db();row=estado_publico(self.s)
        self.assertIsNotNone(wire.project_row(row)['institucional'])
        for changes in ({'firma':'FIRMADA'},{'estado':'RECHAZADA'},{'aprobador':'ACTOR_TEST'},
                        {'fechaDecision':'2026-02-31'},{'actualizadoAt':'2026-01-01T00:00:00'},
                        {'actualizadoAt':(timezone.now()+timedelta(days=1)).isoformat()}):
            bad=deepcopy(row);bad['institucional'].update(changes)
            with self.subTest(change=changes),self.assertRaises(wire.NoDisponible):wire.project_row(bad)

    def test_tampered_body_browser_uuid_or_request_id_cannot_reuse_delegation(self):
        self.consult()
        for payload in ({**self.payload,'numeroDocumento':'99999999'},
                {**self.payload,'solicitudId':str(uuid4())},{**self.payload,'navegador':'c'*64}):
            self.assertEqual(self.replay(body=wire.encode(payload)).status_code,403)
        self.assertEqual(self.replay(rid=str(uuid4())).status_code,403)
        self.assertEqual(self.replay(authorization='Bearer INVALID').status_code,403)
        self.assertEqual(self.replay(secure=False).status_code,403)

    def test_expired_delegation_wrong_purpose_and_wrong_key_are_rejected(self):
        self.consult();original=self.calls[-1]['headers']['Authorization'][7:]
        claims=signing.loads(original,key=owner.key(),salt=owner.SALT,fallback_keys=[])
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()+31):
            self.assertEqual(self.replay().status_code,403)
        for changes in ({'scope':'radicar'},{'aud':'/api/other/'},
                {'sesionExpiraAt':(timezone.now()-timedelta(seconds=1)).isoformat()}):
            token=signing.dumps({**claims,**changes},key=owner.key(),salt=owner.SALT)
            self.assertEqual(self.replay(authorization='Bearer '+token).status_code,403)
        token=signing.dumps(claims,key='WRONG_KEY',salt=owner.SALT)
        self.assertEqual(self.replay(authorization='Bearer '+token).status_code,403)

    def test_missing_owner_source_or_out_of_cohort_is_not_a_fictitious_empty_list(self):
        with override_settings(CREDIHOY_SEGUIMIENTO_DB_ALIAS=''):
            with self.assertRaises(wire.NoDisponible):self.consult()
        with override_settings(CREDIHOY_SEGUIMIENTO_DB_ALIAS='UNAPPROVED_SECONDARY_ALIAS'):
            with self.assertRaises(wire.NoDisponible):self.consult()
        with override_settings(CREDIHOY_TITULAR_DOCUMENTOS_HMAC=['0'*64]):
            with self.assertRaises(wire.NoDisponible):self.consult()
        from django.db import OperationalError
        with patch.object(owner,'read_owned',side_effect=OperationalError('PRIVATE_CONNECTION_DETAIL')):
            with self.assertRaises(wire.NoDisponible):self.consult()

    def test_missing_configured_case_never_becomes_successful_empty_tracking(self):
        for cases in ([],['INVALID'],[None],[str(uuid4())],[str(self.s.pk),str(uuid4())],
                [str(self.s.pk),str(self.s.pk)]):
            with self.subTest(cases=cases),override_settings(CREDIHOY_PUBLICO_ENSAYO={'solicitudes':cases}):
                with self.assertRaises(wire.NoDisponible):self.consult()
        with override_settings(CREDIHOY_PUBLICO_ENSAYO=None):
            with self.assertRaises(wire.NoDisponible):self.consult()
        # The source exists; another authenticated holder owns no cases.
        self.assertEqual(owner.read_owned({'numeroDocumento':'99999999'},self.payload),[])

    def test_wrong_product_or_unaccredited_context_is_unavailable_not_empty(self):
        for changes in ({'producto':'CONSUMO'},{'metadatos':{'modo':'HISTORICO_CONTROLADO'}}):
            self.s.__class__.objects.filter(pk=self.s.pk).update(**changes)
            with self.subTest(changes=changes),self.assertRaises(wire.NoDisponible):
                self.consult()

    def test_revoked_expired_unverified_session_never_reaches_owner(self):
        for field,value in [('revocado',True),('estado','ENVIADO'),
                ('sesion_expira_at',timezone.now()-timedelta(seconds=1))]:
            old=getattr(self.grant,field);setattr(self.grant,field,value)
            with self.assertRaises(wire.NoDisponible):self.consult()
            setattr(self.grant,field,old)
        self.assertEqual(self.calls,[])

    def test_http_owner_failure_never_falls_back_to_local_database(self):
        with patch.object(wire.requests,'post',side_effect=requests.ConnectionError),\
                patch.object(owner,'read_owned',side_effect=AssertionError('NO_DB_FALLBACK')) as db:
            with self.assertRaises(wire.NoDisponible):
                owner.consult(self.grant,{'numeroDocumento':self.document},self.payload,str(uuid4()))
        db.assert_not_called()

    def test_replay_rejected_and_nonce_store_unavailable_is_not_bypassed(self):
        self.consult()
        self.claim.return_value=False
        self.assertEqual(self.replay().status_code,403)
        self.claim.side_effect=wire.NoDisponible
        self.assertEqual(self.replay().status_code,503)

    def test_nonce_claim_requires_dedicated_limited_identity_storage(self):
        from django.conf import settings
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.publico_controles.limite',return_value=False) as store:
            with override_settings(CREDIHOY_TITULAR_NONCE_DB_ALIAS='default'):
                with self.assertRaises(wire.NoDisponible):self.real_claim(str(uuid4()))
            store.assert_not_called()
            with override_settings(CREDIHOY_TITULAR_NONCE_DB_ALIAS='nonce',
                    DATABASES={**settings.DATABASES,'nonce':{'NAME':'credihoy_9a','USER':'credihoy_9a_nonce'}}):
                rid=str(uuid4());self.assertFalse(self.real_claim(rid))
            store.assert_called_once_with([('titular_delegacion',rid,1)],using='nonce')


@skipUnless(connection.vendor=='postgresql','Source isolation uses two disposable PostgreSQL databases')
@override_settings(CREDIHOY_TITULAR_FUENTE_ENABLED=True,CREDIHOY_SEGUIMIENTO_DB_ALIAS='historico',
    CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE='SYNTHETIC_HISTORICAL_ONLY')
class HistoricalSourceTests(TransactionTestCase):
    databases={'default','historico'} if connection.vendor=='postgresql' else {'default'}

    def setUp(self):
        from apps.solicitudes.models import Solicitud
        from apps.usuarios.models import Solicitante
        from django.conf import settings
        from copy import deepcopy
        self.document='0000000001';self.key=str(uuid4())
        for alias,amount in [('default',900000),('historico',400000)]:
            person=Solicitante.objects.using(alias).create(tipo_identificacion='CC',
                numero_identificacion=self.document,primer_apellido='SINTETICO',
                fecha_expedicion='2001-01-01',celular='3000000000',email='test@example.invalid')
            Solicitud.objects.using(alias).create(id=self.key,solicitante=person,
                producto='CREDIHOY UNICO (NUEVO)',monto_solicitado=amount,plazo_solicitado=2,
                estado='CONSENTIMIENTO_FIRMADO',metadatos={'modo':'CONSULTA_REAL_CONTROLADA'})
        # Django test DBs have generated names and local test credentials. Policy
        # metadata is synthetic; the actual connections remain distinct test DBs.
        config=deepcopy(settings.DATABASES)
        for alias in ('default','historico'):connections[alias].ensure_connection()
        self.assertNotEqual(connections['default'].settings_dict['NAME'],connections['historico'].settings_dict['NAME'])
        config['default']['NAME']='credito_digital'
        config['historico'].update(NAME='credihoy_validacion',USER='credihoy_titular_historico',
            OPTIONS={'options':'-c default_transaction_read_only=on -c search_path=credihoy_lectura,pg_catalog'})
        options=override_settings(DATABASES=config,CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[self.key]})
        options.enable();self.addCleanup(options.disable)

    def test_same_uuid_and_document_never_merge_or_read_operative_related_models(self):
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connections['default']) as operative,\
                patch('apps.xcore_consumo.services.credihoy_v2.unico.publico_http.estado_publico',
                    side_effect=AssertionError('NO_IMPLICIT_DEFAULT_PROJECTIONS')):
            rows=owner.read_owned({'numeroDocumento':self.document},{'solicitudId':self.key})
        self.assertEqual(len(operative),0)
        self.assertEqual(rows[0]['monto'],'400000.00')
        self.assertEqual(rows[0]['procedencia']['origen'],'HISTORICO_PILOTO')
        self.assertEqual(rows[0]['procedencia']['estadoRegistrado'],'Consentimiento firmado')
        self.assertIsNone(rows[0]['institucional']);self.assertIsNone(rows[0]['simulacion'])
        self.assertIsNone(rows[0]['requisitosCumplidos'])
        with self.assertRaises(Http404):owner.read_owned({'numeroDocumento':'0000000002'},{'solicitudId':self.key})

    def test_missing_historical_object_never_falls_back_to_same_operative_uuid(self):
        from apps.solicitudes.models import Solicitud
        Solicitud.objects.using('historico').filter(pk=self.key).delete()
        with self.assertRaises(wire.NoDisponible):owner.read_owned({'numeroDocumento':self.document},{'solicitudId':self.key})
        self.assertTrue(Solicitud.objects.using('default').filter(pk=self.key).exists())

    def test_historical_read_mode_does_not_redirect_new_draft_to_original_database(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import autoservicio
        from apps.xcore_consumo.services.credihoy_v2.unico import progreso_privacidad as progress_notice
        from apps.solicitudes.models import Solicitud, ProgresoTemporalCredihoy
        import base64
        identity={'numeroDocumento':self.document,'_sesionReferencia':str(uuid4()),
            '_sesionExpiraAt':(timezone.now()+timedelta(minutes=15)).isoformat()}
        config={'version':progress_notice.VERSION,'sha256':progress_notice.SHA256,'consistenciaPoliticaReferencia':'LOCAL_ONLY',
            'respaldoCustodiaReferencia':'LOCAL_ONLY','limpiezaMaxSegundos':60}
        with override_settings(CREDIHOY_AUTOSERVICIO_ENABLED=True,CREDIHOY_AUTOSERVICIO_DB_ALIAS='default',
                CREDIHOY_TITULAR_SECRET='SYNTHETIC_'+'x'*64,CREDIHOY_PROGRESO_VALIDACION=config,
                CREDIHOY_PROGRESO_AES_KEY_B64=base64.b64encode(b'p'*32).decode()):
            notice=progress_notice.presentar(identity)
            draft=autoservicio.operar(identity,
                {'operacion':'borrador','accion':'guardar','clave':str(uuid4()),'operacionId':str(uuid4()),
                 'version':0,'datos':{},'promotor':None,
                 'aceptacion':{k:notice[k] for k in ('version','sha256','presentacion')}|{'acepta':True}})
        self.assertTrue(ProgresoTemporalCredihoy.objects.using('default').filter(pk=draft['referencia']).exists())
        self.assertFalse(ProgresoTemporalCredihoy.objects.using('historico').exists())
        self.assertFalse(Solicitud.objects.using('default').filter(pk=draft['referencia']).exists())
        self.assertFalse(Solicitud.objects.using('historico').filter(pk=draft['referencia']).exists())

    def test_history_provenance_cannot_assert_cd6_approval_or_foreign_internal_data(self):
        row=owner.read_owned({'numeroDocumento':self.document},{})[0]
        for change in ({'host':'PRIVATE'},{'origen':'ORACLE'},{'estadoRegistrado':'APROBADA'},
                {'actualizadoAt':(timezone.now()+timedelta(days=1)).isoformat()}):
            with self.subTest(change=change),self.assertRaises(wire.NoDisponible):
                wire.project_row({**row,'procedencia':{**row['procedencia'],**change}})
        with self.assertRaises(wire.NoDisponible):wire.project_row({**row,'requisitosCumplidos':16})
