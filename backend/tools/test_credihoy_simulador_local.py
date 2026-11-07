"""Synthetic local simulation, real owner/relay/public projection, no providers."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory
from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import SolicitudConsumo, EvaluacionConsumo, ConsentimientoConsumo
from apps.xcore_consumo.tasas_models import (PerfilTasasCredihoy, TasaComercialCredihoy,
    AsignacionTarifaCredihoy, EventoTasasCredihoy, SeleccionTarifaCredihoy)
from apps.xcore_consumo.public_access_models import AccesoPublicoCredihoy as Acceso
from apps.xcore_consumo.services.otp_crypto import encrypt_text
from apps.xcore_consumo.services.credihoy_v2.base import Hecho, EstadoCertificacion as EC
from apps.xcore_consumo.services.credihoy_v2.pagaduria import FUENTE, adaptar_pagaduria
from apps.xcore_consumo.services.credihoy_v2.unico import (simulacion_lectura as sim,
    publico as access, publico_controles as controls, publico_privacidad as privacy,
    publico_institucional as wire, publico_seguimiento as owner, funcional)
from apps.xcore_consumo.services.credihoy_v2.unico.condicion_comercial import resolver_condicion

LIMITS={'version':'SYNTHETIC_ONLY','referencia':'NOT_REAL_POLICY','modalidad':'111',
    'montoMinimo':'400000','montoMaximo':'10000000','cuotasMinimas':1,'cuotasMaximas':48}
BASE='/api/v1/credihoy/publico/'


class OwnQuoteTests(TransactionTestCase):
    def setUp(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import cotizacion
        self.q=cotizacion
        h=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='0000000099',
            primer_apellido='SYNTHETIC',celular='0000000000',email='test@example.test',fecha_expedicion='2000-01-01')
        self.s=Solicitud.objects.create(solicitante=h,producto=wire.PRODUCTO,metadatos={'modo':'AUTOGESTION_ENVIADA'})
        self.detail=SolicitudConsumo.objects.create(solicitud=self.s,orchestration_data={'conservar':'original'})
        self.ref=str(uuid4())
        self.context={'solicitudId':str(self.s.pk),'versionCondiciones':'a'*64,'condiciones':{
            'producto':'390','modalidad':'111','destinacion':'CFA','monto':'800000','numeroCuotas':6,
            'tasaNominalNamv':'25.410000','periodicidad':'MENSUAL'}}
        self.identity={'numeroDocumento':'0000000099','_sesionReferencia':str(uuid4()),
            '_sesionExpiraAt':(timezone.now()+timedelta(minutes=10)).isoformat()}
        self.study={'referenciaVersion':self.ref,'decisionPolitica':{'continuidad':{'estudioFavorable':True}}}
        self.actor=get_user_model().objects.create(username='svc_synthetic')
        self.real_obtener=self.q._obtener
        def context(s,**kw):
            query=SolicitudConsumo.objects.select_for_update() if kw.get('lock',True) else SolicitudConsumo.objects
            return query.get(solicitud=s),deepcopy(self.context)
        for mock in (patch.object(self.q,'_obtener',return_value=self.s),patch.object(self.q,'_contexto',side_effect=context),
                patch.object(funcional,'leer_estado_funcional',return_value={'revisionEstudio':{'versionReferencia':self.ref}}),
                patch('apps.xcore_consumo.services.credihoy_v2.unico.autoservicio._estudio_publico_personal',side_effect=lambda *a:self.study),
                patch('requests.sessions.Session.request',side_effect=AssertionError('NETWORK_FORBIDDEN'))):
            mock.start();self.addCleanup(mock.stop)
        settings=self.settings(CREDIHOY_COTIZACION_MOTOR='PROPIO_390_111_CFA')
        settings.enable();self.addCleanup(settings.disable)

    def calculate(self):return self.q.calcular_simulacion_propia(self.s.pk,self.actor)

    def operate(self,**extra):
        return self.q._operacion_titular(self.identity,{'solicitudId':str(self.s.pk),'operacion':'consultar',**extra},str(uuid4()))

    def test_calculation_persistence_idempotency_and_read_projection(self):
        first=self.calculate();second=self.calculate()
        self.assertEqual(first,second);self.assertEqual(first['cuota'],'143822')
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_SIMULACION_PROPIA_CALCULADA').count(),1)
        p=wire.project_quote(self.operate());self.assertEqual(p['simulacion'],first)
        self.assertEqual(p['aceptacionSimulacion'],{'aceptada':False,'fecha':None})
        self.detail.refresh_from_db();self.assertEqual(self.detail.orchestration_data['conservar'],'original')

    def test_personal_acceptance_is_distinct_idempotent_and_no_financial_authority(self):
        sim=self.calculate()
        args={'operacion':'aceptar_simulacion','simulacionSha256':sim['sha256'],'acepta':True}
        accepted=self.operate(**args);again=self.operate(**args)
        self.assertEqual(accepted,again);self.assertTrue(wire.project_quote(accepted)['aceptacionSimulacion']['aceptada'])
        self.assertFalse(accepted['aceptacion']['aceptada']);self.assertFalse(accepted['autorizaStart'])
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_SIMULACION_ACEPTADA_TITULAR').count(),1)
        self.detail.refresh_from_db()
        receipt=self.detail.orchestration_data[self.q.KEY]['aceptacionesSimulacion'][sim['sha256']]
        self.assertEqual(receipt['sesionReferencia'],self.identity['_sesionReferencia']);self.assertFalse(receipt['firmaContractual'])

    def test_accepted_simulation_survives_display_ttl_without_renewal_or_new_acceptance(self):
        from dataclasses import fields
        from apps.xcore_consumo.services.credihoy_v2.unico.elegibilidad import ContextoElegibilidad
        sim=self.calculate()
        first=self.operate(operacion='aceptar_simulacion',simulacionSha256=sim['sha256'],acepta=True)
        future=timezone.now()+timedelta(hours=2)
        args={f.name:None for f in fields(ContextoElegibilidad)}
        args.update(solicitud_id=str(self.s.pk),titular_referencia=sha256(self.identity['numeroDocumento'].encode()).hexdigest())
        with patch.object(self.q.timezone,'now',return_value=future):
            repeated=self.operate(operacion='aceptar_simulacion',simulacionSha256=sim['sha256'],acepta=True)
            self.assertEqual(first,repeated)
            self.assertTrue(wire.project_quote(repeated)['aceptacionSimulacion']['aceptada'])
            linked=self.q.aplicar_simulacion_aceptada_a_contexto(ContextoElegibilidad(**args),self.s)
        self.assertEqual(linked.cuota_contractual,sim['cuota'])
        self.assertEqual(repeated['simulacion']['vigenteHasta'],sim['vigenteHasta'])
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_SIMULACION_ACEPTADA_TITULAR').count(),1)
        self.context['versionCondiciones']='b'*64
        with self.assertRaises(ValueError):self.operate()

    def test_expired_unaccepted_simulation_cannot_be_accepted_backdated_or_extended(self):
        sim=self.calculate()
        with patch.object(self.q.timezone,'now',return_value=timezone.now()+timedelta(hours=2)):
            # The independent document validator uses its own clock.
            with patch.object(self.q,'_simulacion_persistida_para_continuidad',wraps=self.q._simulacion_persistida_para_continuidad):
                self.detail.refresh_from_db()
                entry=self.detail.orchestration_data[self.q.KEY]
                late={'solicitudId':str(self.s.pk),'simulacionSha256':sim['sha256'],
                    'versionCondiciones':sim['versionCondiciones'],'revisionReferencia':sim['revisionReferencia'],
                    'titularReferencia':entry['titularReferencia'],'sesionReferencia':self.identity['_sesionReferencia'],
                    'fecha':(timezone.now()).isoformat(),'requestId':str(uuid4()),'acepta':True,
                    'textoVersion':'credihoy-aceptacion-simulacion-v1','firmaContractual':False,
                    'autorizaStart':False,'autorizaDesembolso':False}
                late['sha256']=self.q._huella_simulacion(late)
                with self.assertRaises(ValueError):self.q._validar_aceptacion_simulacion(sim,late,entry['titularReferencia'])
        self.assertEqual(AuditLog.objects.filter(event='CREDIHOY_SIMULACION_ACEPTADA_TITULAR').count(),0)

    def test_intake_evidence_requires_matching_personal_audit_and_current_revision(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import elegibilidad
        sim=self.calculate()
        self.operate(operacion='aceptar_simulacion',simulacionSha256=sim['sha256'],acepta=True)
        decision={'operacionId':777,'sha256':'d'*64,'revisionReferencia':self.ref,
            'resultado':{'estado':'REQUIERE_CONSEJO_ADMINISTRACION'}}
        with patch.object(elegibilidad,'leer_decision_estudio',return_value=decision):
            proof=self.q.comprobante_simulacion_aceptada(self.s,self.identity)
            self.assertEqual(proof['cuota'],sim['cuota']);self.assertFalse(proof['autorizaStart'])
            self.assertEqual(proof['clasificacionRegistrada'],'REQUIERE_CONSEJO_ADMINISTRACION')
            event=AuditLog.objects.get(pk=proof['aceptacionAuditoriaId'])
            self.assertEqual(event.event,'CREDIHOY_SIMULACION_ACEPTADA_TITULAR')
            event.actor='titular:'+str(uuid4());event.save(update_fields=['actor'])
            with self.assertRaisesRegex(ValueError,'ACEPTACION_PERSONAL_SIN_AUDITORIA_VINCULADA'):
                self.q.comprobante_simulacion_aceptada(self.s,self.identity)
        with self.assertRaisesRegex(ValueError,'ACEPTACION_DE_OTRO_TITULAR'):
            self.q.comprobante_simulacion_aceptada(self.s,{**self.identity,'numeroDocumento':'0000000088'})

    def test_reject_cross_holder_changed_revision_changed_catalog_and_payload_injection(self):
        sim=self.calculate()
        self.identity['numeroDocumento']='0000000088'
        with self.assertRaises(ValueError):self.operate()
        self.identity['numeroDocumento']='0000000099';self.study['referenciaVersion']=str(uuid4())
        with self.assertRaises(ValueError):self.operate()
        self.study['referenciaVersion']=self.ref;self.context['versionCondiciones']='b'*64
        with self.assertRaises(ValueError):self.operate()
        self.context['versionCondiciones']='a'*64
        for args in ({'monto':'1'},{'operacion':'aceptar_simulacion','simulacionSha256':'f'*64,'acepta':True},
                {'operacion':'aceptar_simulacion','simulacionSha256':sim['sha256'],'acepta':False}):
            with self.assertRaises(ValueError):self.operate(**args)
        self.assertFalse(AuditLog.objects.filter(event='CREDIHOY_SIMULACION_ACEPTADA_TITULAR').exists())

    def test_tamper_expiry_and_non_favorable_study_fail_closed(self):
        sim=self.calculate()
        for change in (lambda p:p.update(cuota='1'),lambda p:p['amortizacion'][0].update(capital='1'),
                lambda p:p.update(autorizaStart=True),lambda p:p.update(vigenteHasta=p['calculadoEn'])):
            bad=deepcopy(sim);change(bad);bad['sha256']=self.q._huella_simulacion({k:v for k,v in bad.items() if k!='sha256'})
            with self.assertRaises(ValueError):wire.validar_simulacion_propia(bad,str(self.s.pk))
        with self.assertRaises(ValueError):wire.validar_simulacion_propia(sim,str(self.s.pk),ahora=timezone.now()+timedelta(hours=2))
        self.study['decisionPolitica']['continuidad']['estudioFavorable']=False
        with self.assertRaises(ValueError):self.operate()

    def test_postgresql_role_guard_allows_only_personal_append(self):
        import sys
        from pathlib import Path
        from django.db import connection,transaction,DatabaseError
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL required')
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'api_data_v1/tools'))
        from credihoy_oa01_ensamblaje import own_quote_guard_sql
        sim=self.calculate()
        with connection.cursor() as c:
            c.execute("DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='credihoy_titular_cotizacion') THEN CREATE ROLE credihoy_titular_cotizacion NOLOGIN; END IF; END $$")
            c.execute('GRANT USAGE ON SCHEMA public TO credihoy_titular_cotizacion')
            c.execute('GRANT SELECT,UPDATE ON public.xcore_consumo_solicitudconsumo TO credihoy_titular_cotizacion')
            c.execute('GRANT SELECT ON public.usuarios_solicitante,public.solicitudes_solicitud TO credihoy_titular_cotizacion')
            c.execute('GRANT INSERT,SELECT ON public.utils_auditlog TO credihoy_titular_cotizacion')
            c.execute('GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO credihoy_titular_cotizacion')
            c.execute(own_quote_guard_sql())
        try:
            with transaction.atomic():
                with connection.cursor() as c:c.execute('SET LOCAL ROLE credihoy_titular_cotizacion')
                accepted=self.operate(operacion='aceptar_simulacion',simulacionSha256=sim['sha256'],acepta=True)
                self.assertTrue(accepted['aceptacionSimulacion']['aceptada'])
            for mutation in ('drop','rewrite','form'):
                with self.assertRaises(DatabaseError),transaction.atomic():
                    with connection.cursor() as c:c.execute('SET LOCAL ROLE credihoy_titular_cotizacion')
                    detail=SolicitudConsumo.objects.get(solicitud=self.s)
                    data=deepcopy(detail.orchestration_data)
                    if mutation=='drop':data.pop(self.q.KEY)
                    elif mutation=='rewrite':data[self.q.KEY]['simulacion']['cuota']='1'
                    else:data['conservar']='changed'
                    detail.orchestration_data=data;detail.save(update_fields=['orchestration_data'])
        finally:
            with connection.cursor() as c:
                c.execute('DROP TRIGGER credihoy_simulacion_aceptacion_guard ON public.xcore_consumo_solicitudconsumo')
                c.execute('DROP FUNCTION public.credihoy_simulacion_aceptacion_guard()')

    def test_existing_intake_context_receives_quota_not_contractual_acceptance(self):
        from dataclasses import fields
        from apps.xcore_consumo.services.credihoy_v2.unico.elegibilidad import ContextoElegibilidad
        args={f.name:None for f in fields(ContextoElegibilidad)}
        args.update(solicitud_id=str(self.s.pk),titular_referencia=sha256(self.identity['numeroDocumento'].encode()).hexdigest())
        context=ContextoElegibilidad(**args);sim=self.calculate()
        with self.assertRaises(ValueError):self.q.aplicar_simulacion_aceptada_a_contexto(context,self.s)
        self.operate(operacion='aceptar_simulacion',simulacionSha256=sim['sha256'],acepta=True)
        linked=self.q.aplicar_simulacion_aceptada_a_contexto(context,self.s)
        self.assertEqual(linked.cuota_contractual,sim['cuota']);self.assertIsNone(linked.oferta_referencia)
        self.assertIsNone(linked.aceptacion_referencia);self.assertIsNone(linked.decision_autorizada)

    def test_real_holder_scope_and_expiration_checks_are_preserved(self):
        from apps.xcore_consumo.services.credihoy_v2.unico import autoservicio
        with override_settings(CREDIHOY_COTIZACION_TITULAR_ENABLED=True,CREDIHOY_COTIZACION_ENABLED=True,
                CREDIHOY_TITULAR_FUENTE_ENABLED=True,CREDIHOY_SEGUIMIENTO_DB_ALIAS='default',
                CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE='SYNTHETIC',CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[str(self.s.pk)]}),\
                patch.object(autoservicio,'validar_vinculo_consumo'):
            self.assertEqual(self.real_obtener(self.s.pk,None,titular=self.identity).pk,self.s.pk)
            with self.assertRaises(Http404):self.real_obtener(uuid4(),None,titular=self.identity)
            with self.assertRaises(Http404):self.real_obtener(self.s.pk,None,titular={**self.identity,'numeroDocumento':'0000000088'})
            with self.assertRaises(wire.NoDisponible):self.real_obtener(self.s.pk,None,titular={**self.identity,
                '_sesionExpiraAt':(timezone.now()-timedelta(seconds=1)).isoformat()})


class DecimalPlanTests(SimpleTestCase):
    def plan(self,amount='800000',term=6,rate='25.41',**changes):
        from apps.xcore_consumo.services.calculadora import calcular_plan_credihoy
        args={'perfil':('390','111','CFA'),'periodicidad_meses':1,
            'seguro_mensual_porcentaje':Decimal('0.090'),'base_seguro':'SALDO_INICIAL_PERIODO',
            'factor_adicional':Decimal(0),'unidad_redondeo':Decimal(1)}
        return calcular_plan_credihoy(Decimal(amount),term,Decimal(rate),**{**args,**changes})

    def test_two_historical_observations_match_model_without_claiming_general_parity(self):
        for amount,term,rate,payment in [('800000',6,'25.41','143822'),('400000',2,'16.67','204448')]:
            plan=self.plan(amount,term,rate)
            self.assertEqual(plan['cuotaCalculada'],payment)
            self.assertFalse(plan['paridadGeneralLinixAcreditada']);self.assertFalse(plan['ofertaContractual'])
            self.assertFalse(plan['fechasContractualesAcreditadas'])

    def test_schedule_conserves_capital_and_each_period_without_duplicate_insurance(self):
        for term in (1,2,6,12,36,48):
            plan=self.plan(term=term);rows=plan['amortizacion'];balance=Decimal('800000')
            totals={k:Decimal(0) for k in ('capital','intereses','seguro','cuota')}
            for row in rows:
                self.assertEqual(Decimal(row['saldoInicial']),balance)
                for key in totals:totals[key]+=Decimal(row[key])
                self.assertEqual(sum(Decimal(row[k]) for k in ('capital','intereses','seguro','otros')),Decimal(row['cuota']))
                balance-=Decimal(row['capital']);self.assertEqual(balance,Decimal(row['saldoFinal']))
                self.assertGreaterEqual(balance,0)
            self.assertEqual(balance,0);self.assertEqual(totals['capital'],Decimal('800000'))
            self.assertEqual(totals['cuota'],sum(totals[k] for k in ('capital','intereses','seguro')))

    def test_insurance_basis_is_explicit_and_not_inferred_from_examples(self):
        variable=self.plan();fixed=self.plan(base_seguro='CAPITAL_INICIAL')
        self.assertNotEqual(variable['cuotaCalculada'],fixed['cuotaCalculada'])
        self.assertEqual(len({r['seguro'] for r in fixed['amortizacion']}),1)
        self.assertGreater(len({r['seguro'] for r in variable['amortizacion']}),1)

    def test_historical_examples_are_not_rate_configuration(self):
        payments=[Decimal(self.plan(rate=rate)['cuotaSinRedondear']) for rate in ('10.123456','18.234567','29.345678')]
        self.assertTrue(payments[0]<payments[1]<payments[2])

    def test_user_dbms_output_matches_both_rows_including_separately_rounded_ic_iv(self):
        plan=self.plan('400000',2,'16.67',tasa_fija_mensual_porcentaje=Decimal('.277833'),
            tasa_variable_mensual_porcentaje=Decimal('1.111332'))
        expected=[{'capital':'198532','IC':'1111','IV':'4445','seguro':'360','cuota':'204448','saldoFinal':'201468'},
            {'capital':'201468','IC':'560','IV':'2239','seguro':'181','cuota':'204448','saldoFinal':'0'}]
        for row,observed in zip(plan['amortizacion'],expected):
            self.assertEqual({k:Decimal(row[k]) for k in observed},{k:Decimal(v) for k,v in observed.items()})
        self.assertEqual(plan['totales'],{'capital':'400000','intereses':'8355','seguro':'541','cuotas':'408896'})
        self.assertEqual(plan['cuotaCalculada'],'204448')
        self.assertEqual(Decimal(plan['desgloseTasaMensual']['namvResultante']),Decimal('16.669980'))
        self.assertEqual(plan['tasaNamvPorcentaje'],'16.67')
        self.assertFalse(plan['paridadGeneralLinixAcreditada'])

    def test_decimal_precision_zero_rate_cents_and_last_installment_adjustment(self):
        from decimal import localcontext
        expected=self.plan(unidad_redondeo=Decimal('.01'))
        with localcontext() as context:
            context.prec=8
            self.assertEqual(self.plan(unidad_redondeo=Decimal('.01')),expected)
        plan=self.plan(amount='400000.01',term=3,rate='0',seguro_mensual_porcentaje=Decimal(0),unidad_redondeo=Decimal('.01'))
        self.assertEqual(Decimal(plan['saldoFinal']),0)
        self.assertEqual(Decimal(plan['totales']['capital']),Decimal('400000.01'))
        self.assertNotEqual(plan['ultimaCuota'],plan['cuotaCalculada'])

    def test_unsupported_financial_parameters_do_not_silently_become_zero(self):
        for changed in ({'factor_adicional':Decimal('.09')},{'periodicidad_meses':2},
                {'seguro_mensual_porcentaje':0.09},{'unidad_redondeo':Decimal('.05')},
                {'base_seguro':'UNKNOWN'},{'perfil':('390','127','PSC')}):
            with self.assertRaises(ValueError):self.plan(**changed)
        for value in ('NaN','Infinity','-1'):
            with self.assertRaises(ValueError):self.plan(amount=value)

    def test_existing_calculator_for_other_products_is_unchanged(self):
        from apps.xcore_consumo.services.calculadora import calcular_valor_cuota
        self.assertEqual(calcular_valor_cuota(800000,6,25.41,0,0),143387.50)


@override_settings(ROOT_URLCONF='core.urls_credihoy_publico',CREDIHOY_PUBLICO_ENABLED=True,DEBUG=False,
    CREDIHOY_SIMULADOR_LOCAL_ENABLED=True,CREDIHOY_SIMULADOR_LIMITES=LIMITS,CREDIHOY_TASAS_ENABLED=True,
    CREDIHOY_COTIZACION_ENABLED=False,CREDIHOY_COTIZACION_TITULAR_ENABLED=False,
    CREDIHOY_SIMULADOR_DINAMICO_ENABLED=False,CREDIHOY_CONSENTIMIENTO_REAL_ENABLED=False,
    CREDIHOY_TITULAR_ENABLED=True,CREDIHOY_TITULAR_FUENTE_ENABLED=True,
    CREDIHOY_SEGUIMIENTO_TRANSPORTE='HTTP_PROPIETARIO',CREDIHOY_SEGUIMIENTO_DB_ALIAS='default',
    CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE='SYNTHETIC_ONLY',
    CREDIHOY_TITULAR_SECRET='SYNTHETIC_OWNER_'+'a'*64,CREDIHOY_INSTITUCIONAL_SECRET='SYNTHETIC_RELAY_'+'b'*64,
    SESSION_COOKIE_NAME='credihoy_publico_session',CSRF_COOKIE_NAME='credihoy_publico_csrf',
    SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',
    CSRF_COOKIE_SECURE=True,CSRF_COOKIE_HTTPONLY=True,CSRF_COOKIE_SAMESITE='Strict',
    SESSION_ENGINE='django.contrib.sessions.backends.db',
    CREDIHOY_PUBLICO_AVISO_VERSION='SYNTHETIC_ONLY',CREDIHOY_PUBLICO_AVISO_TEXTO='Synthetic notice, not legal policy.',
    CREDIHOY_PUBLICO_BOT_SITEKEY='synthetic',CREDIHOY_PUBLICO_BOT_SECRET='synthetic',
    CREDIHOY_PUBLICO_HOST='testserver',CREDIHOY_PUBLICO_CANALES=('SMS','EMAIL'),
    CREDIHOY_PUBLICO_RESTRINGIDO=True,CREDIHOY_PUBLICO_REDES=('127.0.0.1/32',),
    CREDIHOY_PUBLICO_PROXIES=(),CREDIHOY_PUBLICO_AUTORIZACION_REFERENCIA='SYNTHETIC_ONLY',
    CREDIHOY_PUBLICO_RETENCION_DIAS=30,OTP_PROVIDER_MODE='real',
    CREDIHOY_PUBLICO_VERIFY_SID='VA'+'a'*32,TWILIO_VERIFY_SID='VA'+'b'*32)
class SimuladorLocalTests(TransactionTestCase):
    def setUp(self):
        now=timezone.now();day=timezone.localdate()
        self.actor=get_user_model().objects.create_user(username='synthetic-local-simulator',password='synthetic-only')
        person=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='0000000000',
            primer_apellido='SINTETICO',fecha_expedicion=day)
        self.s=Solicitud.objects.create(solicitante=person,producto=wire.PRODUCTO,
            monto_solicitado=Decimal('1000000'),plazo_solicitado=12,
            metadatos={'modo':'CONSULTA_REAL_CONTROLADA'},consecutivo=1,numero_solicitud='SYNTHETIC_SIM')
        SolicitudConsumo.objects.create(solicitud=self.s,orchestration_data={'untouched':'SYNTHETIC'},form_data={})
        fact=Hecho(valor=Decimal('5000000'),disponible=True,fuente='SYNTHETIC_ONLY',
            referencia_evidencia='SYNTHETIC_ONLY',ruta_origen='synthetic.aportes',fecha_corte=day,
            fecha_consulta=now-timedelta(seconds=1),estado_certificacion=EC.CERTIFICADO)
        self.study={'decisionCrediticia':'NO_AUTORIZADA','reglas':[{'codigo':'UNICO_FILA_14',
            'estadoEvaluacion':'NO_EVALUABLE','evidencia':{'operando':'saldo_aportes_utilizable',
                'hecho':fact.a_dict(),'limiteCOP':'4500000','unidad':'COP'}}]}
        EvaluacionConsumo.objects.create(solicitud=self.s,resultados={funcional.KEY:self.study},decision_final='NO_AUTORIZADA')
        profile=PerfilTasasCredihoy.objects.create()
        for line,rate,kind in [('VENTANILLA','17.69','V'),('LIBRANZA','16.67','N')]:
            common=dict(perfil=profile,responsable=self.actor,numero_version=1,linea=line,
                desde=day-timedelta(days=1),hasta=day+timedelta(days=2),documento='SYNTHETIC',
                version_documento='1',referencia='SYNTHETIC_NOT_REAL_APPROVAL',soporte_sha256='a'*64)
            tariff=TasaComercialCredihoy.objects.create(**common,tasa_namv=Decimal(rate),
                tasa_ea=Decimal('20'),tasa_mv_publicada=Decimal('1'))
            assignment=AsignacionTarifaCredihoy.objects.create(**common,alcance='TIPO',medio_pago='',tipo_nomina=kind)
            for target,field in [(tariff,'tasa'),(assignment,'asignacion')]:
                support={'documento':'SYNTHETIC','versionDocumento':'1','referencia':'SYNTHETIC_ONLY',
                    'soporteSha256':'a'*64,'desde':common['desde'].isoformat(),'hasta':common['hasta'].isoformat(),
                    'vigenciaAbierta':False}
                for action in ('APROBADA','ACTIVADA'):
                    EventoTasasCredihoy.objects.create(**{field:target},responsable=self.actor,accion=action,
                        referencia_autorizacion='SYNTHETIC_ONLY',verificacion={'ratificacionDocumental':support} if action=='APROBADA' else {},
                        idempotency_key=uuid4(),request_sha256='a'*64)
        self.select('V')
        opts=override_settings(CREDIHOY_PUBLICO_ENSAYO={'solicitudes':[str(self.s.pk)]},
            CREDIHOY_TITULAR_DOCUMENTOS_HMAC=[owner.document_hmac(person.numero_identificacion)],
            CREDIHOY_PUBLICO_IDENTIDADES_HMAC=(access.huella('identidad','CC:'+person.numero_identificacion),),
            CREDIHOY_PUBLICO_AUTORIZACION_HASTA=(now+timedelta(hours=1)).isoformat(),
            CREDIHOY_PUBLICO_VALIDACION={'estado':'VALIDADO','referenciaPrivacidad':'SYNTHETIC',
                'referenciaSeguridad':'SYNTHETIC','politicaSha256':privacy.POLITICA['sha256'],
                'avisoVersion':'SYNTHETIC_ONLY','avisoSha256':sha256(b'Synthetic notice, not legal policy.').hexdigest(),
                'sesionSegundos':900,'otpSegundos':300,'limpiezaTemporalMaxSegundos':60,'ventanaSeguimientoDias':30})
        opts.enable();self.addCleanup(opts.disable)
        self.client=APIClient(enforce_csrf_checks=True)
        self.boot=self.client.get(BASE+'inicio/',secure=True).json()
        session=self.client.session
        self.grant=Acceso.objects.create(navegador_hmac=access.huella('navegador',session[access.NAVEGADOR]),
            identidad_hmac=access.huella('identidad','CC:'+person.numero_identificacion),peticion_hmac='a'*64,
            estado='VERIFICADO',expira_at=now+timedelta(minutes=5),sesion_expira_at=now+timedelta(minutes=15),
            datos_cifrados=encrypt_text(json.dumps({'numeroDocumento':person.numero_identificacion,'nombre':'SINTETICO'})))
        session[access.GRANT]=str(self.grant.pk);session.save()
        self.identity={'numeroDocumento':person.numero_identificacion,'_sesionReferencia':str(self.grant.pk),
            '_sesionExpiraAt':self.grant.sesion_expira_at.isoformat()}
        factory=APIRequestFactory()
        def request_owner(url,path,body,rid,headers):
            from apps.xcore_consumo.services.credihoy_v2.unico.publico_seguimiento_http import SolicitudesTitularView
            request=factory.post(path,body,content_type='application/json',secure=True,
                HTTP_AUTHORIZATION=headers['Authorization'],HTTP_X_REQUEST_ID=rid)
            response=SolicitudesTitularView.as_view()(request)
            if response.status_code!=200:raise wire.NoDisponible
            return response.data
        def request_relay(operation,payload,rid):
            from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional_http import SeguimientoView
            def authorize(view,request):view.rid=rid
            request=factory.post('/api/interno/credihoy/seguimiento/',payload,format='json',secure=True)
            with patch.object(SeguimientoView,'authorize_request',authorize):
                response=SeguimientoView.as_view()(request)
            if response.status_code!=200:raise wire.NoDisponible
            return response.data
        for target,value in [(wire.request_packet,request_owner),(wire.call,request_relay)]:
            name='request_packet' if target is wire.request_packet else 'call'
            p=patch.object(wire,name,side_effect=value);p.start();self.addCleanup(p.stop)
        nonce=patch.object(owner,'claim_delegation',side_effect=lambda rid:controls.limite([('titular_delegacion',rid,1)]))
        nonce.start();self.addCleanup(nonce.stop)
        guard=patch.object(funcional,'_firma',side_effect=AssertionError('SIMULATION_MUST_NOT_REQUIRE_SIGNATURE'))
        guard.start();self.addCleanup(guard.stop)

    def select(self,kind):
        rid=str(uuid4());now=timezone.now().isoformat()
        evidence={'codigo':'999' if kind=='V' else '100','tipo':kind,'activa':True,'estado':'DISPONIBLE',
            'fuente':FUENTE,'fechaConsulta':now,'requestId':rid}
        current=resolver_condicion(adaptar_pagaduria(evidence,request_id=rid,fecha_consulta=now))
        return SeleccionTarifaCredihoy.objects.create(solicitud=self.s,responsable=self.actor,request_id=rid,
            evidencia=evidence,evidencia_sha256=sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest(),resultado=current)

    def body(self,monto='1000000.00',plazo=12):
        return {'solicitudId':str(self.s.pk),'operacion':'simular','escenario':{'monto':monto,'plazoMeses':plazo}}

    def operate(self,payload=None):return sim.operar_simulacion(self.identity,payload or self.body(),str(uuid4()))

    def post(self,payload=None,**headers):
        return self.client.post(BASE+'cotizacion/',payload or self.body(),format='json',secure=True,
            HTTP_ORIGIN='https://testserver',HTTP_X_CSRFTOKEN=self.boot['csrfToken'],
            HTTP_X_REQUEST_ID=str(uuid4()),HTTP_X_ACCESO_REFERENCIA=str(self.grant.pk),**headers)

    def test_numeric_rates_insurance_amortization_and_no_financial_writes(self):
        from apps.xcore_consumo.services.calculadora import calcular_valor_cuota,calcular_vida_deudores
        before=deepcopy(SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data)
        for kind,rate in [('V','17.69'),('N','16.67')]:
            self.select(kind);p=self.operate();r=p['resultado'];wire.project_simulation(p)
            expected=calcular_valor_cuota(Decimal('1000000'),12,Decimal(rate),0,0)
            self.assertEqual(r['cuotaCapitalInteres'],format(Decimal(str(expected)),'.2f'))
            self.assertEqual(r['seguro']['valorMensualEstimado'],format(Decimal(str(calcular_vida_deudores(1000000))),'.2f'))
            self.assertEqual(sum(Decimal(a['capital']) for a in r['amortizacion']),Decimal('1000000'))
            self.assertEqual(r['amortizacion'][-1]['saldoFinal'],'0.00')
            self.assertEqual(r['amortizacion'][0]['seguroEstimado'],r['amortizacion'][-1]['seguroEstimado'])
            self.assertFalse(p['ofertable']);self.assertFalse(p['autorizaDecision'])
        self.assertFalse(ConsentimientoConsumo.objects.exists())
        self.assertEqual(SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data,before)
        self.assertEqual(EvaluacionConsumo.objects.get(solicitud=self.s).resultados,{funcional.KEY:self.study})
        self.s.refresh_from_db();self.assertEqual(self.s.monto_solicitado,Decimal('1000000'))

    def test_real_public_relay_owner_chain_csrf_session_and_independent_flags(self):
        response=self.post();self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response['Cache-Control'],'no-store, private')
        self.assertEqual(response.json()['resultado']['seguro']['valorMensualEstimado'],'900.00')
        session=self.client.get(BASE+'sesion/',secure=True).json()
        self.assertTrue(session['acciones']['simuladorLocal']);self.assertFalse(session['acciones']['simular'])
        params=self.post({'solicitudId':str(self.s.pk),'operacion':'simular'})
        self.assertEqual(params.status_code,200);self.assertIsNone(params.json()['resultado'])
        self.assertEqual(params.json()['limites']['montoMaximo'],'10000000.00')
        self.assertEqual(params.json()['alcance'],'ESTIMACION_GENERICA_NO_ELEGIBILIDAD')
        bad=APIClient(enforce_csrf_checks=True)
        self.assertEqual(bad.post(BASE+'cotizacion/',self.body(),format='json',secure=True).status_code,403)
        self.client.session.flush()
        self.assertEqual(self.post().status_code,401)

    def test_closed_flag_limits_rate_overrides_and_monthly_policy(self):
        with override_settings(CREDIHOY_SIMULADOR_LOCAL_ENABLED=False):
            with self.assertRaises(wire.NoDisponible):self.operate()
            self.assertEqual(self.post().status_code,503)
        for body in [self.body('10000000.01'),self.body('0'),self.body('1000000',25),
                self.body('1000000',True),self.body('NaN'),{**self.body(),'tasa':'1'},
                {**self.body(),'escenario':{**self.body()['escenario'],'tasa':'1'}}]:
            with self.assertRaises(ValueError):self.operate(body)
        self.assertEqual(self.post({'solicitudId':str(self.s.pk),'operacion':'cotizar'}).status_code,503)
        with override_settings(CREDIHOY_SIMULADOR_LIMITES=None):
            p=self.operate()
            self.assertEqual(p['limites']['version'],'CREDIHOY_DIGITAL_390_111_CFA_20261026_V1')
            self.assertEqual(p['limites']['cuotasMaximas'],48)

    def test_foreign_expired_revoked_and_wrong_browser_are_rejected(self):
        original=self.identity
        self.identity={**original,'numeroDocumento':'9999999999'}
        with self.assertRaises(Http404):self.operate()
        self.identity={**original,'_sesionExpiraAt':(timezone.now()-timedelta(seconds=1)).isoformat()}
        with self.assertRaises(wire.NoDisponible):self.operate()
        self.identity=original
        self.grant.navegador_hmac='b'*64;self.grant.save()
        self.assertEqual(self.post().status_code,401)
        self.grant.revocado=True;self.grant.save()
        self.assertEqual(self.post().status_code,401)

    def test_inactive_catalog_is_closed_and_historical_study_does_not_gate_generic_estimate(self):
        with override_settings(CREDIHOY_TASAS_ENABLED=False):
            with self.assertRaisesRegex(ValueError,'PAGADURIA_O_TARIFA_SIMULACION_NO_ACREDITADA'):
                self.operate()
        e=EvaluacionConsumo.objects.get(solicitud=self.s)
        e.resultados[funcional.KEY]['reglas'][0]['evidencia']['hecho']['fecha_corte']='2001-01-01'
        e.save()
        self.assertIsNotNone(self.operate()['resultado'])

    def test_generic_line_uses_active_catalog_without_persisting_a_commercial_selection(self):
        original_count=SeleccionTarifaCredihoy.objects.filter(solicitud=self.s).count()
        no_selection=patch.object(SeleccionTarifaCredihoy.objects,'filter',return_value=SeleccionTarifaCredihoy.objects.none())
        before=deepcopy(SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data)
        with no_selection,override_settings(CREDIHOY_SIMULADOR_LIMITES=None):
            for line,rate in [('LIBRANZA','16.670000'),('VENTANILLA','17.690000')]:
                body=self.body();body['escenario']['linea']=line
                rid=str(uuid4());queried=timezone.now().isoformat()
                evidence={'codigo':'100' if line=='LIBRANZA' else '999','tipo':'N' if line=='LIBRANZA' else 'V',
                    'activa':True,'estado':'DISPONIBLE','fuente':FUENTE,'fechaConsulta':queried,'requestId':rid}
                with patch.object(sim,'_pagaduria_identidad',return_value=adaptar_pagaduria(evidence,request_id=rid,fecha_consulta=queried)):
                    p=self.operate(body)
                self.assertEqual(p['tasa']['linea'],line);self.assertEqual(p['tasa']['valor'],rate)
                self.assertFalse(p['ofertable']);self.assertFalse(p['autorizaDecision'])
        self.assertEqual(SeleccionTarifaCredihoy.objects.filter(solicitud=self.s).count(),original_count)
        self.assertEqual(SolicitudConsumo.objects.get(solicitud=self.s).orchestration_data,before)
        for scenario in (None,{'monto':'1000000','plazoMeses':12,'linea':'OTHER'}):
            with self.assertRaises(ValueError):self.operate({**self.body(),'escenario':scenario})
        with patch('apps.xcore_consumo.services.credihoy_v2.unico.tasas.vigente',return_value=False),self.assertRaises(ValueError):
            self.operate()

    def test_insurance_parameter_zero_and_rounding_final_adjustment(self):
        for factor,expected in [('0','0.00'),('0.001','1000.00'),('0.0009','900.00')]:
            with override_settings(CREDIHOY_SIMULADOR_SEGURO_MENSUAL=factor):
                r=self.operate()['resultado'];self.assertEqual(r['seguro']['valorMensualEstimado'],expected)
                self.assertEqual(sum(Decimal(a['cuotaEstimada']) for a in r['amortizacion']),Decimal(r['totalEstimado']))
        for factor in ('NaN','Infinity','-0.1','0.02',0.0009):
            with override_settings(CREDIHOY_SIMULADOR_SEGURO_MENSUAL=factor),self.assertRaises(ValueError):self.operate()
        one=self.operate(self.body('400000.01',1))['resultado']
        self.assertEqual(one['amortizacion'][0]['capital'],'400000.01')

    def test_projection_rejects_tampering_and_relay_rate_limit(self):
        p=self.operate()
        for change in (lambda x:x.update(ofertable=True),lambda x:x.update(oferta={}),
                lambda x:x['resultado'].update(totalEstimado='1.00'),
                lambda x:x['resultado']['amortizacion'][0].update(capital='1.00'),
                lambda x:x['seguro'].update(base='SALDO'),lambda x:x['tasa'].update(valor='1')):
            bad=deepcopy(p);change(bad)
            with self.assertRaises(wire.NoDisponible):wire.project_simulation(bad)
        for _ in range(12):self.assertEqual(self.post().status_code,200)
        with patch.object(access,'limite',return_value=False):self.assertEqual(self.post().status_code,503)
