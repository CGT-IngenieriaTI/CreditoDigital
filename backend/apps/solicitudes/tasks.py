try:
    from celery import shared_task
except ImportError:  # pragma: no cover
    def shared_task(*args, **kwargs):
        def decorator(func):
            return func

        return decorator


@shared_task
def process_credit_application_task(solicitud_id: str):
    from .services import process_credit_application

    return process_credit_application(solicitud_id)
