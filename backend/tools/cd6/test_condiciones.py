"""Solo cotizacion sintetica, SQLite temporal y puerto Oracle falso."""
import base64
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import hashlib
from io import StringIO
import json
from pathlib import Path
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace
from uuid import uuid4
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'api_data_v1'))
from incluirtec.digital_operations.credihoy_oracle import START_SQL
from incluirtec.digital_operations.oracle_fakes import FakeOraclePort
from .condiciones import FichaError, SID, UNIT, VERSION, contexto, request_final, validar
from .gateway import GatewayAdministrativoUnico
from .journal import Journal
from .grant import crear as crear_grant
from .worker import operate
from .cli import bundle, capture, confirmar_unidad


NOW = datetime(2026, 10, 14, 12, tzinfo=ZoneInfo('America/Bogota'))


def fixture():
    base = {'versionContrato': 'credihoy-radicacion-administrativa-v1', 'solicitudId': SID,
        'intencionId': None, 'producto': {'codigo': '390', 'modalidad': '111', 'destino': 'CFA',
            'certificacionId': 'CREDIHOY_390_111_CFA_CD4D'},
        'titular': {'tipoDocumento': 'CC', 'numeroDocumento': '100000001'},
        'credito': {'montoSolicitado': '500000.00', 'plazoMeses': 6, 'periodicidad': 'MENSUAL',
                    'moneda': 'COP', 'tasaNominalAnual': None, 'cuotaContractual': None},
        'autorizacionAdministrativa': {'autorizacionId': None, 'referencia': None,
            'modelo': 'CREDIHOY_VAL_EMPLEADO', 'alcance': 'RADICACION_ADMINISTRATIVA_PENDIENTE'},
        'evidenciaTasa': {'unidad': None, 'campoXml': 'V_TASA_NOMINAL', 'referenciaCertificacion': None},
        'contextoLinix': {'kIdTerc': '123456', 'sucursal': '101', 'fechaSolicitud': None,
            'fechaCuotaOrdinaria': None, 'datosRadicacion': {
                'promotor': {'codigo': None, 'kIdTerc': None, 'nombre': None},
                'cliente': {'nombre': 'PERSONA SINTETICA', 'email': 'prueba@example.test', 'numeroCelular': '3000000000'}},
            'parametros': {'amortizacion': 'CI', 'formaAplicarTasa': 'F', 'medioPago': '100', 'seguroVida': 'P',
                'modalidadCapital': 'N', 'porcentajeCapitalizacion': '0.00', 'valorTotalCapitalizacion': None,
                'periodoPagoCapital': '1', 'periodoPagoInteres': None, 'actividadEconomica': 'EM'}}}
    content = b'SOPORTE SINTETICO NO VALIDO PARA ENVIO REAL'
    ficha = {'version': VERSION, 'contexto': contexto(base), 'tasaNAMV': '20.00', 'unidad': UNIT,
        'referenciaTasa': 'TARIFA-SINTETICA', 'vigenteDesde': '2026-10-01', 'vigenteHasta': '2026-10-31',
        'cuotaContractual': '90000.00', 'periodoPagoInteres': '1', 'capitalizacionTotal': '0.00',
        'primeraCuota': '2026-11-14', 'registroAdministrativoPendienteAdmitido': True,
        'evidencia': {'referencia': 'COTIZACION-SINTETICA', 'operadorId': 'OPERADOR-SINTETICO',
            'nombre': 'sintetico.pdf', 'contenidoBase64': base64.b64encode(content).decode(),
            'sha256': hashlib.sha256(content).hexdigest(), 'oficialMismoExpediente': True, 'seguroYConceptosIncluidos': True}}
    return base, ficha, {'1': 'Mensual', '2': 'Bimestral'}


def message():
    base, ficha, domains = fixture()
    return {'accion': 'enviar', 'autorizarStartUnico': True, 'borrador': base, 'ficha': ficha,
        'dominios': domains, 'autorizacion': {'responsable': 'RESPONSABLE-SINTETICO',
        'referencia': 'AUTORIZACION-SINTETICA', 'antecedente': 'AUTORIZACION SINTETICA SIN VALOR REAL',
        'sinPromotorExperimental': True, 'alcancePendienteAdmitido': True}}


class Port(FakeOraclePort):
    def __init__(self, payload):
        promoted = deepcopy(payload)
        promoted['contextoLinix']['datosRadicacion']['promotor'] = {'codigo': '9', 'kIdTerc': '99', 'nombre': 'SINTETICO'}
        super().__init__(promoted)
        self.controls = ('N', 'N', None)
        self.duplicate = False
        self.wrong_identity = False

    def open_connection(self):
        conn = super().open_connection()
        factory = conn.cursor
        def cursor():
            cur = factory()
            execute = cur.execute
            def run(sql, parameters=None, **binds):
                if sql.startswith('SELECT K_IDTERC,I_ESTADO'):
                    cur.rows = [('0' if self.wrong_identity else self.backend.payload['contextoLinix']['kIdTerc'], 'Y')]
                elif sql.startswith('SELECT c.K_SUCURS,c.K_NOMINA'):
                    cur.rows = [('101', '100', 'N', 'Y')]
                elif sql.startswith('SELECT K_NUMDOC FROM LINIX.CA090'):
                    cur.rows = [('SC-SINTETICA',)] if self.duplicate else []
                else:
                    execute(sql, parameters, **binds)
            cur.execute = run
            return cur
        conn.cursor = cursor
        return conn


class CondicionesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='cd6-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'journal.db'
        self.sid = str(uuid4())
        self.journal = Journal(self.path, b'SECRETO-SINTETICO-NO-PRODUCTIVO', request_id=self.sid)
        self.addCleanup(self.journal.close)
        self.base, self.ficha, self.domains = fixture()
        self.payload, _ = request_final(self.base, self.ficha, self.domains,
            str(uuid4()), str(uuid4()), 'AUTORIZACION-SINTETICA', now=NOW)
        self.payload['solicitudId'] = self.sid
        from .test_caso_operativo import CasePort
        self.port = CasePort(self.payload)
        for name in ('socket.create_connection', 'socket.getaddrinfo', 'socket.socket.connect'):
            p = patch(name, side_effect=AssertionError('RED_REAL_PROHIBIDA'))
            p.start()
            self.addCleanup(p.stop)

    def run_operation(self, msg=None, journal=None):
        from .test_caso_operativo import case_message
        journal = journal or self.journal
        msg = msg or message()
        if msg['accion'] == 'conciliar':
            return operate({'accion': 'conciliar-caso', 'solicitudId': self.sid}, journal, self.port,
                           'WORKFLOW-SINTETICO', now=NOW)
        if msg.get('autorizarStartUnico') is not True:
            raise FichaError('START_NO_AUTORIZADO')
        validar(msg['ficha'], msg['borrador'], msg['dominios'], now=NOW)
        if not journal.exists():
            prepared = case_message(self.payload)
            operate(prepared, journal, self.port, 'WORKFLOW-SINTETICO', now=NOW)
        op = journal.operation()
        return operate({'accion': 'enviar-caso', 'solicitudId': self.sid, 'autorizarStartUnico': True,
                        'intencionId': op.intent_id, 'fingerprint': op.fingerprint}, journal, self.port,
                       'WORKFLOW-SINTETICO', now=NOW)

    def starts(self):
        return [x for x in self.port.statements if x[0] == START_SQL]

    def test_xml_real_sintetico_sin_promotor_y_original_intacto(self):
        before = deepcopy(self.base)
        payload, xml = request_final(self.base, self.ficha, self.domains,
            str(uuid4()), str(uuid4()), 'AUTORIZACION-SINTETICA', now=NOW)
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, 'Entrada')
        self.assertEqual(root.findtext('./Datos/V_TASA_NOMINAL'), '20.00')
        self.assertEqual(root.findtext('./Datos/V_VALOR_CUOTA'), '90000.00')
        self.assertEqual(root.findtext('./Datos/F_FECHA_SOLICITUD'), '10/14/2026')
        for tag in ('A_CODIGO_PROMOTOR', 'A_PROMOTOR', 'V_PROMOTOR'):
            self.assertIsNone(root.find('./Datos/' + tag))
            self.assertNotIn(tag, root.findtext('./Estructura/General').split(','))
        self.assertNotIn('evidenciaDecision', payload)
        self.assertEqual(self.base, before)

    def test_rechaza_campos_inventados_unidad_y_evidencia(self):
        changes = [('unidad', 'EA'), ('tasaNAMV', 'NaN'), ('tasaNAMV', '0'), ('tasaNAMV', '20,00'),
            ('cuotaContractual', '0'), ('capitalizacionTotal', ''), ('periodoPagoInteres', '2'),
            ('primeraCuota', '2026-10-14'), ('vigenteHasta', '2026-10-13'),
            ('registroAdministrativoPendienteAdmitido', False)]
        for field, value in changes:
            ficha = deepcopy(self.ficha)
            ficha[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(FichaError):
                validar(ficha, self.base, self.domains, now=NOW)
        for field, value in [('sha256', '0' * 64), ('oficialMismoExpediente', False), ('seguroYConceptosIncluidos', False)]:
            ficha = deepcopy(self.ficha)
            ficha['evidencia'][field] = value
            with self.assertRaises(FichaError):
                validar(ficha, self.base, self.domains, now=NOW)

    def test_cero_capitalizacion_solo_explicito_y_sin_formula(self):
        self.assertEqual(validar(self.ficha, self.base, self.domains, now=NOW)['capitalizacionTotal'], '0.00')
        self.ficha['capitalizacionTotal'] = None
        with self.assertRaises(FichaError):
            validar(self.ficha, self.base, self.domains, now=NOW)

    def test_cero_acreditado_solo_cambia_formato_no_valor(self):
        self.base['contextoLinix']['parametros']['porcentajeCapitalizacion'] = '0'
        self.ficha['contexto'] = contexto(self.base)
        original = deepcopy(self.base)
        payload, xml = request_final(self.base, self.ficha, self.domains,
            str(uuid4()), str(uuid4()), 'AUTORIZACION-SINTETICA', now=NOW)
        self.assertEqual(payload['contextoLinix']['parametros']['porcentajeCapitalizacion'], '0.00')
        self.assertEqual(self.base, original)
        self.assertEqual(ET.fromstring(xml).findtext('./Datos/V_PORCAPITA'), '0.00')

    def test_monto_plazo_titular_producto_no_intercambiables(self):
        for field in ('montoCOP', 'plazoMeses', 'nomina', 'huellaBorrador'):
            ficha = deepcopy(self.ficha)
            ficha['contexto'][field] = 'OTRO'
            with self.subTest(field=field), self.assertRaises(FichaError):
                validar(ficha, self.base, self.domains, now=NOW)

    def test_un_start_corte6964_sin_actividades_humanas(self):
        result = self.run_operation()
        self.assertEqual(result['estado'], 'RADICADA', result)
        self.assertEqual(result['resultado']['activity_current'], 6964)
        self.assertTrue(result['resultado']['cut_verified'])
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.port.backend.executions, 0)
        self.assertEqual(self.port.connections[1].commits, 1)
        self.assertEqual(self.run_operation()['estado'], 'RADICADA')
        self.assertEqual(len(self.starts()), 1)

    def test_sin_autorizacion_o_soporte_no_hay_intencion_ni_conexion(self):
        for path in ('autorizarStartUnico', 'registroAdministrativoPendienteAdmitido'):
            msg = message()
            if path == 'autorizarStartUnico':
                msg[path] = False
            else:
                msg['ficha'][path] = False
            with self.assertRaises(FichaError):
                self.run_operation(msg)
            self.assertFalse(self.journal.exists())
            self.assertFalse(self.port.connections)

    def test_duplicidad_o_identidad_incorrecta_bloquean_start(self):
        self.port.duplicate = True
        self.assertEqual(self.run_operation()['estado'], 'BLOQUEADA')
        self.assertEqual(len(self.starts()), 0)

    def test_identidad_incorrecta_bloquea_start(self):
        self.port.wrong_identity = True
        self.assertEqual(self.run_operation()['estado'], 'BLOQUEADA')
        self.assertEqual(len(self.starts()), 0)

    def test_timeout_recupera_misma_intencion_sin_repetir_start(self):
        self.port.timeout = True
        self.port.internal_commit = True
        first = self.run_operation()
        self.assertEqual(first['estado'], 'RADICADA')
        self.assertEqual(len(self.starts()), 1)
        self.assertGreaterEqual(self.journal.db.execute('SELECT COUNT(*) FROM cd6_observaciones').fetchone()[0], 2)
        self.assertEqual(self.run_operation()['estado'], 'RADICADA')
        recovered = self.run_operation({'accion': 'conciliar'})
        self.assertEqual(recovered['estado'], 'RADICADA', recovered)
        self.assertEqual(recovered['intencionId'], first['intencionId'])
        self.assertEqual(len(self.starts()), 1)

    def test_error_de_workflow_no_commit_y_no_reenvio(self):
        self.port.error_count = 1
        self.assertEqual(self.run_operation()['estado'], 'INCIERTA')
        self.assertEqual(sum(c.commits for c in self.port.connections), 0)
        self.run_operation()
        self.assertEqual(len(self.starts()), 1)

    def test_113_e_sin_sc_persistida_solo_concilia_y_nunca_reenvia(self):
        self.port.create_sc = False
        self.port.internal_commit = True
        self.port.error_count = 2
        self.port.tasks = [(1, 113, 10002896, 'E', 'A'),
                           (2, 3883, 10002896, 'A', 'A')]

        def incomplete_result(backend):
            backend.records = [replace(record, task_state='E', xml_new=record.xml_input,
                xml_output='<Salida><Datos><A_TIPO_SOLI_CREDITO>SC</A_TIPO_SOLI_CREDITO>'
                           '<A_NUMERO_SOLI_CREDITO/></Datos></Salida>')
                for record in backend.records]
        self.port.transform = incomplete_result
        first = self.run_operation()
        self.assertEqual(first['estado'], 'INCIERTA')
        self.assertEqual(first['resultado']['pending_reason'], 'CREDIHOY_SC_NO_CONFIRMADA')
        self.assertEqual(first['resultado']['activity_current'], 3883)
        self.assertFalse(first['resultado']['cut_verified'])
        self.assertEqual(self.port.backend.records[0].task_state, 'E')
        self.assertFalse(self.port.backend.applications)
        recovered = self.run_operation({'accion': 'conciliar'})
        self.assertEqual(recovered['estado'], 'INCIERTA')
        self.assertEqual(recovered['K_FLUJO'], first['K_FLUJO'])
        self.assertEqual(recovered['intencionId'], first['intencionId'])
        self.assertEqual(self.run_operation()['estado'], 'INCIERTA')
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.port.backend.executions, 0)
        self.assertEqual(sum(c.commits for c in self.port.connections), 0)

    def test_actividad_humana_o_aprobacion_inesperada_no_confirman(self):
        self.port.controls = ('Y', 'N', None)
        self.assertEqual(self.run_operation()['estado'], 'INCIERTA')
        self.assertEqual(sum(c.commits for c in self.port.connections), 0)

    def test_journal_privado_sin_payload_en_claro(self):
        self.run_operation()
        raw = self.path.read_bytes()
        for marker in (b'PERSONA SINTETICA', b'100000001', b'90000.00', b'SOPORTE SINTETICO'):
            self.assertNotIn(marker, raw)
        second = Journal(self.path, b'SECRETO-SINTETICO-NO-PRODUCTIVO', request_id=self.sid)
        self.addCleanup(second.close)
        self.assertEqual(self.run_operation(journal=second)['estado'], 'RADICADA')
        self.assertEqual(len(self.starts()), 1)

    def test_claim_atomico_no_permite_segundo(self):
        self.journal.prepare(self.payload, {})
        self.journal.claim()
        with self.assertRaises(FichaError):
            self.journal.claim()

    def test_unidad_repite_solo_confirmacion_sin_revelar_numero(self):
        output = StringIO()
        with patch('builtins.input', side_effect=['17.69', 'NAMV']) as prompt, redirect_stdout(output):
            confirmar_unidad()
        self.assertEqual(prompt.call_count, 2)
        self.assertNotIn('17.69', output.getvalue())
        self.assertIn('no confirma la tarifa aplicable', output.getvalue())

    def test_unidad_no_confirmada_no_guarda_ficha_ni_envia(self):
        seal = Mock()
        with patch.dict(sys.modules, {'credihoy_focal_crypto': SimpleNamespace(seal=seal)}), \
                patch('tools.cd6.cli.sys.stdin.isatty', return_value=True), \
                patch('tools.cd6.cli.getpass', return_value='20.00'), \
                patch('builtins.input', side_effect=['17.69', '17.69', '17.69']), \
                patch('tools.cd6.cli.send') as send, redirect_stdout(StringIO()):
            with self.assertRaisesRegex(FichaError, 'UNIDAD_NAMV_NO_CONFIRMADA'):
                capture(Path(self.temp.name), self.base, self.domains)
        seal.assert_not_called()
        send.assert_not_called()
        self.assertFalse((Path(self.temp.name) / 'cd6-ficha-oficial.sealed.json').exists())

    def test_guardia_start_durable_incluso_con_otro_adaptador(self):
        self.journal.prepare(self.payload, {})
        self.journal.claim()
        self.journal.mark_start()
        self.assertTrue(self.journal.status()['STARTIntentado'])
        second = Journal(self.path, b'SECRETO-SINTETICO-NO-PRODUCTIVO', request_id=self.sid)
        self.addCleanup(second.close)
        with self.assertRaisesRegex(FichaError, 'START_YA_INTENTADO'):
            second.mark_start()
        self.assertEqual(len(self.starts()), 0)

    def test_estado_terminal_no_se_degrada_por_conciliacion_tardia(self):
        self.run_operation()
        self.journal.settle('INCIERTA', {'codigo': 'OBSERVACION_TARDIA'})
        self.assertEqual(self.journal.status()['estado'], 'RADICADA')
        self.assertNotIn('OBSERVACION_TARDIA', str(self.journal.status()))

    def test_respuesta_con_tasa_cambiada_no_confirma_ni_commit(self):
        def changed(backend):
            backend.records = [replace(record, xml_new=record.xml_new.replace('20.00', '25.41'))
                               for record in backend.records]
        from dataclasses import replace
        self.port.transform = changed
        result = self.run_operation()
        self.assertEqual(result['estado'], 'INCIERTA', result)
        self.assertEqual(sum(c.commits for c in self.port.connections), 0)
        self.assertEqual(len(self.starts()), 1)

    def test_vigencia_se_revalida_inmediatamente_antes_de_start(self):
        from datetime import timedelta
        grant_now = NOW.replace(hour=23, minute=59, second=30)
        from .test_caso_operativo import case_message
        prepared = case_message(self.payload)['preparacion']
        grant = crear_grant(self.payload, prepared['autorizacion'], 'WORKFLOW-SINTETICO', grant_now)
        grant['xmlSha256'] = prepared['xmlSha256']
        self.journal.prepare(self.payload, {'preparacion': prepared, 'autorizacion': prepared['autorizacion'],
            'grantAdministrativo': grant})
        operation = self.journal.claim()
        adapter = GatewayAdministrativoUnico(self.journal, self.port,
            workflow_user='WORKFLOW-SINTETICO', clock=lambda: grant_now + timedelta(seconds=60))
        with self.assertRaisesRegex(FichaError, 'FECHA_CAMBIARON'):
            adapter.submit(operation, checkpoint_flow=self.journal.checkpoint)
        self.assertFalse(self.journal.status()['STARTIntentado'])
        self.assertEqual(len(self.starts()), 0)

    def test_paquete_solo_codigo_importable_sin_secretos_ni_fakes(self):
        path = Path(self.temp.name) / 'codigo.zip'
        bundle(path)
        with ZipFile(path) as archive:
            names = archive.namelist()
            self.assertTrue(all(name.endswith('.py') or name ==
                'api_overlay/incluirtec/digital_operations/reference_catalogs.json' for name in names))
            self.assertFalse(any('test_' in name or 'fakes' in name for name in names))
        code = """import sys
import incluirtec, incluirtec.services
incluirtec.__path__.insert(0, sys.argv[1] + '/api_overlay/incluirtec')
incluirtec.services.__path__.insert(0, sys.argv[1] + '/api_overlay/incluirtec/services')
import tools.cd6.worker, tools.cd6.gateway, tools.cd6.journal
assert sys.argv[1] in tools.cd6.gateway.__file__
from incluirtec.digital_operations.credihoy import build_radication_xml
assert sys.argv[1] in sys.modules[build_radication_xml.__module__].__file__
print('PAQUETE_IMPORTABLE_SIN_EJECUCION')
"""
        env = {**os.environ, 'PYTHONPATH': str(path) + os.pathsep + str(Path(__file__).resolve().parents[4] / 'api_data_v1')}
        result = subprocess.run([sys.executable, '-B', '-c', code, str(path)], cwd=self.temp.name,
                                env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'PAQUETE_IMPORTABLE_SIN_EJECUCION')

    def test_dia_colombia_no_utc(self):
        from datetime import timezone
        now = datetime(2026, 10, 15, 2, tzinfo=timezone.utc)
        payload, _ = request_final(self.base, self.ficha, self.domains,
            str(uuid4()), str(uuid4()), 'AUTORIZACION-SINTETICA', now=now)
        self.assertEqual(payload['contextoLinix']['fechaSolicitud'], '10/14/2026')


if __name__ == '__main__':
    unittest.main()
