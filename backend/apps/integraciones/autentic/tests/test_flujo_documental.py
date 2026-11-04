"""Regresiones documentales aisladas: ninguna peticion externa esta permitida."""

import base64
from contextlib import ExitStack, closing
from dataclasses import replace
from io import BytesIO
import json
import logging
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfReader
import requests

from ..cliente import AutenticDEVClient, ReciboFirma
from ..contratos import (AutenticError, ConfiguracionDEV, DEV_URL, TOKEN_URL,
                         preparar_certificacion, preparar_firma)
from ..evidencia import verificar_zip
from ..operaciones import RegistroFirmas
from .test_cliente import Response, Session, pdf, recibo, solicitud, token
from tools.autentic_prueba_offline import ejecutar


class FlujoDocumentalTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('socket.create_connection', 'socket.getaddrinfo', 'socket.socket.connect',
                       'requests.sessions.Session.request'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('RED_REAL_PROHIBIDA')))
        self.config = ConfiguracionDEV('CLIENTE-SINTETICO', 'SECRETO-SINTETICO', permitir_documentos=True)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.journal = RegistroFirmas(self.db)

    def client(self, *responses):
        session = Session(*responses)
        client = AutenticDEVClient(self.config, session=session)
        self.addCleanup(client.close)
        return client, session

    def sign(self, client, request=None, key='firma', journal=None):
        return client.firmar(request or solicitud(), clave=key, registro=journal or self.journal,
            referencia_autorizacion='SOLO-SINTETICO-MOCK')

    def certify(self, client, key='cert', journal=None):
        return client.certificar('TRANSACCION-SINTETICA', 'DOCUMENTO-OFICIAL-SINTETICO',
            clave=key, registro=journal or self.journal, referencia_autorizacion='SOLO-SINTETICO-MOCK')

    def test_request_firma_campos_base64_y_limite_documentos(self):
        request = solicitud()
        self.assertEqual(set(request.payload), {'metadata', 'files'})
        self.assertEqual(base64.b64decode(request.payload['files'][0]['fileContent']), pdf())
        for missing in ('names', 'lastNames', 'docId', 'secureKey'):
            metadata = dict(request.payload['metadata'])
            metadata.pop(missing)
            with self.subTest(missing=missing), self.assertRaisesRegex(AutenticError, 'SOLICITUD_FIRMA_INVALIDA'):
                preparar_firma(metadata, {'prueba.pdf': pdf()})
        with self.assertRaisesRegex(AutenticError, 'SOLICITUD_FIRMA_INVALIDA'):
            preparar_firma(request.payload['metadata'], {f'{i}.pdf': pdf() for i in range(6)})

    def test_certificacion_preparada_sin_inventar_document_number(self):
        for doc in (None, '', {}, 1):
            with self.subTest(doc=doc), self.assertRaisesRegex(AutenticError, 'DOCUMENT_NUMBER_OFICIAL_REQUERIDO'):
                preparar_certificacion('TX-SINTETICA', doc)
        request = preparar_certificacion('TX-SINTETICA', 'NUMERO-PROVEEDOR-SINTETICO')
        self.assertEqual(request.payload, {'certificationType': 'SIGNATURE',
            'transactionId': 'TX-SINTETICA', 'documentNumber': 'NUMERO-PROVEEDOR-SINTETICO'})
        self.assertNotIn('NUMERO-PROVEEDOR', repr(request))

    def test_no_certifica_sin_journal_ni_autorizacion(self):
        client, session = self.client()
        with self.assertRaisesRegex(AutenticError, 'JOURNAL_CERTIFICACION_REQUERIDO'):
            client.certificar('TX', 'DOC', referencia_autorizacion='SOLO-MOCK')
        with self.assertRaisesRegex(AutenticError, 'DOCUMENTOS_DEV_NO_AUTORIZADOS'):
            client.certificar('TX', 'DOC', clave='cert', registro=self.journal, referencia_autorizacion='')
        client.config = replace(client.config, permitir_documentos=False)
        with self.assertRaisesRegex(AutenticError, 'DOCUMENTOS_DEV_NO_AUTORIZADOS'):
            self.certify(client)
        self.assertFalse(session.calls)

    def test_errores_firma_400_401_500_timeout_sin_reenvio_ni_logs(self):
        for response in (Response({'privado': 'NO-MOSTRAR'}, 400), Response({}, 401),
                         Response({}, 500), requests.Timeout('NO-MOSTRAR')):
            with self.subTest(response=type(response).__name__), closing(sqlite3.connect(':memory:')) as db:
                journal = RegistroFirmas(db)
                client, session = self.client(Response(token()), response)
                with self.assertNoLogs(level=logging.WARNING):
                    with self.assertRaisesRegex(AutenticError, '^ENVIO_INDETERMINADO_NO_REPETIR$'):
                        self.sign(client, journal=journal)
                with self.assertRaisesRegex(AutenticError, 'REENVIO_BLOQUEADO'):
                    self.sign(client, journal=journal)
                self.assertEqual(len(session.calls), 2)
                self.assertEqual(journal.preparar('firma', solicitud().fingerprint).estado, 'INDETERMINADA')

    def test_operation_code_y_transaction_id_firma_obligatorios(self):
        for changes in ({'operationCode': True}, {'operationCode': 2000}, {'transactionId': ''},
                        {'processedDate': None}, {'urlDocuments': None}):
            with self.subTest(changes=changes), closing(sqlite3.connect(':memory:')) as db:
                client, session = self.client(Response(token()), Response({**recibo(), **changes}, 201))
                with self.assertRaisesRegex(AutenticError, 'ENVIO_INDETERMINADO_NO_REPETIR'):
                    self.sign(client, journal=RegistroFirmas(db))
                self.assertEqual(len(session.calls), 2)

    def test_misma_firma_otra_clave_bloqueada_antes_de_token(self):
        request = solicitud()
        self.journal.preparar('primera', request.fingerprint)
        client, session = self.client()
        with self.assertRaisesRegex(AutenticError, 'ENVIO_DUPLICADO_OTRA_CLAVE'):
            self.sign(client, request, 'segunda')
        self.assertFalse(session.calls)

    def test_fingerprint_reservado_entre_conexiones_sqlite(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.db'
            with closing(sqlite3.connect(path)) as a, closing(sqlite3.connect(path)) as b:
                ra, rb = RegistroFirmas(a), RegistroFirmas(b)
                ra.preparar('primera', 'a' * 64)
                with self.assertRaisesRegex(AutenticError, 'ENVIO_DUPLICADO_OTRA_CLAVE'):
                    rb.preparar('segunda', 'a' * 64)
                self.assertFalse(b.in_transaction)
                self.assertEqual(b.execute('SELECT COUNT(*) FROM autentic_operaciones').fetchone()[0], 1)

    def test_certificacion_un_post_y_replay_sin_nuevo_token(self):
        client, session = self.client(Response(token()), Response({'operationCode': 1000,
            'certificationContent': base64.b64encode(pdf()).decode()}))
        evidence = self.certify(client)
        replay = self.certify(client)
        self.assertFalse(evidence.firma_criptografica_validada)
        self.assertEqual(replay.estado, 'CERTIFICACION_RECUPERADA_NO_VERIFICADA')
        self.assertEqual(replay.transaccion, 'TRANSACCION-SINTETICA')
        self.assertEqual([call[1] for call in session.calls], [TOKEN_URL, DEV_URL + '/v2/certification/'])
        self.assertNotIn('access_token', json.dumps(list(self.db.execute('SELECT * FROM autentic_operaciones'))))

    def test_certificacion_errores_no_repetibles_y_respuesta_invalida(self):
        responses = [Response({}, status) for status in (400, 401, 500)] + [requests.Timeout(),
            Response({'operationCode': 1010}), Response({'operationCode': 1020}),
            Response({'operationCode': True}), Response({'operationCode': 1000, 'certificationContent': '!!!'}),
            Response({'operationCode': 1000, 'certificationContent': base64.b64encode(b'%PDF-invalid').decode()})]
        for response in responses:
            with self.subTest(response=type(response).__name__), closing(sqlite3.connect(':memory:')) as db:
                journal = RegistroFirmas(db)
                client, session = self.client(Response(token()), response)
                with self.assertRaisesRegex(AutenticError, '^CERTIFICACION_INDETERMINADA_NO_REPETIR$'):
                    self.certify(client, journal=journal)
                with self.assertRaisesRegex(AutenticError, 'REENVIO_BLOQUEADO'):
                    self.certify(client, journal=journal)
                self.assertEqual(len(session.calls), 2)

    def test_certificacion_timeout_reabrir_no_envia_y_cambiar_clave_tampoco(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.db'
            with closing(sqlite3.connect(path)) as first:
                client, _ = self.client(Response(token()), requests.Timeout())
                with self.assertRaisesRegex(AutenticError, 'CERTIFICACION_INDETERMINADA'):
                    self.certify(client, journal=RegistroFirmas(first))
            with closing(sqlite3.connect(path)) as second:
                client, session = self.client()
                journal = RegistroFirmas(second)
                with self.assertRaisesRegex(AutenticError, 'REENVIO_BLOQUEADO'):
                    self.certify(client, journal=journal)
                with self.assertRaisesRegex(AutenticError, 'ENVIO_DUPLICADO_OTRA_CLAVE'):
                    self.certify(client, key='otra', journal=journal)
                self.assertFalse(session.calls)

    def test_renovacion_fallida_descarta_token_y_401_no_reintenta(self):
        now = [0]
        session = Session(Response(token(10)), Response({}, 401))
        client = AutenticDEVClient(self.config, session=session, reloj=lambda: now[0])
        self.addCleanup(client.close)
        client.autenticar()
        now[0] = 10
        with self.assertRaisesRegex(AutenticError, 'AUTENTICACION_RECHAZADA'):
            client.autenticar()
        self.assertIsNone(client._token)
        self.assertEqual(client._vence, 0)
        self.assertEqual(len(session.calls), 2)

    def test_zip_crc_corrupto_y_pdf_no_valido_rechazados(self):
        stream = BytesIO()
        content = pdf()
        with ZipFile(stream, 'w') as archive:
            archive.writestr('prueba.pdf', content)
        damaged = bytearray(stream.getvalue())
        damaged[damaged.index(b'%PDF-') + 1] ^= 1
        with self.assertRaisesRegex(AutenticError, 'ZIP_INVALIDO'):
            verificar_zip(bytes(damaged), ['prueba.pdf'])
        for names in (['prueba.pdf', 'prueba.pdf'], None, [{}]):
            with self.subTest(names=names), self.assertRaisesRegex(AutenticError, 'ZIP_INVALIDO'):
                verificar_zip(stream.getvalue(), names)
        stream = BytesIO()
        with ZipFile(stream, 'w') as archive:
            archive.writestr('prueba.pdf', b'NO ES PDF')
        with self.assertRaisesRegex(AutenticError, 'PDF_INVALIDO'):
            verificar_zip(stream.getvalue(), ['prueba.pdf'])

    def test_zip_symlink_y_limite_descomprimido(self):
        from zipfile import ZipInfo
        entry = ZipInfo('prueba.pdf')
        entry.create_system = 3
        entry.external_attr = 0o120777 << 16
        stream = BytesIO()
        with ZipFile(stream, 'w') as archive:
            archive.writestr(entry, pdf())
        with self.assertRaisesRegex(AutenticError, 'ZIP_INVALIDO'):
            verificar_zip(stream.getvalue(), ['prueba.pdf'])
        with patch('apps.integraciones.autentic.evidencia.MAX_ZIP', 20):
            with self.assertRaisesRegex(AutenticError, 'ZIP_INVALIDO'):
                verificar_zip(stream.getvalue(), ['prueba.pdf'])
        stream = BytesIO()
        with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
            archive.writestr('prueba.pdf', b'%PDF-' + b'x' * 6_000_000)
        with self.assertRaisesRegex(AutenticError, 'ZIP_INVALIDO'):
            verificar_zip(stream.getvalue(), ['prueba.pdf'])

    def test_descarga_http_redirects_timeout_sin_bearer_ni_reintento(self):
        for response in (Response({}, 400), Response({}, 401), Response({}, 500),
                         Response({}, 302), requests.Timeout()):
            client, session = self.client(response)
            client.config = replace(client.config, hosts_descarga=('descarga.example.test',))
            with self.subTest(response=type(response).__name__), self.assertRaises(AutenticError):
                client.recuperar_documentos(ReciboFirma('TX', 'FECHA', recibo()['urlDocuments']),
                    ['prueba.pdf'], referencia_autorizacion='SOLO-MOCK')
            self.assertEqual(len(session.calls), 1)
            self.assertNotIn('Authorization', session.calls[0][2]['headers'])
            self.assertFalse(session.calls[0][2]['allow_redirects'])
            self.assertTrue(session.calls[0][2]['verify'])

    def test_descarga_no_hereda_sesion_con_credenciales(self):
        for property_name, value in (('headers', {'Authorization': 'NO-MOSTRAR'}),
                                     ('cookies', {'privada': 'NO-MOSTRAR'}), ('auth', ('USUARIO', 'SECRETO'))):
            client, session = self.client()
            client.config = replace(client.config, hosts_descarga=('descarga.example.test',))
            setattr(session, property_name, value)
            with self.subTest(property_name=property_name), self.assertRaisesRegex(AutenticError, 'TRANSPORTE_DESCARGA_NO_AISLADO'):
                client.recuperar_documentos(ReciboFirma('TX', 'FECHA', recibo()['urlDocuments']),
                    ['prueba.pdf'], referencia_autorizacion='SOLO-MOCK')
            self.assertFalse(session.calls)

    def test_trazas_http_debug_bloquean_antes_del_token(self):
        client, session = self.client()
        with patch('http.client.HTTPConnection.debuglevel', 1):
            with self.assertRaisesRegex(AutenticError, 'TRANSPORTE_DEBUG_NO_AUTORIZADO'):
                client.autenticar()
        self.assertFalse(session.calls)

    def test_token_400_500_y_red_no_reintentan_ni_muestran_cuerpo(self):
        for response in (Response({'detalle': 'SECRETO-NO-MOSTRAR'}, 400),
                         Response({}, 500), requests.ConnectionError('SECRETO-NO-MOSTRAR')):
            client, session = self.client(response)
            with self.subTest(response=type(response).__name__), self.assertRaises(AutenticError) as caught:
                client.autenticar()
            self.assertNotIn('SECRETO', str(caught.exception))
            self.assertEqual(len(session.calls), 1)
            self.assertIsNone(client._token)

    def test_compatibilidad_journal_preexistente_y_replay_sin_red(self):
        with TemporaryDirectory() as folder, closing(sqlite3.connect(Path(folder) / 'previo.db')) as db:
            db.execute("CREATE TABLE autentic_operaciones (clave TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                       "estado TEXT NOT NULL, transaccion TEXT NOT NULL DEFAULT '')")
            request = solicitud()
            db.execute("INSERT INTO autentic_operaciones VALUES (?, ?, ?, ?)",
                       ('firma', request.fingerprint, 'FIRMA_REPORTADA_NO_VERIFICADA', 'TX-PREVIA'))
            db.commit()
            client, session = self.client()
            state = self.sign(client, request, journal=RegistroFirmas(db))
            self.assertEqual(state.transaccion, 'TX-PREVIA')
            self.assertFalse(session.calls)

    def test_transporte_con_reintentos_configurados_bloqueado(self):
        client = AutenticDEVClient(self.config)
        self.addCleanup(client.close)
        client.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=1))
        with self.assertRaisesRegex(AutenticError, 'TRANSPORTE_NO_AISLADO'):
            client.autenticar()

    def test_recorrido_ejecutable_sintetico_sin_finanzas_ni_red(self):
        with TemporaryDirectory() as folder:
            destination = Path(folder) / 'artefactos'
            result = ejecutar(destination)
            self.assertEqual(result['estado'], 'AUTENTIC_RECORRIDO_OFFLINE_VERIFICADO')
            self.assertEqual(result['proveedorPeticionesReales'], 0)
            self.assertEqual(result['peticionesSimuladas'], 4)
            self.assertFalse(result['firmaReal'])
            self.assertFalse(result['certificacionReal'])
            self.assertFalse(result['verificacionCriptografica'])
            self.assertTrue(result['replaySinEnvios'])
            for name in ('entrada-sintetica.pdf', 'salida-simulada.pdf', 'certificacion-simulada.pdf'):
                reader = PdfReader(destination / name, strict=True)
                self.assertIn('SINTETIC', reader.pages[0].extract_text())
            with self.assertRaises(FileExistsError):
                ejecutar(destination)


if __name__ == '__main__':
    unittest.main()
