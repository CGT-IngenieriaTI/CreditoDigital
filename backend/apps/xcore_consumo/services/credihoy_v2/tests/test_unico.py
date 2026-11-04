"""Modalidad 111 certificada; identidades e importes de prueba sinteticos."""

import ast
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from .. import EstadoCertificacion as EC, EfectoVariable, Hecho, HechosInternosCrediHoyV2
from ..unico import (
    EntradaCredihoyUnicoV2, EstadoCapacidadUnico as Estado, EstadoOperandoUnico as EO,
    EstadoValidacionRecogida as EV, ModalidadCredihoyPermitidaV2, OrigenCuotaRecogida as Fuente,
    RecogidaCredihoyUnicoV2, ReferenciaDocumentoUnicoV2, ValidacionRecogidaUnicoV2,
    CorrespondenciaRecogidaHDCV2,
    construir_capacidad_unico_v2, evaluar_capacidad_unico_v2, inventario_reglas_unico_v2,
)
from .test_capacidad_shadow import internos


def h(valor, fuente="HDC_SINTETICO"):
    return Hecho(valor=valor, disponible=valor is not None, fuente=fuente,
        referencia_evidencia="EVIDENCIA-SINTETICA", ruta_origen="ruta.sintetica",
        estado_certificacion=EC.OBSERVADO if valor is not None else EC.NO_DISPONIBLE)


def politica():
    return ModalidadCredihoyPermitidaV2(codigo_producto="PRODUCTO-SINTETICO",
        codigo_modalidad="111", referencia_certificacion="unico/REGLAS_DURAS.md#equivalencia-linix-certificada")


def recogida():
    obligacion = ReferenciaDocumentoUnicoV2(tipo="OB", numero="OBLIGACION-SINTETICA")
    return RecogidaCredihoyUnicoV2(aplica=True, producto_origen="CREDIHOY",
        codigo_producto="PRODUCTO-SINTETICO", modalidad_codigo="111", linea_codigo="LIV",
        solicitud_origen=ReferenciaDocumentoUnicoV2(tipo="SC", numero="SOLICITUD-SINTETICA"),
        obligacion=obligacion,
        periodicidad_cuota="MENSUAL", origen_cuota=Fuente.LINIX,
        cuota_actual=h(Decimal("180000"), "LINIX-CUOTA-SINTETICA"), saldo_pendiente=h(None, "LINIX"),
        correspondencia_hdc=CorrespondenciaRecogidaHDCV2(
            referencia_agregado_hdc="EVIDENCIA-SINTETICA", obligacion=obligacion,
            cuota_incluida=replace(h(Decimal("180000"), "CORRESPONDENCIA-SINTETICA"),
                                  estado_certificacion=EC.CERTIFICADO)))


def entrada(r=None):
    return EntradaCredihoyUnicoV2(monto_solicitado=Decimal("900000"), plazo_meses=3,
        periodicidad_pago="MENSUAL", codigo_producto="PRODUCTO-SINTETICO",
        modalidad_codigo="111",
        recogida=RecogidaCredihoyUnicoV2(aplica=False) if r is None else r)


def evaluar(e=None, ingresos=Decimal("4000000"), bruto=Decimal("700000"), definicion=None):
    with patch("socket.socket", side_effect=AssertionError("No APIs en dominio puro")):
        return evaluar_capacidad_unico_v2(entrada() if e is None else e,
            ingresos_mensuales=h(ingresos, "LINIX"), cuotas_deudas_hdc_brutas=h(bruto),
            modalidad_permitida=definicion)


class RecogidaUnicoTests(unittest.TestCase):
    def test_sin_recogida_cero_explicito(self):
        r = evaluar()
        self.assertEqual(r.cuota_credihoy_recogido.valor, Decimal("0"))
        self.assertTrue(r.cuota_credihoy_recogido.disponible)
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.NO_APLICA)
        self.assertEqual(r.cuotas_deudas_hdc_aplicables.valor, Decimal("700000"))
        self.assertEqual(r.capacidad_pago.valor, Decimal("2000000"))

    def test_recogida_credihoy_misma_modalidad_matematica_2180000(self):
        r = evaluar(entrada(recogida()), definicion=politica())
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.VALIDA)
        self.assertEqual(r.cuotas_deudas_hdc_aplicables.valor, Decimal("520000"))
        self.assertEqual(r.capacidad_pago.valor, Decimal("2180000"))
        self.assertFalse(r.autorizado_decision_crediticia)

    def test_otros_productos_nunca_validos(self):
        for producto in ("CUPO_ROTATIVO", "CONSUMO", "LIBRE_INVERSION", "OTRO"):
            with self.subTest(producto=producto):
                r = evaluar(entrada(replace(recogida(), producto_origen=producto)), definicion=politica())
                self.assertEqual(r.validacion_recogida.estado_validacion, EV.NO_VALIDA)
                self.assertIsNone(r.capacidad_pago.valor)

    def test_modalidad_distinta_no_valida(self):
        r = evaluar(entrada(replace(recogida(), modalidad_codigo="OTRA")), definicion=politica())
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.NO_VALIDA)

    def test_modalidad_111_recogida_explicita_compatible_sin_override(self):
        r = evaluar(entrada(recogida()))
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.VALIDA)
        self.assertEqual(r.cuota_credihoy_recogido.valor, Decimal("180000"))
        self.assertNotIn("EQUIVALENCIA_PRODUCTO_MODALIDAD_NO_CERTIFICADA", r.validacion_recogida.motivos)

    def comprobar_modalidad_rechazada(self, modalidad, linea=None):
        rec = replace(recogida(), modalidad_codigo=modalidad, linea_codigo=linea)
        r = evaluar(replace(entrada(rec), modalidad_codigo=modalidad))
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.NO_VALIDA)
        self.assertIn("MODALIDAD_RECOGIDA_NO_PERMITIDA", r.validacion_recogida.motivos)
        self.assertIsNone(r.cuota_credihoy_recogido.valor)
        self.assertIsNone(r.capacidad_pago.valor)

    def test_modalidad_112_rechazada(self):
        self.comprobar_modalidad_rechazada("112")

    def test_modalidad_127_rechazada(self):
        self.comprobar_modalidad_rechazada("127")

    def test_modalidad_121_persona_juridica_rechazada(self):
        self.comprobar_modalidad_rechazada("121", "CCF")

    def test_modalidad_150_rechazada(self):
        self.comprobar_modalidad_rechazada("150")

    def test_linea_liv_no_certifica_modalidad(self):
        for modalidad in ("112", "127", "121", "150"):
            with self.subTest(modalidad=modalidad):
                self.comprobar_modalidad_rechazada(modalidad, "LIV")

    def test_modalidad_111_sin_intencion_no_asume_recogida(self):
        r = evaluar(entrada())
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.NO_APLICA)
        self.assertEqual(r.cuota_credihoy_recogido.valor, Decimal("0"))
        self.assertEqual(r.cuotas_deudas_hdc_aplicables.valor, Decimal("700000"))
        r = evaluar(replace(entrada(), recogida=None))
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.PENDIENTE_VALIDACION)
        self.assertIsNone(r.cuota_credihoy_recogido.valor)

    def test_producto_y_linea_son_metadatos_no_discriminantes(self):
        for cambios in (dict(codigo_producto="OTRO", linea_codigo="OTRA"),
                        dict(codigo_producto=None, linea_codigo=None, producto_origen=None)):
            r = evaluar(replace(entrada(replace(recogida(), **cambios)), codigo_producto=None))
            self.assertEqual(r.validacion_recogida.estado_validacion, EV.VALIDA)

    def test_no_admite_override_de_modalidad_certificada(self):
        for modalidad in ("112", "127", "121", "150", "0111", " 111 "):
            with self.subTest(modalidad=modalidad), self.assertRaises(ValueError):
                replace(politica(), codigo_modalidad=modalidad)

    def test_modalidad_ausente_no_se_infiere_desde_linea_o_descripcion(self):
        r = evaluar(entrada(replace(recogida(), modalidad_codigo=None)))
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.PENDIENTE_VALIDACION)
        self.assertIn("MODALIDAD_CODIGO_NO_IDENTIFICADO", r.validacion_recogida.motivos)
        self.assertIsNone(r.cuota_credihoy_recogido.valor)

    def test_modalidad_es_string_exacta_sin_normalizacion(self):
        for modalidad in ("0111", " 111 "):
            with self.subTest(modalidad=modalidad):
                self.comprobar_modalidad_rechazada(modalidad)
        with self.assertRaises(TypeError):
            replace(recogida(), modalidad_codigo=111)

    def test_escenario_ausente_no_es_sin_recogida(self):
        r = evaluar(replace(entrada(), recogida=None))
        self.assertIsNone(r.cuota_credihoy_recogido.valor)
        self.assertEqual(r.estado, Estado.NO_EVALUABLE)

    def test_cuota_recogida_ausente_no_es_saldo(self):
        rec = replace(recogida(), cuota_actual=h(None, "LINIX"), saldo_pendiente=h(Decimal("99999")))
        r = evaluar(entrada(rec), definicion=politica())
        self.assertIsNone(r.cuota_credihoy_recogido.valor)
        self.assertEqual(r.estado, Estado.NO_EVALUABLE)

    def test_no_usa_declaracion_del_asesor_ni_cuota_no_mensual(self):
        for cambios in (dict(origen_cuota=Fuente.DECLARADA_ASESOR), dict(periodicidad_cuota="1"),
                         dict(periodicidad_cuota="9"), dict(cuota_actual=h(Decimal("180000"), None))):
            r = evaluar(entrada(replace(recogida(), **cambios)), definicion=politica())
            self.assertEqual(r.estado, Estado.NO_EVALUABLE)

    def test_identidad_recogida_incompleta_no_valida(self):
        for cambios in (dict(solicitud_origen=None), dict(obligacion=None)):
            r = evaluar(entrada(replace(recogida(), **cambios)), definicion=politica())
            self.assertEqual(r.validacion_recogida.estado_validacion, EV.PENDIENTE_VALIDACION)

    def test_cuota_con_semantica_pendiente_no_se_utiliza(self):
        rec = replace(recogida(), cuota_actual=replace(recogida().cuota_actual,
                                                       estado_certificacion=EC.SEMANTICA_PENDIENTE))
        r = evaluar(entrada(rec), definicion=politica())
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.PENDIENTE_VALIDACION)
        self.assertIsNone(r.cuota_credihoy_recogido.valor)

    def test_cuota_recogida_cero_disponible(self):
        rec = recogida()
        rec = replace(rec, cuota_actual=h(Decimal("0")), correspondencia_hdc=replace(rec.correspondencia_hdc,
            cuota_incluida=replace(rec.correspondencia_hdc.cuota_incluida, valor=Decimal("0"))))
        r = evaluar(entrada(rec), definicion=politica())
        self.assertEqual(r.validacion_recogida.estado_validacion, EV.VALIDA)
        self.assertEqual(r.cuota_credihoy_recogido.valor, Decimal("0"))

    def test_cuota_recogida_mayor_hdc_reporta_inconsistencia_no_clamp(self):
        r = evaluar(entrada(recogida()), bruto=Decimal("100000"), definicion=politica())
        self.assertEqual(r.estado, Estado.INFORMACION_INCONSISTENTE)
        self.assertIn("CUOTA_RECOGIDA_SUPERA_CUOTAS_HDC_BRUTAS", r.inconsistencias)
        self.assertIsNone(r.cuotas_deudas_hdc_aplicables.valor)
        self.assertIsNone(r.capacidad_pago.valor)

    def test_cuota_recogida_igual_hdc_cero_aplicable_explicito(self):
        r = evaluar(entrada(recogida()), bruto=Decimal("180000"), definicion=politica())
        self.assertEqual(r.cuotas_deudas_hdc_aplicables.valor, Decimal("0"))

    def test_sin_recogida_no_admite_seleccion_contradictoria(self):
        with self.assertRaises(ValueError):
            replace(recogida(), aplica=False)


class CapacidadUnicoTests(unittest.TestCase):
    def test_ingreso_disponible_y_ausente(self):
        for ingreso in (Decimal("4000000"), Decimal("0"), None):
            r = evaluar(ingresos=ingreso)
            self.assertEqual(r.ingresos_mensuales.valor, ingreso)
            self.assertEqual(r.estado, Estado.NO_EVALUABLE if ingreso is None else Estado.EVALUABLE)

    def test_gastos_25_por_ciento_truncamiento_hacia_cero(self):
        for ingreso, gastos in (("3419270", "854817"), ("7.999", "1")):
            r = evaluar(ingresos=Decimal(ingreso))
            self.assertEqual(r.gastos_familiares.valor, Decimal(gastos))

    def test_cuota_nueva_por_plazo_no_por_codigo_periodicidad(self):
        for monto, plazo, cuota in (("530000", 3, "176666"), ("530000", 2, "265000"),
                                    ("1090000", 10, "109000"), ("2043000", 12, "170250")):
            r = evaluar(replace(entrada(), monto_solicitado=Decimal(monto), plazo_meses=plazo))
            self.assertEqual(r.cuota_nuevo_credito.valor, Decimal(cuota))

    def test_plazo_fuera_de_rango_no_evaluable(self):
        for plazo in (None, 0, -1, 49):
            r = evaluar(replace(entrada(), plazo_meses=plazo))
            self.assertIsNone(r.cuota_nuevo_credito.valor)
            self.assertEqual(r.estado, Estado.NO_EVALUABLE)

    def test_monto_ausente_cero_negativo_no_evaluable(self):
        for monto in (None, Decimal("0"), Decimal("-1")):
            r = evaluar(replace(entrada(), monto_solicitado=monto))
            self.assertIsNone(r.cuota_nuevo_credito.valor)

    def test_capacidad_negativa_no_es_rechazo(self):
        r = evaluar(ingresos=Decimal("0"))
        self.assertEqual(r.capacidad_pago.valor, Decimal("-1000000"))
        self.assertEqual(r.estado, Estado.EVALUABLE)
        self.assertFalse(r.autorizado_decision_crediticia)

    def test_plazos_extremos_validos(self):
        for plazo in (1, 48):
            self.assertTrue(evaluar(replace(entrada(), plazo_meses=plazo)).cuota_nuevo_credito.disponible)

    def test_periodicidad_no_mensual_no_evaluable(self):
        for periodo in (None, "1", "9", "30", "QUINCENAL", "mensual"):
            self.assertIsNone(evaluar(replace(entrada(), periodicidad_pago=periodo)).cuota_nuevo_credito.valor)

    def test_hdc_pendiente_cero_y_positivo(self):
        for bruto in (None, Decimal("0"), Decimal("700000")):
            r = evaluar(bruto=bruto)
            self.assertEqual(r.cuotas_deudas_hdc_brutas.valor, bruto)
            if bruto is None:
                self.assertEqual(r.cuotas_deudas_hdc_brutas.estado, EO.HOMOLOGACION_HDC_PENDIENTE)
                self.assertIsNone(r.capacidad_pago.valor)
            else:
                self.assertTrue(r.capacidad_pago.disponible)

    def test_hdc_bruto_negativo_inconsistente(self):
        r = evaluar(bruto=Decimal("-1"))
        self.assertEqual(r.estado, Estado.INFORMACION_INCONSISTENTE)
        self.assertIsNone(r.capacidad_pago.valor)

    def test_constructor_siempre_hdc_pendiente_no_suma_linix(self):
        facts = internos(Decimal("4000000"))
        facts = replace(facts, deducciones_linix=h(Decimal("99999")))
        antes = facts.a_dict()
        r = construir_capacidad_unico_v2(entrada(), hechos_internos=facts)
        # A recorded aggregate cannot replace the two supported AP014 components.
        self.assertIsNone(r.ingresos_mensuales.valor)
        self.assertEqual(r.ingresos_mensuales.ruta_origen, 'adaptacionCongente.ingresoComputableAp014')
        self.assertEqual(r.cuotas_deudas_hdc_brutas.estado, EO.HOMOLOGACION_HDC_PENDIENTE)
        self.assertIsNone(r.capacidad_pago.valor)
        self.assertEqual(facts.a_dict(), antes)

    def test_constructor_no_fallback_sueldo_ni_economia(self):
        facts = HechosInternosCrediHoyV2(sueldo_registrado=h(Decimal("99999")),
                                        ingreso_operacional=h(Decimal("88888")))
        r = construir_capacidad_unico_v2(entrada(), hechos_internos=facts)
        self.assertIsNone(r.ingresos_mensuales.valor)
        self.assertIsNone(r.gastos_familiares.valor)

    def test_decimal_precision_global_no_redondea_capacidad(self):
        with localcontext() as ctx:
            ctx.prec = 6
            r = evaluar(ingresos=Decimal("4000000.12345678901234567890"),
                        bruto=Decimal("700000.00000000000000000001"))
            self.assertEqual(ctx.prec, 6)
        self.assertEqual(r.capacidad_pago.valor, Decimal("2000000.12345678901234567889"))

    def test_dto_rechaza_float_bool_no_finitos_y_productos_adicionales(self):
        for changes in (dict(monto_solicitado=0.1), dict(monto_solicitado=Decimal("NaN")),
                        dict(plazo_meses=True), dict(producto="CREDIHOY ADICIONAL")):
            with self.assertRaises((TypeError, ValueError)):
                replace(entrada(), **changes)
        with self.assertRaises(TypeError):
            replace(recogida(), cuota_actual=h(0.1))

    def test_inmutabilidad_y_no_decision_crediticia(self):
        r = evaluar()
        self.assertEqual(r.modelo, "CREDIHOY_UNICO_REGLAS_DURAS")
        self.assertFalse(r.autorizado_decision_crediticia)
        self.assertEqual(r.conclusion_funcional, "NO_DETERMINADA")
        with self.assertRaises(FrozenInstanceError):
            r.autorizado_decision_crediticia = True
        self.assertEqual(json.loads(json.dumps(r.a_dict()))["capacidad_pago"]["valor"], "2000000")
        for nombre in ("deducciones_dc", "deducciones_internas", "cuota_credito_recogida_declarada"):
            self.assertNotIn(nombre, r.a_dict())


class InventarioYSeparacionTests(unittest.TestCase):
    def test_inventario_exclusivo_unico_estados_y_efectos(self):
        reglas = inventario_reglas_unico_v2()
        self.assertEqual(len(reglas), 16)
        self.assertEqual(sum(r.activa for r in reglas), 15)
        inactiva = next(r for r in reglas if r.fila == 30)
        self.assertFalse(inactiva.activa)
        with self.assertRaises(ValueError):
            replace(inactiva, efecto=EfectoVariable.BLOQUEA)
        self.assertTrue(all(r.producto == "CREDIHOY UNICO (NUEVO)" for r in reglas))
        self.assertTrue(all(r.efecto == EfectoVariable.PENDIENTE_DEFINICION for r in reglas))
        self.assertFalse(any("MORA EXTERNA" in r.nombre for r in reglas))

    def test_inventario_preserva_texto_excel_sin_sustituir_por_adaptacion(self):
        capacidad = next(r for r in inventario_reglas_unico_v2() if r.fila == 2)
        self.assertIn("excepto los de congente", capacidad.observacion)
        self.assertIn("LINIX", capacidad.observacion)
        self.assertIn("Periodicidad", capacidad.observacion)
        self.assertEqual(capacidad.condicion_original, "0")

    def test_normativo_no_importa_legacy_y_legacy_no_se_exporta_en_raiz(self):
        import apps.xcore_consumo.services.credihoy_v2 as paquete
        self.assertFalse(hasattr(paquete, "construir_capacidad_shadow_v2"))
        self.assertFalse(hasattr(paquete, "parametros_observados_curuba"))
        raiz = Path(__file__).parents[1] / "unico"
        for p in raiz.glob("*.py"):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("legacy", node.module or "")
                    self.assertNotIn("curuba", node.module or "")
        from ..legacy_curuba import MODELO
        self.assertEqual(MODELO, "LEGACY_SHADOW_CURUBA")
