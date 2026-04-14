from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.test.utils import override_settings
from rest_framework.test import APIClient


@override_settings(CREDIT_USE_MOCK_SERVICES=True)
class SolicitudFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_credit_flow_happy_path(self):
        documents_response = self.client.get("/api/v1/documentos/")
        self.assertEqual(documents_response.status_code, 200)
        document_ids = [item["id"] for item in documents_response.json()]
        self.assertEqual(len(document_ids), 1)

        start_response = self.client.post(
            "/api/v1/solicitudes/",
            {
                "tipo_identificacion": "CC",
                "numero_identificacion": "1020304050",
                "primer_apellido": "Garcia",
                "fecha_expedicion": (date.today() - timedelta(days=3650)).isoformat(),
                "celular": "3001234567",
                "email": "persona@correo.com",
            },
            format="json",
        )
        self.assertEqual(start_response.status_code, 201)
        solicitud_id = start_response.json()["id"]

        with patch("apps.solicitudes.views.dispatch_credit_pipeline", return_value=False):
            authorize_response = self.client.post(
                f"/api/v1/solicitudes/{solicitud_id}/autorizar/",
                {
                    "accepted_documents": [
                        {
                            "document_id": document_id,
                            "viewed_seconds": 12,
                            "reached_end": True,
                        }
                        for document_id in document_ids
                    ]
                },
                format="json",
            )
        self.assertEqual(authorize_response.status_code, 202)

        status_response = self.client.get(f"/api/v1/solicitudes/{solicitud_id}/")
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["estado"], "AUTORIZADA")

    def test_rejects_underage_document_policy(self):
        response = self.client.post(
            "/api/v1/solicitudes/",
            {
                "tipo_identificacion": "TI",
                "numero_identificacion": "123456789",
                "primer_apellido": "Lopez",
                "fecha_expedicion": (date.today() - timedelta(days=365)).isoformat(),
                "celular": "3001234567",
                "email": "persona@correo.com",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
