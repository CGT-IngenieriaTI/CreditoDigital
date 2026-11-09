"""Minimal read contract to mount only in the authorized data-owning service."""
from django.db import DatabaseError
from . import publico_seguimiento as owner
from . import publico_institucional as wire
from .publico_s2s import InstitucionalView


class SolicitudesTitularView(InstitucionalView):
    def authorize_request(self,request):
        import time
        started=time.monotonic()
        try:self.rid,self.identity=owner.authorize(request)
        except Exception as error:
            registrar_rechazo_saneado(request,error,None,started,'validacion_sesion')
            raise

    def post(self,request):
        import time
        started=time.monotonic()
        try:
            if not owner.claim_delegation(self.rid):raise ValueError('DELEGACION_YA_UTILIZADA')
            if 'radicacion' in request.data:
                from .cotizacion import operacion_radicacion_titular
                result=operacion_radicacion_titular(self.identity,request.data['radicacion'],self.rid)
                return self.packet(sesionReferencia=request.data['accesoId'],radicacion=wire.project_radication(result))
            if 'autorizacion' in request.data:
                from .consentimiento_real import operar_autogestion,operar_contractual
                action=request.data['autorizacion']
                operation=operar_contractual if type(action) is dict and str(action.get('accion','')).startswith('contractual_') else operar_autogestion
                result=operation(self.identity,action)
                return self.packet(sesionReferencia=request.data['accesoId'],autorizacion=wire.project_authorization(result))
            if 'cotizacion' in request.data:
                from .simulacion_lectura import es_simulacion, operar_simulacion
                if es_simulacion(request.data['cotizacion']):
                    result=operar_simulacion(self.identity,request.data['cotizacion'],self.rid)
                    return self.packet(sesionReferencia=request.data['accesoId'],cotizacion=wire.project_simulation(result))
                from .cotizacion import operacion_titular
                result=operacion_titular(self.identity,request.data['cotizacion'],self.rid)
                return self.packet(sesionReferencia=request.data['accesoId'],cotizacion=wire.project_quote(result))
            if 'borrador' in request.data:
                from .autoservicio import operar
                from .progreso_privacidad import presentar
                result=operar(self.identity,request.data['borrador'])
                return self.packet(sesionReferencia=request.data['accesoId'],borrador=result,
                    avisoTemporal=presentar(self.identity))
            rows=owner.read_owned(self.identity,request.data)
            for row in rows:
                try:scope=wire.alcance_contractual(row['referencia'],self.identity)
                except wire.NoDisponible:continue
                row['formalizacionStaging']={'ambiente':'STAGING','documento':'COLO-FO.001','version':'13',
                    'paginas':3,'disponibleHasta':scope['venceEn'],'firmaProductivaAcreditada':False}
                if 'documentoPrueba' in scope:
                    row['formalizacionStaging'].update(documento=scope['documentoPrueba']['codigo'],
                        version=scope['documentoPrueba']['version'],paginas=scope['documentoPrueba']['paginas'])
        except Exception as error:
            registrar_rechazo_saneado(request,error,self.rid,started,'operacion')
            if isinstance(error,DatabaseError):raise wire.NoDisponible from None
            raise
        owner.audit_read(self.rid,len(rows))
        return self.packet(sesionReferencia=request.data['accesoId'],solicitudes=rows)


def registrar_rechazo_saneado(request,error,rid,started,etapa):
    import logging,re,traceback,time
    from uuid import UUID
    from django.http import Http404
    cause=error.__cause__ or error
    try:payload=request.data
    except Exception:payload={}
    action=payload.get('autorizacion',{}).get('accion') if type(payload) is dict and type(payload.get('autorizacion')) is dict else None
    try:rid=str(UUID(str(rid or request.headers.get('X-Request-ID'))))
    except (AttributeError,TypeError,ValueError):rid=None
    if action not in {'presentar','aceptar','consultar','ver_firma','verificar_firmante',
            'consultar_complemento','guardar_complemento','centrales_presentar',
            'centrales_enviar','centrales_resultado','centrales_consultar'}:action='otra'
    code=getattr(error,'codigo',None)
    if type(code) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{1,100}',code):code='ERROR_TECNICO'
    if type(error) is ValueError and re.fullmatch(r'(?:AUTORIZACION|DOCUMENTO|PRESENTACION|CUSTODIA|CONSENTIMIENTO|CENTRALES|IDENTIDAD|ETAPA)_[A-Z0-9_]{1,90}',str(error)):
        code=str(error)
    points=[{'archivo':p.filename.replace('\\','/').rsplit('/',1)[-1],'linea':p.lineno,'funcion':p.name}
        for p in traceback.extract_tb(error.__traceback__)[-6:]]
    status=503 if isinstance(error,(DatabaseError,wire.NoDisponible)) else 404 if isinstance(error,(Http404,wire.NoEncontrado)) else 403
    if rid is not None:
        from .publico_controles import guardar_traza_saneada
        guardar_traza_saneada({'codigo':code,'etapa':etapa.upper(),'correlationId':rid,
            'httpStatus':status,'servicio':'CREDIHOY_TITULAR','duracionMs':round((time.monotonic()-started)*1000),
            'tipo':type(error).__name__,'sqlstate':getattr(cause,'sqlstate',None) or getattr(cause,'pgcode',None),'puntos':points})
    logging.getLogger('credito.publico').warning('credihoy_operacion_rechazada servicio=propietario etapa=%s accion=%s tipo=%s codigo=%s sqlstate=%s puntos=%s requestId=%s http=%s duracionMs=%s',
        etapa,action,type(error).__name__,code,getattr(cause,'sqlstate',None) or getattr(cause,'pgcode',None),points,rid,status,round((time.monotonic()-started)*1000))
