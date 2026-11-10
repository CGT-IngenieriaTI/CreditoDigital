from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied, ObjectDoesNotExist
from django.http import Http404
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from .tasas import catalogo, administrar, permiso


class TasasCredihoyView(APIView):
    authentication_classes=[SessionAuthentication]
    permission_classes=[IsAuthenticated,IsAdminUser]

    def initial(self,request,*args,**kwargs):
        if getattr(settings,'CREDIHOY_TASAS_ENABLED',False) is not True:raise Http404
        super().initial(request,*args,**kwargs)
        if not any(permiso(request.user,a) for a in ('consultar','administrar','aprobar','activar')):raise PermissionDenied

    def get(self,request):
        return Response(catalogo(request.user),headers={'Cache-Control':'no-store','Vary':'Cookie'})

    def post(self,request):
        try:
            administrar(request.user,request.data,request.headers.get('Idempotency-Key'))
            return self.get(request)
        except ValidationError as exc:
            return Response({'codigo':'PARAMETRO_NO_VALIDO','motivos':exc.messages},status=409)
        except (ValueError,TypeError):
            return Response({'codigo':'REQUEST_INVALIDO'},status=400)
        except ObjectDoesNotExist:
            raise Http404
