"""Consultas S2S exclusivamente sinteticas: sin proveedores ni persistencia."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from io import StringIO
import json
import logging
from pathlib import Path
import traceback
from unittest.mock import Mock, patch
from uuid import UUID

from django.test import SimpleTestCase
import requests

from ..clientes import HDCGatewayClient, PreselectaGatewayClient
from ..configuracion import RiskGatewayConfig
from ..consultas import HDCRequest, PreselectaRequest
from ..errores import (
    GatewayContentTypeError, GatewayCorrelationError, GatewayDisabled,
    GatewayEvidenceConflict, GatewayEvidenceNotFound, GatewayForbidden,
    GatewayIdempotencyConflict, GatewayInvalidResponse, GatewayJSONError,
    GatewayNetworkError, GatewayPDFError, GatewayProviderError, GatewayRateLimited,
    GatewayRedirectError, GatewaySchemaError, GatewayTimeout, GatewayUnauthorized,
    GatewayValidationError,
)
from ..evidencia import EvidenciaPDFHDC
from ..transporte import GatewayTransport, HDC_PATH, PRESELECTA_PATH
from .fixtures_consultas import (
    CID, RID, hdc, hdc_agregado, preselecta, request_hdc, request_preselecta,
)


SECRET = "S" * 40
CONFIG = RiskGatewayConfig(enabled=True, base_url="https://gateway.invalid",
    internal_client="health-test", internal_secret=SECRET)
OTHER = "00000000-0000-4000-8000-000000000009"
PDF = b"%PDF-1.4\n% Evidencia exclusivamente sintetica\n%%EOF\n"


class OfflineCase(SimpleTestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target in ("requests.sessions.Session.send", "socket.socket", "socket.getaddrinfo",
                       "socket.create_connection", "http.client.HTTPConnection.connect", "oracledb.connect"):
            stack.enter_context(patch(target, side_effect=AssertionError("RED PROHIBIDA")))
        for target in ("apps.preselecta.client.PreselectaClient",
                       "apps.historial_pago.client.HistorialPagoSOAPClient"):
            stack.enter_context(patch(target, side_effect=AssertionError("PROVEEDOR PROHIBIDO")))
        self.post = stack.enter_context(patch("requests.Session.post"))
        self.get = stack.enter_context(patch("requests.Session.get"))
        self.transport = GatewayTransport(CONFIG)

    def response(self, *, data=None, raw=None, status=200, headers=None, pdf=False):
        response = requests.Response()
        response.status_code = status
        response._content = (PDF if pdf else json.dumps(data).encode()) if raw is None else raw
        response._content_consumed = True
        response.headers.update({"Content-Type": "application/pdf" if pdf else "application/json",
                                 "X-Request-ID": RID})
        if headers:
            response.headers.update(headers)
        response.close = Mock()
        self.post.return_value = self.get.return_value = response
        return response


class OperationTests:
    def request(self):
        return self.request_type.desde_payload(self.request_fixture())

    def call(self, **kwargs):
        return getattr(self.client_type(self.transport), self.method)(self.request(), request_id=RID, **kwargs)

    def response_dto(self):
        return self.response(data=self.response_fixture())

    def test_request_response_validos(self):
        response = self.response_dto()
        result = self.call()
        self.post.assert_called_once_with(CONFIG.base_url + self.path,
            json=self.request_fixture(), headers={"Authorization": "Bearer " + SECRET,
                "X-Internal-Client": "health-test", "X-Request-ID": RID, "Accept": "application/json",
                "Content-Type": "application/json"}, timeout=(5, 30), verify=True, allow_redirects=False)
        self.assertEqual(result.a_dict(), self.response_fixture())
        response.close.assert_called_once_with()
        self.get.assert_not_called()

    def test_uuid_generado(self):
        def answer(url, **kwargs):
            rid = kwargs["headers"]["X-Request-ID"]
            UUID(rid)
            data = self.response_fixture()
            data["requestId"] = data["resultado"]["request_id"] = rid
            return self.response(data=data, headers={"X-Request-ID": rid})
        self.post.side_effect = answer
        result = getattr(self.client_type(self.transport), self.method)(self.request())
        self.assertEqual(result["requestId"], self.post.call_args.kwargs["headers"]["X-Request-ID"])

    def test_uuid_invalido_no_se_reemplaza(self):
        with self.assertRaises(GatewayValidationError):
            getattr(self.client_type(self.transport), self.method)(self.request(), request_id="invalid")
        self.post.assert_not_called()

    def test_correlacion_body_y_header(self):
        for header in ("", OTHER):
            with self.subTest(header=header):
                self.response(data=self.response_fixture(), headers={"X-Request-ID": header})
                with self.assertRaises(GatewayCorrelationError):
                    self.call()
        data = self.response_fixture()
        data["requestId"] = data["resultado"]["request_id"] = OTHER
        self.response(data=data)
        with self.assertRaises(GatewayCorrelationError):
            self.call()

    def test_request_id_anidado_inconsistente(self):
        data = self.response_fixture()
        data["resultado"]["request_id"] = OTHER
        self.response(data=data)
        with self.assertRaises(GatewaySchemaError):
            self.call()

    def test_campos_extra_envelope_rechazados(self):
        data = self.response_fixture()
        data["futureField"] = "synthetic"
        self.response(data=data)
        with self.assertRaises(GatewaySchemaError):
            self.call()

    def test_version_incompatible(self):
        data = self.response_fixture()
        data["versionContrato"] = "UNSUPPORTED"
        self.response(data=data)
        with self.assertRaises(GatewaySchemaError):
            self.call()

    def test_trazabilidad_incompatible(self):
        data = self.response_fixture()
        data["resultado"]["trazabilidad"]["version_contrato"] = "UNSUPPORTED"
        self.response(data=data)
        with self.assertRaises(GatewaySchemaError):
            self.call()

    def test_schema_invalido(self):
        for field in ("consulta_id", "estado_tecnico", "trazabilidad"):
            with self.subTest(field=field):
                data = self.response_fixture()
                del data["resultado"][field]
                self.response(data=data)
                with self.assertRaises(GatewaySchemaError):
                    self.call()

    def test_http_200_no_puede_contener_estado_error(self):
        data = self.response_fixture()
        data["resultado"]["estado_tecnico"] = "ERROR_TECNICO"
        self.response(data=data)
        with self.assertRaises(GatewaySchemaError):
            self.call()

    def test_sin_informacion_preserva_evidencia_sin_defaults(self):
        data = self.response_fixture()
        data["resultado"]["estado_tecnico"] = "SIN_INFORMACION"
        self.response(data=data)
        self.assertEqual(self.call().a_dict(), data)

    def test_ca_bundle_y_timeouts_configurados(self):
        ca = str(Path(__file__).resolve())
        self.transport = GatewayTransport(replace(CONFIG, ca_bundle=ca,
            connect_timeout=2, read_timeout=7))
        self.response_dto()
        self.call()
        self.assertEqual(self.post.call_args.kwargs["verify"], ca)
        self.assertEqual(self.post.call_args.kwargs["timeout"], (2, 7))

    def test_json_no_objeto(self):
        for value in (None, [], False, "synthetic"):
            with self.subTest(value=value):
                self.response(data=value)
                with self.assertRaises(GatewaySchemaError):
                    self.call()

    def test_json_invalido_duplicado_no_finito(self):
        for raw in (b"{", b"NaN", b'{"requestId":"x","requestId":"y"}'):
            with self.subTest(raw=raw):
                self.response(raw=raw)
                with self.assertRaises(GatewayJSONError):
                    self.call()

    def test_content_type(self):
        self.response(data=self.response_fixture(), headers={"Content-Type": "text/html"})
        with self.assertRaises(GatewayContentTypeError):
            self.call()
        self.response(data=self.response_fixture(), headers={"Content-Type": "application/json; charset=utf-8"})
        self.call()

    def test_request_invalido_antes_de_http(self):
        for value in (None, {}, "synthetic", self.response_fixture()):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(GatewayValidationError):
                    getattr(self.client_type(self.transport), self.method)(value, request_id=RID)
        self.post.assert_not_called()
        data = self.request_fixture()
        data["unknown"] = True
        with self.assertRaises(GatewayValidationError):
            self.request_type.desde_payload(data)

    def test_metadata_consumer_inconsistente(self):
        data = self.request_fixture()
        data["metadata"] = {"consumidor": "another-synthetic-client"}
        with self.assertRaises(GatewayValidationError):
            getattr(self.client_type(self.transport), self.method)(
                self.request_type.desde_payload(data), request_id=RID)
        self.post.assert_not_called()

    def test_disabled_sin_session(self):
        with patch("requests.Session") as session:
            with self.assertRaises(GatewayDisabled):
                getattr(self.client_type(GatewayTransport(RiskGatewayConfig())), self.method)(self.request())
        session.assert_not_called()

    def test_key_opcional_no_generada(self):
        self.response_dto()
        self.call()
        self.assertNotIn("Idempotency-Key", self.post.call_args.kwargs["headers"])

    def test_key_explicita_y_echo(self):
        key = "synthetic-key:001"
        self.response(data=self.response_fixture(), headers={"Idempotency-Key": key,
            "X-Idempotency-Status": "IDEMPOTENCIA_DE_NEGOCIO_PENDIENTE"})
        self.call(idempotency_key=key)
        self.assertEqual(self.post.call_args.kwargs["headers"]["Idempotency-Key"], key)
        self.assertEqual(self.post.call_args.kwargs["headers"]["X-Request-ID"], RID)
        self.assertNotEqual(key, RID)

    def test_key_invalida(self):
        for key in ("", "bad key", "/path", "A" * 129, False, 1):
            with self.subTest(kind=type(key).__name__):
                with self.assertRaises(GatewayValidationError):
                    self.call(idempotency_key=key)
        self.post.assert_not_called()

    def test_key_128_caracteres(self):
        key = "A" * 128
        self.response(data=self.response_fixture(), headers={"Idempotency-Key": key,
            "X-Idempotency-Status": "IDEMPOTENCIA_DE_NEGOCIO_PENDIENTE"})
        self.call(idempotency_key=key)

    def test_key_echo_inconsistente(self):
        for headers in ({}, {"Idempotency-Key": "another"},
                        {"Idempotency-Key": "synthetic-key", "X-Idempotency-Status": "REPLAY"}):
            with self.subTest(headers=headers):
                self.response(data=self.response_fixture(), headers=headers)
                with self.assertRaises(GatewayCorrelationError):
                    self.call(idempotency_key="synthetic-key")

    def test_http_errors_no_body_no_retry(self):
        cases = {400: GatewayValidationError, 401: GatewayUnauthorized, 403: GatewayForbidden,
                 405: GatewayValidationError, 406: GatewayValidationError, 409: GatewayIdempotencyConflict,
                 415: GatewayValidationError, 422: GatewayValidationError, 429: GatewayRateLimited,
                 500: GatewayProviderError, 502: GatewayProviderError, 503: GatewayProviderError,
                 504: GatewayTimeout, 507: GatewayProviderError}
        for status, error in cases.items():
            with self.subTest(status=status):
                self.post.reset_mock()
                response = self.response(status=status, raw=SECRET.encode())
                response.json = Mock(side_effect=AssertionError("BODY PROHIBIDO"))
                with self.assertRaises(error) as raised:
                    self.call()
                self.assertEqual(raised.exception.status, status)
                self.assertEqual(raised.exception.request_id, RID)
                self.assertNotIn(SECRET, repr(raised.exception))
                self.post.assert_called_once()
                response.json.assert_not_called()
                response.close.assert_called_once()

    def test_redirect_y_status_inesperado(self):
        for status, error in ((302, GatewayRedirectError), (307, GatewayRedirectError),
                              (201, GatewayInvalidResponse), (404, GatewayInvalidResponse)):
            with self.subTest(status=status):
                self.response(status=status, raw=b"uncontrolled")
                with self.assertRaises(error):
                    self.call()
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])

    def test_timeout_conexion_lectura(self):
        for exception in (requests.ConnectTimeout, requests.ReadTimeout):
            with self.subTest(exception=exception):
                self.post.reset_mock()
                self.post.side_effect = exception(SECRET)
                with self.assertRaises(GatewayTimeout):
                    self.call()
                self.post.assert_called_once()

    def test_dns_conexion_tls(self):
        for exception in (requests.ConnectionError, requests.exceptions.SSLError):
            with self.subTest(exception=exception):
                self.post.side_effect = exception(SECRET)
                with self.assertRaises(GatewayNetworkError) as raised:
                    self.call()
                self.assertNotIn(SECRET, str(raised.exception))

    def test_no_secretos_payload_en_repr_logs_traceback(self):
        data = self.request_fixture()
        request = self.request_type.desde_payload(data)
        forbidden = data.get("person_id_number", data.get("identidad", {}).get("numero_documento"))
        self.assertNotIn(forbidden, repr(request))
        self.response_dto()
        result = self.call()
        self.assertNotIn(forbidden, repr(result))
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self.post.side_effect = requests.ConnectionError(SECRET + forbidden)
        try:
            self.call()
        except GatewayNetworkError as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertNotIn(SECRET, rendered)
            self.assertNotIn(forbidden, rendered)
        else:
            self.fail("La conexion fallida debe producir un error seguro")
        self.assertNotIn(SECRET, stream.getvalue())
        self.assertNotIn(forbidden, stream.getvalue())

    def test_dto_inmutable_y_wire_independiente(self):
        self.response_dto()
        result = self.call()
        with self.assertRaises(FrozenInstanceError):
            result.campos = ()
        copy = result.a_dict()
        copy["resultado"]["consulta_id"] = OTHER
        self.assertEqual(result["resultado"]["consulta_id"], CID)

    def test_session_sin_proxy_netrc_retry(self):
        self.response_dto()
        original = requests.Session
        sessions = []
        def factory():
            session = original()
            sessions.append(session)
            return session
        with patch("requests.Session", side_effect=factory):
            self.call()
        self.assertFalse(sessions[0].trust_env)
        self.assertEqual(sessions[0].get_adapter(CONFIG.base_url).max_retries.total, 0)


class PreselectaTests(OperationTests, OfflineCase):
    client_type = PreselectaGatewayClient
    request_type = PreselectaRequest
    request_fixture = staticmethod(request_preselecta)
    response_fixture = staticmethod(preselecta)
    method = "consultar_preselecta"
    path = PRESELECTA_PATH

    def test_rechazo_es_hecho_no_error_http(self):
        self.response(data=preselecta(decision="RECHAZADO"))
        result = self.call()
        self.assertEqual(result["resultado"]["resultado_proveedor"]["decision"]["valor_original"], "RECHAZADO")

    def test_decimal_score_y_originales(self):
        self.response_dto()
        result = self.call()["resultado"]["resultado_proveedor"]
        self.assertEqual(result["score"]["valor_homologado"], Decimal("701.000000000000000001"))
        self.assertEqual(result["datos_documentados"]["booleano"], True)

    def test_extra_documentado_json_value_preservado(self):
        data = preselecta()
        data["resultado"]["resultado_proveedor"]["datos_documentados"]["nuevo"] = ["original", None]
        self.response(data=data)
        self.assertEqual(self.call().a_dict(), data)

    def test_request_catalogos_y_tipos(self):
        for value in ("UNSUPPORTED", None, 1):
            data = request_preselecta()
            data["estrategia_id"] = value
            with self.subTest(value=value), self.assertRaises(GatewayValidationError):
                PreselectaRequest.desde_payload(data)
        data = request_preselecta()
        data["parametros"]["ACTIVIDAD"] = "4"
        with self.assertRaises(GatewayValidationError):
            PreselectaRequest.desde_payload(data)


class HDCTests(OperationTests, OfflineCase):
    client_type = HDCGatewayClient
    request_type = HDCRequest
    request_fixture = staticmethod(request_hdc)
    response_fixture = staticmethod(hdc_agregado)
    method = "consultar_hdc"
    path = HDC_PATH

    def test_originales_decimal_sin_factor_1000(self):
        self.response_dto()
        r = self.call()["resultado"]
        detail = r["obligaciones"][0]["valores"][0]["cuota"]
        self.assertEqual(detail["valor_original"], "562000.0")
        self.assertEqual(detail["valor_monetario"], Decimal("562000.0"))
        self.assertEqual(r["endeudamiento_actual"]["cuota_mensual_pesos"], Decimal("1936000"))
        self.assertEqual(r["obligaciones"][0]["valores"][0]["saldoActual"]["valor_monetario"], Decimal("0"))
        self.assertIsNone(r["obligaciones"][0]["valores"][0]["saldoMora"])

    def test_sin_agregado_compatible_no_default(self):
        self.response(data=hdc())
        result = self.call()["resultado"]
        self.assertIsNone(result.get("endeudamiento_actual"))
        self.assertIs(result["obligaciones"][0]["valor_actual"]["disponible"], False)

    def test_natural_default_y_juridica_explicita(self):
        natural = request_hdc()
        del natural["persona"]
        self.assertEqual(HDCRequest.desde_payload(natural).a_dict(), natural)
        juridica = {"persona": "juridica", "person_id_type": "2",
                    "person_id_number": "SINTETICA-NIT", "razon_social": "EMPRESA SINTETICA"}
        self.assertEqual(HDCRequest.desde_payload(juridica).a_dict(), juridica)

    def test_nombres_excluyentes(self):
        for changes in ({"razon_social": "SINTETICA"}, {"persona": "juridica"},
                        {"person_id_type": 1}, {"persona": "unknown"}):
            data = request_hdc()
            data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(GatewayValidationError):
                HDCRequest.desde_payload(data)

    def test_decimal_invalido_no_numero_float(self):
        for value in ("NaN", "Infinity", "not-a-decimal", 1.25, False):
            data = hdc_agregado()
            data["resultado"]["obligaciones"][0]["valores"][0]["cuota"]["valor_monetario"] = value
            self.response(data=data)
            with self.subTest(kind=type(value).__name__), self.assertRaises(GatewaySchemaError):
                self.call()

    def test_pares_originales_y_null_preservados(self):
        data = hdc_agregado()
        state = data["resultado"]["obligaciones"][0]["estado_obligacion_compuesto"]
        state["entradas_originales"] = [["EstadoPago", None]]
        self.response(data=data)
        self.assertEqual(self.call().a_dict(), data)
        state["entradas_originales"] = [[None, "01"]]
        self.response(data=data)
        with self.assertRaises(GatewaySchemaError):
            self.call()


class PDFTests(OfflineCase):
    def call(self, cid=CID):
        return HDCGatewayClient(self.transport).obtener_pdf_hdc(cid, request_id=RID)

    def test_pdf_valido_efimero(self):
        response = self.response(pdf=True)
        result = self.call()
        self.assertIsInstance(result, EvidenciaPDFHDC)
        self.assertEqual(result.contenido, PDF)
        self.assertEqual(result.consulta_id, CID)
        self.assertNotIn("Evidencia exclusivamente", repr(result))
        self.get.assert_called_once_with(CONFIG.base_url + "/api/internal/v1/hdc/consultas/" + CID + "/pdf/",
            headers={"Authorization": "Bearer " + SECRET, "X-Internal-Client": "health-test",
                     "X-Request-ID": RID, "Accept": "application/pdf"},
            timeout=(5, 30), verify=True, allow_redirects=False, stream=True)
        self.post.assert_not_called()
        response.close.assert_called_once()

    def test_consulta_id_invalido(self):
        for cid in (None, "../path", "invalid", 123, UUID(CID)):
            with self.subTest(kind=type(cid).__name__), self.assertRaises(GatewayValidationError):
                self.call(cid)
        self.get.assert_not_called()

    def test_http_401_403_404_409_429_5xx(self):
        for status, error in ((401, GatewayUnauthorized), (403, GatewayForbidden),
                              (404, GatewayEvidenceNotFound), (409, GatewayEvidenceConflict),
                              (429, GatewayRateLimited), (500, GatewayProviderError),
                              (502, GatewayProviderError), (503, GatewayProviderError), (504, GatewayTimeout)):
            response = self.response(status=status, raw=SECRET.encode())
            response.json = Mock(side_effect=AssertionError("BODY PROHIBIDO"))
            with self.subTest(status=status), self.assertRaises(error):
                self.call()
            response.close.assert_called_once()
            response.json.assert_not_called()

    def test_content_type_incorrecto(self):
        self.response(pdf=True, headers={"Content-Type": "application/json"})
        with self.assertRaises(GatewayContentTypeError):
            self.call()

    def test_pdf_vacio_o_sin_firma(self):
        for raw in (b"", b"<html>synthetic</html>"):
            self.response(raw=raw, pdf=True)
            with self.subTest(raw=raw), self.assertRaises(GatewayPDFError):
                self.call()

    def test_limite_bytes_en_stream(self):
        self.response(pdf=True)
        with patch("apps.integraciones.risk_gateway.transporte.MAX_PDF_BYTES", 10):
            with self.assertRaises(GatewayPDFError):
                self.call()

    def test_content_length_invalido(self):
        for value in ("0", "-1", "invalid", "99999999999"):
            self.response(pdf=True, headers={"Content-Length": value})
            with self.subTest(value=value), self.assertRaises(GatewayPDFError):
                self.call()

    def test_correlacion_header(self):
        self.response(pdf=True, headers={"X-Request-ID": OTHER})
        with self.assertRaises(GatewayCorrelationError):
            self.call()

    def test_correlacion_header_ausente(self):
        response = self.response(pdf=True)
        del response.headers["X-Request-ID"]
        with self.assertRaises(GatewayCorrelationError):
            self.call()
        response.close.assert_called_once()

    def test_timeout_y_conexion(self):
        for remote, error in ((requests.ReadTimeout, GatewayTimeout),
                              (requests.ConnectionError, GatewayNetworkError),
                              (requests.exceptions.SSLError, GatewayNetworkError)):
            self.get.side_effect = remote(SECRET)
            with self.subTest(remote=remote), self.assertRaises(error):
                self.call()

    def test_timeout_durante_stream_y_cierre(self):
        response = self.response(pdf=True)
        response.iter_content = Mock(side_effect=requests.ReadTimeout(SECRET))
        with self.assertRaises(GatewayTimeout):
            self.call()
        response.close.assert_called_once()

    def test_redirect_rechazado(self):
        self.response(status=302, pdf=True)
        with self.assertRaises(GatewayRedirectError):
            self.call()

    def test_disabled(self):
        with patch("requests.Session") as session:
            with self.assertRaises(GatewayDisabled):
                HDCGatewayClient(GatewayTransport(RiskGatewayConfig())).obtener_pdf_hdc(CID)
        session.assert_not_called()


class ArchitectureTests(SimpleTestCase):
    def test_paquete_sin_proveedores_pipeline_db_celery_ni_persistencia(self):
        root = Path(__file__).parents[1]
        forbidden = ("apps.preselecta", "apps.historial_pago", "apps.xcore_consumo",
                     "celery", "oracledb", "django.db")
        for source in root.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imports = [node.module or ""]
                for name in imports:
                    self.assertFalse(name.startswith(forbidden), (source.name, name))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, {"save", "write", "write_bytes", "write_text",
                                                     "delay", "apply_async"})

    def test_snapshot_versionado_y_refs_locales(self):
        snapshot = json.loads((Path(__file__).parents[1] / "contrato_openapi.json").read_text())
        self.assertEqual(snapshot["source_commit"], "78fff71328c95e2dc64d3fc9756db5adc34f522f")
        schemas = snapshot["schemas"]
        def walk(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    self.assertIn(value["$ref"].rsplit("/", 1)[1], schemas)
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(schemas)
