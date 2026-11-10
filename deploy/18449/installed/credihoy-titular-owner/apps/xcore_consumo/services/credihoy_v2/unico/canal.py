"""Canal restringido de validacion, sin recepcion ni ejecucion financiera."""

import re
import json
import logging
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView


VERSION = "credihoy-canal-validacion-v1"


class CanalBase(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def initial(self, request, *args, **kwargs):
        if getattr(settings, "CREDIHOY_CANAL_VALIDACION_ENABLED", False) is not True:
            raise Http404
        raw = request.headers.get("X-Request-ID", "")
        self.request_id = raw if re.fullmatch(r"[a-zA-Z0-9._:-]{1,64}", raw) else uuid4().hex
        super().initial(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["X-Request-ID"] = getattr(self, "request_id", uuid4().hex)
        logging.getLogger("credito.canal").info(json.dumps({"evento": "credihoy.canal",
            "requestId": response["X-Request-ID"], "operacion": type(self).__name__,
            "httpStatus": response.status_code}))
        return response


class EstadoCanalView(CanalBase):
    def get(self, request):
        result = {"versionContrato": VERSION, "requestId": self.request_id,
            "producto": "CREDIHOY UNICO (NUEVO)", "modalidad": "111",
            "entorno": "VALIDACION_RESTRINGIDA", "recepcionHabilitada": False,
            "evaluacionHabilitada": False, "capacidad": "NO_EJECUTADA",
            "decisionCrediticia": "NO_AUTORIZADA", "autorizadoDecisionCrediticia": False,
            "radicacionLinix": "NO_EJECUTADA",
            "bloqueos": ["CONSENTIMIENTOS_Y_RETENCION_PENDIENTES",
                "HECHOS_FINANCIEROS_PENDIENTES", "POLITICA_DECISORIA_PENDIENTE"]}
        if (getattr(settings, "CREDIHOY_PILOTO_ASISTIDO_ENABLED", False) is True
                and request.user.username == getattr(settings, "CREDIHOY_FIRMA_OPERADOR_USERNAME", None)):
            result["pilotoAsistido"] = {"habilitado": True, "identidadDigitalCertificada": False,
                "recepcionPublica": False, "proveedores": ["HDC"], "requiereConfirmacionEnvio": True}
        if getattr(settings,'CREDIHOY_TASAS_ENABLED',False) is True:
            from .tasas import permiso
            if any(permiso(request.user,a) for a in ('administrar','aprobar','activar')):
                result['administracionTasas']={'ruta':'/credihoy/administracion/parametros/tasas'}
        return Response(result)


@method_decorator(csrf_protect, name="dispatch")
class LoginCanalView(CanalBase):
    permission_classes = [AllowAny]

    def post(self, request):
        body = request.data
        if (not isinstance(body, dict) or set(body) != {"username", "password"}
                or any(not isinstance(body[k], str) or not 0 < len(body[k]) <= 254 for k in body)):
            return Response({"codigo": "SESION_NO_AUTORIZADA"}, status=403)
        user = authenticate(request=request, username=body["username"], password=body["password"])
        if user is None or not user.is_active or not user.is_staff:
            return Response({"codigo": "SESION_NO_AUTORIZADA"}, status=403)
        login(request, user)
        return Response({"sesion": "OPERADOR_AUTENTICADO"})


class LogoutCanalView(CanalBase):
    def post(self, request):
        logout(request)
        return Response(status=204)
