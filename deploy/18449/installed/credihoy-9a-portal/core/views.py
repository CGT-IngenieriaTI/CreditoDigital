from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views import View
from django.conf import settings
from django.db import connection


class HealthcheckView(View):
    def get(self, request):
        maintenance = getattr(settings, 'CREDIHOY_ACCESO_MANTENIMIENTO', False)
        access_profile = settings.ROOT_URLCONF in (
            'core.urls_credihoy_publico', 'core.urls_credihoy_institucional')
        if maintenance or access_profile:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1')
                    if cursor.fetchone() != (1,):
                        raise ValueError
            except Exception:
                return JsonResponse({'estado': 'NO_DISPONIBLE'}, status=503,
                    headers={'Cache-Control': 'no-store, private'})
            enabled = not maintenance and getattr(settings, 'CREDIHOY_PUBLICO_ENABLED', False) is True
            relay = not maintenance and getattr(settings, 'CREDIHOY_INSTITUCIONAL_ENABLED', False) is True
            return JsonResponse({'estado': 'HABILITADO' if enabled or relay else 'MANTENIMIENTO',
                'baseEstado': 'DISPONIBLE', 'accesoDisponible': enabled,
                'releHabilitado': relay, 'seguimientoDisponible': not maintenance
                    and getattr(settings, 'CREDIHOY_SEGUIMIENTO_ENABLED', False) is True},
                headers={'Cache-Control': 'no-store, private'})
        return JsonResponse({"status": "ok", "service": "credito-digital"})


class CsrfTokenView(View):
    def get(self, request):
        return JsonResponse({"csrfToken": get_token(request)})
