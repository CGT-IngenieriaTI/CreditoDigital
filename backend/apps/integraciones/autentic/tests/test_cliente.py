"""Solo dobles y PDF sinteticos; nunca conexiones externas ni datos personales."""

import base64
from contextlib import closing
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from pypdf import PdfWriter
import requests

from ..cliente import AutenticClient, AutenticDEVClient, ReciboFirma, cargar_cliente_recuperacion
from ..contratos import (AutenticError, ConfiguracionDEV, ConfiguracionSTAGING, ConfiguracionProduccion,
                        DEV_URL, PRODUCTION_URL, TOKEN_URL, preparar_firma)
from ..evidencia import verificar_zip
from ..operaciones import RegistroFirmas


def pdf():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    return stream.getvalue()


def solicitud():
    return preparar_firma({'names': 'PRUEBA', 'lastNames': 'SINTETICA',
        'docId': 'DOCUMENTO-FICTICIO', 'secureKey': 'EVIDENCIA-SINTETICA-NO-OTP'}, {'prueba.pdf': pdf()})


def token(ttl=120):
    return {'access_token': 'TOKEN-SINTETICO', 'token_type': 'Bearer',
            'scope': 'signingcore:fullaccess', 'expires_in': ttl}


def recibo():
    return {'operationCode': 1000, 'transactionId': 'TRANSACCION-SINTETICA',
            'processedDate': '2026-10-13 12:00:00',
            'urlDocuments': 'https://descarga.example.test/prueba.zip?firma=NO-REAL'}


class Response:
    def __init__(self, body, status=200, mime='application/json'):
        self.body = json.dumps(body).encode() if isinstance(body, dict) else body
        self.status_code = status
        self.headers = {'Content-Type': mime}
        self.closed = False

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i+size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


class AutenticTests(unittest.TestCase):
    def test_production_cannot_reuse_staging_receipt_or_uncertain_journal(self):
        request=solicitud()
        for state in ('FIRMA_REPORTADA_NO_VERIFICADA','INDETERMINADA'):
            with self.subTest(state=state):
                self.db.execute('DELETE FROM autentic_operaciones');self.db.commit()
                self.registro.preparar('SHARED-KEY',request.fingerprint)
                self.registro.iniciar('SHARED-KEY');self.registro.terminar('SHARED-KEY',state,'DEV-ONLY')
                session=Session()
                client=AutenticDEVClient(ConfiguracionProduccion('SYNTHETIC','SECRET',
                    permitir_documentos=True),session=session)
                self.addCleanup(client.close)
                with self.assertRaisesRegex(AutenticError,'IDEMPOTENCIA_CONFLICTO'):
                    client.firmar(request,clave='SHARED-KEY',registro=self.registro,
                        referencia_autorizacion='SYNTHETIC-NOT-A-REAL-SIGNATURE')
                self.assertEqual(session.calls,[])

    def test_production_is_explicit_and_disabled_for_documents_by_default(self):
        config = ConfiguracionProduccion('SYNTHETIC', 'SYNTHETIC-SECRET')
        self.assertFalse(config.permitir_documentos)
        self.assertEqual(config.base_url, PRODUCTION_URL)
        self.assertEqual(config.audience, 'api.autenticsign.com')
        self.assertNotIn('SYNTHETIC-SECRET', repr(config))
        session = Session(Response(token()))
        client = AutenticDEVClient(config, session=session)
        self.addCleanup(client.close)
        client.autenticar()
        self.assertEqual(session.calls[0][2]['json']['audience'], config.audience)
        with self.assertRaises(AutenticError):
            client.firmar(solicitud(), clave='PROD-SYNTHETIC', registro=self.registro,
                          referencia_autorizacion='SYNTHETIC')
        self.assertEqual(len(session.calls), 1)

    def test_production_signature_uses_same_durable_contract_and_correct_endpoint(self):
        session = Session(Response(token()), Response(recibo(), status=201))
        client = AutenticDEVClient(ConfiguracionProduccion('SYNTHETIC', 'SECRET',
                                   permitir_documentos=True), session=session)
        self.addCleanup(client.close)
        result = client.firmar(solicitud(), clave='PROD-ONLY-MOCK', registro=self.registro,
                               referencia_autorizacion='SYNTHETIC-NO-REAL-SIGNATURE')
        self.assertEqual(session.calls[-1][1], PRODUCTION_URL + '/v2/signature/')
        self.assertEqual(result.estado, 'FIRMA_REPORTADA_NO_VERIFICADA')

    def test_production_loader_never_accepts_staging_profile(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'profile.json'
            (Path(folder)/'client.txt').write_text('SYNTHETIC')
            (Path(folder)/'secret.txt').write_text('SECRET')
            data = dict(audience='stag-api.autenticsign.com', grant_type='client_credentials',
                        client_id_file='client.txt', client_secret_file='secret.txt')
            path.write_text(json.dumps(data))
            with self.assertRaises(AutenticError):
                ConfiguracionProduccion.desde_archivo(path)
            data['audience'] = 'api.autenticsign.com'
            path.write_text(json.dumps(data))
            self.assertEqual(ConfiguracionProduccion.desde_archivo(path).ambiente, 'PRODUCCION')

    def test_profiles_are_coherent_and_staging_cannot_bypass_legacy_dev_journal(self):
        for cls, perfil, url, audience in (
                (ConfiguracionDEV, 'DEV', DEV_URL, 'stag-api.autenticsign.com'),
                (ConfiguracionSTAGING, 'STAGING', DEV_URL, 'stag-api.autenticsign.com'),
                (ConfiguracionProduccion, 'PRODUCCION', PRODUCTION_URL, 'api.autenticsign.com')):
            config = cls('SYNTHETIC-CLIENT', 'SYNTHETIC-SECRET')
            self.assertEqual((config.perfil, config.base_url, config.audience), (perfil, url, audience))
            self.assertFalse(config.permitir_documentos)
            self.assertNotIn('SYNTHETIC-CLIENT', repr(config))
            self.assertNotIn('SYNTHETIC-SECRET', repr(config))
        request = solicitud()
        self.registro.preparar('original', request.fingerprint)
        self.registro.iniciar('original'); self.registro.terminar('original', 'INDETERMINADA')
        session = Session()
        client = AutenticDEVClient(ConfiguracionSTAGING('SYNTHETIC', 'SECRET', permitir_documentos=True), session=session)
        for key in ('original', 'different'):
            with self.assertRaises(AutenticError):
                client.firmar(request, clave=key, registro=self.registro, referencia_autorizacion='SYNTHETIC')
        self.assertEqual(session.calls, [])

    def test_recovery_factory_is_neutral_get_only_and_cannot_authorize_any_post(self):
        for perfil, audience in (('DEV', 'stag-api.autenticsign.com'), ('STAGING', 'stag-api.autenticsign.com'),
                ('PRODUCCION', 'api.autenticsign.com')):
            with self.subTest(perfil=perfil), TemporaryDirectory() as folder:
                root = Path(folder)
                (root/'client.txt').write_text('SYNTHETIC-CLIENT')
                (root/'secret.txt').write_text('SYNTHETIC-ROTATED-TEST-ONLY')
                metadata = dict(audience=audience, grant_type='client_credentials', client_id_file='client.txt',
                    client_secret_file='secret.txt', hosts_descarga=['descarga.example.test'], timeout=[5, 20])
                path = root/'profile.json'; path.write_text(json.dumps(metadata))
                session = Session()
                client = cargar_cliente_recuperacion(perfil, path, referencia_recuperacion='CUSTODIED-TEST-REF', session=session)
                self.assertIsInstance(client, AutenticClient)
                self.assertTrue(client.config.solo_recuperacion)
                self.assertTrue(client.config.permitir_documentos)
                self.assertEqual(client.config.perfil, perfil)
                actions = (client.autenticar,
                    lambda:client.firmar(solicitud(), clave='factory', registro=self.registro, referencia_autorizacion='CUSTODIED-TEST-REF'),
                    lambda:client.certificar('TX', 'DOC', referencia_autorizacion='CUSTODIED-TEST-REF'),
                    lambda:client._request('POST', TOKEN_URL),
                    lambda:client._request('POST', client.config.base_url+'/v2/signature/'),
                    lambda:client._request('GET', 'https://descarga.example.test/a.zip', bearer=True),
                    lambda:AutenticClient(client.config, session=session).autenticar())
                for action in actions:
                    with self.assertRaisesRegex(AutenticError, 'SOLO_RECUPERACION_POST_BLOQUEADO'):action()
                with self.assertRaisesRegex(AutenticError, 'DESCARGA_SIN_RECIBO_CUSTODIADO'):
                    client._request('GET', 'https://descarga.example.test/arbitrary.zip')
                self.assertEqual(session.calls, [])
                self.assertEqual(self.db.execute('SELECT COUNT(*) FROM autentic_operaciones').fetchone()[0], 0)
                stream = BytesIO()
                with ZipFile(stream, 'w') as archive:archive.writestr('prueba.pdf', pdf())
                session.responses.append(Response(stream.getvalue(), mime='application/zip'))
                receipt = ReciboFirma('TX', 'FECHA', recibo()['urlDocuments'], ambiente=client.config.ambiente)
                with self.assertRaisesRegex(AutenticError, 'REFERENCIA_RECUPERACION_NO_COINCIDE'):
                    client.recuperar_documentos(receipt, ['prueba.pdf'], referencia_autorizacion='OTHER')
                self.assertEqual(session.calls, [])
                result = client.recuperar_documentos(receipt, ['prueba.pdf'], referencia_autorizacion='CUSTODIED-TEST-REF')
                self.assertFalse(result['prueba.pdf'].firma_criptografica_validada)
                self.assertEqual([call[0] for call in session.calls], ['GET'])
                self.assertIsNone(client._url_recuperacion_en_curso)
                self.assertNotIn('Authorization', session.calls[0][2]['headers'])
                self.assertNotIn('SYNTHETIC-ROTATED-TEST-ONLY', path.read_text())

    def test_recovery_factory_requires_explicit_profile_reference_and_allowlisted_hosts(self):
        from unittest.mock import patch
        for profile, reference in (('INVALID','EXISTING'), ('STAGING',''), (None,'EXISTING')):
            with patch.object(ConfiguracionSTAGING, 'desde_archivo') as load:
                with self.assertRaisesRegex(AutenticError, 'PERFIL_RECUPERACION_INVALIDO'):
                    cargar_cliente_recuperacion(profile, 'UNREAD', referencia_recuperacion=reference)
                load.assert_not_called()
        with patch.object(ConfiguracionSTAGING, 'desde_archivo', return_value=ConfiguracionSTAGING('SYNTHETIC','SECRET')):
            with self.assertRaisesRegex(AutenticError, 'HOSTS_RECUPERACION_REQUERIDOS'):
                cargar_cliente_recuperacion('STAGING', 'UNREAD', referencia_recuperacion='EXISTING')

    def test_inline_credentials_and_arbitrary_endpoint_are_not_accepted_in_profile_json(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'profile.json'
            for cls in (ConfiguracionDEV, ConfiguracionSTAGING, ConfiguracionProduccion):
                data = dict(audience=cls.AUDIENCE, grant_type='client_credentials',
                            client_id='SYNTHETIC-CLIENT', client_secret='SYNTHETIC-SECRET')
                path.write_text(json.dumps(data))
                with self.assertRaises(AutenticError) as error:cls.desde_archivo(path)
                self.assertNotIn('SYNTHETIC', str(error.exception))
            path.write_text(json.dumps(dict(audience='stag-api.autenticsign.com', grant_type='client_credentials',
                client_id_file='client.txt', client_secret_file='secret.txt', base_url='https://other.invalid')))
            with self.assertRaises(AutenticError):ConfiguracionSTAGING.desde_archivo(path)

    def test_credential_files_are_literal_bounded_and_do_not_decode_or_log_secrets(self):
        with TemporaryDirectory() as folder:
            client, secret = Path(folder)/'client', Path(folder)/'secret'
            client.write_text('SYNTHETIC-CLIENT\n', encoding='utf-8-sig')
            secret.write_text('SYNTHETIC+/%=\r\n', encoding='utf-8-sig')
            config = ConfiguracionSTAGING.desde_archivos(client, secret)
            self.assertEqual(config.client_id, 'SYNTHETIC-CLIENT')
            self.assertEqual(config.client_secret, 'SYNTHETIC+/%=')
            for body in ('', 'x'*4097, 'MULTI\nLINE', 'SECRET\x00VALUE'):
                secret.write_text(body)
                with self.assertRaises(AutenticError):ConfiguracionSTAGING.desde_archivos(client, secret)
            with self.assertRaises(AutenticError):ConfiguracionSTAGING.desde_archivos(client, client)

    def test_http200_and_201_accept_only_a_consistent_successful_reply(self):
        from unittest.mock import patch
        for status in (200, 201):
            for cls in (ConfiguracionDEV, ConfiguracionSTAGING, ConfiguracionProduccion):
                with self.subTest(status=status, profile=cls.__name__), closing(sqlite3.connect(':memory:')) as db:
                    body = {**recibo(), 'operationMsg':'DOCS_SIGNED', 'type':'reply_signature'}
                    session = Session(Response(token()), Response(body, status))
                    client = AutenticDEVClient(cls('SYNTHETIC', 'SECRET', permitir_documentos=True), session=session)
                    with patch('requests.sessions.Session.request', side_effect=AssertionError('NO_NETWORK')):
                        receipt = client.firmar(solicitud(), clave='http-success', registro=RegistroFirmas(db),
                                               referencia_autorizacion='SYNTHETIC')
                    self.assertEqual(receipt.estado, 'FIRMA_REPORTADA_NO_VERIFICADA')
                    self.assertEqual(client.auditoria_firma['http'], status)
                    self.assertEqual(receipt.ambiente, client.config.ambiente)
                    self.assertEqual(len(session.calls), 2)

    def test_success_http_does_not_override_bad_code_url_date_or_conflicting_message(self):
        cases = ({'operationCode':True}, {'operationCode':'1000'}, {'operationCode':2000},
            {'transactionId':''}, {'transactionId':' PRIVATE\nVALUE'}, {'processedDate':'invalid'},
            {'processedDate':'2026-11-06'}, {'urlDocuments':'http://download.example.invalid/a.zip'},
            {'urlDocuments':'https://user:password@download.example.invalid/a.zip'},
            {'urlDocuments':'https://download.example.invalid/a.zip#fragment'},
            {'operationMsg':'ERROR'}, {'type':'reply'}, {'type':True})
        for status in (200, 201):
            for change in cases:
                with self.subTest(status=status, change=change), closing(sqlite3.connect(':memory:')) as db:
                    client, session = self.client(Response(token()), Response({**recibo(), **change}, status), enabled=True)
                    journal = RegistroFirmas(db)
                    with self.assertRaisesRegex(AutenticError, '^ENVIO_INDETERMINADO_NO_REPETIR$'):
                        client.firmar(solicitud(), clave='bad-reply', registro=journal, referencia_autorizacion='SYNTHETIC')
                    self.assertEqual(journal.preparar('bad-reply', solicitud().fingerprint).estado, 'INDETERMINADA')
                    self.assertEqual(len(session.calls), 2)

    def test_signed_url_is_bounded_without_truncating_private_query_or_retaining_extra_fields(self):
        body = {**recibo(), 'urlDocuments':'https://descarga.example.test/a.zip?signature='+'a'*2000,
                'access_token':'PRIVATE_ECHO', 'client_secret':'PRIVATE_ECHO', 'account':'PRIVATE_ECHO'}
        receipt = ReciboFirma.desde_respuesta(body, 'DEV')
        self.assertEqual(receipt.url_documentos, body['urlDocuments'])
        self.assertNotIn('PRIVATE_ECHO', json.dumps(receipt.respuesta_original))
        self.assertNotIn(body['urlDocuments'], repr(receipt))
        for url in ('https://descarga.example.test/a.zip?x='+'a'*8192,
                'https://descarga.example.test/a.zip?x=bad value'):
            with self.assertRaises(AutenticError):ReciboFirma.desde_respuesta({**body, 'urlDocuments':url}, 'DEV')

    def test_http200_does_not_require_or_invent_type_or_requesttype(self):
        for extra in ({}, {'requesttype':'UNSPECIFIED_PROVIDER_VALUE'}):
            with self.subTest(extra=extra), closing(sqlite3.connect(':memory:')) as db:
                body = {**recibo(), 'operationMsg':'DOCS_SIGNED', **extra}
                client, session = self.client(Response(token()), Response(body, 200), enabled=True)
                receipt = client.firmar(solicitud(), clave='no-type', registro=RegistroFirmas(db),
                    referencia_autorizacion='SYNTHETIC-MOCK-ONLY')
                self.assertEqual(receipt.tipo_respuesta, '')
                self.assertNotIn('type', receipt.respuesta_original)
                self.assertNotIn('requesttype', receipt.respuesta_original)
                self.assertEqual(receipt.estado, 'FIRMA_REPORTADA_NO_VERIFICADA')
                self.assertEqual(len(session.calls), 2)
                client.close()
                self.assertIsNone(client._token)

    def test_http200_without_provider_type_or_zoned_date_never_promotes_contractual_signature(self):
        from dataclasses import asdict
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        from ..evidencia import verificar_firma_contractual
        from .test_verificacion_documental import context
        now = datetime.now(timezone.utc)
        original = pdf()
        # Isolate receipt/time gates even when all independent crypto checks pass.
        crypto = dict(correspondenciaOriginal=True, firmaIntegra=True, cadenaConfiable=True,
            identidadCertificado=True, fechaConfiable=True, rolCertificado='FIRMANTE_TITULAR',
            participacionElectronicaAcreditada=True, firmadoEn=(now-timedelta(seconds=30)).isoformat())
        for missing_type, missing_zone in ((True, False), (False, True), (True, True)):
            with self.subTest(missing_type=missing_type, missing_zone=missing_zone), closing(sqlite3.connect(':memory:')) as db:
                candidate = context(original, original, now)
                body = {**candidate['recibo']['respuesta_original'], 'operationMsg':'DOCS_SIGNED'}
                if missing_type:
                    body.pop('type')
                if missing_zone:
                    body['processedDate'] = datetime.fromisoformat(body['processedDate']).replace(tzinfo=None).isoformat()
                client, session = self.client(Response(token()), Response(body, 200), enabled=True)
                self.addCleanup(client.close)
                with patch('requests.sessions.Session.request', side_effect=AssertionError('RED_PROHIBIDA')):
                    receipt = client.firmar(solicitud(), clave='transport-only', registro=RegistroFirmas(db),
                        referencia_autorizacion='SYNTHETIC-MOCK-ONLY')
                self.assertEqual(receipt.estado, 'FIRMA_REPORTADA_NO_VERIFICADA')
                self.assertEqual(client.auditoria_firma['http'], 200)
                self.assertEqual(len(session.calls), 2)
                candidate['recibo'] = asdict(receipt)
                with patch('apps.integraciones.autentic.pdf_criptografia.verificar_pdf', return_value=crypto):
                    with self.assertRaisesRegex(AutenticError, '^FIRMA_CONTRACTUAL_EVIDENCIA_PENDIENTE$') as error:
                        verificar_firma_contractual(original=original, firmado=original,
                            transaccion='SYNTHETIC-TX', documento_identidad='SYNTHETIC-ID',
                            contexto=candidate, momento=now)
                report = error.exception.resultado
                self.assertTrue(report['firmaCriptografica'])
                self.assertFalse(report['firmaAcreditada'])
                self.assertEqual(report['estado'], 'PENDIENTE_VERIFICACION')
                self.assertEqual(report['reciboProveedor'], not missing_type)
                self.assertEqual(report['fechaProveedorConZona'], not missing_zone)
                if missing_type:self.assertIn('reciboProveedor', report['pendientes'])
                if missing_zone:self.assertIn('fechaProveedorConZona', report['pendientes'])

    def test_v4_uses_lowercase_docId_and_preserves_documented_identity_fields(self):
        metadata={'names':'SYNTHETIC','lastNames':'ONLY','docId':'SYNTHETIC-ID','secureKey':'SYNTHETIC-EVIDENCE',
            'identityValidation':'OTP','identityValidationId':'SYNTHETIC-NOT-REAL',
            'identityValidationEmail':'synthetic@example.invalid'}
        request=preparar_firma(metadata,{'test.pdf':pdf()})
        self.assertEqual(request.payload['metadata'],metadata)
        for invalid in ({**metadata,'DocId':metadata['docId']},
                {k:v for k,v in metadata.items() if k!='identityValidationId'},
                {k:v for k,v in metadata.items() if k!='identityValidationEmail'}):
            with self.assertRaises(AutenticError):preparar_firma(invalid,{'test.pdf':pdf()})

    def test_documented_success_fields_are_retained_without_certifying_signature(self):
        body={**recibo(),'operationMsg':'DOCS_SIGNED','type':'reply_signature'}
        c,s=self.client(Response(token()),Response(body,status=201),enabled=True)
        result=c.firmar(solicitud(),clave='documented-response',registro=self.registro,referencia_autorizacion='SYNTHETIC')
        self.assertEqual(result.codigo_operacion,1000);self.assertEqual(result.mensaje_operacion,'DOCS_SIGNED')
        self.assertEqual(result.tipo_respuesta,'reply_signature');self.assertEqual(result.fecha_proveedor,body['processedDate'])
        self.assertEqual(result.estado,'FIRMA_REPORTADA_NO_VERIFICADA')
        self.assertEqual(result.respuesta_original,body)
        self.assertEqual(c.auditoria_firma['http'],201)
        self.assertNotIn(body['urlDocuments'],repr(result))
        self.assertNotIn('documentNumber',body)

    def test_private_download_dns_is_rejected_before_get(self):
        from unittest.mock import patch
        c=AutenticDEVClient(replace(self.config,permitir_documentos=True,hosts_descarga=('allowed.example.test',)))
        self.addCleanup(c.close)
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),\
                patch.object(c,'_request',side_effect=AssertionError('NO_HTTP')):
            with self.assertRaises(AutenticError) as error:
                c.recuperar_documentos(ReciboFirma('SYNTHETIC','2026-10-29','https://allowed.example.test/a.zip'),
                    ['test.pdf'],referencia_autorizacion='SYNTHETIC')
            self.assertEqual(error.exception.codigo,'DESTINO_DESCARGA_NO_PUBLICO')
    def setUp(self):
        self.config = ConfiguracionDEV(client_id='CLIENTE-FICTICIO', client_secret='SECRETO-FICTICIO')
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.registro = RegistroFirmas(self.db)

    def client(self, *responses, enabled=False, **kwargs):
        s = Session(*responses)
        c = AutenticDEVClient(replace(self.config, permitir_documentos=enabled), session=s, **kwargs)
        return c, s

    def test_token_expiracion_real_cache_y_renovacion(self):
        now = [0]
        c, s = self.client(Response(token(10)), Response(token(86400)), reloj=lambda: now[0])
        self.assertEqual(c.autenticar()['expires_in'], 10)
        now[0] = 8
        self.assertTrue(c.autenticar()['token_en_cache'])
        now[0] = 9
        self.assertEqual(c.autenticar()['expires_in'], 86400)
        self.assertEqual(len(s.calls), 2)
        self.assertTrue(all(call[1] == TOKEN_URL for call in s.calls))
        self.assertFalse(s.trust_env)
        self.assertTrue(all(call[2]['verify'] is True and call[2]['allow_redirects'] is False for call in s.calls))

    def test_secreto_y_url_no_aparecen_en_repr(self):
        self.assertNotIn('SECRETO-FICTICIO', repr(self.config))
        self.assertNotIn('firma=', repr(ReciboFirma('ID', 'FECHA', recibo()['urlDocuments'])))
        self.assertNotIn('DOCUMENTO-FICTICIO', repr(solicitud()))
        self.assertNotIn('PRIVATE_PROVIDER_VALUE',repr(ReciboFirma('ID','FECHA',recibo()['urlDocuments'],
            respuesta_original={'opaque':'PRIVATE_PROVIDER_VALUE'})))

    def test_config_solo_dev(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'config.json'
            p.write_text(json.dumps({'audience': 'api.autenticsign.com', 'grant_type': 'client_credentials',
                'client_id': 'PRUEBA', 'client_secret': 'PRUEBA'}))
            with self.assertRaisesRegex(AutenticError, 'CONFIGURACION_DEV_INVALIDA'):
                ConfiguracionDEV.desde_archivo(p)

    def test_token_malformado_y_credenciales_rechazadas(self):
        for body, status, code in ((token(True), 200, 'TOKEN_INVALIDO'),
                ({**token(), 'token_type': {}}, 200, 'TOKEN_INVALIDO'),
                (b'{"x":1,"x":2}', 200, 'RESPUESTA_INVALIDA'),
                ({'mensaje': 'SECRETO-QUE-NO-DEBE-SALIR'}, 401, 'AUTENTICACION_RECHAZADA')):
            with self.subTest(code=code):
                c, s = self.client(Response(body, status))
                with self.assertRaisesRegex(AutenticError, '^'+code+'$'):
                    c.autenticar()
                self.assertEqual(len(s.calls), 1)

    def test_timeout_token_y_redirect_no_reintentan(self):
        for response, code in ((requests.Timeout(), 'TIMEOUT_SIN_REINTENTO'),
                               (Response({}, 302), 'HTTP_NO_EXITOSO')):
            c, s = self.client(response)
            with self.assertRaisesRegex(AutenticError, '^'+code+'$'):
                c.autenticar()
            self.assertEqual(len(s.calls), 1)

    def test_journal_no_acepta_conexion_financiera(self):
        with self.assertRaisesRegex(AutenticError, 'JOURNAL_SQLITE_EXCLUSIVO_REQUERIDO'):
            RegistroFirmas(object())

    def test_documentos_off_por_defecto_sin_red(self):
        c, s = self.client()
        with self.assertRaisesRegex(AutenticError, 'DOCUMENTOS_DEV_NO_AUTORIZADOS'):
            c.firmar(solicitud(), clave='caso1', registro=self.registro, referencia_autorizacion='PRUEBA')
        self.assertFalse(s.calls)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM autentic_operaciones').fetchone()[0], 0)

    def test_una_firma_y_replay_sin_segundo_post(self):
        c, s = self.client(Response(token()), Response(recibo(), 201), enabled=True)
        request = solicitud()
        result = c.firmar(request, clave='caso1', registro=self.registro, referencia_autorizacion='SOLO-DOBLE')
        replay = c.firmar(request, clave='caso1', registro=self.registro, referencia_autorizacion='SOLO-DOBLE')
        self.assertEqual(result.transaccion, replay.transaccion)
        self.assertEqual([x[1] for x in s.calls], [TOKEN_URL, DEV_URL+'/v2/signature/'])
        self.assertEqual(result.estado, 'FIRMA_REPORTADA_NO_VERIFICADA')

    def test_timeout_envio_no_reintentable(self):
        c, s = self.client(Response(token()), requests.Timeout('CUERPO-PRIVADO'), enabled=True)
        request = solicitud()
        with self.assertRaisesRegex(AutenticError, '^ENVIO_INDETERMINADO_NO_REPETIR$'):
            c.firmar(request, clave='caso1', registro=self.registro, referencia_autorizacion='SOLO-DOBLE')
        with self.assertRaisesRegex(AutenticError, 'REENVIO_BLOQUEADO'):
            c.firmar(request, clave='caso1', registro=self.registro, referencia_autorizacion='SOLO-DOBLE')
        self.assertEqual(len(s.calls), 2)

    def test_http_failure_preserves_only_safe_transport_facts_without_retry(self):
        for status,code in [(400,'HTTP_NO_EXITOSO'),(401,'AUTENTICACION_RECHAZADA'),
                (503,'HTTP_NO_EXITOSO')]:
            with self.subTest(status=status):
                db=sqlite3.connect(':memory:')
                try:
                    c,s=self.client(Response(token()),Response({'mensaje':'PRIVATE_PROVIDER_BODY'},status),enabled=True)
                    with self.assertRaisesRegex(AutenticError,'ENVIO_INDETERMINADO_NO_REPETIR'):
                        c.firmar(solicitud(),clave='once',registro=RegistroFirmas(db),referencia_autorizacion='SYNTHETIC_ONLY')
                    self.assertEqual(c.auditoria_firma['http'],status)
                    self.assertEqual(c.auditoria_firma['codigo'],code)
                    self.assertEqual(c.auditoria_firma['peticiones'],1)
                    self.assertNotIn('PRIVATE_PROVIDER_BODY',json.dumps(c.auditoria_firma))
                    self.assertEqual(len(s.calls),2)
                    self.assertEqual(db.execute('SELECT estado FROM autentic_operaciones').fetchone()[0],'INDETERMINADA')
                finally:db.close()

    def test_respuesta_firma_invalida_tambien_bloquea_reenvio(self):
        c, s = self.client(Response(token()), Response({'operationCode': 2000}), enabled=True)
        with self.assertRaisesRegex(AutenticError, 'ENVIO_INDETERMINADO'):
            c.firmar(solicitud(), clave='caso1', registro=self.registro, referencia_autorizacion='SOLO-DOBLE')
        self.assertEqual(self.db.execute('SELECT estado FROM autentic_operaciones').fetchone()[0], 'INDETERMINADA')

    def test_timeout_audit_keeps_no_exception_payload(self):
        c,s=self.client(Response(token()),requests.Timeout('PRIVATE_EXCEPTION'),enabled=True)
        with self.assertRaisesRegex(AutenticError,'ENVIO_INDETERMINADO_NO_REPETIR'):
            c.firmar(solicitud(),clave='once',registro=self.registro,referencia_autorizacion='SYNTHETIC_ONLY')
        self.assertEqual(c.auditoria_firma['peticiones'],1)
        self.assertIsNone(c.auditoria_firma['http'])
        self.assertEqual(c.auditoria_firma['codigo'],'TIMEOUT_SIN_REINTENTO')
        self.assertNotIn('PRIVATE_EXCEPTION',json.dumps(c.auditoria_firma))
        self.assertEqual(len(s.calls),2)

    def test_reabrir_journal_tras_timeout_no_reenvia(self):
        request = solicitud()
        with TemporaryDirectory() as d:
            path = str(Path(d)/'journal.sqlite3')
            with closing(sqlite3.connect(path)) as first:
                c, _ = self.client(Response(token()), requests.Timeout(), enabled=True)
                with self.assertRaisesRegex(AutenticError, 'ENVIO_INDETERMINADO'):
                    c.firmar(request, clave='caso', registro=RegistroFirmas(first), referencia_autorizacion='SOLO-DOBLE')
            with closing(sqlite3.connect(path)) as reopened:
                c, s = self.client(enabled=True)
                with self.assertRaisesRegex(AutenticError, 'REENVIO_BLOQUEADO'):
                    c.firmar(request, clave='caso', registro=RegistroFirmas(reopened), referencia_autorizacion='SOLO-DOBLE')
                self.assertFalse(s.calls)

    def test_idempotencia_conflicto_y_concurrencia_durable(self):
        with TemporaryDirectory() as d:
            path = str(Path(d)/'journal.sqlite3')
            with closing(sqlite3.connect(path)) as a, closing(sqlite3.connect(path)) as b:
                ra, rb = RegistroFirmas(a), RegistroFirmas(b)
                ra.preparar('caso', 'a'*64)
                with self.assertRaisesRegex(AutenticError, 'IDEMPOTENCIA_CONFLICTO'):
                    rb.preparar('caso', 'b'*64)
                ra.iniciar('caso')
                self.assertEqual(rb.preparar('caso', 'a'*64).estado, 'ENVIANDO')
                with self.assertRaisesRegex(AutenticError, 'REENVIO_BLOQUEADO'):
                    rb.iniciar('caso')

    def test_payload_mutado_bloqueado_antes_de_red(self):
        request = solicitud()
        request.payload['metadata']['names'] = 'CAMBIO'
        c, s = self.client(enabled=True)
        with self.assertRaisesRegex(AutenticError, 'SOLICITUD_FIRMA_INVALIDA'):
            c.firmar(request, clave='caso', registro=self.registro, referencia_autorizacion='SOLO-DOBLE')
        self.assertFalse(s.calls)

    def test_estado_sin_identificador_no_inventa_endpoint_o_identificador(self):
        c, s = self.client()
        with self.assertRaisesRegex(AutenticError, 'TRANSACTION_ID_PROVEEDOR_REQUERIDO'):
            c.consultar_estado(None)
        self.assertFalse(s.calls)

    def test_zip_integridad_rutas_y_nombre(self):
        out = BytesIO()
        with ZipFile(out, 'w') as z:
            z.writestr('prueba.pdf', pdf())
        evidence = verificar_zip(out.getvalue(), ['prueba.pdf'])['prueba.pdf']
        self.assertEqual(len(evidence.sha256), 64)
        self.assertFalse(evidence.firma_criptografica_validada)
        with self.assertRaisesRegex(AutenticError, 'ZIP_INVALIDO'):
            verificar_zip(out.getvalue(), ['otro.pdf'])
        out = BytesIO()
        with ZipFile(out, 'w') as z:
            z.writestr('../prueba.pdf', pdf())
        with self.assertRaisesRegex(AutenticError, 'ZIP_INVALIDO'):
            verificar_zip(out.getvalue(), ['../prueba.pdf'])

    def test_descarga_ssrf_y_bearer_no_reenviado(self):
        out = BytesIO()
        with ZipFile(out, 'w') as z:
            z.writestr('prueba.pdf', pdf())
        c, s = self.client(Response(out.getvalue(), mime='application/zip'), enabled=True)
        r = ReciboFirma('ID', 'FECHA', recibo()['urlDocuments'])
        with self.assertRaisesRegex(AutenticError, 'DESTINO_DESCARGA_NO_AUTORIZADO'):
            c.recuperar_documentos(r, ['prueba.pdf'], referencia_autorizacion='SOLO-DOBLE')
        self.assertFalse(s.calls)
        c.config = replace(c.config, hosts_descarga=('descarga.example.test',))
        c.recuperar_documentos(r, ['prueba.pdf'], referencia_autorizacion='SOLO-DOBLE')
        self.assertNotIn('Authorization', s.calls[0][2]['headers'])
        self.assertEqual(s.calls[0][0], 'GET')

    def test_certificacion_pdf_y_document_number_obligatorio(self):
        c, s = self.client(Response(token()), Response({'operationCode': 1000,
            'certificationContent': base64.b64encode(pdf()).decode()}), enabled=True)
        with self.assertRaisesRegex(AutenticError, 'DOCUMENT_NUMBER_OFICIAL_REQUERIDO'):
            c.certificar('ID', None, referencia_autorizacion='SOLO-DOBLE')
        evidence = c.certificar('ID', 'DOCUMENTO-OFICIAL-SINTETICO', clave='cert1',
            registro=self.registro, referencia_autorizacion='SOLO-DOBLE')
        self.assertFalse(evidence.firma_criptografica_validada)
        self.assertEqual(s.calls[-1][2]['json']['certificationType'], 'SIGNATURE')

    def test_limites_pdf_y_json(self):
        with self.assertRaisesRegex(AutenticError, 'PDF_INVALIDO'):
            preparar_firma(solicitud().payload['metadata'], {'prueba.pdf': b'%PDF-NO-VALIDO'})
        c, s = self.client(Response(b'x'*1_000_001))
        with self.assertRaisesRegex(AutenticError, 'RESPUESTA_EXCEDE_LIMITE'):
            c.autenticar()


if __name__ == '__main__':
    unittest.main()
