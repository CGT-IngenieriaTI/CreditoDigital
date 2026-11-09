"""New-case regressions of the EXISTING CD6 gateway; fake Oracle only."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'api_data_v1'))

from incluirtec.digital_operations.contracts import canonical
from incluirtec.digital_operations.production_preparation import _prepare_v1 as prepare, verify, digest, PROOF_FIELDS
from incluirtec.digital_operations.production_preflight import QUERIES, ROUTE_DETAIL_SQL, ROUTE_PATHS_SQL
from incluirtec.digital_operations.references import normalize_references, reference_catalog
from .test_condiciones import fixture, Port, NOW
from .condiciones import request_final, SID
from .journal import Journal
from .worker import operate


def configuration():
    return {'identidad': [{'DB_NAME': 'linix', 'DB_UNIQUE_NAME': 'linix', 'SERVICE_NAME': 'linix',
            'INSTANCE_NAME': 'linix', 'HOST_NAME': 'svr-cgt-orabd', 'SESSION_USER': 'CGTE'}],
        'modelo': [{'K_PROCES': 'CREDITOS', 'K_MODELO': 'CREDIHOY_VAL_EMPLEADO', 'I_ACTIVO': 'Y', 'K_IDRUTA': 10002896}],
        'ruta': [{'K_SERIE': n, 'Q_TRANSI': i, 'K_IDACTI': act, 'I_EJECUC': execution, 'I_CAPTUR': capture,
                  'I_AUTORI': 'N', 'N_ACCION': action} for i, n, act, execution, capture, action in (
            (1, 7501, 113, 'A', 'N', 'PK_CA_WF_RADICA_SOL_CREDITO.PR_RADICAR_SOLICITUD'),
            (2, 7502, 3883, 'A', 'N', 'PK_CA_WF_REFERENCIA.PR_INSERTA_REFER_SOLICITUD'),
            (3, 7503, 6964, 'H', 'D', 'PK_WF_ACTIVIDAD_CRUD.PR_EJECUTAR_ACTIVIDAD_CRUD'))],
        'caminos': [], 'packages': [{'OBJECT_NAME': name, 'OBJECT_TYPE': 'PACKAGE', 'STATUS': 'VALID',
                                   'LAST_DDL_TIME': '2026-10-01T00:00:00'} for name in (
            'PK_WF_FLUJO', 'PK_CA_WF_RADICA_SOL_CREDITO', 'PK_CA_WF_REFERENCIA')], 'triggers': []}


def certificates(config):
    fingerprint = digest(canonical(config))
    return {name: {'configSha256': fingerprint, 'alcance': scope, 'referencia': 'SYNTHETIC-ONLY',
                  'responsable': 'SYNTHETIC-ONLY', 'desde': (NOW - timedelta(minutes=1)).isoformat(),
                  'hasta': (NOW + timedelta(hours=1)).isoformat()} for name, scope in (
        ('efectos', '113_3883_HASTA_6964_SIN_EFECTOS_POSTERIORES'),
        ('referencias3883', 'REFERENCIAS_3883_ADMISIBLES'))}


def case_message(payload, config=None):
    config = config or configuration()
    a, b, c, _ = reference_catalog()[0]['ciudades'][0]
    references = normalize_references([{'tipoReferencia': 'PE', 'nombre': 'PERSONA SINTETICA NO REAL',
        'telefono': '3000000000', 'codigoPais': a, 'codigoDepartamento': b, 'codigoCiudad': c}])
    evidence = {key: {'referencia': 'SYNTHETIC-ONLY', 'sha256': digest(key)} for key in PROOF_FIELDS}
    evidence['referencias']['sha256'] = digest(canonical(references))
    authority = {'solicitudId': payload['solicitudId'], 'intencionId': payload['intencionId'],
        'responsable': 'RESPONSABLE-SINTETICO', 'referencia': payload['autorizacionAdministrativa']['referencia'],
        'antecedente': 'SOLO PRUEBAS UNITARIAS - NO AUTORIZACION REAL'}
    return {'accion': 'preparar-caso', 'solicitudId': payload['solicitudId'],
        'preparacion': prepare(payload, references, evidence=evidence, authorization=authority),
        'perfilCore': {'sucursal': '101', 'nomina': '100', 'tipoNomina': 'N'},
        'preflight': {'configuracion': config, 'sha256': digest(canonical(config))},
        'certificaciones': certificates(config)}


def technical_message(payload, config=None):
    from incluirtec.digital_operations.production_preparation import prepare as prepare_v2
    legacy = case_message(payload, config)
    source = deepcopy(payload)
    source.pop('autorizacionAdministrativa')
    source['evidenciaTasa'].pop('referenciaCertificacion')
    return {key: legacy[key] for key in ('accion', 'solicitudId', 'perfilCore', 'preflight')} | {
        'preparacion': prepare_v2(source, legacy['preparacion']['referencias'], evidence={
            'consentimiento': legacy['preparacion']['evidencias']['consentimiento']})}


class CasePort(Port):
    def __init__(self, payload):
        super().__init__(payload)
        self.config = configuration()

    def open_connection(self):
        conn = super().open_connection()
        factory = conn.cursor
        def cursor():
            cur = factory()
            execute = cur.execute
            def run(sql, parameters=None, **binds):
                label = next((label for label, query in QUERIES.items() if sql == query), None)
                if label:
                    rows = self.config[label]
                    keys = list(rows[0]) if rows else []
                    cur.description = [(key,) for key in keys]
                    cur.rows = [tuple(row[key] for key in keys) for row in rows]
                elif sql in (ROUTE_DETAIL_SQL, ROUTE_PATHS_SQL):
                    detail = self.child_routes[parameters['route_id']]
                    rows = detail['ruta' if sql == ROUTE_DETAIL_SQL else 'caminos']
                    keys = list(rows[0]) if rows else []
                    cur.description = [(key,) for key in keys]
                    cur.rows = [tuple(row[key] for key in keys) for row in rows]
                elif sql == 'SET TRANSACTION READ ONLY':
                    cur.rows = []
                elif 't.K_IDACTI=3883 ORDER BY' in sql:
                    # Simulate LINIX propagation of the newly generated 113 SC, never a prior SC.
                    records = conn.backend.records
                    text = records[0].xml_new if records else None
                    transform = getattr(self, 'reference_input_transform', None)
                    if text and transform:
                        text = transform(text)
                    cur.rows = [(2, 'E', text, text, '<Salida/>')] if text else []
                elif 't.K_IDACTI=6783 ORDER BY' in sql:
                    cur.rows = getattr(self, 'evaluation_rows', [])
                elif 't.K_IDACTI=4965 ORDER BY' in sql:
                    cur.rows = getattr(self, 'central_rows', [])
                elif sql.startswith('SELECT * FROM LINIX.WF1750DERREJEC WHERE K_FLUJO='):
                    cur.description = [('ERROR_SINTETICO',)]
                    cur.rows = getattr(self, 'error_details', [])
                else:
                    from incluirtec.digital_operations.oracle_readonly import SERIALIZED_RADICATION_SQL
                    from incluirtec.digital_operations.credihoy_repository import RADICATION_BY_FLOW_SQL
                    if sql == SERIALIZED_RADICATION_SQL:
                        sql = RADICATION_BY_FLOW_SQL
                    execute(sql, parameters, **binds)
            cur.execute = run
            cur.fetchmany = lambda count: cur.rows[:count]
            return cur
        conn.cursor = cursor
        return conn


class CaseTests(TestCase):
    def test_incidents_are_classified_without_declaring_them_resolved(self):
        from .worker import summarize_incidents
        evidence = {'K_FLUJO': '123', 'erroresWorkflow': 1,
            'tareas': [{'K_SERIE': 1, 'I_ESTTAR': 'E'}, {'K_SERIE': 2, 'I_ESTTAR': 'A'}],
            'erroresDetalleIncompleto': False,
            'erroresDetalle': [{'K_FLUJO': 123, 'K_SERIE': 1, 'N_ERROR': 'PRIVATE_ERROR'}]}
        before = deepcopy(evidence)
        result = summarize_incidents(evidence)
        self.assertEqual(result['estado'], 'CONSERVADAS_EN_TAREAS_EJECUTADAS')
        self.assertEqual(result['enTareasEjecutadas'], 1)
        self.assertEqual(result['sinClasificar'], 0)
        self.assertNotIn('PRIVATE_ERROR', str(result))
        self.assertEqual(evidence, before)
        for change in ({'erroresDetalleIncompleto': True}, {'erroresWorkflow': 2},
                {'erroresDetalle': [{'K_FLUJO': 999, 'K_SERIE': 1}]},
                {'tareas': evidence['tareas'] + [{'K_SERIE': 1, 'I_ESTTAR': 'E'}]}):
            result = summarize_incidents(evidence | change)
            self.assertEqual(result['estado'], 'REQUIEREN_REVISION')
            self.assertEqual(result['enTareasEjecutadas'], 0)
            self.assertGreater(result['sinClasificar'], 0)
        evidence['erroresDetalle'][0]['K_SERIE'] = 2
        self.assertEqual(summarize_incidents(evidence)['enTareasActivas'], 1)
        self.assertEqual(summarize_incidents(evidence | {'erroresWorkflow': 0})['estado'], 'REQUIEREN_REVISION')
        self.assertEqual(summarize_incidents(evidence | {'erroresWorkflow': 0, 'erroresDetalle': []})['estado'],
            'SIN_ERRORES_REGISTRADOS')

    def test_read_only_projection_keeps_uncertain_journal_and_permanent_start_guard(self):
        from .condiciones import FichaError
        self.journal.prepare(self.payload, {'usuarioWorkflow': 'SYNTHETIC'})
        self.journal.claim(); self.journal.mark_start(); self.journal.checkpoint('123')
        self.journal.settle('INCIERTA', {'codigo': 'INCIDENCIA_HISTORICA'})
        op = self.journal.operation()
        fact = {'estado': 'CONFIRMADA_EN_CA090', 'K_FLUJO': '123', 'SC': '999',
            'intencionId': str(op.intent_id), 'fingerprint': op.fingerprint,
            'observadoEn': NOW.isoformat(), 'actividadesActivas': [{'actividad': 5989, 'K_SERIE': 20}],
            'incidencias': {'estado': 'CONSERVADAS_EN_TAREAS_EJECUTADAS'}, 'STARTNuevos': 0}
        self.journal.record('RADICACION_PERSISTIDA_CONCILIADA', fact)
        changes = self.journal.db.total_changes
        for _ in range(2):
            status = self.journal.status()
            self.assertEqual(status['estado'], 'INCIERTA')
            self.assertTrue(status['STARTIntentado'])
            projection = status['radicacionPersistida']['presentacion']
            self.assertEqual(projection['asociado']['estado'], 'SOLICITUD_REGISTRADA')
            self.assertFalse(projection['asociado']['aprobacionAcreditada'])
            self.assertNotIn('5989', str(projection['asociado']))
            self.assertNotIn('123', str(projection['asociado']))
            self.assertEqual(projection['administrador']['actividadesActivasAlCorte'], fact['actividadesActivas'])
            self.assertEqual(projection['administrador']['accionPermitida'], 'SOLO_CONCILIAR')
            self.assertFalse(projection['administrador']['nuevoStartPermitido'])
        self.assertEqual(self.journal.db.total_changes, changes)
        self.assertEqual(self.journal.last_evidence('RADICACION_PERSISTIDA_CONCILIADA'), fact)
        self.journal.record('RADICACION_PERSISTIDA_CONCILIADA', fact | {'intencionId': str(uuid4())})
        self.assertNotIn('radicacionPersistida', self.journal.status())
        with self.assertRaises(FichaError):
            self.journal.claim()
        with self.assertRaises(FichaError):
            self.journal.mark_start()

    def test_institutional_approval_projection_requires_matching_task_evidence_and_preserves_start_guard(self):
        import hashlib
        import json
        self.journal.prepare(self.payload, {'usuarioWorkflow': 'SYNTHETIC'})
        self.journal.claim();self.journal.mark_start();self.journal.checkpoint('123')
        self.journal.settle('INCIERTA',{'codigo':'INCIDENCIA_HISTORICA'})
        op=self.journal.operation()
        institution={'versionContrato':'credihoy-linix-estado-v2','solicitudId':self.sid,
            'intencionId':str(op.intent_id),'kFlujo':'123','numeroSc':'999',
            'estado':'APROBADA_EN_LINIX_PENDIENTE_FIRMA',
            'aprobacion':{'aprobada':True,'estadoSolicitud':'A','actividad':6774,'estadoActividad':'E',
                         'aprobadorReferencia':'SYNTHETIC','fecha':'2026-10-23','kSerie':'19'},
            'actividad':{'codigo':99001065,'kSerie':'20','ruta':'10003122','estado':'A','tipoEjecucion':'H'},
            'firma':{'estado':'PENDIENTE','acreditada':False},'liquidacion':{'liquidada':False,'fecha':None}}
        fact={'estado':'CONFIRMADA_EN_CA090','K_FLUJO':'123','SC':'999','intencionId':str(op.intent_id),
              'fingerprint':op.fingerprint,'observadoEn':NOW.isoformat(),
              'actividadesActivas':[{'actividad':99001065,'K_SERIE':20}]}
        def seal(value):
            value.pop('evidenciaSha256',None)
            value['evidenciaSha256']=hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
            return value
        fact['institucional']=seal(institution)
        self.journal.record('RADICACION_PERSISTIDA_CONCILIADA',fact)
        status=self.journal.status()
        self.assertEqual(status['estado'],'INCIERTA');self.assertTrue(status['STARTIntentado'])
        view=status['radicacionPersistida']['presentacion']
        self.assertTrue(view['asociado']['aprobacionAcreditada'])
        self.assertFalse(view['administrador']['nuevoStartPermitido'])
        self.assertNotIn('SYNTHETIC',str(view['asociado']))
        for field,value in (('intencionId',str(uuid4())),('firma',{'estado':'FIRMADA','acreditada':True}),
                            ('aprobacion',{'aprobada':True}),('actividad',{'codigo':99001065,'kSerie':'bad'})):
            candidate=seal(deepcopy(institution)|{field:value})
            with self.subTest(field=field):
                self.assertFalse(self.journal.presentation(fact|{'institucional':candidate},op)['asociado']['aprobacionAcreditada'])
        self.assertFalse(self.journal.presentation(fact|{'actividadesActivas':[]},op)['asociado']['aprobacionAcreditada'])

    def test_independent_connections_allow_one_intention_and_one_claim(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from .condiciones import FichaError
        barrier=Barrier(2)
        secret=b'SYNTHETIC-LOCAL-CUSTODY-ONLY'
        def prepare(index):
            db=Journal(self.journal.path,secret,request_id=self.sid)
            try:
                payload=deepcopy(self.payload)
                payload['intencionId']='00000000-0000-4000-8000-00000000000'+str(index+1)
                barrier.wait(timeout=10)
                try:db.prepare(payload,{'usuarioWorkflow':'SYNTHETIC'});return 'PREPARADA'
                except FichaError as exc:return str(exc)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:prepared=list(pool.map(prepare,range(2)))
        self.assertEqual(prepared.count('PREPARADA'),1)
        self.assertEqual(prepared.count('EXPEDIENTE_YA_TIENE_INTENCION_NO_REPETIR'),1)
        def claim(index):
            db=Journal(self.journal.path,secret,request_id=self.sid)
            try:
                barrier.wait(timeout=10)
                try:db.claim();return 'RECLAMADA'
                except FichaError as exc:return str(exc)
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:claimed=list(pool.map(claim,range(2)))
        self.assertEqual(claimed.count('RECLAMADA'),1)
        self.assertEqual(claimed.count('START_YA_RECLAMADO_NO_REPETIR'),1)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM cd6_envio').fetchone()[0],1)
        self.assertFalse(self.port.connections)

    def test_losing_preflight_cannot_block_an_already_claimed_operation(self):
        from unittest.mock import patch
        from .condiciones import FichaError
        from .worker import execute_prepared
        self.operation(self.message)
        def preflight_loses_race():
            other=Journal(self.journal.path,b'SYNTHETIC-LOCAL-CUSTODY-ONLY',request_id=self.sid)
            try:other.claim()
            finally:other.close()
            raise FichaError('SC_EQUIVALENTE_POSIBLE_CONCILIAR_ANTES_DE_START')
        with patch('tools.cd6.gateway.GatewayAdministrativoUnico') as gateway:
            gateway.return_value.preflight_duplicate.side_effect=preflight_loses_race
            result=execute_prepared(self.journal,self.port,'WORKFLOW-SINTETICO',now=NOW,verification={})
        self.assertEqual(result['estado'],'EXECUTING')
        self.assertFalse(result['STARTIntentado']);gateway.return_value.submit.assert_not_called()

    def test_timeout_never_repeats_send_after_reopen_or_reconciliation(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(self.message);self.port.timeout=True
        first=self.send();self.assertEqual(first['estado'],'INCIERTA')
        for _ in range(2):
            self.assertEqual(self.send()['estado'],'INCIERTA')
        reopened=Journal(self.journal.path,b'SYNTHETIC-LOCAL-CUSTODY-ONLY',request_id=self.sid)
        self.addCleanup(reopened.close)
        operate({'accion':'conciliar-caso','solicitudId':self.sid},reopened,self.port,'WORKFLOW-SINTETICO',now=NOW)
        self.assertEqual(sum(sql==START_SQL for sql,_ in self.port.statements),1)
        self.assertTrue(reopened.status()['STARTIntentado'])

    def test_crash_after_durable_start_guard_before_transport_never_sends_on_recovery(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(self.message)
        self.journal.claim(); self.journal.mark_start()
        reopened=Journal(self.journal.path,b'SYNTHETIC-LOCAL-CUSTODY-ONLY',request_id=self.sid)
        self.addCleanup(reopened.close)
        op=reopened.operation()
        message={'accion':'enviar-caso','solicitudId':self.sid,'autorizarStartUnico':True,
            'intencionId':op.intent_id,'fingerprint':op.fingerprint}
        for _ in range(2):
            result=operate(message,reopened,self.port,'WORKFLOW-SINTETICO',now=NOW)
            self.assertEqual(result['estado'],'INCIERTA')
            self.assertTrue(result['STARTIntentado'])
        self.assertEqual(sum(sql==START_SQL for sql,_ in self.port.statements),0)
        with self.assertRaisesRegex(Exception,'START_YA_RECLAMADO'):
            reopened.claim()

    def test_existing_bound_sc_is_reconciled_without_any_new_start(self):
        from incluirtec.digital_operations.credihoy import LINE
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(self.message)
        self.port.backend.auto_radicate=True
        self.port.backend.start_workflow(self.message['preparacion']['xml'],LINE)
        self.port.intent_name='CD4G-'+self.payload['intencionId']
        self.journal.claim();self.journal.settle('INCIERTA',{'codigo':'RESPUESTA_PREVIA_INCIERTA_SINTETICA'})
        result=self.operation({'accion':'conciliar-caso','solicitudId':self.sid})
        self.assertEqual(result['estado'],'RADICADA',result)
        self.assertEqual(result['K_FLUJO'],self.port.backend.flow_id)
        self.assertEqual(sum(sql==START_SQL for sql,_ in self.port.statements),0)
        self.assertFalse(result['STARTIntentado'])

    def setUp(self):
        self.temp = TemporaryDirectory(prefix='cd6-case-'); self.addCleanup(self.temp.cleanup)
        self.sid = str(uuid4())
        base, ficha, domains = fixture()
        payload, _ = request_final(base, ficha, domains, str(uuid4()), str(uuid4()), 'CASE-SYNTHETIC', now=NOW)
        payload['solicitudId'] = self.sid
        self.payload, self.port = payload, CasePort(payload)
        self.journal = Journal(Path(self.temp.name) / 'case.sqlite3', b'SYNTHETIC-LOCAL-CUSTODY-ONLY', request_id=self.sid)
        self.addCleanup(self.journal.close)
        self.message = case_message(payload)

    def operation(self, message):
        return operate(message, self.journal, self.port, 'WORKFLOW-SINTETICO', now=NOW)

    def send(self):
        op = self.journal.operation()
        return self.operation({'accion': 'enviar-caso', 'solicitudId': self.sid, 'autorizarStartUnico': True,
                               'intencionId': op.intent_id, 'fingerprint': op.fingerprint})

    def test_prepare_sends_nothing_then_exact_b_one_start(self):
        self.assertEqual(self.operation(self.message)['estado'], 'PREPARADA')
        self.assertFalse(self.port.connections)
        result = self.send()
        self.assertEqual(result['estado'], 'RADICADA', result)
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        starts = [binds for sql, binds in self.port.statements if sql == START_SQL]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]['n_trama'].getvalue(), self.message['preparacion']['xml'])
        self.assertNotIn('<A_TIPO_SOLI_CREDITO>', starts[0]['n_trama'].getvalue())
        self.assertIn('PERSONA SINTETICA NO REAL', starts[0]['n_trama'].getvalue())
        self.send()
        self.assertEqual(len([x for x in self.port.statements if x[0] == START_SQL]), 1)
        observations = [self.journal.decrypt(row[0]) for row in self.journal.db.execute(
            "SELECT evidencia FROM cd6_observaciones WHERE estado_observado='EVIDENCIA_ORACLE_SOLO_SELECT'")]
        self.assertEqual(len(observations), 1)
        self.assertFalse(observations[0]['n_error113']['disponible'])
        self.assertEqual(observations[0]['3883'][0]['estado'], 'E')
        self.assertEqual(len(observations[0]['CA090']), 1)
        self.assertTrue(self.journal.db.execute("SELECT 1 FROM cd6_observaciones WHERE estado_observado='COMMIT_RETORNO'").fetchone())

    def test_explicit_zero_input_quota_preserves_calculated_quote_end_to_end(self):
        from xml.etree import ElementTree as ET
        self.payload['contextoLinix']['entrada113'] = {'tipoConsulta': '2', 'modalidadSimular': 'M',
            'datoCalcularSimulacion': 'C', 'valorCuota': '0'}
        self.port = CasePort(self.payload)
        self.message = case_message(self.payload)
        self.assertEqual(self.operation(self.message)['estado'], 'PREPARADA')
        result = self.send()
        self.assertEqual(result['estado'], 'RADICADA', result)
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        starts = [b for sql, b in self.port.statements if sql == START_SQL]
        self.assertEqual(len(starts), 1)
        tree = ET.fromstring(starts[0]['n_trama'].getvalue())
        self.assertEqual(tree.findtext('./Datos/V_VALOR_CUOTA'), '0.00')
        self.assertEqual(tree.findtext('./Datos/V_VALOR_CUOTA_CAL'), self.payload['credito']['cuotaContractual'])
        self.send()
        self.assertEqual(len([b for sql, b in self.port.statements if sql == START_SQL]), 1)

    def test_changed_calculated_quote_is_never_confirmed(self):
        from dataclasses import replace
        from xml.etree import ElementTree as ET
        self.payload['contextoLinix']['entrada113'] = {'tipoConsulta': '2', 'modalidadSimular': 'M',
            'datoCalcularSimulacion': 'C', 'valorCuota': '0'}
        self.port = CasePort(self.payload)
        self.message = case_message(self.payload)
        def changed(backend):
            records = []
            for record in backend.records:
                tree = ET.fromstring(record.xml_new)
                tree.find('./Datos/V_VALOR_CUOTA_CAL').text = '1'
                records.append(replace(record, xml_new=ET.tostring(tree, encoding='unicode')))
            backend.records = records
        self.port.transform = changed
        self.operation(self.message)
        result = self.send()
        self.assertEqual(result['estado'], 'INCIERTA', result)
        self.assertEqual(sum(c.commits for c in self.port.connections), 0)

    def test_tampered_xml_conditions_references_or_version_fail_before_connection(self):
        for field in ('xml', 'xmlOriginal', 'xmlSha256', 'builderVersion', 'referencias'):
            message = deepcopy(self.message)
            message['preparacion'][field] = 'ALTERED'
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError, KeyError)):
                self.operation(message)
        self.assertFalse(self.port.connections); self.assertFalse(self.journal.exists())

    def test_missing_effects_or_3883_certification_never_prepares(self):
        for name in ('efectos', 'referencias3883'):
            message = deepcopy(self.message); del message['certificaciones'][name]
            with self.assertRaisesRegex(ValueError, 'CERTIFICACION_INSTITUCIONAL_PENDIENTE'):
                self.operation(message)
        self.assertFalse(self.journal.exists())

    def test_route_change_before_start_blocks(self):
        self.operation(self.message)
        self.port.config['ruta'][2]['I_EJECUC'] = 'A'
        result = self.send()
        self.assertEqual(result['estado'], 'BLOQUEADA')
        self.assertFalse(result['STARTIntentado'])

    def test_claim_and_cipher_remain_after_restart(self):
        self.operation(self.message)
        self.port.timeout = True
        result = self.send()
        self.assertEqual(result['estado'], 'INCIERTA')
        reopened = Journal(self.journal.path, b'SYNTHETIC-LOCAL-CUSTODY-ONLY', request_id=self.sid)
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.status()['STARTIntentado'])
        with self.assertRaises(Exception): reopened.claim()
        self.assertNotIn(b'PERSONA SINTETICA', Path(self.journal.path).read_bytes())

    def test_cross_case_cannot_open_or_modify_pilot_journal(self):
        self.operation(self.message)
        before = Path(self.journal.path).read_bytes()
        with self.assertRaisesRegex(Exception, 'JOURNAL_DE_OTRO_EXPEDIENTE'):
            Journal(self.journal.path, b'SYNTHETIC-LOCAL-CUSTODY-ONLY', request_id=SID)
        self.assertEqual(Path(self.journal.path).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'PILOTO_PROTEGIDO'):
            self.operation({'accion': 'enviar-caso', 'solicitudId': SID})

    def test_wrong_intent_or_fingerprint_never_sends(self):
        self.operation(self.message)
        with self.assertRaisesRegex(Exception, 'INTENCION_NO_COINCIDE'):
            self.operation({'accion': 'enviar-caso', 'solicitudId': self.sid, 'autorizarStartUnico': True,
                'intencionId': str(uuid4()), 'fingerprint': self.journal.operation().fingerprint})
        self.assertFalse(self.port.connections)

    def test_absent_references_and_wrong_case_authority_rejected(self):
        prepared = self.message['preparacion']
        with self.assertRaisesRegex(ValueError, 'REFERENCIAS'):
            prepare(self.payload, [], evidence=prepared['evidencias'], authorization=prepared['autorizacion'])
        authority = {**prepared['autorizacion'], 'solicitudId': str(uuid4())}
        with self.assertRaisesRegex(ValueError, 'AUTORIZACION_ESPECIFICA'):
            prepare(self.payload, prepared['referencias'], evidence=prepared['evidencias'], authorization=authority)

    def test_preventive_check_runs_again_at_exact_start_boundary(self):
        self.operation(self.message)
        from .gateway import GatewayAdministrativoUnico
        self.journal.claim()
        gateway = GatewayAdministrativoUnico(self.journal, self.port, workflow_user='WORKFLOW-SINTETICO', clock=lambda: NOW)
        conn, cursor = gateway._connect(self.port)
        self.addCleanup(lambda: gateway._dispose(conn, cursor, rollback=True))
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        from incluirtec.digital_operations.oracle_fakes import FakeVariable
        xml = FakeVariable(); xml.setvalue(0, self.message['preparacion']['xml'])
        kwargs = {'n_i_commit': 'FALSE', 'n_trama': xml, 'n_flujo': 'CD4G-' + self.payload['intencionId'],
                  'n_k_modelo': 'CREDIHOY_VAL_EMPLEADO', 'n_k_proces': 'CREDITOS', 'n_k_usuari': 'WORKFLOW-SINTETICO'}
        self.port.config['ruta'][2]['I_EJECUC'] = 'A'
        with self.assertRaisesRegex(ValueError, 'BARRERA_HUMANA'):
            cursor.execute(START_SQL, **kwargs)
        self.assertFalse(self.journal.status()['STARTIntentado'])
        self.assertFalse([x for x in self.port.statements if x[0] == START_SQL])

    def test_actual_bind_must_equal_persisted_b(self):
        self.operation(self.message); self.journal.claim()
        from .gateway import GatewayAdministrativoUnico
        from incluirtec.digital_operations.oracle_fakes import FakeVariable
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        gateway = GatewayAdministrativoUnico(self.journal, self.port, workflow_user='WORKFLOW-SINTETICO', clock=lambda: NOW)
        conn, cur = gateway._connect(self.port)
        self.addCleanup(lambda: gateway._dispose(conn, cur, rollback=True))
        xml = FakeVariable(); xml.setvalue(0, self.message['preparacion']['xmlOriginal'])
        with self.assertRaisesRegex(Exception, 'XML_BIND_DIFIERE_DEL_SELLADO'):
            cur.execute(START_SQL, n_i_commit='FALSE', n_trama=xml)
        self.assertFalse(self.journal.status()['STARTIntentado'])

    def test_new_case_cannot_checkpoint_or_reopen_the_pilot(self):
        self.operation(self.message); self.journal.claim()
        with self.assertRaisesRegex(Exception, 'FLUJO_PILOTO_PROTEGIDO'):
            self.journal.checkpoint('16087353')
        with self.assertRaisesRegex(Exception, 'NO_PUEDE_CAMBIAR_XML'):
            self.journal.reopen_autocommit_preflight(self.payload, self.journal.packet()['evidencia'])

    def test_cli_routes_new_uuid_without_loading_historical_custody(self):
        from .cli import main
        from unittest.mock import patch
        with patch('tools.cd6.cli.load') as historical, patch('tools.cd6.cli.send') as send:
            main(['estado-caso', '--solicitud', self.sid])
        historical.assert_not_called()
        send.assert_called_once_with({'accion': 'estado-caso', 'solicitudId': self.sid})

    def case_source(self):
        prepared = self.message['preparacion']
        return deepcopy({'payload': prepared['payload'], 'referencias': prepared['referencias'],
                         'evidencias': prepared['evidencias'], 'autorizacion': prepared['autorizacion']})

    def test_cli_preview_reuses_builder_without_connection_journal_or_private_output(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        from unittest.mock import patch
        from .cli import main
        source = self.case_source()
        stream = StringIO()
        with patch('tools.cd6.cli.sys.stdin', StringIO(json.dumps(source))), \
                patch('tools.cd6.cli.send') as remote, patch('tools.cd6.cli.load') as historical, \
                redirect_stdout(stream):
            self.assertEqual(main(['vista-previa-caso', '--solicitud', self.sid]), 0)
        result = json.loads(stream.getvalue())
        self.assertEqual(result['xmlSha256'], self.message['preparacion']['xmlSha256'])
        self.assertEqual(result['referenciasCapturadas'], 1)
        self.assertFalse(result['delta']['enviable'])
        self.assertFalse(result['Oracle'])
        self.assertEqual(result['START'], 0)
        self.assertNotIn(source['referencias'][0]['nombre'], stream.getvalue())
        self.assertNotIn(source['payload']['titular']['numeroDocumento'], stream.getvalue())
        self.assertNotIn('<Entrada', stream.getvalue())
        remote.assert_not_called(); historical.assert_not_called()
        self.assertFalse(self.port.connections)
        self.assertFalse(self.journal.exists())

    def test_cli_private_input_rejects_duplicate_nonfinite_invalid_and_large_json(self):
        from io import StringIO
        from unittest.mock import patch
        from .cli import read_case_input
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"secreto-no-imprimir":', ' ' * 2_000_001):
            with self.subTest(raw_length=len(raw)), patch('tools.cd6.cli.sys.stdin', StringIO(raw)):
                with self.assertRaises(Exception) as caught:
                    read_case_input()
                self.assertNotIn('secreto-no-imprimir', str(caught.exception))

    def test_offline_preparation_drops_administrative_proofs_but_preserves_consent(self):
        from .cli import case_preview, prepare_case_input
        source = self.case_source()
        source['evidencias'] = {'consentimiento': source['evidencias']['consentimiento']}
        source.pop('autorizacion')
        before = deepcopy(source)
        result = case_preview(source, self.sid)
        self.assertEqual(result['estado'], 'XML_DIAGNOSTICO_NO_APTO_PARA_ENVIO')
        self.assertEqual(result['documentosAdministrativosObligatorios'], [])
        self.assertFalse(result['enviable'])
        self.assertEqual(source, before)
        packet = prepare_case_input(source, self.sid)
        self.assertNotIn('autorizacionAdministrativa', packet['preparacion']['payload'])
        self.assertNotIn('autorizacion', packet['preparacion'])
        with self.assertRaisesRegex(Exception, 'CONTRATO_FUENTE_CASO_INVALIDO'):
            prepare_case_input(result, self.sid)
        self.assertFalse(self.journal.exists())

    def test_offline_artifacts_reuse_exact_builders_and_never_fabricate_sc(self):
        import hashlib, json
        from xml.etree import ElementTree as ET
        from .cli import case_preview
        from incluirtec.services.xml_builder import build_credihoy_references_xml
        source = self.case_source()
        result = case_preview(source, self.sid, destination=self.temp.name)
        directory = Path(result['custodiaPrivada'])
        xml113 = (directory / 'XML113_XML_DIAGNOSTICO_NO_APTO_PARA_ENVIO.xml').read_bytes()
        xml3883 = (directory / 'XML3883_REFERENCIAS_XML_DIAGNOSTICO_NO_APTO_PARA_ENVIO.xml').read_bytes()
        self.assertEqual(xml113.decode(), self.message['preparacion']['xml'])
        self.assertEqual(xml3883.decode(), build_credihoy_references_xml(source['referencias']))
        self.assertEqual(hashlib.sha256(xml113).hexdigest(), result['xmlSha256'])
        self.assertEqual(hashlib.sha256(xml3883).hexdigest(), result['xml3883']['sha256'])
        self.assertEqual(json.loads((directory / 'manifest.json').read_bytes()), result)
        tree = ET.fromstring(xml113)
        self.assertIsNone(tree.find('./Datos/A_NUMERO_SOLI_CREDITO'))
        self.assertEqual(ET.fromstring(xml3883).tag, 'R_REFERSOLCRED')
        self.assertEqual(result['xml3883']['estado'], 'FRAGMENTO_REFERENCIAS_NO_ES_ENTRADA_COMPLETA')
        other = case_preview(source, self.sid, destination=self.temp.name)
        self.assertNotEqual(result['custodiaPrivada'], other['custodiaPrivada'])
        self.assertEqual((directory / 'XML113_XML_DIAGNOSTICO_NO_APTO_PARA_ENVIO.xml').read_bytes(), xml113)
        self.assertFalse(self.port.connections)

    def test_offline_preview_still_rejects_missing_structural_data_and_cross_case(self):
        from .cli import case_preview
        source = self.case_source()
        source['payload']['credito']['cuotaContractual'] = None
        with self.assertRaises(Exception):
            case_preview(source, self.sid, destination=self.temp.name)
        source = self.case_source()
        source['referencias'][0]['codigoPais'] = '000'
        with self.assertRaisesRegex(Exception, 'REFERENCIA_UBICACION_INVALIDA'):
            case_preview(source, self.sid, destination=self.temp.name)
        with self.assertRaisesRegex(Exception, 'NO_CORRESPONDE_AL_EXPEDIENTE'):
            case_preview(self.case_source(), str(uuid4()), destination=self.temp.name)
        self.assertFalse(list(Path(self.temp.name).glob('xml-diagnostico-*')))

    def test_cli_preview_with_missing_proofs_does_not_call_preparer_or_transport(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        from unittest.mock import patch
        from .cli import main
        source = self.case_source()
        source['evidencias'] = {'consentimiento': source['evidencias']['consentimiento']}
        stream = StringIO()
        with patch('tools.cd6.cli.read_case_input', return_value=source), \
                patch('tools.cd6.cli.send') as send, redirect_stdout(stream):
            self.assertEqual(main(['vista-previa-caso', '--solicitud', self.sid]), 0)
        send.assert_not_called()
        self.assertNotIn('<Entrada', stream.getvalue())
        self.assertNotIn(source['referencias'][0]['nombre'], stream.getvalue())
        self.assertEqual(json.loads(stream.getvalue())['documentosAdministrativosObligatorios'], [])

    def test_offline_preview_preserves_pilot_request_and_intent_protection(self):
        from .cli import case_preview
        from incluirtec.digital_operations.production_preparation import PILOT_REQUEST, PILOT_INTENT
        for key, value in (('solicitudId', PILOT_REQUEST), ('intencionId', PILOT_INTENT)):
            source = self.case_source()
            source['payload'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(Exception, 'ENSAYO_REQUIERE_EXPEDIENTE'):
                case_preview(source, source['payload']['solicitudId'], destination=self.temp.name)
        self.assertFalse(list(Path(self.temp.name).glob('xml-diagnostico-*')))

    def test_cli_private_file_loads_bom_without_copying_or_mutating_source(self):
        from .cli import read_case_input
        path = Path(self.temp.name) / 'privado.json'
        content = b'\xef\xbb\xbf{"dato":"SINTETICO"}'
        path.write_bytes(content)
        self.assertEqual(read_case_input(path), {'dato': 'SINTETICO'})
        self.assertEqual(path.read_bytes(), content)

    def test_cli_source_rejects_other_case_pilot_and_hashes_references_itself(self):
        from .cli import prepare_case_input
        source = self.case_source()
        with self.assertRaisesRegex(Exception, 'NO_CORRESPONDE_AL_EXPEDIENTE'):
            prepare_case_input(source, str(uuid4()))
        source['payload']['solicitudId'] = SID
        with self.assertRaisesRegex(Exception, 'ENSAYO_REQUIERE_EXPEDIENTE'):
            prepare_case_input(source, self.sid)
        source = self.case_source()
        source['referencias'][0]['nombre'] = 'OTRA PERSONA SINTETICA'
        prepared = prepare_case_input(source, self.sid)['preparacion']
        self.assertEqual(prepared['referenciasSha256'], digest(canonical(source['referencias'])))
        self.assertNotEqual(prepared['referenciasSha256'], source['evidencias']['referencias']['sha256'])

    def test_cli_v2_preparation_dispatches_to_existing_worker_without_certificates(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        from unittest.mock import patch
        from .cli import main
        source = self.case_source()
        source.update({key: self.message[key] for key in ('perfilCore', 'preflight')})
        source['evidencias'].pop('elegibilidadAdministrativa')
        source.pop('autorizacion')
        stream = StringIO()
        with patch('tools.cd6.cli.read_case_input', return_value=source), \
                patch('tools.cd6.cli.send') as remote, redirect_stdout(stream):
            self.assertEqual(main(['preparar-caso', '--solicitud', self.sid, '--entrada',
                                   str(Path(self.temp.name) / 'privado.json')]), 0)
        sent = remote.call_args.args[0]
        self.assertEqual(sent['accion'], 'preparar-caso')
        self.assertEqual(sent['preparacion']['payload']['intencionId'], self.payload['intencionId'])
        self.assertEqual(set(sent), {'accion', 'solicitudId', 'preparacion', 'perfilCore', 'preflight'})
        result = json.loads(stream.getvalue())
        self.assertEqual(result['estado'], 'PREPARACION_TECNICA_LOCAL_NO_ENVIADA')
        self.assertTrue(Path(result['archivoPrivado']).is_file())
        self.assertFalse(self.journal.exists())

    def test_cli_legacy_sealed_packet_keeps_existing_transport_when_boundary_valid(self):
        from unittest.mock import patch
        from .cli import main
        source = {key: value for key, value in self.message.items() if key not in ('accion', 'solicitudId')}
        with patch('tools.cd6.cli.read_case_input', return_value=source), \
                patch('tools.cd6.cli.instante', return_value=NOW), patch('tools.cd6.cli.send') as remote:
            self.assertEqual(main(['preparar-caso', '--solicitud', self.sid, '--entrada', 'privado.json']), 0)
        self.assertEqual(remote.call_args.args[0], self.message)

    def test_v2_contract_has_no_administrative_permission_or_certification_fields(self):
        from .cli import prepare_case_input
        from incluirtec.digital_operations.contracts import CREDIHOY_TECHNICAL_VERSION
        source = self.case_source()
        source.pop('autorizacion')
        source['payload'].pop('autorizacionAdministrativa')
        source['evidencias'] = {'consentimiento': source['evidencias']['consentimiento']}
        source['payload']['evidenciaTasa'].pop('referenciaCertificacion')
        prepared = prepare_case_input(source, self.sid)['preparacion']
        self.assertEqual(prepared['payload']['versionContrato'], CREDIHOY_TECHNICAL_VERSION)
        self.assertEqual(set(prepared['payload']['evidenciaTasa']), {'unidad', 'campoXml'})
        for field in ('autorizacionAdministrativa', 'evidenciaDecision'):
            self.assertNotIn(field, prepared['payload'])
        for field in ('autorizacion', 'grantAdministrativo', 'certificaciones'):
            self.assertNotIn(field, prepared)
        self.assertFalse(prepared['consentimientosLegalesEvaluados'])
        self.assertEqual(verify(prepared), prepared['xml'])

    def test_v2_still_rejects_missing_or_invalid_consent_support(self):
        from .cli import prepare_case_input
        for proof in (None, {}, {'referencia': '', 'sha256': '0' * 64},
                      {'referencia': 'SYNTHETIC', 'sha256': 'not-a-hash'}):
            source = self.case_source()
            source['evidencias'] = {'consentimiento': proof}
            with self.subTest(proof=proof), self.assertRaisesRegex(ValueError, 'CONSENTIMIENTO'):
                prepare_case_input(source, self.sid)

    def test_v2_prepares_same_journal_without_documentary_grants_or_oracle(self):
        from unittest.mock import patch
        message = technical_message(self.payload)
        with patch('tools.cd6.grant.crear', side_effect=AssertionError('NO_GRANT_V2')):
            result = self.operation(message)
        self.assertEqual(result['estado'], 'PREPARADA')
        self.assertEqual(result['intencionId'], self.payload['intencionId'])
        self.assertEqual(self.journal.packet()['request'], message['preparacion']['payload'])
        evidence = self.journal.packet()['evidencia']
        for key in ('autorizacion', 'grantAdministrativo', 'certificaciones', 'elegibilidadAdministrativa'):
            self.assertNotIn(key, evidence)
        self.assertFalse(self.port.connections)

    def test_v2_existing_gateway_reaches_exact_start_binds_and_dynamic_sc_3883(self):
        from unittest.mock import patch
        from xml.etree import ElementTree as ET
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.payload['credito'].update(montoSolicitado='400000.00', plazoMeses=2,
            tasaNominalAnual='16.67', cuotaContractual='204448.00')
        self.port = CasePort(self.payload)
        self.message = technical_message(self.payload)
        self.port.backend.flow_id = '29999992'
        self.port.backend.application_number = '26999000042'
        self.operation(self.message)
        with patch('tools.cd6.grant.validar', side_effect=AssertionError('NO_GRANT_V2')):
            result = self.send()
        self.assertEqual(result['estado'], 'RADICADA', result)
        starts = [binds for sql, binds in self.port.statements if sql == START_SQL]
        self.assertEqual(len(starts), 1)
        self.assertIn('LINIX.PK_WF_FLUJO.PR_INICIAR_FLUJO_RET', START_SQL)
        self.assertEqual(starts[0]['n_flujo'], 'CD4G-' + self.payload['intencionId'])
        self.assertEqual(starts[0]['n_trama'].getvalue(), self.message['preparacion']['xml'])
        self.assertEqual(starts[0]['n_k_modelo'], 'CREDIHOY_VAL_EMPLEADO')
        self.assertEqual(starts[0]['n_i_commit'], 'FALSE')
        self.assertIsNone(ET.fromstring(starts[0]['n_trama'].getvalue()).find('./Datos/A_NUMERO_SOLI_CREDITO'))
        self.assertEqual(result['K_FLUJO'], '29999992')
        self.assertEqual(result['resultado']['application_number'], '26999000042')
        evidence = self.journal.decrypt(self.journal.db.execute(
            "SELECT evidencia FROM cd6_observaciones WHERE estado_observado='EVIDENCIA_ORACLE_SOLO_SELECT'").fetchone()[0])
        entry = ET.fromstring(evidence['3883'][0]['entrada'])
        self.assertEqual(entry.findtext('./Datos/A_NUMERO_SOLI_CREDITO'), '26999000042')
        self.assertEqual(entry.findtext('./Datos/A_TIPO_SOLI_CREDITO'), 'SC')
        self.assertEqual(evidence['CA090'][0]['K_NUMDOC'], '26999000042')
        self.assertEqual(self.send()['estado'], 'RADICADA')
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 1)
        self.assertEqual(self.port.backend.executions, 0)

    def test_v2_real_port_is_blocked_before_connection_not_by_documents(self):
        from unittest.mock import Mock
        self.operation(technical_message(self.payload))
        self.port = Mock()
        result = self.send()
        self.assertEqual(result['estado'], 'BLOQUEADA')
        self.assertEqual(result['resultado']['codigo'], 'V2_PUERTO_SIN_CAPACIDAD_SEGURA')
        self.port.open_connection.assert_not_called()
        self.assertFalse(result['STARTIntentado'])
        self.assertFalse(result['STARTReclamado'])

    def test_v2_simulated_port_cannot_supply_a_real_driver_connection(self):
        from unittest.mock import Mock
        from incluirtec.digital_operations.credihoy_oracle import SimulatedOraclePort
        from .gateway import GatewayAdministrativoUnico
        self.operation(technical_message(self.payload))
        class InvalidSimulation(SimulatedOraclePort):
            def open_connection(inner):
                return native
        native = Mock()
        port = InvalidSimulation()
        adapter = GatewayAdministrativoUnico(self.journal, port, workflow_user='WORKFLOW-SINTETICO', clock=lambda: NOW)
        with self.assertRaisesRegex(Exception, 'V2_PUERTO_SIN_CAPACIDAD_SEGURA'):
            adapter.preflight_duplicate()
        native.cursor.assert_not_called()
        native.close.assert_called_once()
        native.rollback.assert_called_once()

    def test_v2_timeout_reconciles_without_repeating_start_after_reopen(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(technical_message(self.payload))
        self.port.timeout = True
        result = self.send()
        self.assertEqual(result['estado'], 'INCIERTA', result)
        self.assertTrue(result['STARTIntentado'])
        self.assertEqual(self.send()['estado'], 'INCIERTA')
        reopened = Journal(self.journal.path, b'SYNTHETIC-LOCAL-CUSTODY-ONLY', request_id=self.sid)
        self.addCleanup(reopened.close)
        operate({'accion': 'conciliar-caso', 'solicitudId': self.sid}, reopened, self.port, 'WORKFLOW-SINTETICO', now=NOW)
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 1)

    def readonly_port(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from incluirtec.digital_operations.oracle_readonly import RealOracleReadPort
        native_port = self.port
        driver = SimpleNamespace(__name__='oracledb', __version__='SYNTHETIC',
            NUMBER=native_port.number_type, CLOB=native_port.clob_type)
        infrastructure = patch('incluirtec.digital_operations.oracle_readonly._infrastructure',
            return_value=(native_port.open_connection, driver, 'LAB_READER'))
        infrastructure.start()
        self.addCleanup(infrastructure.stop)
        return RealOracleReadPort(call_timeout_ms=15000)

    def test_v2_real_read_connector_runs_preflight_but_blocks_start_before_claim(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        native = self.port
        self.operation(technical_message(self.payload))
        self.port = self.readonly_port()
        result = self.send()
        self.assertEqual(result['estado'], 'BLOQUEADA', result)
        self.assertEqual(result['resultado']['codigo'], 'V2_EFECTOS_FINANCIEROS_INTERNOS_NO_CONTENIDOS')
        self.assertTrue(native.connections)
        self.assertTrue(all(c.closed for c in native.connections))
        self.assertFalse(result['STARTReclamado'])
        self.assertFalse(result['STARTIntentado'])
        self.assertEqual(sum(sql == START_SQL for sql, _ in native.statements), 0)
        self.assertEqual(sum(c.commits for c in native.connections), 0)

    def test_v2_real_read_connector_supports_reconciliation_without_new_start(self):
        from incluirtec.digital_operations.credihoy import LINE
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        native = self.port
        message = technical_message(self.payload)
        self.operation(message)
        native.backend.auto_radicate = True
        native.backend.start_workflow(message['preparacion']['xml'], LINE)
        native.intent_name = 'CD4G-' + self.payload['intencionId']
        self.journal.claim()
        self.journal.settle('INCIERTA', {'codigo': 'SYNTHETIC_UNKNOWN'})
        self.port = self.readonly_port()
        result = self.operation({'accion': 'conciliar-caso', 'solicitudId': self.sid})
        self.assertEqual(result['estado'], 'RADICADA', result)
        self.assertFalse(result['STARTIntentado'])
        self.assertEqual(sum(sql == START_SQL for sql, _ in native.statements), 0)
        self.assertEqual(sum(c.commits for c in native.connections), 0)

    def test_v2_read_connector_preserves_existing_private_diagnostic(self):
        from incluirtec.digital_operations.credihoy import LINE
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        native = self.port
        message = technical_message(self.payload)
        self.operation(message)
        native.backend.auto_radicate = True
        native.backend.start_workflow(message['preparacion']['xml'], LINE)
        self.journal.claim()
        self.journal.checkpoint(native.backend.flow_id)
        self.journal.settle('INCIERTA', {'codigo': 'SYNTHETIC_UNKNOWN'})
        self.port = self.readonly_port()
        result = self.operation({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        self.assertEqual(result['codigo'], 'DIAGNOSTICO_CIFRADO_SIN_START')
        self.assertEqual(result['estado'], 'INCIERTA')
        self.assertEqual(sum(sql == START_SQL for sql, _ in native.statements), 0)
        self.assertEqual(sum(c.commits for c in native.connections), 0)

    def test_v2_automatic_6964_is_not_a_configuration_requirement_for_human_stop(self):
        from incluirtec.digital_operations.production_preflight import require_configuration
        config = configuration()
        config['ruta'][2]['I_EJECUC'] = 'A'
        observed = {'configuracion': config, 'sha256': digest(canonical(config))}
        self.assertTrue(require_configuration(observed))
        with self.assertRaisesRegex(ValueError, 'BARRERA_HUMANA_6964_NO_ACREDITADA'):
            require_configuration(observed, legacy_cut=True)
        self.operation(technical_message(self.payload, config))
        self.port.config = config
        native = self.port
        self.port = self.readonly_port()
        result = self.send()
        self.assertEqual(result['resultado']['codigo'], 'V2_EFECTOS_FINANCIEROS_INTERNOS_NO_CONTENIDOS')
        self.assertFalse(result['STARTIntentado'])
        self.assertEqual(sum(c.commits for c in native.connections), 0)

    def test_v2_reconciliation_reports_active_4965_without_claiming_cut_at_6964(self):
        from incluirtec.digital_operations.credihoy import LINE
        native = self.port
        message = technical_message(self.payload)
        self.operation(message)
        native.backend.auto_radicate = True
        native.backend.start_workflow(message['preparacion']['xml'], LINE)
        native.intent_name = 'CD4G-' + self.payload['intencionId']
        native.tasks = [(1, 113, 10002896, 'E', 'A'), (2, 3883, 10002896, 'E', 'A'),
            (3, 6964, 10002896, 'E', 'A'), (4, 6783, 10002896, 'E', 'A'),
            (5, 6783, 10002896, 'E', 'A'), (6, 4965, 10002897, 'A', 'H')]
        self.journal.claim()
        self.journal.settle('INCIERTA', {'codigo': 'SYNTHETIC_UNKNOWN'})
        self.port = self.readonly_port()
        result = self.operation({'accion': 'conciliar-caso', 'solicitudId': self.sid})
        self.assertEqual(result['estado'], 'INCIERTA', result)
        self.assertEqual(result['resultado']['activity_current'], 4965)
        self.assertEqual(result['resultado']['candidate_application_number'], native.backend.application_number)
        self.assertFalse(result['resultado']['cut_verified'])
        self.assertFalse(result['STARTIntentado'])

    def test_v2_timeout_with_committed_sc_reconciles_once(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(technical_message(self.payload))
        self.port.timeout = True
        self.port.internal_commit = True
        result = self.send()
        self.assertEqual(result['estado'], 'RADICADA', result)
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 1)

    def test_v2_prior_flow_of_same_intention_is_recovered_without_start(self):
        from incluirtec.digital_operations.credihoy import LINE
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        message = technical_message(self.payload)
        self.operation(message)
        self.port.backend.auto_radicate = True
        self.port.backend.start_workflow(message['preparacion']['xml'], LINE)
        self.port.intent_name = 'CD4G-' + self.payload['intencionId']
        result = self.send()
        self.assertEqual(result['estado'], 'RADICADA', result)
        self.assertEqual(result['K_FLUJO'], self.port.backend.flow_id)
        self.assertFalse(result['STARTIntentado'])
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 0)

    def test_v2_sc_or_reference_change_in_3883_never_confirms_radication(self):
        from xml.etree import ElementTree as ET
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(technical_message(self.payload))
        def changed(text):
            root = ET.fromstring(text)
            root.find('./Datos/A_NUMERO_SOLI_CREDITO').text = '26999000999'
            return ET.tostring(root, encoding='unicode')
        self.port.reference_input_transform = changed
        self.port.internal_commit = True
        result = self.send()
        self.assertEqual(result['estado'], 'INCIERTA', result)
        private = self.journal.decrypt(self.journal.db.execute('SELECT result FROM cd6_envio').fetchone()[0])
        self.assertIn('SC_3883_DIFIERE_DE_113', private['mensajePrivado'])
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 1)
        self.assertEqual(self.send()['estado'], 'INCIERTA')
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 1)

    def test_v2_changed_reference_in_3883_is_not_accepted(self):
        from xml.etree import ElementTree as ET
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(technical_message(self.payload))
        def changed(text):
            root = ET.fromstring(text)
            root.find('./Datos/R_REFERSOLCRED').clear()
            return ET.tostring(root, encoding='unicode')
        self.port.reference_input_transform = changed
        self.port.internal_commit = True
        self.assertEqual(self.send()['estado'], 'INCIERTA')
        private = self.journal.decrypt(self.journal.db.execute('SELECT result FROM cd6_envio').fetchone()[0])
        self.assertIn('REFERENCIAS_3883_DIFIEREN_DEL_XML_SELLADO', private['mensajePrivado'])
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 1)

    def test_v2_concurrent_executors_never_create_two_effective_starts(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from unittest.mock import patch
        from .condiciones import FichaError
        from .worker import execute_prepared
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(technical_message(self.payload))
        barrier = Barrier(2)
        original_claim = Journal.claim
        ports = [CasePort(self.payload), CasePort(self.payload)]
        def synchronized_claim(journal):
            barrier.wait(timeout=10)
            return original_claim(journal)
        def execute(index):
            journal = Journal(self.journal.path, b'SYNTHETIC-LOCAL-CUSTODY-ONLY', request_id=self.sid)
            try:
                return execute_prepared(journal, ports[index], 'WORKFLOW-SINTETICO', now=NOW, verification={})['estado']
            except FichaError as exc:
                return str(exc)
            finally:
                journal.close()
        with patch.object(Journal, 'claim', synchronized_claim), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, range(2)))
        self.assertEqual(results.count('RADICADA'), 1, results)
        self.assertEqual(results.count('START_YA_RECLAMADO_NO_REPETIR'), 1, results)
        self.assertEqual(sum(sql == START_SQL for port in ports for sql, _ in port.statements), 1)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM cd6_envio').fetchone()[0], 1)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM cd6_start_guard').fetchone()[0], 1)

    def test_v2_changed_xml_is_rejected_before_journal_or_connection(self):
        message = technical_message(self.payload)
        message['preparacion']['xml'] += ' '
        with self.assertRaisesRegex(ValueError, 'XML_CONDICIONES_O_VERSION_CAMBIARON'):
            self.operation(message)
        self.assertFalse(self.journal.exists())
        self.assertFalse(self.port.connections)

    def test_v2_legacy_lab_boundary_is_not_reused_when_6964_becomes_automatic(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(technical_message(self.payload))
        self.port.config['ruta'][2]['I_EJECUC'] = 'A'
        result = self.send()
        self.assertEqual(result['estado'], 'BLOQUEADA')
        self.assertFalse(result['STARTReclamado'])
        self.assertFalse(result['STARTIntentado'])
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 0)

    def test_v2_read_capability_refuses_start_and_commit_before_native_driver(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        native = self.port
        port = self.readonly_port()
        with port.open_connection() as connection:
            cursor = connection.cursor()
            with self.assertRaisesRegex(Exception, 'CREDIHOY_SQL_NO_PERMITIDO'):
                cursor.execute(START_SQL)
            with self.assertRaisesRegex(Exception, 'CREDIHOY_MUTACION_NO_AUTORIZADA'):
                connection.commit()
        self.assertEqual(sum(sql == START_SQL for sql, _ in native.statements), 0)
        self.assertEqual(sum(c.commits for c in native.connections), 0)

    def test_preflight_follows_linked_routes_without_changing_workflow_or_journal(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.port.config['ruta'][2]['I_EJECUC'] = 'A'
        for order in (4, 5):
            row = deepcopy(self.port.config['ruta'][2])
            row.update(K_SERIE=7500 + order, Q_TRANSI=order, K_IDACTI=6783,
                N_ACCION='PK_WF_MOVIMIENTO.PR_EJECUTA_EVALUACION')
            self.port.config['ruta'].append(row)
        self.port.config['caminos'] = [{'K_SERIE': 7505, 'K_IDCAMI': i, 'I_DEFAUL': 'N',
            'N_CONDIC': 'SYNTHETIC', 'K_IDRUTA_CAM': r, 'K_IDACTI_CAM': None, 'Q_PRIORI': 1}
            for i, r in ((4256, 10002927), (4257, 10002926))]
        def branch():
            row = deepcopy(configuration()['ruta'][2])
            row.update(K_IDACTI=4965, Q_TRANSI=1)
            return {'ruta': [row], 'caminos': [{'K_SERIE': row['K_SERIE'], 'K_IDRUTA_CAM': 99999999}]}
        self.port.child_routes = {10002927: branch(), 10002926: branch()}
        result = self.operation({'accion': 'preflight-caso', 'solicitudId': self.sid})
        self.assertEqual(result['estado'], 'PREFLIGHT_SOLO_SELECT')
        data = result['configuracion']
        self.assertEqual({row['K_IDRUTA'] for row in data['rutasDerivadas']}, {10002927, 10002926})
        self.assertFalse(data['rutasDerivadasIncompletas'])
        self.assertEqual(result['sha256'], digest(canonical(data)))
        self.assertFalse(self.journal.exists())
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 0)
        self.assertEqual(sum(c.commits for c in self.port.connections), 0)

    def test_v2_workflow_actor_cannot_be_replaced(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(technical_message(self.payload))
        op = self.journal.operation()
        message = {'accion': 'enviar-caso', 'solicitudId': self.sid, 'autorizarStartUnico': True,
            'intencionId': op.intent_id, 'fingerprint': op.fingerprint}
        result = operate(message, self.journal, self.port, 'OTRO-USUARIO-SINTETICO', now=NOW)
        self.assertEqual(result['estado'], 'INCIERTA')
        self.assertFalse(result['STARTIntentado'])
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 0)

    def test_v2_commit_response_lost_is_reconciled_not_resent(self):
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(technical_message(self.payload))
        self.port.commit_failure = 'AFTER'
        self.assertEqual(self.send()['estado'], 'RADICADA')
        self.assertEqual(sum(sql == START_SQL for sql, _ in self.port.statements), 1)

    def test_v2_new_cases_and_amounts_are_parametric_without_documentary_grants(self):
        from .cli import prepare_case_input
        from xml.etree import ElementTree as ET
        for amount, sid, intent in (('400000.00', str(uuid4()), str(uuid4())),
                                    ('500000.00', str(uuid4()), str(uuid4()))):
            source = self.case_source()
            source.pop('autorizacion')
            source['payload'].pop('autorizacionAdministrativa')
            source['payload'].update(solicitudId=sid, intencionId=intent)
            source['payload']['credito']['montoSolicitado'] = amount
            source['evidencias'] = {'consentimiento': source['evidencias']['consentimiento']}
            prepared = prepare_case_input(source, sid)['preparacion']
            self.assertEqual(prepared['payload']['solicitudId'], sid)
            self.assertEqual(prepared['payload']['intencionId'], intent)
            self.assertEqual(ET.fromstring(verify(prepared)).findtext('./Datos/V_MONTOSOLICITADO'), amount)

    def test_v2_local_artifact_hash_integrity_and_no_automatic_send(self):
        from .cli import prepare_case_input, store_technical_preparation, read_case_input
        from hashlib import sha256
        packet = prepare_case_input(self.case_source(), self.sid)
        result = store_technical_preparation(packet, self.temp.name)
        raw = Path(result['archivoPrivado']).read_bytes()
        self.assertEqual(sha256(raw).hexdigest(), result['preparacionSha256'])
        stored = read_case_input(Path(result['archivoPrivado']))
        self.assertEqual(verify(stored['preparacion']), packet['preparacion']['xml'])
        self.assertEqual(result['intencionId'], self.payload['intencionId'])
        self.assertFalse(result['journalRemotoCreado'])
        self.assertFalse(result['Oracle'])
        self.assertEqual(result['START'], 0)
        for key in ('xml', 'referencias', 'fingerprint', 'grantAdministrativo'):
            self.assertNotIn(key, result)

    def test_legacy_uncertain_journal_reconciles_without_rewriting_old_builder_evidence(self):
        from .grant import crear
        from incluirtec.digital_operations.credihoy import LINE
        old = deepcopy(self.message['preparacion'])
        old['builderVersion'] = {key: '0' * 64 for key in old['builderVersion']}
        payload = old['payload']
        grant = crear(payload, old['autorizacion'], 'WORKFLOW-SINTETICO', NOW)
        grant['xmlSha256'] = old['xmlSha256']
        evidence = {'preparacion': old, 'autorizacion': old['autorizacion'],
            'perfilCore': self.message['perfilCore'], 'certificaciones': self.message['certificaciones'],
            'preflight': self.message['preflight'], 'xml': old['xml'], 'xmlSha256': old['xmlSha256'],
            'usuarioWorkflow': 'WORKFLOW-SINTETICO', 'grantAdministrativo': grant}
        self.journal.prepare(payload, evidence)
        self.port.backend.auto_radicate = True
        self.port.backend.start_workflow(old['xml'], LINE)
        self.port.intent_name = 'CD4G-' + payload['intencionId']
        self.journal.claim()
        self.journal.settle('INCIERTA', {'codigo': 'INCERTIDUMBRE_LEGADA_SINTETICA'})
        result = self.operation({'accion': 'conciliar-caso', 'solicitudId': self.sid})
        self.assertEqual(result['estado'], 'RADICADA', result)
        self.assertEqual(self.journal.packet()['evidencia']['preparacion'], old)
        self.assertEqual(self.journal.operation().intent_id, payload['intencionId'])
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.assertFalse([sql for sql, _ in self.port.statements if sql == START_SQL])

    def test_diagnostic_requires_existing_checkpoint_without_connection_or_start(self):
        with self.assertRaisesRegex(Exception, 'INTENCION_EXISTENTE'):
            self.operation({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        self.operation(self.message)
        with self.assertRaisesRegex(Exception, 'CHECKPOINT_DISTINTO'):
            self.operation({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        self.assertFalse(self.port.connections)

    def test_diagnostic_cli_and_worker_share_case_dispatch_not_pilot_dispatch(self):
        from unittest.mock import patch
        from .cli import main
        from .worker import CASE_ACTIONS
        self.assertIn('diagnosticar-caso', CASE_ACTIONS)
        with patch('tools.cd6.cli.send') as remote, patch('tools.cd6.cli.load') as historical:
            main(['diagnosticar-caso', '--solicitud', self.sid])
        remote.assert_called_once_with({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        historical.assert_not_called()

    def test_cli_accepts_persisted_radication_fact_and_keeps_raw_xml_private(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from .cli import send
        fact = {'estado': 'CONFIRMADA_EN_CA090', 'K_FLUJO': '900001', 'SC': '900002',
                'workflow': 'CON_INCIDENCIA', 'STARTNuevos': 0}
        response = {'estado': 'INCIERTA', 'radicacionPersistida': fact,
                    'detallePrivado': {'xml': 'CONTENIDO PRIVADO SINTETICO'}}
        seal = Mock(); stream = StringIO()
        remote = SimpleNamespace(returncode=0, stdout=json.dumps(response).encode())
        with patch.dict(sys.modules, {'credihoy_focal_crypto': SimpleNamespace(seal=seal)}), \
                patch('tools.cd6.cli.bundle'), patch('tools.cd6.cli.subprocess.run', return_value=remote) as run, \
                redirect_stdout(stream):
            send({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        text = stream.getvalue()
        self.assertNotIn('AMBIGUA', text)
        self.assertNotIn('CONTENIDO PRIVADO', text)
        self.assertEqual(json.loads(text.splitlines()[0])['radicacionPersistida'], fact)
        self.assertIn('CONTENIDO PRIVADO', seal.call_args.args[2]['respuestaPrivada'])
        self.assertEqual(run.call_count, 2)
        self.assertNotIn('enviar-caso', str(run.call_args_list))

    def test_diagnostic_preserves_state_and_seals_xml_controls_and_error_details(self):
        from incluirtec.digital_operations.credihoy import LINE
        self.operation(self.message)
        self.port.backend.auto_radicate = True
        self.port.backend.start_workflow(self.message['preparacion']['xml'], LINE)
        self.journal.claim(); self.journal.checkpoint(self.port.backend.flow_id)
        self.journal.settle('INCIERTA', {'codigo': 'INCERTIDUMBRE_SINTETICA',
                                       'mensajePrivado': 'NO MOSTRAR DATO PRIVADO SINTETICO'})
        self.port.error_details = [('ORA-20000: error sintetico privado',)]
        previous = self.journal.status()
        result = self.operation({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        self.assertEqual(result['estado'], 'INCIERTA')
        after = self.journal.status()
        fact = after.pop('radicacionPersistida')
        self.assertEqual(after, previous)
        self.assertEqual(fact['estado'], 'CONFIRMADA_EN_CA090')
        self.assertEqual(fact['STARTNuevos'], 0)
        self.assertNotIn('ORA-20000', str(result))
        self.assertNotIn('NO MOSTRAR', str(result))
        observations = [self.journal.decrypt(row[0]) for row in self.journal.db.execute(
            "SELECT evidencia FROM cd6_observaciones WHERE estado_observado='EVIDENCIA_ORACLE_SOLO_SELECT'")]
        self.assertEqual(len(observations), 1)
        self.assertIn('ORA-20000', observations[0]['erroresDetalle'][0]['ERROR_SINTETICO'])
        self.assertFalse(observations[0]['erroresDetalleIncompleto'])
        self.assertEqual(observations[0]['controlesCA090'][0]['filas'][0][:2], ['N', 'N'])
        self.assertTrue(observations[0]['113'][0]['xml_input'])
        self.assertEqual(observations[0]['3883'][0]['estado'], 'E')
        self.assertFalse(self.journal.status()['STARTIntentado'])
        self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql, _ in self.port.statements))
        self.assertTrue(all(connection.closed for connection in self.port.connections))

    def test_diagnostic_marks_truncated_errors_in_private_evidence(self):
        from incluirtec.digital_operations.credihoy import LINE
        self.operation(self.message)
        self.port.backend.auto_radicate = True
        self.port.backend.start_workflow(self.message['preparacion']['xml'], LINE)
        self.journal.claim(); self.journal.checkpoint(self.port.backend.flow_id)
        self.port.error_details = [('ERROR SINTETICO',)] * 101
        self.operation({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        evidence = self.journal.decrypt(self.journal.db.execute(
            "SELECT evidencia FROM cd6_observaciones WHERE estado_observado='EVIDENCIA_ORACLE_SOLO_SELECT'").fetchone()[0])
        self.assertTrue(evidence['erroresDetalleIncompleto'])
        self.assertEqual(len(evidence['erroresDetalle']), 100)

    def test_persisted_sc_with_workflow_error_is_not_a_new_start_or_a_completed_cut(self):
        from incluirtec.digital_operations.credihoy import LINE
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(self.message)
        self.port.backend.auto_radicate = True
        self.port.backend.start_workflow(self.message['preparacion']['xml'], LINE)
        self.port.intent_name = 'CD4G-' + self.payload['intencionId']
        self.journal.claim(); self.journal.mark_start(); self.journal.checkpoint(self.port.backend.flow_id)
        self.journal.settle('INCIERTA', {'codigo': 'INCIDENCIA_POSTERIOR_SINTETICA'})
        self.port.tasks = [(1, 113, 10002896, 'E', 'A'), (2, 3883, 10002896, 'E', 'A'),
                           (3, 6964, 10002896, 'E', 'A'), (4, 6783, 10002896, 'A', 'A')]
        self.port.error_count = 2
        self.port.evaluation_rows = [(4, 'A', 4, '<Entrada/>', '<Entrada/>', '<Salida/>')]
        previous = self.journal.operation()
        for _ in range(2):
            result = self.operation({'accion': 'conciliar-caso', 'solicitudId': self.sid})
            self.assertEqual(result['estado'], 'INCIERTA')
            fact = result['radicacionPersistida']
            self.assertEqual(fact['estado'], 'CONFIRMADA_EN_CA090')
            self.assertEqual(fact['SC'], self.port.backend.applications[0]['K_NUMDOC'])
            self.assertEqual(fact['workflow'], 'CON_INCIDENCIA')
            self.assertEqual(fact['actividadesActivas'], [{'actividad': 6783, 'K_SERIE': 4}])
            self.assertEqual(fact['erroresWorkflow'], 2)
            self.assertEqual(fact['STARTNuevos'], 0)
        self.assertTrue(result['STARTIntentado'])
        self.assertEqual(self.journal.operation().fingerprint, previous.fingerprint)
        self.assertEqual(self.journal.operation().intent_id, previous.intent_id)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM cd6_start_guard').fetchone()[0], 1)
        with self.assertRaisesRegex(Exception, 'START_YA_RECLAMADO'):
            self.journal.claim()
        self.assertFalse(any(sql == START_SQL for sql, _ in self.port.statements))
        raw = self.journal.last_evidence('EVIDENCIA_ORACLE_SOLO_SELECT')
        self.assertEqual(raw['6783'][0]['salida'], '<Salida/>')
        self.assertNotIn('A_CODIGO_RESPUESTA', str(raw['6783']))

    def test_sc_of_another_holder_is_never_recorded_as_persisted_for_this_case(self):
        from incluirtec.digital_operations.credihoy import LINE
        self.operation(self.message)
        self.port.backend.auto_radicate = True
        self.port.backend.start_workflow(self.message['preparacion']['xml'], LINE)
        self.journal.claim(); self.journal.mark_start(); self.journal.checkpoint(self.port.backend.flow_id)
        self.journal.settle('INCIERTA', {'codigo': 'INCIDENCIA_SINTETICA'})
        self.operation({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        self.assertEqual(self.journal.status()['radicacionPersistida']['estado'], 'CONFIRMADA_EN_CA090')
        self.port.backend.applications[0]['K_IDTERC'] = '999999999999'
        self.operation({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        fact = self.journal.status()['radicacionPersistida']
        self.assertEqual(fact['estado'], 'NO_CONFIRMADA')
        self.assertNotIn('SC', fact)
        self.assertEqual(self.journal.status()['estado'], 'INCIERTA')
        self.assertTrue(self.journal.status()['STARTIntentado'])

    def test_central_task_is_read_as_pending_without_creating_a_report_or_repeating_start(self):
        from incluirtec.digital_operations.credihoy import LINE
        from incluirtec.digital_operations.credihoy_oracle import START_SQL
        self.operation(self.message)
        self.port.backend.auto_radicate = True
        self.port.backend.start_workflow(self.message['preparacion']['xml'], LINE)
        self.journal.claim(); self.journal.mark_start(); self.journal.checkpoint(self.port.backend.flow_id)
        self.journal.settle('INCIERTA', {'codigo': 'INCIDENCIA_HISTORICA_SINTETICA'})
        self.port.tasks = [(1, 113, 10002896, 'E', 'A'), (2, 3883, 10002896, 'E', 'A'),
                           (3, 6964, 10002896, 'E', 'A'), (4, 6783, 10002896, 'E', 'A'),
                           (5, 6735, 10002926, 'E', 'A'), (6, 4965, 10002897, 'A', 'H')]
        self.port.central_rows = [(6, 'A', 1, '<Entrada/>', '<Entrada/>', '<Salida/>')]
        result = self.operation({'accion': 'diagnosticar-caso', 'solicitudId': self.sid})
        self.assertEqual(result['estado'], 'INCIERTA')
        self.assertTrue(result['STARTIntentado'])
        evidence = self.journal.last_evidence('EVIDENCIA_ORACLE_SOLO_SELECT')
        self.assertEqual(evidence['4965'], [{'serie': 6, 'estado': 'A', 'transicion': 1,
            'entrada': '<Entrada/>', 'entradaNueva': '<Entrada/>', 'salida': '<Salida/>'}])
        self.assertEqual(self.journal.status()['radicacionPersistida']['actividadesActivas'],
                         [{'actividad': 4965, 'K_SERIE': 6}])
        self.assertFalse(any(sql == START_SQL for sql, _ in self.port.statements))

    def test_zip_uses_same_builder_hash_and_catalog_as_local(self):
        from .cli import bundle
        import json, os, subprocess, sys
        path = Path(self.temp.name) / 'case.zip'; bundle(path)
        code = '''import sys,json
import incluirtec,incluirtec.services
incluirtec.__path__.insert(0,sys.argv[1]+'/api_overlay/incluirtec')
incluirtec.services.__path__.insert(0,sys.argv[1]+'/api_overlay/incluirtec/services')
from incluirtec.digital_operations.production_preparation import builder_version
from incluirtec.digital_operations.references import reference_catalog
print(json.dumps({'builder':builder_version(),'catalog':reference_catalog()[1]}))'''
        env = {**os.environ, 'PYTHONPATH': str(path) + os.pathsep + str(Path(__file__).resolve().parents[4] / 'api_data_v1')}
        result = subprocess.run([sys.executable, '-B', '-c', code, str(path)], cwd=self.temp.name,
                                env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed['builder'], self.message['preparacion']['builderVersion'])
        self.assertEqual(observed['catalog'], self.message['preparacion']['catalogoSha256'])
