"""Original table SELECT -> existing HTTP client -> owned dossier/classifier."""
from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from unittest.mock import Mock,patch
from uuid import uuid4
import sys
from pathlib import Path
from django.test import TransactionTestCase

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'api_data_v1'))
from incluirtec.test_credihoy_estamentos_select import OriginalSelectTests,DOC
from apps.xcore_consumo.services.credihoy_v2.cliente import ApiDataCrediHoyV2Client,ConfiguracionApiDataV2,ENDPOINT_V2
from apps.xcore_consumo.services.credihoy_v2.unico.estamentos import (
    actualizar_estamentos,clasificar_novedades,COLABORADOR,FAMILIAR,BLOQUEADO,validar_lectura_original)
from incluirtec.services.credihoy_estamentos_select import read_estamentos


class OriginalSourceIntegrationTests(TransactionTestCase):
    def test_cgte_receipt_has_honest_execution_mode_and_preserves_classification(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.estamentos import ORDINARIO
        raw=read_estamentos(self.fixture.conn,DOC,solicitud_id=self.case.pk,request_id='SYNTHETIC',cgte=True)
        with self.assertRaises(ValueError):validar_lectura_original(raw,solicitud_id=self.case.pk,documento=DOC,request_id='SYNTHETIC')
        raw['autorizacionReferencia']='a'*64
        validar_lectura_original(raw,solicitud_id=self.case.pk,documento=DOC,request_id='SYNTHETIC')
        result=clasificar_novedades(raw,solicitud_id=self.case.pk,titular_referencia=sha256(DOC.encode()).hexdigest(),
            ahora=datetime.now(timezone.utc),monto=Decimal('400000'))
        self.assertEqual(result['estado'],ORDINARIO)
        raw['ejecucionSelectAcotada']['procedimientoEjecutado']=True
        with self.assertRaises(ValueError):validar_lectura_original(raw,solicitud_id=self.case.pk,documento=DOC,request_id='SYNTHETIC')

    def setUp(self):
        from tools.cd2b.support import seed
        from django.contrib.auth import get_user_model
        self.case=seed();self.user=get_user_model().objects.create_user('select-source-synthetic',is_staff=True)
        self.case.asesor=self.user;self.case.producto='CREDIHOY UNICO (NUEVO)'
        self.case.metadatos={'modo':'CONSULTA_REAL_CONTROLADA'};self.case.save()
        self.case.solicitante.numero_identificacion=DOC;self.case.solicitante.save()
        self.fixture=OriginalSelectTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.session=Mock();self.client=ApiDataCrediHoyV2Client(
            ConfiguracionApiDataV2(base_url='https://internal.invalid',token='SYNTHETIC'),session=self.session)

    def answer(self,url,*,json,**kwargs):
        self.assertTrue(url.endswith(ENDPOINT_V2));self.assertEqual(json['alcance'],'ESTAMENTOS')
        self.assertTrue(kwargs['verify']);self.assertFalse(kwargs['allow_redirects'])
        result=read_estamentos(self.fixture.conn,json['numeroDocumento'],solicitud_id=json['solicitudId'],
            request_id=kwargs['headers']['X-Request-ID'])
        reply=Mock(status_code=200);reply.json.return_value=result
        return reply

    def test_worker_reads_original_source_via_existing_internal_client_and_persists_no_approval(self):
        from apps.xcore_consumo.models import ConsultaEstamentoOracle
        self.fixture.db.execute("INSERT INTO NO223MEMPLEADO VALUES('HOLDER','AC')")
        self.session.post.side_effect=self.answer
        with patch('apps.xcore_consumo.services.oracle._connect',side_effect=AssertionError('PORTAL_ORACLE_PROHIBIDO')):
            result=actualizar_estamentos(self.case.pk,actor=self.user,cliente=self.client)
        state=clasificar_novedades(result,solicitud_id=self.case.pk,
            titular_referencia=sha256(DOC.encode()).hexdigest(),ahora=datetime.now(timezone.utc),monto=Decimal('400000'))
        self.assertEqual(state['estado'],COLABORADOR);self.assertEqual(state['rutaDestinoCandidata'],10002856)
        self.assertFalse(state['autorizaAutomatizacion']);self.session.post.assert_called_once()
        record=ConsultaEstamentoOracle.objects.latest('pk');self.assertEqual(record.estado,'OK')
        self.assertNotIn(DOC,str(record.request_payload));self.assertFalse(record.response_payload['ejecutaStart'])

    def test_family_priority_survives_sp_employee_branch_without_changing_kinship_rule(self):
        self.fixture.db.executemany('INSERT INTO NO223MEMPLEADO VALUES(?,?)',[('HOLDER','AC'),('BEN','AC')])
        self.fixture.db.execute("INSERT INTO GR1068MGRUPFAM VALUES('HOLDER','BEN','1')")
        self.session.post.side_effect=self.answer
        result=actualizar_estamentos(self.case.pk,actor=self.user,cliente=self.client)
        self.assertEqual(result['ramaOriginalSP'],'COLABORADOR')
        state=clasificar_novedades(result,solicitud_id=self.case.pk,titular_referencia=sha256(DOC.encode()).hexdigest(),
            ahora=datetime.now(timezone.utc),monto=Decimal('400000'))
        self.assertEqual(state['estado'],FAMILIAR);self.assertFalse(state['autorizaAutomatizacion'])

    def test_timeout_and_foreign_response_fail_closed_without_private_error_or_retry(self):
        from apps.xcore_consumo.models import ConsultaEstamentoOracle
        import requests
        self.session.post.side_effect=requests.Timeout('PRIVATE_DOCUMENT_NOT_FOR_LOG')
        with self.assertRaisesRegex(ValueError,'SIN_REINTENTO'):
            actualizar_estamentos(self.case.pk,actor=self.user,cliente=self.client)
        self.session.post.assert_called_once()
        record=ConsultaEstamentoOracle.objects.latest('pk')
        self.assertEqual(record.estado,'ERROR');self.assertNotIn('PRIVATE_DOCUMENT',str(record.response_payload))
        self.session.post.side_effect=self.answer
        raw=self.answer('https://internal.invalid'+ENDPOINT_V2,json={'alcance':'ESTAMENTOS','numeroDocumento':DOC,'solicitudId':str(self.case.pk)},headers={'X-Request-ID':'SYNTHETIC'},verify=True,allow_redirects=False).json()
        with self.assertRaises(ValueError):validar_lectura_original(raw,solicitud_id=uuid4(),documento=DOC,request_id='SYNTHETIC')

    def test_no_novelty_requires_unique_holder_and_uncovered_organ_never_clears_ordinary(self):
        self.fixture.db.execute("INSERT INTO AP579DCARGXASO VALUES('HOLDER','1','Y',NULL)")
        self.session.post.side_effect=self.answer
        result=actualizar_estamentos(self.case.pk,actor=self.user,cliente=self.client)
        state=clasificar_novedades(result,solicitud_id=self.case.pk,titular_referencia=sha256(DOC.encode()).hexdigest(),
            ahora=datetime.now(timezone.utc),monto=Decimal('400000'))
        self.assertEqual(state['estado'],BLOQUEADO)

    def test_source_without_exclusive_reader_evidence_never_authorizes_an_ordinary_case(self):
        from apps.xcore_consumo.services.credihoy_v2.unico.estamentos import ORDINARIO
        self.session.post.side_effect=self.answer
        result=actualizar_estamentos(self.case.pk,actor=self.user,cliente=self.client)
        args=dict(solicitud_id=self.case.pk,titular_referencia=sha256(DOC.encode()).hexdigest(),
            ahora=datetime.now(timezone.utc),monto=Decimal('400000'))
        state=clasificar_novedades(result,**args)
        self.assertEqual(state['estado'],ORDINARIO)
        self.assertEqual(state['accionPermitida'],'EVALUAR_POLITICA_ORDINARIA')
        self.assertFalse(state['paradaInstitucionalGarantizada'])
        for missing in (None,False,'true'):
            raw={**result,'lectorSelectExclusivo':missing}
            with self.assertRaises(ValueError):
                validar_lectura_original(raw,solicitud_id=self.case.pk,documento=DOC,request_id=raw['requestId'])
            self.assertFalse(clasificar_novedades(raw,**args)['autorizaAutomatizacion'])
