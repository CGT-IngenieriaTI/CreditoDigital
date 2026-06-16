import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-congente")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.utils",
    "apps.usuarios",
    "apps.solicitudes",
    "apps.documentos",
    "apps.preselecta",
    "apps.historial_pago",
    "apps.xcore",
    "apps.decisiones",
    "apps.xcore_consumo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

if os.getenv("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB"),
            "USER": os.getenv("POSTGRES_USER", "postgres"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "postgres"),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"

USE_I18N = True

USE_TZ = True


STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        "apps.utils.throttling.BurstRateThrottle",
        "apps.utils.throttling.SustainedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "burst": os.getenv("DRF_BURST_RATE", "15/min"),
        "sustained": os.getenv("DRF_SUSTAINED_RATE", "100/day"),
    },
}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CSRF_TRUSTED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

EXTERNAL_API_TIMEOUT = int(os.getenv("EXTERNAL_API_TIMEOUT", "15"))
CREDIT_PIPELINE_ASYNC = os.getenv("CREDIT_PIPELINE_ASYNC", "0") == "1"
CREDIT_USE_MOCK_SERVICES = os.getenv("CREDIT_USE_MOCK_SERVICES", "1") == "1"
try:
    XCORE_CONSUMO_TASA_CUPOS_ROTATIVOS = float(
        str(os.getenv("XCORE_CONSUMO_TASA_CUPOS_ROTATIVOS", "22.4824")).replace(",", ".")
    )
except ValueError:
    XCORE_CONSUMO_TASA_CUPOS_ROTATIVOS = 22.4824
PRESELECTA_API_URL = os.getenv("PRESELECTA_API_URL", "https://api.preselecta.local/score")
HISTORIAL_PAGO_SOAP_URL = os.getenv("HISTORIAL_PAGO_SOAP_URL", "https://soap.historial.local")
XCORE_API_URL = os.getenv("XCORE_API_URL", "https://api.xcore.local/evaluate")
XCORE_CONSUMO_ORACLE_ENABLED = os.getenv("XCORE_CONSUMO_ORACLE_ENABLED", "0") == "1"
ORACLE_DSN = os.getenv("ORACLE_DSN", "")
ORACLE_USER = os.getenv("ORACLE_USER", "")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD", "")
OKTA_TOKEN_URL = os.getenv("OKTA_TOKEN_URL", "")
OKTA_CLIENT_ID = os.getenv("OKTA_CLIENT_ID", "")
OKTA_CLIENT_SECRET = os.getenv("OKTA_CLIENT_SECRET", "")
OKTA_USERNAME = os.getenv("OKTA_USERNAME", "")
OKTA_PASSWORD = os.getenv("OKTA_PASSWORD", "")
OKTA_SCOPE = os.getenv("OKTA_SCOPE", "")
# SERVICE_URL queda como alias operativo del endpoint de PRESELECTA.
# Si no se define explicitamente, se reutiliza PRESELECTA_API_URL para no
# obligar a duplicar la misma URL en el .env.
SERVICE_URL = os.getenv("SERVICE_URL", "") or PRESELECTA_API_URL
PRESELECTA_AUTH_STYLE = os.getenv("PRESELECTA_AUTH_STYLE", "access_token")
PRESELECTA_VERIFY_SSL = os.getenv("PRESELECTA_VERIFY_SSL", "1")
OKTA_GRANT_TYPE = os.getenv("OKTA_GRANT_TYPE", "password")
PRESELECTA_INQUIRY_ID = os.getenv("PRESELECTA_INQUIRY_ID", "892000373")
PRESELECTA_INQUIRY_CLIENT_TYPE = os.getenv("PRESELECTA_INQUIRY_CLIENT_TYPE", "2")
PRESELECTA_INQUIRY_USER_TYPE = os.getenv("PRESELECTA_INQUIRY_USER_TYPE", "2")
DATACREDITO_OKTA_USER = os.getenv("DATACREDITO_OKTA_USER", "")
DATACREDITO_OKTA_PASSWORD = os.getenv("DATACREDITO_OKTA_PASSWORD", "")
DATACREDITO_WSDL_URL = os.getenv("DATACREDITO_WSDL_URL", "")
DATACREDITO_SOAP_USER = os.getenv("DATACREDITO_SOAP_USER", "")
DATACREDITO_SOAP_PASSWORD = os.getenv("DATACREDITO_SOAP_PASSWORD", "")
DATACREDITO_PRODUCT_ID = os.getenv("DATACREDITO_PRODUCT_ID", "64")
DATACREDITO_INFO_ACCOUNT_TYPE = os.getenv("DATACREDITO_INFO_ACCOUNT_TYPE", "1")
DATACREDITO_SOAP_CERT = os.getenv("DATACREDITO_SOAP_CERT", "")
DATACREDITO_SOAP_KEY = os.getenv("DATACREDITO_SOAP_KEY", "")
DATACREDITO_SOAP_FULLCHAIN = os.getenv("DATACREDITO_SOAP_FULLCHAIN", "")
DATACREDITO_SOAP_ADDRESS = os.getenv("DATACREDITO_SOAP_ADDRESS", "")
DATACREDITO_SOAP_CA_BUNDLE = os.getenv("DATACREDITO_SOAP_CA_BUNDLE", "")
DATACREDITO_SERVER_IP = os.getenv("DATACREDITO_SERVER_IP", "")
DATACREDITO_SOAP_USE_WSSE = os.getenv("DATACREDITO_SOAP_USE_WSSE", "1")
DATACREDITO_SOAP_USE_SIGNATURE = os.getenv("DATACREDITO_SOAP_USE_SIGNATURE", "1")
DATACREDITO_SOAP_SIGNATURE_MODE = os.getenv("DATACREDITO_SOAP_SIGNATURE_MODE", "binary")
DATACREDITO_SOAP_PASSWORD_DIGEST = os.getenv("DATACREDITO_SOAP_PASSWORD_DIGEST", "0")
DATACREDITO_SOAP_LOG_XML = os.getenv("DATACREDITO_SOAP_LOG_XML", "0")
DATACREDITO_SOAP_TLS_VERIFY = os.getenv("DATACREDITO_SOAP_TLS_VERIFY", "1")
DATACREDITO_SOAP_MINIMAL_FIELDS = os.getenv("DATACREDITO_SOAP_MINIMAL_FIELDS", "1")
DATACREDITO_SOAP_TTL_SECONDS = os.getenv("DATACREDITO_SOAP_TTL_SECONDS", "300")
DATACREDITO_SOAP_TIME_SKEW_SECONDS = os.getenv("DATACREDITO_SOAP_TIME_SKEW_SECONDS", "120")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587") or 587)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "1") == "1"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL", "") or DEFAULT_FROM_EMAIL
OTP_PROVIDER_MODE = os.getenv("OTP_PROVIDER_MODE", "test" if DEBUG else "real").strip().lower()
OTP_AES_KEY_B64 = os.getenv("OTP_AES_KEY_B64", "").strip()
OTP_EMAIL_CONSENT_URL = os.getenv("OTP_EMAIL_CONSENT_URL", "").strip()
OTP_EMAIL_LOGO_URL = os.getenv("OTP_EMAIL_LOGO_URL", "").strip()
OTP_EMAIL_TTL_SECONDS = int(os.getenv("OTP_EMAIL_TTL_SECONDS", "600") or 600)
OTP_RESEND_COOLDOWN_SECONDS = int(os.getenv("OTP_RESEND_COOLDOWN_SECONDS", "30") or 30)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_VERIFY_SID = os.getenv("TWILIO_VERIFY_SID", "").strip()
TWILIO_VERIFY_CHANNEL = os.getenv("TWILIO_VERIFY_CHANNEL", "sms").strip() or "sms"
TWILIO_VERIFY_TEMPLATE_SID = os.getenv("TWILIO_VERIFY_TEMPLATE_SID", "").strip()
TWILIO_VERIFY_TTL_SECONDS = int(os.getenv("TWILIO_VERIFY_TTL_SECONDS", "600") or 600)
TWILIO_VERIFY_RESEND_MAX = int(os.getenv("TWILIO_VERIFY_RESEND_MAX", "3") or 3)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        }
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "file": {
            "class": "logging.FileHandler",
            "filename": LOG_DIR / "credito_digital.log",
            "formatter": "standard",
        },
    },
    "loggers": {
        "credito": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
    },
}





