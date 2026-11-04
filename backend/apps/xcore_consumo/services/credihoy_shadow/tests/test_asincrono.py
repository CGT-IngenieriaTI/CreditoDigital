from copy import deepcopy
from unittest.mock import Mock, patch

from celery.exceptions import Retry
from django.test import TestCase, override_settings
import requests

from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo
from apps.xcore_consumo.services import pipeline
from apps.xcore_consumo.tasks import evaluar_credihoy_shadow as task
from apps.xcore_consumo.services.credihoy_integracion.tests.fixtures import cliente_mockeado, FECHA_HTTP
from apps.xcore_consumo.services.credihoy_shadow.asincrono import encolar_shadow, reporte_comparativo
from .test_shadow import ShadowTests, S

A = "apps.xcore_consumo.services.credihoy_shadow.asincrono."


class AsyncTests(TestCase):
    setUp = ShadowTests.setUp
    flujo = ShadowTests.flujo
    snapshot_productivo = ShadowTests.snapshot_productivo
    auditoria = ShadowTests.auditoria

    def preparar(self):
        with override_settings(CREDIHOY_SHADOW_ENABLED=True), patch(A + "current_app.send_task") as broker:
            with self.captureOnCommitCallbacks(execute=True):
                pipeline.procesar_consumo(self.solicitud, actor="test", selected_hc2_keys=[], request_id="REQ-ASYNC")
        broker.assert_called_once()
        self.http.post.assert_not_called()
        return broker.call_args.kwargs["kwargs"]

    def ejecutar(self, datos, retries=0):
        with override_settings(CREDIHOY_SHADOW_ENABLED=True):
            task.push_request(retries=retries)
            try:
                return task.run(**datos)
            finally:
                task.pop_request()

    def test_on_commit_publica_sin_evaluar_http_ni_motor(self):
        with patch(S + "ServicioEvaluacionCrediHoyShadow.evaluar") as motor:
            datos = self.preparar()
        motor.assert_not_called()
        self.assertEqual(set(datos), {"solicitud_id", "request_id", "fecha_evaluacion",
                                     "evaluacion_actualizada", "version_politica"})
        self.assertTrue(all(isinstance(v, str) for v in datos.values()))
        self.assertEqual(self.auditoria().payload["estado_operativo"], "ENCOLADO")

    def test_flag_off_no_publica_y_worker_no_ejecuta(self):
        datos = self.preparar()
        with override_settings(CREDIHOY_SHADOW_ENABLED=False), patch(A + "current_app.send_task") as broker:
            encolar_shadow(**datos)
            self.assertEqual(task.run(**datos), "DESHABILITADO")
        broker.assert_not_called()
        self.http.post.assert_not_called()

    def test_ejecucion_exitosa_insuficiente_y_comparativo(self):
        datos = self.preparar()
        antes = self.snapshot_productivo()
        self.assertEqual(self.ejecutar(datos), "EJECUTADO")
        self.assertEqual(antes, self.snapshot_productivo())
        p = reporte_comparativo(self.solicitud.pk)[0]
        self.assertEqual(p["estado_global"], "INFORMACION_INSUFICIENTE")
        self.assertEqual(p["decision_actual"], "APROBADO")
        self.assertEqual(p["decision_pipeline"], "APROBADO")
        self.assertEqual(p["request_id"], "REQ-ASYNC")
        self.assertFalse(p["autorizado_decision_crediticia"])

    def test_doble_entrega_retry_y_reenqueue_no_duplican_resultado(self):
        datos = self.preparar()
        self.ejecutar(datos)
        original = deepcopy(self.auditoria().payload)
        self.assertEqual(self.ejecutar(datos), "DUPLICADO")
        self.assertEqual(self.ejecutar(datos, retries=1), "DUPLICADO")
        with override_settings(CREDIHOY_SHADOW_ENABLED=True), patch(A + "current_app.send_task") as broker:
            encolar_shadow(**datos)
        broker.assert_not_called()
        self.assertEqual(self.http.post.call_count, 1)
        self.assertEqual(self.auditoria().payload, original)
        self.assertEqual(AuditLog.objects.filter(event="credihoy_shadow_evaluado").count(), 1)

    def test_timeout_retry_acotado_y_error_final(self):
        datos = self.preparar()
        antes = self.snapshot_productivo()
        self.http.post.side_effect = requests.Timeout("TOKEN-PRIVADO")
        for intento in range(3):
            with patch.object(task, "retry", side_effect=Retry()) as retry:
                with self.assertRaises(Retry):
                    self.ejecutar(datos, retries=intento)
            self.assertEqual(retry.call_args.kwargs["countdown"], 10 * 2**intento)
            self.assertFalse(AuditLog.objects.filter(event="credihoy_shadow_error").exists())
        self.assertEqual(self.ejecutar(datos, retries=3), "ERROR")
        p = self.auditoria().payload
        self.assertEqual(p["resultado_operativo"], "SHADOW_ERROR")
        self.assertEqual(p["categoria_error"], "API_DATA_TIMEOUT")
        self.assertEqual(p["intentos"], 4)
        self.assertNotIn("TOKEN-PRIVADO", str(p))
        self.assertEqual(antes, self.snapshot_productivo())

    def test_retry_503_y_500(self):
        datos = self.preparar()
        for status in (503, 500):
            api, _, _ = cliente_mockeado({"codigo": "ERROR_TEMPORAL"}, status=status)
            with self.subTest(status=status), patch(S + "ApiDataCrediHoyClient", return_value=api), \
                    patch.object(task, "retry", side_effect=Retry()) as retry:
                with self.assertRaises(Retry):
                    self.ejecutar(datos)
                retry.assert_called_once()

    def test_red_temporal_reintenta(self):
        datos = self.preparar()
        self.http.post.side_effect = requests.ConnectionError("SECRETO")
        with patch.object(task, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                self.ejecutar(datos)
        self.assertEqual(str(retry.call_args.kwargs["exc"]), "API_DATA_ERROR_TEMPORAL")

    def test_503_sin_envelope_financiero_reintenta(self):
        datos = self.preparar()
        for sin_json in (False, True):
            api, _, response = cliente_mockeado({}, status=503)
            if sin_json:
                response.json.side_effect = ValueError("HTML-PRIVADO")
            with self.subTest(sin_json=sin_json), patch(S + "ApiDataCrediHoyClient", return_value=api), \
                    patch.object(task, "retry", side_effect=Retry()) as retry:
                with self.assertRaises(Retry):
                    self.ejecutar(datos)
                self.assertEqual(str(retry.call_args.kwargs["exc"]), "API_DATA_ERROR_TEMPORAL")

    def test_errores_definitivos_no_reintentan(self):
        datos = self.preparar()
        for status, codigo in ((400, "REQUEST_INVALIDO"), (401, None), (403, None),
                               (404, "ASOCIADO_NO_DISPONIBLE"), (404, "INFORMACION_NO_DISPONIBLE"),
                               (409, "INFORMACION_INCONSISTENTE"), (200, "INVALIDO")):
            with self.subTest(status=status, codigo=codigo):
                AuditLog.objects.filter(event="credihoy_shadow_error").delete()
                api, _, _ = cliente_mockeado({"codigo": codigo}, status=status)
                with patch(S + "ApiDataCrediHoyClient", return_value=api), patch.object(task, "retry") as retry:
                    self.assertEqual(self.ejecutar(datos), "ERROR")
                retry.assert_not_called()
                self.assertEqual(self.auditoria().payload["estado_global"], "INFORMACION_INSUFICIENTE")
                self.assertFalse(self.auditoria().payload["autorizado_decision_crediticia"])

    def test_fallo_broker_no_hace_fallback_y_permite_reencolar(self):
        datos = self.preparar()
        with override_settings(CREDIHOY_SHADOW_ENABLED=True), patch(A + "current_app.send_task", side_effect=OSError("SECRETO")):
            encolar_shadow(**datos)
        self.assertEqual(self.auditoria().event, "credihoy_shadow_enqueue_error")
        self.http.post.assert_not_called()
        with override_settings(CREDIHOY_SHADOW_ENABLED=True), patch(A + "current_app.send_task") as broker:
            encolar_shadow(**datos)
        broker.assert_called_once()

    def test_fecha_original_no_se_reemplaza_por_fecha_worker(self):
        datos = self.preparar()
        with patch(S + "timezone.localtime", return_value=FECHA_HTTP.replace(year=2027)):
            self.ejecutar(datos)
        self.assertEqual(self.http.post.call_args.kwargs["json"]["fechaEvaluacion"], datos["fecha_evaluacion"])

    def test_version_politica_incompatible_no_consulta_http(self):
        datos = self.preparar() | {"version_politica": "OTRA"}
        self.assertEqual(self.ejecutar(datos), "ERROR")
        self.http.post.assert_not_called()

    def test_version_contrato_incompatible_no_retry(self):
        datos = self.preparar()
        respuesta = self.http.post.side_effect
        def version_invalida(*args, **kwargs):
            r = respuesta(*args, **kwargs)
            r.json.return_value["versionContrato"] = "v2"
            return r
        self.http.post.side_effect = version_invalida
        with patch.object(task, "retry") as retry:
            self.assertEqual(self.ejecutar(datos), "ERROR")
        retry.assert_not_called()
        self.assertIn("API_DATA_VERSION_NO_SOPORTADA", self.auditoria().payload["errores_tecnicos"])

    def test_retry_recuperado_deja_un_solo_terminal(self):
        datos = self.preparar()
        post = self.http.post.side_effect
        self.http.post.side_effect = requests.Timeout()
        with patch.object(task, "retry", side_effect=Retry()):
            with self.assertRaises(Retry):
                self.ejecutar(datos)
        self.http.post.side_effect = post
        self.assertEqual(self.ejecutar(datos, retries=1), "EJECUTADO")
        self.assertEqual(self.auditoria().payload["intentos"], 2)
        self.assertFalse(AuditLog.objects.filter(event="credihoy_shadow_error").exists())
        self.assertEqual(self.ejecutar(datos, retries=2), "DUPLICADO")

    def test_bloqueo_postgresql_concurrente_no_hace_http(self):
        datos = self.preparar()
        conexion = Mock(vendor="postgresql")
        cursor = Mock()
        conexion.cursor.return_value.__enter__ = Mock(return_value=cursor)
        conexion.cursor.return_value.__exit__ = Mock(return_value=False)
        cursor.fetchone.return_value = (False,)
        with patch("apps.xcore_consumo.tasks.connection", conexion):
            self.assertEqual(self.ejecutar(datos), "EN_EJECUCION")
        self.assertIn("pg_try_advisory_xact_lock", cursor.execute.call_args.args[0])
        self.http.post.assert_not_called()

    def test_bloqueo_postgresql_adquirido_ejecuta(self):
        datos = self.preparar()
        conexion = Mock(vendor="postgresql")
        cursor = Mock()
        conexion.cursor.return_value.__enter__ = Mock(return_value=cursor)
        conexion.cursor.return_value.__exit__ = Mock(return_value=False)
        cursor.fetchone.return_value = (True,)
        with patch("apps.xcore_consumo.tasks.connection", conexion):
            self.assertEqual(self.ejecutar(datos), "EJECUTADO")
        self.http.post.assert_called_once()

    def test_snapshot_cambiado_durante_http_no_guarda_resultado_obsoleto(self):
        datos = self.preparar()
        post = self.http.post.side_effect
        def actualizar(*args, **kwargs):
            EvaluacionConsumo.objects.filter(solicitud=self.solicitud).update(updated_at=FECHA_HTTP.replace(year=2027))
            return post(*args, **kwargs)
        self.http.post.side_effect = actualizar
        self.assertEqual(self.ejecutar(datos), "ERROR")
        self.assertEqual(self.auditoria().payload["categoria_error"], "SHADOW_SNAPSHOT_REEMPLAZADO")
        self.assertEqual(self.auditoria().payload["reglas"], [])

    def test_persistencia_fallida_reintenta_y_no_inventa_auditoria(self):
        datos = self.preparar()
        with patch("apps.xcore_consumo.tasks.registrar", side_effect=RuntimeError("SECRETO")), \
                patch.object(task, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                self.ejecutar(datos)
            self.assertEqual(self.ejecutar(datos, retries=3), "ERROR_SIN_AUDITORIA")
        retry.assert_called_once()
        self.assertEqual(self.auditoria().payload["estado_operativo"], "ENCOLADO")

    def test_celery_registra_task_en_app_existente(self):
        from core.celery import app
        app.autodiscover_tasks(force=True)
        self.assertIn("credihoy.evaluar_shadow", app.tasks)
        self.assertEqual(task.max_retries, 3)
