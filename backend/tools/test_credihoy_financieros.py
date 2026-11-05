from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest import TestCase

from apps.xcore_consumo.services.credihoy_v2.unico.embargos import leer_embargos_persistidos
from apps.xcore_consumo.services.credihoy_v2.unico.reglas import HechosReglasUnicoV2, evaluar_reglas_unico_v2
from apps.xcore_consumo.services.credihoy_v2.unico.dto import EntradaCredihoyUnicoV2
from apps.xcore_consumo.services.credihoy_v2.base import Hecho, EstadoCertificacion as EC


class CabSummaryTests(TestCase):
    def setUp(self):
        from unittest.mock import patch
        for target in ('requests.sessions.Session.send','socket.create_connection',
                'socket.getaddrinfo','django.db.backends.base.base.BaseDatabaseWrapper.cursor'):
            self.enterContext(patch(target,side_effect=AssertionError('CAB_TEST_SIDE_EFFECT_FORBIDDEN')))

    def controlled_context(self, rows):
        from uuid import UUID
        from apps.integraciones.risk_gateway.operaciones.servicio import ResultadoOperacion
        from apps.xcore_consumo.services.credihoy_v2.unico.dto import RecogidaCredihoyUnicoV2
        from apps.xcore_consumo.services.credihoy_v2.unico.normativa import POLITICA_FINANCIERA_CONGENTE
        dto=HdcReconciliationTests().evidence(rows)
        rid=UUID(dto['requestId'])
        op=ResultadoOperacion(UUID('22222222-2222-4222-8222-222222222222'),'HDC','COMPLETADA',
            rid,UUID(dto['resultado']['consulta_id']),dto['versionContrato'],'',False,dto)
        entry=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('400000'),plazo_meses=2,
            periodicidad_pago='MENSUAL',modalidad_codigo='111',recogida=RecogidaCredihoyUnicoV2(aplica=False))
        income=Hecho(valor=Decimal('2435509'),disponible=True,fuente='LINIX.AP014MCLIENTE',
            referencia_evidencia=POLITICA_FINANCIERA_CONGENTE,ruta_origen='adaptacionCongente.ingresoComputableAp014',
            estado_certificacion=EC.OBSERVADO)
        return entry,dict(hdc=op,operaciones_esperadas={op.operacion_id:('HDC',rid)},
            ingresos=income,hechos_reglas=HechosReglasUnicoV2(),
            fecha=datetime(2026,10,31,15,tzinfo=timezone.utc),request_id=str(rid))

    def assert_controlled_non_decisory(self, result):
        self.assertEqual(len(result['reglas']),16)
        self.assertEqual(result['decisionCrediticia'],'NO_AUTORIZADA')
        self.assertFalse(result['autorizadoDecisionCrediticia'])
        self.assertEqual(result['capacidadMatematica']['estado'],'NO_EVALUABLE')
        self.assertFalse(result['conciliacionHdc']['cuotas_aplicables']['disponible'])
        self.assertFalse(result['conciliacionHdc']['aprobacion_riesgos'])
        rows={r['codigo']:r for r in result['reglas']}
        self.assertEqual(rows['UNICO_FILA_10']['resultado'],'NO_EVALUABLE')
        self.assertEqual(rows['UNICO_FILA_14']['resultado'],'NO_EVALUABLE')
        self.assertEqual(rows['UNICO_FILA_32']['estadoEvaluacion'],'NO_EVALUABLE')
        self.assertIn('CORTE_EXISTENCIA_111_NO_ACREDITADO',rows['UNICO_FILA_32']['motivos'])

    def test_controlled_engine_forwards_xml_once_without_relabelling_or_promoting_decision(self):
        from copy import deepcopy
        from hashlib import sha256
        from lxml import etree as E
        from unittest.mock import patch
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        from apps.xcore_consumo.services.credihoy_v2.unico.evaluacion_controlada import evaluar_respuestas_controladas
        f,row,root,detail,summary=self.scenario()
        entry,context=self.controlled_context([row]);xml=E.tostring(root)
        before=deepcopy(context['hdc'].respuesta.a_dict())
        old=evaluar_respuestas_controladas(entry,**context)
        self.assertEqual(old,evaluar_respuestas_controladas(entry,**context,informe_xml=None))
        self.assertFalse(old['conciliacionHdc']['partidas_resueltas'])
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.conciliacion.conciliar_cuotas_hdc',
                wraps=conciliar_cuotas_hdc) as reconcile:
            result=evaluar_respuestas_controladas(entry,**context,informe_xml=xml)
            reconcile.assert_called_once_with(context['hdc'].respuesta,criterio_funcional=True,informe_xml=xml)
        self.assertTrue(result['conciliacionHdc']['partidas_resueltas'])
        part=result['conciliacionHdc']['partidas'][0]
        self.assertEqual(part['periodicidad'],'9')
        self.assertEqual(part['xml_cotejado_sha256'],sha256(xml).hexdigest())
        self.assertEqual(result['capacidadPreliminar']['reservas'],[])
        self.assertFalse(result['capacidadPreliminar']['autorizadoDecisionCrediticia'])
        self.assert_controlled_non_decisory(result)
        self.assertEqual(context['hdc'].respuesta.a_dict(),before)
        self.assertEqual(E.tostring(root),xml)

    def test_controlled_engine_keeps_cab_reservations_for_manipulated_or_foreign_xml(self):
        from copy import deepcopy
        from lxml import etree as E
        from apps.xcore_consumo.services.credihoy_v2.unico.evaluacion_controlada import evaluar_respuestas_controladas
        for change in ('account','owner','report_date','value_date','quota','balance','initial','duplicate'):
            with self.subTest(change=change):
                f,row,root,detail,summary=self.scenario()
                entry,context=self.controlled_context([row])
                if change=='account':detail.set('numero','OTHER_SYNTHETIC_ACCOUNT')
                if change=='owner':detail.set('identificacion','OTHER_SYNTHETIC_PERSON')
                if change=='report_date':root[0].set('fechaConsulta','2025-01-01T00:00:00')
                if change=='value_date':detail.find('Valores/Valor').set('fecha','2025-01-01')
                if change in ('quota','balance','initial'):
                    summary.set({'quota':'cuotaMes','balance':'saldoActual','initial':'valorInicial'}[change],'6')
                if change=='duplicate':summary.getparent().append(deepcopy(summary))
                if change=='report_date':
                    with self.assertRaisesRegex(ValueError,'XML_HDC_NO_CORRESPONDE_A_RESPUESTA'):
                        evaluar_respuestas_controladas(entry,**context,informe_xml=E.tostring(root))
                    continue
                result=evaluar_respuestas_controladas(entry,**context,informe_xml=E.tostring(root))
                self.assertFalse(result['conciliacionHdc']['partidas_resueltas'])
                self.assertEqual(result['conciliacionHdc']['partidas'][0]['tratamiento'],'PENDIENTE')
                self.assertNotIn('capacidadPreliminar',result)
                self.assert_controlled_non_decisory(result)

    def test_controlled_engine_rejects_xxe_and_malformed_xml_without_effects(self):
        from lxml import etree as E
        from apps.xcore_consumo.services.credihoy_v2.unico.evaluacion_controlada import evaluar_respuestas_controladas
        f,row,root,detail,summary=self.scenario();entry,context=self.controlled_context([row])
        xml=E.tostring(root)
        xxe=(b'<!DOCTYPE Informes [<!ENTITY xxe SYSTEM "https://xxe.example.invalid/forbidden">]>'
            +xml.replace(b'</Informes>',b'&xxe;</Informes>'))
        for forbidden in (b'<!DOCTYPE Informes>'+xml,xxe):
            with self.subTest(xml=forbidden[:40]),self.assertRaisesRegex(ValueError,'XML_DTD_PROHIBIDO'):
                evaluar_respuestas_controladas(entry,**context,informe_xml=forbidden)
        with self.assertRaisesRegex(ValueError,'XML_HDC_INVALIDO'):
            evaluar_respuestas_controladas(entry,**context,informe_xml=b'<Informes>')

    def test_verified_embargo_fact_reaches_all_rules_as_information(self):
        from types import SimpleNamespace
        from lxml import etree as E
        from unittest.mock import patch
        from apps.xcore_consumo.services.credihoy_v2.unico.evaluacion_controlada import evaluar_respuestas_controladas
        f,row,root,detail,summary=self.scenario();entry,context=self.controlled_context([row])
        fact=Hecho(valor=True,disponible=True,fuente='SYNTHETIC_HDC',estado_certificacion=EC.CERTIFICADO,
            referencia_evidencia='SYNTHETIC-XML',ruta_origen='Informes/Informe/Cuenta/Estado')
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.embargos.leer_embargos_persistidos',
                return_value=SimpleNamespace(cuentas_embargadas=fact)):
            result=evaluar_respuestas_controladas(entry,**context,informe_xml=E.tostring(root),
                fecha_consulta_hdc=context['fecha'])
        self.assertEqual(len(result['reglas']),16)
        embargo=next(r for r in result['reglas'] if r['codigo']=='UNICO_FILA_6')
        self.assertEqual(embargo['resultado'],'NO_CUMPLE')
        self.assertEqual(embargo['efecto'],'INFORMATIVO')

    def scenario(self):
        from lxml import etree as E
        from apps.integraciones.risk_gateway.tests.fixtures import importe
        f=HdcReconciliationTests();row=f.row(quota='5000',period='9')
        row['tipoCuenta'].update(codigo_original='CAB',abreviatura='CAB',semantica='CONOCIDA')
        row['sector']['codigo_original']='1'
        attrs={'numero':row['identificador']['numero'],'identificacion':'TITULAR-SINTETICO',
            'codSuscriptor':'SYNTHETIC','entidad':row['entidad'],'sector':'1',
            'fechaApertura':'2025-01-01','fechaVencimiento':'2029-01-01'}
        row['identificador']['identificacion']=attrs['identificacion']
        row['datos_originales']['atributos']=[list(p) for p in attrs.items()]
        row['valores'][0].update(saldoActual=importe('106000','106000'),valorInicial=importe('14500000','14500000'))
        root=E.Element('Informes');report=E.SubElement(root,'Informe',fechaConsulta='2026-10-01T12:00:00')
        detail=E.SubElement(report,'CuentaCartera',**attrs)
        E.SubElement(detail,'Caracteristicas',tipoCuenta='CAB',calidadDeudor='00')
        E.SubElement(E.SubElement(detail,'Valores'),'Valor',fecha='2026-10-01',periodicidad='9',moneda='1',
            cuota='5000',saldoActual='106000',valorInicial='14500000')
        sector=E.SubElement(E.SubElement(E.SubElement(E.SubElement(report,'InfoAgregadaMicrocredito'),'Resumen'),
            'EndeudamientoActual'),'Sector',codSector='1')
        summary=E.SubElement(E.SubElement(E.SubElement(sector,'TipoCuenta',tipoCuenta='CAB'),'Usuario',tipoUsuario='Principal'),
            'Cuenta',estadoActual='Al d\u00eda',cuotaMes='5.0',saldoActual='106.0',valorInicial='14500.0')
        return f,row,root,detail,summary

    def test_cab_monthly_field_does_not_relabel_period_9_or_authorize_decision(self):
        from lxml import etree as E
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        f,row,root,detail,summary=self.scenario()
        r=conciliar_cuotas_hdc(f.evidence([row]),criterio_funcional=True,informe_xml=E.tostring(root))
        self.assertEqual(r.subtotal_reconocido.valor,Decimal('5000'))
        p=r.partidas[0];self.assertEqual(p.periodicidad,'9');self.assertEqual(p.cuota_mes_original,'5.0')
        self.assertIn('/Usuario/Cuenta/@cuotaMes',p.ruta_cuota_mes)
        self.assertIn('/Valores/Valor/@periodicidad',p.ruta_periodicidad_detalle)
        self.assertTrue(r.partidas_resueltas);self.assertFalse(r.cuotas_aplicables.disponible)
        self.assertFalse(r.aprobacion_riesgos);self.assertEqual(len(p.xml_cotejado_sha256),64)
        old=conciliar_cuotas_hdc(f.evidence([row]),criterio_funcional=True)
        self.assertEqual(old.partidas[0].tratamiento,'PENDIENTE')

    def test_cab_requires_unique_identity_matching_cutoff_and_three_cop_controls(self):
        from copy import deepcopy
        from lxml import etree as E
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        for change in ('identity','cutoff','sector','state','quota','balance','initial','invalid','summary_copy','detail_copy','dto_copy'):
            with self.subTest(change=change):
                f,row,root,detail,summary=self.scenario();rows=[row]
                if change=='identity':detail.set('numero','OTRA-CUENTA')
                if change=='cutoff':root[0].set('fechaConsulta','2025-01-01T00:00:00')
                if change=='sector':summary.getparent().getparent().getparent().set('codSector','2')
                if change=='state':summary.set('estadoActual','Cancelada')
                if change in ('quota','balance','initial','invalid'):
                    summary.set({'quota':'cuotaMes','balance':'saldoActual','initial':'valorInicial','invalid':'cuotaMes'}[change],
                        'NaN' if change=='invalid' else '6')
                if change=='summary_copy':summary.getparent().append(deepcopy(summary))
                if change=='detail_copy':root[0].append(deepcopy(detail))
                if change=='dto_copy':rows.append(deepcopy(row))
                r=conciliar_cuotas_hdc(f.evidence(rows),criterio_funcional=True,informe_xml=E.tostring(root))
                self.assertEqual(r.subtotal_reconocido.valor,Decimal(0))
                self.assertTrue(any(p.tratamiento=='PENDIENTE' for p in r.partidas))

    def test_closed_cab_ledger_reuses_capacity_without_creating_a_credit_decision(self):
        from copy import deepcopy
        from lxml import etree as E
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        from apps.xcore_consumo.services.credihoy_v2.unico.motor import calcular_preliminar_congente,aplicar_cuota_observada_preliminar
        from apps.xcore_consumo.services.credihoy_v2.unico.normativa import POLITICA_FINANCIERA_CONGENTE
        from apps.xcore_consumo.services.credihoy_v2.unico.dto import RecogidaCredihoyUnicoV2
        f,row,root,detail,summary=self.scenario()
        other=f.row(quota='656000');other['identificador']['numero']='OTRA-SINTETICA';other['posicion_original']=1
        dto=f.evidence([row,other]);before=deepcopy(dto.a_dict());xml=E.tostring(root)
        result=conciliar_cuotas_hdc(dto,criterio_funcional=True,informe_xml=xml)
        income=Hecho(valor=Decimal('2435509'),disponible=True,fuente='LINIX.AP014MCLIENTE',
            referencia_evidencia=POLITICA_FINANCIERA_CONGENTE,ruta_origen='adaptacionCongente.ingresoComputableAp014',
            estado_certificacion=EC.OBSERVADO)
        entry=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('400000'),plazo_meses=2,
            periodicidad_pago='MENSUAL',modalidad_codigo='111',recogida=RecogidaCredihoyUnicoV2(aplica=False))
        preliminary=aplicar_cuota_observada_preliminar(calcular_preliminar_congente(entry,income,result),
            Decimal('204448'),'REFERENCIA-SINTETICA-NO-ORACLE')
        self.assertEqual(result.subtotal_reconocido.valor,Decimal('661000'))
        self.assertEqual(preliminary['capacidad']['valor'],'961184')
        self.assertEqual(preliminary['reservas'],[])
        self.assertFalse(preliminary['autorizadoDecisionCrediticia'])
        self.assertFalse(preliminary['endeudamientoCompleto'])
        self.assertEqual(dto.a_dict(),before);self.assertEqual(E.tostring(root),xml)


class HdcReconciliationTests(TestCase):
    def test_functional_criteria_exclude_verified_issuer_and_service_not_card_payments(self):
        from copy import deepcopy
        from apps.integraciones.risk_gateway.tests.fixtures import codigo,importe
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        rows=[]
        for n,(entity,quota) in enumerate((('CONGENTE ANTES COOTRADEPMETA','825000'),
                ('CLARO SERV FIJ','59000'),('NU COLOMBIA CF','12000'),
                ('ORI DA VIVIEND ADM RAPPIPAY','40000'),('BANCO BOGOTA CARTERA TOTAL','5000'))):
            row=self.row(quota=quota);row['identificador']['numero']='SYNTHETIC-'+str(n)
            row['entidad']=entity;row['codSuscriptor']=None
            if n==1:
                row['sector']=codigo('4');row['valores'][0]['saldoActual']=importe('0','0')
            if n in (2,3):row['tipo_estructural']='TarjetaCredito';row['valores'][0]['periodicidad']=None
            if n==4:row['valores'][0]['periodicidad']=codigo('9')
            rows.append(row)
        result=conciliar_cuotas_hdc(self.evidence(rows),criterio_funcional=True)
        self.assertEqual([p.tratamiento for p in result.partidas],['EXCLUIDA','EXCLUIDA','INCLUIDA','INCLUIDA','PENDIENTE'])
        self.assertEqual(result.subtotal_reconocido.valor,Decimal('52000'))
        self.assertFalse(result.aprobacion_riesgos);self.assertFalse(result.cuotas_aplicables.disponible)
        duplicate=deepcopy(rows[2]);duplicate['posicion_original']=99
        same=conciliar_cuotas_hdc(self.evidence(rows+[duplicate]),criterio_funcional=True)
        self.assertEqual(same.subtotal_reconocido.valor,Decimal('52000'))

    def test_functional_card_needs_open_state_valid_money_and_no_conflicting_copy(self):
        from copy import deepcopy
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        row=self.row(quota='12000');row['tipo_estructural']='TarjetaCredito'
        for mutation in ('closed','currency','role','conflict'):
            a=deepcopy(row)
            if mutation=='closed':a['estadoPago']['codigo_original']='05';a['estadoCuenta']['codigo_original']='03';a['valores'][0]['saldoActual']['valor_monetario']='0'
            if mutation=='currency':a['valores'][0]['cuota']['estado_conversion']='SIN_CONVERSION'
            if mutation=='role':a['calidadDeudor']['codigo_original']='01'
            rows=[a]
            if mutation=='conflict':
                b=deepcopy(a);b['valores'][0]['cuota']['valor_monetario']='40000';rows.append(b)
            r=conciliar_cuotas_hdc(self.evidence(rows),criterio_funcional=True)
            self.assertFalse(any(p.tratamiento=='INCLUIDA' for p in r.partidas))

    def evidence(self, rows):
        from apps.integraciones.risk_gateway.tests.fixtures import hdc
        from apps.integraciones.risk_gateway.contratos import HDCNormalizado
        p=hdc();p['resultado']['obligaciones']=rows
        p['resultado']['atributos_informe_originales']=[['fechaConsulta',p['resultado']['fecha_consulta']]]
        p['resultado']['cobertura'].update(total_carteras=sum(r['tipo_estructural']=='CuentaCartera' for r in rows),
            total_tarjetas=sum(r['tipo_estructural']=='TarjetaCredito' for r in rows),total_ahorros=0,total_corrientes=0,total_otros=0)
        return HDCNormalizado.desde_payload(p)

    def row(self, state='01', quota='100', period='1'):
        from apps.integraciones.risk_gateway.tests.fixtures import obligacion,codigo,importe
        r=obligacion();r['codSuscriptor']='SYNTHETIC';r['calidadDeudor']=codigo('00')
        r['estadoPago']=codigo(state);r['estadoCuenta']=codigo('03' if state=='08' else '01')
        r['valores'][0].update(cuota=importe(quota,quota),saldoActual=importe('1000','1000'),periodicidad=codigo(period))
        return r

    def reconcile(self, rows):
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        return conciliar_cuotas_hdc(self.evidence(rows))

    def test_monthly_zero_and_closed_without_amount(self):
        r=self.row();r['valores'][0]['cuota']['moneda_homologada']='Moneda legal'
        self.assertEqual(self.reconcile([r]).cuotas_aplicables.valor,Decimal('100'))
        self.assertEqual(self.reconcile([self.row(quota='0')]).cuotas_aplicables.valor,Decimal('0'))
        closed=self.row('08');closed['valores']=[]
        self.assertEqual(self.reconcile([closed]).cuotas_aplicables.valor,Decimal('0'))

    def test_zero_balance_with_positive_installment_needs_reconciliation(self):
        from apps.integraciones.risk_gateway.tests.fixtures import importe
        r=self.row(quota='59000');r['valores'][0]['saldoActual']=importe('0','0')
        result=self.reconcile([r])
        self.assertIsNone(result.cuotas_aplicables.valor)
        self.assertEqual(result.partidas[0].motivo,'SALDO_CERO_Y_CUOTA_POSITIVA_SIN_APLICABILIDAD_CERTIFICADA')

    def test_zero_balance_and_quota_keep_antecedent_without_monthly_factor(self):
        from apps.integraciones.risk_gateway.tests.fixtures import importe
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        for period in ('1','2','3','4','5','6','9'):
            row=self.row(quota='0',period=period)
            row['valores'][0]['saldoActual']=importe('0','0')
            result=conciliar_cuotas_hdc(self.evidence([row]),criterio_funcional=True)
            self.assertTrue(result.partidas_resueltas)
            self.assertEqual(result.subtotal_reconocido.valor,Decimal('0'))
            self.assertEqual(result.partidas[0].periodicidad,period)
            self.assertIn('NO_CERTIFICA_CANCELACION',result.partidas[0].motivo)
            self.assertFalse(result.aprobacion_riesgos)

    def test_zero_does_not_override_identity_role_dates_or_conflicts(self):
        from copy import deepcopy
        from apps.integraciones.risk_gateway.tests.fixtures import importe
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        for change in ('role','future','unknown_period','identity','missing_money','special_state','conflict'):
            row=self.row(quota='0',period='6')
            row['valores'][0]['saldoActual']=importe('0','0')
            if change=='role':row['calidadDeudor']['codigo_original']='01'
            if change=='future':row['valores'][0]['fecha']='2099-01-01'
            if change=='unknown_period':row['valores'][0]['periodicidad']=None
            if change=='identity':row['identificador']['numero']=None
            if change=='missing_money':row['valores'][0]['saldoActual']=importe(None,None)
            if change=='special_state':row['estadoCuenta']['codigo_original']='03'
            rows=[row]
            if change=='conflict':
                other=deepcopy(row);other['posicion_original']=1
                other['valores'][0]['cuota']=importe('1','1');rows.append(other)
            result=conciliar_cuotas_hdc(self.evidence(rows),criterio_funcional=True)
            self.assertFalse(result.partidas_resueltas,change)

    def test_no_partial_total_for_other_period_or_card(self):
        from copy import deepcopy
        a=self.row();b=deepcopy(a);b['identificador']['numero']='SYNTHETIC-OTHER'
        b['valores'][0]['periodicidad']['codigo_original']='9'
        r=self.reconcile([a,b]);self.assertIsNone(r.cuotas_aplicables.valor)
        self.assertEqual(r.subtotal_reconocido.valor,Decimal('100'))
        b['tipo_estructural']='TarjetaCredito'
        self.assertIsNone(self.reconcile([b]).cuotas_aplicables.valor)

    def test_exact_duplicate_not_summed_and_conflict_not_resolved(self):
        from copy import deepcopy
        a=self.row();b=deepcopy(a);b['posicion_original']=1
        r=self.reconcile([a,b]);self.assertEqual(r.cuotas_aplicables.valor,Decimal('100'))
        self.assertEqual(r.partidas[1].motivo,'COPIA_EXACTA_YA_CONTABILIZADA')
        b['valores'][0]['cuota']['valor_monetario']='90'
        self.assertTrue(all(x.motivo=='DUPLICADO_CONFLICTIVO' for x in self.reconcile([a,b]).partidas))

    def test_multiple_values_no_latest_inference(self):
        from copy import deepcopy
        a=self.row();a['valores'].append(deepcopy(a['valores'][0]));a['valores'][1]['posicion_original']=1
        self.assertIsNone(self.reconcile([a]).cuotas_aplicables.valor)

    def test_null_conversion_future_date_and_role_stay_unknown(self):
        for change in ('null','future','role'):
            a=self.row()
            if change=='null':a['valores'][0]['cuota']['valor_monetario']=None
            if change=='future':a['valores'][0]['fecha']='2099-01-01'
            if change=='role':a['calidadDeudor']['codigo_original']='01'
            self.assertIsNone(self.reconcile([a]).cuotas_aplicables.valor)

    def test_conflicting_account_state_not_open_by_payment_alone(self):
        a=self.row();a['estadoCuenta']['codigo_original']='10'
        self.assertIsNone(self.reconcile([a]).cuotas_aplicables.valor)

    def test_anonymous_reader_serializes_dto_without_identifiers(self):
        import json
        from tools.credihoy_e2e_actual import anonymous_hdc_details
        encoded=json.dumps(anonymous_hdc_details(self.evidence([self.row()])))
        self.assertNotIn('NodoContrato',encoded)
        self.assertNotIn('SINTETICA-0',encoded)
        self.assertNotIn('SYNTHETIC',encoded)
        self.assertNotIn('datos_originales',encoded)
        self.assertIn('valor_monetario',encoded)


class EmbargosTests(TestCase):
    def read(self, body, coverage=(1,0),cutoff=date(2026,10,19)):
        return leer_embargos_persistidos(('<Informes><Informe>'+body+'</Informe></Informes>').encode(),
            referencia='SYNTHETIC',fecha_consulta=datetime(2026,10,19,tzinfo=timezone.utc),
            fecha_corte=cutoff,cobertura=coverage)

    def test_current_historical_and_complete_negative(self):
        for code,expected in [('06',True),('07',False),('01',False)]:
            r=self.read('<CuentaAhorro><Estado codigo="'+code+'"/></CuentaAhorro>')
            self.assertIs(r.cuentas_embargadas.valor,expected)
            self.assertEqual(r.cuentas_reactivadas_observadas,int(code=='07'))

    def test_missing_alert_not_absence_without_coverage(self):
        for xml,coverage in [('',(0,0)),('<CuentaAhorro/>',(1,0)),
                ('<CuentaAhorro><Estado codigo="01"/></CuentaAhorro>',(2,0))]:
            self.assertIsNone(self.read(xml,coverage).cuentas_embargadas.valor)

    def test_public_alert_open_count_not_double_counted(self):
        xml='<Alerta fuente="000001" codigo="002" descripcion="Tiene 2 embargos cerrados y 3 abiertos."/>'
        r=self.read(xml,(0,0))
        self.assertTrue(r.cuentas_embargadas.valor)
        self.assertEqual(r.embargos_abiertos_alerta.valor,3)

    def test_unstructured_and_zero_alert_do_not_certify_absence(self):
        for text in ('2026 embargo 3','Tiene 2 embargos cerrados y 0 abiertos.'):
            self.assertIsNone(self.read('<Alerta fuente="000001" codigo="002" descripcion="'+text+'"/>',(0,0)).cuentas_embargadas.valor)

    def test_multiple_reports_dtd_and_unattributed_date(self):
        raw=b'<Informes><Informe/><Informe/></Informes>'
        r=leer_embargos_persistidos(raw,referencia='SYNTHETIC',fecha_consulta=datetime(2026,10,19,tzinfo=timezone.utc),fecha_corte=None,cobertura=(0,0))
        self.assertIsNone(r.cuentas_embargadas.valor)
        with self.assertRaises(ValueError):
            leer_embargos_persistidos(b'<!DOCTYPE X [<!ENTITY x SYSTEM "file:///etc/passwd">]><Informes/>',referencia='SYNTHETIC',fecha_consulta=datetime.now(timezone.utc),fecha_corte=None,cobertura=(0,0))
        self.assertEqual(self.read('<CuentaAhorro><Estado codigo="06"/></CuentaAhorro>',cutoff=None).cuentas_embargadas.estado_certificacion,EC.OBSERVADO)


class LimiteAportesTests(TestCase):
    def test_certified_zero_is_available_and_fails_positive_amount(self):
        h=Hecho(valor=Decimal('0'),disponible=True,fuente='SYNTHETIC',ruta_origen='limite',
            referencia_evidencia='SYNTHETIC',estado_certificacion=EC.CERTIFICADO)
        entry=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('400000'),plazo_meses=2,
            periodicidad_pago='MENSUAL',modalidad_codigo='111')
        result=evaluar_reglas_unico_v2(entry,hechos=HechosReglasUnicoV2(limite_aportes_90=h))
        self.assertEqual(next(r for r in result.resultados if r.codigo=='UNICO_FILA_14').resultado.value,'NO_CUMPLE')

    def test_percentage_already_applied_not_reapplied(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.normativa import evidencia_monetaria
        h=Hecho(valor=Decimal('900'),disponible=True,fuente='SYNTHETIC',ruta_origen='limite',
            referencia_evidencia='SYNTHETIC',estado_certificacion=EC.CERTIFICADO)
        entry=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('850'),plazo_meses=2,periodicidad_pago='MENSUAL',modalidad_codigo='111')
        facts=HechosReglasUnicoV2(limite_aportes_90=h)
        r=evaluar_reglas_unico_v2(entry,hechos=facts)
        self.assertEqual(next(v for v in r.resultados if v.codigo=='UNICO_FILA_14').resultado.value,'CUMPLE')
        self.assertEqual(evidencia_monetaria(entry,facts)['UNICO_FILA_14']['limiteCOP'],'900')
        self.assertFalse(r.autorizado_decision_crediticia)


class ActualizacionCalendarioTests(TestCase):
    def evaluate(self,updated,cutoff):
        from apps.xcore_consumo.services.credihoy_v2.unico.reglas import estado_evaluacion_regla
        h=Hecho(valor=updated,disponible=updated is not None,fuente='AP014MCLIENTE.F_ULTACT',
            ruta_origen='actualizacion.fecha',referencia_evidencia='SYNTHETIC',
            estado_certificacion=EC.OBSERVADO if updated else EC.NO_DISPONIBLE)
        e=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('400000'),plazo_meses=2,periodicidad_pago='MENSUAL')
        r=evaluar_reglas_unico_v2(e,hechos=HechosReglasUnicoV2(fecha_actualizacion_datos=h),fecha_evaluacion=cutoff)
        return next(x for x in r.resultados if x.codigo=='UNICO_FILA_9')

    def test_authorized_case_and_inclusive_boundary(self):
        self.assertEqual(self.evaluate(date(2026,9,16),date(2026,10,19)).resultado.value,'NO_CUMPLE')
        self.assertEqual(self.evaluate(date(2026,9,16),date(2026,10,16)).resultado.value,'CUMPLE')
        r=self.evaluate(date(2026,9,16),date(2026,10,17))
        self.assertEqual(r.motivos,('Debes actualizar tus datos para continuar con tu solicitud.',))
        self.assertEqual(r.version_regla,'actualizacion-mes-calendario-v1')

    def test_end_of_month_leap_year_and_missing_dates(self):
        for updated,cutoff in [(date(2026,1,31),date(2026,2,28)),(date(2024,1,31),date(2024,2,29)),(date(2026,12,31),date(2027,1,31))]:
            self.assertEqual(self.evaluate(updated,cutoff).resultado.value,'CUMPLE')
        self.assertEqual(self.evaluate(None,date(2026,10,19)).resultado.value,'NO_EVALUABLE')
        self.assertEqual(self.evaluate(date(2026,10,20),date(2026,10,19)).resultado.value,'NO_EVALUABLE')


def financial_fixture():
    import sys
    from pathlib import Path
    from unittest.mock import Mock
    from django.utils import timezone as clock
    api=Path(__file__).resolve().parents[3]/'api_data_v1'
    sys.path.insert(0,str(api))
    try:
        from incluirtec.services.credihoy_financial_facts import read_financial_facts
        r=Mock()
        r.financial_client.return_value=[dict(K_IDTERC=1,V_SUELDO=Decimal('1000'),V_INGVAR=Decimal('20'),
            V_EGRVAR=Decimal('0'),V_DTONOM=Decimal('10'),F_ULTACT=date(2026,1,1),Q_CALIFI='A')]
        r.financial_third_party.return_value=[dict(K_IDTERC=1,I_NATURA='N',F_ULTACT=date(2026,1,1))]
        for n in ('monthly_arrears','arrears_history','registered_social_balances'):getattr(r,n).return_value=[]
        now=clock.now()
        return read_financial_facts(r,1,now,now.isoformat()),now
    finally:sys.path.remove(str(api))


class FinancialAdapterTests(TestCase):
    def test_congente_income_policy_requires_both_components_same_cutoff(self):
        from apps.xcore_consumo.services.credihoy_v2.financieros_http import adaptar_financieros
        from apps.xcore_consumo.services.credihoy_v2.unico.normativa import ingreso_computable_congente,POLITICA_FINANCIERA_CONGENTE
        raw,now=financial_fixture();f=adaptar_financieros(raw,consulta=now)
        f=replace(f,sueldo_basico_mensual=replace(f.sueldo_basico_mensual,valor=Decimal('2435509')),
            otros_ingresos_mensuales=replace(f.otros_ingresos_mensuales,valor=Decimal('0')))
        income=ingreso_computable_congente(f)
        self.assertEqual(income.valor,Decimal('2435509'))
        self.assertEqual(income.referencia_evidencia,POLITICA_FINANCIERA_CONGENTE)
        self.assertEqual(income.fecha_consulta,f.sueldo_basico_mensual.fecha_consulta)
        self.assertEqual(ingreso_computable_congente(replace(f,sueldo_basico_mensual=replace(f.sueldo_basico_mensual,valor=Decimal('0')))).valor,Decimal('0'))
        self.assertFalse(ingreso_computable_congente(replace(f,otros_ingresos_mensuales=Hecho(fuente='AP014MCLIENTE.V_INGVAR'))).disponible)
        self.assertFalse(ingreso_computable_congente(replace(f,otros_ingresos_mensuales=replace(f.otros_ingresos_mensuales,fecha_corte=date(2020,1,1)))).disponible)

    def test_preliminary_reuses_method_and_keeps_four_reservations(self):
        from apps.xcore_consumo.services.credihoy_v2.financieros_http import adaptar_financieros
        from apps.xcore_consumo.services.credihoy_v2.unico.normativa import ingreso_computable_congente
        from apps.xcore_consumo.services.credihoy_v2.unico.motor import calcular_preliminar_congente,evaluar_capacidad_unico_v2
        from apps.xcore_consumo.services.credihoy_v2.unico.conciliacion import conciliar_cuotas_hdc
        from apps.xcore_consumo.services.credihoy_v2.unico.dto import RecogidaCredihoyUnicoV2
        raw,now=financial_fixture();f=adaptar_financieros(raw,consulta=now)
        f=replace(f,sueldo_basico_mensual=replace(f.sueldo_basico_mensual,valor=Decimal('2435509')),
            otros_ingresos_mensuales=replace(f.otros_ingresos_mensuales,valor=Decimal('0')))
        income=ingreso_computable_congente(f)
        helper=HdcReconciliationTests();rows=[]
        from apps.integraciones.risk_gateway.tests.fixtures import importe
        for i,(quota,period,kind) in enumerate([('825000','1','CuentaCartera'),('41000','1','CuentaCartera'),
                ('236000','1','CuentaCartera'),('327000','1','CuentaCartera'),
                ('59000','1','CuentaCartera'),('12000',None,'TarjetaCredito'),
                ('40000',None,'TarjetaCredito'),('5000','9','CuentaCartera')]):
            r=helper.row(quota=quota,period=period or '0');r['identificador']['numero']='SYNTHETIC-'+str(i)
            r['posicion_original']=i;r['tipo_estructural']=kind
            if kind=='TarjetaCredito':r['valores'][0]['periodicidad']=None
            if quota=='59000':r['valores'][0]['saldoActual']=importe('0','0')
            rows.append(r)
        dto=helper.evidence(rows);c=conciliar_cuotas_hdc(dto)
        entry=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('400000'),plazo_meses=2,
            periodicidad_pago='MENSUAL',modalidad_codigo='111',recogida=RecogidaCredihoyUnicoV2(aplica=False))
        preliminary=calcular_preliminar_congente(entry,income,c)
        self.assertEqual(preliminary['capacidad']['valor'],'197632')
        self.assertEqual(preliminary['gastosFamiliares']['valor'],'608877')
        self.assertEqual(preliminary['cuotaParaCapacidad']['valor'],'200000')
        self.assertEqual(Decimal(preliminary['subtotalMensualHdcReconocido']['valor']),Decimal('1429000'))
        self.assertEqual(len(preliminary['reservas']),4)
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from tools import credihoy_e2e_actual as reader
        from apps.xcore_consumo.services.credihoy_v2.unico.motor import aplicar_cuota_observada_preliminar
        source=Path(reader.__file__).parents[1]/'apps/xcore_consumo/services/credihoy_v2/unico/conciliacion.py'
        prior=aplicar_cuota_observada_preliminar(preliminary,Decimal('204448'),'SYNTHETIC')
        with patch.dict(reader.__dict__,{'CONCILIACION_FUNCIONAL_RUNTIME':source.read_bytes()}),\
                patch('apps.xcore_consumo.services.credihoy_v2.unico.funcional.leer_estado_funcional',
                    return_value={'resultado':{'capacidadPreliminar':prior}}):
            checked=reader.revisar_criterio_funcional(dto,SimpleNamespace(monto_solicitado=Decimal('400000'),plazo_solicitado=2))
        self.assertEqual(checked['capacidadPreliminar']['capacidad']['valor'],'141184')
        self.assertFalse(checked['persistido'])
        self.assertFalse(preliminary['endeudamientoCompleto']);self.assertFalse(preliminary['autorizadoDecisionCrediticia'])
        official=evaluar_capacidad_unico_v2(entry,ingresos_mensuales=income,
            cuotas_deudas_hdc_brutas=c.subtotal_reconocido,cuotas_aplicables_explicitas=c.cuotas_aplicables)
        self.assertIsNone(official.capacidad_pago.valor)
        rule=next(r for r in evaluar_reglas_unico_v2(entry,hechos=HechosReglasUnicoV2(),capacidad=official).resultados if r.codigo=='UNICO_FILA_2')
        self.assertEqual(rule.resultado.value,'NO_EVALUABLE')
        from apps.integraciones.risk_gateway.operaciones.servicio import ResultadoOperacion
        from apps.xcore_consumo.services.credihoy_v2.unico.evaluacion_controlada import evaluar_respuestas_controladas
        from uuid import UUID,uuid4
        op=ResultadoOperacion(uuid4(),'HDC','COMPLETADA',UUID(dto['requestId']),UUID(dto['resultado']['consulta_id']),dto['versionContrato'],'',False,dto)
        result=evaluar_respuestas_controladas(entry,hdc=op,operaciones_esperadas={op.operacion_id:('HDC',op.request_id)},
            ingresos=income,hechos_reglas=HechosReglasUnicoV2(),fecha=now,request_id=str(uuid4()))
        # The new functional criterion recognizes both open card payments.
        self.assertEqual(result['capacidadPreliminar']['capacidad']['valor'],'145632')
        self.assertFalse(result['capacidadPreliminar']['aprobacionRiesgosEndeudamiento'])
        self.assertIsNone(result['capacidadMatematica']['valor']);self.assertEqual(result['decisionCrediticia'],'NO_AUTORIZADA')
        from apps.xcore_consumo.services.credihoy_v2.unico.pendientes import detalle_regla
        from apps.xcore_consumo.services.credihoy_v2.unico.inventario import inventario_reglas_unico_v2
        row=next(r for r in result['reglas'] if r['codigo']=='UNICO_FILA_2')
        explanation=detalle_regla({**row,'estado':'NO_EVALUABLE'},inventario_reglas_unico_v2()[0],'SYNTHETIC')
        self.assertLessEqual(len(explanation['accionPendiente']),500)

    def test_registered_income_not_automatically_computable(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.motor import evaluar_capacidad_unico_v2
        from apps.xcore_consumo.services.credihoy_v2.unico.dto import RecogidaCredihoyUnicoV2
        income=Hecho(valor=Decimal('2435509'),disponible=True,fuente='AP014MCLIENTE.V_SUELDO+V_INGVAR',
            ruta_origen='hechosFinancieros.ingresoMensualRegistrado',estado_certificacion=EC.OBSERVADO)
        amount=Hecho(valor=Decimal('0'),disponible=True,fuente='SYNTHETIC',estado_certificacion=EC.OBSERVADO)
        entry=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('400000'),plazo_meses=2,
            periodicidad_pago='MENSUAL',modalidad_codigo='111',recogida=RecogidaCredihoyUnicoV2(aplica=False))
        result=evaluar_capacidad_unico_v2(entry,ingresos_mensuales=income,cuotas_deudas_hdc_brutas=amount,
            cuotas_aplicables_explicitas=amount)
        self.assertFalse(result.ingresos_mensuales.disponible)
        self.assertIsNone(result.capacidad_pago.valor)
        self.assertEqual(income.valor,Decimal('2435509'))

    def test_contract_and_real_mappings(self):
        from apps.xcore_consumo.services.credihoy_v2.financieros_http import adaptar_financieros
        from apps.xcore_consumo.services.credihoy_v2.hechos import HechosInternosCrediHoyV2
        from apps.xcore_consumo.services.credihoy_v2.unico.proyeccion_interna import proyectar_reglas_internas
        raw,now=financial_fixture()
        f=adaptar_financieros(raw,consulta=now)
        self.assertEqual(f.ingreso_mensual_registrado.valor,Decimal('1020'))
        self.assertEqual(f.otros_descuentos_nomina.valor,Decimal('10'))
        r=proyectar_reglas_internas(HechosInternosCrediHoyV2(financieros=f),fecha=now)
        self.assertTrue(r.persona_natural.valor)
        self.assertEqual(r.calificacion_interna.valor,'A')
        self.assertIsNone(r.saldo_aportes_utilizable.valor)

    def test_altered_total_and_double_percentage_rejected(self):
        from apps.xcore_consumo.services.credihoy_v2.financieros_http import adaptar_financieros
        from apps.xcore_consumo.services.credihoy_v2.errores_http import ApiDataCrediHoyV2Error
        raw,now=financial_fixture();raw['ingresoMensualRegistrado']['valor']='999'
        with self.assertRaises(ApiDataCrediHoyV2Error):adaptar_financieros(raw,consulta=now)


from django.test import TransactionTestCase,override_settings


@override_settings(CREDIHOY_FUNCIONAL_ENABLED=True,CREDIHOY_CONSENTIMIENTO_REAL_ENABLED=True,
    CREDIHOY_CONSUMO_CONTROLADO_ENABLED=False,RISK_GATEWAY_ORCHESTRATION_ENABLED=False,
    ROOT_URLCONF='core.urls_firma',DEBUG=True)
class FinancialRevisionTests(TransactionTestCase):
    from tools.test_credihoy_normativa import RevisionNormativaTests as Setup
    setUp=Setup.setUp
    post=Setup.post
    setup_operation=Setup.setup_operation
    prepare=Setup.prepare

    def test_revision_preserves_old_evaluations_consent_and_consumption(self):
        from copy import deepcopy
        from unittest.mock import patch
        from contextlib import redirect_stdout
        import io,json,hashlib
        from django.core import signing
        from django.db import connection
        from django.utils import timezone as clock
        from apps.xcore_consumo.models import EvaluacionConsumo
        from apps.xcore_consumo.services.credihoy_v2.unico import funcional as f
        job=self.prepare()
        raw,now=financial_fixture()
        raw['aportes']['documentos']=[dict(tipo='203',numero='SYNTHETIC',cancelacion='N',vigencia=None,
            saldoRegistrado='0',fuente='DR041MGDOCUMEN',excluidoPorCancelacion=False)]
        self.s.refresh_from_db()
        before=deepcopy(EvaluacionConsumo.objects.get(solicitud=self.s).resultados)
        p={'solicitudId':str(self.s.pk),'requestId':'00000000-0000-4000-8000-000000000077',
            'emitida':clock.now().isoformat(),'documentoSha256':hashlib.sha256(self.s.solicitante.numero_identificacion.encode()).hexdigest(),
            'firma':f._firma(self.s).text_hash,'monto':str(self.s.monto_solicitado),'plazoMeses':self.s.plazo_solicitado,
            'resultadoAnteriorSha256':f._digest(f.leer_estado_funcional(self.s)['resultado'])}
        response={'solicitudId':str(self.s.pk),'requestId':p['requestId'],'soloSelect':True,'START':0,
            'peticionSha256':hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest(),
            'fechaConsulta':now.isoformat(),'fechaEvaluacion':now.isoformat(),'hechosFinancieros':raw}
        packet={'peticionFinanciera':p,'vinculo':signing.dumps(p,salt='credihoy-financieros-v1')}
        with patch.object(job,'FINANCIAL_SOURCES',{},create=True),patch.object(job,'FINANCIAL_PACKET',packet,create=True), \
                patch.object(job,'FINANCIAL_RESPONSE',response,create=True),patch.object(job,'FINANCIAL_SOURCE_HASH','a'*64,create=True), \
                patch.object(connection,'vendor','postgresql'),redirect_stdout(io.StringIO()):
            job.apply_financial_revision()
            job.apply_financial_revision()
        e=EvaluacionConsumo.objects.get(solicitud=self.s)
        self.assertEqual(len(e.resultados),len(before)+1)
        self.assertTrue(all(e.resultados[k]==v for k,v in before.items()))
        result=f.leer_estado_funcional(self.s)['resultado']
        states={r['codigo']:r['estadoEvaluacion'] for r in result['reglas']}
        self.assertEqual(states['UNICO_FILA_21'],'CUMPLE')
        self.assertEqual(states['UNICO_FILA_24'],'CUMPLE')
        self.assertTrue(result['ingresosAplicables']['disponible'])
        self.assertEqual(e.decision_final,'NO_AUTORIZADA')
        self.assertEqual(self.calls.call_count,1)
        baseline=deepcopy(e.resultados)
        c4=dict(estado='C4_RECONCILIADO',solicitudId=str(self.s.pk),requestId=p['requestId'],
            documentoSha256=p['documentoSha256'],actualiza=False,forcot='T',
            respuestaOrigenSha256=hashlib.sha256(json.dumps(response,sort_keys=True).encode()).hexdigest(),
            fechaCorte=response['fechaEvaluacion'],cancelacion='N',tipo='203',numero='SYNTHETIC',
            titularYDocumentoVerificados=True,saldoC4='474141',fechaConsulta=now.isoformat())
        with patch.object(job,'FINANCIAL_C4_RESPONSE',c4,create=True), \
                patch.object(job,'FINANCIAL_SOURCES',{},create=True),patch.object(job,'FINANCIAL_PACKET',packet,create=True), \
                patch.object(job,'FINANCIAL_RESPONSE',response,create=True),patch.object(job,'FINANCIAL_SOURCE_HASH','b'*64,create=True), \
                patch.object(job,'FINANCIAL_RECONCILE_EXISTING',True,create=True), \
                patch.object(connection,'vendor','postgresql'),redirect_stdout(io.StringIO()):
            job.apply_financial_revision()
            job.apply_financial_revision()
        e.refresh_from_db()
        self.assertEqual(len(e.resultados),len(baseline)+1)
        self.assertTrue(all(e.resultados[k]==v for k,v in baseline.items()))
        reconciled=f.leer_estado_funcional(self.s)['resultado']
        self.assertIn('conciliacionHdc',reconciled)
        self.assertTrue(reconciled['ingresosAplicables']['disponible'])
        aportes=next(r for r in reconciled['reglas'] if r['codigo']=='UNICO_FILA_14')
        self.assertIn(aportes['estadoEvaluacion'],{'CUMPLE','NO_CUMPLE'})
        self.assertEqual(aportes['evidencia']['limiteCOP'],'426726.90')
        self.assertEqual(e.decision_final,'NO_AUTORIZADA')
        self.assertEqual(self.calls.call_count,1)
        existing=deepcopy(e.resultados)
        baseline=deepcopy(f.leer_estado_funcional(self.s)['resultado'])
        with patch('django.core.signing.time.time',return_value=1):
            expired={**packet,'vinculo':signing.dumps(p,salt='credihoy-financieros-v1')}
        with patch.object(job,'FINANCIAL_C4_RESPONSE',c4,create=True), \
                patch.object(job,'FINANCIAL_SOURCES',{},create=True),patch.object(job,'FINANCIAL_PACKET',expired,create=True), \
                patch.object(job,'FINANCIAL_RESPONSE',response,create=True),patch.object(job,'FINANCIAL_SOURCE_HASH','c'*64,create=True), \
                patch.object(job,'FINANCIAL_RECONCILE_EXISTING',True,create=True), \
                patch.object(connection,'vendor','postgresql'),redirect_stdout(io.StringIO()):
            with self.assertRaises(signing.SignatureExpired):job.apply_financial_revision()
            with patch.object(job,'FINANCIAL_POLICY_REVIEW',True,create=True), \
                    patch.object(job,'FINANCIAL_REVIEW_BASELINE',baseline,create=True):
                job.apply_financial_revision()
        e.refresh_from_db()
        self.assertEqual(len(e.resultados),len(existing)+1)
        self.assertTrue(all(e.resultados[k]==v for k,v in existing.items()))
        self.assertEqual(e.decision_final,'NO_AUTORIZADA');self.assertEqual(self.calls.call_count,1)
