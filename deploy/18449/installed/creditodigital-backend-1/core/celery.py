import os

try:
    from celery import Celery
except ImportError:  # pragma: no cover
    Celery = None


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

if Celery:
    app = Celery("core")
    app.config_from_object("django.conf:settings", namespace="CELERY")
    app.autodiscover_tasks()
else:  # pragma: no cover
    app = None
