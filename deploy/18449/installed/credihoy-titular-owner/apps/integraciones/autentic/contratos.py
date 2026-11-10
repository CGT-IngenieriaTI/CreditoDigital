"""Manual SOP-MN-3 V4, 20/03/2025. Contratos documentales, no pagare."""

import base64
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
from pathlib import Path
from typing import ClassVar

from pypdf import PdfReader


MAX_PDF = 6_000_000
TOKEN_URL = "https://authorizer.autenticsign.com/v2/authorizer/getToken"
DEV_URL = "https://stag-api.autenticsign.com"
STAGING_URL = DEV_URL
PRODUCTION_URL = "https://api.autenticsign.com"


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
    AUDIENCE: ClassVar[str] = "stag-api.autenticsign.com"
    PERFIL: ClassVar[str] = "DEV"
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    permitir_documentos: bool = False
    hosts_descarga: tuple = ()
    timeout: tuple = (5, 20)
    solo_recuperacion: bool = False
    referencia_recuperacion: str = field(default='', repr=False)

    @property
    def base_url(self):
        return DEV_URL

    @property
    def audience(self):
        return self.AUDIENCE

    @property
    def perfil(self):
        return self.PERFIL

    @property
    def ambiente(self):
        return "DEV"

    def __post_init__(self):
        if (not texto(self.client_id) or not texto(self.client_secret)
                or any(ord(c) < 32 or ord(c) == 127 for v in (self.client_id, self.client_secret) for c in v)
                or (self.base_url, self.audience, self.ambiente) not in {
                    (DEV_URL, 'stag-api.autenticsign.com', 'DEV'),
                    (PRODUCTION_URL, 'api.autenticsign.com', 'PRODUCCION')}
                or type(self.permitir_documentos) is not bool
                or type(self.solo_recuperacion) is not bool
                or (self.solo_recuperacion and (not self.permitir_documentos or not texto(self.referencia_recuperacion)))
                or (not self.solo_recuperacion and self.referencia_recuperacion != '')
                or type(self.hosts_descarga) is not tuple
                or any(not texto(h) or h != h.lower() or any(c in h for c in '/:*@') for h in self.hosts_descarga)
                or type(self.timeout) is not tuple or len(self.timeout) != 2
                or any(type(t) not in (int, float) or not 0 < t <= 120 for t in self.timeout)):
            raise AutenticError("CONFIGURACION_DEV_INVALIDA")

    @classmethod
    def desde_archivos(cls, archivo_cliente, archivo_secreto, **opciones):
        """Raw UTF-8 credential files or constructor injection; never JSON values."""
        try:
            def leer(ruta):
                with Path(ruta).open('rb') as source:
                    raw = source.read(4097)
                if not 0 < len(raw) <= 4096:
                    raise ValueError
                return raw.decode('utf-8-sig').rstrip('\r\n')
            if Path(archivo_cliente).resolve() == Path(archivo_secreto).resolve():
                raise ValueError
            return cls(leer(archivo_cliente), leer(archivo_secreto), **opciones)
        except (OSError, ValueError, TypeError, UnicodeError):
            raise AutenticError('CONFIGURACION_DEV_INVALIDA') from None

    @classmethod
    def desde_archivo(cls, ruta):
        """Non-secret profile JSON containing file references, not credentials."""
        try:
            path = Path(ruta)
            with path.open('rb') as source:
                raw = source.read(16_385)
            if len(raw) > 16_384:
                raise ValueError
            data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_config_unica)
            if (type(data) is not dict
                    or not {"audience", "grant_type", "client_id_file", "client_secret_file"} <= set(data)
                    or set(data) - {"audience", "grant_type", "client_id_file", "client_secret_file", 'hosts_descarga', 'timeout'}
                    or data["audience"] != cls.AUDIENCE
                    or data["grant_type"] != "client_credentials"
                    or not texto(data['client_id_file']) or not texto(data['client_secret_file'])):
                raise ValueError
            if (type(data.get('hosts_descarga', [])) is not list or type(data.get('timeout', [5, 20])) is not list):
                raise ValueError
            return cls.desde_archivos(path.parent / data['client_id_file'], path.parent / data['client_secret_file'],
                hosts_descarga=tuple(data.get('hosts_descarga', [])), timeout=tuple(data.get('timeout', [5, 20])))
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise AutenticError("CONFIGURACION_DEV_INVALIDA") from None


@dataclass(frozen=True)
class ConfiguracionSTAGING(ConfiguracionDEV):
    """DEV/STAGING are one provider environment; preserve the legacy DEV journal."""
    PERFIL: ClassVar[str] = 'STAGING'


@dataclass(frozen=True)
class ConfiguracionProduccion(ConfiguracionDEV):
    AUDIENCE: ClassVar[str] = "api.autenticsign.com"
    PERFIL: ClassVar[str] = 'PRODUCCION'
    @property
    def base_url(self):
        return PRODUCTION_URL

    @property
    def ambiente(self):
        return "PRODUCCION"


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


def preparar_consulta(transaccion):
    """Signing Core v2 consult accepts a provider transactionId, not a local key."""
    if (type(transaccion) is not str or not 0 < len(transaccion) <= 1000
            or transaccion != transaccion.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in transaccion)):
        raise AutenticError('TRANSACTION_ID_PROVEEDOR_REQUERIDO')
    return {'transactionId': transaccion}

