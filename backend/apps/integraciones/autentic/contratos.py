"""Manual SOP-MN-3 V4, 20/03/2025. Contratos documentales, no pagare."""

import base64
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
from pathlib import Path

from pypdf import PdfReader


MAX_PDF = 6_000_000
TOKEN_URL = "https://authorizer.autenticsign.com/v2/authorizer/getToken"
DEV_URL = "https://stag-api.autenticsign.com"


class AutenticError(Exception):
    def __init__(self, codigo):
        self.codigo = codigo
        super().__init__(codigo)


def texto(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 1000


def _config_unica(pares):
    result = {}
    for key, value in pares:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def validar_pdf(content):
    try:
        if type(content) is not bytes or not 0 < len(content) <= MAX_PDF or not content.startswith(b"%PDF-"):
            raise ValueError
        pdf = PdfReader(BytesIO(content), strict=True)
        if pdf.is_encrypted or not len(pdf.pages):
            raise ValueError
    except Exception:
        raise AutenticError("PDF_INVALIDO") from None
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class ConfiguracionDEV:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    permitir_documentos: bool = False
    hosts_descarga: tuple = ()
    timeout: tuple = (5, 20)

    def __post_init__(self):
        if (not texto(self.client_id) or not texto(self.client_secret)
                or type(self.permitir_documentos) is not bool
                or type(self.hosts_descarga) is not tuple
                or any(not texto(h) or h != h.lower() or any(c in h for c in '/:*@') for h in self.hosts_descarga)
                or type(self.timeout) is not tuple or len(self.timeout) != 2
                or any(type(t) not in (int, float) or not 0 < t <= 120 for t in self.timeout)):
            raise AutenticError("CONFIGURACION_DEV_INVALIDA")

    @classmethod
    def desde_archivo(cls, ruta):
        try:
            data = json.loads(Path(ruta).read_text(encoding="utf-8-sig"), object_pairs_hook=_config_unica)
            if (set(data) != {"audience", "grant_type", "client_id", "client_secret"}
                    or data["audience"] != "stag-api.autenticsign.com"
                    or data["grant_type"] != "client_credentials"):
                raise ValueError
            return cls(client_id=data["client_id"], client_secret=data["client_secret"])
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise AutenticError("CONFIGURACION_DEV_INVALIDA") from None


@dataclass(frozen=True, repr=False)
class SolicitudFirma:
    payload: dict
    hashes_pdf: dict
    fingerprint: str


@dataclass(frozen=True, repr=False)
class SolicitudCertificacion:
    payload: dict
    fingerprint: str


def preparar_certificacion(transaccion, documento):
    """documentNumber debe proceder del proveedor, no de docId ni del archivo."""
    if not texto(transaccion) or not texto(documento):
        raise AutenticError("DOCUMENT_NUMBER_OFICIAL_REQUERIDO")
    payload = {"certificationType": "SIGNATURE", "transactionId": transaccion,
               "documentNumber": documento}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return SolicitudCertificacion(payload, hashlib.sha256(canonical).hexdigest())


def preparar_firma(metadata, documentos):
    """Primera firma solamente; identityValidation/secureKey no se derivan de OTP."""
    required={"names", "lastNames", "docId", "secureKey"}
    validation={"identityValidation","identityValidationId","identityValidationEmail","identityValidationPhone"}
    if (type(metadata) is not dict or not required<=set(metadata) or set(metadata)-required-validation
            or not all(texto(v) for v in metadata.values())
            or type(documentos) is not dict or not 1 <= len(documentos) <= 5):
        raise AutenticError("SOLICITUD_FIRMA_INVALIDA")
    if set(metadata)&validation and (not {'identityValidation','identityValidationId'}<=set(metadata)
            or not {'identityValidationEmail','identityValidationPhone'}&set(metadata)):
        raise AutenticError('VALIDACION_IDENTIDAD_DOCUMENTADA_INCOMPLETA')
    files, hashes = [], {}
    for name, content in documentos.items():
        if (not texto(name) or not name.lower().endswith('.pdf')
                or any(c in name for c in '/\\:\x00') or name.startswith('.')):
            raise AutenticError("NOMBRE_DOCUMENTO_INVALIDO")
        hashes[name] = validar_pdf(content)
        files.append({"fileName": name, "fileContent": base64.b64encode(content).decode("ascii")})
    if sum(map(len, documentos.values())) > MAX_PDF:
        raise AutenticError("LIMITE_DOCUMENTOS")
    payload = {"metadata": dict(metadata), "files": files}
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return SolicitudFirma(payload, hashes, hashlib.sha256(canonical).hexdigest())
