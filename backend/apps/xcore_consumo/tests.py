from datetime import date, timedelta
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.documentos.services import get_active_documents
from apps.historial_pago.client import HistorialPagoClientError
from apps.historial_pago.models import HistorialPagoConsulta
from apps.preselecta.client import PreselectaClient
from apps.usuarios.models import PerfilAsesor, RolAsesor
from apps.xcore_consumo.models import (
    ConsentimientoConsumo,
    ConsultaAsociadoIntento,
    EstadoOtp,
    EstadoSolicitudConsumo,
    OtpChallenge,
    SolicitudConsumo,
)
from apps.xcore_consumo.services.otp_crypto import decrypt_text
from apps.xcore_consumo.services.oracle import _map_capacidad_row
from apps.xcore_consumo.services.orchestration import build_consumo_snapshot, normalize_preselecta_business_status, _payload_preselecta


@override_settings(
    XCORE_CONSUMO_ORACLE_ENABLED=False,
    OTP_PROVIDER_MODE="test",
    OTP_AES_KEY_B64="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    OTP_EMAIL_TTL_SECONDS=600,
    OTP_RESEND_COOLDOWN_SECONDS=30,
)
class ConsumoRobustFlowTests(TestCase):
    def setUp(self):
        self._provider_mode_original = os.environ.get("XCORE_PROVIDER_MODE")
        self._provider_case_original = os.environ.get("XCORE_PROVIDER_TEST_CASE")
        os.environ["XCORE_PROVIDER_MODE"] = "real"
        os.environ["XCORE_PROVIDER_TEST_CASE"] = ""
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="asesor.demo",
            password="ClaveSegura123*",
            first_name="Asesor",
            last_name="Demo",
            email="asesor@congente.test",
        )
        PerfilAsesor.objects.create(usuario=self.user, rol=RolAsesor.ASESOR)
        self.client.force_authenticate(self.user)

    def tearDown(self):
        if self._provider_mode_original is None:
            os.environ.pop("XCORE_PROVIDER_MODE", None)
        else:
            os.environ["XCORE_PROVIDER_MODE"] = self._provider_mode_original
        if self._provider_case_original is None:
            os.environ.pop("XCORE_PROVIDER_TEST_CASE", None)
        else:
            os.environ["XCORE_PROVIDER_TEST_CASE"] = self._provider_case_original
        super().tearDown()

    def _base_payload(self, numero_identificacion="1020304050"):
        return {
            "tipo_identificacion": "CC",
            "numero_identificacion": numero_identificacion,
            "primer_apellido": "Garcia",
            "fecha_expedicion": (date.today() - timedelta(days=3650)).isoformat(),
            "celular": "3001234567",
            "email": "persona@correo.com",
        }

    def _consult_payload(self, numero_identificacion="1020304050"):
        return self._base_payload(numero_identificacion=numero_identificacion)

    def _create_solicitud(self, numero_identificacion="1020304050"):
        response = self.client.post(
            "/api/v1/consumo/solicitudes/",
            self._base_payload(numero_identificacion=numero_identificacion),
            format="json",
        )
        self.assertIn(response.status_code, (200, 201))
        return response.json()

    def _latest_challenge(self, solicitud_id):
        return OtpChallenge.objects.filter(solicitud_id=solicitud_id).order_by("-created_at").first()

    def _detail(self, solicitud_id):
        return SolicitudConsumo.objects.get(solicitud_id=solicitud_id)

    def _register_consent(self, solicitud_id):
        accepted_documents = [
            {
                "document_id": document.id,
                "viewed_seconds": 8 + index,
                "reached_end": True,
            }
            for index, document in enumerate(get_active_documents(), start=1)
        ]
        response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/consentimiento/",
            {
                "accepted": True,
                "version": "2026.03",
                "canal": "SMS",
                "text_snapshot": "Acepto tratamiento y consulta de datos para credito digital.",
                "accepted_documents": accepted_documents,
            },
            format="json",
            HTTP_USER_AGENT="pytest-agent",
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(response.status_code, 202)
        return response.json()

    def test_build_snapshot_uses_linix_values_for_preselecta_payload(self):
        payload = _payload_preselecta(
            "1006442329",
            "ORTIZ",
            {"forma_pago": "Nomina", "ocupacion": "Profesional independiente", "antiguedad_asociado": "Entre 2 y 5 anos"},
        )
        self.assertEqual(payload["linea_credito"], "1")
        self.assertEqual(payload["tipo_asociado"], "2")
        self.assertEqual(payload["medio_pago"], "2")
        self.assertEqual(payload["actividad"], "3")

    def test_create_solicitud_starts_on_consent_pending(self):
        payload = self._create_solicitud()
        self.assertEqual(payload["estado"], EstadoSolicitudConsumo.CONSENTIMIENTO_PENDIENTE)
        self.assertEqual(payload["wizard_step"], "consentimiento")
        self.assertEqual(payload["solicitud"]["asesor"]["username"], self.user.username)
        self.assertRegex(payload["solicitud"]["numero_solicitud"], r"^CD\d+$")
        self.assertIn("orchestration", payload)
        self.assertIn("datos_linix", payload["orchestration"])
        self.assertIn("campos_editables", payload["orchestration"])
        self.assertEqual(payload["orchestration"]["datos_preselecta"], {})

    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    @patch("apps.xcore_consumo.services.orchestration.validar_credito_digital")
    def test_preview_consulta_identificacion_returns_consolidated_payload(self, mock_validar, mock_consultar):
        mock_validar.return_value = {"ok": True, "message": "Validacion Exitosa"}
        mock_consultar.return_value = {"nombre": "Maria Ortiz"}

        response = self.client.post(
            "/api/v1/consumo/orquestacion/preview/",
            self._consult_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["duplicate_request"]["has_active"])
        self.assertTrue(payload["validation_credito_digital"]["ok"])
        self.assertTrue(payload["core"]["found"])
        self.assertEqual(payload["form_defaults"]["nombre"], "Maria Ortiz")
        self.assertTrue(payload["can_continue"])
        mock_validar.assert_called_once()
        mock_consultar.assert_called_once()

    @override_settings(
        OKTA_TOKEN_URL="https://token.preselecta.test",
        OKTA_CLIENT_ID="okta-client-demo",
        OKTA_CLIENT_SECRET="secret-demo",
        OKTA_SCOPE="scope-demo",
        SERVICE_URL="https://service.preselecta.test",
        PRESELECTA_INQUIRY_ID="892000373",
        PRESELECTA_INQUIRY_CLIENT_TYPE="2",
        PRESELECTA_INQUIRY_USER_TYPE="2",
    )
    def test_preselecta_payload_uses_operational_inquiry_id(self):
        client = PreselectaClient()
        payload = client._normalize_service_payload(
            {
                "idNumber": "1110501568",
                "idType": "1",
                "firstLastName": "VARGAS",
                "linea_credito": "1",
                "tipo_asociado": "1",
                "medio_pago": "1",
                "actividad": "1",
            }
        )

        self.assertEqual(payload["inquiryClientId"], "892000373")
        self.assertEqual(payload["inquiryUserId"], "892000373")
        self.assertNotEqual(payload["inquiryClientId"], client.client_id)
        self.assertNotEqual(payload["inquiryUserId"], client.client_id)

    def test_preselecta_business_status_uses_engine_response(self):
        normalized = normalize_preselecta_business_status(
            {
                "estado": "SUCCESS",
                "mensaje": "Respuesta recibida",
                "engine_response": [
                    {"key": "DECISION", "value": "APROBADO"},
                    {"key": "RIESGO_SCORE", "value": "VERDE"},
                ],
                "raw": {
                    "engineResponse": [
                        {"key": "DECISION", "value": "APROBADO"},
                        {"key": "RIESGO_SCORE", "value": "VERDE"},
                    ]
                },
            }
        )

        self.assertEqual(normalized["decision"], "APROBADO")
        self.assertEqual(normalized["estado_negocio"], "APROBADO")
        self.assertTrue(normalized["puede_continuar"])

    def test_map_capacidad_row_reads_valactivos_from_current_trace(self):
        row = (
            'Nivel 1 y 2', 'T?cnico o Tecnol?gico', 'Soltero', 'Masculino', 'Familiar', 'N?mina',
            'Prestaci?n de servicios Formal', '0', 'Menor de 31', 'Entre 2 y 5 a?os',
            '($1,500,000 - $3,000,000)', '($12,800,000 - $24,800,000)', 'Menos de $150,000,000',
            10000000, '0', '26,3', 'Empleado', 'Urbano', 'ORTIZ ANGEL CARLOS DANIEL'
        )

        mapped = _map_capacidad_row(row)

        self.assertEqual(mapped['activos'], 'Menos de $150,000,000')
        self.assertEqual(mapped['valor_activos'], 10000000)
        self.assertEqual(mapped['pasivos'], '0')
        self.assertEqual(mapped['saldo_creditos'], '26,3')
        self.assertEqual(mapped['ocupacion'], 'Empleado')
        self.assertEqual(mapped['zona'], 'Urbano')
        self.assertEqual(mapped['nombre'], 'ORTIZ ANGEL CARLOS DANIEL')

    def test_preselecta_fault_is_error_not_business_rejection(self):
        normalized = normalize_preselecta_business_status(
            {
                "estado": "ERROR",
                "mensaje": "nitUsuario no valido o no especificado",
                "fault": {"faultcode": "500", "faultstring": "nitUsuario no valido o no especificado"},
                "raw": {"Fault": {"faultcode": "500", "faultstring": "nitUsuario no valido o no especificado"}},
            }
        )

        self.assertEqual(normalized["estado_negocio"], "ERROR")
        self.assertFalse(normalized["puede_continuar"])
        self.assertEqual(normalized["mensaje_tecnico"], "nitUsuario no valido o no especificado")

    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    @patch("apps.xcore_consumo.services.orchestration.validar_credito_digital")
    def test_preview_normalizes_colombia_mobile_input(self, mock_validar, mock_consultar):
        mock_validar.return_value = {"ok": True, "message": "Validacion Exitosa"}
        mock_consultar.return_value = {"nombre": "ASOCIADO DE PRUEBA"}

        response = self.client.post(
            "/api/v1/consumo/orquestacion/preview/",
            {**self._consult_payload(numero_identificacion="1006442329"), "celular": "+57 300-123-4567"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        mock_validar.assert_called_once()
        self.assertEqual(mock_validar.call_args.kwargs["celular"], "3001234567")

    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    @patch("apps.xcore_consumo.services.orchestration.validar_credito_digital")
    def test_duplicate_active_request_blocks_before_oracle_calls(self, mock_validar, mock_consultar):
        self._create_solicitud(numero_identificacion="1006442329")

        response = self.client.post(
            "/api/v1/consumo/orquestacion/preview/",
            self._consult_payload(numero_identificacion="1006442329"),
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["duplicate_request"]["has_active"])
        self.assertFalse(payload["can_continue"])
        self.assertIn("solicitud activa", payload["duplicate_request"]["message"].lower())
        mock_validar.assert_not_called()
        mock_consultar.assert_not_called()

    def test_recent_preselecta_rejection_blocks_for_thirty_days(self):
        payload = self._create_solicitud(numero_identificacion="86048885")
        solicitud_id = payload["solicitud"]["id"]
        detail = self._detail(solicitud_id)
        detail.estado = EstadoSolicitudConsumo.FINALIZADO
        detail.orchestration_data = {"datos_preselecta": {"estado_negocio": "RECHAZADO"}}
        detail.save(update_fields=["estado", "orchestration_data", "updated_at"])
        SolicitudConsumo.objects.filter(pk=detail.pk).update(updated_at=timezone.now())

        response = self.client.post(
            "/api/v1/consumo/orquestacion/preview/",
            self._consult_payload(numero_identificacion="86048885"),
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["can_continue"])
        self.assertTrue(payload["validation_credito_digital"]["blocked"])
        self.assertEqual(payload["centrales_restriction"]["reason"], "PRESELECTA_RECHAZADO")
        self.assertIn("1 mes", payload["centrales_restriction"]["message"].lower())

    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    @patch("apps.xcore_consumo.services.orchestration.validar_credito_digital")
    def test_failed_attempts_block_after_third_lookup(self, mock_validar, mock_consultar):
        mock_validar.return_value = {"ok": False, "message": "Rechazado por politica"}

        last_payload = None
        for _ in range(4):
            response = self.client.post(
                "/api/v1/consumo/orquestacion/preview/",
                self._consult_payload(numero_identificacion="1099988877"),
                format="json",
            )
            self.assertEqual(response.status_code, 200)
            last_payload = response.json()

        assert last_payload is not None
        self.assertTrue(last_payload["validation_credito_digital"]["blocked"])
        self.assertEqual(last_payload["validation_credito_digital"]["failed_attempts"], 3)
        self.assertEqual(last_payload["validation_credito_digital"]["remaining_attempts"], 0)
        self.assertEqual(mock_validar.call_count, 3)
        mock_consultar.assert_not_called()
        self.assertEqual(
            ConsultaAsociadoIntento.objects.filter(numero_identificacion="1099988877").count(),
            4,
        )

    def test_register_consent_before_otp_moves_to_otp_step(self):
        payload = self._create_solicitud()
        solicitud_id = payload["solicitud"]["id"]

        consent_response = self._register_consent(solicitud_id)
        self.assertEqual(consent_response["estado"], EstadoSolicitudConsumo.OTP_PENDIENTE)
        self.assertEqual(consent_response["wizard_step"], "otp")

        consent = ConsentimientoConsumo.objects.get(solicitud_id=solicitud_id)
        self.assertTrue(consent.aceptado)
        self.assertFalse(consent.firmado)
        self.assertEqual(consent.tipo_firma, "ACEPTACION_PENDIENTE_OTP")

    def test_cannot_send_otp_without_consent(self):
        payload = self._create_solicitud()
        solicitud_id = payload["solicitud"]["id"]

        response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("aceptar", str(response.json()).lower())

    @patch("apps.xcore_consumo.services.otp.persist_orchestration_snapshot", side_effect=lambda detail: detail)
    def test_consent_then_otp_verify_finalizes_consent(self, _mock_snapshot):
        payload = self._create_solicitud(numero_identificacion="1020304058")
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)

        send_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )
        self.assertEqual(send_response.status_code, 202)
        otp_payload = send_response.json()["otp"]
        self.assertEqual(otp_payload["estado"], EstadoOtp.ENVIADA)
        challenge = self._latest_challenge(solicitud_id)
        self.assertTrue(challenge.codigo)

        verify_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": challenge.codigo},
            format="json",
        )
        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(
            verify_response.json()["estado"],
            EstadoSolicitudConsumo.CONSENTIMIENTO_FIRMADO,
        )
        self.assertEqual(verify_response.json()["wizard_step"], "analisis")

        consent = ConsentimientoConsumo.objects.get(solicitud_id=solicitud_id)
        self.assertEqual(consent.version, "2026.03")
        self.assertEqual(consent.canal, "SMS")
        self.assertEqual(consent.user_agent, "pytest-agent")
        self.assertEqual(str(consent.ip_address), "127.0.0.1")
        self.assertTrue(consent.text_hash)
        self.assertTrue(consent.firmado)
        self.assertEqual(consent.tipo_firma, "ACEPTACION_OTP")

    @patch("apps.xcore_consumo.services.otp.persist_orchestration_snapshot", side_effect=lambda detail: detail)
    def test_create_returns_existing_resumable_request_for_same_advisor(self, _mock_snapshot):
        payload = self._create_solicitud(numero_identificacion="1002003004")
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)

        send_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )
        challenge = self._latest_challenge(solicitud_id)
        verify_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": challenge.codigo},
            format="json",
        )
        self.assertEqual(verify_response.status_code, 200)

        second_create = self.client.post(
            "/api/v1/consumo/solicitudes/",
            self._base_payload(numero_identificacion="1002003004"),
            format="json",
        )

        self.assertEqual(second_create.status_code, 200)
        self.assertEqual(second_create.json()["solicitud"]["id"], solicitud_id)
        self.assertEqual(second_create.json()["wizard_step"], "analisis")

    @patch("apps.xcore_consumo.services.orchestration.HistorialPagoSOAPClient")
    @patch("apps.xcore_consumo.services.orchestration.PreselectaClient.evaluate")
    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    def test_otp_verify_persists_historial_xml(self, mock_consultar_capa, mock_preselecta, mock_historial_client):
        payload = self._create_solicitud(numero_identificacion="1020304059")
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)

        mock_consultar_capa.return_value = {"nombre": "VARGAS PRUEBA"}
        mock_preselecta.return_value = {
            "estado": "SUCCESS",
            "mensaje": "APROBADO",
            "engine_response": [{"key": "DECISION", "value": "APROBADO"}],
            "decision": "APROBADO",
            "score": 780,
        }
        mock_historial_client.return_value.consult.return_value = {
            "estado": "OK",
            "score_pago": 735,
            "mora_maxima": 0,
            "categoria": "A",
            "resumen": "HC2 OK",
            "xml": """
            <Informes><Informe>
              <CuentaCartera entidad="CONGENTE" numero="0001" sector="1">
                <Caracteristicas tipoCuenta="CAB" />
                <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
                <Valores><Valor saldoActual="2500" cuota="120" valorInicial="4000" /></Valores>
              </CuentaCartera>
              <InfoAgregadaMicrocredito><Resumen><EndeudamientoActual><Sector><TipoCuenta><Cuenta saldoActual="4.2" /></TipoCuenta></Sector></EndeudamientoActual></Resumen></InfoAgregadaMicrocredito>
            </Informe></Informes>
            """,
            "soap_request_xml": "<soap>req</soap>",
            "raw": "<soap>response</soap>",
        }

        self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )
        challenge = self._latest_challenge(solicitud_id)
        verify_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": challenge.codigo},
            format="json",
        )

        self.assertEqual(verify_response.status_code, 200)
        consulta = HistorialPagoConsulta.objects.get(solicitud_id=solicitud_id)
        self.assertTrue(consulta.xml_payload)
        self.assertIn("<Informes>", consulta.xml_payload)
        self.assertEqual(consulta.soap_request_xml, "<soap>req</soap>")
        self.assertEqual(consulta.response_payload.get("source"), "live")
        self.assertIn("valor_pasivos", verify_response.json()["orchestration"]["historial_pago"]["metrics"])

    @patch("apps.xcore_consumo.services.orchestration.HistorialPagoSOAPClient.consult")
    @patch("apps.xcore_consumo.services.orchestration.PreselectaClient.evaluate")
    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    def test_snapshot_reuses_persisted_historial_xml(self, mock_consultar_capa, mock_preselecta, mock_historial):
        payload = self._create_solicitud(numero_identificacion="1020304060")
        solicitud_id = payload["solicitud"]["id"]
        solicitud = self._detail(solicitud_id).solicitud
        mock_consultar_capa.return_value = {"nombre": "VARGAS PRUEBA"}
        mock_preselecta.return_value = {
            "estado": "SUCCESS",
            "mensaje": "APROBADO",
            "engine_response": [{"key": "DECISION", "value": "APROBADO"}],
            "decision": "APROBADO",
            "score": 780,
        }
        HistorialPagoConsulta.objects.create(
            solicitud=solicitud,
            estado="OK",
            request_payload={"numero_identificacion": "1020304060", "primer_apellido": "GARCIA"},
            response_payload={"source": "live"},
            xml_payload="""
            <Informes><Informe>
              <CuentaCartera entidad="CONGENTE" numero="0001" sector="1">
                <Caracteristicas tipoCuenta="CAB" />
                <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
                <Valores><Valor saldoActual="2500" cuota="120" valorInicial="4000" /></Valores>
              </CuentaCartera>
              <InfoAgregadaMicrocredito><Resumen><EndeudamientoActual><Sector><TipoCuenta><Cuenta saldoActual="4.2" /></TipoCuenta></Sector></EndeudamientoActual></Resumen></InfoAgregadaMicrocredito>
            </Informe></Informes>
            """,
            score_pago=710,
            mora_maxima=0,
            categoria="A",
            resumen="HC2 persistido",
        )

        snapshot = build_consumo_snapshot(solicitud=solicitud)

        self.assertEqual(snapshot["historial_pago"]["source"], "stored_xml")
        self.assertIn("valor_pasivos", snapshot["historial_pago"]["metrics"])
        mock_historial.assert_not_called()

    @patch("apps.xcore_consumo.services.orchestration.HistorialPagoSOAPClient.consult")
    @patch("apps.xcore_consumo.services.orchestration.PreselectaClient.evaluate")
    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    def test_snapshot_skips_stored_historial_when_identity_differs(self, mock_consultar_capa, mock_preselecta, mock_historial):
        payload = self._create_solicitud(numero_identificacion="1020304061")
        solicitud_id = payload["solicitud"]["id"]
        solicitud = self._detail(solicitud_id).solicitud
        mock_consultar_capa.return_value = {"nombre": "GOMEZ PRUEBA"}
        mock_preselecta.return_value = {
            "estado": "SUCCESS",
            "mensaje": "APROBADO",
            "engine_response": [{"key": "DECISION", "value": "APROBADO"}],
            "decision": "APROBADO",
            "score": 780,
        }
        HistorialPagoConsulta.objects.create(
            solicitud=solicitud,
            estado="OK",
            request_payload={"numero_identificacion": "1110501568", "primer_apellido": "VARGAS"},
            response_payload={"source": "live"},
            xml_payload="""
            <Informes><Informe identificacionDigitada="1110501568" apellidoDigitado="VARGAS">
              <CuentaCartera entidad="CONGENTE" numero="0001" sector="1">
                <Caracteristicas tipoCuenta="CAB" />
                <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
                <Valores><Valor saldoActual="2500" cuota="120" valorInicial="4000" /></Valores>
              </CuentaCartera>
            </Informe></Informes>
            """,
            score_pago=710,
            mora_maxima=0,
            categoria="A",
            resumen="HC2 persistido",
        )
        mock_historial.return_value = {
            "estado": "OK",
            "score_pago": 700,
            "mora_maxima": 0,
            "categoria": "A",
            "resumen": "HC2 live",
            "xml": """
            <Informes><Informe identificacionDigitada="1090438586" apellidoDigitado="GOMEZ">
              <CuentaCartera entidad="CONGENTE" numero="0002" sector="1">
                <Caracteristicas tipoCuenta="CAB" />
                <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
                <Valores><Valor saldoActual="1800" cuota="90" valorInicial="2200" /></Valores>
              </CuentaCartera>
              <InfoAgregadaMicrocredito><Resumen><EndeudamientoActual><Sector><TipoCuenta><Cuenta saldoActual="4.6" /></TipoCuenta></Sector></EndeudamientoActual></Resumen></InfoAgregadaMicrocredito>
            </Informe></Informes>
            """,
            "soap_request_xml": "<soap>req</soap>",
            "raw": "<soap>response</soap>",
        }

        snapshot = build_consumo_snapshot(solicitud=solicitud)

        self.assertEqual(snapshot["historial_pago"]["estado"], "OK")
        self.assertEqual(snapshot["historial_pago"].get("source"), "live")
        self.assertEqual(snapshot["integration_errors"], {})
        self.assertEqual(
            snapshot["datos_preselecta"]["identidad_efectiva"]["numero_identificacion"],
            "1020304061",
        )
        self.assertEqual(
            snapshot["datos_preselecta"]["identidad_efectiva"]["primer_apellido"],
            "GARCIA",
        )

    @patch("apps.xcore_consumo.services.orchestration.HistorialPagoSOAPClient.consult")
    @patch("apps.xcore_consumo.services.orchestration.PreselectaClient.evaluate")
    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    def test_otp_verify_moves_to_result_when_preselecta_rejects(self, mock_consultar_capa, mock_preselecta, mock_historial):
        payload = self._create_solicitud(numero_identificacion="1090438586")
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)

        mock_consultar_capa.return_value = {"nombre": "GOMEZ PRUEBA"}
        mock_preselecta.return_value = {
            "estado": "RECHAZADO",
            "mensaje": "Solicitud rechazada por politica inicial",
            "preaprobado": False,
            "score": 620,
        }

        self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )
        challenge = self._latest_challenge(solicitud_id)
        verify_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": challenge.codigo},
            format="json",
        )

        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(verify_response.json()["wizard_step"], "resultado")
        self.assertFalse(verify_response.json()["orchestration"]["datos_preselecta"]["puede_continuar"])
        self.assertEqual(
            verify_response.json()["orchestration"]["datos_preselecta"]["estado_negocio"],
            "RECHAZADO",
        )
        mock_historial.assert_not_called()

    def test_expired_otp_fails_verification(self):
        payload = self._create_solicitud()
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)
        self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "EMAIL"},
            format="json",
        )
        challenge = self._latest_challenge(solicitud_id)
        challenge.expira_at = timezone.now() - timedelta(seconds=1)
        challenge.save(update_fields=("expira_at", "updated_at"))

        verify_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": challenge.codigo},
            format="json",
        )

        self.assertEqual(verify_response.status_code, 400)
        challenge.refresh_from_db()
        self.assertEqual(challenge.estado, EstadoOtp.EXPIRADA)

    @patch("apps.xcore_consumo.services.orchestration.HistorialPagoSOAPClient.consult")
    @patch("apps.xcore_consumo.services.orchestration.PreselectaClient.evaluate")
    @patch("apps.xcore_consumo.services.orchestration.consultar_capa")
    def test_preselecta_rechazo_no_consulta_historial(self, mock_consultar_capa, mock_preselecta, mock_historial):
        payload = self._create_solicitud(numero_identificacion="1110501568")
        solicitud_id = payload["solicitud"]["id"]
        detail = self._detail(solicitud_id)

        mock_consultar_capa.return_value = {"nombre": "VARGAS PRUEBA"}
        mock_preselecta.return_value = {
            "estado": "NEGADO",
            "mensaje": "Solicitud negada por politicas internas",
            "preaprobado": False,
            "score": 610,
        }

        snapshot = build_consumo_snapshot(solicitud=detail.solicitud)

        self.assertEqual(snapshot["datos_preselecta"]["estado_negocio"], "RECHAZADO")
        self.assertFalse(snapshot["datos_preselecta"]["puede_continuar"])
        self.assertEqual(snapshot["historial_pago"], {})
        self.assertEqual(snapshot["datos_datacredito"], {})
        self.assertEqual(snapshot["campos_editables"], [])
        self.assertEqual(snapshot["campos_faltantes"], [])
        mock_historial.assert_not_called()

    def test_process_and_external_refresh_are_blocked_without_signed_consent(self):
        payload = self._create_solicitud()
        solicitud_id = payload["solicitud"]["id"]

        core_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/core/consultar/",
            {},
            format="json",
        )
        self.assertEqual(core_response.status_code, 400)

        process_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/procesar/",
            {},
            format="json",
        )
        self.assertEqual(process_response.status_code, 400)

    @patch("apps.xcore_consumo.services.otp.persist_orchestration_snapshot", side_effect=lambda detail: detail)
    @patch("apps.xcore_consumo.services.pipeline.HistorialPagoSOAPClient")
    @patch("apps.xcore_consumo.services.pipeline.PreselectaClient.evaluate")
    def test_process_returns_503_when_historial_cert_config_is_invalid(
        self,
        mock_preselecta,
        mock_historial_client,
        _mock_snapshot,
    ):
        payload = self._create_solicitud(numero_identificacion="1090438587")
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)
        self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )
        challenge = self._latest_challenge(solicitud_id)
        verify_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": challenge.codigo},
            format="json",
        )
        self.assertEqual(verify_response.status_code, 200)

        detail = self._detail(solicitud_id)
        detail.oracle_consultado = True
        detail.core_data = {"nombre": "GOMEZ PRUEBA"}
        detail.form_data = {"tipo_cliente": "ANTIGUO", "forma_pago": "NOMINA"}
        detail.save(update_fields=("oracle_consultado", "core_data", "form_data", "updated_at"))
        mock_preselecta.return_value = {
            "decision": "APROBADO",
            "risk_level": "VERDE",
            "mensaje": "Aprobado",
            "score": 780,
        }
        mock_historial_client.side_effect = HistorialPagoClientError(
            "Archivo no encontrado: /app/erts/client_key.pem"
        )

        response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/procesar/",
            {"selected_hc2_keys": []},
            format="json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("/app/erts/client_key.pem", response.json()["detail"])
        consulta = HistorialPagoConsulta.objects.get(solicitud_id=solicitud_id)
        self.assertEqual(consulta.estado, "ERROR")
        self.assertIn("/app/erts/client_key.pem", consulta.resumen)
        detail.refresh_from_db()
        self.assertEqual(detail.estado, EstadoSolicitudConsumo.FORMULARIO_XCORE_OK)
        self.assertIn("/app/erts/client_key.pem", detail.ultimo_error)

    def test_otp_wrong_attempts_can_block_flow(self):
        payload = self._create_solicitud()
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)
        self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )

        for _ in range(3):
            response = self.client.post(
                f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
                {"codigo": "000000"},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        challenge = self._latest_challenge(solicitud_id)
        self.assertEqual(challenge.estado, EstadoOtp.BLOQUEADA)

    @override_settings(
        OTP_PROVIDER_MODE="real",
        OTP_AES_KEY_B64="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        TWILIO_ACCOUNT_SID="AC123",
        TWILIO_AUTH_TOKEN="token",
        TWILIO_VERIFY_SID="VA123",
    )
    @patch("apps.xcore_consumo.services.otp.TwilioVerifyClient")
    def test_sms_real_uses_twilio_verify_without_persisting_local_code(
        self,
        mock_twilio_client,
    ):
        mock_twilio_client.return_value.start_verification.return_value = SimpleNamespace(sid="VE123", status="pending")
        mock_twilio_client.return_value.check_verification.return_value = SimpleNamespace(sid="VC123", status="approved")

        payload = self._create_solicitud(numero_identificacion="1002003010")
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)

        send_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )
        self.assertEqual(send_response.status_code, 202)
        challenge = self._latest_challenge(solicitud_id)
        self.assertEqual(challenge.provider, "twilio_verify")
        self.assertEqual(challenge.verification_sid, "VE123")
        self.assertEqual(challenge.otp_hash, "")
        self.assertEqual(challenge.otp_code_encrypted, "")
        self.assertTrue(challenge.destination_full_encrypted)
        mock_twilio_client.return_value.start_verification.assert_called_once()
        args, kwargs = mock_twilio_client.return_value.start_verification.call_args
        self.assertEqual(args[0], "+573001234567")
        self.assertEqual(kwargs["channel"], "sms")

        verify_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": "123456"},
            format="json",
        )
        self.assertEqual(verify_response.status_code, 200)
        challenge.refresh_from_db()
        self.assertEqual(challenge.estado, EstadoOtp.VALIDADA)
        self.assertEqual(challenge.verification_check_sid, "VC123")
        consentimiento = ConsentimientoConsumo.objects.get(solicitud_id=solicitud_id)
        self.assertTrue(consentimiento.pdf_consentimiento)
        self.assertEqual(consentimiento.evidencia.get("provider"), "twilio_verify")
        self.assertTrue(consentimiento.evidencia.get("transaction_uuid"))

    @override_settings(
        OTP_PROVIDER_MODE="real",
        OTP_AES_KEY_B64="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        EMAIL_HOST="smtp.test.local",
        EMAIL_PORT=587,
        EMAIL_HOST_USER="no-reply@congente.test",
        EMAIL_HOST_PASSWORD="smtp-secret",
        EMAIL_USE_TLS=True,
        DEFAULT_FROM_EMAIL="no-reply@congente.test",
        OTP_EMAIL_CONSENT_URL="https://consent.congente.test",
    )
    @patch("apps.xcore_consumo.services.otp.EmailMultiAlternatives.send", return_value=1)
    def test_email_real_uses_internal_otp_hash_and_saves_pdf_support(self, mock_send):
        payload = self._create_solicitud(numero_identificacion="1002003011")
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)

        send_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "EMAIL"},
            format="json",
        )
        self.assertEqual(send_response.status_code, 202)
        challenge = self._latest_challenge(solicitud_id)
        self.assertEqual(challenge.provider, "internal_email")
        self.assertTrue(challenge.otp_hash)
        self.assertTrue(challenge.otp_code_encrypted)
        self.assertTrue(challenge.destination_full_encrypted)
        self.assertEqual(challenge.codigo, "")
        plain_code = decrypt_text(challenge.otp_code_encrypted)

        verify_response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": plain_code},
            format="json",
        )
        self.assertEqual(verify_response.status_code, 200)
        challenge.refresh_from_db()
        self.assertEqual(challenge.estado, EstadoOtp.VALIDADA)
        consentimiento = ConsentimientoConsumo.objects.get(solicitud_id=solicitud_id)
        self.assertTrue(consentimiento.pdf_consentimiento)
        self.assertEqual(consentimiento.evidencia.get("provider"), "internal_email")
        self.assertTrue(consentimiento.evidencia.get("transaction_uuid"))
        self.assertEqual(mock_send.call_count, 1)

    def test_preview_rejects_primer_apellido_with_digits(self):
        response = self.client.post(
            "/api/v1/consumo/orquestacion/preview/",
            {**self._consult_payload(), "primer_apellido": "ORTIZ1"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("solo permite letras y espacios", str(response.json()).lower())

    @patch("apps.xcore_consumo.services.pipeline.consultar_familiar")
    @patch("apps.xcore_consumo.services.pipeline._consultar_historial")
    @patch("apps.xcore_consumo.services.pipeline.PreselectaClient.evaluate")
    @patch("apps.xcore_consumo.services.pipeline.evaluar_xcore_consumo")
    def test_process_blocks_when_comision_garantia_returns_error(
        self,
        mock_evaluar,
        mock_preselecta,
        mock_consultar_historial,
        mock_consultar_familiar,
    ):
        mock_preselecta.return_value = {
            "decision": "APROBADO",
            "risk_level": "Categoria D",
            "mensaje": "Aprobado",
        }
        payload = self._create_solicitud(numero_identificacion="1090438586")
        solicitud_id = payload["solicitud"]["id"]
        self._register_consent(solicitud_id)
        self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/send/",
            {"canal": "SMS"},
            format="json",
        )
        challenge = self._latest_challenge(solicitud_id)
        self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/otp/verify/",
            {"codigo": challenge.codigo},
            format="json",
        )

        detail = self._latest_challenge(solicitud_id).solicitud.consumo_detail
        detail.oracle_consultado = True
        detail.core_data = {"nombre": "GOMEZ"}
        detail.form_data = {
            "tipo_garantia": "FNG EMP319",
            "tipo_cliente": "ANTIGUO",
            "forma_pago": "NOMINA",
            "monto_solicitado": 15000000,
            "plazo": 36,
            "tipo_credito": "Libre inversion",
        }
        detail.save(update_fields=("oracle_consultado", "core_data", "form_data", "updated_at"))

        mock_consultar_historial.return_value = (
            {"numero_identificacion": "1090438586"},
            {"source": "stored_xml"},
            {"obligaciones_abiertas": [], "metrics": {}},
        )
        mock_consultar_familiar.return_value = {"resultado": "NO", "tipofamiliar": ""}
        mock_evaluar.return_value = {"error": "FNG EMP319 permite monto entre 1 y 6 SMMLV."}

        response = self.client.post(
            f"/api/v1/consumo/solicitudes/{solicitud_id}/procesar/",
            {"selected_hc2_keys": []},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "FNG EMP319 permite monto entre 1 y 6 SMMLV.")
        self.assertFalse(hasattr(detail.solicitud, "evaluacion_consumo"))


class OracleCapacidadMappingTests(TestCase):
    def test_map_capacidad_row_supports_crc_contract_without_pasivos_columns(self):
        row = [
            "3",
            "PROFESIONAL",
            "SOLTERO",
            "F",
            "PROPIA",
            "NOMINA",
            "INDEFINIDO",
            1,
            34,
            12,
            4200000,
            18000,
            65000000,
            3200000,
            "IGNORED",
            "ANALISTA",
            "URBANA",
            "GOMEZ PRUEBA",
        ]

        mapped = _map_capacidad_row(row)

        self.assertEqual(mapped["estrato"], "3")
        self.assertEqual(mapped["saldo_creditos"], 3200000)
        self.assertEqual(mapped["ocupacion"], "ANALISTA")
        self.assertEqual(mapped["zona"], "URBANA")
        self.assertEqual(mapped["nombre"], "GOMEZ PRUEBA")
        self.assertEqual(mapped["pasivos"], "")
        self.assertEqual(mapped["valor_pasivos"], 0)
