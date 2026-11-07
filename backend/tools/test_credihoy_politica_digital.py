"""Approved digital policy, local intercepted E2E only; no financial calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal,localcontext
from io import BytesIO
from unittest import TestCase
from unittest.mock import Mock

from pypdf import PdfReader
from apps.xcore_consumo.services.credihoy_v2.unico.condiciones_producto import plazos_por_monto,exigir_plazo_por_monto,politica_digital
from apps.xcore_consumo.services.credihoy_v2.unico.estamentos import (
    clasificar_novedades,resultado_cursor_familiar,ORDINARIO,FAMILIAR,COLABORADOR,BLOQUEADO)
from apps.xcore_consumo.services.credihoy_v2.unico.elegibilidad import (
    evaluar_decision_digital,contrato_evaluacion_decision,PuertaRadicacionOrdinaria,RadicacionOrdinariaBloqueada)
from apps.xcore_consumo.services.credihoy_v2.unico.desembolso import precondiciones_desembolso
from apps.documentos.formularios_credihoy import _diligenciar,inventario_formato,original
from tools import cd8_test_elegibilidad as fixture
from tools.test_credihoy_preparacion_producto import fact

NOW=datetime(2026,10,26,20,tzinfo=timezone.utc)
SID='00000000-0000-4000-8000-000000000001'
TITULAR='a'*64


def evidence(rows=None):
    return {'fuente':'SP_CONSULTAFAMILIAR','estado':'OK','mocked':False,'completa':True,'contratoAcreditado':True,
        'solicitudId':SID,'titularReferencia':TITULAR,'consultaReferencia':'SYNTHETIC_METADATA_NOT_LIVE',
        'fechaConsulta':NOW.isoformat(),'filas':rows if rows is not None else [{'resultado':'NO','tipofamiliar':''}]}


def classify(rows=None,**kw):
    return clasificar_novedades(evidence(rows),solicitud_id=SID,titular_referencia=TITULAR,ahora=NOW,
        monto=Decimal('2000000'),**kw)


class PlazoTests(TestCase):
    def test_namv_ea_conversion_is_decimal_and_not_a_contractual_rounding(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.condiciones_producto import convertir_namv_a_ea
        with localcontext() as ctx:
            ctx.prec=6
            converted=convertir_namv_a_ea(Decimal('16.67'))
        self.assertEqual(converted,convertir_namv_a_ea(Decimal('16.67')))
        self.assertGreater(Decimal(converted['tasaEaPorcentaje']),Decimal('16.67'))
        self.assertFalse(converted['esOfertaContractual']);self.assertFalse(converted['redondeoContractualAplicado'])
        for value in (16.67,Decimal('NaN'),Decimal('-1')):
            with self.assertRaises(ValueError):convertir_namv_a_ea(value)

    def test_all_nonambiguous_ranges_and_exact_boundaries(self):
        wage=Decimal('1750905')
        for ratio,expected in [('0.2',24),('1.499999999',24),('1.500000001',36),('3',36),('3.1',48),('40',48)]:
            with self.subTest(ratio=ratio):
                result=plazos_por_monto(wage*Decimal(ratio),fecha=NOW)
                self.assertEqual(result['plazoMaximo'],expected)
                self.assertEqual(result['smmlv']['referencia_evidencia'][:10],'SMMLV_CO_2')
        for ratio in ('0.199','3.0000001','3.05','3.0999999','40.01'):
            result=plazos_por_monto(wage*Decimal(ratio),fecha=NOW)
            self.assertIsNone(result['plazoMaximo'])

    def test_overlap_never_expands_and_normative_precision_is_configurable(self):
        amount=Decimal('1750905')*Decimal('1.5')
        r=plazos_por_monto(amount,fecha=NOW)
        self.assertEqual(r['estado'],'SUPERPOSICION_MENOR_PLAZO_COMUN');self.assertEqual(r['plazoMaximo'],24)
        with self.assertRaises(ValueError):exigir_plazo_por_monto(amount,25,fecha=NOW)
        p=politica_digital();p['rangosSMMLV'][0]['incluyeHasta']=False
        self.assertEqual(plazos_por_monto(amount,fecha=NOW,configuracion=p)['plazoMaximo'],36)

    def test_backend_rejects_unauthorized_term_for_amount(self):
        for amount,term in [('400000',25),('3501810',37),('5340260.25',1),('80000000',48)]:
            with self.subTest(amount=amount),self.assertRaises(ValueError):
                exigir_plazo_por_monto(Decimal(amount),term,fecha=NOW)


class EstamentosTests(TestCase):
    def test_real_metadata_selects_only_a_human_boundary_not_an_authorization(self):
        import json
        from pathlib import Path
        from apps.xcore_consumo.services.credihoy_v2.unico.estamentos import preparar_ruta_estamentos
        root=Path(__file__).resolve().parents[1]/'apps/xcore_consumo/services/credihoy_v2/unico'
        reading=json.loads((root/'workflow_estamentos.json').read_text(encoding='utf-8'))
        for description,target in [('GRADO CIVIL TRABAJADOR',10002991),('TRABAJADOR CONGENTE',10002856)]:
            state=classify([{'resultado':'SI','tipofamiliar':description}])
            plan=preparar_ruta_estamentos(state,reading)
            self.assertTrue(plan['paradaConfigurada']);self.assertEqual(plan['actividadParada'],121)
            self.assertEqual(plan['rutaSeleccionada'],target)
            self.assertFalse(plan['autorizaStart']);self.assertFalse(plan['paradaEnInstanciaComprobada'])
            changed=deepcopy(reading)
            next(r for r in changed['catalogo'] if r['K_IDACTI']==121)['I_EJECUC']='A'
            self.assertFalse(preparar_ruta_estamentos(state,changed)['paradaConfigurada'])

    def test_all_rows_and_accents_family_precedes_employee_independent_of_order(self):
        rows=[{'resultado':'SI','tipofamiliar':'  Primer GRADO de consanguinidad - TRABAJADOR '},
            {'resultado':'SI','tipofamiliar':'COLABORADOR DE CONGENTE'}]
        for data in (rows,rows[::-1]):
            result=classify(data)
            self.assertEqual(result['estado'],FAMILIAR)
            self.assertFalse(result['autorizaAutomatizacion']);self.assertEqual(result['actividadSeleccionEstamento'],121)
            self.assertEqual(result['descripcionOriginal'],[r['tipofamiliar'] for r in data])
            self.assertFalse(result['paradaInstitucionalGarantizada'])
        self.assertEqual(classify([{'resultado':'SI','tipofamiliar':'C\u00d3NYUGE de COLABORADOR'}])['estado'],FAMILIAR)

    def test_employee_never_becomes_ordinary_even_missing_or_outside_council_range(self):
        rows=[{'resultado':'SI','tipofamiliar':'TRABAJADOR DE CONGENTE'}]
        result=classify(rows)
        self.assertEqual(result['estado'],COLABORADOR);self.assertFalse(result['dentroDeRangoConsejo'])
        self.assertFalse(result['autorizaAutomatizacion'])
        wage=fact(Decimal('1750905'),cutoff=NOW.date());patrimony=fact(Decimal('50000000'),cutoff=NOW.date())
        result=classify(rows,smmlv=wage,patrimonio=patrimony)
        self.assertTrue(result['dentroDeRangoConsejo']);self.assertFalse(result['autorizaAutomatizacion'])
        result=classify(rows,smmlv=wage,patrimonio=replace(patrimony,valor=Decimal('1000000')))
        self.assertFalse(result['dentroDeRangoConsejo']);self.assertEqual(result['estado'],COLABORADOR)

    def test_silence_unknown_contract_timeout_conflicts_and_other_holder_block(self):
        for changes in ({'filas':[]},{'completa':False},{'estado':'TIMEOUT'},{'mocked':True},
                {'contratoAcreditado':False},{'solicitudId':'OTHER'},{'titularReferencia':'b'*64},
                {'filas':[{'resultado':'SI','tipofamiliar':''}]},
                {'filas':[{'resultado':'NO','tipofamiliar':''},{'resultado':'SI','tipofamiliar':'TRABAJADOR'}]}):
            result=clasificar_novedades({**evidence(),**changes},solicitud_id=SID,titular_referencia=TITULAR,
                ahora=NOW,monto=Decimal('2000000'))
            self.assertEqual(result['estado'],BLOQUEADO)
        self.assertEqual(classify()['estado'],ORDINARIO)

    def test_existing_cursor_adapter_retains_every_record_and_needs_verified_columns(self):
        columns=[('SYNTHETIC_FLAG',),('SYNTHETIC_DESCRIPTION',)]
        rows=[('SI','TRABAJADOR'),('SI','GRADO CIVIL COLABORADOR')]
        result=resultado_cursor_familiar(columns,rows)
        self.assertFalse(result['contratoAcreditado']);self.assertEqual(len(result['filas']),2)
        self.assertFalse(resultado_cursor_familiar(columns,rows,
            columnas_acreditadas=('SYNTHETIC_FLAG','SYNTHETIC_DESCRIPTION'))['contratoAcreditado'])
        real=[('RESULTADO','DB_TYPE_VARCHAR'),('TIPOFAMILIAR','DB_TYPE_VARCHAR')]
        self.assertTrue(resultado_cursor_familiar(real,rows)['contratoAcreditado'])
        negative=resultado_cursor_familiar([("'NO'",'DB_TYPE_CHAR'),("'NINGUNA'",'DB_TYPE_CHAR')],[('NO','NINGUNA')])
        self.assertTrue(negative['contratoAcreditado'])
        self.assertEqual(classify(negative['filas'])['estado'],ORDINARIO)
        for bad in ([],[('NO',)],[('NO',None)]):
            with self.assertRaises(ValueError):resultado_cursor_familiar(columns,bad)


class DigitalDecisionTests(TestCase):
    def setUp(self):
        self.c,self.p,self.g,self.a=fixture.fixture()
        self.c=replace(self.c,clasificacion_estamentos=classify(),titular_referencia=TITULAR)

    def test_ordinary_approved_without_human_grant_but_not_registered_in_linix(self):
        c=replace(self.c,decision_autorizada=False,decision_referencia=None)
        result=evaluar_decision_digital(c,ahora=NOW)
        self.assertEqual(result['estado'],'APROBADO');self.assertFalse(result['decisionLinixRegistrada'])
        self.assertFalse(result['requiereAprobadorManualOrdinario']);self.assertFalse(result['autorizaDesembolso'])
        self.a.resolve.assert_not_called()
        stages=contrato_evaluacion_decision(c,self.p,ahora=NOW)['etapasProceso']
        self.assertNotIn('DECISION_HUMANA_LINIX',stages['ordenEtapas'])
        self.assertFalse(stages['politicaDecisionVigente']['aprobadorHumanoOrdinarioRequerido'])

    def test_ordinary_preflight_uses_approved_policy_without_human_grant_and_keeps_transport_closed(self):
        c=replace(self.c,decision_autorizada=False,decision_referencia=None,
            oferta_desde=NOW-timedelta(minutes=1),oferta_hasta=NOW+timedelta(minutes=5),aceptacion_fecha=NOW)
        transport=Mock();gate=PuertaRadicacionOrdinaria(autoridad=self.a,politica=self.p,
            cargar_contexto=lambda _:c,transporte=transport)
        result=gate.preflight(c.solicitud_id,ahora=NOW)
        self.assertTrue(result['elegible']);self.assertFalse(result['requiereGrantAprobadorHumano'])
        with self.assertRaises(RadicacionOrdinariaBloqueada) as error:gate.start(c.solicitud_id,ahora=NOW)
        self.assertEqual(error.exception.resultado['codigo'],'TRANSPORTE_ORDINARIO_NO_HABILITADO')
        transport.start.assert_not_called();self.a.resolve.assert_not_called()
        c=replace(c,consentimiento_vigente=False)
        with self.assertRaises(RadicacionOrdinariaBloqueada):gate.preflight(c.solicitud_id,ahora=NOW)

    def test_proven_exclusion_rejects_unknown_or_outdated_data_allow_subsanation(self):
        for code,state,expected in [('UNICO_FILA_21','NO_CUMPLE','RECHAZADO'),
                ('UNICO_FILA_9','NO_CUMPLE','SUBSANACION_DIGITAL'),('UNICO_FILA_21','NO_EVALUABLE','SUBSANACION_DIGITAL')]:
            rows=deepcopy(self.c.reglas);next(r for r in rows if r['codigo']==code)['estadoEvaluacion']=state
            self.assertEqual(evaluar_decision_digital(replace(self.c,reglas=rows),ahora=NOW)['estado'],expected)
        self.assertEqual(evaluar_decision_digital(replace(self.c,capacidad_cumple_criterio=False),ahora=NOW)['estado'],'RECHAZADO')

    def test_embargo_exception_preserves_original_fact_and_legal_payment_controls(self):
        rows=deepcopy(self.c.reglas);row=next(r for r in rows if r['codigo']=='UNICO_FILA_6')
        row.update(estadoEvaluacion='NO_CUMPLE',evidencia={'fuente':'HDC_PERSISTIDA','embargada':True,'referencia':'TEST'})
        before=deepcopy(rows)
        result=evaluar_decision_digital(replace(self.c,reglas=rows),ahora=NOW)
        self.assertEqual(result['estado'],'APROBADO');self.assertEqual(rows,before)
        self.assertEqual(result['alertas'][0]['hechoOriginal'],row)
        projected=next(r for r in result['reglasFuncionales'] if r['codigo']=='UNICO_FILA_6')
        self.assertEqual(projected['estadoEvaluacion'],'NO_CUMPLE')
        self.assertEqual(projected['efecto'],'INFORMATIVO')
        self.assertTrue(result['alertas'][0]['noOmiteControlLegalCuentaDesembolso'])
        row.pop('evidencia')
        self.assertFalse(evaluar_decision_digital(replace(self.c,reglas=rows),ahora=NOW)['decisionLinixRegistrada'])

    def test_accredited_no_aplica_neutral_but_unexplained_exclusion_is_not(self):
        rows=deepcopy(self.c.reglas);row=next(r for r in rows if r['codigo']=='UNICO_FILA_31')
        row.update(estadoEvaluacion='NO_APLICA',justificacionExclusion='SOLO_RENOVACION',referenciaExclusion='GER-PO.068')
        self.assertEqual(evaluar_decision_digital(replace(self.c,reglas=rows),ahora=NOW)['estado'],'APROBADO')
        row.pop('referenciaExclusion')
        self.assertNotEqual(evaluar_decision_digital(replace(self.c,reglas=rows),ahora=NOW)['estado'],'APROBADO')

    def test_special_cases_cannot_reach_transport_despite_retry_or_concurrency(self):
        for description,state in [('GRADO CIVIL TRABAJADOR',FAMILIAR),('TRABAJADOR CONGENTE',COLABORADOR)]:
            c=replace(self.c,clasificacion_estamentos=classify([{'resultado':'SI','tipofamiliar':description}]))
            transport=Mock();gate=PuertaRadicacionOrdinaria(autoridad=self.a,politica=self.p,
                cargar_contexto=lambda _:c,transporte=transport)
            def attempt(_):
                with self.assertRaises(RadicacionOrdinariaBloqueada):gate.start(c.solicitud_id,ahora=NOW)
            with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(attempt,range(8)))
            self.assertEqual(evaluar_decision_digital(c,ahora=NOW)['estado'],state)
            transport.start.assert_not_called()

    def test_special_intake_preflight_does_not_require_final_offer_or_human_approval(self):
        c=replace(self.c,clasificacion_estamentos=classify([{'resultado':'SI','tipofamiliar':'TRABAJADOR CONGENTE'}]),
            decision_autorizada=False,decision_referencia=None,oferta_referencia=None,oferta_huella=None,
            aceptacion_referencia=None,aceptacion_oferta_huella=None)
        transport=Mock();gate=PuertaRadicacionOrdinaria(autoridad=self.a,politica=self.p,
            cargar_contexto=lambda _:c,transporte=transport)
        result=gate.preflight(c.solicitud_id,ahora=NOW,modo='ESPECIAL')
        self.assertEqual(result['estado'],'APROBADO_PARA_RADICACION')
        self.assertTrue(result['requiereSeleccionHumana']);self.assertFalse(result['esAprobacion'])
        self.assertEqual({v['codigo'] for v in result['requisitosFormalizacionPosterior']},
            {'OFERTA_CONTRACTUAL_AUSENTE_O_NO_VIGENTE','ACEPTACION_NO_VINCULADA_A_OFERTA_VIGENTE'})
        with self.assertRaises(RadicacionOrdinariaBloqueada):gate.preflight(c.solicitud_id,ahora=NOW)
        for changed in (replace(c,consentimiento_vigente=False),replace(c,documentos_vigentes=False),
                replace(c,xml_completo=False),replace(c,tasa_concordante=False),replace(c,cuota_contractual=None),
                replace(c,start_previos=1),replace(c,intencion_existente='ALREADY_RESERVED')):
            gate.cargar_contexto=lambda _,value=changed:value
            with self.assertRaises(RadicacionOrdinariaBloqueada):
                gate.preflight(c.solicitud_id,ahora=NOW,modo='ESPECIAL')
        transport.start.assert_not_called()

    def test_historical_study_fitness_is_not_a_current_start_or_signature_permission(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.elegibilidad import continuidad_desde_decision
        from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional import project_study_decision
        c=replace(self.c,clasificacion_estamentos=classify([{'resultado':'SI','tipofamiliar':'TRABAJADOR CONGENTE'}]))
        decision=evaluar_decision_digital(c,ahora=NOW);before=deepcopy(decision)
        continuity=continuidad_desde_decision(decision)
        self.assertTrue(continuity['estudioFavorable'])
        self.assertEqual(continuity['decisionInstitucional'],'PENDIENTE')
        self.assertEqual(continuity['clasificacion'],COLABORADOR)
        self.assertFalse(continuity['autorizaStart']);self.assertFalse(continuity['autorizaFirma'])
        projected=project_study_decision(decision)
        self.assertEqual(projected['estado'],'EN_REVISION')
        self.assertTrue(projected['continuidad']['estudioFavorable'])
        self.assertFalse(projected['continuidad']['radicacionHabilitada'])
        self.assertEqual(decision,before)
        rows=deepcopy(c.reglas);next(r for r in rows if r['codigo']=='UNICO_FILA_21')['estadoEvaluacion']='NO_CUMPLE'
        failed=evaluar_decision_digital(replace(c,reglas=rows),ahora=NOW)
        self.assertFalse(continuidad_desde_decision(failed)['estudioFavorable'])

    def test_special_quote_preparation_is_read_only_and_cannot_create_contractual_offer(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from apps.xcore_consumo.services.credihoy_v2.unico import cotizacion,elegibilidad
        case=SimpleNamespace(pk=SID)
        c=replace(self.c,clasificacion_estamentos=classify([{'resultado':'SI','tipofamiliar':'TRABAJADOR CONGENTE'}]))
        decision={'resultado':evaluar_decision_digital(c,ahora=NOW),'operacionId':'SYNTHETIC',
            'sha256':'d'*64,'revisionReferencia':SID}
        with patch.object(cotizacion,'_obtener',return_value=case), \
                patch.object(elegibilidad,'leer_decision_estudio',return_value=decision), \
                patch.object(cotizacion,'_contexto',return_value=(None,{'solicitudId':SID})) as context, \
                patch.object(cotizacion,'preparar_oferta_linix') as offer:
            result=cotizacion.preparar_cotizacion_radicacion_especial(SID,Mock())
            context.assert_called_once_with(case,lock=False);offer.assert_not_called()
            self.assertFalse(result['ejecutaCuota']);self.assertFalse(result['ofertaContractual'])
            self.assertFalse(result['autorizaStart']);self.assertFalse(result['autorizaFirma'])
            decision['resultado']=evaluar_decision_digital(self.c,ahora=NOW)
            with self.assertRaisesRegex(ValueError,'RADICACION_ESPECIAL_REQUIERE_ESTUDIO_FAVORABLE'):
                cotizacion.preparar_cotizacion_radicacion_especial(SID,Mock())

    def test_owner_continuity_projection_needs_no_classifier_module_and_preserves_wire_states(self):
        import ast,inspect
        from apps.xcore_consumo.services.credihoy_v2.unico.elegibilidad import continuidad_desde_decision
        source=inspect.getsource(continuidad_desde_decision)
        self.assertFalse(any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.walk(ast.parse(source))))
        namespace={};exec(compile(source,'owner-without-classifier','exec'),namespace)
        for description,state in [('TRABAJADOR CONGENTE',COLABORADOR),('GRADO CIVIL TRABAJADOR',FAMILIAR)]:
            c=replace(self.c,clasificacion_estamentos=classify([{'resultado':'SI','tipofamiliar':description}]))
            result=evaluar_decision_digital(c,ahora=NOW)
            projection=namespace['continuidad_desde_decision'](result)
            self.assertTrue(projection['estudioFavorable']);self.assertEqual(projection['clasificacion'],state)

    def test_technical_quote_keeps_real_scope_and_rejects_ordinary_or_mixed_identity(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from apps.xcore_consumo.services.credihoy_v2.unico import cotizacion,funcional,elegibilidad
        case=SimpleNamespace(pk=SID);operator=Mock()
        c=replace(self.c,clasificacion_estamentos=classify([{'resultado':'SI','tipofamiliar':'TRABAJADOR CONGENTE'}]))
        with patch.object(funcional,'obtener_para_evaluacion',return_value=case) as access, \
                patch.object(elegibilidad,'leer_decision_estudio',return_value={'resultado':evaluar_decision_digital(c,ahora=NOW)}):
            self.assertEqual(cotizacion._obtener(SID,operator,autogestion_tecnica=True,lock=True),case)
            access.assert_called_once_with(SID,operator,autogestion=True,revision_conservada=True,lock=True)
            with self.assertRaisesRegex(ValueError,'NO_MEZCLAR_ACTOR_Y_TITULAR'):
                cotizacion._obtener(SID,operator,titular={'numeroDocumento':'SYNTHETIC'},autogestion_tecnica=True)
        with patch.object(funcional,'obtener_para_evaluacion',return_value=case), \
                patch.object(elegibilidad,'leer_decision_estudio',return_value={'resultado':evaluar_decision_digital(self.c,ahora=NOW)}):
            with self.assertRaisesRegex(ValueError,'COTIZACION_TECNICA_SOLO_RADICACION_ESPECIAL'):
                cotizacion._obtener(SID,operator,autogestion_tecnica=True)

    def test_missing_expired_foreign_classification_and_prior_start_do_not_reopen_chain(self):
        for c in (replace(self.c,clasificacion_estamentos=None),replace(self.c,titular_referencia='b'*64)):
            self.assertEqual(evaluar_decision_digital(c,ahora=NOW)['estado'],'BLOQUEO_ESTAMENTOS_SIN_EVIDENCIA')
        self.assertEqual(evaluar_decision_digital(self.c,ahora=NOW+timedelta(minutes=6))['estado'],'BLOQUEO_ESTAMENTOS_SIN_EVIDENCIA')
        result=evaluar_decision_digital(replace(self.c,start_previos=1),ahora=NOW)
        self.assertEqual(result['accionPermitida'],'SOLO_CONCILIAR')
        self.assertFalse(result['autorizaContinuacionInstitucional'])
        transport=Mock()
        gate=PuertaRadicacionOrdinaria(autoridad=self.a,politica=self.p,
            cargar_contexto=lambda _:replace(self.c,clasificacion_estamentos=None),transporte=transport)
        with self.assertRaises(RadicacionOrdinariaBloqueada):gate.preflight(self.c.solicitud_id,ahora=NOW)
        transport.start.assert_not_called()


class OfficialPdfTests(TestCase):
    def test_payment_channel_uses_existing_widgets_and_requires_verified_selection(self):
        for method,field,other in [('VENTANILLA','Check Box25','Check Box26'),('NOMINA','Check Box26','Check Box25')]:
            pdf,meta=_diligenciar('COLO-FO.001',{'medioPago':{'valor':method,'referencia':'SYNTHETIC_VERIFIED','verificado':True}})
            fields=PdfReader(BytesIO(pdf)).get_fields()
            self.assertEqual(str(fields[field]['/V']),'/S\u00ed');self.assertEqual(str(fields[other]['/V']),'/Off')
            self.assertIn('medioPago',meta['evidenciasPorCampo']);self.assertFalse(meta['firmaAcreditada'])
        with self.assertRaises(ValueError):
            _diligenciar('COLO-FO.001',{'medioPago':{'valor':'DEBITO','referencia':'SYNTHETIC','verificado':True}})

    def test_institutional_liv_classification_maps_existing_checkbox_not_a_new_option(self):
        pdf,meta=_diligenciar('COLO-FO.001',{},perfil=('390','111','CFA'))
        fields=PdfReader(BytesIO(pdf)).get_fields()
        self.assertEqual(str(fields['Check Box14'].get('/V')),'/S\u00ed')
        for key in ('Check Box15','Check Box16','Check Box17'):
            self.assertEqual(str(fields[key].get('/V')),'/Off')
        self.assertEqual(meta['clasificacionProducto']['linea'],'LIV')
        self.assertNotIn('CLASIFICACION_LINEA_PRODUCTO',meta['pendientes'])
        self.assertFalse(meta['firmaAcreditada'])

    def test_inspected_original_versions_fields_rectangles_and_appearance(self):
        for code,count,pages in [('COLO-FO.001',98,3),('COLO-FO.023',9,1)]:
            inventory=inventario_formato(code)
            self.assertEqual(len(inventory['campos']),count);self.assertEqual(inventory['paginas'],pages)
            self.assertTrue(all(len(f['rect'])==4 for f in inventory['campos']))
        self.assertTrue(inventario_formato('COLO-FO.001')['observaciones'])

    def test_filled_pdf_reproducible_preserves_static_pages_and_not_a_signature(self):
        for code in ('COLO-FO.001','COLO-FO.023'):
            fields={'nombre':{'valor':'TITULAR SINTETICO','referencia':'FIXTURE_NOT_INSTITUTIONAL','verificado':True}}
            pdf,meta=_diligenciar(code,fields);again,_=_diligenciar(code,fields)
            self.assertEqual(pdf,again)
            self.assertFalse(meta['firmaAcreditada']);self.assertFalse(meta['envioHabilitado'])
            reader=PdfReader(BytesIO(pdf));source=PdfReader(BytesIO(original(code)[0]))
            self.assertEqual([p.extract_text() for p in reader.pages],[p.extract_text() for p in source.pages])
            self.assertNotIn('TITULAR SINTETICO',str(meta))
            self.assertIn('TITULAR SINTETICO',[f.get('/V') for f in reader.get_fields().values()])

    def test_health_signatures_product_checkbox_and_unverified_fields_never_prefill(self):
        for key in ('salud','firma','Check Box29','lineaCredito','Check Box14'):
            with self.assertRaises(ValueError):_diligenciar('COLO-FO.001',{key:{'valor':'SI','referencia':'TEST','verificado':True}})
        with self.assertRaises(ValueError):_diligenciar('COLO-FO.023',{'nombre':{'valor':'X','referencia':'TEST','verificado':False}})
        pdf,_=_diligenciar('COLO-FO.001',{})
        r=PdfReader(BytesIO(pdf))
        self.assertTrue(all(not str(f.get('/V','')) for f in r.get_fields().values()))

    def test_communications_is_a_separate_real_selection_not_default_yes(self):
        for choice in (None,{'valor':False,'referenciaAceptacionTitular':'SYNTHETIC_EXPLICIT_CHOICE'}):
            pdf,_=_diligenciar('COLO-FO.023',{},seleccion_comunicaciones=choice)
            fields=PdfReader(BytesIO(pdf)).get_fields()
            self.assertEqual(fields['No'].get('/V',''),'X' if choice is not None else '')
        with self.assertRaises(ValueError):_diligenciar('COLO-FO.023',{},seleccion_comunicaciones=True)


class DisbursementGateTests(TestCase):
    def test_special_can_finish_only_after_real_human_selector_and_estamento_decision(self):
        ready=self.ready();ready['clasificacion'].update(estado=FAMILIAR,autorizaAutomatizacion=False)
        ready['aprobacion'].update(SC='90002',K_FLUJO='90001')
        decision={'solicitudId':SID,'fuente':'LINIX','verificado':True,'referencia':'SYNTHETIC_HUMAN_ONLY',
            'sha256':'e'*64,'SC':'90002','K_FLUJO':'90001','ruta':10002991,'K_SERIE':'502',
            'estadoTarea':'E','usuario':'HUMAN_TEST','respuesta':'APRUEBA','intervencionHumanaAcreditada':True,
            'seleccion121':{'actividad':121,'ruta':10002852,'destino':10002991,'estado':'E',
                'usuario':'ANALYST_TEST','K_SERIE':'501'}}
        self.assertIn('DECISION_HUMANA_ESTAMENTO_NO_ACREDITADA',precondiciones_desembolso(SID,ahora=NOW,**ready)['pendientes'])
        result=precondiciones_desembolso(SID,ahora=NOW,decision_estamento=decision,**ready)
        self.assertEqual(result['estado'],'PRECONDICIONES_ACREDITADAS_SIN_EJECUCION')
        self.assertFalse(result['autorizaDesembolso'])
        for mutate in (lambda d:d.update(usuario='CGTE'),lambda d:d.update(ruta=10002856),
                lambda d:d.update(SC='OTHER'),lambda d:d.update(respuesta='RECHAZA'),
                lambda d:d['seleccion121'].update(usuario='CGTE'),lambda d:d['seleccion121'].update(estado='A')):
            changed=deepcopy(decision);mutate(changed)
            self.assertIn('DECISION_HUMANA_ESTAMENTO_NO_ACREDITADA',
                precondiciones_desembolso(SID,ahora=NOW,decision_estamento=changed,**ready)['pendientes'])

    def ready(self):
        def receipt(source):return {'solicitudId':SID,'fuente':source,'verificado':True,'referencia':'SYNTHETIC',
            'sha256':'c'*64,'firmado':True,'eventoFirmante':'SYNTHETIC_EVENT','integridadVerificada':True}
        return dict(clasificacion=classify(),aprobacion={**receipt('LINIX'),'aprobada':True},
            condiciones=receipt('ACEPTACION_TITULAR'),liquidacion={**receipt('LINIX'),'liquidada':True},
            cuenta={**receipt('CUENTA_INSTITUCIONAL'),'titularVerificado':True,'disponibleLegalmente':True},
            documentos_autentic=[{**receipt('AUTENTIC'),'codigo':c} for c in ('COLO-FO.001','COLO-FO.023')],
            pagare_linpag=receipt('LINPAG'),journal={'solicitudId':SID,'resultadoIncierto':False,
                'desembolsosPrevios':0,'claveIdempotencia':'SYNTHETIC_ONCE'})

    def test_no_signature_no_payment_and_http_success_not_signature(self):
        ready=self.ready()
        self.assertEqual(precondiciones_desembolso(SID,ahora=NOW,**ready)['estado'],'PRECONDICIONES_ACREDITADAS_SIN_EJECUCION')
        for mutate in (lambda p:p['documentos_autentic'][0].update(firmado=False),
                lambda p:p['pagare_linpag'].update(eventoFirmante=None,http=200),
                lambda p:p['cuenta'].update(disponibleLegalmente=False),
                lambda p:p['journal'].update(resultadoIncierto=True),
                lambda p:p['journal'].update(desembolsosPrevios=1),
                lambda p:p['aprobacion'].update(solicitudId='OTHER'),
                lambda p:p['clasificacion'].update(estado=FAMILIAR)):
            bad=deepcopy(ready);mutate(bad)
            result=precondiciones_desembolso(SID,ahora=NOW,**bad)
            self.assertEqual(result['estado'],'BLOQUEADO');self.assertFalse(result['autorizaDesembolso'])

    def test_expired_classification_and_malformed_signature_list_cannot_release_payment(self):
        ready=self.ready()
        self.assertEqual(precondiciones_desembolso(SID,ahora=NOW+timedelta(minutes=6),**ready)['estado'],'BLOQUEADO')
        ready['documentos_autentic']=[None,True]
        self.assertEqual(precondiciones_desembolso(SID,ahora=NOW,**ready)['estado'],'BLOQUEADO')

    def test_verified_linix_receipt_does_not_imply_liquidation_occurred(self):
        for value in (None,False,'Y',1):
            ready=self.ready();ready['liquidacion']['liquidada']=value
            result=precondiciones_desembolso(SID,ahora=NOW,**ready)
            self.assertIn('LIQUIDACION_NO_REGISTRADA',result['pendientes'])
            self.assertEqual(result['estado'],'BLOQUEADO')
            self.assertFalse(result['transporteHabilitado']);self.assertFalse(result['autorizaDesembolso'])
        ready=self.ready();del ready['liquidacion']['liquidada']
        self.assertIn('LIQUIDACION_NO_REGISTRADA',precondiciones_desembolso(SID,ahora=NOW,**ready)['pendientes'])


from django.test import TransactionTestCase


class PersistedDigitalTests(TransactionTestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from tools.cd2b.support import seed
        from apps.xcore_consumo.models import EvaluacionConsumo
        import hashlib,json
        self.s=seed();self.user=get_user_model().objects.create_user('policy-operator-synthetic',is_staff=True)
        self.s.asesor=self.user;self.s.producto='CREDIHOY UNICO (NUEVO)';self.s.metadatos={'modo':'CONSULTA_REAL_CONTROLADA'};self.s.save()
        context,_,_,_=fixture.fixture()
        snapshot={'solicitudId':str(self.s.pk),'reglas':list(context.reglas),'decisionCrediticia':'NO_AUTORIZADA'}
        self.e=EvaluacionConsumo.objects.create(solicitud=self.s,decision_final='NO_AUTORIZADA',resultados={'TEST_SNAPSHOT':snapshot})
        digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,default=str).encode()).hexdigest()
        e=evidence();e['solicitudId']=str(self.s.pk)
        classification=clasificar_novedades(e,solicitud_id=self.s.pk,titular_referencia=TITULAR,ahora=NOW,monto=Decimal('400000'))
        self.c=replace(context,solicitud_id=str(self.s.pk),evaluacion_id=digest,
            titular_referencia=TITULAR,clasificacion_estamentos=classification)

    def test_policy_journal_is_idempotent_and_does_not_rewrite_historical_evaluation(self):
        from uuid import uuid4
        from apps.xcore_consumo.services.credihoy_v2.unico.elegibilidad import registrar_decision_digital
        from apps.utils.models import AuditLog
        before=deepcopy(self.e.resultados);key=uuid4()
        def record():return registrar_decision_digital(self.s.pk,operador=self.user,contexto=self.c,
            snapshot_clave='TEST_SNAPSHOT',clave_idempotencia=key,ahora=NOW)
        first=record();self.assertEqual(record(),first)
        self.assertEqual(first['resultado']['estado'],'APROBADO')
        self.e.refresh_from_db();self.assertEqual(self.e.resultados,before);self.assertEqual(self.e.decision_final,'NO_AUTORIZADA')
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_DECISION_DIGITAL_LOCAL').count(),1)
        self.c=replace(self.c,capacidad_cumple_criterio=False)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCIA_'):record()

    def test_pdf_access_checks_owner_and_does_not_persist_signed_documents(self):
        from django.contrib.auth.models import Permission
        from django.contrib.auth import get_user_model
        from django.core.exceptions import PermissionDenied
        from apps.documentos.formularios_credihoy import diligenciar_para_expediente
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='solicitudes',codename='view_solicitud'),
            Permission.objects.get(content_type__app_label='documentos',codename='view_versiondocumental'))
        pdf,meta=diligenciar_para_expediente(self.s.pk,actor=self.user,codigo='COLO-FO.023',campos={})
        self.assertTrue(pdf.startswith(b'%PDF'));self.assertEqual(meta['solicitudId'],str(self.s.pk))
        outsider=get_user_model().objects.create_user('foreign-synthetic',is_staff=True)
        outsider.user_permissions.set(self.user.user_permissions.all())
        with self.assertRaises(PermissionDenied):
            diligenciar_para_expediente(self.s.pk,actor=outsider,codigo='COLO-FO.023',campos={})
        from apps.documentos.formularios_credihoy import preparar_autentic_para_expediente
        metadata={'names':'SYNTHETIC','lastNames':'ONLY','docId':self.s.solicitante.numero_identificacion,
            'secureKey':'SYNTHETIC_NOT_OTP_OR_REAL_SECRET'}
        request,evidence=preparar_autentic_para_expediente(self.s.pk,actor=self.user,
            codigo='COLO-FO.023',campos={},metadata=metadata)
        self.assertEqual(evidence['etapa'],'PRECONSULTA');self.assertFalse(evidence['envioHabilitado'])
        self.assertEqual(list(request.hashes_pdf),['COLO-FO.023.pdf'])
        self.assertFalse(evidence['pagareIncluido'])
        with self.assertRaisesRegex(ValueError,'FIRMANTE_DE_OTRO_EXPEDIENTE'):
            preparar_autentic_para_expediente(self.s.pk,actor=self.user,codigo='COLO-FO.023',campos={},
                metadata={**metadata,'docId':'OTHER'})

    def test_latest_failed_novelty_attempt_never_reuses_a_previous_success(self):
        from hashlib import sha256
        from apps.xcore_consumo.models import ConsultaEstamentoOracle
        from apps.xcore_consumo.services.credihoy_v2.unico.estado_credito import construir_estado
        from tools.cd5a.test_funcional import FORM,_resultado
        raw=evidence();raw.update(solicitudId=str(self.s.pk),
            titularReferencia=sha256(self.s.solicitante.numero_identificacion.encode()).hexdigest())
        packet={'resultado':_resultado(self.s,FORM,'SYNTHETIC'),
            'consentimiento':{'version':'SYNTHETIC','estado':'CONSENTIMIENTO_FIRMADO'},'estadoObtencionDatos':[]}
        ConsultaEstamentoOracle.objects.create(solicitud=self.s,estado='OK',response_payload=raw)
        first=construir_estado(self.s,packet,ahora=NOW)['operador']['decisionDigital']
        self.assertNotEqual(first['estado'],'BLOQUEO_ESTAMENTOS_SIN_EVIDENCIA')
        ConsultaEstamentoOracle.objects.create(solicitud=self.s,estado='ERROR',response_payload=raw)
        second=construir_estado(self.s,packet,ahora=NOW)['operador']['decisionDigital']
        self.assertEqual(second['estado'],'BLOQUEO_ESTAMENTOS_SIN_EVIDENCIA')
        self.assertFalse(second['autorizaContinuacionInstitucional'])

    def test_existing_sp_caller_is_intercepted_and_never_defaults_empty_to_no(self):
        from unittest.mock import MagicMock,patch
        from apps.xcore_consumo.services import oracle
        from apps.xcore_consumo.models import ConsultaEstamentoOracle
        with patch.object(oracle,'_oracle_enabled',return_value=True),patch(
                'apps.xcore_consumo.services.credihoy_v2.unico.estamentos.exigir_consulta_familiar_segura'):
            for rows,full in (([],False),([('SI','TRABAJADOR'),('SI','GRADO CIVIL TRABAJADOR')],False),
                    ([('SI','TRABAJADOR'),('SI','GRADO CIVIL TRABAJADOR')],True)):
                conn=MagicMock();cursor=MagicMock();out=MagicMock()
                conn.cursor.side_effect=[cursor,out]
                out.description=[('SYNTHETIC_FLAG',),('SYNTHETIC_DESCRIPTION',)]
                out.__iter__.return_value=iter(rows)
                with patch.object(oracle,'_connect',return_value=conn):
                    if full:
                        result=oracle.consultar_familiar(self.s,'0000000000',todas_novedades=True)
                        self.assertEqual(len(result['filas']),2);self.assertFalse(result['contratoAcreditado'])
                    else:
                        with self.assertRaises((ValueError,oracle.OracleIntegrationError)):
                            oracle.consultar_familiar(self.s,'0000000000')
                record=ConsultaEstamentoOracle.objects.latest('pk')
                if rows:self.assertEqual(len(record.response_payload['filas']),2)
                else:self.assertEqual(record.estado,'ERROR')
                cursor.callproc.assert_called_once_with('SP_CONSULTAFAMILIAR',['0000000000',out])

    def test_installed_sp_shared_delete_and_commits_block_before_connect(self):
        from unittest.mock import patch
        from apps.xcore_consumo.services import oracle
        with patch.object(oracle,'_oracle_enabled',return_value=True),patch.object(oracle,'_connect') as connect:
            with self.assertRaisesRegex(ValueError,'EFECTOS_COMPARTIDOS_NO_CONTENIDOS'):
                oracle.consultar_familiar(self.s,'0000000000',todas_novedades=True)
            connect.assert_not_called()

    def test_generated_document_private_custody_idempotency_and_cross_case_rejection(self):
        from uuid import uuid4
        from django.contrib.auth.models import Permission
        from apps.documentos.formularios_credihoy import custodiar_formato,recuperar_formato
        from apps.xcore_consumo.models import SolicitudConsumo
        from apps.utils.models import AuditLog
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='solicitudes',codename='view_solicitud'),
            Permission.objects.get(content_type__app_label='documentos',codename='view_versiondocumental'),
            Permission.objects.get(content_type__app_label='documentos',codename='add_eventodocumental'))
        fields={'nombre':{'valor':'PERSONA SINTETICA','referencia':'SYNTHETIC','verificado':True}}
        key=uuid4()
        first=custodiar_formato(self.s.pk,actor=self.user,codigo='COLO-FO.023',campos=fields,clave_idempotencia=key)
        again=custodiar_formato(self.s.pk,actor=self.user,codigo='COLO-FO.023',campos=fields,clave_idempotencia=key)
        self.assertEqual(first,again)
        detail=SolicitudConsumo.objects.get(solicitud=self.s)
        self.assertNotIn('PERSONA SINTETICA',str(detail.orchestration_data))
        pdf,metadata=recuperar_formato(self.s.pk,actor=self.user,operacion_id=key)
        self.assertEqual(metadata['pdfSha256'],first['pdfSha256']);self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_PDF_OFICIAL_CUSTODIADO').count(),1)
        self.assertNotIn('PERSONA SINTETICA',str(list(AuditLog.objects.values_list('payload',flat=True))))
        fields['nombre']['valor']='DISTINTA'
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCIA_DOCUMENTO_CONFLICTO'):
            custodiar_formato(self.s.pk,actor=self.user,codigo='COLO-FO.023',campos=fields,clave_idempotencia=key)
        stored=detail.orchestration_data['credihoy_pdf_oficiales_v1'][str(key)]
        stored['metadata']['pdfSha256']='0'*64
        detail.save(update_fields=['orchestration_data'])
        with self.assertRaisesRegex(ValueError,'CUSTODIA_DOCUMENTAL_NO_INTEGRA'):
            recuperar_formato(self.s.pk,actor=self.user,operacion_id=key)

    def test_partial_scalar_quote_cannot_become_signable_credit_form(self):
        from django.contrib.auth.models import Permission
        from unittest.mock import patch
        from apps.documentos.formularios_credihoy import preparar_autentic_para_expediente
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='solicitudes',codename='view_solicitud'),
            Permission.objects.get(content_type__app_label='documentos',codename='view_versiondocumental'))
        metadata={'names':'SYNTHETIC','lastNames':'ONLY','docId':self.s.solicitante.numero_identificacion,
            'secureKey':'SYNTHETIC_NOT_REAL'}
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.cotizacion.leer_cotizacion_guardada',
                return_value={'disponible':True,'cotizacionIntegral':False}):
            with self.assertRaisesRegex(ValueError,'CONDICIONES_CONTRACTUALES_DEFINITIVAS'):
                preparar_autentic_para_expediente(self.s.pk,actor=self.user,codigo='COLO-FO.001',campos={},metadata=metadata)

    def test_official_pdf_autentic_send_and_download_intercepted_never_certify_signature(self):
        import sqlite3
        from io import BytesIO
        from zipfile import ZipFile
        from django.contrib.auth.models import Permission
        from apps.documentos.formularios_credihoy import preparar_autentic_para_expediente
        from apps.integraciones.autentic.cliente import AutenticDEVClient
        from apps.integraciones.autentic.contratos import ConfiguracionDEV
        from apps.integraciones.autentic.operaciones import RegistroFirmas
        from apps.integraciones.autentic.tests.test_cliente import Response,Session,token,recibo
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label='solicitudes',codename='view_solicitud'),
            Permission.objects.get(content_type__app_label='documentos',codename='view_versiondocumental'))
        metadata={'names':'SYNTHETIC','lastNames':'ONLY','docId':self.s.solicitante.numero_identificacion,
            'secureKey':'SYNTHETIC_NOT_REAL'}
        request,meta=preparar_autentic_para_expediente(self.s.pk,actor=self.user,codigo='COLO-FO.023',campos={},metadata=metadata)
        provider=recibo()
        session=Session(Response(token()),Response(provider,201))
        client=AutenticDEVClient(ConfiguracionDEV('SYNTHETIC','SYNTHETIC',permitir_documentos=True),session=session)
        with sqlite3.connect(':memory:') as db:
            journal=RegistroFirmas(db)
            receipt=client.firmar(request,clave='SYNTHETIC_ONCE',registro=journal,referencia_autorizacion='MOCK_ONLY')
            self.assertEqual(receipt.estado,'FIRMA_REPORTADA_NO_VERIFICADA')
            client.firmar(request,clave='SYNTHETIC_ONCE',registro=journal,referencia_autorizacion='MOCK_ONLY')
            self.assertEqual(len(session.calls),2)
        self.assertFalse(meta['firmaAcreditada']);self.assertFalse(meta['envioHabilitado'])
        # Download verification only confirms archive/PDF integrity, not a signer.
        import base64
        from apps.integraciones.autentic.evidencia import verificar_zip
        archive=BytesIO()
        with ZipFile(archive,'w') as zipped:
            zipped.writestr('COLO-FO.023.pdf',base64.b64decode(request.payload['files'][0]['fileContent']))
        evidence=verificar_zip(archive.getvalue(),['COLO-FO.023.pdf'])['COLO-FO.023.pdf']
        self.assertEqual(evidence.sha256,meta['pdfSha256'])
        self.assertFalse(evidence.firma_criptografica_validada)
