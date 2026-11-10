"""Download aliases never replace ZIP, PDF or host validation."""
from io import BytesIO
import unittest
from zipfile import ZipFile

from ..cliente import AutenticDEVClient,ReciboFirma
from ..contratos import ConfiguracionSTAGING,AutenticError
from .test_cliente import Response,Session,pdf


class BinaryDownloadTests(unittest.TestCase):
    def download(self,body,mime):
        session=Session(Response(body,mime=mime))
        client=AutenticDEVClient(ConfiguracionSTAGING('SYNTHETIC','SECRET',permitir_documentos=True,
            hosts_descarga=('descarga.example.test',)),session=session)
        self.addCleanup(client.close)
        receipt=ReciboFirma('SYNTHETIC','2026-11-09 12:00:00','https://descarga.example.test/test.zip')
        return client,session,lambda:client.recuperar_documentos(receipt,['prueba.pdf'],referencia_autorizacion='SYNTHETIC')

    def test_binary_zip_aliases_are_accepted_only_with_valid_archive_and_pdf(self):
        stream=BytesIO()
        with ZipFile(stream,'w') as z:z.writestr('prueba.pdf',pdf())
        for mime in ('application/zip','application/x-zip-compressed','application/octet-stream','binary/octet-stream'):
            with self.subTest(mime=mime):
                client,session,fetch=self.download(stream.getvalue(),mime)
                self.assertEqual(fetch()['prueba.pdf'].contenido,pdf())
                self.assertEqual(client.auditoria_descarga['tipoContenido'],mime)
                self.assertEqual([call[0] for call in session.calls],['GET'])
                self.assertTrue(session.calls[0][2]['verify'])
                self.assertFalse(session.calls[0][2]['allow_redirects'])

    def test_html_json_corruption_and_arbitrary_entry_remain_rejected(self):
        samples=[(b'<html>not a document</html>','application/octet-stream'),
            (b'PK\x03\x04broken','application/octet-stream'),(pdf(),'application/octet-stream'),
            (b'{}','application/json')]
        stream=BytesIO()
        with ZipFile(stream,'w') as z:z.writestr('../prueba.pdf',pdf())
        samples.append((stream.getvalue(),'application/octet-stream'))
        for raw,mime in samples:
            with self.subTest(mime=mime,bytes=len(raw)):
                _,session,fetch=self.download(raw,mime)
                with self.assertRaises(AutenticError):fetch()
                self.assertEqual(len(session.calls),1)
