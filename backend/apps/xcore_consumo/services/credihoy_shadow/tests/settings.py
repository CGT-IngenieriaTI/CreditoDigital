"""Configuracion exclusiva de tests; nunca usa la base del entorno."""

from core.settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
SECRET_KEY = "solo-tests-shadow"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CREDIHOY_SHADOW_ENABLED = False
XCORE_CONSUMO_ORACLE_ENABLED = False
CREDIT_USE_MOCK_SERVICES = True
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"null": {"class": "logging.NullHandler"}},
           "root": {"handlers": ["null"], "level": "CRITICAL"}}
