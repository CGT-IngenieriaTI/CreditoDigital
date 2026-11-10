"""Cookie flags only for the restricted operator origin; legacy hosts unchanged."""
from django.conf import settings


class AdminTasasCookies:
    def __init__(self,get_response):
        self.get_response=get_response

    def __call__(self,request):
        response=self.get_response(request)
        if request.get_host()=='consulta.congente.coop:18451' and request.path.startswith('/admin/'):
            for name in (settings.SESSION_COOKIE_NAME,settings.CSRF_COOKIE_NAME):
                if name in response.cookies:
                    cookie=response.cookies[name]
                    cookie['secure']=True;cookie['httponly']=True;cookie['samesite']='Strict'
        return response
