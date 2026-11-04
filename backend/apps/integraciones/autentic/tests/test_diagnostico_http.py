"""HTTP doubles only; no real provider traffic or dossier signing."""
from contextlib import closing
from cryptography.fernet import Fernet
import json
import sqlite3
import unittest
import requests
from ..contratos import AutenticError
from ..operaciones import RegistroFirmas
from . import test_cliente as fixtures
from .test_cliente import Response,token,solicitud


class DiagnosticTests(unittest.TestCase):
    setUp=fixtures.AutenticTests.setUp
    client=fixtures.AutenticTests.client
    def decoded(self,client):
        cipher=Fernet(Fernet.generate_key())
        result=client.diagnostico_cifrado(lambda text:cipher.encrypt(text.encode()).decode(),
            solicitud_id='SYNTHETIC_CASE',operacion_id='SYNTHETIC_OPERATION')
        self.assertNotIn('operationCode',result)
        return json.loads(cipher.decrypt(result.encode()))

    def test_error_body_is_redacted_then_encrypted_with_transport_reference(self):
        c,s=self.client(Response(token()),Response({'operationCode':2000,
            'operationMsg':'PDF header signature not found.','docId':'PRIVATE_ID',
            'access_token':'PRIVATE_TOKEN','files':[{'fileContent':'PRIVATE_PDF'}],
            'client_secret':'PRIVATE_SECRET','type':'reply','transactionId':'SYNTHETIC-PROVIDER-TX',
            'processedDate':'2026-10-29T12:00:00+00:00'},400),enabled=True)
        with self.assertRaises(AutenticError):
            c.firmar(solicitud(),clave='redacted',registro=self.registro,referencia_autorizacion='SYNTHETIC')
        report=self.decoded(c);event=report['eventos'][-1]
        self.assertEqual(event['operacion'],'FIRMA');self.assertEqual(event['http'],400)
        self.assertTrue(event['fecha']);self.assertTrue(event['referenciaLocal'])
        self.assertEqual(event['cuerpo']['operationCode'],2000)
        self.assertEqual(event['cuerpo']['mensajeClasificado'],'PDF_INVALIDO')
        self.assertEqual(event['cuerpo']['transactionId'],'SYNTHETIC-PROVIDER-TX')
        self.assertEqual(event['cuerpo']['processedDate'],'2026-10-29T12:00:00+00:00')
        self.assertNotIn('SYNTHETIC-PROVIDER-TX',json.dumps(c.auditoria_firma))
        for secret in ('PRIVATE_ID','PRIVATE_TOKEN','PRIVATE_PDF','PRIVATE_SECRET'):
            self.assertNotIn(secret,json.dumps(report));self.assertNotIn(secret,json.dumps(c.auditoria_firma))
        self.assertEqual(len(s.calls),2)

    def test_arbitrary_provider_message_and_non_json_are_not_custodied_as_text(self):
        for body in ({'operationMsg':'PRIVATE_PERSON PRIVATE_EMAIL PRIVATE_ID'},b'<html>PRIVATE_PERSON</html>',
                {'transactionId':self.config.client_secret},{'transactionId':'DOCUMENTO-FICTICIO'},
                {'transactionId':'EVIDENCIA-SINTETICA-NO-OTP'}):
            c,_=self.client(Response(token()),Response(body,503),enabled=True)
            db=sqlite3.connect(':memory:');self.addCleanup(db.close)
            with self.assertRaises(AutenticError):
                c.firmar(solicitud(),clave='private',registro=RegistroFirmas(db),
                    referencia_autorizacion='SYNTHETIC')
            report=self.decoded(c)
            self.assertNotIn('PRIVATE_PERSON',json.dumps(report))
            self.assertIn('sha256Fragmento',report['eventos'][-1]['cuerpo'])
            self.assertNotIn('transactionId',report['eventos'][-1]['cuerpo'])

    def test_error_body_is_bounded_and_truncation_explicit(self):
        c,_=self.client(Response(token()),Response(b'x'*80000,500),enabled=True)
        with self.assertRaises(AutenticError):
            c.firmar(solicitud(),clave='bounded',registro=self.registro,referencia_autorizacion='SYNTHETIC')
        body=self.decoded(c)['eventos'][-1]['cuerpo']
        self.assertEqual(body['bytesAnalizados'],32768);self.assertFalse(body['completo'])

    def test_timeout_and_connection_loss_keep_indeterminate_and_no_replay(self):
        for failure in (requests.Timeout('PRIVATE'),requests.ConnectionError('PRIVATE')):
            with closing(sqlite3.connect(':memory:')) as db:
                registry=RegistroFirmas(db)
                c,s=self.client(Response(token()),failure,enabled=True)
                for key in ('same','same','different'):
                    with self.assertRaises(AutenticError):c.firmar(solicitud(),clave=key,registro=registry,
                        referencia_autorizacion='SYNTHETIC')
                self.assertEqual(len(s.calls),2)
                self.assertEqual(db.execute('SELECT estado FROM autentic_operaciones').fetchone()[0],'INDETERMINADA')
                report=self.decoded(c)
                self.assertIsNone(report['eventos'][-1]['http'])
                self.assertNotIn('PRIVATE',json.dumps(report))

    def test_ambiguous_success_and_invalid_json_preserve_diagnostics_without_signature(self):
        for body in ({'operationCode':2000,'operationMsg':'PRIVATE'},b'not-json'):
            with closing(sqlite3.connect(':memory:')) as db:
                c,_=self.client(Response(token()),Response(body,201),enabled=True)
                with self.assertRaises(AutenticError):c.firmar(solicitud(),clave='ambiguous',registro=RegistroFirmas(db),
                    referencia_autorizacion='SYNTHETIC')
                self.assertEqual(self.decoded(c)['eventos'][-1]['http'],201)
                self.assertEqual(db.execute('SELECT estado FROM autentic_operaciones').fetchone()[0],'INDETERMINADA')
