"""S2S-only identity operations. Never mount these URLs on the Committee pilot."""
import hmac
import json
import re
from uuid import UUID, uuid4, uuid5

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.xcore_consumo.public_access_models import (
    AccesoPublicoCredihoy as Acceso, OperacionVerifyCredihoy as Operacion)
from apps.xcore_consumo.services.otp_crypto import decrypt_text,encrypt_text
from apps.xcore_consumo.services.twilio_verify import TwilioVerifyClient
from . import publico as access, publico_controles as controls, publico_institucional as wire
from .publico_s2s import InstitucionalView
from .simulacion_lectura import es_simulacion, simulador_habilitado


def state_alias():
    return getattr(settings, 'CREDIHOY_ACCESO_STATE_DB_ALIAS', 'default')


def _identity(a):
    if a.revocado or not controls.identidad_permitida(a.identidad_hmac):
        raise access.AccesoDenegado
    data = json.loads(decrypt_text(a.datos_cifrados))
    if access.huella('identidad', 'CC:' + data['numeroDocumento']) != a.identidad_hmac:
        raise access.AccesoDenegado
    return data


def _verify(request, rid, *, sending):
    payload = request.data
    required = {'accesoId'} if sending else {'accesoId', 'numeroIntento', 'codigo'}
    if type(payload) is not dict or set(payload) != required:
        raise access.AccesoDenegado
    key = UUID(payload['accesoId'])
    number = 0 if sending else payload['numeroIntento']
    if not sending and (type(number) is not int or not 1 <= number <= 5
            or type(payload['codigo']) is not str or not re.fullmatch(r'[0-9]{6}', payload['codigo'])):
        raise access.AccesoDenegado
    expected = uuid5(key, 'ENVIO' if sending else 'COMPROBAR:' + str(number))
    if str(expected) != rid:
        raise access.AccesoDenegado
    alias = state_alias()
    from django.db import connections
    if connections[alias].in_atomic_block:
        raise wire.NoDisponible
    with transaction.atomic(using=alias):
        a = Acceso.objects.using(alias).select_for_update().get(pk=key)
        data = _identity(a)
        if (a.expira_at <= timezone.now() or a.estado != ('ENVIANDO' if sending else 'VERIFICANDO')
                or (not sending and (a.intentos != number or not a.verification_sid))):
            raise access.AccesoDenegado
        fingerprint = hmac.new(wire.secret(), wire.encode(payload), 'sha256').hexdigest()
        op, created = Operacion.objects.using(alias).get_or_create(pk=expected, defaults={
            'acceso_id': a.pk, 'accion': 'ENVIAR' if sending else 'COMPROBAR', 'peticion_hmac': fingerprint})
        if not hmac.compare_digest(op.peticion_hmac, fingerprint):
            raise access.AccesoDenegado
        if not created:
            if op.estado == 'FINALIZADA':
                return op.resultado
            raise wire.NoDisponible  # Unknown outcome: never repeat a provider operation.
        label = 'relay_envio' if sending else 'relay_comprobar'
        if not access.limite([(label, a.identidad_hmac, 3 if sending else 15)], using=alias):
            raise access.AccesoDenegado
        access.auditar(a, 'relay_reservado', accion=op.accion)
    try:
        client = TwilioVerifyClient(service_sid=settings.CREDIHOY_PUBLICO_VERIFY_SID)
        if sending:
            result = client.start_verification(data['contacto'], channel='sms' if data['canal'] == 'SMS' else 'email')
            if result.status != 'pending' or not re.fullmatch(r'VE[a-fA-F0-9]{32}', result.sid or ''):
                raise wire.NoDisponible
        else:
            result = client.check_verification(data['contacto'], payload['codigo'], verification_sid=a.verification_sid)
            if (result.sid != a.verification_sid or result.status not in ('pending', 'approved')
                    or (result.status == 'approved' and (result.to != data['contacto']
                        or result.channel != ('sms' if data['canal'] == 'SMS' else 'email')))):
                raise wire.NoDisponible
        answer = {'verificationSid': result.sid, 'estadoProveedor': result.status}
        a.refresh_from_db(using=alias)
        if a.revocado or a.expira_at <= timezone.now():
            raise wire.NoDisponible
        Operacion.objects.using(alias).filter(pk=expected).update(estado='FINALIZADA', resultado=answer)
        access.auditar(a, 'relay_finalizado', accion=op.accion, estado=result.status)
        return answer
    except Exception:
        Operacion.objects.using(alias).filter(pk=expected).update(estado='INCIERTA')
        raise wire.NoDisponible from None


class EnviarView(InstitucionalView):
    def post(self, request):
        return self.packet(**_verify(request, self.rid, sending=True))


class ComprobarView(InstitucionalView):
    def post(self, request):
        return self.packet(**_verify(request, self.rid, sending=False))


def _centrales_binding(a,token,operation):
    from django.core import signing
    from hashlib import sha256
    from datetime import datetime
    from .publico_seguimiento import key
    binding=signing.loads(token,key=key(),salt='credihoy-centrales-intencion-v1',max_age=600,fallback_keys=[])
    identity=_identity(a)
    if (binding.get('version')!='CENTRALES_OTP_V1' or binding.get('proposito')!='CONSULTA_CENTRALES'
            or binding.get('operacionId')!=operation or binding.get('sesionReferencia')!=str(a.pk)
            or binding.get('titularSha256')!=sha256(identity['numeroDocumento'].encode()).hexdigest()):
        raise access.AccesoDenegado
    accepted=datetime.fromisoformat(binding['aceptadoEn']);until=datetime.fromisoformat(binding['vigenteHasta'])
    now=timezone.now()
    if (accepted.utcoffset() is None or until.utcoffset() is None or not accepted<=now<until
            or a.revocado or a.estado!='VERIFICADO' or a.sesion_expira_at is None or now>=a.sesion_expira_at):
        raise access.AccesoDenegado
    return binding


def _centrales_verify(a,payload,rid):
    """Purpose-bound OTP using the existing relay journal, never the access OTP."""
    from hashlib import sha256
    from django.core import signing
    from datetime import datetime,timedelta
    from . import publico_seguimiento as owner
    if getattr(settings,'CREDIHOY_CENTRALES_OTP_ENABLED',False) is not True:raise wire.NoDisponible
    action=payload['autorizacion'];sending=action['accion']=='centrales_enviar'
    required={'operacion','accion','solicitudId','operacionId','presentacion','acepta'} if sending else {
        'operacion','accion','solicitudId','operacionId','presentacion','comprobacionId','codigo'}
    if type(action) is not dict or set(action)!=required:raise access.AccesoDenegado
    operation=str(UUID(action['operacionId']))
    if operation!=action['operacionId']:raise access.AccesoDenegado
    if sending:
        if action['acepta'] is not True:raise access.AccesoDenegado
        packet=owner.consult(a,_identity(a),payload,rid)
        token=packet['autorizacion']['centrales']['presentacion']
        if token is None and packet['autorizacion']['centrales']['estado'] in {'OTP_CONFIRMADA','RECHAZADA','INDETERMINADA'}:
            return packet
    else:
        if type(action['codigo']) is not str or not re.fullmatch('[0-9]{6}',action['codigo']):raise access.AccesoDenegado
        if str(UUID(action['comprobacionId']))!=action['comprobacionId']:raise access.AccesoDenegado
        token=action['presentacion']
    binding=_centrales_binding(a,token,operation)
    if binding['solicitudId']!=action['solicitudId']:raise access.AccesoDenegado
    alias=state_alias();send_id=uuid5(UUID(operation),'CENTRALES:ENVIAR')
    expected=send_id if sending else uuid5(UUID(operation),'CENTRALES:COMPROBAR:'+action['comprobacionId'])
    fingerprint=hmac.new(wire.secret(),wire.encode(action),'sha256').hexdigest()
    def open_result(op):
        result=json.loads(decrypt_text(op.resultado['evidenciaCifrada']))
        if result['vinculo']!=binding:raise access.AccesoDenegado
        return result
    def sealed(result):return {'proposito':'CONSULTA_CENTRALES','evidenciaCifrada':encrypt_text(wire.encode(result).decode())}
    cached=None
    with transaction.atomic(using=alias):
        current=Acceso.objects.using(alias).select_for_update().get(pk=a.pk)
        if current.revocado or current.estado!='VERIFICADO' or current.sesion_expira_at<=timezone.now():raise access.AccesoDenegado
        op,created=Operacion.objects.using(alias).get_or_create(pk=expected,defaults={'acceso_id':a.pk,
            'accion':'CENTRALES_ENVIO' if sending else 'CENTRALES_CHECK','peticion_hmac':fingerprint})
        if op.acceso_id!=a.pk or not hmac.compare_digest(op.peticion_hmac,fingerprint):raise access.AccesoDenegado
        if not created:
            if op.estado!='FINALIZADA':raise wire.NoDisponible
            cached=open_result(op)
        elif sending:
            if not access.limite([('centrales_envio',a.identidad_hmac,3)],using=alias):raise access.AccesoDenegado
        else:
            send=Operacion.objects.using(alias).select_for_update().get(pk=send_id,acceso_id=a.pk,estado='FINALIZADA')
            sent=open_result(send)
            if sent.get('bloqueada') or sent.get('intentos',0)>=3:raise access.AccesoDenegado
            if sent.get('enVerificacion'):raise wire.NoDisponible
            sent['intentos']=sent.get('intentos',0)+1
            sent['enVerificacion']=True
            send.resultado=sealed(sent);send.save(using=alias,update_fields=['resultado','updated_at'])
        if created:access.auditar(a,'centrales_otp_reservado',accion=op.accion)
    if cached is None:
        try:
            identity=_identity(a);contact=identity['contacto'];channel='sms' if identity['canal']=='SMS' else 'email'
            sid=settings.TWILIO_VERIFY_SID
            if not re.fullmatch('VA[a-fA-F0-9]{32}',sid):raise wire.NoDisponible
            client=TwilioVerifyClient(service_sid=sid)
            if sending:
                checked=client.start_verification(contact,channel=channel)
                accepted=datetime.fromisoformat(binding['aceptadoEn'])
                # Verify can reuse pending tokens; never accept an access verification SID.
                if (checked.status!='pending' or not re.fullmatch('VE[a-fA-F0-9]{32}',checked.sid or '')
                        or checked.to!=contact or checked.channel!=channel
                        or checked.date_created is None or checked.date_created<accepted-timedelta(seconds=5)
                        or Acceso.objects.using(alias).filter(verification_sid=checked.sid).exists()):raise wire.NoDisponible
                cached={'vinculo':binding,'verificationSid':checked.sid,'intentos':0,'bloqueada':False,'enVerificacion':False,
                    'estado':'ENVIADO','estadoProveedor':'pending','operacionVerify':str(expected),'confirmadoEn':None}
            else:
                checked=client.check_verification(contact,action['codigo'],verification_sid=sent['verificationSid'])
                if (checked.sid!=sent['verificationSid'] or checked.status not in {'pending','approved'}
                        or checked.to!=contact or checked.channel!=channel):raise wire.NoDisponible
                status='CONFIRMADA' if checked.status=='approved' else 'RECHAZADA' if sent['intentos']>=3 else 'ENVIADO'
                cached={**sent,'estado':status,'estadoProveedor':checked.status,'operacionVerify':str(expected),
                    'confirmadoEn':timezone.now().isoformat() if checked.status=='approved' else None,
                    'bloqueada':status in {'CONFIRMADA','RECHAZADA'},'enVerificacion':False}
            current.refresh_from_db(using=alias)
            if current.revocado or current.sesion_expira_at<=timezone.now():raise access.AccesoDenegado
            with transaction.atomic(using=alias):
                Operacion.objects.using(alias).filter(pk=expected,estado='RESERVADA').update(estado='FINALIZADA',resultado=sealed(cached))
                if not sending:Operacion.objects.using(alias).filter(pk=send_id).update(resultado=sealed(cached))
                access.auditar(a,'centrales_otp_finalizado',estado=cached['estado'])
        except Exception:
            Operacion.objects.using(alias).filter(pk=expected).update(estado='INCIERTA')
            if not sending:
                sent['bloqueada']=True;sent['enVerificacion']=False
                Operacion.objects.using(alias).filter(pk=send_id).update(resultado=sealed(sent))
            proof={'vinculo':binding,'estado':'INDETERMINADA',
                'verificationReferencia':sha256(sent['verificationSid'].encode()).hexdigest() if not sending else None,
                'operacionVerify':str(expected),'confirmadoEn':None,'estadoProveedor':None}
            _centrales_resultado(a,payload,proof)
            raise wire.NoDisponible from None
    proof={k:cached[k] for k in ('vinculo','estado','operacionVerify','confirmadoEn','estadoProveedor')}
    proof['verificationReferencia']=sha256(cached['verificationSid'].encode()).hexdigest()
    return _centrales_resultado(a,payload,proof)


def _centrales_resultado(a,payload,proof):
    from django.core import signing
    from . import publico_seguimiento as owner
    token=signing.dumps(proof,key=owner.key(),salt='centrales-otp-proveedor-v1')
    packet={'accesoId':str(a.pk),'navegador':payload['navegador'],'autorizacion':{
        'operacion':'autorizacion','accion':'centrales_resultado','solicitudId':proof['vinculo']['solicitudId'],
        'comprobacion':token}}
    return owner.consult(a,_identity(a),packet,str(uuid4()))


class SeguimientoView(InstitucionalView):
    def authorize_request(self,request):
        if type(request.data) is dict and request.data.get('operacion') in {'verify_contractual','verify_contractual_preflight'}:
            self.rid=_autorizar_verify_contractual(request)
        else:super().authorize_request(request)

    def post(self, request):
        if type(request.data) is dict and request.data.get('operacion')=='verify_contractual_preflight':
            from hashlib import sha256
            p=request.data
            if set(p)!={'operacion','solicitudId','pdfSha256'}:raise access.AccesoDenegado
            scope=wire.alcance_contractual(p['solicitudId'])
            sid=settings.CREDIHOY_OTP_CONTRACTUAL_VERIFY_SID
            if (scope['pdfSha256']!=p['pdfSha256'] or not re.fullmatch('VA[a-fA-F0-9]{32}',sid)
                    or sid.lower() in {settings.CREDIHOY_PUBLICO_VERIFY_SID.lower(),settings.TWILIO_VERIFY_SID.lower()}
                    or not getattr(settings,'TWILIO_VERIFY_API_KEY_SECRET','')):raise wire.NoDisponible
            return self.packet(configuracionContractual={'ambiente':'STAGING','serviceSha256':sha256(sid.encode()).hexdigest(),
                'SIDIndependiente':True,'SMS':0,'firma':0})
        if type(request.data) is dict and request.data.get('operacion')=='verify_contractual':
            return self.packet(verifyContractual=_verify_contractual(request.data,self.rid))
        if type(request.data) is dict and 'radicacion' in request.data:
            from . import publico_seguimiento as owner
            payload=request.data
            if (getattr(settings,'CREDIHOY_RADICACION_TITULAR_ENABLED',False) is not True
                    or getattr(settings,'CREDIHOY_SEGUIMIENTO_TRANSPORTE','')!='HTTP_PROPIETARIO'):
                raise wire.NoDisponible
            if set(payload)!={'accesoId','navegador','radicacion'} or type(payload['radicacion']) is not dict:
                raise access.AccesoDenegado
            a=Acceso.objects.using(state_alias()).get(pk=UUID(payload['accesoId']),
                estado='VERIFICADO',revocado=False,sesion_expira_at__gt=timezone.now())
            if type(payload['navegador']) is not str or not hmac.compare_digest(payload['navegador'],a.navegador_hmac):
                raise access.AccesoDenegado
            readonly=payload['radicacion'].get('operacion')=='consultar'
            if not access.limite([('radicacion_lectura' if readonly else 'radicacion_personal',str(a.pk),120 if readonly else 10)],using=state_alias()):
                raise access.AccesoDenegado
            packet=owner.consult(a,_identity(a),payload,self.rid)
            a.refresh_from_db(using=state_alias())
            if a.revocado or a.estado!='VERIFICADO' or a.sesion_expira_at<=timezone.now():raise access.AccesoDenegado
            if packet.get('sesionReferencia')!=str(a.pk):raise wire.NoDisponible
            return self.packet(sesionReferencia=str(a.pk),radicacion=wire.project_radication(packet['radicacion']))
        if type(request.data) is dict and 'autorizacion' in request.data:
            from . import publico_seguimiento as owner
            payload=request.data
            if (getattr(settings,'CREDIHOY_AUTORIZACION_TITULAR_ENABLED',False) is not True
                    or getattr(settings,'CREDIHOY_SEGUIMIENTO_TRANSPORTE','')!='HTTP_PROPIETARIO'):
                raise wire.NoDisponible
            if set(payload)!={'accesoId','navegador','autorizacion'}:raise access.AccesoDenegado
            a=Acceso.objects.using(state_alias()).get(pk=UUID(payload['accesoId']),
                estado='VERIFICADO',revocado=False,sesion_expira_at__gt=timezone.now())
            if type(payload['navegador']) is not str or not hmac.compare_digest(payload['navegador'],a.navegador_hmac):
                raise access.AccesoDenegado
            if not access.limite([('autorizacion',str(a.pk),30)],using=state_alias()):raise access.AccesoDenegado
            if type(payload['autorizacion']) is dict and payload['autorizacion'].get('accion') in {'centrales_enviar','centrales_comprobar'}:
                packet=_centrales_verify(a,payload,self.rid)
                return self.packet(sesionReferencia=str(a.pk),autorizacion=wire.project_authorization(packet['autorizacion']))
            firmante=None
            if type(payload['autorizacion']) is dict and payload['autorizacion'].get('accion')=='contractual_presentar':
                from .publico_proveedores import acreditar_firmante
                action=payload['autorizacion'];scope=wire.alcance_contractual(action['solicitudId'],
                    {**_identity(a),'_sesionExpiraAt':a.sesion_expira_at.isoformat()})
                if set(action)!={'operacion','accion','solicitudId','clave'}:raise access.AccesoDenegado
                firmante=acreditar_firmante(a,solicitud_id=action['solicitudId'],pdf_sha256=scope['pdfSha256'],
                    clave=action['clave'],request_id=self.rid,autoriza_firma=False,presentacion_contractual=True)
            if type(payload['autorizacion']) is dict and payload['autorizacion'].get('accion')=='verificar_firmante':
                from .publico_seguimiento import verificar_presentacion_firma
                from .publico_proveedores import acreditar_firmante
                action=payload['autorizacion']
                if set(action)!={'operacion','accion','solicitudId','clave','presentacion','autorizaFirma'}:
                    raise access.AccesoDenegado
                p=verificar_presentacion_firma(action['presentacion'],solicitud_id=action['solicitudId'],
                    sesion=a.pk,clave=action['clave'])
                firmante=acreditar_firmante(a,solicitud_id=action['solicitudId'],pdf_sha256=p['pdfSha256'],
                    clave=action['clave'],request_id=self.rid,autoriza_firma=action['autorizaFirma'])
            packet=owner.consult(a,_identity(a),payload,self.rid,**({'firmante':firmante} if firmante is not None else {}))
            a.refresh_from_db(using=state_alias())
            if a.revocado or a.estado!='VERIFICADO' or a.sesion_expira_at<=timezone.now():raise access.AccesoDenegado
            if packet.get('sesionReferencia')!=str(a.pk):raise wire.NoDisponible
            return self.packet(sesionReferencia=str(a.pk),autorizacion=wire.project_authorization(packet['autorizacion']))
        if type(request.data) is dict and 'cotizacion' in request.data:
            from . import publico_seguimiento as owner
            payload=request.data
            local = es_simulacion(payload['cotizacion'])
            if (not (simulador_habilitado() if local else
                    getattr(settings,'CREDIHOY_COTIZACION_TITULAR_ENABLED',False) is True)
                    or getattr(settings,'CREDIHOY_SEGUIMIENTO_TRANSPORTE','')!='HTTP_PROPIETARIO'):
                raise wire.NoDisponible
            if set(payload)!={'accesoId','navegador','cotizacion'}:raise access.AccesoDenegado
            a=Acceso.objects.using(state_alias()).get(pk=UUID(payload['accesoId']),
                estado='VERIFICADO',revocado=False,sesion_expira_at__gt=timezone.now())
            if type(payload['navegador']) is not str or not hmac.compare_digest(payload['navegador'],a.navegador_hmac):
                raise access.AccesoDenegado
            identity=_identity(a)
            limits=([('simulacion',str(a.pk),120),('simulacion_titular',a.identidad_hmac,240)]
                if local else [('cotizacion',str(a.pk),12)])
            if not access.limite(limits,using=state_alias()):raise access.AccesoDenegado
            packet=owner.consult(a,identity,payload,self.rid)
            a.refresh_from_db(using=state_alias())
            if a.revocado or a.estado!='VERIFICADO' or a.sesion_expira_at<=timezone.now():raise access.AccesoDenegado
            if packet.get('sesionReferencia')!=str(a.pk):raise wire.NoDisponible
            return self.packet(sesionReferencia=str(a.pk),
                cotizacion=(wire.project_simulation if local else wire.project_quote)(packet['cotizacion']))
        if type(request.data) is dict and 'borrador' in request.data:
            from .autoservicio import habilitar
            from . import publico_seguimiento as owner
            payload=request.data
            if set(payload)!={'accesoId','navegador','borrador'}:raise access.AccesoDenegado
            a=Acceso.objects.using(state_alias()).get(pk=UUID(payload['accesoId']),
                estado='VERIFICADO',revocado=False,sesion_expira_at__gt=timezone.now())
            habilitar(str(a.pk),expira_at=a.sesion_expira_at)
            if type(payload['navegador']) is not str or not hmac.compare_digest(payload['navegador'],a.navegador_hmac):
                raise access.AccesoDenegado
            identity=_identity(a)
            if not access.limite([('borrador',str(a.pk),60)],using=state_alias()):raise access.AccesoDenegado
            packet=owner.consult(a,identity,payload,self.rid)
            if packet.get('sesionReferencia')!=str(a.pk) or 'borrador' not in packet:raise wire.NoDisponible
            a.refresh_from_db(using=state_alias())
            if a.revocado or a.estado!='VERIFICADO' or a.sesion_expira_at<=timezone.now():raise access.AccesoDenegado
            access.auditar(a,'borrador_consultado')
            response={'sesionReferencia':str(a.pk),'borrador':wire.project_draft(packet['borrador'])}
            if 'avisoTemporal' in packet:response['avisoTemporal']=wire.project_progress_notice(packet['avisoTemporal'])
            return self.packet(**response)
        if getattr(settings,'CREDIHOY_SEGUIMIENTO_ENABLED',True) is not True:
            raise wire.NoDisponible
        payload = request.data
        if (type(payload) is not dict or set(payload) not in ({'accesoId', 'navegador'},
                {'accesoId', 'navegador', 'solicitudId'}) or type(payload['navegador']) is not str):
            raise access.AccesoDenegado
        a = Acceso.objects.using(state_alias()).get(pk=UUID(payload['accesoId']),
            estado='VERIFICADO', revocado=False, sesion_expira_at__gt=timezone.now())
        if not hmac.compare_digest(payload['navegador'], a.navegador_hmac):
            raise access.AccesoDenegado
        identity = _identity(a)
        if getattr(settings,'CREDIHOY_SEGUIMIENTO_TRANSPORTE','')=='HTTP_PROPIETARIO':
            from . import publico_seguimiento as owner
            if not access.limite([('seguimiento',str(a.pk),120)],using=state_alias()):
                raise access.AccesoDenegado
            packet=owner.consult(a,identity,payload,self.rid)
            if (packet.get('sesionReferencia')!=str(a.pk) or type(packet.get('solicitudes')) is not list
                    or len(packet['solicitudes'])>20):raise wire.NoDisponible
            rows=[wire.project_row(v) for v in packet['solicitudes']]
            if (len({v['referencia'] for v in rows})!=len(rows)
                    or ('solicitudId' in payload and (len(rows)!=1
                        or rows[0]['referencia']!=payload['solicitudId']))):raise wire.NoDisponible
            a.refresh_from_db(using=state_alias())
            if a.revocado or a.estado!='VERIFICADO' or a.sesion_expira_at<=timezone.now():
                raise access.AccesoDenegado
            access.auditar(a,'seguimiento_consultado',cantidad=len(rows))
            return self.packet(sesionReferencia=str(a.pk),solicitudes=rows)
        if not access.limite([('seguimiento', str(a.pk), 120)], using=state_alias()):
            raise access.AccesoDenegado
        from .publico_seguimiento import read_owned
        rows=read_owned(identity,payload)
        access.auditar(a, 'seguimiento_consultado', cantidad=len(rows))
        return self.packet(sesionReferencia=str(a.pk), solicitudes=rows)


def _autorizar_verify_contractual(request):
    from hashlib import sha256
    from django.core import signing
    from .publico_seguimiento import key
    if getattr(settings,'CREDIHOY_CONTRACTUAL_RELE_ENABLED',False) is not True:raise wire.NoDisponible
    rid=request.headers.get('X-Request-ID','');authorization=request.headers.get('Authorization','')
    if (request.method!='POST' or not request.is_secure() or request.content_type!='application/json'
            or request.path!=wire.BASE+'seguimiento/' or len(request.body)>4096
            or str(UUID(rid))!=rid or not authorization.startswith('Bearer chcontractual.')
            or len(authorization)>8192):raise access.AccesoDenegado
    proof=signing.loads(authorization[len('Bearer chcontractual.'):],key=key(),
        salt='credihoy-verify-contractual-rele-v1',max_age=30,fallback_keys=[])
    if (type(proof) is not dict or set(proof)!={'version','aud','requestId','bodySha256'}
            or proof['version']!='CREDIHOY_VERIFY_CONTRACTUAL_RELE_V1' or proof['aud']!=request.path
            or proof['requestId']!=rid or proof['bodySha256']!=sha256(request.body).hexdigest()):
        raise access.AccesoDenegado
    return rid



def _verify_contractual(payload,rid):
    """Existing relay SDK and durable Verify journal, separate from login and centrals."""
    from datetime import datetime,timedelta
    from hashlib import sha256
    from apps.xcore_consumo.services.otp import normalize_sms_destination
    import logging
    if any(logging.getLogger(name).isEnabledFor(logging.INFO) for name in ('twilio','twilio.http_client')):
        raise wire.NoDisponible
    required={'operacion','accion','solicitudId','sesionReferencia','operacionFirmaId','otpId','pdfSha256',
        'aceptacionSha256','serviceSha256','contactoSha256','aceptadoEn'}
    if type(payload) is not dict:raise access.AccesoDenegado
    sending=payload.get('accion')=='enviar'
    if (payload.get('accion') not in {'enviar','comprobar'}
            or set(payload)!=(required if sending else required|{'codigo','verificationSid'})
            or payload['operacion']!='verify_contractual'
            or any(str(UUID(payload[k]))!=payload[k] for k in ('solicitudId','sesionReferencia','operacionFirmaId'))
            or not re.fullmatch('[1-9][0-9]{0,18}',str(payload['otpId']))
            or any(not re.fullmatch('[a-f0-9]{64}',payload[k]) for k in
                ('pdfSha256','aceptacionSha256','serviceSha256','contactoSha256'))):raise access.AccesoDenegado
    if not sending and (not re.fullmatch('[0-9]{6}',payload['codigo'])
            or not re.fullmatch('VE[a-fA-F0-9]{32}',payload['verificationSid'])):raise access.AccesoDenegado
    scope=wire.alcance_contractual(payload['solicitudId'])
    if payload['pdfSha256']!=scope['pdfSha256']:raise access.AccesoDenegado
    service=settings.CREDIHOY_OTP_CONTRACTUAL_VERIFY_SID
    if (not re.fullmatch('VA[a-fA-F0-9]{32}',service)
            or service.lower() in {settings.CREDIHOY_PUBLICO_VERIFY_SID.lower(),settings.TWILIO_VERIFY_SID.lower()}
            or sha256(service.encode()).hexdigest()!=payload['serviceSha256']):raise access.AccesoDenegado
    accepted=datetime.fromisoformat(payload['aceptadoEn']);now=timezone.now()
    if accepted.utcoffset() is None or not 0<=(now-accepted).total_seconds()<600:raise access.AccesoDenegado
    alias=state_alias();fingerprint=hmac.new(wire.secret(),wire.encode(payload),'sha256').hexdigest()
    with transaction.atomic(using=alias):
        grant=Acceso.objects.using(alias).select_for_update().get(pk=payload['sesionReferencia'],
            estado='VERIFICADO',revocado=False,sesion_expira_at__gt=now)
        identity=_identity(grant)
        if sha256(identity['numeroDocumento'].encode()).hexdigest()!=scope['titularReferencia']:raise access.AccesoDenegado
        destination=normalize_sms_destination(identity['celular'])
        if sha256(destination.encode()).hexdigest()!=payload['contactoSha256']:raise access.AccesoDenegado
        operation,created=Operacion.objects.using(alias).get_or_create(pk=UUID(rid),defaults={
            'acceso_id':grant.pk,'accion':'CONTR_ENVIO' if sending else 'CONTR_CHECK','peticion_hmac':fingerprint})
        if operation.acceso_id!=grant.pk or not hmac.compare_digest(operation.peticion_hmac,fingerprint):raise access.AccesoDenegado
        if not created:
            if operation.estado!='FINALIZADA':raise wire.NoDisponible
            return json.loads(decrypt_text(operation.resultado['evidenciaCifrada']))
        label='contractual_enviar' if sending else 'contractual_comprobar'
        limit_key=payload['sesionReferencia']+':'+payload['pdfSha256']
        if not access.limite([(label,limit_key,1 if sending else 3)],using=alias):raise access.AccesoDenegado
        access.auditar(grant,'contractual_verify_reservado',accion=operation.accion)
    try:
        client=TwilioVerifyClient(service_sid=service)
        if sending:
            reply=client.start_verification(destination,channel='sms')
            if (reply.status!='pending' or reply.date_created is None
                    or reply.date_created<accepted-timedelta(seconds=5)):raise wire.NoDisponible
        else:reply=client.check_verification(destination,payload['codigo'],verification_sid=payload['verificationSid'])
        if (not re.fullmatch('VE[a-fA-F0-9]{32}',reply.sid or '') or reply.status not in {'pending','approved'}
                or reply.to!=destination or reply.channel!='sms'
                or not sending and reply.sid!=payload['verificationSid']):raise wire.NoDisponible
        result={'verificationSid':reply.sid,'estadoProveedor':reply.status}
        grant.refresh_from_db(using=alias)
        if grant.revocado or grant.sesion_expira_at<=timezone.now():raise wire.NoDisponible
        Operacion.objects.using(alias).filter(pk=operation.pk,estado='RESERVADA').update(
            estado='FINALIZADA',resultado={'evidenciaCifrada':encrypt_text(json.dumps(result,sort_keys=True))})
        access.auditar(grant,'contractual_verify_finalizado',accion=operation.accion)
        return result
    except Exception:
        Operacion.objects.using(alias).filter(pk=operation.pk,estado='RESERVADA').update(estado='INCIERTA')
        raise wire.NoDisponible from None

