"""Synthetic pure-domain checks. No DB, HTTP, SOAP, OTP, C4 or persistence."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from unittest import TestCase
from unittest.mock import patch

from apps.xcore_consumo.services.credihoy_v2.base import Hecho, EstadoCertificacion as EC
from apps.xcore_consumo.services.credihoy_v2.unico.dto import EscenarioCapacidadDisponibleV2
from apps.xcore_consumo.services.credihoy_v2.unico.motor import normalizar_capacidad_disponible


class AvailableCapacityTests(TestCase):
    def setUp(self):
        for target in ('socket.create_connection','socket.getaddrinfo','requests.sessions.Session.request',
                       'django.db.backends.base.base.BaseDatabaseWrapper.cursor'):
            self.enterContext(patch(target,side_effect=AssertionError('SYNTHETIC_CAPACITY_NO_IO')))

    def fact(self, value, *, payroll=False):
        return Hecho(valor=Decimal(value),disponible=True,
            fuente='AP014MCLIENTE.V_DTONOM' if payroll else 'SYNTHETIC_SIMULADOR_PUBLICADO',
            referencia_evidencia='SYNTHETIC_ONLY',ruta_origen='SYNTHETIC.nomina' if payroll else 'SYNTHETIC.cuotaEstimada',
            fecha_corte=date(2026,11,2),fecha_consulta=datetime(2026,11,2,12,tzinfo=timezone.utc),
            estado_certificacion=EC.OBSERVADO)

    def preliminary(self, income='2435509', debt='317000', payroll='694840', quota='200000'):
        amounts=[Decimal(v) for v in (income,debt,payroll,quota)]
        i,d,p,q=amounts
        with localcontext() as ctx:
            ctx.prec=60
            expense=(i*Decimal('0.25')).to_integral_value(rounding='ROUND_DOWN')
            residual=i-d-p-expense-q
        money=lambda v:{'disponible':True,'valor':str(v)}
        source=self.fact(payroll,payroll=True).a_dict()
        return {'estado':'PRELIMINAR_NO_DECISORIA','autorizadoDecisionCrediticia':False,
            'aprobacionRiesgosEndeudamiento':False,'conciliacionPartidasCompleta':True,
            'ingresoComputable':money(i),'subtotalMensualHdcReconocido':money(d),
            'gastosFamiliares':money(expense),'cuotaParaCapacidad':money(q),'capacidad':money(residual),
            'deduccionesNomina':{**money(p),'procedencia':source,'agregadoAplicadoUnaVez':True,
                'componentesAplicadosAdicionalmente':False},
            'procedencia':{'ingreso':{'fecha_corte':source['fecha_corte'],'fecha_consulta':source['fecha_consulta']}},
            'tratamientoCuotasLinix':{'criterio':'HDC_EXCLUSIVO_SIN_SEGUNDA_SUMA_LINIX','importeAdicionalAplicado':'0'},
            'reservas':[]}

    def test_exact_available_before_quota_and_estimated_residual_are_separate(self):
        original=self.preliminary();before=deepcopy(original)
        result=normalizar_capacidad_disponible(original,cuota_estimada=self.fact('204793.29'))
        self.assertEqual(Decimal(result['disponibleAntesGastosFamiliares']['valor']),Decimal('1423669'))
        self.assertEqual(Decimal(result['capacidadDisponible']['valor']),Decimal('814791.75'))
        self.assertEqual(Decimal(result['gastosFamiliaresExactos']['valor']),Decimal('608877.25'))
        self.assertEqual(Decimal(result['residualPosteriorEstimado']['valor']),Decimal('609998.46'))
        self.assertEqual(result['capacidad']['valor'],'614792')
        self.assertEqual(result['antecedenteTrunc']['capacidad'],original['capacidad'])
        self.assertEqual(result['antecedenteTrunc']['gastosFamiliares']['valor'],'608877')
        self.assertFalse(result['aprobacionRiesgosEndeudamiento'])
        self.assertFalse(result['autorizadoDecisionCrediticia'])
        self.assertTrue(result['escenarioEstimadoNoContractual'])
        self.assertEqual(original,before)
        self.assertNotIn('cuotaContractual',result)

    def test_family_expense_does_not_change_the_pre_expense_available(self):
        result=normalizar_capacidad_disponible(self.preliminary('4000000','500000','300000','100000'),
            cuota_estimada=self.fact('90000'))
        self.assertEqual(Decimal(result['disponibleAntesGastosFamiliares']['valor']),Decimal('3200000'))
        self.assertEqual(Decimal(result['gastosFamiliaresExactos']['valor']),Decimal('1000000'))
        self.assertEqual(Decimal(result['capacidadDisponible']['valor']),Decimal('2200000'))
        self.assertEqual(Decimal(result['residualPosteriorEstimado']['valor']),Decimal('2110000'))

    def test_without_estimated_quota_available_is_still_known_but_estimate_is_not_inferred(self):
        result=normalizar_capacidad_disponible(self.preliminary())
        self.assertEqual(result['capacidadDisponible']['valor'],'814791.75')
        self.assertEqual(result['residualPosteriorEstimado'],{'disponible':False,'valor':None})
        self.assertEqual(result['cuotaNuevaEstimada'],{'disponible':False,'valor':None})

    def test_general_inputs_do_not_require_original_holder_amount_or_uuid(self):
        result=normalizar_capacidad_disponible(self.preliminary('4000000','500000','300000','100000'),
            cuota_estimada=self.fact('90000'))
        self.assertEqual(Decimal(result['capacidadDisponible']['valor']),Decimal('2200000'))
        self.assertEqual(Decimal(result['residualPosteriorEstimado']['valor']),Decimal('2110000'))
        self.assertNotIn('solicitudId',result)

    def test_idempotent_and_quota_change_does_not_change_available_or_source(self):
        first=normalizar_capacidad_disponible(self.preliminary(),cuota_estimada=self.fact('204793.29'))
        self.assertEqual(first,normalizar_capacidad_disponible(first,cuota_estimada=self.fact('204793.29')))
        second=normalizar_capacidad_disponible(first,cuota_estimada=self.fact('300000'))
        self.assertEqual(first['capacidadDisponible'],second['capacidadDisponible'])
        self.assertEqual(first['antecedenteTrunc'],second['antecedenteTrunc'])
        self.assertEqual(second['residualPosteriorEstimado']['valor'],'514791.75')

    def test_negative_and_zero_results_do_not_approve_or_reject(self):
        for payroll,expected in (('750000','0.00'),('800000','-50000.00')):
            with self.subTest(payroll=payroll):
                result=normalizar_capacidad_disponible(self.preliminary('1000000','0',payroll,'100000'),
                    cuota_estimada=self.fact('100000'))
                self.assertEqual(result['capacidadDisponible']['valor'],expected)
                self.assertFalse(result['autorizadoDecisionCrediticia'])
                self.assertEqual(result['estado'],'PRELIMINAR_NO_DECISORIA')

    def test_missing_payroll_and_double_application_are_blocked(self):
        for name,value in (('agregadoAplicadoUnaVez',False),('componentesAplicadosAdicionalmente',True)):
            changed=self.preliminary();changed['deduccionesNomina'][name]=value
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'NOMINA_REGISTRADA_NO_ACREDITADA'):
                normalizar_capacidad_disponible(changed)
        changed=self.preliminary();del changed['deduccionesNomina']
        with self.assertRaisesRegex(ValueError,'OPERANDO_CAPACIDAD_DISPONIBLE_INVALIDO'):
            normalizar_capacidad_disponible(changed)

    def test_tampered_origin_and_dates_are_blocked(self):
        for name,value in (('valor','0'),('fuente','UNTRUSTED'),('fecha_corte','2026-11-01'),
                           ('estado_certificacion','NO_CERTIFICADO')):
            changed=self.preliminary();changed['deduccionesNomina']['procedencia'][name]=value
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'NOMINA_REGISTRADA_NO_ACREDITADA'):
                normalizar_capacidad_disponible(changed)

    def test_nonfinite_negative_and_unavailable_operands_are_blocked(self):
        for name in ('ingresoComputable','subtotalMensualHdcReconocido','gastosFamiliares','cuotaParaCapacidad','deduccionesNomina'):
            for bad in ('NaN','Infinity','-1','not-money'):
                changed=self.preliminary();changed[name]['valor']=bad
                with self.subTest(name=name,bad=bad),self.assertRaisesRegex(ValueError,'OPERANDO_CAPACIDAD_DISPONIBLE_INVALIDO'):
                    normalizar_capacidad_disponible(changed)

    def test_old_residual_cannot_hide_an_extra_internal_quota_or_second_payroll(self):
        changed=self.preliminary();changed['capacidad']['valor']='-210560'
        with self.assertRaisesRegex(ValueError,'ANTECEDENTE_TRUNC_NO_CONCILIA'):
            normalizar_capacidad_disponible(changed)
        changed=self.preliminary();changed['tratamientoCuotasLinix']['importeAdicionalAplicado']='825352'
        with self.assertRaisesRegex(ValueError,'METODOLOGIA_CAPACIDAD_NO_COMPATIBLE'):
            normalizar_capacidad_disponible(changed)

    def test_does_not_change_risk_approval_or_accept_already_authorized_packet(self):
        for name in ('autorizadoDecisionCrediticia','aprobacionRiesgosEndeudamiento'):
            changed=self.preliminary();changed[name]=True
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'METODOLOGIA_CAPACIDAD_NO_COMPATIBLE'):
                normalizar_capacidad_disponible(changed)

    def test_estimate_needs_real_typed_source_not_an_arbitrary_number(self):
        for invalid in (Decimal('204793.29'),replace(self.fact('204793.29'),referencia_evidencia=None),
                        self.fact('0'),self.fact('-1')):
            with self.subTest(invalid=invalid),self.assertRaisesRegex(ValueError,'CUOTA_ESTIMADA_NO_ACREDITADA'):
                normalizar_capacidad_disponible(self.preliminary(),cuota_estimada=invalid)

    def test_exact_under_low_decimal_precision(self):
        original=self.preliminary()
        with localcontext() as ctx:
            ctx.prec=4
            result=normalizar_capacidad_disponible(original,cuota_estimada=self.fact('204793.29'))
        self.assertEqual(result['capacidadDisponible']['valor'],'814791.75')
        self.assertEqual(result['residualPosteriorEstimado']['valor'],'609998.46')

    def test_dto_rejects_inconsistent_available_residual_and_truncated_expense(self):
        scenario=EscenarioCapacidadDisponibleV2(ingresos=Decimal('2435509'),cuotas_hdc=Decimal('317000'),
            deducciones_nomina=Decimal('694840'),gastos_familiares_exactos=Decimal('608877.25'),
            capacidad_disponible=Decimal('814791.75'),cuota_nueva_estimada=Decimal('204793.29'),
            residual_posterior_estimado=Decimal('609998.46'))
        for change in ({'capacidad_disponible':Decimal('609998.46')},
                       {'residual_posterior_estimado':Decimal('814791.75')},
                       {'gastos_familiares_exactos':Decimal('608877')},
                       {'cuota_nueva_estimada':None}):
            with self.subTest(change=change),self.assertRaises(ValueError):replace(scenario,**change)
        self.assertFalse(scenario.a_dict()['aprobacion_riesgos'])

    def test_controlled_evaluator_uses_native_normalizer_without_changing_decision(self):
        from uuid import UUID
        from apps.integraciones.risk_gateway.tests.fixtures import hdc,obligacion,codigo,importe
        from apps.integraciones.risk_gateway.contratos import HDCNormalizado
        from apps.integraciones.risk_gateway.operaciones.servicio import ResultadoOperacion
        from apps.xcore_consumo.services.credihoy_v2.unico.dto import EntradaCredihoyUnicoV2,RecogidaCredihoyUnicoV2
        from apps.xcore_consumo.services.credihoy_v2.unico.reglas import HechosReglasUnicoV2
        from apps.xcore_consumo.services.credihoy_v2.unico.normativa import POLITICA_FINANCIERA_CONGENTE
        from apps.xcore_consumo.services.credihoy_v2.unico.evaluacion_controlada import evaluar_respuestas_controladas
        row=obligacion();row.update(codSuscriptor='SYNTHETIC',calidadDeudor=codigo('00'),
            estadoPago=codigo('01'),estadoCuenta=codigo('01'))
        row['valores'][0].update(cuota=importe('317000','317000'),saldoActual=importe('1000','1000'),periodicidad=codigo('1'))
        payload=hdc();payload['resultado']['obligaciones']=[row]
        payload['resultado']['cobertura'].update(total_carteras=1,total_tarjetas=0,total_ahorros=0,total_corrientes=0,total_otros=0)
        dto=HDCNormalizado.desde_payload(payload);rid=UUID(dto['requestId'])
        op=ResultadoOperacion(UUID('22222222-2222-4222-8222-222222222222'),'HDC','COMPLETADA',
            rid,UUID(dto['resultado']['consulta_id']),dto['versionContrato'],'',False,dto)
        entry=EntradaCredihoyUnicoV2(monto_solicitado=Decimal('400000'),plazo_meses=2,
            periodicidad_pago='MENSUAL',modalidad_codigo='111',recogida=RecogidaCredihoyUnicoV2(aplica=False))
        payroll=self.fact('694840',payroll=True)
        income=Hecho(valor=Decimal('2435509'),disponible=True,fuente='LINIX.AP014MCLIENTE',
            referencia_evidencia=POLITICA_FINANCIERA_CONGENTE,ruta_origen='adaptacionCongente.ingresoComputableAp014',
            estado_certificacion=EC.OBSERVADO,fecha_corte=payroll.fecha_corte,fecha_consulta=payroll.fecha_consulta)
        context=dict(hdc=op,operaciones_esperadas={op.operacion_id:('HDC',rid)},ingresos=income,
            hechos_reglas=HechosReglasUnicoV2(),fecha=payroll.fecha_consulta,request_id=str(rid))
        result=evaluar_respuestas_controladas(entry,**context,descuentos_nomina=payroll,cuota_estimada=self.fact('204793.29'))
        self.assertEqual(result['capacidadDisponible']['valor'],'814791.75')
        self.assertEqual(result['residualPosteriorEstimado']['valor'],'609998.46')
        self.assertEqual(result['decisionCrediticia'],'NO_AUTORIZADA')
        self.assertFalse(result['conciliacionHdc']['aprobacion_riesgos'])
        self.assertEqual(result['estadoTramite'],'PENDIENTE_REVISION')
        self.assertEqual(len(result['reglas']),16)
        self.assertEqual(result['capacidadPreliminar']['capacidadDisponible'],result['capacidadDisponible'])
        result['capacidadDisponible']['valor']='tampered'
        self.assertEqual(result['capacidadPreliminar']['capacidadDisponible']['valor'],'814791.75')
