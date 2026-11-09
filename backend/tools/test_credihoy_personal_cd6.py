"""Personal confirmation against the disposable local PostgreSQL test database."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import sys

from django.core import signing
from django.db import connection
from django.test import TransactionTestCase,override_settings
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
                patch.object(q,'fuente_radicacion_personal',return_value={'payload':{}})):
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

    def prepared(self):return self.operate('preparar',primeraFechaPagoPropuesta='2030-01-10')['presentacion']

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

