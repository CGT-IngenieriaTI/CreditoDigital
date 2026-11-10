import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.historial_pago.client import _cert_path, _required


def _setting_value(key: str) -> str:
    return str(getattr(settings, key, "") or "").strip()


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.netloc:
        return value
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _file_digest(path: str) -> dict:
    file_path = Path(path)
    data = file_path.read_bytes()
    return {
        "path": str(file_path),
        "exists": file_path.exists(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _public_key_sha256(public_key) -> str:
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def _name_attrs(name: x509.Name) -> str:
    parts = []
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME, NameOID.COUNTRY_NAME):
        values = name.get_attributes_for_oid(oid)
        if values:
            parts.append(f"{oid._name}={values[0].value}")
    return ", ".join(parts) or name.rfc4514_string()


def _cert_info(cert: x509.Certificate) -> dict:
    eku_names = []
    san_dns = []
    try:
        eku = cert.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
        for item in eku:
            if item == ExtendedKeyUsageOID.CLIENT_AUTH:
                eku_names.append("clientAuth")
            elif item == ExtendedKeyUsageOID.SERVER_AUTH:
                eku_names.append("serverAuth")
            else:
                eku_names.append(item.dotted_string)
    except x509.ExtensionNotFound:
        pass
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        san_dns = list(san.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    public_key = cert.public_key()
    return {
        "subject": _name_attrs(cert.subject),
        "issuer": _name_attrs(cert.issuer),
        "serial_number": f"{cert.serial_number:x}",
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "sha256": fingerprint,
        "public_key_sha256": _public_key_sha256(public_key),
        "extended_key_usage": eku_names,
        "has_client_auth": "clientAuth" in eku_names,
        "has_server_auth": "serverAuth" in eku_names,
        "san_dns": san_dns,
    }


def _load_certificates(path: str) -> list[x509.Certificate]:
    data = Path(path).read_bytes()
    certs = x509.load_pem_x509_certificates(data)
    return list(certs)


def _private_key_info(path: str) -> dict:
    data = Path(path).read_bytes()
    private_key = serialization.load_pem_private_key(data, password=None)
    key_type = type(private_key).__name__
    key_size = None
    if isinstance(private_key, rsa.RSAPrivateKey):
        key_size = private_key.key_size
    elif isinstance(private_key, ec.EllipticCurvePrivateKey):
        key_size = private_key.key_size
    return {
        "type": key_type,
        "key_size": key_size,
        "public_key_sha256": _public_key_sha256(private_key.public_key()),
    }


class Command(BaseCommand):
    help = "Diagnostica certificados mTLS usados por Datacredito SOAP sin exponer llave privada."

    def add_arguments(self, parser):
        parser.add_argument(
            "--live-wsdl",
            action="store_true",
            help="Intenta descargar el WSDL con el certificado configurado y reporta el error TLS/HTTP.",
        )
        parser.add_argument(
            "--url",
            default="",
            help="URL WSDL alternativa para probar sin cambiar DATACREDITO_WSDL_URL.",
        )

    def handle(self, *args, **options):
        cert_path = _cert_path(_required("DATACREDITO_SOAP_CERT"))
        key_path = _cert_path(_required("DATACREDITO_SOAP_KEY"))
        fullchain_raw = _setting_value("DATACREDITO_SOAP_FULLCHAIN")
        fullchain_path = _cert_path(fullchain_raw) if fullchain_raw else ""
        tls_cert_path = fullchain_path or cert_path

        certs = _load_certificates(cert_path)
        fullchain_certs = _load_certificates(tls_cert_path)
        leaf = certs[0]
        private_key = _private_key_info(key_path)
        leaf_info = _cert_info(leaf)

        tls_verify = str(getattr(settings, "DATACREDITO_SOAP_TLS_VERIFY", "1")).lower() not in {
            "0",
            "false",
        }
        wsdl_url = str(options.get("url") or _setting_value("DATACREDITO_WSDL_URL")).strip()
        response = {
            "runtime": {
                "credit_use_mock_services": bool(getattr(settings, "CREDIT_USE_MOCK_SERVICES", False)),
                "wsdl_url": _safe_url(wsdl_url),
                "wsdl_url_source": "argument" if options.get("url") else "settings",
                "tls_verify": tls_verify,
                "client_module": "apps.historial_pago.client",
                "tls_session_cert": [tls_cert_path, key_path],
                "uses_fullchain_for_mtls": bool(fullchain_path and tls_cert_path == fullchain_path),
            },
            "files": {
                "cert": _file_digest(cert_path),
                "key": _file_digest(key_path),
                "fullchain": _file_digest(fullchain_path) if fullchain_path else None,
            },
            "leaf_certificate": leaf_info,
            "private_key": private_key,
            "key_matches_leaf_certificate": private_key["public_key_sha256"] == leaf_info["public_key_sha256"],
            "tls_chain": [_cert_info(cert) for cert in fullchain_certs],
        }

        if options["live_wsdl"]:
            try:
                live = requests.get(
                    wsdl_url,
                    cert=(tls_cert_path, key_path),
                    verify=tls_verify,
                    timeout=getattr(settings, "EXTERNAL_API_TIMEOUT", 15),
                )
                response["live_wsdl"] = {
                    "ok": live.ok,
                    "status_code": live.status_code,
                    "content_type": live.headers.get("Content-Type", ""),
                    "bytes": len(live.content),
                }
            except requests.RequestException as exc:
                response["live_wsdl"] = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

        self.stdout.write(json.dumps(response, ensure_ascii=False, indent=2))
