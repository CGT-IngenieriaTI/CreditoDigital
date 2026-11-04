from copy import deepcopy
from datetime import date
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from apps.decisiones.models import DecisionFinal
from apps.historial_pago.models import HistorialPagoConsulta
from apps.preselecta.models import PreselectaConsulta
from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from apps.xcore_consumo.services.credihoy_shadow.importador import exigir_local, validar_archivos
from apps.xcore_consumo.services.credihoy_shadow.evidencia import recuperar_externos
from apps.xcore_consumo.services.credihoy_shadow.piloto import preflight, reporte_piloto, autorizar_y_encolar
from apps.xcore_consumo.services.credihoy_integracion.tests.fixtures import cliente_mockeado
from apps.xcore_consumo.tasks import evaluar_credihoy_shadow as task

A = "apps.xcore_consumo.services.credihoy_shadow.asincrono.current_app.send_task"
S = "apps.xcore_consumo.services.credihoy_shadow.servicio.ApiDataCrediHoyClient"


@override_settings(DEBUG=True, CREDIHOY_SHADOW_ENABLED=False, API_DATA_CREDIHOY_BASE_URL="https://api.invalid",
                   API_DATA_CREDIHOY_TOKEN="TOKEN-SINTETICO", API_DATA_CREDIHOY_TIMEOUT="2")
class ImportadorTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p = Path(self.temp.name) / "p.json"
        self.h = Path(self.temp.name) / "h.json"
        self.pre = {"schema": "credihoy-shadow-import-preselecta-v1", "fecha_consulta": "2024-05-08T12:20:30",
            "request_payload": {"idType": "1", "idNumber": "1234567890", "firstLastName": "PRUEBA"},
            "response_payload": {"raw": {"score": "700", "engineResponse": [
                {"key": "DECISION", "value": "APROBADO"}, {"key": "RIESGO_SCORE", "value": "Bajo"},
                {"key": "SCORE", "value": "700"}]}}}
        self.hdc = {"schema": "credihoy-shadow-import-hdc-v1", "fecha_consulta": "2024-05-09T12:21:00-05:00",
            "historial_payload": {"tipo_identificacion": "1", "numero_identificacion": "1234567890", "primer_apellido": "PRUEBA"},
            "historial_raw": {"score_pago": None, "mora_maxima": None, "categoria": None, "embargo": None,
                              "experiencia": None, "obligaciones": ["CuentaCartera-LOCAL-1"]}}
        self.guardar()
        for target in ("socket.socket", "requests.sessions.Session.request",
                       "apps.preselecta.client.PreselectaClient.evaluate",
                       "apps.historial_pago.client.HistorialPagoSOAPClient.consult"):
            p = patch(target, side_effect=AssertionError("Red/proveedor prohibido"))
            p.start()
            self.addCleanup(p.stop)
        self.broker = patch(A)
        self.publicar = self.broker.start()
        self.addCleanup(self.broker.stop)

    def guardar(self):
        self.p.write_text(json.dumps(self.pre), encoding="utf-8")
        self.h.write_text(json.dumps(self.hdc), encoding="utf-8")

    def comando(self, **kwargs):
        salida = StringIO()
        call_command("credihoy_shadow_import_local", preselecta=str(self.p), hdc=str(self.h), stdout=salida, **kwargs)
        return json.loads(salida.getvalue())

    def persistir(self):
        return self.comando(persistir=True, fecha_expedicion=date(2001, 1, 1))

    def test_dry_run_sin_queries_ni_publicacion(self):
        with self.assertNumQueries(0):
            r = self.comando()
        self.assertEqual(r["modo"], "DRY_RUN")
        self.assertTrue(all(r["PRESELECTA"]["disponibilidad"].values()))
        self.assertEqual(r["HDC"]["disponibilidad"], dict(score=False, mora=False, categoria=False,
                         embargo=False, experiencia=False, obligaciones=True))
        self.assertNotIn("1234567890", json.dumps(r))
        self.publicar.assert_not_called()

    def test_persistencia_crea_solo_registros_de_evidencia(self):
        r = self.persistir()
        self.assertEqual(r["modo"], "PERSISTIDO_LOCAL")
        for modelo in (Solicitante, Solicitud, SolicitudConsumo, PreselectaConsulta, EvaluacionConsumo):
            self.assertEqual(modelo.objects.count(), 1)
        for modelo in (DecisionFinal, HistorialPagoConsulta, AuditLog):
            self.assertEqual(modelo.objects.count(), 0)
        s = Solicitud.objects.get()
        self.assertEqual(s.producto, "CONSUMO")
        self.assertEqual(s.estado, "INICIADA")
        self.publicar.assert_not_called()

    def test_debug_false_impide_persistir_pero_no_dry_run(self):
        with override_settings(DEBUG=False):
            self.comando()
            with self.assertRaisesMessage(CommandError, "REQUIERE_DEBUG_Y_SQLITE_LOCAL"):
                self.persistir()
        self.assertFalse(Solicitud.objects.exists())

    def test_postgres_y_sqlite_remoto_rechazados(self):
        for cambio in ({"ENGINE": "django.db.backends.postgresql"}, {"NAME": "//host/recurso/db.sqlite3"},
                       {"NAME": "file://host/db"}, {"HOST": "servidor"}):
            with self.subTest(cambio=cambio), patch.dict(connection.settings_dict, cambio):
                with self.assertRaisesMessage(ValueError, "REQUIERE_DEBUG_Y_SQLITE_LOCAL"):
                    exigir_local()

    def test_fecha_expedicion_no_se_fabrica(self):
        with self.assertRaisesMessage(CommandError, "FECHA_EXPEDICION_REAL_REQUERIDA"):
            self.comando(persistir=True)
        self.assertFalse(Solicitante.objects.exists())

    def test_reutiliza_persona_sin_modificar_historia(self):
        self.persistir()
        persona_antes = Solicitante.objects.values().get()
        solicitud_antes = Solicitud.objects.values().get()
        self.comando(persistir=True)
        self.assertEqual(Solicitud.objects.count(), 2)
        self.assertEqual(persona_antes, Solicitante.objects.values().get())
        self.assertEqual(solicitud_antes, Solicitud.objects.values().get(pk=solicitud_antes["id"]))

    def test_identidad_incoherente_aborta_sin_escrituras(self):
        self.hdc["historial_payload"]["numero_identificacion"] = "999"
        self.guardar()
        with self.assertRaisesMessage(CommandError, "IDENTIDADES_INCONSISTENTES"):
            self.persistir()
        self.assertFalse(Solicitud.objects.exists())

    def test_rechaza_demo(self):
        self.pre["request_payload"]["provider_mode"] = "test"
        self.guardar()
        with self.assertRaisesMessage(CommandError, "IDENTIDADES_INCONSISTENTES_O_DEMO"):
            self.comando()

    def test_rechaza_schema_y_valores_incompatibles(self):
        original = deepcopy(self.hdc)
        for cambios in ({"schema": "otro"}, {"historial_raw": original["historial_raw"] | {"embargo": "N"}},
                        {"historial_raw": original["historial_raw"] | {"metrics": {}}}):
            self.hdc = original | cambios
            self.guardar()
            with self.assertRaises(CommandError):
                self.comando()

    def test_no_guarda_xml_secretos_ni_respuesta_completa(self):
        self.pre["response_payload"]["raw"]["soap_request"] = "SECRETO-NO-PERSISTIR"
        self.pre["request_payload"]["password"] = "SECRETO-NO-PERSISTIR"
        self.guardar()
        self.persistir()
        for modelo in (PreselectaConsulta, EvaluacionConsumo, Solicitud):
            self.assertNotIn("SECRETO-NO-PERSISTIR", str(list(modelo.objects.values())))
        self.assertFalse(AuditLog.objects.exists())

    def test_rechaza_fuente_dentro_repo(self):
        # Archivo existente de codigo, rechazado por ubicacion antes de interpretar JSON.
        with self.assertRaisesMessage(ValueError, "EVIDENCIA_DENTRO_DE_REPOSITORIO"):
            validar_archivos(__file__, self.h)

    def test_fechas_reales_no_updated_at_y_nulls_preservados(self):
        self.persistir()
        s = Solicitud.objects.get()
        e = EvaluacionConsumo.objects.get()
        datos, errores = recuperar_externos(s, e, preselecta=PreselectaConsulta.objects.get())
        self.assertFalse(errores)
        self.assertEqual(datos.score_preselecta.fecha_corte, date(2024, 5, 8))
        self.assertEqual(datos.obligaciones.fecha_corte, date(2024, 5, 9))
        for campo in ("score", "mora", "categoria", "embargo", "experiencia"):
            self.assertIsNone(getattr(datos, campo).valor)
            self.assertFalse(getattr(datos, campo).disponible)
        self.assertEqual(e.integraciones_snapshot["shadow_import"]["fechas_consulta"]["PRESELECTA"], self.pre["fecha_consulta"])

    def test_importacion_sin_fecha_no_recurre_a_updated_at(self):
        self.persistir()
        e = EvaluacionConsumo.objects.get()
        e.integraciones_snapshot["shadow_import"]["fechas_consulta"].pop("HDC")
        with self.assertRaisesMessage(ValueError, "FECHA_EVIDENCIA_INVALIDA"):
            recuperar_externos(Solicitud.objects.get(), e)

    def test_comparativo_no_relajado_tecnico_si_apto(self):
        r = self.persistir()
        self.assertIn("DECISION_FINAL_AUSENTE", preflight(r["solicitud_id"])["errores"])
        self.assertEqual(preflight(r["solicitud_id"], tipo_piloto="PILOTO_TECNICO")["estado"], "PILOTO_APTO")
        with override_settings(DEBUG=False):
            self.assertEqual(preflight(r["solicitud_id"], tipo_piloto="PILOTO_TECNICO")["estado"], "PILOTO_NO_APTO")

    def test_tecnico_comando_solo_preflight_sin_autorizacion(self):
        r = self.persistir()
        call_command("credihoy_shadow_pilot", solicitud=r["solicitud_id"], tipo_piloto="PILOTO_TECNICO", stdout=StringIO())
        self.publicar.assert_not_called()
        self.assertFalse(AuditLog.objects.exists())

    def test_tecnico_task_mockeada_sin_decision_y_con_autorizacion(self):
        r = self.persistir()
        previo = preflight(r["solicitud_id"], tipo_piloto="PILOTO_TECNICO")
        seguimiento = autorizar_y_encolar(previo, "REQ-TECNICO")
        datos = self.publicar.call_args.kwargs["kwargs"]
        api, http, respuesta = cliente_mockeado()
        respuesta.json.return_value["fechaEvaluacion"] = datos["fecha_evaluacion"]
        with patch(S, return_value=api):
            self.assertEqual(task.run(**datos), "EJECUTADO")
        self.assertFalse(DecisionFinal.objects.exists())
        reporte = reporte_piloto(r["solicitud_id"], seguimiento["request_id"])
        self.assertEqual(reporte["decision_productiva_baseline"], "NO_DISPONIBLE")
        self.assertEqual(reporte["comparacion_productiva"], "NO_APLICA")
        self.assertFalse(reporte["autorizado_decision_crediticia"])
        self.assertEqual(task.run(**datos), "DUPLICADO")
        self.assertEqual(http.post.call_count, 1)
        with override_settings(DEBUG=False):
            self.assertEqual(task.run(**datos), "DESAUTORIZADO")
        sin_permiso = datos | {"piloto_id": None}
        self.assertEqual(task.run(**sin_permiso), "DESAUTORIZADO")

    def test_fechas_invalidas_y_score_contradictorio(self):
        self.pre["fecha_consulta"] = "ayer"
        self.guardar()
        with self.assertRaisesMessage(CommandError, "FECHA_EVIDENCIA_INVALIDA"):
            self.comando()
        self.pre["fecha_consulta"] = "2024-05-08T12:20:30"
        self.pre["response_payload"]["raw"]["score"] = "701"
        self.guardar()
        with self.assertRaisesMessage(CommandError, "PRESELECTA_NO_IDENTIFICABLE"):
            self.comando()

    def test_no_persiste_con_hook_global_activo(self):
        with override_settings(CREDIHOY_SHADOW_ENABLED=True):
            with self.assertRaisesMessage(CommandError, "FLAG_GLOBAL_DEBE_SER_FALSE"):
                self.persistir()
        self.assertFalse(Solicitud.objects.exists())

    def test_permiso_tecnico_no_se_reutiliza_como_comparativo(self):
        r = self.persistir()
        autorizar_y_encolar(preflight(r["solicitud_id"], tipo_piloto="PILOTO_TECNICO"), "REQ")
        datos = self.publicar.call_args.kwargs["kwargs"]
        self.assertEqual(task.run(**(datos | {"tipo_piloto": "PILOTO_COMPARATIVO"})), "DESAUTORIZADO")

    def test_fallo_transaccion_no_deja_importacion_parcial(self):
        with patch.object(PreselectaConsulta.objects, "create", side_effect=ValueError("FALLO_SINTETICO")):
            with self.assertRaises(CommandError):
                self.persistir()
        for modelo in (Solicitante, Solicitud, SolicitudConsumo, EvaluacionConsumo):
            self.assertFalse(modelo.objects.exists())
