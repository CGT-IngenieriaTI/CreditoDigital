from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views import View


class HealthcheckView(View):
    def get(self, request):
        return JsonResponse({"status": "ok", "service": "credito-digital"})


class CsrfTokenView(View):
    def get(self, request):
        return JsonResponse({"csrfToken": get_token(request)})
