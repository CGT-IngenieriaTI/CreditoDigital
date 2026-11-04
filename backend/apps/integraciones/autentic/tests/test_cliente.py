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

from ..cliente import AutenticDEVClient, ReciboFirma
from ..contratos import AutenticError, ConfiguracionDEV, DEV_URL, TOKEN_URL, preparar_firma
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

    def test_estado_no_inventa_endpoint(self):
        c, s = self.client()
        with self.assertRaisesRegex(AutenticError, 'CONSULTA_ESTADO_NO_DOCUMENTADA'):
            c.consultar_estado('ID-SINTETICO')
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
