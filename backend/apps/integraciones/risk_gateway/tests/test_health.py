"""Contrato sintetico Health. Ninguna prueba permite conexiones reales."""

import ast
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
from io import StringIO
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import traceback
from unittest.mock import Mock, patch
from uuid import UUID

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings
import requests

from ..clientes import RiskGatewayHealthClient
from ..configuracion import RiskGatewayConfig
from ..contratos import HealthResponse
from ..errores import (
    GatewayConfigurationError, GatewayDisabled, GatewayForbidden,
    GatewayInvalidResponse, GatewayNetworkError, GatewayProviderError,
    GatewayRateLimited, GatewayTimeout, GatewayUnauthorized, GatewayValidationError,
)
from ..transporte import GatewayTransport, HEALTH_PATH


RID = "00000000-0000-4000-8000-000000000001"
OTHER_RID = "00000000-0000-4000-8000-000000000002"
SECRET = "S" * 40
CONFIG = RiskGatewayConfig(enabled=True, base_url="https://gateway.invalid",
    internal_client="health-test", internal_secret=SECRET)


def payload(**overrides):
    return dict(apiVersion="v1", status="ready", providerChecks=False,
                requestId=RID, **overrides)


class HealthTests(SimpleTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ("requests.sessions.Session.send", "socket.socket",
                       "socket.getaddrinfo", "socket.create_connection",
                       "http.client.HTTPConnection.connect", "oracledb.connect"):
            self.stack.enter_context(patch(target,
                side_effect=AssertionError("RED PROHIBIDA: test offline")))
        self.post = self.stack.enter_context(patch("requests.Session.post",
            side_effect=AssertionError("POST PROHIBIDO")))
        self.get = self.stack.enter_context(patch("requests.Session.get"))
        self.config = CONFIG

    def tearDown(self):
        self.post.assert_not_called()

    def response(self, data=None, *, status=200, raw=None, headers=None):
        response = requests.Response()
        response.status_code = status
        response._content = (json.dumps(payload() if data is None else data)
                             if raw is None else raw).encode("utf-8")
        response._content_consumed = True
        response.headers["Content-Type"] = "application/json"
        if headers:
            response.headers.update(headers)
        response.close = Mock()
        self.get.return_value = response
        return response

    def check(self):
        return RiskGatewayHealthClient(GatewayTransport(self.config)).check(request_id=RID)

    def invalid(self, data):
        response = self.response(data)
        with self.assertRaises(GatewayInvalidResponse):
            self.check()
        response.close.assert_called_once_with()

    def test_health_200(self):
        response = self.response(headers={"X-Request-ID": RID})
        result = self.check()
        self.assertEqual(result.a_dict(), dict(httpStatus=200, **payload()))
        self.assertIs(result.provider_checks, False)
        response.close.assert_called_once_with()

    def test_api_version_incorrecta(self):
        for value in ("v2", None, 1):
            with self.subTest(value=value):
                data = payload()
                data["apiVersion"] = value
                self.invalid(data)

    def test_status_incorrecto(self):
        for value in ("down", None, True):
            with self.subTest(value=value):
                data = payload()
                data["status"] = value
                self.invalid(data)

    def test_provider_checks_faltante(self):
        data = payload()
        del data["providerChecks"]
        self.invalid(data)

    def test_provider_checks_tipo_incorrecto(self):
        for value in (0, 1, None, "false", [], {}):
            with self.subTest(value=value):
                data = payload()
                data["providerChecks"] = value
                self.invalid(data)

    def test_provider_checks_true_no_es_este_contrato(self):
        data = payload()
        data["providerChecks"] = True
        self.invalid(data)

    def test_request_id_faltante(self):
        data = payload()
        del data["requestId"]
        self.invalid(data)

    def test_request_id_distinto_o_invalido(self):
        for value in (OTHER_RID, "invalid", None, 1):
            with self.subTest(value=value):
                data = payload()
                data["requestId"] = value
                self.invalid(data)

    def test_header_request_id_distinto(self):
        for value in (OTHER_RID, "", "invalid"):
            with self.subTest(value=value):
                self.response(headers={"X-Request-ID": value})
                with self.assertRaises(GatewayInvalidResponse):
                    self.check()

    def test_header_request_id_opcional(self):
        self.response()
        self.assertEqual(self.check().request_id, RID)

    def test_campos_adicionales_no_se_conservan(self):
        self.response(payload(futureField={"uncontrolled": "synthetic-extra"}))
        result = self.check()
        self.assertNotIn("futureField", result.a_dict())
        self.assertNotIn("synthetic-extra", repr(result))

    def test_orden_y_content_length_no_son_contrato(self):
        data = dict(reversed(list(payload().items())))
        self.response(data, headers={"Content-Length": "99999"})
        self.assertEqual(self.check().status, "ready")

    def test_content_type_json_compatible(self):
        for value in ("application/json; charset=utf-8", "Application/JSON",
                      "application/health+json"):
            with self.subTest(value=value):
                self.response(headers={"Content-Type": value})
                self.assertEqual(self.check().status, "ready")

    def test_content_type_invalido(self):
        for value in ("", "text/html", "text/json"):
            with self.subTest(value=value):
                self.response(headers={"Content-Type": value})
                with self.assertRaises(GatewayInvalidResponse):
                    self.check()

    def assert_http_error(self, status, error):
        response = self.response(status=status, raw=SECRET + " arbitrary remote body")
        response.json = Mock(side_effect=AssertionError("NO LEER BODY DE ERROR"))
        with self.assertRaises(error) as raised:
            self.check()
        self.assertEqual(raised.exception.status, status)
        self.assertEqual(raised.exception.request_id, RID)
        self.assertNotIn(SECRET, str(raised.exception))
        response.json.assert_not_called()
        response.close.assert_called_once_with()

    def test_http_401(self):
        self.assert_http_error(401, GatewayUnauthorized)

    def test_http_403(self):
        self.assert_http_error(403, GatewayForbidden)

    def test_http_429(self):
        self.assert_http_error(429, GatewayRateLimited)

    def test_http_500(self):
        self.assert_http_error(500, GatewayProviderError)

    def test_http_502(self):
        self.assert_http_error(502, GatewayProviderError)

    def test_http_503(self):
        self.assert_http_error(503, GatewayProviderError)

    def test_http_504(self):
        self.assert_http_error(504, GatewayTimeout)

    def test_redirect_no_se_sigue(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                self.assert_http_error(status, GatewayInvalidResponse)
        self.assertFalse(self.get.call_args.kwargs["allow_redirects"])

    def test_status_http_inesperado(self):
        for status in (201, 204, 400, 404, 418):
            with self.subTest(status=status):
                self.assert_http_error(status, GatewayInvalidResponse)

    def assert_network_error(self, remote, expected):
        self.get.side_effect = remote(SECRET + " untrusted transport detail")
        with self.assertRaises(expected) as raised:
            self.check()
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn(SECRET, repr(raised.exception))
        self.assertEqual(raised.exception.request_id, RID)
        self.assertEqual(self.get.call_count, 1)

    def test_timeout_conexion(self):
        self.assert_network_error(requests.ConnectTimeout, GatewayTimeout)

    def test_timeout_lectura(self):
        self.assert_network_error(requests.ReadTimeout, GatewayTimeout)

    def test_connection_error_dns(self):
        self.assert_network_error(requests.ConnectionError, GatewayNetworkError)

    def test_tls_error(self):
        self.assert_network_error(requests.exceptions.SSLError, GatewayNetworkError)

    def test_request_exception(self):
        self.assert_network_error(requests.RequestException, GatewayNetworkError)

    def test_json_invalido(self):
        for raw in ("{", "<html>invalid</html>", "NaN", '{"apiVersion":"v1","apiVersion":"v2"}'):
            with self.subTest(raw=raw):
                self.response(raw=raw)
                with self.assertRaises(GatewayInvalidResponse):
                    self.check()

    def test_body_no_objeto(self):
        for data in ([], "text", 0, True):
            with self.subTest(data=data):
                self.invalid(data)
        self.response(raw="null")
        with self.assertRaises(GatewayInvalidResponse):
            self.check()

    def test_disabled_no_crea_session(self):
        with patch("requests.Session") as session:
            with self.assertRaises(GatewayDisabled):
                GatewayTransport(RiskGatewayConfig()).health()
        session.assert_not_called()

    def test_config_invalida_no_crea_session(self):
        invalid = (dict(base_url="http://gateway.invalid"), dict(base_url="https://gateway.invalid/path"),
            dict(base_url="https://user:pass@gateway.invalid"), dict(base_url="https://gateway.invalid?q=1"),
            dict(base_url="https://gateway.invalid#fragment"), dict(base_url="https://gateway.invalid:bad"),
            dict(internal_secret=""), dict(internal_secret="Bearer " + SECRET),
            dict(internal_client="invalid client"), dict(connect_timeout=0),
            dict(read_timeout=float("nan")), dict(connect_timeout=True),
            dict(verify_ssl=False), dict(verify_ssl="Y"), dict(ca_bundle="missing-synthetic.pem"))
        for values in invalid:
            with self.subTest(fields=list(values)), patch("requests.Session") as session:
                with self.assertRaises(GatewayConfigurationError):
                    GatewayTransport(replace(CONFIG, **values)).health()
                session.assert_not_called()

    def test_settings_defaults_y_validacion_diferida(self):
        config = RiskGatewayConfig.from_settings(SimpleNamespace(RISK_GATEWAY_ENABLED="N",
            RISK_GATEWAY_READ_TIMEOUT="invalid"))
        self.assertFalse(config.enabled)
        enabled = RiskGatewayConfig.from_settings(SimpleNamespace(RISK_GATEWAY_ENABLED="Y"))
        self.assertEqual((enabled.connect_timeout, enabled.read_timeout), (5, 30))
        with self.assertRaises(GatewayConfigurationError):
            enabled.validar()
        self.get.assert_not_called()

    def test_settings_invalidas(self):
        for fields in (dict(RISK_GATEWAY_ENABLED="bad"), dict(RISK_GATEWAY_ENABLED="Y",
            RISK_GATEWAY_VERIFY_SSL="bad"), dict(RISK_GATEWAY_ENABLED="Y",
            RISK_GATEWAY_CONNECT_TIMEOUT="infinity")):
            with self.subTest(fields=list(fields)):
                with self.assertRaises(GatewayConfigurationError):
                    RiskGatewayConfig.from_settings(SimpleNamespace(**fields))

    def test_tls_produccion_y_ca_bundle(self):
        with self.assertRaises(GatewayConfigurationError):
            replace(CONFIG, verify_ssl=False, debug=False).validar()
        self.assertFalse(replace(CONFIG, verify_ssl=False, debug=True).validar().verify)
        with TemporaryDirectory() as directory:
            ca = str(Path(directory) / "synthetic-ca.pem")
            Path(ca).touch()
            self.assertEqual(replace(CONFIG, ca_bundle=ca).validar().verify, ca)

    def test_secreto_no_aparece_repr(self):
        self.assertNotIn(SECRET, repr(CONFIG))
        self.assertNotIn(SECRET, repr(replace(CONFIG, base_url=SECRET)))

    def test_secreto_no_aparece_logs(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self.response()
        self.check()
        self.get.side_effect = requests.ConnectionError(SECRET)
        with self.assertRaises(GatewayNetworkError):
            self.check()
        self.assertNotIn(SECRET, stream.getvalue())

    def test_secreto_no_aparece_excepciones_traceback(self):
        self.get.side_effect = requests.ConnectionError(SECRET)
        try:
            self.check()
        except GatewayNetworkError as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertNotIn(SECRET, rendered)
            self.assertNotIn(SECRET, repr(error.__dict__))
        else:
            self.fail("Expected safe network error")

    @override_settings(RISK_GATEWAY_ENABLED="Y", RISK_GATEWAY_BASE_URL=CONFIG.base_url,
        RISK_GATEWAY_INTERNAL_CLIENT=CONFIG.internal_client, RISK_GATEWAY_INTERNAL_SECRET=SECRET,
        RISK_GATEWAY_VERIFY_SSL="Y", RISK_GATEWAY_CA_BUNDLE="",
        RISK_GATEWAY_CONNECT_TIMEOUT=5, RISK_GATEWAY_READ_TIMEOUT=30)
    def test_management_command_exitoso_sin_db(self):
        def answer(url, **kwargs):
            data = payload()
            data["requestId"] = kwargs["headers"]["X-Request-ID"]
            return self.response(data)
        self.get.side_effect = answer
        output = StringIO()
        with patch("django.db.backends.base.base.BaseDatabaseWrapper.cursor",
                   side_effect=AssertionError("DB PROHIBIDA")):
            call_command("check_risk_gateway", stdout=output)
        data = json.loads(output.getvalue())
        self.assertEqual(set(data), {"httpStatus", "apiVersion", "status", "providerChecks", "requestId"})
        self.assertEqual(data["status"], "ready")
        UUID(data["requestId"])
        self.assertNotIn(SECRET, output.getvalue())
        self.assertEqual(self.get.call_count, 1)

    @override_settings(RISK_GATEWAY_ENABLED="N")
    def test_management_command_error_seguro(self):
        output = StringIO()
        with self.assertRaisesMessage(CommandError, "GATEWAY_DISABLED"):
            call_command("check_risk_gateway", stdout=output)
        self.assertEqual(output.getvalue(), "")
        self.get.assert_not_called()

    def test_un_get_sin_retries_proxies_ni_netrc(self):
        self.response()
        original = requests.Session
        sessions = []
        def factory():
            session = original()
            sessions.append(session)
            return session
        with patch("requests.Session", side_effect=factory):
            self.check()
        self.get.assert_called_once_with(CONFIG.base_url + HEALTH_PATH,
            headers={"Authorization": "Bearer " + SECRET, "X-Internal-Client": "health-test",
                     "X-Request-ID": RID, "Accept": "application/json"},
            timeout=(5, 30), verify=True, allow_redirects=False)
        self.assertFalse(sessions[0].trust_env)
        self.assertEqual(sessions[0].get_adapter(CONFIG.base_url).max_retries.total, 0)

    def test_uuid_generado_y_uuid_entrada(self):
        def answer(url, **kwargs):
            rid = kwargs["headers"]["X-Request-ID"]
            self.assertEqual(str(UUID(rid)), rid)
            data = payload()
            data["requestId"] = rid
            return self.response(data)
        self.get.side_effect = answer
        result = GatewayTransport(CONFIG).health()
        self.assertEqual(result.request_id, self.get.call_args.kwargs["headers"]["X-Request-ID"])
        with self.assertRaises(GatewayValidationError):
            GatewayTransport(CONFIG).health(request_id="not-a-uuid")
        self.assertEqual(self.get.call_count, 1)

    def test_dto_y_config_inmutables(self):
        self.response()
        with self.assertRaises(FrozenInstanceError):
            self.check().status = "down"
        with self.assertRaises(FrozenInstanceError):
            CONFIG.enabled = False

    def test_aislamiento_imports_sin_proveedores_ni_negocio(self):
        package = Path(__file__).parents[1]
        forbidden = ("apps.preselecta", "apps.historial_pago", "apps.xcore_consumo.services",
                     "apps.xcore_consumo.tasks", "celery", "oracledb", "django.db")
        for source in package.glob("*.py"):
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
                    self.assertNotIn(node.func.attr, {"post", "put", "patch", "delete", "delay", "apply_async"})

    def test_bloqueo_http_socket_dns(self):
        import socket
        for operation in (lambda: requests.Session().send(requests.Request("GET", CONFIG.base_url).prepare()),
                          lambda: socket.socket(), lambda: socket.getaddrinfo("gateway.invalid", 443)):
            with self.assertRaises(AssertionError):
                operation()
