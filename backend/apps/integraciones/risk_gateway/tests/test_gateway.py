from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID

import requests

from apps.integraciones.risk_gateway.clientes import HDCGatewayClient, PreselectaGatewayClient
from apps.integraciones.risk_gateway.configuracion import GatewayConfig
from apps.integraciones.risk_gateway.contratos import HDCNormalizado, HDCRequest, PreselectaNormalizado, PreselectaRequest
from apps.integraciones.risk_gateway.errores import (
    GatewayConfigurationError, GatewayDisabled, GatewayForbidden, GatewayIdempotencyConflict,
    GatewayInvalidResponse, GatewayNetworkError, GatewayProviderError, GatewayRateLimited,
    GatewayTimeout, GatewayUnauthorized, GatewayValidationError,
)
from apps.integraciones.risk_gateway.transporte import GatewayTransport
from apps.xcore_consumo.services.credihoy_v2.gateway import adaptar_hdc_a_v2, adaptar_preselecta_a_v2
from apps.xcore_consumo.services.credihoy_v2.hechos import HechosInternosCrediHoyV2
from apps.xcore_consumo.services.credihoy_v2.unico.dto import EntradaCredihoyUnicoV2, EstadoOperandoUnico
from apps.xcore_consumo.services.credihoy_v2.unico.motor import construir_capacidad_unico_v2
from .fixtures import CID, RID, error_gateway, hdc, preselecta, request_hdc, request_preselecta


class OfflineCase(unittest.TestCase):
    def setUp(self):
        for target in ("requests.sessions.Session.send", "socket.socket", "socket.getaddrinfo", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("RED PROHIBIDA"))
            guard.start()
            self.addCleanup(guard.stop)
        self.config = GatewayConfig(enabled=True, base_url="https://gateway.invalid", internal_client="shadow-test",
                                    internal_secret="S" * 40)
        self.transport = GatewayTransport(self.config)

    def respuesta(self, payload, status=200, **headers):
        r = requests.Response()
        r.status_code = status
        r._content = json.dumps(payload).encode("utf-8")
        r._content_consumed = True
        r.headers.update({"Content-Type": "application/json", "X-Request-ID": RID})
        r.headers.update(headers)
        return r

    def consultar(self, payload, *, status=200, request_id=RID, idempotency_key=None, headers=None):
        dto = HDCRequest.desde_payload(request_hdc()) if payload.get("versionContrato") == "HDC_NORMALIZADO_V1" else PreselectaRequest.desde_payload(request_preselecta())
        with patch("requests.Session.post", return_value=self.respuesta(payload, status, **(headers or {}))) as post:
            resultado = self.transport.consultar(dto, request_id=request_id, idempotency_key=idempotency_key)
            return resultado, post


class StoredXmlTests(OfflineCase):
    def custody_response(self,**changes):
        import base64,hashlib
        xml=b'<Informes><Informe /></Informes>'
        packet={'versionContrato':'credihoy-hdc-custodia-v1','consultaNueva':False,
            'normalizadoReconstruidoDesdeXML':True,'xmlBase64':base64.b64encode(xml).decode(),
            'xmlSha256':hashlib.sha256(xml).hexdigest(),'normalizado':hdc()}
        packet.update(changes)
        response=self.response(**{'Content-Type':'application/json'})
        response._content=json.dumps(packet).encode()
        response.headers['X-Content-SHA256']=hashlib.sha256(response.content).hexdigest()
        return response

    def test_reconstructs_normalized_response_from_same_owned_custody_without_post(self):
        with patch('requests.Session.get',return_value=self.custody_response()) as get,patch('requests.Session.post') as post:
            normalized,xml=self.transport.obtener_xml(CID,request_id=RID,titular_sha256='a'*64,con_respuesta=True)
        self.assertEqual(normalized['requestId'],RID)
        self.assertEqual(normalized['resultado']['consulta_id'],CID)
        self.assertEqual(xml,b'<Informes><Informe /></Informes>')
        get.assert_called_once();post.assert_not_called()
        self.assertEqual(get.call_args.kwargs['headers']['Accept'],'application/json')

    def test_custody_rejects_new_query_marker_foreign_response_and_corrupt_xml(self):
        foreign=hdc();foreign['requestId']=CID
        for changes in ({'consultaNueva':True},{'xmlSha256':'0'*64},{'normalizado':foreign},
                {'normalizadoReconstruidoDesdeXML':False}):
            with self.subTest(fields=list(changes)),patch('requests.Session.get',return_value=self.custody_response(**changes)) as get:
                with self.assertRaises(GatewayInvalidResponse):
                    self.transport.obtener_xml(CID,request_id=RID,titular_sha256='a'*64,con_respuesta=True)
                get.assert_called_once()

    def response(self,**changes):
        import hashlib
        result=requests.Response();result.status_code=200
        result._content=b'<Informes><Informe /></Informes>';result._content_consumed=True
        result.headers.update({'Content-Type':'application/xml','X-Request-ID':RID,
            'X-HDC-Request-ID':RID,'X-HDC-Holder-SHA256':'a'*64,
            'X-Content-SHA256':hashlib.sha256(result.content).hexdigest()})
        result.headers.update(changes)
        return result

    def test_owned_xml_is_one_get_not_a_bureau_post(self):
        response=self.response()
        with patch('requests.Session.get',return_value=response) as get,patch('requests.Session.post') as post:
            raw=self.transport.obtener_xml(CID,request_id=RID,titular_sha256='a'*64)
        self.assertEqual(raw,response.content)
        get.assert_called_once();post.assert_not_called()
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertTrue(get.call_args.kwargs['verify'])
        self.assertTrue(get.call_args.args[0].endswith('/'+CID+'/pdf/'))
        self.assertEqual(get.call_args.kwargs['headers']['Accept'],'application/xml')

    def test_foreign_holder_request_or_corrupt_hash_are_rejected(self):
        for headers in ({'X-HDC-Holder-SHA256':'b'*64},{'X-HDC-Request-ID':CID},
                {'X-Content-SHA256':'0'*64},{'Content-Type':'text/html'}):
            with patch('requests.Session.get',return_value=self.response(**headers)) as get:
                with self.assertRaises(GatewayInvalidResponse):
                    self.transport.obtener_xml(CID,request_id=RID,titular_sha256='a'*64)
                get.assert_called_once()

    def test_timeout_never_repeats_a_request(self):
        with patch('requests.Session.get',side_effect=requests.Timeout) as get:
            with self.assertRaises(GatewayTimeout):self.transport.obtener_xml(CID,request_id=RID,titular_sha256='a'*64)
            get.assert_called_once()


class ConfigTests(OfflineCase):
    def test_disabled_default(self):
        with self.assertRaises(GatewayDisabled):
            GatewayTransport(GatewayConfig()).consultar(HDCRequest.desde_payload(request_hdc()))

    def test_disabled_settings_sin_secretos(self):
        self.assertEqual(GatewayConfig.from_settings(SimpleNamespace()), GatewayConfig())

    def test_enabled_incomplete(self):
        with self.assertRaises(GatewayConfigurationError):
            GatewayConfig(enabled=True).validar()

    def test_default_timeouts_ssl(self):
        self.assertEqual((self.config.connect_timeout, self.config.read_timeout, self.config.verify), (5, 30, True))

    def test_settings_conversion(self):
        valores = SimpleNamespace(RISK_GATEWAY_ENABLED="Y", RISK_GATEWAY_BASE_URL="https://gateway.invalid/",
            RISK_GATEWAY_INTERNAL_CLIENT="shadow-test", RISK_GATEWAY_INTERNAL_SECRET="S" * 40,
            RISK_GATEWAY_VERIFY_SSL="N", RISK_GATEWAY_CONNECT_TIMEOUT="2", RISK_GATEWAY_READ_TIMEOUT="4")
        c = GatewayConfig.from_settings(valores).validar()
        self.assertEqual((c.connect_timeout, c.read_timeout, c.verify), (2, 4, False))

    def test_reject_unsafe_urls(self):
        for url in ("http://gateway.invalid", "https://user:pass@gateway.invalid", "https://gateway.invalid/path",
                    "https://gateway.invalid?x=y", "https://gateway.invalid#x", "https://gateway.invalid:bad", "https://gateway.invalid\n"):
            with self.subTest(url=url), self.assertRaises(GatewayConfigurationError):
                replace(self.config, base_url=url).validar()

    def test_reject_bad_timeout(self):
        for t in (0, -1, True, "bad", "5", float("inf"), float("nan")):
            with self.subTest(t=t), self.assertRaises(GatewayConfigurationError):
                replace(self.config, read_timeout=t).validar()

    def test_reject_header_injection(self):
        for field, value in (("internal_client", "x\r\nx"), ("internal_secret", "short"), ("enabled", "Y"), ("verify_ssl", "Y")):
            with self.subTest(field=field), self.assertRaises(GatewayConfigurationError):
                replace(self.config, **{field: value}).validar()

    def test_ca_bundle(self):
        with patch("apps.integraciones.risk_gateway.configuracion.Path.is_file", return_value=True):
            self.assertEqual(replace(self.config, ca_bundle="synthetic.pem").validar().verify, "synthetic.pem")
        with patch("apps.integraciones.risk_gateway.configuracion.Path.is_file", return_value=False):
            with self.assertRaises(GatewayConfigurationError):
                replace(self.config, ca_bundle="synthetic.pem").validar()

    def test_secret_not_in_repr_errors(self):
        self.assertNotIn(self.config.internal_secret, repr(self.config))
        with self.assertRaises(GatewayConfigurationError) as ctx:
            replace(self.config, base_url="https://" + self.config.internal_secret + "@example.invalid").validar()
        self.assertNotIn(self.config.internal_secret, str(ctx.exception))


class ConfigArchivoHealthTests(OfflineCase):
    def settings_archivo(self, path, **kwargs):
        return SimpleNamespace(RISK_GATEWAY_ENABLED="Y", RISK_GATEWAY_BASE_URL="https://gateway.invalid",
            RISK_GATEWAY_INTERNAL_CLIENT="shadow-test", RISK_GATEWAY_INTERNAL_SECRET_FILE=str(path), **kwargs)

    def test_archivo_privado_sin_copiar_a_repr(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "synthetic-only"
            path.write_text("S" * 40 + "\n")
            path.chmod(0o600)
            config = GatewayConfig.from_settings(self.settings_archivo(path)).validar()
            self.assertEqual(config.internal_secret, "S" * 40)
            self.assertNotIn("S" * 40, repr(config))

    def test_off_no_abre_archivo(self):
        with patch("apps.integraciones.risk_gateway.configuracion.Path.open") as opened:
            c = GatewayConfig.from_settings(SimpleNamespace(RISK_GATEWAY_ENABLED="N",
                RISK_GATEWAY_INTERNAL_SECRET_FILE="NO_EXISTE"))
            self.assertEqual(c, GatewayConfig())
            opened.assert_not_called()

    def test_dos_fuentes_secretas_no_se_eligen_silenciosamente(self):
        with self.assertRaises(GatewayConfigurationError):
            GatewayConfig.from_settings(self.settings_archivo("NO_EXISTE", RISK_GATEWAY_INTERNAL_SECRET="S" * 40))

    def test_archivo_ausente_relativo_invalido_o_en_git(self):
        for path in ("relativo", Path(__file__).resolve()):
            with self.subTest(path=str(path)), self.assertRaises(GatewayConfigurationError):
                GatewayConfig.from_settings(self.settings_archivo(path))
        with TemporaryDirectory() as d:
            path = Path(d) / "synthetic-only"
            path.write_text("INVALID-SECRET-TOO-SHORT")
            with self.assertRaises(GatewayConfigurationError) as e:
                GatewayConfig.from_settings(self.settings_archivo(path))
            self.assertNotIn("INVALID-SECRET", str(e.exception))

    def test_health_usa_solo_get_autenticado_sin_providerchecks(self):
        p = {"requestId": RID, "status": "ready", "apiVersion": "v1", "providerChecks": False}
        response = self.respuesta(p)
        with patch("requests.Session.get", return_value=response) as get, patch("requests.Session.post") as post:
            self.assertEqual(self.transport.verificar_health(request_id=RID), p)
            get.assert_called_once()
            post.assert_not_called()
            self.assertEqual(get.call_args.args[0], "https://gateway.invalid/api/internal/v1/health/")
            self.assertEqual(get.call_args.kwargs["headers"]["X-Internal-Client"], "shadow-test")
            self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer " + "S" * 40)
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            self.assertTrue(get.call_args.kwargs["verify"])
            self.assertNotIn("Idempotency-Key", get.call_args.kwargs["headers"])

    def test_health_401_no_reintenta_ni_imprime_cuerpo(self):
        with patch("requests.Session.get", return_value=self.respuesta({"password": "NO_EXPOSE"}, 401)) as get:
            with self.assertRaises(GatewayUnauthorized) as e:
                self.transport.verificar_health(request_id=RID)
            get.assert_called_once()
            self.assertNotIn("NO_EXPOSE", str(e.exception))

    def test_health_no_certifica_scopes_por_un_200(self):
        p = {"requestId": RID, "status": "ready", "apiVersion": "v1", "providerChecks": False}
        for key, value in (("providerChecks", True), ("requestId", CID), ("apiVersion", "v2"), ("secret", "NO_EXPOSE")):
            with self.subTest(key=key), patch("requests.Session.get", return_value=self.respuesta({**p, key: value})):
                with self.assertRaises(GatewayInvalidResponse):
                    self.transport.verificar_health(request_id=RID)

    def test_health_tls_obligatorio_y_timeout_sin_post(self):
        with patch("requests.Session.get") as get, self.assertRaises(GatewayConfigurationError):
            GatewayTransport(replace(self.config, verify_ssl=False)).verificar_health()
        get.assert_not_called()
        with patch("requests.Session.get", side_effect=requests.Timeout) as get, patch("requests.Session.post") as post:
            with self.assertRaises(GatewayTimeout):
                self.transport.verificar_health()
            get.assert_called_once()
            post.assert_not_called()


class ContractTests(OfflineCase):
    def test_schema_keywords_supported(self):
        snapshot = json.loads((Path(__file__).parents[1] / "contrato_openapi.json").read_text(encoding="utf-8"))
        known = {"$ref", "type", "required", "properties", "additionalProperties", "nullable",
                 "oneOf", "allOf", "not", "format", "enum", "description", "pattern", "minLength",
                 "maxLength", "minItems", "maxItems", "minimum", "default", "items"}
        def check(schema):
            self.assertFalse(set(schema) - known)
            for sub in schema.get("properties", {}).values():
                check(sub)
            for key in ("items", "additionalProperties", "not"):
                if isinstance(schema.get(key), dict):
                    check(schema[key])
            for key in ("oneOf", "allOf"):
                for sub in schema.get(key, []):
                    check(sub)
        for schema in snapshot["schemas"].values():
            check(schema)
        self.assertEqual(len(snapshot["schemas"]), 32)

    def test_registered_date_requires_rfc3339(self):
        for fecha in ("2026-10-01T12:00-05:00", "2026-10-01", "2026-10-01T12:00:00", "2026-02-30T12:00:00Z"):
            p = preselecta()
            p["resultado"]["trazabilidad"]["fecha_registro"] = fecha
            with self.subTest(fecha=fecha), self.assertRaises(GatewayInvalidResponse):
                PreselectaNormalizado.desde_payload(p)

    def test_compound_duplicates_not_hidden(self):
        p = hdc()
        pares = [["EstadoPago", "XX"], ["EstadoPago", None], ["EstadoPago", "XX"]]
        p["resultado"]["obligaciones"][0]["estado_obligacion_compuesto"]["entradas_originales"] = pares
        dto = HDCNormalizado.desde_payload(p)
        self.assertEqual(dto.a_dict()["resultado"]["obligaciones"][0]["estado_obligacion_compuesto"]["entradas_originales"], pares)

    def test_documented_data_not_money_coercion(self):
        p = preselecta()
        p["resultado"]["resultado_proveedor"]["datos_documentados"] = {"valor_homologado": "no es importe"}
        self.assertEqual(PreselectaNormalizado.desde_payload(p).a_dict(), p)

    def test_hdc_complete_roundtrip(self):
        original = hdc()
        dto = HDCNormalizado.desde_payload(original)
        self.assertEqual(dto.a_dict(), original)

    def test_preselecta_complete_roundtrip(self):
        original = preselecta()
        self.assertEqual(PreselectaNormalizado.desde_payload(original).a_dict(), original)

    def test_decimal_exact(self):
        p = PreselectaNormalizado.desde_payload(preselecta())
        self.assertEqual(p["resultado"]["resultado_proveedor"]["score"]["valor_homologado"], Decimal("701.000000000000000001"))
        h = HDCNormalizado.desde_payload(hdc())
        self.assertEqual(h["resultado"]["obligaciones"][0]["valores"][0]["cuota"]["valor_monetario"], Decimal("1.234567890123456789"))

    def test_no_float_money(self):
        for value in (1.2, 0, True, "NaN", "Infinity", "bad", " 1", "1_000"):
            p = hdc()
            p["resultado"]["obligaciones"][0]["valores"][0]["cuota"]["valor_monetario"] = value
            with self.subTest(value=value), self.assertRaises(GatewayInvalidResponse):
                HDCNormalizado.desde_payload(p)

    def test_zero_null_unreported_foreign(self):
        v = HDCNormalizado.desde_payload(hdc())["resultado"]["obligaciones"][1]["valores"]
        self.assertEqual(v[0]["cuota"]["valor_monetario"], Decimal("0"))
        self.assertEqual(v[1]["cuota"]["valor_original"], "-1")
        self.assertFalse(v[1]["cuota"]["disponible"])
        self.assertIsNone(v[1]["cuota"]["valor_monetario"])
        self.assertTrue(v[2]["cuota"]["disponible"])
        self.assertIsNone(v[2]["cuota"]["valor_monetario"])

    def test_compound_second_null(self):
        p = hdc()
        p["resultado"]["obligaciones"][0]["estado_obligacion_compuesto"]["entradas_originales"] = [["EstadoPago", None]]
        self.assertEqual(HDCNormalizado.desde_payload(p).a_dict(), p)

    def test_compound_bad_pairs(self):
        for pairs in ([[None, "01"]], [["EstadoPago"]], [["EstadoPago", "01", "extra"]],
                      [[1, "01"]], [["EstadoPago", 1]], "bad", {"x": "y"}, [["EstadoPago", False]]):
            p = hdc()
            p["resultado"]["obligaciones"][0]["estado_obligacion_compuesto"]["entradas_originales"] = pairs
            with self.subTest(pairs=pairs), self.assertRaises(GatewayInvalidResponse):
                HDCNormalizado.desde_payload(p)

    def test_multiple_values_order_positions(self):
        p = hdc()
        p["resultado"]["obligaciones"][1]["valores"][0]["posicion_original"] = 7
        dto = HDCNormalizado.desde_payload(p)
        self.assertEqual([v["posicion_original"] for v in dto["resultado"]["obligaciones"][1]["valores"]], [7, 1, 2])

    def test_unknown_codes_preserved(self):
        dto = HDCNormalizado.desde_payload(hdc())
        self.assertEqual(dto["resultado"]["obligaciones"][0]["sector"]["codigo_original"], "DESCONOCIDO")

    def test_temporality_preserved(self):
        dto = HDCNormalizado.desde_payload(hdc())
        self.assertEqual(dto["resultado"]["obligaciones"][0]["valor_actual"].a_dict(), hdc()["resultado"]["obligaciones"][0]["valor_actual"])

    def test_preselecta_sin_informacion(self):
        p = preselecta(None, "SIN_INFORMACION")
        p["resultado"]["resultado_proveedor"]["riesgo"] = None
        p["resultado"]["resultado_proveedor"]["score"] = None
        dto = PreselectaNormalizado.desde_payload(p)
        self.assertIsNone(dto["resultado"]["resultado_proveedor"]["score"])

    def test_preselecta_provider_scalars(self):
        for v in (False, 0, None, "RECHAZADO"):
            p = preselecta()
            p["resultado"]["codigo_proveedor_original"] = v
            self.assertEqual(PreselectaNormalizado.desde_payload(p).a_dict(), p)

    def test_preselecta_validation_not_200(self):
        for estado in ("VALIDACION", "ERROR_TECNICO", "ERROR_PROVEEDOR", "DESCONOCIDO"):
            p = preselecta(estado=estado)
            p["resultado"]["error"] = {"codigo": "SINTETICO", "mensaje": "ERROR SINTETICO"}
            with self.subTest(estado=estado), self.assertRaises(GatewayInvalidResponse):
                PreselectaNormalizado.desde_payload(p)

    def test_version_not_fallback(self):
        for factory, cls in ((hdc, HDCNormalizado), (preselecta, PreselectaNormalizado)):
            p = factory()
            p["versionContrato"] = "V2"
            with self.assertRaises(GatewayInvalidResponse):
                cls.desde_payload(p)

    def test_trace_version_inconsistent(self):
        p = hdc()
        p["resultado"]["trazabilidad"]["version_contrato"] = "V2"
        with self.assertRaises(GatewayInvalidResponse):
            HDCNormalizado.desde_payload(p)

    def test_unknown_fields_rejected(self):
        p = hdc()
        p["resultado"]["raw"] = "not allowed"
        with self.assertRaises(GatewayInvalidResponse):
            HDCNormalizado.desde_payload(p)

    def test_missing_field(self):
        p = hdc()
        del p["resultado"]["obligaciones"][0]["valores"]
        with self.assertRaises(GatewayInvalidResponse):
            HDCNormalizado.desde_payload(p)

    def test_types_rejected(self):
        p = hdc()
        p["resultado"]["obligaciones"][0]["valor_actual"]["disponible"] = 0
        with self.assertRaises(GatewayInvalidResponse):
            HDCNormalizado.desde_payload(p)

    def test_immutable_safe_repr(self):
        dto = HDCNormalizado.desde_payload(hdc())
        with self.assertRaises(FrozenInstanceError):
            dto.campos = ()
        self.assertNotIn("SINTETICA", repr(dto))
        output = dto.a_dict()
        output["resultado"]["obligaciones"].clear()
        self.assertEqual(len(dto["resultado"]["obligaciones"]), 2)

    def test_hdc_requests_natural_juridica(self):
        natural = request_hdc()
        self.assertEqual(HDCRequest.desde_payload(natural).a_dict(), natural)
        juridica = dict(persona="juridica", person_id_type="2", person_id_number="0000000000", razon_social="EMPRESA SINTETICA")
        self.assertEqual(HDCRequest.desde_payload(juridica).a_dict(), juridica)

    def test_hdc_request_invalid_persona(self):
        p = request_hdc()
        p["razon_social"] = "BAD"
        with self.assertRaises(GatewayValidationError):
            HDCRequest.desde_payload(p)

    def test_preselecta_request_invalid(self):
        p = request_preselecta()
        p["parametros"]["ACTIVIDAD"] = "invalid"
        with self.assertRaises(GatewayValidationError):
            PreselectaRequest.desde_payload(p)

    def test_invalid_nature_no_credit_decision(self):
        p = preselecta()
        p["resultado"]["resultado_proveedor"]["naturaleza"] = "DECISION_FINAL"
        with self.assertRaises(GatewayInvalidResponse):
            PreselectaNormalizado.desde_payload(p)


class TransportTests(OfflineCase):
    def test_preselecta_validation_error_fixtures(self):
        for payload, status, cls in ((error_gateway(validacion=True), 422, GatewayValidationError),
                                    (error_gateway(), 502, GatewayProviderError)):
            with self.subTest(status=status), self.assertRaises(cls):
                self.consultar(payload, status=status)

    def test_no_cache_no_replay(self):
        with patch("requests.Session.post", side_effect=[self.respuesta(hdc()), self.respuesta(hdc())]) as post:
            request = HDCRequest.desde_payload(request_hdc())
            for _ in range(2):
                self.transport.consultar(request, request_id=RID, idempotency_key="key-explicita")
            self.assertEqual(post.call_count, 2)

    def test_disabled_never_creates_session(self):
        with patch("requests.Session") as factory:
            with self.assertRaises(GatewayDisabled):
                GatewayTransport(GatewayConfig()).consultar(HDCRequest.desde_payload(request_hdc()))
            factory.assert_not_called()

    def test_configuration_failure_never_creates_session(self):
        with patch("requests.Session") as factory:
            with self.assertRaises(GatewayConfigurationError):
                GatewayTransport(GatewayConfig(enabled=True)).consultar(HDCRequest.desde_payload(request_hdc()))
            factory.assert_not_called()

    def test_headers_and_endpoint_preselecta(self):
        dto, post = self.consultar(preselecta())
        args, kwargs = post.call_args
        self.assertEqual(args, ("https://gateway.invalid/api/internal/v1/preselecta/consultar/",))
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer " + "S" * 40,
            "X-Internal-Client": "shadow-test", "X-Request-ID": RID,
            "Content-Type": "application/json", "Accept": "application/json"})
        self.assertEqual(kwargs["timeout"], (5, 30))
        self.assertTrue(kwargs["verify"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["json"], request_preselecta())
        self.assertIsInstance(dto, PreselectaNormalizado)

    def test_hdc_endpoint_independent(self):
        dto, post = self.consultar(hdc())
        self.assertEqual(post.call_args.args, ("https://gateway.invalid/api/internal/v1/hdc/consultar/",))
        self.assertEqual(post.call_count, 1)
        self.assertIsInstance(dto, HDCNormalizado)

    def test_generated_request_id(self):
        def respuesta(_, **kwargs):
            rid = kwargs["headers"]["X-Request-ID"]
            UUID(rid)
            p = hdc()
            p["requestId"] = p["resultado"]["request_id"] = rid
            return self.respuesta(p, **{"X-Request-ID": rid})
        with patch("requests.Session.post", side_effect=respuesta) as post:
            r = self.transport.consultar(HDCRequest.desde_payload(request_hdc()))
            self.assertNotEqual(r["requestId"], RID)
            self.assertEqual(post.call_count, 1)

    def test_invalid_id_generates_uuid(self):
        def respuesta(_, **kwargs):
            rid = kwargs["headers"]["X-Request-ID"]
            UUID(rid)
            p = preselecta()
            p["requestId"] = p["resultado"]["request_id"] = rid
            return self.respuesta(p, **{"X-Request-ID": rid})
        with patch("requests.Session.post", side_effect=respuesta):
            self.transport.consultar(PreselectaRequest.desde_payload(request_preselecta()), request_id="invalid")

    def test_valid_id_reused(self):
        r, post = self.consultar(hdc())
        self.assertEqual(r["requestId"], post.call_args.kwargs["headers"]["X-Request-ID"])

    def test_inconsistent_header(self):
        with self.assertRaises(GatewayInvalidResponse):
            self.consultar(hdc(), headers={"X-Request-ID": CID})

    def test_missing_header(self):
        with self.assertRaises(GatewayInvalidResponse):
            self.consultar(hdc(), headers={"X-Request-ID": ""})

    def test_inconsistent_sent_id(self):
        with self.assertRaises(GatewayInvalidResponse):
            self.consultar(hdc(), request_id=CID)

    def test_inconsistent_result_id(self):
        p = hdc()
        p["resultado"]["request_id"] = CID
        with self.assertRaises(GatewayInvalidResponse):
            self.consultar(p)

    def test_rejected_provider_is_success(self):
        r, post = self.consultar(preselecta("RECHAZADO"))
        self.assertEqual(r["resultado"]["resultado_proveedor"]["decision"]["valor_original"], "RECHAZADO")
        self.assertEqual(post.call_count, 1)
        self.assertNotIn("preaprobado", r.a_dict())

    def test_all_http_errors_no_retry_no_body_leak(self):
        statuses = {400: GatewayValidationError, 401: GatewayUnauthorized, 403: GatewayForbidden,
            405: GatewayValidationError, 406: GatewayValidationError, 409: GatewayIdempotencyConflict,
            415: GatewayValidationError, 422: GatewayValidationError, 429: GatewayRateLimited,
            500: GatewayProviderError, 502: GatewayProviderError, 503: GatewayProviderError, 504: GatewayTimeout}
        for status, cls in statuses.items():
            with self.subTest(status=status):
                response = self.respuesta({"traceback": "privado", "secret": self.config.internal_secret}, status)
                with patch("requests.Session.post", return_value=response) as post:
                    with self.assertRaises(cls) as ctx:
                        self.transport.consultar(HDCRequest.desde_payload(request_hdc()), request_id=RID)
                self.assertEqual(post.call_count, 1)
                self.assertEqual(ctx.exception.status, status)
                self.assertNotIn("privado", str(ctx.exception))
                self.assertNotIn(self.config.internal_secret, repr(ctx.exception))
                self.assertEqual(ctx.exception.request_id, RID)

    def test_remote_nonjson_error_preserves_status(self):
        response = self.respuesta({}, 401)
        response._content = b"not JSON secret internal"
        with patch("requests.Session.post", return_value=response):
            with self.assertRaises(GatewayUnauthorized):
                self.transport.consultar(HDCRequest.desde_payload(request_hdc()))

    def test_timeout_no_retry(self):
        with patch("requests.Session.post", side_effect=requests.ReadTimeout("secret PII")) as post:
            with self.assertRaises(GatewayTimeout) as ctx:
                self.transport.consultar(HDCRequest.desde_payload(request_hdc()))
            self.assertEqual(post.call_count, 1)
            self.assertNotIn("secret", str(ctx.exception))

    def test_network_no_retry(self):
        with patch("requests.Session.post", side_effect=requests.ConnectionError("secret PII")) as post:
            with self.assertRaises(GatewayNetworkError) as ctx:
                self.transport.consultar(HDCRequest.desde_payload(request_hdc()))
            self.assertEqual(post.call_count, 1)
            self.assertNotIn("secret", str(ctx.exception))

    def test_tls_failure_safe(self):
        with patch("requests.Session.post", side_effect=requests.exceptions.SSLError("private path")):
            with self.assertRaises(GatewayNetworkError):
                self.transport.consultar(HDCRequest.desde_payload(request_hdc()))

    def test_no_default_idempotency(self):
        _, post = self.consultar(hdc())
        self.assertNotIn("Idempotency-Key", post.call_args.kwargs["headers"])

    def test_explicit_idempotency(self):
        _, post = self.consultar(hdc(), idempotency_key="synthetic:operation-1")
        self.assertEqual(post.call_args.kwargs["headers"]["Idempotency-Key"], "synthetic:operation-1")

    def test_invalid_idempotency_no_http(self):
        for key in ("", "a\r\nx", "x" * 129, True):
            with self.subTest(key=key), patch("requests.Session.post") as post:
                with self.assertRaises(GatewayValidationError):
                    self.transport.consultar(HDCRequest.desde_payload(request_hdc()), idempotency_key=key)
                post.assert_not_called()

    def test_metadata_mismatch_no_http(self):
        p = request_hdc()
        p["metadata"] = {"consumidor": "different-client"}
        with patch("requests.Session.post") as post:
            with self.assertRaises(GatewayValidationError):
                self.transport.consultar(HDCRequest.desde_payload(p))
            post.assert_not_called()

    def test_invalid_json(self):
        r = self.respuesta({})
        r._content = b"invalid JSON"
        with patch("requests.Session.post", return_value=r):
            with self.assertRaises(GatewayInvalidResponse):
                self.transport.consultar(HDCRequest.desde_payload(request_hdc()))

    def test_duplicate_json_keys(self):
        r = self.respuesta({})
        r._content = b'{"requestId":"x","requestId":"y"}'
        with patch("requests.Session.post", return_value=r):
            with self.assertRaises(GatewayInvalidResponse):
                self.transport.consultar(HDCRequest.desde_payload(request_hdc()))

    def test_invalid_json_constants(self):
        r = self.respuesta({})
        r._content = b'{"value": NaN}'
        with patch("requests.Session.post", return_value=r):
            with self.assertRaises(GatewayInvalidResponse):
                self.transport.consultar(HDCRequest.desde_payload(request_hdc()))

    def test_content_type_invalid(self):
        with self.assertRaises(GatewayInvalidResponse):
            self.consultar(hdc(), headers={"Content-Type": "text/html"})

    def test_redirect_no_retry(self):
        with self.assertRaises(GatewayInvalidResponse):
            self.consultar(hdc(), status=302)

    def test_response_closed(self):
        r = self.respuesta(hdc())
        with patch("requests.Session.post", return_value=r), patch.object(r, "close") as close:
            self.transport.consultar(HDCRequest.desde_payload(request_hdc()), request_id=RID)
            close.assert_called_once()

    def test_default_adapters_have_no_retries(self):
        with requests.Session() as s:
            self.assertEqual(s.get_adapter("https://gateway.invalid").max_retries.total, 0)

    def test_no_netrc_or_environment_proxy(self):
        with patch("requests.Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.post.return_value = self.respuesta(hdc())
            self.transport.consultar(HDCRequest.desde_payload(request_hdc()), request_id=RID)
            self.assertIs(session.trust_env, False)

    def test_clients_independent(self):
        with patch.object(self.transport, "consultar", return_value="SINTETICO") as consultar:
            PreselectaGatewayClient(self.transport).consultar(PreselectaRequest.desde_payload(request_preselecta()))
            self.assertEqual(consultar.call_count, 1)
            consultar.reset_mock()
            HDCGatewayClient(self.transport).consultar(HDCRequest.desde_payload(request_hdc()))
            self.assertEqual(consultar.call_count, 1)

    def test_request_types_not_interchangeable(self):
        with self.assertRaises(GatewayValidationError):
            PreselectaGatewayClient(self.transport).consultar(HDCRequest.desde_payload(request_hdc()))
        with self.assertRaises(GatewayValidationError):
            HDCGatewayClient(self.transport).consultar(PreselectaRequest.desde_payload(request_preselecta()))


class DomainTests(OfflineCase):
    def test_hdc_adapter_complete_source_preserved(self):
        source = HDCNormalizado.desde_payload(hdc())
        dto = adaptar_hdc_a_v2(source)
        self.assertIs(dto.evidencia_gateway_hdc, source)
        self.assertEqual(len(dto.cuentas), 2)
        self.assertEqual(dto.cuentas[1].tipo_cuenta.valor, "TarjetaCredito")
        self.assertEqual(dto.campos_faltantes, ("embargo",))
        self.assertEqual([(c.tipo_cuenta, c.registros_observados) for c in dto.cobertura_por_tipo],
                         [("CuentaCartera", 1), ("TarjetaCredito", 1), ("CuentaAhorro", 0), ("CuentaCorriente", 0), ("Otros", 0)])

    def test_adapter_all_values_no_selection_no_sum(self):
        source = HDCNormalizado.desde_payload(hdc())
        dto = adaptar_hdc_a_v2(source)
        self.assertEqual(len(dto.cuentas[1].observaciones), 3)
        self.assertEqual([v.posicion_original for v in dto.cuentas[1].observaciones], [0, 1, 2])
        self.assertFalse(dto.evidencia_gateway_hdc["resultado"]["obligaciones"][1]["valor_actual"]["disponible"])
        self.assertNotIn("cuotas_deudas_hdc_brutas", dto.a_dict())

    def test_adapter_money_no_conversion(self):
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()))
        money = dto.cuentas[0].observaciones[0].cuota
        self.assertEqual(money.valor_normalizado, Decimal("1.234567890123456789"))
        self.assertEqual(money.evidencia_gateway["estado_conversion"], "CONVERTIDO_MONEDA_LEGAL")
        self.assertEqual(money.valor_original, "1.234567890123456789")

    def test_adapter_zero_foreign_unreported(self):
        v = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc())).cuentas[1].observaciones
        self.assertEqual(v[0].cuota.valor_normalizado, Decimal("0"))
        self.assertIsNone(v[1].cuota.valor_normalizado)
        self.assertEqual(v[1].cuota.valor_original, "-1")
        self.assertIsNone(v[2].cuota.valor_normalizado)
        self.assertEqual(v[2].cuota.moneda_original, "2")

    def test_adapter_missing_embargo_no_inference(self):
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()))
        self.assertIsNone(dto.cuentas[0].embargo.valor)
        self.assertFalse(dto.cuentas[0].embargo.disponible)

    def test_adapter_missing_days_no_inference(self):
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()))
        self.assertIsNone(dto.cuentas[0].observaciones[0].dias_mora.valor)

    def test_adapter_unknown_periodicity_sector(self):
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()))
        self.assertEqual(dto.cuentas[0].sector_codigo_original.valor, "DESCONOCIDO")
        self.assertEqual(dto.cuentas[0].observaciones[0].periodicidad_original.valor, "DESCONOCIDA")

    def test_adapter_missing_date_not_fabricated(self):
        p = hdc()
        p["resultado"]["fecha_consulta"] = None
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(p))
        self.assertIsNone(dto.fecha_consulta)
        self.assertIsNone(dto.fecha_consulta_original)

    def test_adapter_date_no_timezone_assumed(self):
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()))
        self.assertIsNone(dto.fecha_consulta)
        self.assertEqual(dto.fecha_consulta_original, "2026-10-01T12:00:00")

    def test_adapter_aware_date(self):
        p = hdc()
        p["resultado"]["fecha_consulta"] = "2026-10-01T12:00:00Z"
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(p))
        self.assertIsNotNone(dto.fecha_consulta.utcoffset())
        self.assertEqual(dto.fecha_consulta_original, p["resultado"]["fecha_consulta"])

    def test_adapter_original_noniso_date(self):
        p = hdc()
        p["resultado"]["fecha_consulta"] = "fecha original no certificada"
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(p))
        self.assertEqual(dto.fecha_consulta_original, p["resultado"]["fecha_consulta"])
        self.assertIsNone(dto.fecha_consulta)

    def test_preselecta_adapter_provider_not_approval(self):
        source = PreselectaNormalizado.desde_payload(preselecta("RECHAZADO"))
        dto = adaptar_preselecta_a_v2(source)
        self.assertEqual(dto.decision.valor, "RECHAZADO")
        self.assertEqual(dto.score.valor, Decimal("701.000000000000000001"))
        self.assertIs(dto.evidencia_gateway, source)
        self.assertNotIn("preaprobado", dto.a_dict())

    def test_preselecta_nontext_not_forced(self):
        p = preselecta()
        p["resultado"]["resultado_proveedor"]["decision"]["valor_original"] = True
        dto = adaptar_preselecta_a_v2(PreselectaNormalizado.desde_payload(p))
        self.assertIsNone(dto.decision.valor)
        self.assertTrue(dto.evidencia_gateway["resultado"]["resultado_proveedor"]["decision"]["valor_original"])

    def test_combined_adapter_no_queries(self):
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()),
                              preselecta=PreselectaNormalizado.desde_payload(preselecta()))
        self.assertEqual(dto.preselecta.decision.valor, "APROBADO")

    def test_adapter_empty_obligations_no_favorable_defaults(self):
        p = hdc()
        p["resultado"]["obligaciones"] = []
        p["resultado"]["estado_tecnico"] = "SIN_INFORMACION"
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(p))
        self.assertEqual(dto.cuentas, ())
        self.assertTrue(all(c.completa is None for c in dto.cobertura_por_tipo))

    def test_adapter_no_values_valid(self):
        p = hdc()
        p["resultado"]["obligaciones"][0]["valores"] = []
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(p))
        self.assertEqual(dto.cuentas[0].observaciones, ())

    def test_unico_brutas_still_unavailable(self):
        adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()))
        resultado = construir_capacidad_unico_v2(EntradaCredihoyUnicoV2(), hechos_internos=HechosInternosCrediHoyV2())
        self.assertIsNone(resultado.cuotas_deudas_hdc_brutas.valor)
        self.assertFalse(resultado.cuotas_deudas_hdc_brutas.disponible)
        self.assertEqual(resultado.cuotas_deudas_hdc_brutas.estado, EstadoOperandoUnico.HOMOLOGACION_HDC_PENDIENTE)

    def test_gateway_import_cannot_modify_value(self):
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()))
        with self.assertRaises(ValueError):
            replace(dto.cuentas[0].observaciones[0].cuota, valor_normalizado=Decimal("1"))

    def test_gateway_date_cannot_modify_original(self):
        dto = adaptar_hdc_a_v2(HDCNormalizado.desde_payload(hdc()))
        with self.assertRaises(ValueError):
            replace(dto, fecha_consulta_original=None)

    def test_no_local_provider_stack_or_catalogs(self):
        root = Path(__file__).parents[1]
        for file in root.glob("*.py"):
            text = file.read_text(encoding="utf-8")
            for forbidden in ("consultarHC2", "BinarySecurityToken", "UsernameToken", "WS-Security",
                              "import zeep", "import lxml", "ElementTree", "hdc_catalogs", "* 1000", "* Decimal(\"1000\")"):
                with self.subTest(file=file.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)

    def test_no_legacy_pipeline_or_persistence_dependencies(self):
        root = Path(__file__).parents[1]
        for file in root.glob("*.py"):
            text = file.read_text(encoding="utf-8")
            for forbidden in ("apps.historial_pago", "apps.preselecta", "celery", "AuditLog", "DecisionFinal", "procesar_consumo"):
                with self.subTest(file=file.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)

    def test_network_guards_active(self):
        import socket
        with self.assertRaises(AssertionError):
            socket.getaddrinfo("invalid", 443)
        with self.assertRaises(AssertionError):
            requests.Session().send(requests.Request("GET", "https://invalid").prepare())
