"""Configuracion inerte y validacion diferida, sin lectura de secretos en logs."""

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .errores import GatewayConfigurationError, GatewayDisabled


def _bandera(valor):
    if type(valor) is bool:
        return valor
    if isinstance(valor, str):
        if valor.lower() in {"y", "yes", "true", "1"}:
            return True
        if valor.lower() in {"n", "no", "false", "0"}:
            return False
    raise GatewayConfigurationError()


def _timeout(valor, default):
    try:
        if type(valor) is bool:
            raise ValueError
        resultado = float(default if valor in (None, "") else valor)
        if not math.isfinite(resultado) or resultado <= 0:
            raise ValueError
        return resultado
    except (TypeError, ValueError, OverflowError):
        raise GatewayConfigurationError() from None


def _secreto(settings):
    value = getattr(settings, "RISK_GATEWAY_INTERNAL_SECRET", "")
    reference = getattr(settings, "RISK_GATEWAY_INTERNAL_SECRET_FILE", "")
    if not reference:
        return value
    try:
        if value or type(reference) is not str:
            raise ValueError
        path = Path(reference)
        if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
            raise ValueError
        path = path.resolve(strict=True)
        if not path.is_file() or any((p / ".git").exists() for p in path.parents):
            raise ValueError
        if os.name != "nt" and path.stat().st_mode & 0o007:
            raise ValueError
        with path.open("rb") as secret:
            raw = secret.read(261)
        if len(raw) > 260:
            raise ValueError
        value = raw.decode("ascii").rstrip("\r\n")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value):
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError, RuntimeError):
        raise GatewayConfigurationError() from None


@dataclass(frozen=True, kw_only=True)
class GatewayConfig:
    enabled: bool = False
    base_url: str = field(default="", repr=False)
    internal_client: str = ""
    internal_secret: str = field(default="", repr=False)
    verify_ssl: bool = True
    ca_bundle: str = field(default="", repr=False)
    connect_timeout: float = 5
    read_timeout: float = 30

    @classmethod
    def from_settings(cls, settings=None):
        if settings is None:
            from django.conf import settings
        enabled = _bandera(getattr(settings, "RISK_GATEWAY_ENABLED", "N"))
        if not enabled:
            return cls()
        return cls(enabled=True,
            base_url=getattr(settings, "RISK_GATEWAY_BASE_URL", ""),
            internal_client=getattr(settings, "RISK_GATEWAY_INTERNAL_CLIENT", ""),
            internal_secret=_secreto(settings),
            verify_ssl=_bandera(getattr(settings, "RISK_GATEWAY_VERIFY_SSL", "Y")),
            ca_bundle=getattr(settings, "RISK_GATEWAY_CA_BUNDLE", ""),
            connect_timeout=_timeout(getattr(settings, "RISK_GATEWAY_CONNECT_TIMEOUT", ""), 5),
            read_timeout=_timeout(getattr(settings, "RISK_GATEWAY_READ_TIMEOUT", ""), 30))

    def validar(self):
        if type(self.enabled) is not bool:
            raise GatewayConfigurationError()
        if not self.enabled:
            raise GatewayDisabled()
        try:
            if not isinstance(self.base_url, str) or any(c.isspace() for c in self.base_url):
                raise ValueError
            url = urlsplit(self.base_url)
            if (url.scheme != "https" or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment
                    or url.path not in ("", "/")):
                raise ValueError
            url.port
            if not isinstance(self.internal_client, str) or not re.fullmatch(
                    r"[a-z0-9][a-z0-9._-]{0,119}", self.internal_client):
                raise ValueError
            if not isinstance(self.internal_secret, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{32,256}", self.internal_secret):
                raise ValueError
            if type(self.verify_ssl) is not bool or not isinstance(self.ca_bundle, str):
                raise ValueError
            if self.ca_bundle and (not self.verify_ssl or not Path(self.ca_bundle).is_file()):
                raise ValueError
            if any(type(v) not in (int, float) for v in (self.connect_timeout, self.read_timeout)):
                raise ValueError
            _timeout(self.connect_timeout, 5)
            _timeout(self.read_timeout, 30)
        except (ValueError, TypeError, OSError):
            raise GatewayConfigurationError() from None
        return self

    @property
    def verify(self):
        return self.ca_bundle or self.verify_ssl
