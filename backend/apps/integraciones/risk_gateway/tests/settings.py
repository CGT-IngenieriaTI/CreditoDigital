"""Settings exclusivos del runner offline, sin DB ni proveedores reales."""

from core.settings import *  # noqa: F403

DEBUG = True
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CREDIT_USE_MOCK_SERVICES = True
XCORE_CONSUMO_ORACLE_ENABLED = False
OTP_PROVIDER_MODE = "test"
RISK_GATEWAY_ENABLED = "N"
