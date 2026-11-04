from io import StringIO
import json
from unittest.mock import patch
from uuid import UUID, uuid4

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
import requests

from apps.decisiones.models import DecisionFinal
from apps.preselecta.models import PreselectaConsulta
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from apps.xcore_consumo.tasks import evaluar_credihoy_shadow as task
from apps.xcore_consumo.services.credihoy_shadow.piloto import (
    AUTORIZACION, capturar_invariantes, preflight, reporte_piloto,
)
from .test_shadow import ShadowTests, S

A = "apps.xcore_consumo.services.credihoy_shadow.asincrono."


class PilotoTests(TestCase):
    flujo = ShadowTests.flujo
    snapshot_productivo = ShadowTests.snapshot_productivo

    def setUp(self):
        ShadowTests.setUp(self)
        self.flujo(False)
        self.pre_mock.reset_mock()
        self.hdc_mock.reset_mock()
        self.familiar_mock.reset_mock()

    def comando(self, **opciones):
        out = StringIO()
        call_command("credihoy_shadow_pilot", solicitud=str(self.solicitud.pk), stdout=out, **opciones)
        return json.loads(out.getvalue())

    def encolar(self, request_id="REQ-PILOTO"):
        with patch(A + "current_app.send_task") as publicar:
            respuesta = self.comando(autorizar_ejecucion=True, request_id=request_id)
        publicar.assert_called_once()
        self.http.post.assert_not_called()
        return respuesta, publicar.call_args.kwargs["kwargs"]

    def ejecutar(self, datos, retries=0):
        task.push_request(retries=retries)
        try:
            return task.run(**datos)
        finally:
            task.pop_request()

    def cambiar_snapshot(self, **cambios):
        e = EvaluacionConsumo.objects.get(solicitud=self.solicitud)
        e.integraciones_snapshot.update(cambios)
        e.save(update_fields=["integraciones_snapshot"])

    def test_comando_default_solo_preflight_sin_broker_http_ni_escrituras(self):
        antes = capturar_invariantes(self.solicitud.pk)
        count = AuditLog.objects.count()
        with patch(A + "current_app.send_task") as publicar:
            resultado = self.comando()
        self.assertEqual(resultado["estado"], "PILOTO_APTO")
        self.assertFalse(resultado["ejecucion_autorizada"])
        publicar.assert_not_called()
        self.http.post.assert_not_called()
        self.assertEqual(AuditLog.objects.count(), count)
        self.assertEqual(capturar_invariantes(self.solicitud.pk), antes)

    def test_solicitud_inexistente(self):
        with self.assertRaisesMessage(CommandError, "SOLICITUD_NO_EXISTE"):
            call_command("credihoy_shadow_pilot", solicitud=str(uuid4()), stdout=StringIO())

    def test_solicitud_id_invalido(self):
        self.assertEqual(preflight("no-es-uuid")["estado"], "PILOTO_NO_APTO")

    def test_falta_solicitud_consumo(self):
        SolicitudConsumo.objects.filter(solicitud=self.solicitud).delete()
        self.assertIn("SOLICITUD_CONSUMO_AUSENTE", preflight(self.solicitud.pk)["errores"])

    def test_falta_decision(self):
        DecisionFinal.objects.filter(solicitud=self.solicitud).delete()
        self.assertIn("DECISION_FINAL_AUSENTE", preflight(self.solicitud.pk)["errores"])

    def test_falta_evaluacion(self):
        EvaluacionConsumo.objects.filter(solicitud=self.solicitud).delete()
        self.assertIn("EVALUACION_CONSUMO_AUSENTE", preflight(self.solicitud.pk)["errores"])

    def test_falta_preselecta_no_encola(self):
        self.cambiar_snapshot(preselecta_snapshot={})
        with patch(A + "current_app.send_task") as publicar, self.assertRaisesMessage(CommandError, "PRESELECTA_NO_IDENTIFICABLE"):
            self.comando(autorizar_ejecucion=True)
        publicar.assert_not_called()

    def test_falta_hdc_no_encola(self):
        self.cambiar_snapshot(historial_raw={})
        with patch(A + "current_app.send_task") as publicar, self.assertRaisesMessage(CommandError, "HDC_NO_IDENTIFICABLE"):
            self.comando(autorizar_ejecucion=True)
        publicar.assert_not_called()

    def test_hdc_legacy_no_se_completa_con_defaults(self):
        self.cambiar_snapshot(historial_raw={"source": "stored_xml", "xml": "SECRETO", "score_pago": 700})
        self.assertIn("HDC_NO_IDENTIFICABLE", preflight(self.solicitud.pk)["errores"])

    def test_preselecta_raw_persistido_es_apto(self):
        self.cambiar_snapshot(preselecta_snapshot={})
        PreselectaConsulta.objects.create(solicitud=self.solicitud, request_payload=self.payload_pre,
            response_payload={"raw": {"engineResponse": [{"key": "DECISION", "value": "APROBADO"}]}})
        self.assertEqual(preflight(self.solicitud.pk)["estado"], "PILOTO_APTO")

    def test_identidad_demo_o_distinta_no_apta(self):
        for cambios in ({"numero_identificacion": "999"}, {"provider_test_identity": True}):
            with self.subTest(cambios=cambios):
                self.cambiar_snapshot(historial_payload=self.payload_hdc | cambios)
                self.assertIn("IDENTIDAD_HDC_INCONSISTENTE", preflight(self.solicitud.pk)["errores"])

    def test_documento_ausente_o_no_ascii(self):
        persona = self.solicitud.solicitante
        for documento in ("", "\u0661\u0662"):
            persona.numero_identificacion = documento
            persona.save(update_fields=["numero_identificacion"])
            self.assertIn("DOCUMENTO_NO_DISPONIBLE_O_INVALIDO", preflight(self.solicitud.pk)["errores"])

    def test_api_data_sin_configuracion_no_go_sin_imprimir_secreto(self):
        with patch(S + "ConfiguracionApiData.desde_settings", side_effect=ValueError("TOKEN-SECRETO")):
            with self.assertRaisesMessage(CommandError, "CONFIGURACION_API_DATA_INCOMPLETA") as captura:
                self.comando(autorizar_ejecucion=True)
        self.assertNotIn("TOKEN-SECRETO", str(captura.exception))

    def test_flag_global_true_es_no_go(self):
        with override_settings(CREDIHOY_SHADOW_ENABLED=True):
            with self.assertRaisesMessage(CommandError, "FLAG_GLOBAL_DEBE_SER_FALSE"):
                self.comando(autorizar_ejecucion=True)

    def test_encola_task_existente_con_payload_minimo_y_permiso_puntual(self):
        respuesta, datos = self.encolar()
        self.assertEqual(respuesta["estado"], "ENCOLADO")
        self.assertEqual(set(datos), {"solicitud_id", "request_id", "fecha_evaluacion",
                                     "evaluacion_actualizada", "version_politica", "piloto_id"})
        self.assertTrue(all(isinstance(v, str) for v in datos.values()))
        self.assertFalse(settings.CREDIHOY_SHADOW_ENABLED)
        self.assertTrue(AuditLog.objects.filter(pk=datos["piloto_id"], event=AUTORIZACION).exists())
        self.assertEqual(respuesta["request_id"], datos["request_id"])

    def test_correlation_hasta_http_y_audit_con_global_off_sin_datacredito(self):
        respuesta, datos = self.encolar("P" * 128)
        antes = capturar_invariantes(self.solicitud.pk)
        with patch("apps.preselecta.client.PreselectaClient.evaluate") as pre, \
                patch("apps.historial_pago.client.HistorialPagoSOAPClient.consult") as hdc:
            self.assertEqual(self.ejecutar(datos), "EJECUTADO")
        pre.assert_not_called()
        hdc.assert_not_called()
        self.pre_mock.assert_not_called()
        self.hdc_mock.assert_not_called()
        self.familiar_mock.assert_not_called()
        self.assertEqual(antes, capturar_invariantes(self.solicitud.pk))
        self.assertEqual(self.http.post.call_args.kwargs["headers"]["X-Request-ID"], respuesta["request_id"])
        self.assertFalse(settings.CREDIHOY_SHADOW_ENABLED)
        reporte = self.comando(reporte=True, request_id=respuesta["request_id"])
        self.assertFalse(reporte["alteracion_productiva"])
        self.assertEqual(reporte["invariantes"]["antes"], reporte["invariantes"]["despues"])
        self.assertIsNone(reporte["invariantes"]["antes"]["pdf"]["sha256"])
        self.assertEqual(reporte["evidencia"]["C4"]["saldoIndividualObservado"], "424141.00")
        self.assertFalse(reporte["evidencia"]["C4"]["utilizableDecision"])
        self.assertEqual(reporte["evidencia"]["C3"], {"estado": "NO_CERTIFICADO", "disponible": False})
        self.assertEqual(reporte["estado_global"], "INFORMACION_INSUFICIENTE")
        self.assertIn("REVISION_MANUAL_CAPACIDAD", reporte["motivos"])
        self.assertFalse(reporte["autorizado_decision_crediticia"])
        for secreto in ("XML-NO-DUPLICAR", "TOKEN-SINTETICO", "1234567890", "REF-SINTETICA-1"):
            self.assertNotIn(secreto, json.dumps(reporte))

    def test_mensaje_sin_autorizacion_no_puede_saltar_flag(self):
        _, datos = self.encolar()
        for cambio in ({"piloto_id": "99999999"}, {"request_id": "OTRO"}, {"version_politica": "OTRA"}):
            self.assertEqual(self.ejecutar(datos | cambio), "DESAUTORIZADO")
        datos.pop("piloto_id")
        self.assertEqual(self.ejecutar(datos), "DESHABILITADO")
        self.http.post.assert_not_called()

    def test_segundo_comando_y_redelivery_no_duplican_evaluacion(self):
        respuesta, datos = self.encolar()
        self.ejecutar(datos)
        with patch(A + "current_app.send_task") as broker:
            repetido = self.comando(autorizar_ejecucion=True, request_id="OTRO-ID")
        broker.assert_not_called()
        self.assertEqual(repetido["estado"], "DUPLICADO")
        self.assertEqual(repetido["request_id"], respuesta["request_id"])
        self.assertEqual(self.ejecutar(datos), "DUPLICADO")
        self.assertEqual(self.http.post.call_count, 1)
        self.assertEqual(AuditLog.objects.filter(event="credihoy_shadow_evaluado").count(), 1)

    def test_detecta_edicion_antes_del_worker_sin_consultar(self):
        _, datos = self.encolar()
        DecisionFinal.objects.filter(solicitud=self.solicitud).update(tasa_interes="13.00")
        self.assertEqual(self.ejecutar(datos), "ERROR")
        self.http.post.assert_not_called()
        r = reporte_piloto(self.solicitud.pk, datos["request_id"])
        self.assertTrue(r["alteracion_productiva"])
        self.assertEqual(r["categoria_error"], "PILOTO_NO_APTO_AL_EJECUTAR")

    def test_detecta_edicion_durante_http_no_declara_falso_invariante(self):
        _, datos = self.encolar()
        post = self.http.post.side_effect
        def editar(*args, **kwargs):
            DecisionFinal.objects.filter(solicitud=self.solicitud).update(monto_aprobado="1")
            return post(*args, **kwargs)
        self.http.post.side_effect = editar
        self.assertEqual(self.ejecutar(datos), "ERROR")
        self.assertTrue(reporte_piloto(self.solicitud.pk, datos["request_id"])["alteracion_productiva"])

    def test_reporte_pendiente_no_fabrica_verificacion(self):
        respuesta, _ = self.encolar()
        self.assertIsNone(reporte_piloto(self.solicitud.pk, respuesta["request_id"])["alteracion_productiva"])
        self.http.post.assert_not_called()

    def test_broker_fallido_se_informa_sin_fallback(self):
        with patch(A + "current_app.send_task", side_effect=OSError("SECRETO")):
            with self.assertRaisesMessage(CommandError, "PILOTO_NO_ENCOLADO"):
                self.comando(autorizar_ejecucion=True)
        self.http.post.assert_not_called()

    def test_timeout_agotado_preserva_invariantes(self):
        _, datos = self.encolar()
        self.http.post.side_effect = requests.Timeout("TOKEN-SECRETO")
        self.assertEqual(self.ejecutar(datos, retries=3), "ERROR")
        r = reporte_piloto(self.solicitud.pk, datos["request_id"])
        self.assertFalse(r["alteracion_productiva"])
        self.assertIsNone(r["evidencia"]["C4"]["saldoIndividualObservado"])
        self.assertNotIn("TOKEN-SECRETO", str(r))

    def test_request_id_autogenerado_y_request_id_invalido(self):
        with patch(A + "current_app.send_task"):
            salida = self.comando(autorizar_ejecucion=True)
        self.assertEqual(str(UUID(salida["request_id"])), salida["request_id"])
        with self.assertRaisesMessage(CommandError, "REQUEST_ID_INVALIDO"):
            self.comando(autorizar_ejecucion=True, request_id="no permitido\n")

    def test_encolar_no_habilita_hook_para_otras_solicitudes(self):
        from apps.xcore_consumo.services.credihoy_shadow.servicio import programar_shadow
        self.encolar()
        with patch(A + "current_app.send_task") as broker, self.captureOnCommitCallbacks(execute=True) as callbacks:
            programar_shadow(self.solicitud.pk)
        self.assertEqual(callbacks, [])
        broker.assert_not_called()

    def test_evidencia_desaparece_antes_de_consumo_bloquea_api(self):
        _, datos = self.encolar()
        self.cambiar_snapshot(historial_raw={})
        self.assertEqual(self.ejecutar(datos), "ERROR")
        self.http.post.assert_not_called()

    def test_reporte_no_encola_ni_consulta_fuentes(self):
        salida, datos = self.encolar()
        self.ejecutar(datos)
        self.http.post.reset_mock()
        with patch(A + "current_app.send_task") as broker:
            self.comando(reporte=True, request_id=salida["request_id"])
        broker.assert_not_called()
        self.http.post.assert_not_called()

    def test_worker_no_guarda_modelos_productivos_ni_genera_pdf(self):
        from contextlib import ExitStack
        from apps.solicitudes.models import Solicitud
        _, datos = self.encolar()
        with ExitStack() as stack:
            for modelo in (Solicitud, SolicitudConsumo, DecisionFinal, EvaluacionConsumo):
                stack.enter_context(patch.object(modelo, "save", side_effect=AssertionError("Escritura prohibida")))
            pdf = stack.enter_context(patch("apps.xcore_consumo.services.calculadora.build_consumo_decision_pdf"))
            self.assertEqual(self.ejecutar(datos), "EJECUTADO")
        pdf.assert_not_called()
