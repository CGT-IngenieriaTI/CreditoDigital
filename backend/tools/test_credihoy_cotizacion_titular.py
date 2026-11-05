"""Delegated quote/acceptance integration on synthetic records, never real Oracle."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TransactionTestCase,override_settings
from django.utils import timezone
from rest_framework.test import APIClient,APIRequestFactory
from rest_framework.request import Request
from rest_framework.parsers import JSONParser
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import SolicitudConsumo
from apps.xcore_consumo.public_access_models import AccesoPublicoCredihoy as Acceso
from apps.xcore_consumo.services.credihoy_v2.unico import cotizacion as quotes,publico_seguimiento as owner,publico_institucional as wire
from tools.test_credihoy_cotizacion import QuoteFlowTests
from tools.test_credihoy_preparacion_producto import fact
from tools.test_credihoy_integracion_financiera import scalar


@override_settings(CREDIHOY_COTIZACION_TITULAR_ENABLED=True,CREDIHOY_COTIZACION_ENABLED=True,
    CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'o'*64,
    CREDIHOY_INSTITUCIONAL_SECRET='SYNTHETIC_RELAY_'+'r'*64,
    CREDIHOY_COTIZACION_DB_ALIAS='cotizacion',
    DATABASE_ROUTERS=['apps.xcore_consumo.services.credihoy_v2.unico.publico_restringido.CotizacionPropietariaRouter'])
class QuoteAliasTests(TransactionTestCase):
    databases={'default','cotizacion'}
    body=lambda self,*args,**kwargs:DelegatedQuoteTests.body(self,*args,**kwargs)
    operate=lambda self,*args,**kwargs:DelegatedQuoteTests.operate(self,*args,**kwargs)

    def setUp(self):
        DelegatedQuoteTests.setUp(self)
        opts=override_settings(CREDIHOY_COTIZACION_SOLICITUDES=[str(self.s.pk)],
            CREDIHOY_COTIZACION_SOURCE_REFERENCE='SYNTHETIC_OPERATIONAL_ONLY',
            CREDIHOY_SEGUIMIENTO_DB_ALIAS='historico')
        opts.enable();self.addCleanup(opts.disable)

    def test_quote_writer_persists_separately_without_changing_historical_reader(self):
        from django.db import connections,router
        from tools.test_credihoy_cotizacion import decision_oferta_fixture
        decision_oferta_fixture(self.s,self.user)
        result=self.operate()
        self.assertIsNotNone(result['oferta']);self.motor.cotizar.assert_called_once()
        self.assertFalse(connections['cotizacion'].in_atomic_block)
        self.assertTrue(SolicitudConsumo.objects.using('cotizacion').get(solicitud_id=self.s.pk).orchestration_data[quotes.KEY])
        self.assertEqual(router.db_for_read(SolicitudConsumo),'default')
        with override_settings(CREDIHOY_TITULAR_FUENTE_ENABLED=False):
            self.assertIsNotNone(self.operate()['oferta'])
        self.motor.cotizar.assert_called_once()

    def test_writer_context_rejects_other_business_writes_and_always_resets(self):
        from django.db import router
        from apps.xcore_consumo.tasas_models import TasaComercialCredihoy
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_restringido import fuente_cotizacion,alias_cotizacion
        with self.assertRaises(PermissionDenied):
            with fuente_cotizacion():
                self.assertEqual(alias_cotizacion(),'cotizacion')
                self.assertEqual(router.db_for_write(AuditLog),'cotizacion')
                router.db_for_write(TasaComercialCredihoy)
        self.assertEqual(alias_cotizacion(),'default')


@override_settings(ROOT_URLCONF='core.urls_credihoy_titular',DEBUG=False,
    CREDIHOY_COTIZACION_TITULAR_ENABLED=True,CREDIHOY_COTIZACION_ENABLED=True,
    CREDIHOY_TITULAR_ENABLED=True,CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'o'*64,
    CREDIHOY_INSTITUCIONAL_SECRET='SYNTHETIC_RELAY_'+'r'*64,
    CREDIHOY_TITULAR_URL='https://owner.example.invalid',
    CREDIHOY_SEGUIMIENTO_DB_ALIAS='default',CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE='SYNTHETIC_OPERATIONAL',
    CREDIHOY_SEGUIMIENTO_TRANSPORTE='HTTP_PROPIETARIO',
    CREDIHOY_PUBLICO_RESTRINGIDO=True,CREDIHOY_PUBLICO_REDES=['127.0.0.1/32'],
    CREDIHOY_PUBLICO_PROXIES=[],CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA='SYNTHETIC_ONLY')
class DelegatedQuoteTests(TransactionTestCase):
    def setUp(self):
        QuoteFlowTests.setUp(self)
        self.doc=self.s.solicitante.numero_identificacion
        self.grant=Acceso.objects.create(estado='VERIFICADO',revocado=False,navegador_hmac='b'*64,
            identidad_hmac='a'*64,peticion_hmac='c'*64,expira_at=timezone.now()+timedelta(minutes=2),
            sesion_expira_at=timezone.now()+timedelta(minutes=15))
        self.identity={'numeroDocumento':self.doc,'_sesionReferencia':str(self.grant.pk),
            '_sesionExpiraAt':self.grant.sesion_expira_at.isoformat()}
        opts=override_settings(CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[str(self.s.pk)]},
            CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[owner.document_hmac(self.doc)],CREDIHOY_PUBLICO_IDENTIDADES_HMAC=['a'*64],
            CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(timezone.now()+timedelta(hours=1)).isoformat())
        opts.enable();self.addCleanup(opts.disable)
        self.motor=Mock();self.motor.cotizar.side_effect=scalar
        self.commercial['tasaComercialEa']='18.00'
        self.client=APIClient();self.client.force_authenticate(None);self.calls=[]
        self.real_consult=owner.consult
        # This isolated DB has no institutional nonce credentials/alias. Reuse
        # the actual durable counter in it; do not bypass nonce/replay checks.
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_controles import limite
        nonce=patch.object(owner,'claim_delegation',side_effect=lambda rid:limite([('titular_delegacion',rid,1)]))
        nonce.start();self.addCleanup(nonce.stop)

    def body(self,op='cotizar',**kwargs):return {'solicitudId':str(self.s.pk),'operacion':op,**kwargs}

    def operate(self,op='cotizar',**kwargs):
        return quotes.operacion_titular(self.identity,self.body(op,**kwargs),str(uuid4()),cliente=self.motor)

    def complete(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import funcional
        from apps.xcore_consumo.models import EvaluacionConsumo
        from tools.test_credihoy_preparacion_producto import row_aportes
        detail=SolicitudConsumo.objects.get(solicitud=self.s);form=detail.form_data
        detail.form_data={};detail.save(update_fields=['form_data'])
        # QuoteFlow's deliberately partial read fixture cannot render the full
        # administrative state. Only omit that unrelated response projection;
        # the existing save/evaluate service still writes its real test records.
        with patch.object(funcional,'estado_funcional',return_value={}):
            for action,data in (('guardar_datos',form),('evaluar',{})):
                funcional.procesar_funcional(self.s.pk,self.user,accion=action,payload=data,
                    clave=funcional.clave_funcional(self.s),request_id=str(uuid4()))
        evaluation=EvaluacionConsumo.objects.get(solicitud=self.s)
        result=evaluation.resultados[funcional.KEY]
        result['reglas']=[r for r in result['reglas'] if r['codigo']!='UNICO_FILA_14']+[row_aportes()]
        evaluation.save(update_fields=['resultados'])
        quotes.registrar_base_calendario(self.s.pk,self.user,fact(timezone.localdate()))
        quotes._guardar_dia_titular(self.s,self.identity,10,str(uuid4()))
        from tools.test_credihoy_cotizacion import decision_oferta_fixture
        decision_oferta_fixture(self.s,self.user)
        self.operate()
        return quotes.preparar_oferta_linix(self.s.pk,None,titular=self.identity,
            tasa_ea=fact(Decimal('18.00')))

    def test_owner_quote_uses_existing_motor_once_and_does_not_require_associate_staff(self):
        before=type(self.s).objects.count()
        p=self.operate();again=self.operate()
        self.assertEqual(p['cuota'],again['cuota']);self.motor.cotizar.assert_called_once()
        self.assertIsNone(p['oferta']);self.assertIsNone(p['cuota'])
        self.assertFalse(p['autorizaStart']);self.assertEqual(before,type(self.s).objects.count())
        self.assertTrue(AuditLog.objects.filter(event='CREDIHOY_COTIZACION_SOLICITADA',
            actor='titular:'+str(self.grant.pk)).exists())

    def test_acceptance_is_personal_durable_idempotent_and_not_a_signature_or_start(self):
        offer=self.complete()
        p=self.operate('aceptar',ofertaSha256=offer['sha256'],acepta=True)
        again=self.operate('aceptar',ofertaSha256=offer['sha256'],acepta=True)
        self.assertTrue(p['aceptacion']['aceptada']);self.assertEqual(p['aceptacion'],again['aceptacion'])
        self.assertFalse(p['autorizaStart']);self.assertFalse(p['autorizaDesembolso'])
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_OFERTA_ACEPTADA_TITULAR').count(),1)
        self.assertNotIn('titularReferencia',wire.project_quote(p))
        d=SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data
        self.assertEqual(d['credihoy_aceptaciones_oferta_v1'][offer['sha256']]['sesionReferencia'],str(self.grant.pk))
        self.assertEqual(quotes.leer_aceptacion_oferta(self.s)['ofertaSha256'],offer['sha256'])
        from dataclasses import fields,MISSING
        from apps.xcore_consumo.services.credihoy_v2.unico.elegibilidad import ContextoElegibilidad
        required={f.name:None for f in fields(ContextoElegibilidad) if f.default is MISSING}
        required.update(solicitud_id=str(self.s.pk),titular_referencia=offer['titularReferencia'],
            perfil=('390','111','CFA'),reglas=(),decision_autorizada=False)
        context=quotes.aplicar_oferta_aceptada_a_contexto(ContextoElegibilidad(**required),self.s)
        self.assertEqual(context.aceptacion_oferta_huella,offer['sha256'])
        self.assertFalse(context.decision_autorizada)
        self.assertIsNotNone(context.aceptacion_fecha)
        detail=SolicitudConsumo.objects.get(solicitud=self.s)
        detail.orchestration_data['credihoy_aceptaciones_oferta_v1'][offer['sha256']]['titularReferencia']='f'*64
        detail.save(update_fields=['orchestration_data'])
        with self.assertRaises(ValueError):quotes.leer_aceptacion_oferta(self.s)

    def test_partial_conditions_foreign_owner_expired_session_and_historical_source_block(self):
        p=self.operate()
        with self.assertRaisesRegex(ValueError,'OFERTA_NO_DISPONIBLE'):
            self.operate('aceptar',ofertaSha256='a'*64,acepta=True)
        original=self.identity;self.identity={**original,'numeroDocumento':'99999999'}
        with self.assertRaises(Http404):self.operate('consultar')
        self.identity={**original,'_sesionExpiraAt':(timezone.now()-timedelta(seconds=1)).isoformat()}
        with self.assertRaises(wire.NoDisponible):self.operate('consultar')
        self.identity=original
        with override_settings(CREDIHOY_SEGUIMIENTO_DB_ALIAS='historico'):
            with self.assertRaises(wire.NoDisponible):self.operate('consultar')
        with override_settings(CREDIHOY_COTIZACION_TITULAR_ENABLED=False):
            with self.assertRaises(wire.NoDisponible):self.operate('consultar')

    def test_offer_invalidates_after_rate_form_or_evaluation_changes_and_no_acceptance_is_inferred(self):
        offer=self.complete()
        with patch.object(quotes,'_evaluacion_huella',return_value='f'*64):
            with self.assertRaises(ValueError):self.operate('aceptar',ofertaSha256=offer['sha256'],acepta=True)
        self.commercial['tasaComercialEa']='19.20'
        with self.assertRaises(ValueError):self.operate('aceptar',ofertaSha256=offer['sha256'],acepta=True)
        self.commercial['tasaComercialEa']='18.00'
        self.s.monto_solicitado=Decimal('500000');self.s.save()
        with self.assertRaises(ValueError):self.operate('aceptar',ofertaSha256=offer['sha256'],acepta=True)
        self.assertFalse(AuditLog.objects.filter(event='CREDIHOY_OFERTA_ACEPTADA_TITULAR').exists())

    def test_uncertain_quote_is_not_repeated_and_browser_cannot_supply_a_rate_or_staff(self):
        self.motor.cotizar.side_effect=TimeoutError('SYNTHETIC_PRIVATE')
        self.assertIsNone(self.operate()['oferta']);self.assertIsNone(self.operate()['oferta'])
        self.motor.cotizar.assert_called_once()
        with self.assertRaises(ValueError):quotes.operacion_titular(self.identity,
            {**self.body(),'tasaNominalNamv':'1'},str(uuid4()),cliente=self.motor)

    def test_concurrent_quote_observes_claim_and_never_repeats_inflight_motor(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import connection,connections
        if connection.vendor!='postgresql':self.skipTest('Requires real PostgreSQL row locking')
        entered=Event();release=Event()
        def slow(payload,rid):
            entered.set()
            if not release.wait(8):raise TimeoutError('LOCAL_TEST_RELEASE_TIMEOUT')
            return scalar(payload,rid)
        def operate():
            try:return self.operate()
            finally:connections.close_all()
        self.motor.cotizar.side_effect=slow
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(operate)
            try:
                self.assertTrue(entered.wait(5))
                second=pool.submit(operate).result(timeout=5)
                self.assertIsNone(second['oferta'])
                self.motor.cotizar.assert_called_once()
            finally:release.set()
            result=first.result(timeout=5)
        self.assertEqual(self.operate()['cuota'],result['cuota'])
        self.motor.cotizar.assert_called_once()

    def test_ventanilla_rate_is_server_catalogue_selection_not_hardcoded_libranza(self):
        from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
        selected=SeleccionTarifaCredihoy.objects.get(solicitud=self.s)
        rid=uuid4()
        selected.evidencia={**selected.evidencia,'codigo':'200','tipo':'V','requestId':str(rid)}
        selected.evidencia_sha256=sha256(json.dumps(selected.evidencia,sort_keys=True).encode()).hexdigest()
        self.commercial.update(tasaComercialNamv='17.69',tasaComercialEa='19.20')
        SeleccionTarifaCredihoy.objects.create(solicitud=self.s,responsable=self.user,request_id=rid,
            evidencia=selected.evidencia,evidencia_sha256=selected.evidencia_sha256,resultado=self.commercial)
        def motor(payload,rid):
            p=scalar(payload,rid);p['parametrosAplicados']['tasaNominalNamv']=payload['condiciones']['tasaNominalNamv']
            return p
        self.motor.cotizar.side_effect=motor
        p=self.operate()
        self.assertIsNone(p['oferta'])
        self.assertEqual(self.motor.cotizar.call_args.args[0]['condiciones']['tasaNominalNamv'],'17.69')
        self.assertEqual(self.motor.cotizar.call_args.args[0]['condiciones']['tipoPagaduria'],'V')

    def test_offer_expiry_and_wrong_acceptance_hash_do_not_write_acceptance(self):
        offer=self.complete()
        with self.assertRaises(ValueError):self.operate('aceptar',ofertaSha256='f'*64,acepta=True)
        with patch.object(quotes.timezone,'now',return_value=timezone.now()+timedelta(hours=2)):
            with self.assertRaises((ValueError,wire.NoDisponible)):
                self.operate('aceptar',ofertaSha256=offer['sha256'],acepta=True)
        self.assertFalse(AuditLog.objects.filter(event='CREDIHOY_OFERTA_ACEPTADA_TITULAR').exists())

    def manual_quote(self,payload,rid,*,match_native=False):
        from pathlib import Path
        import sys
        root=Path(__file__).resolve().parents[3]/'api_data_v1'
        sys.path.insert(0,str(root));self.addCleanup(lambda:sys.path.remove(str(root)))
        from incluirtec.services.credihoy_scalar_quote import manual_execution_quote
        e=json.loads((root/'tools/fixtures/credihoy_cuota_manual_sintetica.json').read_text())
        e['solicitudId']=str(self.s.pk)
        if match_native:
            e['resultado']['cuota']='210180'
            for row in e['resultado']['cuotas']:row.update(IC='10000',SV='180',total='210180')
        return manual_execution_quote(e,payload,rid)

    def test_manual_observation_does_not_prepare_contractual_offer_or_acceptance(self):
        from django.db import transaction
        with transaction.atomic():_,payload=quotes._contexto(self.s)
        rid=str(uuid4());manual=self.manual_quote(payload,rid)
        self.assertIsNone(manual['vigenteHasta'])
        self.assertFalse(manual['evidenciaManual']['coberturaContractualCompleta'])
        with patch.object(quotes,'leer_cotizacion_guardada',return_value={**manual,'calculoRequestId':rid}):
            with self.assertRaisesRegex(ValueError,'OFERTA_REQUIERE_CUOTA_LINIX_VIGENTE'):
                quotes.preparar_oferta_linix(self.s.pk,None,titular=self.identity)
        data=SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data
        self.assertNotIn('credihoy_oferta_linix_actual',data)
        self.assertNotIn('credihoy_aceptaciones_oferta_v1',data)
        self.motor.cotizar.assert_not_called()

    def test_manual_result_cannot_reuse_native_offer_even_with_same_id_and_amount(self):
        from django.db import transaction
        offer=self.complete()
        with transaction.atomic():_,payload=quotes._contexto(self.s)
        rid=offer['calculoRequestId'];manual=self.manual_quote(payload,rid,match_native=True)
        self.assertEqual(manual['cuotaInstitucional'],offer['cuotaContractual'])
        with patch.object(quotes,'_resultado_guardado',return_value={**manual,'calculoRequestId':rid}):
            with self.assertRaisesRegex(ValueError,'OFERTA_LINIX_NO_VIGENTE_O_NO_CONCILIA'):
                quotes.leer_oferta_linix(self.s)
            with self.assertRaises((ValueError,KeyError)):
                self.operate('aceptar',ofertaSha256=offer['sha256'],acepta=True)
        self.assertFalse(AuditLog.objects.filter(event='CREDIHOY_OFERTA_ACEPTADA_TITULAR').exists())

    def owner_transport(self,grant,identity,payload,rid):
        def http(url,**kwargs):
            import requests
            self.calls.append(kwargs)
            reply=self.client.post(owner.PATH,kwargs['data'],content_type='application/json',secure=True,
                HTTP_AUTHORIZATION=kwargs['headers']['Authorization'],HTTP_X_REQUEST_ID=kwargs['headers']['X-Request-ID'])
            r=requests.Response();r.status_code=reply.status_code;r._content=reply.content;r._content_consumed=True;r.headers.update(reply.headers)
            return r
        with patch.object(wire.requests,'post',side_effect=http),patch.object(quotes,'ApiDataCotizacionClient',return_value=self.motor),\
                patch.object(quotes.ConfiguracionApiDataV2,'desde_settings',return_value=SimpleNamespace()):
            return self.real_consult(grant,identity,payload,rid)

    def test_real_delegation_nonce_scope_tamper_and_signed_request_replay(self):
        payload={'accesoId':str(self.grant.pk),'navegador':'b'*64,'cotizacion':self.body()};rid=str(uuid4())
        packet=self.owner_transport(self.grant,{'numeroDocumento':self.doc},payload,rid)
        self.assertEqual(packet['cotizacion']['solicitudId'],str(self.s.pk))
        sent=self.calls[-1]
        replay=self.client.post(owner.PATH,sent['data'],content_type='application/json',secure=True,
            HTTP_AUTHORIZATION=sent['headers']['Authorization'],HTTP_X_REQUEST_ID=rid)
        self.assertEqual(replay.status_code,403)
        altered=self.client.post(owner.PATH,wire.encode({**payload,'cotizacion':self.body('consultar')}),
            content_type='application/json',secure=True,HTTP_AUTHORIZATION=sent['headers']['Authorization'],HTTP_X_REQUEST_ID=rid)
        self.assertEqual(altered.status_code,403)
        claims=signing.loads(sent['headers']['Authorization'][7:],key=owner.key(),salt=owner.SALT)
        self.assertEqual(claims['scope'],'solicitudes:cotizar')
        claims['scope']='solicitudes:leer'
        token=signing.dumps(claims,key=owner.key(),salt=owner.SALT)
        bad=self.client.post(owner.PATH,sent['data'],content_type='application/json',secure=True,
            HTTP_AUTHORIZATION='Bearer '+token,HTTP_X_REQUEST_ID=rid)
        self.assertEqual(bad.status_code,403)

    def test_public_to_relay_to_owner_to_offer_uses_same_session_and_revocation_is_checked(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_http import CotizacionTitularView
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional_http import SeguimientoView
        def relay(action,payload,rid):
            self.assertEqual(action,'seguimiento')
            request=Request(APIRequestFactory().post('/api/interno/credihoy/acceso/seguimiento/',payload,format='json',secure=True),parsers=[JSONParser()])
            view=SeguimientoView();view.rid=rid
            with patch('apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional_http._identity',return_value={'numeroDocumento':self.doc}),\
                    patch.object(owner,'consult',side_effect=self.owner_transport):
                return view.post(request).data
        request=Request(APIRequestFactory().post('/api/v1/credihoy/publico/cotizacion/',self.body(),format='json',secure=True,
            HTTP_X_ACCESO_REFERENCIA=str(self.grant.pk),HTTP_X_REQUEST_ID=str(uuid4())),parsers=[JSONParser()])
        from apps.xcore_consumo.services.credihoy_v2.unico import publico as access
        with patch.object(access,'acceso',return_value=(self.grant,{})),patch.object(wire,'call',side_effect=relay):
            result=CotizacionTitularView().post(request).data
        self.assertEqual(result['solicitudId'],str(self.s.pk));self.assertIsNone(result['oferta'])
        self.motor.cotizar.assert_called_once()
        self.grant.revocado=True;self.grant.save()
        with self.assertRaises(wire.NoDisponible):owner.consult(self.grant,{'numeroDocumento':self.doc},
            {'accesoId':str(self.grant.pk),'navegador':'b'*64,'cotizacion':self.body('consultar')},str(uuid4()))
