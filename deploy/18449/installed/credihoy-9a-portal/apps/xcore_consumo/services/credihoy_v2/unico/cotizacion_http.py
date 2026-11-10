from uuid import UUID, uuid4
from django.conf import settings
from django.http import Http404
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.decorators import method_decorator
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle
from rest_framework.settings import api_settings
from rest_framework.exceptions import APIException
from .cotizacion import cotizacion, disponibilidad_simulador
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError


class CotizacionBurstThrottle(UserRateThrottle):
    scope = 'burst'

    def get_rate(self):
        # Reuse institutional rates without inventing an unconfigured user scope.
        self.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        return super().get_rate()


class CotizacionSustainedThrottle(CotizacionBurstThrottle):
    scope = 'sustained'


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class CotizacionInstitucionalView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]
    throttle_classes = [CotizacionBurstThrottle, CotizacionSustainedThrottle]
    http_method_names = ['get', 'post', 'options']

    def initial(self, request, *args, **kwargs):
        if getattr(settings, 'CREDIHOY_COTIZACION_ENABLED', False) is not True:
            raise Http404
        super().initial(request, *args, **kwargs)

    def _respond(self, request, solicitud_id, ejecutar):
        rid = str(UUID(request.headers.get('X-Request-ID', str(uuid4()))))
        dynamic = (ejecutar and type(request.data) is dict and set(request.data)=={'escenario'}) or (
            not ejecutar and dict(request.query_params)=={'simulacion':['1']})
        if ((request.query_params and (ejecutar or not dynamic)) or ejecutar and request.data != {} and not dynamic):
            return Response({'codigo': 'REQUEST_INVALIDO'}, status=400)
        session=None
        if dynamic:
            from django.utils.crypto import salted_hmac
            if not getattr(getattr(request,'session',None),'session_key',None):
                return Response({'codigo':'SESION_REQUERIDA'},status=401)
            session=salted_hmac('credihoy-simulacion-v1',str(request.user.pk)+':'+request.session.session_key,
                algorithm='sha256').hexdigest()
        result = cotizacion(solicitud_id, request.user, rid, ejecutar=ejecutar,
            escenario=request.data['escenario'] if dynamic and ejecutar else None,sesion=session,dinamica=dynamic)
        result={**result,'simuladorDinamico':disponibilidad_simulador()}
        return Response(result, headers={'X-Request-ID': rid, 'Cache-Control': 'no-store'})

    def get(self, request, solicitud_id):
        return self._respond(request, solicitud_id, False)

    def post(self, request, solicitud_id):
        return self._respond(request, solicitud_id, True)

    def handle_exception(self, exc):
        if isinstance(exc, APIException):
            return Response({'codigo': 'SESION_REQUERIDA' if exc.status_code in (401, 403) else 'HTTP_INVALIDO'}, status=exc.status_code)
        if isinstance(exc, (Http404, ObjectDoesNotExist, OperacionError)):
            return Response({'codigo': 'COTIZACION_NO_HABILITADA'}, status=404)
        return Response({'codigo': 'COTIZACION_NO_DISPONIBLE'}, status=400 if isinstance(exc, ValueError) else 503)
