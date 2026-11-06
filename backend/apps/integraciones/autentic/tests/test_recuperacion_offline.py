"""Mock recovery and authenticated local custody, never provider evidence."""

import base64
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..cliente import AutenticDEVClient, ReciboFirma
from ..contratos import AutenticError, ConfiguracionDEV, ConfiguracionProduccion
from ..evidencia import EvidenciaPDF, custodiar_descarga, leer_descarga_custodiada
from ..operaciones import RegistroFirmas
from .test_cliente import Response, Session, pdf, solicitud


class RecuperacionTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('RED_PROHIBIDA'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.body = pdf()
        self.now = datetime.now(timezone.utc).isoformat()
        self.receipt = ReciboFirma('SYNTHETIC-TX', self.now, 'https://download.example.invalid/docs.zip')

    def client(self, *responses, production=False):
        config = ConfiguracionProduccion if production else ConfiguracionDEV
        session = Session(*responses)
        client = AutenticDEVClient(config('SYNTHETIC-CLIENT', 'SYNTHETIC-SECRET',
            permitir_documentos=True, hosts_descarga=('download.example.invalid',)), session=session)
        self.addCleanup(client.close)
        return client, session

    def download(self, client, receipt=None):
        return client.recuperar_documentos(receipt or self.receipt, ['original.pdf'],
            referencia_autorizacion='SYNTHETIC-MOCK-ONLY')

    def zip_response(self):
        out = BytesIO()
        with ZipFile(out, 'w') as archive:
            archive.writestr('original.pdf', self.body)
        return Response(out.getvalue(), mime='application/zip')

    def test_indeterminate_or_foreign_receipt_does_not_authenticate_or_download(self):
        client, session = self.client()
        for change in ({'estado': 'INDETERMINADA'}, {'estado': 'ENVIANDO'},
                {'ambiente': 'PRODUCCION'}, {'transaccion': ''}, {'codigo_operacion': True},
                {'respuesta_original': {'transactionId': 'OTHER'}}):
            with self.subTest(change=change), self.assertRaisesRegex(AutenticError, 'RECIBO_RECUPERACION'):
                self.download(client, replace(self.receipt, **change))
        self.assertEqual(session.calls, [])
        self.assertEqual(client.auditoria_token['peticiones'], 0)

    def test_production_download_keeps_its_environment_and_never_uses_bearer(self):
        client, session = self.client(self.zip_response(), production=True)
        result = self.download(client, replace(self.receipt, ambiente='PRODUCCION'))
        self.assertFalse(result['original.pdf'].firma_criptografica_validada)
        self.assertEqual(client.auditoria_descarga['ambiente'], 'PRODUCCION')
        self.assertEqual(len(session.calls), 1)
        method, _, options = session.calls[0]
        self.assertEqual(method, 'GET')
        self.assertNotIn('Authorization', options['headers'])
        self.assertFalse(options['allow_redirects'])
        self.assertTrue(options['verify'])

    def test_failed_recovery_cannot_reuse_previous_download_provenance(self):
        client, _ = self.client(self.zip_response(), Response(b'INVALID-ZIP', mime='application/zip'))
        self.download(client)
        self.assertTrue(client.auditoria_descarga)
        with self.assertRaises(AutenticError):self.download(client)
        self.assertEqual(client.auditoria_descarga, {})
        client.auditoria_descarga = {'http': 200}
        with self.assertRaises(AutenticError):
            self.download(client, replace(self.receipt, estado='INDETERMINADA'))
        self.assertEqual(client.auditoria_descarga, {})

    def test_production_verification_requires_matching_receipt_environment_without_attesting_signer(self):
        from ..evidencia import analizar_firma_documental
        from .test_verificacion_documental import context
        now = datetime.now(timezone.utc)
        candidate = context(self.body, self.body, now)
        candidate['recibo']['ambiente'] = 'PRODUCCION'
        candidate['descarga']['procedencia']['ambiente'] = 'PRODUCCION'
        result = analizar_firma_documental(original=self.body, firmado=self.body,
            transaccion='SYNTHETIC-TX', documento_identidad='SYNTHETIC-ID', contexto=candidate, momento=now)
        self.assertTrue(result['procedenciaDescarga'])
        self.assertFalse(result['firmaAcreditada'])
        self.assertFalse(result['identidadFirmante'])
        self.assertFalse(result['participacionFirmante'])

    def test_original_indeterminate_journal_is_not_modified_by_download_or_verification(self):
        from ..evidencia import analizar_firma_documental
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        journal = RegistroFirmas(db)
        request = solicitud()
        journal.preparar('ORIGINAL-DEV', request.fingerprint)
        journal.iniciar('ORIGINAL-DEV')
        journal.terminar('ORIGINAL-DEV', 'INDETERMINADA')
        before = '\n'.join(db.iterdump())
        client, session = self.client()
        with self.assertRaisesRegex(AutenticError, 'REENVIO_BLOQUEADO'):
            client.firmar(request, clave='ORIGINAL-DEV', registro=journal,
                referencia_autorizacion='SYNTHETIC-MOCK-ONLY')
        with self.assertRaisesRegex(AutenticError, 'ENVIO_DUPLICADO_OTRA_CLAVE'):
            client.firmar(request, clave='NEW-KEY', registro=journal,
                referencia_autorizacion='SYNTHETIC-MOCK-ONLY')
        result = analizar_firma_documental(original=self.body, firmado=self.body,
            transaccion='SYNTHETIC-TX', documento_identidad='SYNTHETIC-ID')
        self.assertFalse(result['firmaAcreditada'])
        self.assertEqual('\n'.join(db.iterdump()), before)
        self.assertEqual(session.calls, [])


class CustodiaTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('RED_PROHIBIDA'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.body = pdf()
        self.evidence = EvidenciaPDF(sha256(self.body).hexdigest(), self.body)
        self.now = datetime.now(timezone.utc).isoformat()
        self.receipt = ReciboFirma('SYNTHETIC-TX', self.now, 'https://download.example.invalid/docs.zip?PRIVATE')
        self.binding = dict(solicitudId=str(uuid4()), envioId=str(uuid4()), documentoOperacionId=str(uuid4()),
            originalSha256=sha256(self.body).hexdigest(), transaccionReferencia=sha256(b'SYNTHETIC-TX').hexdigest(),
            ambiente='DEV')
        self.provenance = dict(version='AUTENTIC_DESCARGA_V1', ambiente='DEV', http=200,
            TLSVerificado=True, sinRedirecciones=True, zipSha256='a'*64,
            urlSha256=sha256(self.receipt.url_documentos.encode()).hexdigest())
        self.aes = AESGCM(AESGCM.generate_key(bit_length=256))

    def encrypt(self, text):
        nonce = os.urandom(12)
        return base64.b64encode(nonce + self.aes.encrypt(nonce, text.encode(), None)).decode()

    def decrypt(self, ciphertext):
        raw = base64.b64decode(ciphertext, validate=True)
        return self.aes.decrypt(raw[:12], raw[12:], None).decode()

    def seal(self, **changes):
        args = dict(original=self.body, evidencia=self.evidence, recibo=self.receipt,
            vinculo=self.binding, procedencia=self.provenance, recuperado_en=self.now, cifrar=self.encrypt)
        return custodiar_descarga(**{**args, **changes})

    def open(self, ciphertext, **changes):
        return leer_descarga_custodiada(ciphertext, vinculo={**self.binding, **changes}, descifrar=self.decrypt)

    def test_encrypted_roundtrip_keeps_exact_pdf_and_never_claims_a_signature(self):
        first, second = self.seal(), self.seal()
        self.assertNotEqual(first, second)
        for private in ('SYNTHETIC-TX', 'PRIVATE', self.binding['solicitudId'], '%PDF'):
            self.assertNotIn(private, first)
        evidence, meta = self.open(first)
        self.assertEqual(evidence.contenido, self.body)
        self.assertEqual(evidence.sha256, self.evidence.sha256)
        self.assertFalse(meta['firmaAcreditada'])
        self.assertFalse(evidence.firma_criptografica_validada)
        self.assertNotIn('pdfBase64', meta)

    def test_foreign_case_send_document_original_transaction_or_environment_is_rejected(self):
        ciphertext = self.seal()
        for key in ('solicitudId', 'envioId', 'documentoOperacionId', 'originalSha256',
                'transaccionReferencia', 'ambiente'):
            value = 'PRODUCCION' if key == 'ambiente' else 'b'*64 if key.endswith(('Sha256', 'Referencia')) else str(uuid4())
            with self.subTest(key=key), self.assertRaisesRegex(AutenticError, 'CUSTODIA_DESCARGA_NO_INTEGRA'):
                self.open(ciphertext, **{key: value})

    def test_ciphertext_tampering_and_wrong_key_are_rejected_without_private_errors(self):
        ciphertext = self.seal()
        raw = bytearray(base64.b64decode(ciphertext)); raw[-1] ^= 1
        for candidate in ('INVALID', base64.b64encode(raw).decode()):
            with self.assertRaisesRegex(AutenticError, '^CUSTODIA_DESCARGA_NO_INTEGRA$'):
                self.open(candidate)
        self.aes = AESGCM(AESGCM.generate_key(bit_length=256))
        with self.assertRaises(AutenticError):self.open(ciphertext)

    def test_plaintext_cipher_or_invalid_binding_and_provenance_cannot_be_custodied(self):
        for change in ({'cifrar': lambda text: text}, {'recibo': None}):
            with self.assertRaisesRegex(AutenticError, 'CUSTODIA_DESCARGA_NO_COMPLETADA'):
                self.seal(**change)
        for key, value in (('http', 401), ('ambiente', 'PRODUCCION'), ('TLSVerificado', False),
                ('sinRedirecciones', False), ('zipSha256', ''), ('urlSha256', 'b'*64)):
            with self.subTest(key=key), self.assertRaises(AutenticError):
                self.seal(procedencia={**self.provenance, key: value})
        with self.assertRaises(AutenticError):self.seal(recibo=replace(self.receipt, estado='INDETERMINADA'))
        with self.assertRaises(AutenticError):self.seal(recuperado_en='2026-11-06T03:00:00')

    def test_authenticated_but_semantically_corrupt_snapshot_is_rejected(self):
        envelope = json.loads(self.decrypt(self.seal()))
        for change in ({'pdfSha256': 'b'*64}, {'pdfBase64': 'INVALID'}, {'firmaAcreditada': True},
                {'version': 'UNKNOWN'}, {'recuperadoEn': '2026-11-06T03:00:00'}, {'procedencia': {}},
                {'procedencia': {**self.provenance, 'ambiente': 'PRODUCCION'}}):
            with self.subTest(change=change), self.assertRaises(AutenticError):
                self.open(self.encrypt(json.dumps({**envelope, **change})))
        duplicated = json.dumps(envelope)[:-1] + ',"firmaAcreditada":false}'
        with self.assertRaises(AutenticError):self.open(self.encrypt(duplicated))

    def test_constructed_pdf_evidence_cannot_self_attest_or_lie_about_its_hash(self):
        for args in (('b'*64, self.body), (self.evidence.sha256, self.body, True)):
            with self.assertRaises(AutenticError):EvidenciaPDF(*args)
        self.assertNotIn('%PDF', repr(self.evidence))
