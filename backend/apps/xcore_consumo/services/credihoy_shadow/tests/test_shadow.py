from copy import deepcopy
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
import requests

from apps.decisiones.models import DecisionFinal
from apps.historial_pago.models import HistorialPagoConsulta
from apps.preselecta.models import PreselectaConsulta
from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import ConsentimientoConsumo, EvaluacionConsumo, SolicitudConsumo
from apps.xcore_consumo.services import pipeline
from apps.xcore_consumo.services.credihoy import EstadoEvaluacion
from apps.xcore_consumo.services.credihoy.tests.fixtures import politica_sintetica
from apps.xcore_consumo.services.credihoy_integracion.internos import AdaptadorHechosInternos
from apps.xcore_consumo.services.credihoy_integracion.servicio import ServicioEvaluacionCrediHoy
from apps.xcore_consumo.services.credihoy_integracion.tests.fixtures import cliente_mockeado, FECHA_HTTP
from apps.xcore_consumo.services.credihoy_shadow.evidencia import recuperar_externos, formulario_shadow
from apps.xcore_consumo.services.credihoy_shadow.servicio import (
    ServicioEvaluacionCrediHoyShadow, ejecutar_shadow_seguro, programar_shadow,
)


P = "apps.xcore_consumo.services.pipeline."
S = "apps.xcore_consumo.services.credihoy_shadow.servicio."
E = "apps.xcore_consumo.services.credihoy_shadow.evidencia."


@override_settings(CREDIHOY_SHADOW_ENABLED=False)
class ShadowTests(TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("socket.socket", side_effect=AssertionError("Red prohibida")).start()
        patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP real prohibido")).start()
        self.clock = patch("django.utils.timezone.now", return_value=FECHA_HTTP).start()
        self.secuencia = 0
        patch(S + "timezone.localtime", return_value=FECHA_HTTP).start()
        persona = Solicitante.objects.create(tipo_identificacion="CC", numero_identificacion="1234567890",
            primer_apellido="PRUEBA", fecha_expedicion=date(2010, 1, 1), celular="3000000000", email="test@example.invalid")
        self.solicitud = Solicitud.objects.create(solicitante=persona, producto="CONSUMO")
        self.form = {"monto_solicitado": "1000000", "plazo": "24", "smmlv": "1000000",
                     "garantia": "Firma Personal", "forma_pago": "Libranza", "asalariados": "3000000",
                     "pensionados": "0", "prestadores_prof": "0", "independientes": "0",
                     "rentistas_capital": "0", "transportadores": "0"}
        self.detail = SolicitudConsumo.objects.create(solicitud=self.solicitud, oracle_consultado=True,
                                                     form_data=self.form, core_data={})
        ConsentimientoConsumo.objects.create(solicitud=self.solicitud, aceptado=True,
                                             fecha_aceptacion=FECHA_HTTP, text_hash="HASH-SINTETICO")
        self.payload_hdc = {"numero_identificacion": "1234567890", "primer_apellido": "PRUEBA",
                            "tipo_identificacion": "1", "provider_mode": "real"}
        self.payload_pre = {"idNumber": "1234567890", "firstLastName": "PRUEBA", "idType": "1"}
        self.raw = {"estado": "OK", "score_pago": 700, "mora_maxima": 0, "categoria": "A",
                    "embargo": False, "experiencia": 12, "obligaciones": [], "xml": "XML-NO-DUPLICAR"}
        self.pre = {"decision": "APROBADO", "risk_level": "Bajo", "estado": "OK",
                    "request_payload": self.payload_pre,
                    "engine_response": [{"key": "DECISION", "value": "APROBADO"},
                                        {"key": "RIESGO_SCORE", "value": "Bajo"},
                                        {"key": "SCORE", "value": "700"}]}
        self.pre_mock = patch(P + "PreselectaClient", return_value=Mock()).start()
        self.pre_mock.return_value.evaluate.return_value = self.pre
        patch(P + "_payload_preselecta", return_value=self.payload_pre).start()
        patch(P + "assert_otp_verified").start()
        patch(P + "assert_consent_granted").start()
        self.hdc_mock = patch(P + "_consultar_historial", side_effect=lambda *a, **k: (
            deepcopy(self.payload_hdc), deepcopy(self.raw), {"obligaciones_abiertas": [], "metrics": {}})).start()
        self.soap_mock = patch(P + "HistorialPagoSOAPClient").start()
        self.familiar_mock = patch(P + "consultar_familiar", return_value={"resultado": "NO"}).start()
        self.calc = patch(P + "evaluar_xcore_consumo", return_value={
            "decision_final": "Cr\u00e9dito Aprobado", "valor_monto_max_decision": "1000000",
            "tasa_efectiva_calculada": "12.00", "total_valor_cuota": "50000"}).start()
        patch(P + "build_consumo_decision_pdf", return_value=b"PDF-PRODUCTIVO").start()
        self.api, self.http, _ = cliente_mockeado()
        self.integracion = ServicioEvaluacionCrediHoy(cliente=self.api, adaptador=AdaptadorHechosInternos(),
                                                     politica=politica_sintetica())
        patch(S + "ConfiguracionApiData.desde_settings", return_value=self.api.configuracion).start()
        patch(S + "ApiDataCrediHoyClient", return_value=self.api).start()

    def flujo(self, enabled):
        from apps.xcore_consumo.tasks import evaluar_credihoy_shadow
        self.secuencia += 1
        self.clock.return_value = FECHA_HTTP + timedelta(microseconds=self.secuencia)
        with override_settings(CREDIHOY_SHADOW_ENABLED=enabled):
            with patch("apps.xcore_consumo.services.credihoy_shadow.asincrono.current_app.send_task") as publicar:
                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    result = pipeline.procesar_consumo(self.solicitud, actor="test", selected_hc2_keys=[], request_id="REQ-SHADOW")
            # Simula entrega fuera del request, sin broker y con retries ya agotados.
            for llamada in publicar.call_args_list:
                evaluar_credihoy_shadow.push_request(retries=3)
                try:
                    evaluar_credihoy_shadow.run(**llamada.kwargs["kwargs"])
                finally:
                    evaluar_credihoy_shadow.pop_request()
        return result, callbacks

    def snapshot_productivo(self):
        datos = {}
        for modelo in (Solicitud, SolicitudConsumo, EvaluacionConsumo, DecisionFinal):
            filas = list(modelo.objects.values())
            for fila in filas:
                for campo in ("created_at", "updated_at"):
                    fila.pop(campo, None)
            datos[modelo.__name__] = filas
        return datos

    def auditoria(self):
        return AuditLog.objects.filter(event__startswith="credihoy_shadow_").latest("id")

    def test_flag_off_no_registra_callback_ni_consulta(self):
        with patch(S + "ServicioEvaluacionCrediHoyShadow.evaluar") as evaluar:
            _, callbacks = self.flujo(False)
        self.assertEqual(callbacks, [])
        evaluar.assert_not_called()
        self.http.post.assert_not_called()
        self.assertFalse(AuditLog.objects.filter(event__startswith="credihoy_shadow_").exists())

    def test_flag_on_postcommit_una_ejecucion_mismo_producto_que_off(self):
        off, _ = self.flujo(False)
        antes = self.snapshot_productivo()
        on, callbacks = self.flujo(True)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(antes, self.snapshot_productivo())
        self.assertEqual(off[2], on[2])
        self.assertEqual(on[1].resultado, "APROBADO")
        self.assertEqual(self.http.post.call_count, 1)
        self.assertEqual(self.pre_mock.return_value.evaluate.call_count, 2)
        self.assertEqual(self.hdc_mock.call_count, 2)
        self.assertEqual(self.familiar_mock.call_count, 2)
        self.soap_mock.assert_not_called()

    def test_cuatro_estados_shadow_no_tienen_efecto_productivo(self):
        self.flujo(False)
        antes = self.snapshot_productivo()
        for estado in EstadoEvaluacion:
            with self.subTest(estado=estado):
                original = self.integracion.evaluar
                def evaluar(**kwargs):
                    e = original(**kwargs)
                    return replace(e, resultado=replace(e.resultado, estado_global=estado))
                with patch(S + "ServicioEvaluacionCrediHoy", return_value=Mock(evaluar=evaluar)):
                    resultado, _ = self.flujo(True)
                self.assertEqual(antes, self.snapshot_productivo())
                self.assertEqual(resultado[2], b"PDF-PRODUCTIVO")
                self.assertEqual(self.auditoria().payload["estado_global"], estado.value)
                self.assertFalse(self.auditoria().payload["autorizado_decision_crediticia"])

    def test_timeout_no_rompe_pipeline_y_no_fabrica_hechos(self):
        self.flujo(False)
        antes = self.snapshot_productivo()
        self.http.post.side_effect = requests.Timeout("BEARER-SECRETO")
        self.flujo(True)
        self.assertEqual(antes, self.snapshot_productivo())
        payload = self.auditoria().payload
        self.assertIn("API_DATA_TIMEOUT", payload["errores_tecnicos"])
        self.assertFalse(payload["hechos"]["interno"]["aporte_social_203"]["disponible"])
        self.assertFalse(payload["c4"]["saldo_individual_observado_disponible"])
        self.assertIsNone(payload["c4"]["saldo_certificado"])
        self.assertFalse(payload["c4"]["utilizable_decision"])
        self.assertNotIn("BEARER-SECRETO", json.dumps(payload))

    def test_error_normalizacion_hdc_no_rompe_pipeline(self):
        self.flujo(False)
        antes = self.snapshot_productivo()
        with patch(E + "normalizar_hdc_credihoy", side_effect=ValueError("XML-SECRETO")):
            self.flujo(True)
        self.assertEqual(antes, self.snapshot_productivo())
        self.assertIn("SHADOW_HDC_NORMALIZACION", self.auditoria().payload["errores_tecnicos"])

    def test_excepcion_inesperada_no_rompe_pipeline(self):
        self.flujo(False)
        antes = self.snapshot_productivo()
        with patch(S + "ServicioEvaluacionCrediHoyShadow.evaluar", side_effect=RuntimeError("TOKEN-SECRETO")):
            self.flujo(True)
        self.assertEqual(antes, self.snapshot_productivo())
        self.assertEqual(self.auditoria().event, "credihoy_shadow_error")
        self.assertNotIn("TOKEN-SECRETO", json.dumps(self.auditoria().payload))

    def test_error_auditoria_no_rompe_pipeline(self):
        self.flujo(False)
        antes = self.snapshot_productivo()
        real = AuditLog.objects.create
        def crear(**kwargs):
            if kwargs.get("event", "").startswith("credihoy_shadow_"):
                raise DatabaseError("FALLO-DB-SECRETO")
            return real(**kwargs)
        with patch.object(AuditLog.objects, "create", side_effect=crear):
            self.flujo(True)
        self.assertEqual(antes, self.snapshot_productivo())
        self.assertTrue(DecisionFinal.objects.exists())

    def test_error_programacion_no_rompe_pipeline(self):
        self.flujo(False)
        antes = self.snapshot_productivo()
        with patch(S + "programar_shadow", side_effect=RuntimeError("fallo")):
            _, callbacks = self.flujo(True)
        self.assertEqual(callbacks, [])
        self.assertEqual(antes, self.snapshot_productivo())

    def test_c3_null_c4_observado_no_certificado(self):
        self.flujo(True)
        payload = self.auditoria().payload
        self.assertEqual(payload["estado_global"], "INFORMACION_INSUFICIENTE")
        self.assertIn("REVISION_MANUAL_CAPACIDAD", payload["motivos"])
        r = next(r for r in payload["reglas"] if r["codigo"] == "CH07_CUOTA_INTERNA")
        self.assertIsNone(r["valor_evaluado"]["cuota_vigente_interna"])
        self.assertFalse(payload["hechos"]["interno"]["aporte_social_203"]["disponible"])
        self.assertTrue(payload["c4"]["saldo_individual_observado_disponible"])
        self.assertIsNone(payload["c4"]["saldo_certificado"])
        self.assertFalse(payload["c4"]["utilizable_decision"])

    def test_correlation_id_y_auditoria_sin_secretos_ni_xml(self):
        self.flujo(True)
        evento = self.auditoria()
        self.assertEqual(evento.request_id, "REQ-SHADOW")
        self.assertEqual(self.http.post.call_args.kwargs["headers"]["X-Request-ID"], "REQ-SHADOW")
        for dato in ("XML-NO-DUPLICAR", "1234567890", "TOKEN-SINTETICO", "REF-SINTETICA-1"):
            self.assertNotIn(dato, json.dumps(evento.payload))
        for campo in ("solicitud_id", "fecha_evaluacion", "request_id", "version_politica",
                      "version_contrato_interno", "estado_global", "reglas", "hechos", "errores_tecnicos"):
            self.assertIn(campo, evento.payload)

    def test_auditoria_append_only(self):
        self.flujo(True)
        inicial = self.auditoria()
        contenido = deepcopy(inicial.payload)
        self.flujo(True)
        inicial.refresh_from_db()
        self.assertEqual(inicial.payload, contenido)
        self.assertEqual(AuditLog.objects.filter(event="credihoy_shadow_evaluado").count(), 2)

    def test_request_id_largo_se_preserva_en_http_y_payload(self):
        self.flujo(False)
        referencia = "R" * 128
        ServicioEvaluacionCrediHoyShadow(integracion=self.integracion).evaluar(self.solicitud, request_id=referencia)
        self.assertEqual(self.http.post.call_args.kwargs["headers"]["X-Request-ID"], referencia)
        self.assertEqual(self.auditoria().payload["request_id"], referencia)
        self.assertEqual(self.auditoria().request_id, referencia[:120])

    def test_rollback_no_ejecuta_shadow(self):
        with override_settings(CREDIHOY_SHADOW_ENABLED=True), self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    programar_shadow(self.solicitud.pk)
                    raise ValueError("rollback")
            except ValueError:
                pass
        self.assertEqual(callbacks, [])
        self.http.post.assert_not_called()

    def test_no_corre_antes_del_commit(self):
        with override_settings(CREDIHOY_SHADOW_ENABLED=True), self.captureOnCommitCallbacks(execute=False) as callbacks:
            pipeline.procesar_consumo(self.solicitud, actor="test", selected_hc2_keys=[])
            self.http.post.assert_not_called()
        self.assertEqual(len(callbacks), 1)

    def test_reuso_preselecta_hdc_persistidos_sin_clientes(self):
        self.flujo(False)
        e = EvaluacionConsumo.objects.get(solicitud=self.solicitud)
        datos, errores = recuperar_externos(self.solicitud, e)
        self.assertEqual(datos.score.valor, Decimal("700"))
        self.assertEqual(datos.mora.valor, 0)
        self.assertEqual(datos.decision_preselecta.valor, "APROBADO")
        self.assertEqual(datos.score_preselecta.valor, Decimal("700"))
        self.assertFalse(errores)
        self.http.post.assert_not_called()
        self.soap_mock.assert_not_called()

    def test_no_reusa_identidad_demo_o_diferente(self):
        self.flujo(False)
        e = EvaluacionConsumo.objects.get(solicitud=self.solicitud)
        for cambios in ({"numero_identificacion": "OTRO"}, {"provider_test_identity": True}, {"provider_mode": "test"}):
            with self.subTest(cambios=cambios):
                e.integraciones_snapshot["historial_payload"] = self.payload_hdc | cambios
                datos, _ = recuperar_externos(self.solicitud, e)
                self.assertFalse(datos.score.disponible)

    def test_no_usa_defaults_persistidos_legacy(self):
        self.flujo(False)
        e = EvaluacionConsumo.objects.get(solicitud=self.solicitud)
        e.integraciones_snapshot["historial_raw"] = {"xml_payload": "XML", "source": "stored_xml"}
        h = HistorialPagoConsulta.objects.create(solicitud=self.solicitud, request_payload=self.payload_hdc,
            score_pago=700, mora_maxima=0, categoria="A", xml_payload="XML-SECRETO", response_payload={"score_pago": 700})
        datos, _ = recuperar_externos(self.solicitud, e, historial=h)
        self.assertIsNone(datos.score.valor)
        self.assertIsNone(datos.mora.valor)
        self.assertIsNone(datos.categoria.valor)

    def test_preselecta_raw_persistido_sin_nueva_consulta(self):
        p = PreselectaConsulta.objects.create(solicitud=self.solicitud, request_payload=self.payload_pre,
            response_payload={"raw": {"engineResponse": [{"key": "DECISION", "value": "ZONA_GRIS"}], "score": 600}})
        datos, _ = recuperar_externos(self.solicitud, None, preselecta=p)
        self.assertEqual(datos.decision_preselecta.valor, "ZONA_GRIS")
        self.assertEqual(datos.score_preselecta.valor, Decimal("600"))
        self.pre_mock.return_value.evaluate.assert_not_called()

    def test_formulario_no_inventa_smmlv_ingresos_o_equivalencias(self):
        self.detail.form_data = {"garantia": "Personal", "forma_pago": "Nomina"}
        ctx = formulario_shadow(self.solicitud, self.detail, FECHA_HTTP.date())
        self.assertIsNone(ctx.smmlv.valor)
        self.assertIsNone(ctx.ingresos.valor)
        self.assertIsNone(ctx.garantia)
        self.assertIsNone(ctx.forma_pago)

    def test_snapshot_reemplazado_no_consulta_fuentes(self):
        self.flujo(False)
        with override_settings(CREDIHOY_SHADOW_ENABLED=True):
            ejecutar_shadow_seguro(self.solicitud.pk, evaluacion_actualizada=FECHA_HTTP.replace(year=2020))
        self.http.post.assert_not_called()
        self.assertEqual(self.auditoria().event, "credihoy_shadow_error")

    def test_servicio_shadow_no_puede_guardar_modelos_productivos(self):
        self.flujo(False)
        antes = self.snapshot_productivo()
        with ExitStack() as stack:
            for modelo in (Solicitud, SolicitudConsumo, EvaluacionConsumo, DecisionFinal):
                stack.enter_context(patch.object(modelo, "save", side_effect=AssertionError("Escritura prohibida")))
            r = ServicioEvaluacionCrediHoyShadow(integracion=self.integracion).evaluar(self.solicitud, request_id="REQ")
        self.assertEqual(r.estado_global, EstadoEvaluacion.INFORMACION_INSUFICIENTE)
        self.assertEqual(antes, self.snapshot_productivo())

    def test_usa_formulario_de_evaluacion_no_edicion_posterior(self):
        self.flujo(False)
        SolicitudConsumo.objects.filter(pk=self.detail.pk).update(form_data=self.form | {"monto_solicitado": "999"})
        r = ServicioEvaluacionCrediHoyShadow(integracion=self.integracion).evaluar(self.solicitud, request_id="REQ")
        regla = next(r for r in r.reglas if r.codigo == "CH03_MONTO_MINIMO")
        self.assertEqual(regla.valor_evaluado["monto"], Decimal("1000000"))

    def test_vista_propaga_request_id_sin_cambiar_respuesta(self):
        from apps.xcore_consumo.views import ConsumoProcessView
        productivo, _ = self.flujo(False)
        request = APIRequestFactory().post("/procesar/", {"selected_hc2_keys": []}, format="json",
                                           HTTP_X_REQUEST_ID="REQ-VISTA")
        user = SimpleNamespace(is_authenticated=True, username="test", get_username=lambda: "test",
                               get_full_name=lambda: "Test")
        force_authenticate(request, user=user)
        with patch("apps.xcore_consumo.views.get_detail_or_404", return_value=self.detail), \
             patch("apps.xcore_consumo.views.procesar_consumo", return_value=productivo) as proceso:
            response = ConsumoProcessView.as_view()(request, solicitud_id=self.solicitud.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(proceso.call_args.kwargs["request_id"], "REQ-VISTA")
        self.assertEqual(response.data["decision"]["resultado"], "APROBADO")
