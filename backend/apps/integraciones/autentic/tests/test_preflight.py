"""Solo regresiones del preflight y autorizacion local; sin red."""

from contextlib import ExitStack, contextmanager, redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
import base64
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

from ..cliente import AutenticDEVClient
from ..cliente import ReciboFirma
from ..contratos import AutenticError, ConfiguracionDEV, TOKEN_URL, preparar_firma
from .test_cliente import Response, Session, token
from tools import autentic_verificar_dev as tool


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = ConfiguracionDEV('CLIENTE-FICTICIO', 'SECRETO-FICTICIO')

    def test_dev_document_is_noncontractual_and_copies_only_the_public_key(self):
        from pypdf import PdfReader
        with TemporaryDirectory() as tmp:
            root=Path(tmp);custody=root/'credihoy-contrato-minimo-20261013';custody.mkdir()
            (custody/'contrato-trabajo-public.pem').write_text('SYNTHETIC_PUBLIC_KEY')
            with patch.dict('os.environ',{'TEMP':tmp}):
                result=tool.documento_dev_privado(root/'dev')
            text=PdfReader(root/'dev/prueba-dev.pdf').pages[0].extract_text()
            self.assertIn('No es autorizacion de centrales',text)
            self.assertIn('No autoriza radicacion',text)
            self.assertEqual((root/'dev/contrato-trabajo-public.pem').read_text(),'SYNTHETIC_PUBLIC_KEY')
            self.assertFalse((root/'dev/contrato-trabajo-private.pem').exists())
            self.assertEqual(len(result['sha256']),64)

    def test_dev_send_uses_bound_fresh_consent_and_consultable_own_secure_key(self):
        from datetime import datetime,timedelta,timezone
        from hashlib import sha256
        from types import SimpleNamespace
        from .test_cliente import pdf
        with TemporaryDirectory() as tmp:
            root=Path(tmp);body=pdf();(root/'prueba-dev.pdf').write_bytes(body)
            now=datetime.now(timezone.utc)
            proof={'ambiente':'DEV','pdfSha256':sha256(body).hexdigest(),'aceptacionExplicita':True,
                'cotejoYOTPReales':True,'proveedorIdentity':'HTTPS_18449','sesionReferencia':'SYNTHETIC_SESSION',
                'expedienteReferencia':'SYNTHETIC_CASE','titularReferencia':sha256(b'SYNTHETIC_DOCUMENT').hexdigest(),
                'aceptadoEn':now.isoformat(),'autenticadoEn':(now-timedelta(seconds=10)).isoformat()}
            packet={'evidencia':proof,'metadata':{'names':'SYNTHETIC','lastNames':'ONLY','docId':'SYNTHETIC_DOCUMENT'}}
            client=unittest.mock.Mock()
            client.auditoria_token={'peticiones':1}
            client.auditoria_firma={'peticiones':1,'http':201,'tiempoRespuestaMs':1,'codigo':None}
            client.firmar.return_value=ReciboFirma('SYNTHETIC_TRANSACTION',now.isoformat(),'https://example.invalid/zip')
            crypto=SimpleNamespace(seal=unittest.mock.Mock())
            with patch.dict('sys.modules',{'credihoy_focal_crypto':crypto}),patch.object(tool,'preflight_transporte'),\
                    patch.object(tool,'AutenticDEVClient',return_value=client),patch.object(
                        tool.socket,'getaddrinfo',side_effect=OSError('SYNTHETIC_NOT_REAL_DNS')):
                r=tool.firmar_prueba_dev(self.config,root,packet)
                client.firmar.assert_called_once()
                request=client.firmar.call_args.args[0]
                self.assertTrue(request.payload['metadata']['secureKey'].startswith('CREDIHOY-DEV:'))
                self.assertEqual(r['firmasAcreditadas'],0)
                self.assertEqual(r['estado'],'FIRMA_REPORTADA_NO_VERIFICADA')
                self.assertNotIn('SYNTHETIC_DOCUMENT',json.dumps(r))
                self.assertEqual(r['consultaEstado'],'NO_DOCUMENTADA_NO_INVOCADA')
                client.consultar_estado.assert_not_called()
                client.recuperar_documentos.assert_not_called()
                with self.assertRaises(FileExistsError):tool.firmar_prueba_dev(self.config,root,packet)
                client.firmar.assert_called_once()

    def test_dev_send_refuses_wrong_pdf_expired_identity_and_implicit_consent_before_network(self):
        from datetime import datetime,timedelta,timezone
        from hashlib import sha256
        from .test_cliente import pdf
        with TemporaryDirectory() as tmp:
            root=Path(tmp);body=pdf();(root/'prueba-dev.pdf').write_bytes(body)
            now=datetime.now(timezone.utc)
            good={'ambiente':'DEV','pdfSha256':sha256(body).hexdigest(),'aceptacionExplicita':True,
                'cotejoYOTPReales':True,'proveedorIdentity':'HTTPS_18449','sesionReferencia':'SYNTHETIC',
                'expedienteReferencia':'SYNTHETIC','titularReferencia':sha256(b'SYNTHETIC').hexdigest(),
                'aceptadoEn':now.isoformat(),'autenticadoEn':now.isoformat()}
            for change in ({'aceptacionExplicita':False},{'pdfSha256':'a'*64},{'cotejoYOTPReales':False},
                    {'autenticadoEn':(now-timedelta(hours=1)).isoformat()}):
                with patch.object(tool,'AutenticDEVClient') as client:
                    with self.assertRaises(AutenticError):tool.firmar_prueba_dev(self.config,root,{
                        'metadata':{'names':'SYNTHETIC','lastNames':'ONLY','docId':'SYNTHETIC'},
                        'evidencia':{**good,**change}})
                    client.assert_not_called()

    @contextmanager
    def reconciliation_fixture(self):
        from .test_cliente import pdf
        with TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp);body=pdf();(root/'prueba-dev.pdf').write_bytes(body)
            digest=sha256(body).hexdigest()
            proof={'pdfSha256':digest,'aceptadoEn':'2026-10-27T19:35:00+00:00'}
            canonical=json.dumps(proof,sort_keys=True,separators=(',',':'))
            acceptance={'evidencia':proof,'metadata':{'names':'SYNTHETIC','lastNames':'ONLY','docId':'PRIVATE_ID'},
                'secureKey':'CREDIHOY-DEV:'+sha256(canonical.encode()).hexdigest()}
            acceptance['fingerprint']=preparar_firma({**acceptance['metadata'],'secureKey':acceptance['secureKey']},
                {'prueba-dev.pdf':body}).fingerprint
            result={'resultadoEnvioIncierto':True,'enviosIntentados':1,'firmasAcreditadas':0,'pdfSha256':digest}
            (root/'envio-resultado.json').write_text(json.dumps(result))
            (root/'aceptacion-dev.sealed.json').write_bytes(b'SYNTHETIC_SEALED_ACCEPTANCE')
            (root/'conciliacion-soporte-dev-original.sealed.json').write_bytes(b'SYNTHETIC_ORIGINAL_SUPPORT')
            db=sqlite3.connect(root/'autentic-journal.sqlite3')
            db.execute('CREATE TABLE autentic_operaciones(clave,fingerprint,estado,transaccion)')
            db.execute('INSERT INTO autentic_operaciones VALUES (?,?,?,?)',
                ('LOCAL_OP',acceptance['fingerprint'],'INDETERMINADA',''))
            db.commit();db.close()
            crypto=SimpleNamespace(unpack=unittest.mock.Mock(return_value=acceptance),seal=unittest.mock.Mock())
            stack.enter_context(patch.dict('sys.modules',{'credihoy_focal_crypto':crypto}))
            stack.enter_context(patch.dict('os.environ',{'TEMP':tmp},clear=True))
            stack.enter_context(patch.object(sys,'path',list(sys.path)))
            for target in ('socket.create_connection','socket.getaddrinfo','socket.socket.connect',
                    'socket.socket.connect_ex','requests.sessions.Session.request','dotenv.load_dotenv',
                    'tools.autentic_verificar_dev.AutenticDEVClient',
                    'tools.autentic_verificar_dev.ConfiguracionDEV.desde_archivo',
                    'tools.autentic_verificar_dev.verificar_tls'):
                blocked=stack.enter_context(patch(target,side_effect=AssertionError('NO_NETWORK_ENV_OR_PROVIDER')))
                stack.callback(blocked.assert_not_called)
            yield root,acceptance,crypto

    def reconciliation_snapshot(self,root):
        return {p.relative_to(root).as_posix():sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}

    def assert_reconciliation_rejected(self,root,crypto,code):
        before=self.reconciliation_snapshot(root)
        for inspect in (False,True):
            with self.subTest(solo_inspeccion=inspect),self.assertRaisesRegex(AutenticError,code):
                tool.conciliar_prueba_dev(root,root,solo_inspeccion=inspect)
            self.assertEqual(before,self.reconciliation_snapshot(root))
        crypto.seal.assert_not_called()

    def test_offline_reconciliation_never_reads_oauth_calls_network_or_changes_journal(self):
        with self.reconciliation_fixture() as (root,acceptance,crypto):
            before=self.reconciliation_snapshot(root);output=StringIO()
            with redirect_stdout(output):
                self.assertEqual(tool.main(['--conciliar-envio-dev',str(root),'--custodia-cifrado',str(root)]),0)
            safe=json.loads(output.getvalue());self.assertEqual(safe['HTTPNuevos'],0)
            self.assertEqual(safe['reenvios'],0);self.assertNotIn('PRIVATE_ID',output.getvalue())
            report=crypto.seal.call_args.args[2]
            self.assertIsNone(report['httpOriginal']);self.assertEqual(report['firmante'],acceptance['metadata'])
            self.assertEqual(report['fingerprintLocal'],acceptance['fingerprint'])
            self.assertNotEqual(crypto.seal.call_args.args[1],'conciliacion-soporte-dev-original.sealed.json')
            crypto.seal.assert_called_once()
            self.assertEqual(before,self.reconciliation_snapshot(root))

    def test_solo_inspeccion_is_repeatable_read_only_and_never_seals_or_exposes_private_data(self):
        with self.reconciliation_fixture() as (root,acceptance,crypto):
            before=self.reconciliation_snapshot(root)
            # A module without seal must suffice for inspection.
            del crypto.seal
            original_open=Path.open;original_connect=sqlite3.connect
            def read_only_open(path,mode='r',*args,**kwargs):
                if any(flag in mode for flag in 'wax+'):
                    raise AssertionError('FILE_WRITE_PROHIBITED')
                return original_open(path,mode,*args,**kwargs)
            def read_only_connect(database,*args,**kwargs):
                self.assertTrue(kwargs.get('uri'))
                self.assertTrue(database.endswith('?mode=ro&immutable=1'))
                return original_connect(database,*args,**kwargs)
            with patch.object(Path,'open',read_only_open),patch('sqlite3.connect',read_only_connect),\
                    patch.object(tool,'uuid4',side_effect=AssertionError('NO_NEW_SUPPORT_PACKAGE')):
                for _ in range(2):
                    output=StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(tool.main(['--conciliar-envio-dev',str(root),'--custodia-cifrado',
                            str(root),'--solo-inspeccion']),0)
                    safe=json.loads(output.getvalue())
                    self.assertEqual(safe,{'ambiente':'DEV','estado':'SOLO_INSPECCION','operacionesJournal':1,
                        'estadoJournal':'INDETERMINADA','fingerprintVerificado':True,
                        'pdfSha256':acceptance['evidencia']['pdfSha256'],'escrituras':0,
                        'referenciaProveedorPresente':False,'firmasAcreditadas':0,'HTTPNuevos':0,'reenvios':0,'START':0})
                    for private in ('PRIVATE_ID',acceptance['secureKey'],str(root),'SYNTHETIC_ORIGINAL_SUPPORT'):
                        self.assertNotIn(private,output.getvalue())
            self.assertEqual(before,self.reconciliation_snapshot(root))

    def test_solo_inspeccion_rejects_wal_and_recovery_sidecars_without_creating_files(self):
        for suffix in ('WAL_MODE','-wal','-shm','-journal'):
            with self.subTest(suffix=suffix),self.reconciliation_fixture() as (root,acceptance,crypto):
                journal=root/'autentic-journal.sqlite3'
                if suffix=='WAL_MODE':
                    db=sqlite3.connect(journal)
                    self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
                    db.close()
                else:Path(str(journal)+suffix).write_bytes(b'SYNTHETIC_PENDING_RECOVERY')
                before=self.reconciliation_snapshot(root)
                with patch('sqlite3.connect',side_effect=AssertionError('NO_SQLITE_OPEN')),\
                        self.assertRaisesRegex(AutenticError,'CONCILIACION_DEV_INSPECCION_REQUIERE_JOURNAL_ESTABLE'):
                    tool.conciliar_prueba_dev(root,root,solo_inspeccion=True)
                crypto.seal.assert_not_called()
                self.assertEqual(before,self.reconciliation_snapshot(root))

    def test_reconciliation_rebuilds_fingerprint_instead_of_trusting_matching_stored_values(self):
        with self.reconciliation_fixture() as (root,acceptance,crypto):
            acceptance['fingerprint']='a'*64
            with sqlite3.connect(root/'autentic-journal.sqlite3') as db:
                db.execute('UPDATE autentic_operaciones SET fingerprint=?',(acceptance['fingerprint'],))
            db.close()
            self.assert_reconciliation_rejected(root,crypto,'CONCILIACION_DEV_EVIDENCIA_NO_CONCUERDA')

    def test_reconciliation_rejects_changed_or_malformed_metadata(self):
        with self.reconciliation_fixture() as (root,acceptance,crypto):
            good=deepcopy(acceptance['metadata'])
            for metadata in ({**good,'names':'OTHER'},{**good,'lastNames':'OTHER'},
                    {**good,'docId':'OTHER'},{**good,'secureKey':'INJECTED'},
                    {**good,'names':''},{**good,'names':None},{'docId':'PRIVATE_ID'},None):
                with self.subTest(metadata=metadata):
                    acceptance['metadata']=metadata
                    self.assert_reconciliation_rejected(root,crypto,'CONCILIACION_DEV_EVIDENCIA_NO_CONCUERDA')

    def test_reconciliation_rejects_pdf_tampering_even_when_hashes_and_secure_key_are_updated(self):
        for content in (b'CHANGED',None):
            with self.subTest(invalid_pdf=content is not None),self.reconciliation_fixture() as (root,acceptance,crypto):
                body=content if content is not None else (root/'prueba-dev.pdf').read_bytes()+b'\n% SYNTHETIC CHANGE\n'
                (root/'prueba-dev.pdf').write_bytes(body)
                acceptance['evidencia']['pdfSha256']=sha256(body).hexdigest()
                canonical=json.dumps(acceptance['evidencia'],sort_keys=True,separators=(',',':'))
                acceptance['secureKey']='CREDIHOY-DEV:'+sha256(canonical.encode()).hexdigest()
                result=json.loads((root/'envio-resultado.json').read_text())
                result['pdfSha256']=sha256(body).hexdigest()
                (root/'envio-resultado.json').write_text(json.dumps(result))
                self.assert_reconciliation_rejected(root,crypto,'CONCILIACION_DEV_EVIDENCIA_NO_CONCUERDA')

    def test_reconciliation_rejects_inconsistent_hash_secure_key_and_stored_fingerprint(self):
        for field,value in (('secureKey','OTHER'),('fingerprint','b'*64),('fingerprint',None)):
            with self.subTest(field=field,value=value),self.reconciliation_fixture() as (root,acceptance,crypto):
                acceptance[field]=value
                self.assert_reconciliation_rejected(root,crypto,'CONCILIACION_DEV_EVIDENCIA_NO_CONCUERDA')
        with self.reconciliation_fixture() as (root,acceptance,crypto):
            (root/'prueba-dev.pdf').write_bytes(b'CHANGED')
            self.assert_reconciliation_rejected(root,crypto,'CONCILIACION_DEV_EVIDENCIA_NO_CONCUERDA')

    def test_reconciliation_rejects_extra_or_missing_rows_wrong_fingerprint_and_other_states(self):
        for change in ('EMPTY','EXTRA','FINGERPRINT','PREPARADA','ENVIANDO',
                'FIRMA_REPORTADA_NO_VERIFICADA','CERTIFICACION_RECUPERADA_NO_VERIFICADA'):
            with self.subTest(change=change),self.reconciliation_fixture() as (root,acceptance,crypto):
                with sqlite3.connect(root/'autentic-journal.sqlite3') as db:
                    if change=='EMPTY':db.execute('DELETE FROM autentic_operaciones')
                    elif change=='EXTRA':db.execute('INSERT INTO autentic_operaciones VALUES (?,?,?,?)',
                        ('OTHER_OP',acceptance['fingerprint'],'INDETERMINADA',''))
                    elif change=='FINGERPRINT':db.execute('UPDATE autentic_operaciones SET fingerprint=?',('b'*64,))
                    else:db.execute('UPDATE autentic_operaciones SET estado=?',(change,))
                db.close()
                self.assert_reconciliation_rejected(root,crypto,'CONCILIACION_DEV_JOURNAL_NO_CONCUERDA')

    def test_solo_inspeccion_rejects_missing_context_or_conflicting_actions_before_reading(self):
        with self.reconciliation_fixture() as (root,acceptance,crypto):
            base=['--conciliar-envio-dev',str(root),'--custodia-cifrado',str(root),'--solo-inspeccion']
            cases=[(['--solo-inspeccion'],'SOLO_INSPECCION_REQUIERE_CONCILIACION'),
                (['--conciliar-envio-dev',str(root),'--solo-inspeccion'],'CUSTODIA_CIFRADO_REQUERIDA')]
            for action in (['--config-privada','SYNTHETIC'],['--verificar-tls'],
                    ['--autorizar-una-autenticacion-dev'],['--auditoria','SYNTHETIC'],
                    ['--preparar-documento-dev','SYNTHETIC'],['--firmar-documento-dev','SYNTHETIC']):
                cases.append((base+action,'SOLO_INSPECCION_NO_ADMITE_OTRAS_OPERACIONES'))
            before=self.reconciliation_snapshot(root)
            for args,code in cases:
                with self.subTest(args=args),redirect_stdout(StringIO()) as output:
                    self.assertEqual(tool.main(args),1)
                    self.assertEqual(json.loads(output.getvalue())['codigo'],code)
            crypto.unpack.assert_not_called();crypto.seal.assert_not_called()
            self.assertEqual(before,self.reconciliation_snapshot(root))

    def test_default_solo_local(self):
        output = StringIO()
        with patch.object(tool.ConfiguracionDEV, 'desde_archivo', return_value=self.config), \
                patch.object(tool, 'AutenticDEVClient') as client, \
                patch.object(tool, 'verificar_tls') as tls, redirect_stdout(output):
            self.assertEqual(tool.main(['--config-privada', 'ruta-no-real']), 0)
        client.assert_not_called()
        tls.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result['postAutenticacion'], 0)
        self.assertFalse(result['documentosHabilitados'])

    def test_tls_sin_cliente_autenticacion(self):
        with patch.object(tool.ConfiguracionDEV, 'desde_archivo', return_value=self.config), \
                patch.object(tool, 'AutenticDEVClient') as client, \
                patch.object(tool, 'verificar_tls', return_value={'tlsValidado': True}) as tls, \
                redirect_stdout(StringIO()):
            self.assertEqual(tool.main(['--config-privada', 'ruta-no-real', '--verificar-tls']), 0)
        client.assert_not_called()
        tls.assert_called_once_with()

    def test_autorizacion_una_peticion_solo_token(self):
        session = Session(Response(token()))
        real = AutenticDEVClient(self.config, session=session)
        output = StringIO()
        with patch.object(tool.ConfiguracionDEV, 'desde_archivo', return_value=self.config), \
                patch.object(tool, 'AutenticDEVClient', return_value=real), redirect_stdout(output):
            self.assertEqual(tool.main(['--config-privada', 'ruta-no-real', '--autorizar-una-autenticacion-dev']), 0)
        self.assertEqual(len(session.calls), 1)
        method, url, args = session.calls[0]
        self.assertEqual((method, url), ('POST', TOKEN_URL))
        self.assertEqual(set(args['json']), {'audience', 'grant_type', 'client_id', 'client_secret'})
        self.assertNotIn('secureKey', args['json'])
        self.assertNotIn('TOKEN-SINTETICO', output.getvalue())
        self.assertNotIn('SECRETO-FICTICIO', output.getvalue())

    def test_exception_no_secretos_ni_causa(self):
        session = Session(requests.Timeout('SECRETO-FICTICIO TOKEN-PRIVADO'))
        real = AutenticDEVClient(self.config, session=session)
        output = StringIO()
        with patch.object(tool.ConfiguracionDEV, 'desde_archivo', return_value=self.config), \
                patch.object(tool, 'AutenticDEVClient', return_value=real), redirect_stdout(output):
            self.assertEqual(tool.main(['--config-privada', 'ruta-no-real', '--autorizar-una-autenticacion-dev']), 1)
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn('SECRETO-FICTICIO', output.getvalue())
        self.assertNotIn('TOKEN-PRIVADO', output.getvalue())

    def test_archivo_utf8_bom_preserva_credenciales_sin_base64(self):
        with TemporaryDirectory() as d:
            p = Path(d)/'config.json'
            payload = {'audience': 'stag-api.autenticsign.com', 'grant_type': 'client_credentials',
                       'client_id': ' CLIENTE-FICTICIO ', 'client_secret': 'SINTETICO+/%='}
            p.write_text(json.dumps(payload), encoding='utf-8-sig')
            config = ConfiguracionDEV.desde_archivo(p)
            self.assertEqual(config.client_id, payload['client_id'])
            self.assertEqual(config.client_secret, payload['client_secret'])
            p.write_text('{"audience":"a","audience":"b"}', encoding='utf-8')
            with self.assertRaisesRegex(AutenticError, 'CONFIGURACION_DEV_INVALIDA'):
                ConfiguracionDEV.desde_archivo(p)

    def test_preflight_transporte_real_sin_red(self):
        client = AutenticDEVClient(self.config)
        try:
            tool.preflight_transporte(client)
            client.session.hooks['response'].append(lambda r: r)
            with self.assertRaisesRegex(AutenticError, 'PREFLIGHT_TRANSPORTE_BLOQUEADO'):
                tool.preflight_transporte(client)
        finally:
            client.close()

    def test_auditoria_durable_una_peticion_y_token_descartado(self):
        session = Session(Response(token()))
        client = AutenticDEVClient(self.config, session=session)
        with TemporaryDirectory() as d:
            path = Path(d)/'auditoria.json'
            with patch.object(tool, 'AutenticDEVClient', return_value=client), \
                    patch.object(tool, 'preflight_transporte'), \
                    patch.object(tool, 'verificar_tls', return_value={'tlsValidado': True}):
                result = tool.ejecutar_una_prueba(self.config, path)
                self.assertEqual(result['estado'], 'AUTENTIC_DEV_TOKEN_VALIDADO')
                self.assertEqual(result['http'], 200)
                self.assertEqual(result['peticiones'], 1)
                self.assertIsNone(client._token)
                text = path.read_text()
                self.assertNotIn('SECRETO-FICTICIO', text)
                self.assertNotIn('TOKEN-SINTETICO', text)
                with self.assertRaisesRegex(AutenticError, 'AUDITORIA_EXISTENTE_NO_REPETIR'):
                    tool.ejecutar_una_prueba(self.config, path)
            self.assertEqual(len(session.calls), 1)

    def test_http_rechazo_y_timeout_no_segundo_intento(self):
        for response, expected in ((Response({}, 401), 401), (requests.Timeout(), None)):
            session = Session(response)
            client = AutenticDEVClient(self.config, session=session)
            with TemporaryDirectory() as d:
                path = Path(d)/'auditoria.json'
                with patch.object(tool, 'AutenticDEVClient', return_value=client), \
                        patch.object(tool, 'preflight_transporte'), \
                        patch.object(tool, 'verificar_tls', return_value={'tlsValidado': True}):
                    result = tool.ejecutar_una_prueba(self.config, path)
                    self.assertEqual(result['http'], expected)
                    self.assertEqual(result['peticiones'], 1)
                    self.assertFalse(result['autenticado'])
                    with self.assertRaisesRegex(AutenticError, 'AUDITORIA_EXISTENTE_NO_REPETIR'):
                        tool.ejecutar_una_prueba(self.config, path)
                self.assertEqual(len(session.calls), 1)

    def test_indicador_jwt_aud_no_equivale_a_firma_validada(self):
        def jwt(aud):
            payload = base64.urlsafe_b64encode(json.dumps({'aud': aud}).encode()).decode().rstrip('=')
            return 'CABECERA.'+payload+'.FIRMA-SINTETICA'
        self.assertIn('NO_VERIFICADA_CRIPTOGRAFICAMENTE', tool.indicador_dev(jwt('stag-api.autenticsign.com')))
        with self.assertRaisesRegex(AutenticError, 'AUDIENCIA_JWT_DEV_NO_COINCIDE'):
            tool.indicador_dev(jwt('api.autenticsign.com'))
            p.write_bytes(b'\xff\xff')
            with self.assertRaisesRegex(AutenticError, 'CONFIGURACION_DEV_INVALIDA'):
                ConfiguracionDEV.desde_archivo(p)


if __name__ == '__main__':
    unittest.main()
