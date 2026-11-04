"""Shared institutional HTTP boundary, independent of identity providers."""
from django.db import transaction
from django.http import Http404
from django.utils.decorators import method_decorator
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from . import publico_controles as controls, publico_institucional as wire


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class InstitucionalView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]
    http_method_names = ['post']

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        controls.validar_habilitacion()
        controls.ip_cliente(request)
        self.authorize_request(request)

    def authorize_request(self,request):
        self.rid=wire.authorize(request)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        response['X-Request-ID'] = getattr(self, 'rid', '')
        return response

    def handle_exception(self, exc):
        # No exception text, provider payloads, contact values or OTP are logged.
        code = 404 if isinstance(exc, (Http404, wire.NoEncontrado)) else 503 if isinstance(exc, wire.NoDisponible) else 403
        return Response({'mensaje': 'Operacion no disponible'}, status=code)

    def packet(self, **data):
        return Response({'versionContrato': wire.VERSION, 'requestId': self.rid, **data})
