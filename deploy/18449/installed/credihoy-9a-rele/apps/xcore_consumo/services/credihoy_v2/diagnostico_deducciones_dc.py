"""Extraccion HDC offline limitada a candidatos DC, sin homologacion ni scoring."""

import argparse
import json
from pathlib import Path

from lxml import etree


LIMITE_XML_BYTES = 8 * 1024 * 1024
SOAP_NAMESPACES = {
    "http://schemas.xmlsoap.org/soap/envelope/",
    "http://www.w3.org/2003/05/soap-envelope",
}
NAMESPACES_NO_FINANCIEROS = SOAP_NAMESPACES | {
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
    "http://schemas.xmlsoap.org/ws/2002/12/secext",
    "http://www.w3.org/2000/09/xmldsig#",
    "http://www.w3.org/2001/04/xmlenc#",
}


class ErrorDiagnosticoDeduccionesDc(ValueError):
    """Codigos publicos; nunca publicar texto XML, atributos ni errores del parser."""


def _nombre(nodo):
    nombre = etree.QName(nodo)
    return nombre.namespace, nombre.localname


def _hijos(nodo, nombre, namespace):
    return [h for h in nodo if isinstance(h.tag, str) and _nombre(h) == (namespace, nombre)]


def _raiz_hdc(raiz):
    namespace, nombre = _nombre(raiz)
    if nombre == "Informes" and namespace not in NAMESPACES_NO_FINANCIEROS:
        return raiz
    if nombre == "Envelope" and namespace in SOAP_NAMESPACES:
        bodies = _hijos(raiz, "Body", namespace)
        if len(bodies) == 1:
            informes = [h for h in bodies[0] if isinstance(h.tag, str)
                        and _nombre(h)[1] == "Informes" and _nombre(h)[0] not in NAMESPACES_NO_FINANCIEROS]
            if len(informes) == 1:
                return informes[0]
    raise ErrorDiagnosticoDeduccionesDc("XML_HDC_RAIZ_NO_SOPORTADA")


def _recorrer(raiz, *segmentos):
    namespace = _nombre(raiz)[0]
    actuales = [(raiz, "Informes[1]")]
    for nombre in segmentos:
        actuales = [(hijo, f"{ruta}/{nombre}[{i}]")
                    for nodo, ruta in actuales
                    for i, hijo in enumerate(_hijos(nodo, nombre, namespace), start=1)]
    return actuales


def _campo(nodo, atributo, ruta):
    return {"valorOriginal": nodo.get(atributo) if nodo is not None else None,
            "rutaOrigen": f"{ruta}/@{atributo}"}


def extraer_evidencia_deducciones_dc(xml: str | bytes) -> dict:
    """Solo lee atributos permitidos de un unico Informe; devuelve strings/null.

    Acepta Informes directo o como hijo XML directo del Body SOAP. No procesa
    retornos escapados, headers, archivos externos ni XML con DTD. No infiere
    fechaCorte desde fechaConsulta ni desde un trimestre historico.
    """
    if not isinstance(xml, (str, bytes)):
        raise ErrorDiagnosticoDeduccionesDc("XML_HDC_TIPO_INVALIDO")
    try:
        contenido = xml.encode("utf-8") if isinstance(xml, str) else xml
        if len(contenido) > LIMITE_XML_BYTES:
            raise ErrorDiagnosticoDeduccionesDc("XML_HDC_EXCEDE_LIMITE")
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
            recover=False, huge_tree=False, remove_comments=True, remove_pis=True,
            encoding="utf-8" if isinstance(xml, str) else None)
        raiz = etree.fromstring(contenido, parser=parser)
        if raiz.getroottree().docinfo.doctype:
            raise ErrorDiagnosticoDeduccionesDc("XML_HDC_DTD_NO_PERMITIDO")
    except (etree.XMLSyntaxError, UnicodeError, ValueError) as error:
        if isinstance(error, ErrorDiagnosticoDeduccionesDc):
            raise
        raise ErrorDiagnosticoDeduccionesDc("XML_HDC_MAL_FORMADO") from None

    raiz = _raiz_hdc(raiz)
    informes = _recorrer(raiz, "Informe")
    if len(informes) != 1:
        raise ErrorDiagnosticoDeduccionesDc("XML_HDC_INFORME_NO_UNICO")
    informe, ruta_informe = informes[0]
    saldos = _recorrer(raiz, "Informe", "InfoAgregada", "Resumen", "Saldos")
    if len(saldos) > 1:
        raise ErrorDiagnosticoDeduccionesDc("XML_HDC_CUOTA_MENSUAL_NO_UNICA")
    nodo_saldos, ruta_saldos = (saldos[0] if saldos else
        (None, f"{ruta_informe}/InfoAgregada[1]/Resumen[1]/Saldos[1]"))
    cuota_mensual = _campo(nodo_saldos, "cuotaMensual", ruta_saldos)
    fecha_corte = _campo(informe, "fechaCorte", ruta_informe)
    advertencias = ["monedaNoCertificada", "escalaNoCertificada", "coberturaNoCertificada",
                    "equivalenciaCurubaNoCertificada"]
    if cuota_mensual["valorOriginal"] is None:
        advertencias.append("cuotaMensualAusente")
    elif cuota_mensual["valorOriginal"] == "":
        advertencias.append("cuotaMensualVacia")
    if fecha_corte["valorOriginal"] is None:
        advertencias.append("fechaCorteNoExplicita")
    evidencia = {
        "cuotaMensual": cuota_mensual,
        "totales": [
            {"calidadDeudor": nodo.get("calidadDeudor"), "cuota": nodo.get("cuota"),
             "rutaOrigen": f"{ruta}/@cuota"}
            for nodo, ruta in _recorrer(raiz, "Informe", "InfoAgregada", "Totales", "Total")],
        "tiposCuenta": [
            {"codigoTipo": nodo.get("codigoTipo"), "tipo": nodo.get("tipo"),
             "calidadDeudor": nodo.get("calidadDeudor"), "cuota": nodo.get("cuota"),
             "rutaOrigen": f"{ruta}/@cuota"}
            for nodo, ruta in _recorrer(raiz, "Informe", "InfoAgregada", "Totales", "TipoCuenta")],
        "cuotasMes": [
            _campo(nodo, "cuotaMes", ruta)
            for nodo, ruta in _recorrer(raiz, "Informe", "InfoAgregadaMicrocredito", "Resumen",
                                       "EndeudamientoActual", "Sector", "TipoCuenta", "Usuario", "Cuenta")],
        "fechaCorte": fecha_corte,
        "fechaConsulta": _campo(informe, "fechaConsulta", ruta_informe),
        "advertencias": advertencias,
    }
    return {"evidenciaDeduccionesDc": evidencia}


def diagnosticar_archivo_xml(archivo: str | Path) -> dict:
    """Ruta explicita, apertura binaria de solo lectura; no escribe el resultado."""
    try:
        with Path(archivo).open("rb") as fuente:
            contenido = fuente.read(LIMITE_XML_BYTES + 1)
    except (OSError, ValueError, TypeError):
        raise ErrorDiagnosticoDeduccionesDc("ARCHIVO_XML_HDC_NO_LEIBLE") from None
    return extraer_evidencia_deducciones_dc(contenido)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evidencia de deducciones DC desde XML HDC local")
    parser.add_argument("--archivo", required=True)
    argumentos = parser.parse_args(argv)
    try:
        resultado = diagnosticar_archivo_xml(argumentos.archivo)
    except ErrorDiagnosticoDeduccionesDc as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=True))
        return 1
    print(json.dumps({"ok": True, **resultado}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
