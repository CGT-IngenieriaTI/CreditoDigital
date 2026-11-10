"""Pure offline SC-input experiment. No transport, settings or Oracle access."""

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import re
from xml.etree import ElementTree as ET


SC_TAGS = ("A_TIPO_SOLI_CREDITO", "A_NUMERO_SOLI_CREDITO")
MAX_XML_BYTES = 1024 * 1024


class XmlExperimentError(ValueError):
    pass


def _parse(xml):
    if (not isinstance(xml, str) or not xml.strip()
            or len(xml.encode("utf-8")) > MAX_XML_BYTES
            or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", xml, re.I)):
        raise XmlExperimentError("CD16_XML_NO_ADMISIBLE")
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        root = ET.fromstring(xml, parser=parser)
    except (ET.ParseError, ValueError):
        raise XmlExperimentError("CD16_XML_INVALIDO") from None
    if root.tag != "Entrada":
        raise XmlExperimentError("CD16_NO_ES_ENTRADA_ORIGINAL")
    for path in ("./Datos", "./Estructura", "./Estructura/General"):
        if len(root.findall(path)) != 1:
            raise XmlExperimentError("CD16_ESTRUCTURA_AMBIGUA")
    data, general = root.find("./Datos"), root.find("./Estructura/General")
    tokens = (general.text or "").split(",")
    names = [token.strip().removesuffix("[]") for token in tokens]
    fields = [node.tag for node in data if isinstance(node.tag, str)]
    if (any(not name for name in names) or len(set(names)) != len(names)
            or len(set(fields)) != len(fields) or set(names) != set(fields)):
        raise XmlExperimentError("CD16_GENERAL_DATOS_INCOHERENTES")
    if any(data.findtext(tag) != value for tag, value in (
            ("A_PRODUCTO", "390"), ("A_MODALIDAD_CREDITO", "111"), ("A_DESTINACION", "CFA"))):
        raise XmlExperimentError("CD16_PERFIL_NO_CREDIHOY")
    return root


def _without_sc(root):
    candidate = deepcopy(root)
    data = candidate.find("./Datos")
    for tag in SC_TAGS:
        nodes = data.findall(tag)
        if len(nodes) != 1 or nodes[0].attrib or len(nodes[0]):
            raise XmlExperimentError("CD16_CLAVES_SC_AMBIGUAS")
        value = nodes[0].text
        if (tag == SC_TAGS[0] and value != "SC") or (tag == SC_TAGS[1] and value and value.strip()):
            raise XmlExperimentError("CD16_NO_ES_SC_PENDIENTE")
        data.remove(nodes[0])
    general = candidate.find("./Estructura/General")
    tokens = general.text.split(",")
    general.text = ",".join(token for token in tokens if token.strip() not in SC_TAGS)
    return candidate


def _signature(node):
    # Ignore indentation, not meaningful field values, attributes or child order.
    meaningful = lambda text: text if text and text.strip() else None
    text = node.text if not len(node) else meaningful(node.text)
    return (node.tag, tuple(sorted(node.attrib.items())), text,
            meaningful(node.tail), tuple(_signature(child) for child in node))


def verify_sc_input_variant(original, candidate):
    expected = _without_sc(_parse(original))
    actual = _parse(candidate)
    if _signature(expected) != _signature(actual):
        raise XmlExperimentError("CD16_CAMBIO_FUERA_DE_CLAVES_SC")
    return {
        "xmlBienFormado": True, "estructuraDeclarativaCoherente": True,
        "soloCambioClavesScEntrada": True,
        "cambiosXPath": ["/Entrada/Datos/" + tag for tag in SC_TAGS]
                        + ["/Entrada/Estructura/General/text()"],
        "compatibilidadOracle": "NO_CERTIFICADA", "enviable": False,
    }


@dataclass(frozen=True)
class ScInputVariants:
    original: str
    candidate: str

    def report(self):
        return {
            **verify_sc_input_variant(self.original, self.candidate),
            "sha256Original": sha256(self.original.encode("utf-8")).hexdigest(),
            "sha256Candidato": sha256(self.candidate.encode("utf-8")).hexdigest(),
        }


def prepare_sc_input_variants(original):
    root = _parse(original)
    candidate = ET.tostring(_without_sc(root), encoding="unicode")
    verify_sc_input_variant(original, candidate)
    return ScInputVariants(original, candidate)
