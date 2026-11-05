"""Local synthetic quote integration. Oracle, providers and sockets are never used."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sys
from datetime import timedelta
import hashlib
import json
from unittest.mock import Mock, patch
from uuid import uuid4
import requests
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from tools.cd2b.support import seed
from apps.xcore_consumo.models import EvaluacionConsumo, ConsentimientoConsumo, SolicitudConsumo
from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
from apps.xcore_consumo.services.credihoy_v2.cotizacion_cliente import (
    ApiDataCotizacionClient, validar_respuesta, VERSION)
from apps.xcore_consumo.services.credihoy_v2.cliente import ConfiguracionApiDataV2
from apps.xcore_consumo.services.credihoy_v2.pagaduria import FUENTE
from apps.xcore_consumo.services.credihoy_v2.unico import cotizacion as module
from apps.xcore_consumo.services.credihoy_v2.unico.consulta_controlada import texto_consentimiento_controlado
from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import WORKFLOW, _identidad
from apps.xcore_consumo.services.consentimiento_integridad import build_consent_hash


def response(payload, rid, *, available=False):
    p = module._empty(payload['solicitudId'], rid, 'NO_HABILITADA', payload)
    if available:
        p.update(estado='DISPONIBLE_HOMOLOGACION', disponible=True, cuotaInstitucional='204448',
            vigenteHasta=(timezone.now()+timedelta(hours=1)).isoformat(),
            ambiente='HOMOLOGACION', parametrosAplicados={'tasaFija':'3.334','tasaVariable':'13.336','P_EXTRAO':'50'})
    return p


def aportes_fixture(value='1000000'):
    from apps.xcore_consumo.services.credihoy_v2.base import Hecho, EstadoCertificacion as EC
    now=timezone.now()-timedelta(seconds=1)
    h=Hecho(valor=Decimal(value),disponible=True,fuente='SYNTHETIC_NOT_REAL_BALANCE',
        referencia_evidencia='TEST_ONLY',ruta_origen='synthetic.aportes203',fecha_corte=timezone.localdate(),
        fecha_consulta=now,estado_certificacion=EC.CERTIFICADO)
    return {'codigo':'UNICO_FILA_14','estadoEvaluacion':'NO_EVALUABLE','evidencia':{
        'operando':'limite_aportes_90','hecho':h.a_dict(),'limiteCOP':value,'unidad':'COP'}}


def decision_oferta_fixture(s, usuario):
    """Arrange an eligible SYNTHETIC study and use the native decision journal.

    This is not a recalculation or an approval of an institutional dossier.
    Quote tests isolate the handoff after the independently tested rule engine.
    """
    from dataclasses import replace
    from tools.cd8_test_elegibilidad import fixture
    from apps.xcore_consumo.services.credihoy_v2.unico import funcional
    from apps.xcore_consumo.services.credihoy_v2.unico.estamentos import clasificar_novedades
    from apps.xcore_consumo.services.credihoy_v2.unico.elegibilidad import registrar_decision_digital
    now=timezone.now()
    context,_,_,_=fixture()
    if not EvaluacionConsumo.objects.filter(solicitud=s).exists():
        detail=SolicitudConsumo.objects.get(solicitud=s);form=deepcopy(detail.form_data)
        detail.form_data={};detail.save(update_fields=['form_data'])
        with patch.object(funcional,'estado_funcional',return_value={}):
            for action,data in (('guardar_datos',form),('evaluar',{})):
                funcional.procesar_funcional(s.pk,usuario,accion=action,payload=data,
                    clave=funcional.clave_funcional(s),request_id=str(uuid4()))
    evaluation=EvaluacionConsumo.objects.get(solicitud=s)
    snapshot=deepcopy(evaluation.resultados.get(funcional.KEY,{}))
    previous={row['codigo']:row for row in snapshot.get('reglas',[])}
    previous['UNICO_FILA_14']=aportes_fixture()
    rows=[{**previous.get(row['codigo'],{}),**row,'resultado':'CUMPLE'} for row in context.reglas]
    snapshot.update(solicitudId=str(s.pk),reglas=rows)
    evaluation.resultados={**evaluation.resultados,funcional.KEY:snapshot}
    evaluation.save(update_fields=['resultados'])
    holder=hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest()
    evidence={'fuente':'SP_CONSULTAFAMILIAR','estado':'OK','mocked':False,'completa':True,
        'contratoAcreditado':True,'solicitudId':str(s.pk),'titularReferencia':holder,
        'consultaReferencia':'SYNTHETIC_NOT_LIVE','fechaConsulta':now.isoformat(),
        'filas':[{'resultado':'NO','tipofamiliar':''}]}
    classification=clasificar_novedades(evidence,solicitud_id=s.pk,titular_referencia=holder,
        ahora=now,monto=Decimal(s.monto_solicitado))
    context=replace(context,solicitud_id=str(s.pk),reglas=tuple(rows),
        evaluacion_id=hashlib.sha256(json.dumps(snapshot,sort_keys=True,default=str).encode()).hexdigest(),
        titular_referencia=holder,clasificacion_estamentos=classification)
    result=registrar_decision_digital(s.pk,operador=usuario,contexto=context,
        snapshot_clave=funcional.KEY,clave_idempotencia=str(uuid4()),ahora=now)
    if result['resultado']['estado']!='APROBADO':raise AssertionError('FIXTURE_NO_ELEGIBLE')
    return result


class ClientTests(SimpleTestCase):
    def test_scalar_partial_contract_preserves_rate_and_unavailable_components(self):
        payload = {'solicitudId':str(uuid4()), 'versionCondiciones':'a'*64,
            'condiciones':{'tasaNominalNamv':'16.67'}}
        rid = str(uuid4())
        p = response(payload, rid, available=True)
        p.update(fuente='LINIX_CALCULO_CUOTA', estado='DISPONIBLE_PARCIAL', ambiente='PRODUCCION',
            cuotaInstitucional='123.4567891', parametrosAplicados={
                'periodicidadInterna':'1','factorAdicional':'0','tasaNominalNamv':'16.67'})
        r = validar_respuesta(p, payload, rid)
        self.assertEqual(r['cuotaInstitucional'], '123.4567891')
        self.assertFalse(r['cotizacionIntegral'])
        self.assertFalse(r['habilitadoDecisionProductiva'])
        self.assertIsNone(r['seguroVida']['factorAplicado'])
        self.assertIsNone(r['primeraFechaPago'])
        for mutate in (lambda x:x.update(estado='DISPONIBLE_HOMOLOGACION'),
                lambda x:x.update(cuotaInstitucional='0'),
                lambda x:x.update(primeraFechaPago='2026-12-01'),
                lambda x:x['seguroVida'].update(factorAplicado='0.09'),
                lambda x:x['parametrosAplicados'].update(tasaNominalNamv='25.41'),
                lambda x:x['parametrosAplicados'].update(factorAdicional='50'),
                lambda x:x['parametrosAplicados'].update(periodicidadInterna='9')):
            changed = deepcopy(p); mutate(changed)
            with self.assertRaises(ValueError): validar_respuesta(changed, payload, rid)

    def test_scalar_disabled_remains_null_without_claiming_forms_parameters(self):
        payload = {'solicitudId':str(uuid4()), 'versionCondiciones':'a'*64,
            'condiciones':{'tasaNominalNamv':'16.67'}}
        rid = str(uuid4())
        p = response(payload, rid)
        p.update(fuente='LINIX_CALCULO_CUOTA', camposFaltantes=['revision_efectos_calculo_cuota'])
        r = validar_respuesta(p, payload, rid)
        self.assertIsNone(r['cuotaInstitucional'])
        self.assertEqual(r['camposFaltantes'], ['revision_efectos_calculo_cuota'])

    def test_authenticated_http_and_closed_contract(self):
        session = Mock()
        client = ApiDataCotizacionClient(ConfiguracionApiDataV2(base_url='https://api-data.invalid', token='SYNTHETIC'), session=session)
        payload = {'solicitudId':str(uuid4()), 'versionCondiciones':'a'*64,
            'condiciones':{'tasaNominalNamv':'16.67'}}
        rid = str(uuid4())
        r = requests.Response(); r.status_code = 503
        r.headers.update({'Content-Type':'application/json', 'X-Request-ID':rid})
        r._content = json.dumps(response(payload, rid)).encode(); r._content_consumed = True
        session.post.return_value = r
        self.assertFalse(client.cotizar(payload, rid)['disponible'])
        args = session.post.call_args
        self.assertEqual(args.kwargs['headers']['Authorization'], 'Bearer SYNTHETIC')
        self.assertIs(args.kwargs['allow_redirects'], False)
        self.assertIs(args.kwargs['verify'], True)
        session.post.assert_called_once()

    def test_reject_identity_false_success_and_unknown_data(self):
        payload = {'solicitudId':str(uuid4()), 'versionCondiciones':'a'*64,
            'condiciones':{'tasaNominalNamv':'16.67'}}
        rid = str(uuid4())
        for change in (lambda p:p.update(requestId=str(uuid4())),
            lambda p:p.update(habilitadoDecisionProductiva=True),
            lambda p:p.update(cuotaInstitucional='0'),
            lambda p:p.update(disponible=True)):
            p = response(payload, rid); change(p)
            with self.assertRaises(ValueError):validar_respuesta(p, payload, rid)
        p = response(payload, rid); p['Oracle'] = 'ORA-PRIVATE'
        self.assertNotIn('Oracle', validar_respuesta(p, payload, rid))


@override_settings(CREDIHOY_COTIZACION_ENABLED=True, CREDIHOY_CONSENTIMIENTO_REAL_ENABLED=True,
    REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES':{'burst':'15/min','sustained':'100/day'}},
    ROOT_URLCONF='apps.xcore_consumo.services.credihoy_v2.unico.cotizacion_urls', ALLOWED_HOSTS=['testserver'])
class QuoteFlowTests(TransactionTestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.s = seed()
        self.user = get_user_model().objects.create_user('quote-synthetic', is_staff=True)
        self.s.producto = 'CREDIHOY UNICO (NUEVO)'; self.s.metadatos={'modo':'CONSULTA_REAL_CONTROLADA'}
        self.s.asesor=self.user; self.s.monto_solicitado=Decimal(400000); self.s.plazo_solicitado=2; self.s.save()
        detail=self.s.consumo_detail
        detail.form_data={'monto':'400000.00','plazoMeses':2,'periodicidadPago':'MENSUAL','recogerCredihoy':False}
        detail.save()
        c=self.s.consentimiento_consumo; o=c.otp; o.provider='twilio_verify'; o.save()
        c.version='CREDIHOY-REAL-1'; c.text_snapshot=texto_consentimiento_controlado()
        c.text_hash=build_consent_hash(c.version,c.text_snapshot,o.transaction_uuid,o.canal,o.verificado_at)
        c.evidencia={'workflow':WORKFLOW,'identidad':_identidad(self.s),
            'documentoSha256':hashlib.sha256(c.text_snapshot.encode()).hexdigest()}; c.save()
        self.firma=ConsentimientoConsumo.objects.values().get(pk=c.pk)
        rid=str(uuid4())
        evidence={'codigo':'100','tipo':'N','activa':True,'estado':'DISPONIBLE','fuente':FUENTE,
            'fechaConsulta':'2026-10-19T10:00:00-05:00','requestId':rid}
        self.commercial={'tarifaId':1,'versionTarifa':1,'asignacionId':1,'versionAsignacion':1,
            'soporteTarifaSha256':'a'*64,'soporteAsignacionSha256':'b'*64,'tasaComercialNamv':'16.67'}
        SeleccionTarifaCredihoy.objects.create(solicitud=self.s, request_id=rid, responsable=self.user,
            evidencia=evidence, evidencia_sha256=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest(),
            resultado=self.commercial)
        patcher=patch.object(module,'resolver_condicion', return_value=self.commercial)
        patcher.start(); self.addCleanup(patcher.stop)
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import leer_estado_funcional
        def persisted_or_synthetic(s):
            if EvaluacionConsumo.objects.filter(solicitud=s).exists():return leer_estado_funcional(s)
            return {'resultado':{'reglas':[aportes_fixture()]}}
        read=patch('apps.xcore_consumo.services.credihoy_v2.unico.funcional.leer_estado_funcional',
            side_effect=persisted_or_synthetic)
        read.start();self.addCleanup(read.stop)
        self.client=APIClient(); self.client.force_authenticate(self.user)
        self.url=f'/api/v1/credihoy/unico/solicitudes/{self.s.pk}/cotizacion/'

    def test_get_no_http_and_post_no_oracle_overrides(self):
        with patch.object(module,'ApiDataCotizacionClient') as client:
            r=self.client.get(self.url)
            self.assertEqual(r.status_code,200); self.assertEqual(r.json()['estado'],'SIN_COTIZACION')
            self.assertEqual(self.client.post(self.url,{'monto':'1'},format='json').status_code,400)
            client.assert_not_called()

    @override_settings(REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES':{'burst':'2/min','sustained':'100/day'}})
    def test_institutional_scopes_work_and_authenticated_limit_is_enforced(self):
        with patch.object(module,'ApiDataCotizacionClient') as client:
            self.assertEqual(self.client.get(self.url).status_code,200)
            self.assertEqual(self.client.get(self.url).status_code,200)
            response=self.client.get(self.url)
            self.assertEqual(response.status_code,429)
            self.assertEqual(response.json()['codigo'],'HTTP_INVALIDO')
            client.assert_not_called()

    def test_quote_persisted_outside_transaction_and_reused(self):
        client=Mock()
        def call(payload,rid):
            self.assertFalse(connection.in_atomic_block)
            saved=SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data[module.KEY]
            self.assertEqual(saved['requestId'],rid)
            return response(payload,rid,available=True)
        client.cotizar.side_effect=call
        r=module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        self.assertTrue(r['disponible']); self.assertFalse(r['cotizacionIntegral'])
        again=module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        self.assertEqual(again['cuotaInstitucional'],'204448'); client.cotizar.assert_called_once()
        self.assertEqual(ConsentimientoConsumo.objects.values().get(pk=self.s.consentimiento_consumo.pk),self.firma)
        self.assertEqual(EvaluacionConsumo.objects.count(),0)

    def test_timeout_no_second_call_or_estimated_value(self):
        client=Mock(); client.cotizar.side_effect=requests.Timeout('PRIVATE ORA-123')
        first=module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        second=module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        self.assertEqual(first['estado'],'REQUIERE_CONCILIACION')
        self.assertIsNone(second['cuotaInstitucional']); client.cotizar.assert_called_once()
        self.assertNotIn('PRIVATE',json.dumps(second))

    def test_scalar_partial_persisted_separately_from_credit_evaluation(self):
        client = Mock()
        def calculate(payload, rid):
            p = response(payload, rid, available=True)
            p.update(fuente='LINIX_CALCULO_CUOTA', estado='DISPONIBLE_PARCIAL',
                ambiente='PRODUCCION', cuotaInstitucional='123.4567891',
                parametrosAplicados={'periodicidadInterna':'1', 'factorAdicional':'0', 'tasaNominalNamv':'16.67'})
            return validar_respuesta(p, payload, rid)
        client.cotizar.side_effect = calculate
        first = module.cotizacion(self.s.pk, self.user, str(uuid4()), ejecutar=True, cliente=client)
        recovered = module.cotizacion(self.s.pk, self.user, str(uuid4()), cliente=client)
        self.assertEqual(recovered['fuente'], 'LINIX_CALCULO_CUOTA')
        self.assertEqual(recovered['cuotaInstitucional'], first['cuotaInstitucional'])
        self.assertFalse(recovered['cotizacionIntegral'])
        self.assertFalse(recovered['habilitadoDecisionProductiva'])
        self.assertIsNone(recovered['primeraFechaPago'])
        client.cotizar.assert_called_once()
        self.assertEqual(EvaluacionConsumo.objects.count(), 0)
        self.assertEqual(ConsentimientoConsumo.objects.values().get(pk=self.s.consentimiento_consumo.pk), self.firma)

    def test_change_amount_or_rate_invalidates_previous_result(self):
        client=Mock(); client.cotizar.side_effect=lambda p,r: response(p,r,available=True)
        module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        self.s.monto_solicitado=Decimal(500000); self.s.save()
        d=SolicitudConsumo.objects.get(solicitud=self.s); d.form_data['monto']='500000.00'; d.save()
        r=module.cotizacion(self.s.pk,self.user,str(uuid4()))
        self.assertEqual(r['estado'],'DESACTUALIZADA'); self.assertIsNone(r['cuotaInstitucional'])
        self.commercial['tasaComercialNamv']='17.00'
        self.assertEqual(module.cotizacion(self.s.pk,self.user,str(uuid4()))['estado'],'PARAMETROS_EXPEDIENTE_INCOMPLETOS')

    def test_ownership_and_feature_gate(self):
        other=get_user_model().objects.create_user('other-operator',is_staff=True)
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(self.url).status_code,404)
        with override_settings(CREDIHOY_COTIZACION_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code,404)
        self.assertIn(APIClient().get(self.url).status_code,(401,403))

    def test_explicit_unavailable_is_not_published_as_zero(self):
        client=Mock(); client.cotizar.side_effect=lambda p,r: response(p,r)
        r=module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        self.assertFalse(r['disponible']); self.assertIsNone(r['cuotaInstitucional'])
        self.assertIsNone(module.cotizacion(self.s.pk,self.user,str(uuid4()))['primeraFechaPago'])
        self.assertEqual(EvaluacionConsumo.objects.count(),0)

    def test_missing_configuration_leaves_no_inflight_claim(self):
        r=module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True)
        self.assertEqual(r['estado'],'NO_HABILITADA')
        self.assertNotIn(module.KEY,SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data)

    def test_same_request_id_reuses_definitive_unavailable(self):
        client=Mock(); client.cotizar.side_effect=lambda p,r: response(p,r)
        rid=str(uuid4())
        module.cotizacion(self.s.pk,self.user,rid,ejecutar=True,cliente=client)
        module.cotizacion(self.s.pk,self.user,rid,ejecutar=True,cliente=client)
        client.cotizar.assert_called_once()

    def test_http_contract_with_actual_api_data_boundary_disabled(self):
        api_root=Path(__file__).resolve().parents[3]/'api_data_v1'
        if not api_root.is_dir():self.skipTest('Sibling API_DATA repository unavailable')
        sys.path.insert(0,str(api_root)); self.addCleanup(lambda:sys.path.remove(str(api_root)))
        from incluirtec.services.credihoy_forms_quote_http import quote_http
        session=Mock()
        def wire(url, *, json, headers, **kwargs):
            p,status=quote_http(json,headers['X-Request-ID'])
            r=requests.Response(); r.status_code=status
            r.headers.update({'Content-Type':'application/json','X-Request-ID':headers['X-Request-ID']})
            r._content=__import__('json').dumps(p).encode(); r._content_consumed=True
            return r
        session.post.side_effect=wire
        client=ApiDataCotizacionClient(ConfiguracionApiDataV2(base_url='https://api-data.invalid',token='SYNTHETIC'),session=session)
        with override_settings(CREDIHOY_FORMS_QUOTE_HTTP_ENABLED=False):
            r=module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        self.assertEqual(r['estado'],'NO_HABILITADA'); self.assertIsNone(r['cuotaInstitucional'])
        session.post.assert_called_once()

    def test_expiration_invalidates_without_http(self):
        client=Mock(); client.cotizar.side_effect=lambda p,r: response(p,r,available=True)
        module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        d=SolicitudConsumo.objects.get(solicitud=self.s)
        d.orchestration_data[module.KEY]['resultado']['vigenteHasta']='2020-01-01T00:00:00+00:00'; d.save()
        r=module.cotizacion(self.s.pk,self.user,str(uuid4()),cliente=client)
        self.assertEqual(r['estado'],'DESACTUALIZADA'); self.assertIsNone(r['cuotaInstitucional'])
        client.cotizar.assert_called_once()

    def test_pending_call_blocks_even_after_condition_change(self):
        client=Mock(); client.cotizar.side_effect=requests.Timeout
        module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        self.s.monto_solicitado=Decimal(500000); self.s.save()
        d=SolicitudConsumo.objects.get(solicitud=self.s); d.form_data['monto']='500000.00'; d.save()
        r=module.cotizacion(self.s.pk,self.user,str(uuid4()),ejecutar=True,cliente=client)
        self.assertEqual(r['estado'],'REQUIERE_CONCILIACION'); client.cotizar.assert_called_once()

    def test_missing_selection_never_calls_http(self):
        self.s.consumo_detail.form_data={}; self.s.consumo_detail.save()
        with patch.object(module,'ApiDataCotizacionClient') as client:
            r=self.client.post(self.url,{},format='json')
            self.assertEqual(r.json()['estado'],'PARAMETROS_EXPEDIENTE_INCOMPLETOS')
            self.assertIsNone(r.json()['cuotaInstitucional']); client.assert_not_called()

    def test_explicit_recovery_preserves_original_and_quotes_current_catalog(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import condicion_comercial as cc
        from apps.utils.models import AuditLog
        original=SeleccionTarifaCredihoy.objects.get(solicitud=self.s)
        before=SeleccionTarifaCredihoy.objects.values().get(pk=original.pk)
        key=str(uuid4())
        with patch.object(cc,'resolver_condicion',return_value=self.commercial):
            result=cc.revalidar_seleccion_conservada(self.s.pk,self.user,request_id=key,
                seleccion_anterior_request_id=str(original.request_id))
            again=cc.revalidar_seleccion_conservada(self.s.pk,self.user,request_id=key,
                seleccion_anterior_request_id=str(original.request_id))
        self.assertEqual(result.pk,again.pk)
        self.assertEqual(SeleccionTarifaCredihoy.objects.values().get(pk=original.pk),before)
        self.assertEqual(cc.pagaduria_seleccion(result).requestId,str(original.request_id))
        self.assertEqual(SeleccionTarifaCredihoy.objects.count(),2)
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_CONDICIONES_REVALIDADAS').count(),1)
        with patch.object(module,'ApiDataCotizacionClient') as client:
            r=self.client.get(self.url)
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['estado'],'SIN_COTIZACION')
            self.assertEqual(r.json()['condicionesSolicitadas']['tasaNominalNamv'],'16.67')
            client.assert_not_called()
        self.assertEqual(ConsentimientoConsumo.objects.values().get(pk=self.s.consentimiento_consumo.pk),self.firma)

    def test_recovery_refuses_different_rate_expired_catalog_and_wrong_owner(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import condicion_comercial as cc
        from django.core.exceptions import ValidationError
        original=SeleccionTarifaCredihoy.objects.get(solicitud=self.s)
        for current in ({**self.commercial,'tasaComercialNamv':'17.00'},{}):
            with patch.object(cc,'resolver_condicion',return_value=current),self.assertRaises(ValidationError):
                cc.revalidar_seleccion_conservada(self.s.pk,self.user,request_id=str(uuid4()),
                    seleccion_anterior_request_id=str(original.request_id))
        self.assertEqual(SeleccionTarifaCredihoy.objects.count(),1)

    def test_recovery_of_undetermined_selection_does_not_infer_acceptance(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import condicion_comercial as cc
        initial=SeleccionTarifaCredihoy.objects.get(solicitud=self.s)
        rid=uuid4();evidence={**initial.evidencia,'requestId':str(rid)}
        old=SeleccionTarifaCredihoy.objects.create(solicitud=self.s,responsable=self.user,request_id=rid,
            evidencia=evidence,evidencia_sha256=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest(),
            resultado={'estado':'TARIFA_NO_DETERMINADA','tasaComercialNamv':None})
        with patch.object(cc,'resolver_condicion',return_value=self.commercial):
            selected=cc.revalidar_seleccion_conservada(self.s.pk,self.user,request_id=str(uuid4()),
                seleccion_anterior_request_id=str(old.request_id))
        self.assertIs(selected.resultado['revalidacion']['aceptacionAsociadoInferida'],False)
        self.assertEqual(selected.evidencia,old.evidencia)
        selected.evidencia_sha256='a'*64
        with self.assertRaisesRegex(ValueError,'EVIDENCIA_COMERCIAL_INVALIDA'):cc.pagaduria_seleccion(selected)

    def test_manual_execution_append_only_residual_and_get_without_automatic_call(self):
        from django.db import transaction
        from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
        from apps.xcore_consumo.services.credihoy_v2.unico import funcional as f
        api_root=Path(__file__).resolve().parents[3]/'api_data_v1'
        sys.path.insert(0,str(api_root));self.addCleanup(lambda:sys.path.remove(str(api_root)))
        from incluirtec.services.credihoy_scalar_quote import manual_execution_quote
        # Synthetic contract regression, never a replacement for the private real receipt.
        evidence=json.loads((api_root/'tools/fixtures/credihoy_cuota_manual_sintetica.json').read_text())
        evidence['solicitudId']=str(self.s.pk)
        with transaction.atomic():_,payload=module._contexto(self.s)
        rid=str(uuid4());quoted=manual_execution_quote(evidence,payload,rid)
        op=OperacionGateway.objects.create(solicitud=self.s,consentimiento=self.s.consentimiento_consumo,
            proveedor='HDC',estado='COMPLETADA',consulta_id=uuid4(),idempotency_key=str(uuid4()))
        from apps.integraciones.risk_gateway.operaciones.servicio import _recibo
        op.request_hmac='a'*64;op.recibo_hmac=_recibo(op)
        op.save(update_fields=['request_hmac','recibo_hmac'])
        old_key=f.KEY_HDC_REVISION+':'+str(uuid4())
        preliminary={'estado':'PRELIMINAR_NO_DECISORIA','versionPolitica':'SYNTHETIC_EXISTING_METHOD',
            'ingresoComputable':{'disponible':True,'valor':'2435509'},
            'subtotalMensualHdcReconocido':{'disponible':True,'valor':'1429000'},
            'gastosFamiliares':{'disponible':True,'valor':'608877'},
            'cuotaParaCapacidad':{'disponible':True,'valor':'200000'},
            'capacidad':{'disponible':True,'valor':'197632'},'procedencia':{},
            'reservas':[{'motivo':'PENDIENTE_'+str(n)} for n in range(4)],
            'endeudamientoCompleto':False,'autorizadoDecisionCrediticia':False}
        before={'requestId':str(uuid4()),'solicitudId':str(self.s.pk),'decisionCrediticia':'NO_AUTORIZADA',
            'estadoObtencionDatos':f._datos_obtencion(hdc=True),'capacidadPreliminar':preliminary,
            'cuotaParaCapacidad':preliminary['cuotaParaCapacidad'],
            'cuotaContractual':{'disponible':False,'valor':None},
            'reglas':[{'codigo':'UNICO_FILA_2','estadoEvaluacion':'NO_EVALUABLE'},
                {'codigo':'UNICO_FILA_9','estadoEvaluacion':'NO_CUMPLE'},
                {'codigo':'UNICO_FILA_32','estadoEvaluacion':'PENDIENTE_POLITICA'}]}
        e=EvaluacionConsumo.objects.create(solicitud=self.s,decision_final='NO_AUTORIZADA',
            resultados={old_key:before},input_snapshot={'revisionHdcActiva':old_key,old_key:{
                'operacionId':str(op.pk),'consultaId':str(op.consulta_id),'firma':self.firma['text_hash'],
                'resultadoSha256':f._digest(before),'revisionEvidencia':str(uuid4())}},integraciones_snapshot={'original':True})
        original_e=deepcopy(e.resultados)
        with self.assertRaisesRegex(ValueError,'EVALUACION_BASE_CAMBIO'):
            module.incorporar_prueba_manual(self.s.pk,self.user,quoted,revision_id=rid,resultado_anterior_sha256='0'*64)
        entry=module.incorporar_prueba_manual(self.s.pk,self.user,quoted,revision_id=rid,resultado_anterior_sha256=f._digest(before))
        again=module.incorporar_prueba_manual(self.s.pk,self.user,quoted,revision_id=rid,resultado_anterior_sha256=f._digest(before))
        self.assertEqual(entry,again)
        e.refresh_from_db();self.assertEqual(len(e.resultados),2)
        self.assertEqual(e.resultados[old_key],original_e[old_key])
        self.assertEqual(e.integraciones_snapshot,{'original':True})
        self.s.refresh_from_db()
        after=f.leer_estado_funcional(self.s)['resultado']
        self.assertEqual(after['capacidadPreliminar']['capacidad']['valor'],'193184')
        self.assertEqual(after['capacidadPreliminar']['cuotaParaCapacidad']['valor'],'204448')
        self.assertEqual(after['capacidadPreliminar']['reservas'],preliminary['reservas'])
        self.assertFalse(after['cuotaContractual']['disponible'])
        self.assertEqual([r['estadoEvaluacion'] for r in after['reglas']],['NO_EVALUABLE','NO_CUMPLE','PENDIENTE_POLITICA'])
        with patch.object(module,'ApiDataCotizacionClient') as automatic:
            result=self.client.get(self.url)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['cuotaInstitucional'],'204448')
            self.assertEqual(result.json()['fuente'],'LINIX_EJECUCION_MANUAL')
            automatic.assert_not_called()
        self.assertEqual(ConsentimientoConsumo.objects.values().get(pk=self.s.consentimiento_consumo.pk),self.firma)
        self.assertEqual(OperacionGateway.objects.count(),1)
