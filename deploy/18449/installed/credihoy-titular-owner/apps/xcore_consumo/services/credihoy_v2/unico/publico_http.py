"""Public cookie/CSRF boundary. No staff auth or financial actions are exposed."""
from datetime import timedelta
from uuid import uuid4
import logging
from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.solicitudes.models import Solicitud
from . import publico as service
from .consulta_controlada import PRODUCTO


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class PublicView(APIView):
    authentication_classes=[]
    permission_classes=[AllowAny]
    parser_classes=[JSONParser]
    renderer_classes=[JSONRenderer]
    http_method_names=['get','post','options']

    def initial(self,request,*args,**kwargs):
        if getattr(settings,'CREDIHOY_PUBLICO_ENABLED',False) is not True:raise Http404
        super().initial(request,*args,**kwargs)
        service.habilitado()
        service.controles.ip_cliente(request)
        from . import publico_restringido as restringido
        if restringido.activo():restringido.autorizar_edge(request)
        if not request.is_secure():raise service.AccesoDenegado
        if request.method not in {'GET','HEAD','OPTIONS'}:
            # DRF exempts APIViews from the middleware: enforce even anonymously.
            SessionAuthentication().enforce_csrf(request)

    def finalize_response(self,request,response,*args,**kwargs):
        response=super().finalize_response(request,response,*args,**kwargs)
        response['Cache-Control']='no-store, private';response['Vary']='Cookie'
        response['Referrer-Policy']='no-referrer'
        return response

    def handle_exception(self,exc):
        if isinstance(exc,Http404):status=404
        elif isinstance(exc,service.AccesoDenegado):status=401
        elif isinstance(exc,ValueError):status=400
        elif isinstance(exc,APIException):status=exc.status_code
        else:status=503
        message='No fue posible completar la verificacion. Revisa tus datos o comunicate con Congente.'
        return Response({'mensaje':message},status=status)


class InicioView(PublicView):
    http_method_names=['get','options']
    def get(self,request):
        return Response({**service.inicializar(request),'csrfToken':get_token(request)})


class SolicitarView(PublicView):
    http_method_names=['get','post','options']
    def get(self,request):
        if set(request.query_params)!={'intento'}:raise ValueError
        return Response(service.estado_intento(request,request.query_params['intento']))
    def post(self,request):
        if type(request.data) is dict and set(request.data)=={'intento','canal'}:
            return Response(service.confirmar_envio(request,request.data),status=202)
        if type(request.data) is dict and 'cancelar' in request.data:
            return Response(service.cancelar_intento(request,request.data))
        return Response(service.iniciar(request,request.data,request.headers.get('X-Request-ID','')),status=202)


class VerificarView(PublicView):
    http_method_names=['post','options']
    def post(self,request):return Response(service.verificar(request,request.data))


class SesionView(PublicView):
    http_method_names=['get','options']
    def get(self,request):
        try:a,data=service.acceso(request)
        except service.AccesoDenegado:return Response({'versionContrato':service.VERSION,'autenticado':False})
        return Response({'versionContrato':service.VERSION,'autenticado':True,
            'nombre':data['nombre'],'expiraAt':a.sesion_expira_at.isoformat(),'sesionReferencia':str(a.pk),
            'acciones':{'seguimiento':getattr(settings,'CREDIHOY_SEGUIMIENTO_ENABLED',True) is True,
                'solicitar':False,'simular':False,'documentos':False}})


class CerrarView(PublicView):
    http_method_names=['post','options']
    def post(self,request):
        if request.data!={}:raise ValueError
        service.cerrar(request);return Response(status=204)


def solicitudes(request):
    grant,identity=service.acceso(request)
    if request.headers.get('X-Acceso-Referencia')!=str(grant.pk):raise service.AccesoDenegado
    _exigir_seguimiento()
    if getattr(settings,'CREDIHOY_PUBLICO_SOLICITUDES_POR_API',False):
        # Do not silently fall back to a pilot DB or claim the associate has no cases.
        error=APIException('Consulta institucional de solicitudes no disponible')
        error.status_code=503
        raise error
    days=getattr(settings,'CREDIHOY_PUBLICO_RETENCION_DIAS',None)
    if type(days) is not int or not 1<=days<=3650:raise service.AccesoDenegado
    query = Solicitud.objects.select_related('solicitante').filter(producto=PRODUCTO,
        solicitante__tipo_identificacion='CC',solicitante__numero_identificacion=identity['numeroDocumento'],
        created_at__gte=timezone.now()-timedelta(days=days),metadatos__modo='CONSULTA_REAL_CONTROLADA')
    from . import publico_restringido as restringido
    if restringido.activo():query=query.filter(pk__in=settings.CREDIHOY_PUBLICO_ENSAYO['solicitudes'])
    return query


def estado_publico(s):
    from .funcional import leer_estado_funcional
    update=False
    try:
        packet=leer_estado_funcional(s);result=packet['resultado'];rows=result['reglas'] if result else []
        update=any(r['codigo']=='UNICO_FILA_9' and r.get('estadoEvaluacion')=='NO_CUMPLE' for r in rows)
        title='Actualiza tus datos' if update else 'Tu solicitud esta en revision'
        pending=[]
        if update:pending.append('Debes actualizar tus datos para continuar con tu solicitud.')
        if any(r.get('estadoEvaluacion') in {'NO_EVALUABLE','PENDIENTE_POLITICA'} for r in rows):
            pending.append('Necesitamos completar algunas validaciones antes de tomar una decision.')
        if any(r.get('estadoEvaluacion')=='NO_CUMPLE' and r['codigo']!='UNICO_FILA_9' for r in rows):
            pending.append('Hay requisitos pendientes que requieren revision con Congente.')
        completed=sum(r.get('estadoEvaluacion')=='CUMPLE' for r in rows)
    except Exception:
        logging.getLogger('credito.publico').warning('seguimiento_no_verificable solicitud=%s',s.pk)
        title='Estamos verificando tu solicitud';pending=['Comunicate con Congente para revisar el estado.'];completed=None
    institutional=institucional_publica(s)
    if institutional:
        pending=list(dict.fromkeys('Hay requisitos pendientes que requieren revision con Congente.'
            if p=='Necesitamos completar algunas validaciones antes de tomar una decision.' else p for p in pending))
    return {'referencia':str(s.pk),'titulo':title,'monto':str(s.monto_solicitado),
        'plazoMeses':s.plazo_solicitado,'periodicidad':'Mensual','fechaCreacion':s.created_at.isoformat(),
        'requisitosCumplidos':completed,'pendientes':pending,
        'siguientePaso':'Actualiza tus datos con Congente.' if update else 'Espera la revision de Congente.',
        'ofertaDisponible':False,'acciones':{'radicar':False,'firmar':False},
        'simulacion':simulacion_publica(s), 'institucional':institutional}


def institucional_publica(s):
    """Only verified institutional evidence; never a local rule/score or task label."""
    from apps.xcore_consumo.models import SolicitudConsumo
    from .linix_estado import presentar, VERSION_INSTITUCIONAL
    try:
        snapshot=presentar(s)
    except SolicitudConsumo.DoesNotExist:
        return None
    except ValueError:
        from .publico_institucional import NoDisponible
        raise NoDisponible from None
    if not snapshot or snapshot.get('versionContrato')!=VERSION_INSTITUCIONAL:
        return None
    counts=snapshot['incidencias']
    if any(v is None for v in counts.values()):
        incident='NO_DISPONIBLE'
    elif counts['enTareasActivas']:
        incident='EN_REVISION'
    elif counts['registradas']:
        incident='ANTECEDENTES_SIN_PENDIENTE_ACTIVO'
    else:
        incident='SIN_INCIDENCIAS_REGISTRADAS'
    return {'estado':'APROBADA_PENDIENTE_FIRMA','fechaDecision':snapshot['aprobacion']['fecha'],
            'actualizadoAt':snapshot['actualizadoAt'],'documentos':'NO_ACREDITADOS',
            'firma':'PENDIENTE','incidencias':incident}


def simulacion_publica(s):
    """Project only stored, current 9C results after the caller's owner filter."""
    from .cotizacion import leer_cotizacion_guardada
    try:
        p=leer_cotizacion_guardada(s,str(uuid4()))
        if not p['disponible']:return None
        manual=p['fuente']=='LINIX_EJECUCION_MANUAL'
        if not manual and (p['fuente']!='LINIX_CALCULO_CUOTA' or p['ambiente']!='PRODUCCION'):return None
        return {'cuota':p['cuotaInstitucional'],'tasaNominalNamv':p['condicionesSolicitadas']['tasaNominalNamv'],
            'origen':'REFERENCIA_INSTITUCIONAL_REGISTRADA' if manual else 'CALCULO_INSTITUCIONAL',
            'ofertaContractual':False,'seguroDisponible':False,'calendarioDisponible':False}
    except Exception:
        # Unavailable or stale data must never become a fabricated quote.
        return None


class SolicitudesView(PublicView):
    http_method_names=['get','options']
    def get(self,request):
        if getattr(settings,'CREDIHOY_PUBLICO_SOLICITUDES_POR_API',False):
            return Response(_seguimiento_institucional(request))
        return Response({'sesionReferencia':request.headers.get('X-Acceso-Referencia'),
            'solicitudes':[estado_publico(s) for s in solicitudes(request).order_by('-created_at')[:20]]})


class SolicitudView(PublicView):
    http_method_names=['get','options']
    def get(self,request,solicitud_id):
        if getattr(settings,'CREDIHOY_PUBLICO_SOLICITUDES_POR_API',False):
            packet=_seguimiento_institucional(request,solicitud_id)
            if len(packet['solicitudes'])!=1:raise Http404
            return Response(packet['solicitudes'][0])
        s=solicitudes(request).filter(pk=solicitud_id).first()
        if s is None:raise Http404
        return Response(estado_publico(s))


def _seguimiento_institucional(request,solicitud_id=None):
    grant,_=service.acceso(request)
    if request.headers.get('X-Acceso-Referencia')!=str(grant.pk):raise service.AccesoDenegado
    _exigir_seguimiento()
    from . import publico_institucional as wire
    try:
        p=wire.seguimiento(grant,solicitud_id)
        return {'sesionReferencia':str(grant.pk),'solicitudes':p['solicitudes']}
    except wire.NoEncontrado:
        raise Http404 from None
    except Exception:
        error=APIException('Consulta institucional de solicitudes no disponible')
        error.status_code=503
        raise error from None


def _exigir_seguimiento():
    if getattr(settings,'CREDIHOY_SEGUIMIENTO_ENABLED',True) is not True:
        error=APIException('Consulta de solicitudes no habilitada')
        error.status_code=503
        raise error
