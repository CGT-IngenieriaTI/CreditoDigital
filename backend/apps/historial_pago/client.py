import html
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

import requests
from django.conf import settings

from .extractor import _coerce_xml_string

try:
    from lxml import etree
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    from zeep import Client, Settings as ZeepSettings
    from zeep.plugins import HistoryPlugin
    from zeep.transports import Transport
    from zeep.wsse.signature import BinarySignature
    from zeep.wsse.username import UsernameToken
except ImportError:  # pragma: no cover - deps optional in local setup
    etree = None
    HTTPAdapter = None
    Retry = None
    Client = None
    ZeepSettings = None
    HistoryPlugin = None
    Transport = None
    BinarySignature = None
    UsernameToken = None


class HistorialPagoClientError(Exception):
    pass


logger = logging.getLogger("credito")


def _required(key: str) -> str:
    value = str(getattr(settings, key, "") or os.getenv(key, "")).strip()
    if value:
        return value
    raise HistorialPagoClientError(f"Falta variable requerida: {key}")


def _cert_path(path_value: str) -> str:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    path = path.resolve()
    if not path.exists():
        raise HistorialPagoClientError(f"Archivo no encontrado: {path}")
    return str(path)


def _optional_cert_path(key: str) -> str:
    value = str(getattr(settings, key, "") or os.getenv(key, "")).strip()
    return _cert_path(value) if value else ""


class HistorialPagoSOAPClient:
    def __init__(self):
        self.timeout = settings.EXTERNAL_API_TIMEOUT
        self.endpoint = settings.HISTORIAL_PAGO_SOAP_URL
        self.use_mock = settings.CREDIT_USE_MOCK_SERVICES
        if not self.use_mock:
            self.wsdl_url = _required("DATACREDITO_WSDL_URL")
            self.soap_user = _required("DATACREDITO_SOAP_USER")
            self.soap_password = _required("DATACREDITO_SOAP_PASSWORD")
            self.okta_user = _required("DATACREDITO_OKTA_USER")
            self.okta_password = _required("DATACREDITO_OKTA_PASSWORD")
            self.product_id = str(getattr(settings, "DATACREDITO_PRODUCT_ID", "64")).strip()
            self.info_account_type = str(getattr(settings, "DATACREDITO_INFO_ACCOUNT_TYPE", "1")).strip()
            self.cert_path = _cert_path(_required("DATACREDITO_SOAP_CERT"))
            self.key_path = _cert_path(_required("DATACREDITO_SOAP_KEY"))
            self.fullchain_path = _optional_cert_path("DATACREDITO_SOAP_FULLCHAIN")
            self.tls_cert_path = self.fullchain_path or self.cert_path
            self.minimal_fields = str(getattr(settings, "DATACREDITO_SOAP_MINIMAL_FIELDS", "1")).lower() in {
                "1",
                "true",
            }
            if not all([etree, HTTPAdapter, Retry, Client, ZeepSettings, HistoryPlugin, Transport, BinarySignature, UsernameToken]):
                raise HistorialPagoClientError(
                    "Dependencias SOAP faltantes. Instala requests, lxml y zeep."
                )
            self.client = self._create_zeep_client()

    def consult(self, payload: dict) -> dict:
        if self.use_mock:
            mocked = self._mock_response(payload)
            return mocked
        parameters = [{"tipo": "0", "nombre": "codigos", "valor": "HC2"}]
        result = self.consultar_hc2(
            identificacion=payload["numero_identificacion"],
            tipo_identificacion=payload.get("tipo_identificacion", "1"),
            primer_apellido=payload.get("primer_apellido", ""),
            parameters=parameters,
        )
        return result

    def _create_session(self):
        session = requests.Session()
        session.cert = (self.tls_cert_path, self.key_path)
        tls_verify = str(getattr(settings, "DATACREDITO_SOAP_TLS_VERIFY", "1")).lower() not in {"0", "false"}
        session.verify = tls_verify
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504], allowed_methods=["POST"])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.headers.update({"User-Agent": "HistorialPagoSOAPClient/1.0"})
        return session

    def _create_zeep_client(self):
        session = self._create_session()
        transport = Transport(session=session, timeout=self.timeout)
        history = HistoryPlugin()
        zeep_settings = ZeepSettings(strict=False, xml_huge_tree=True, xsd_ignore_sequence_order=True)
        client = Client(self.wsdl_url, transport=transport, settings=zeep_settings, plugins=[history])
        client.wsse = UsernameToken(username=self.okta_user, password=self.okta_password, use_digest=False)
        self.history = history
        return client

    def _build_manual_envelope(self, *, operation, identificacion, tipo_identificacion, primer_apellido, parameters=None):
        def _safe(value):
            return xml_escape(str(value or ""))

        params_xml = ""
        if parameters and not self.minimal_fields:
            for param in parameters:
                params_xml += f"""
                <ns1:parametro>
                    <ns1:tipo>{_safe(param.get('tipo', '0'))}</ns1:tipo>
                    <ns1:nombre>{_safe(param.get('nombre', ''))}</ns1:nombre>
                    <ns1:valor>{_safe(param.get('valor', ''))}</ns1:valor>
                </ns1:parametro>"""

        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:ns1="http://ws.hc2.dc.com/v1"
    xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
    <soapenv:Header>
        <wsse:Security soapenv:mustUnderstand="1">
            <wsu:Timestamp>
                <wsu:Created>{created}</wsu:Created>
                <wsu:Expires>{expires}</wsu:Expires>
            </wsu:Timestamp>
            <wsse:UsernameToken>
                <wsse:Username>{self.okta_user}</wsse:Username>
                <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText">{self.okta_password}</wsse:Password>
            </wsse:UsernameToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body>
        <ns1:{operation}>
            <ns1:solicitud>
                <ns1:clave>{_safe(self.soap_password)}</ns1:clave>
                <ns1:identificacion>{_safe(identificacion)}</ns1:identificacion>
                <ns1:primerApellido>{_safe(primer_apellido)}</ns1:primerApellido>
                <ns1:producto>{_safe(self.product_id)}</ns1:producto>
                <ns1:tipoIdentificacion>{_safe(tipo_identificacion)}</ns1:tipoIdentificacion>
                <ns1:usuario>{_safe(self.soap_user)}</ns1:usuario>
                {params_xml}
            </ns1:solicitud>
        </ns1:{operation}>
    </soapenv:Body>
</soapenv:Envelope>"""
        envelope_el = etree.fromstring(envelope.encode("utf-8"))
        envelope_el, _ = BinarySignature(self.key_path, self.cert_path).apply(envelope_el, {})
        return etree.tostring(envelope_el, encoding="utf-8", xml_declaration=True).decode("utf-8")

    def _service_url(self) -> str:
        explicit = str(getattr(settings, "DATACREDITO_SOAP_ADDRESS", "") or "").strip()
        if explicit:
            return explicit
        services = list(self.client.wsdl.services.values())
        ports = list(services[0].ports.values()) if services else []
        if not ports:
            raise HistorialPagoClientError("No se pudo resolver la URL del servicio SOAP")
        return ports[0].binding_options.get("address", "")

    def _send(self, envelope_xml: str) -> dict:
        session = self._create_session()
        try:
            response = session.post(
                self._service_url(),
                data=envelope_xml.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise HistorialPagoClientError(f"Error en request SOAP: {exc}") from exc
        return {"raw": response.text, "soap_request_xml": envelope_xml}

    def _extract_xml_from_response(self, soap_response: str) -> str:
        try:
            if etree is not None:
                root = etree.fromstring(soap_response.encode("utf-8", errors="ignore"))
                result = root.xpath(
                    "//*[local-name()='consultarHC2Return' or local-name()='consultarHC2PJReturn' or local-name()='return']/text()"
                )
            else:
                root = ET.fromstring(soap_response.encode("utf-8", errors="ignore"))
                result = [
                    (node.text or "")
                    for node in root.iter()
                    if node.tag.split("}")[-1] in {"consultarHC2Return", "consultarHC2PJReturn", "return"}
                    and (node.text or "").strip()
                ]
            if not result:
                raise HistorialPagoClientError("No se encontro XML embebido en la respuesta SOAP.")
            xml_payload = _coerce_xml_string(result[0])
            if not xml_payload:
                raise HistorialPagoClientError("La respuesta SOAP no contiene XML util para procesar.")
            return xml_payload
        except (ET.ParseError, etree.XMLSyntaxError if etree is not None else ET.ParseError) as exc:
            raise HistorialPagoClientError(f"Error parseando respuesta SOAP: {exc}") from exc

    def consultar_hc2(self, *, identificacion, tipo_identificacion, primer_apellido, parameters=None):
        envelope = self._build_manual_envelope(
            operation="consultarHC2",
            identificacion=identificacion,
            tipo_identificacion=tipo_identificacion,
            primer_apellido=primer_apellido,
            parameters=parameters,
        )
        result = self._send(envelope)
        result["xml"] = self._extract_xml_from_response(result["raw"])
        return result

    def _mock_response(self, payload: dict) -> dict:
        digits = [int(char) for char in str(payload.get("numero_identificacion", "")) if char.isdigit()]
        checksum = sum(digits)
        mora_maxima = checksum % 45
        score_pago = 660 + (checksum % 220)
        categoria = "A" if mora_maxima <= 5 else "B" if mora_maxima <= 20 else "C"
        xml = f"""
        <Informes>
          <Informe>
            <CuentaCartera entidad="CONGENTE" numero="0001" sector="1">
              <Caracteristicas tipoCuenta="CAB" />
              <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
              <Valores><Valor saldoActual="2500" cuota="120" valorInicial="4000" /></Valores>
            </CuentaCartera>
            <TarjetaCredito entidad="BANCO X" numero="9999" sector="1">
              <Estados><EstadoPago codigo="01" /><EstadoCuenta codigo="01" /></Estados>
              <Valores><Valor saldoActual="0" cuota="0" cupoTotal="3000" /></Valores>
            </TarjetaCredito>
            <InfoAgregadaMicrocredito>
              <Resumen>
                <EndeudamientoActual>
                  <Sector><TipoCuenta><Cuenta saldoActual="4.2" /></TipoCuenta></Sector>
                </EndeudamientoActual>
              </Resumen>
            </InfoAgregadaMicrocredito>
          </Informe>
        </Informes>
        """.strip()
        return {
            "estado": "OK",
            "score_pago": score_pago,
            "mora_maxima": mora_maxima,
            "categoria": categoria,
            "resumen": f"Comportamiento {categoria} con mora maxima de {mora_maxima} dias.",
            "xml": xml,
            "raw": xml,
        }
