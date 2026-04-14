from django.test import TestCase

from apps.documentos.models import DocumentoLegal, TipoDocumento
from apps.documentos.services import get_active_documents


class DocumentoLegalServiceTests(TestCase):
    def test_get_active_documents_keeps_only_centrales(self):
        DocumentoLegal.objects.create(
            codigo="tratamiento-datos",
            tipo_documento=TipoDocumento.DATOS,
            titulo="Tratamiento",
            descripcion="Documento antiguo",
            orden=4,
            activo=True,
        )
        DocumentoLegal.objects.create(
            codigo="terminos-condiciones",
            tipo_documento=TipoDocumento.TERMINOS,
            titulo="Terminos",
            descripcion="Documento antiguo",
            orden=5,
            activo=True,
        )

        documents = list(get_active_documents())

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].codigo, "centrales-riesgo")
        self.assertFalse(DocumentoLegal.objects.get(codigo="tratamiento-datos").activo)
        self.assertFalse(DocumentoLegal.objects.get(codigo="terminos-condiciones").activo)
