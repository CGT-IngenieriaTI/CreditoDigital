from pathlib import Path

from django.test import SimpleTestCase

from apps.historial_pago.client import HistorialPagoSOAPClient
from apps.historial_pago.extractor import _coerce_xml_string, _parse_root, _to_pesos, extract_financial_metrics


class HistorialPagoXmlParsingTests(SimpleTestCase):
    def test_coerce_xml_string_prioriza_informes(self):
        raw = "ï»¿ basura antes &lt;Informes&gt;<Informe /></Informes>"
        payload = _coerce_xml_string(raw)
        self.assertTrue(payload.startswith("<Informes"))

    def test_parse_root_recovers_invalid_ampersand(self):
        xml = "<Informes><Informe><Texto>A & B</Texto></Informe></Informes>"
        root = _parse_root(xml)
        self.assertEqual(root.tag, "Informes")

    def test_extract_xml_from_response_uses_return_nodes(self):
        soap = """
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
          <soapenv:Body>
            <consultarHC2Response>
              <consultarHC2Return>&lt;Informes&gt;&lt;Informe /&gt;&lt;/Informes&gt;</consultarHC2Return>
            </consultarHC2Response>
          </soapenv:Body>
        </soapenv:Envelope>
        """
        client = HistorialPagoSOAPClient.__new__(HistorialPagoSOAPClient)
        extracted = client._extract_xml_from_response(soap)
        self.assertTrue(extracted.startswith("<Informes"))


    def test_to_pesos_supports_hc2_thousands_and_already_pesos(self):
        self.assertEqual(_to_pesos("1027.0", 1000), 1027000)
        self.assertEqual(_to_pesos("1027000", 1000), 1027000)
        self.assertEqual(_to_pesos("1.027.000", 1000), 1027000)
        self.assertEqual(_to_pesos("56,000", 1000), 56000)
        self.assertEqual(_to_pesos("292.008", 1000), 292008)
        self.assertEqual(_to_pesos("4.674", 1000, aggregate=True), 4674000)

    def test_tmp_sample_credit_metrics_match_expected_values(self):
        xml_path = Path(__file__).resolve().parent / "fixtures" / "tmp_sample_credit.xml"
        metrics = extract_financial_metrics(xml_path.read_text(encoding="utf-8"))
        self.assertEqual(metrics["metrics"]["valor_pasivos"], 37696000)
        self.assertEqual(metrics["metrics"]["saldo_total_creditos"], 32007000)
        self.assertEqual(metrics["metrics"]["saldo_total_creditos_deudor_principal"], 27311000)
        self.assertEqual(metrics["metrics"]["saldo_abierto_codeudor"], 4696000)
        self.assertEqual(metrics["metrics"]["cupos_tarjetas_rotativos"], 5197000)
        self.assertEqual(metrics["metrics"]["total_cuotas_credito"], 1980000)
        self.assertEqual(metrics["metrics"]["total_cuotas_credito_deudor_principal"], 1507000)
        self.assertEqual(metrics["metrics"]["cuotas_creditos_codeudor"], 473000)

    def test_extract_financial_metrics_exposes_principal_and_codeudor_readings(self):
        xml = """
        <Informes>
          <Informe>
            <CuentaCartera entidad="BCO TEST" numero="1" sector="1" calidadDeudor="Principal" estadoActual="Al dia">
              <Caracteristicas tipoCuenta="CAB" />
              <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
              <Valores><Valor saldoActual="1000.0" cuota="100.0" valorInicial="1000.0" /></Valores>
            </CuentaCartera>
            <CuentaCartera entidad="BCO TEST" numero="2" sector="1" calidadDeudor="Codeudor" estadoActual="Al dia">
              <Caracteristicas tipoCuenta="MCR" />
              <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
              <Valores><Valor saldoActual="2000.0" cuota="200.0" valorInicial="2000.0" /></Valores>
            </CuentaCartera>
            <TarjetaCredito entidad="TDC TEST" numero="3" sector="1" calidadDeudor="Principal">
              <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
              <Valores><Valor saldoActual="500.0" cuota="50.0" cupoTotal="900.0" /></Valores>
            </TarjetaCredito>
            <InfoAgregadaMicrocredito>
              <Resumen>
                <EndeudamientoActual>
                  <Sector><TipoCuenta><Cuenta saldoActual="3.5" /></TipoCuenta></Sector>
                </EndeudamientoActual>
              </Resumen>
            </InfoAgregadaMicrocredito>
          </Informe>
        </Informes>
        """
        metrics = extract_financial_metrics(xml)
        self.assertEqual(metrics["metrics"]["valor_pasivos"], 3500)
        self.assertEqual(metrics["metrics"]["saldo_total_creditos"], 3000000)
        self.assertEqual(metrics["metrics"]["saldo_total_creditos_deudor_principal"], 1000000)
        self.assertEqual(metrics["metrics"]["saldo_abierto_codeudor"], 2000000)
        self.assertEqual(metrics["metrics"]["total_cuotas_credito"], 300000)
        self.assertEqual(metrics["metrics"]["total_cuotas_credito_deudor_principal"], 100000)
        self.assertEqual(metrics["metrics"]["cuota_abierta_codeudor"], 200000)
        self.assertEqual(metrics["metrics"]["cuotas_creditos_codeudor"], 200000)
        self.assertEqual(metrics["metrics"]["cupos_tarjetas_rotativos"], 900000)

    def test_tmp_sample_credit_selected_keys_recalculate_recoge(self):
        xml_path = Path(__file__).resolve().parent / "fixtures" / "tmp_sample_credit.xml"
        selected_keys = [
            "10109043858647000347111111113000000000",
            "10109043858647000347111111114000000000",
            "10109043858647000347111111115000000000",
            "10109043858605002005014178501000000000",
            "10109043858605002005014413211000000000",
            "10109043858646002046000311111000000000",
            "10109043858646002046111486338000000000",
            "10109043858646002046119211111000000000",
            "10109043858646002046173411111000000000",
            "10109043858646002046370576810000000000",
            "10109043858646002046634211111000000000",
            "10109043858646002046938979750000000000",
            "10109043858609086009487089162000000000",
        ]
        metrics = extract_financial_metrics(xml_path.read_text(encoding="utf-8"), selected_keys=selected_keys)
        self.assertEqual(metrics["metrics"]["valor_pasivos_que_recoge"], 27311000)
        self.assertEqual(metrics["metrics"]["valor_cuota_que_recoge_pago_personal"], 1507000)
        self.assertEqual(len([row for row in metrics["obligaciones_abiertas"] if row["elegible_recoge"]]), 6)



    def test_extract_financial_metrics_excludes_sistecredito_and_addi_from_recoge(self):
        xml = """
        <Informes>
          <Informe>
            <CuentaCartera entidad="SISTECREDITO" numero="1" sector="1" calidadDeudor="Principal">
              <Caracteristicas tipoCuenta="CAB" />
              <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
              <Valores><Valor saldoActual="1000.0" cuota="100.0" valorInicial="1000.0" /></Valores>
            </CuentaCartera>
            <CuentaCartera entidad="ADDI COLOMBIA" numero="2" sector="1" calidadDeudor="Principal">
              <Caracteristicas tipoCuenta="CAB" />
              <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
              <Valores><Valor saldoActual="2000.0" cuota="200.0" valorInicial="2000.0" /></Valores>
            </CuentaCartera>
            <CuentaCartera entidad="BANCO TEST" numero="3" sector="1" calidadDeudor="Principal">
              <Caracteristicas tipoCuenta="CAB" />
              <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
              <Valores><Valor saldoActual="3000.0" cuota="300.0" valorInicial="3000.0" /></Valores>
            </CuentaCartera>
          </Informe>
        </Informes>
        """
        metrics = extract_financial_metrics(xml)
        self.assertEqual(len(metrics["obligaciones_abiertas"]), 1)
        self.assertEqual(metrics["obligaciones_abiertas"][0]["entidad"], "BANCO TEST")
        self.assertEqual(metrics["metrics"]["saldo_total_creditos"], 3000000)
        self.assertEqual(metrics["metrics"]["total_cuotas_credito"], 300000)
