"""Configuracion diferida: importar settings no habilita ni consulta el gateway."""

from dataclasses import dataclass, field
import math
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
    except (ValueError, TypeError, OverflowError):
        raise GatewayConfigurationError() from None


@dataclass(frozen=True, kw_only=True, repr=False)
class RiskGatewayConfig:
    enabled: bool = False
    base_url: str = ""
    internal_client: str = ""
    internal_secret: str = field(default="", repr=False)
    verify_ssl: bool = True
    ca_bundle: str = ""
    connect_timeout: float = 5
    read_timeout: float = 30
    debug: bool = False

    def __repr__(self):
        return "RiskGatewayConfig(<redacted>)"

    @classmethod
    def from_settings(cls, settings=None):
        if settings is None:
            from django.conf import settings
        if not _bandera(getattr(settings, "RISK_GATEWAY_ENABLED", "N")):
            return cls()
        return cls(enabled=True,
            base_url=getattr(settings, "RISK_GATEWAY_BASE_URL", ""),
            internal_client=getattr(settings, "RISK_GATEWAY_INTERNAL_CLIENT", ""),
            internal_secret=getattr(settings, "RISK_GATEWAY_INTERNAL_SECRET", ""),
            verify_ssl=_bandera(getattr(settings, "RISK_GATEWAY_VERIFY_SSL", "Y")),
            ca_bundle=getattr(settings, "RISK_GATEWAY_CA_BUNDLE", ""),
            connect_timeout=_timeout(getattr(settings, "RISK_GATEWAY_CONNECT_TIMEOUT", ""), 5),
            read_timeout=_timeout(getattr(settings, "RISK_GATEWAY_READ_TIMEOUT", ""), 30),
            debug=getattr(settings, "DEBUG", False))

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
            if type(self.verify_ssl) is not bool or type(self.debug) is not bool:
                raise ValueError
            if not self.verify_ssl and not self.debug:
                raise ValueError
            if not isinstance(self.ca_bundle, str) or (self.ca_bundle and (
                    not self.verify_ssl or not Path(self.ca_bundle).is_file())):
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
