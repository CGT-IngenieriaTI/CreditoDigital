"""Local capture only. No radication transport is reachable from these views."""

from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.response import Response

from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from .firma_http import FirmaBase
from . import referencias


class ReferenciasView(FirmaBase):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.correlation = str(uuid4())

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["X-Request-ID"] = getattr(self, "correlation", str(uuid4()))
        return response

    def operation(self, request, call):
        if getattr(settings, "CREDIHOY_REFERENCIAS_CAPTURE_ENABLED", False) is not True:
            return Response({"codigo": "REFERENCIAS_CAPTURA_NO_HABILITADA"}, status=404)
        try:
            self.correlation = str(UUID(request.headers.get("X-Request-ID", self.correlation)))
        except (ValueError, TypeError):
            return Response({"codigo": "REQUEST_ID_INVALIDO"}, status=400)
        try:
            return Response({**call(), "requestId": self.correlation})
        except ObjectDoesNotExist:
            return Response({"codigo": "EXPEDIENTE_NO_DISPONIBLE"}, status=404)
        except OperacionError as exc:
            return Response({"codigo": exc.codigo}, status=409)
        except Exception:
            return Response({"codigo": "REFERENCIAS_OPERACION_NO_COMPLETADA"}, status=400)

    def get(self, request, solicitud_id):
        return self.operation(request, lambda: referencias.leer(solicitud_id, request.user))

    def post(self, request, solicitud_id):
        return self.operation(request, lambda: referencias.guardar(solicitud_id, request.user, request.data,
                                                                  request_id=self.correlation))


class ReferenciasCatalogosView(ReferenciasView):
    http_method_names = ["get", "head", "options"]

    def get(self, request, solicitud_id):
        def read():
            referencias._context(solicitud_id, request.user)
            return referencias.catalogos()
        return self.operation(request, read)
