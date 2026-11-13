"""AutenTIC Sign Cargas Masivas; provider OTP, not Signing Core or Twilio."""
import base64
from dataclasses import dataclass,field
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import time
from urllib.parse import quote,urlsplit
from uuid import UUID,uuid5,NAMESPACE_URL

import requests

from .contratos import AutenticError,TOKEN_URL,validar_pdf,_config_unica
from .operaciones import RegistroFirmasExpediente

HOSTS={'QA':'https://qa-mpl.autenticsign.com','PRODUCCION':'https://mpl.autenticsign.com'}
LIMIT_PDF=5_000_000


def vigencia_token(token,expires_in,*,ahora=None):
    """Cache lifetime only: OAuth authenticates the token; JWT exp limits reuse."""
    if type(expires_in) not in (int,float) or expires_in<=0:
        raise ErrorCargasMasivas('MPL_TOKEN_INVALIDO',http=200)
    try:
        parts=token.split('.')
        if len(parts)!=3:return min(expires_in,30)
        claims=json.loads(base64.urlsafe_b64decode(parts[1]+'='*((-len(parts[1]))%4)))
        expiration=claims['exp']
        if type(expiration) not in (int,float):raise ValueError
        return max(0,min(expires_in,expiration-(time.time() if ahora is None else ahora)))
    except (ValueError,TypeError,KeyError,UnicodeError):
        raise ErrorCargasMasivas('MPL_TOKEN_EXPIRACION_INVALIDA',http=200) from None


class ErrorCargasMasivas(AutenticError):
    def __init__(self,codigo,*,http=None,codigo_proveedor=None,descripcion=''):
        super().__init__(codigo)
        self.http=http;self.codigo_proveedor=codigo_proveedor;self.descripcion=descripcion

    def diagnostico(self):
        return {'codigo':self.codigo,'http':self.http,'codigoProveedor':self.codigo_proveedor,
            'descripcion':self.descripcion}


@dataclass(frozen=True)
class ConfiguracionCargasMasivas:
    ambiente:str
    client_id:str=field(repr=False)
    client_secret:str=field(repr=False)
    enterprise_id:str=field(default='',repr=False)
    sender_email:str=field(default='',repr=False)
    sender_identification:str=field(default='',repr=False)
    notification_email:str=field(default='',repr=False)
    timeout:tuple=(5,60)
    hosts_descarga:tuple=()

    def __post_init__(self):
        if self.ambiente not in HOSTS:raise ErrorCargasMasivas('MPL_AMBIENTE_INVALIDO')
        for value in (self.client_id,self.client_secret):
            if type(value) is not str or not value.strip() or any(ord(c)<32 for c in value):
                raise ErrorCargasMasivas('MPL_PERFIL_INCOMPLETO')
        if (type(self.timeout) is not tuple or len(self.timeout)!=2
                or any(type(v) not in (int,float) or not 0<v<=120 for v in self.timeout)):
            raise ErrorCargasMasivas('MPL_TIMEOUT_INVALIDO')
        if type(self.hosts_descarga) is not tuple or any(not re.fullmatch('[a-z0-9.-]+',v) for v in self.hosts_descarga):
            raise ErrorCargasMasivas('MPL_HOST_DESCARGA_INVALIDO')

    @property
    def base_url(self):return HOSTS[self.ambiente]

    @property
    def audience(self):return urlsplit(self.base_url).hostname

    @classmethod
    def desde_archivo(cls,path):
        """Runtime profile: credentials are references to existing secret files."""
        path=Path(path)
        data=json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=_config_unica)
        credentials={key:(path.parent/data[key+'_file']).read_text(encoding='utf-8-sig').rstrip('\r\n')
            for key in ('client_id','client_secret')}
        return cls(data['ambiente'],**credentials,enterprise_id=data['enterprise_id'],
            sender_email=data['sender_email'],sender_identification=data['sender_identification'],
            notification_email=data['notification_email'],timeout=tuple(data.get('timeout',[5,60])),
            hosts_descarga=tuple(data.get('hosts_descarga',[])))


def _identifier(value):
    if type(value) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}',value):
        raise ErrorCargasMasivas('MPL_IDENTIFICADOR_PROVEEDOR_REQUERIDO')
    return value


def preparar_carga(config,pdf,filename,signer,reference):
    validar_pdf(pdf)
    if any(type(value) is not str or not value.strip() for value in (config.enterprise_id,
            config.sender_email,config.sender_identification,config.notification_email)):
        raise ErrorCargasMasivas('MPL_DATOS_REMITENTE_REQUERIDOS')
    if len(pdf)>LIMIT_PDF:raise ErrorCargasMasivas('MPL_PDF_EXCEDE_5_MB')
    if (type(filename) is not str or not filename.lower().endswith('.pdf') or filename!=Path(filename).name
            or any(c in filename for c in ('/','\\','\r','\n'))):
        raise ErrorCargasMasivas('MPL_FILENAME_PDF_REQUERIDO')
    required={'name','lastName','documentType','identification','email','dial','phone'}
    if (type(signer) is not dict or set(signer)!=required
            or any(type(v) is not str or not v.strip() or any(ord(c)<32 for c in v) for v in signer.values())
            or type(reference) is not str or not re.fullmatch('[A-Za-z0-9_.:-]{1,128}',reference)):
        raise ErrorCargasMasivas('MPL_FIRMANTE_O_REFERENCIA_INVALIDOS')
    # Preserve the literal types used in the provider's official Postman example.
    return {'sendCompletionNotification':'true','emailForNotification':config.notification_email,
        'processes':[{'enterpriseId':config.enterprise_id,'senderEmail':config.sender_email,
            'senderIdentification':config.sender_identification,
            'signers':[{**signer,'role':'SIGNER','authMethods':['OTP']}],
            'documents':[{'content':base64.b64encode(pdf).decode('ascii'),'fileName':filename}],
            'subject':'CrediHoy | '+reference,'message':'Revise y firme el documento presentado. Referencia: '+reference,
            'order':'true','expirationDate':'','sendEmail':'true'}]}


@dataclass(frozen=True)
class RecepcionCarga:
    massive_processing_id:str
    ambiente:str
    respuesta:dict=field(repr=False)
    estado:str=field(default='CARGA_RECIBIDA_PENDIENTE_PROCESO',init=False)
    firmado:bool=field(default=False,init=False)


def clave_carga(solicitud_id,ambiente,pdf_sha256,identificacion):
    """One deterministic existing-journal entry per document, holder and environment."""
    sid=str(UUID(str(solicitud_id)))
    if ambiente not in HOSTS or not re.fullmatch('[a-f0-9]{64}',pdf_sha256):
        raise ErrorCargasMasivas('MPL_VINCULO_DOCUMENTAL_INVALIDO')
    holder=hashlib.sha256(identificacion.encode()).hexdigest()
    return str(uuid5(NAMESPACE_URL,'credihoy-mpl:'+sid+':'+ambiente+':'+pdf_sha256+':'+holder))


class RegistroCargasMasivasExpediente(RegistroFirmasExpediente):
    """MPL states in the existing PostgreSQL journal; no new database or tables."""
    def exigir_aceptacion(self,clave,pdf_sha256,ambiente,identity):
        from datetime import datetime,timedelta,timezone
        from django.db import connections,transaction
        from apps.solicitudes.models import Solicitud
        from apps.xcore_consumo.models import SolicitudConsumo
        from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import _auditorias_contractuales
        try:
            if identity.get('_scopeContractual')!='solicitudes:firmarContrato':raise ValueError
            with transaction.atomic(using=self.alias):
                with connections[self.alias].cursor() as cursor:cursor.execute('SET TRANSACTION READ ONLY')
                case=Solicitud.objects.using(self.alias).select_related('solicitante').get(pk=self.solicitud_id,
                    solicitante__numero_identificacion=identity['numeroDocumento'])
                entry=SolicitudConsumo.objects.using(self.alias).values_list('orchestration_data',flat=True).get(
                    solicitud_id=self.solicitud_id)['credihoy_autentic_envios_v1'][clave]
                proof=entry['aceptacionMpl'];binding=entry['vinculo']
                audits=_auditorias_contractuales(case,self.alias,'credihoy_contractual_mpl_aceptado',clave)
                current=datetime.now(timezone.utc);accepted=datetime.fromisoformat(proof['aceptadoEn'])
                end=datetime.fromisoformat(identity['_sesionExpiraAt'])
                if (entry['productoProveedor']!='AUTENTIC_MPL' or binding['solicitudId']!=str(case.pk)
                        or binding['pdfSha256']!=pdf_sha256 or binding['ambienteProveedor']!=ambiente
                        or proof['acepta'] is not True or proof['pdfSha256']!=pdf_sha256
                        or proof['sesionReferencia']!=identity['_sesionReferencia']
                        or proof['titularSha256']!=hashlib.sha256(identity['numeroDocumento'].encode()).hexdigest()
                        or len(audits)!=1 or audits[0]!=proof or accepted.utcoffset() is None or end.utcoffset() is None
                        or not accepted<=current<min(end,accepted+timedelta(seconds=600))):raise ValueError
        except (KeyError,ValueError,TypeError,AttributeError,Solicitud.DoesNotExist,SolicitudConsumo.DoesNotExist):
            raise ErrorCargasMasivas('MPL_ACEPTACION_PERSONAL_VIGENTE_REQUERIDA') from None

    def finalizar_carga(self,clave,*,recibo=None,error=None):
        def finish(current):
            if current is None or current['clave']!=clave or current['estado']!='ENVIANDO':
                raise ErrorCargasMasivas('MPL_TRANSICION_NO_ACREDITADA')
            if recibo is None:
                return {**current,'estado':'RECHAZADA_AUTENTICACION' if error and error.http==401 else 'INDETERMINADA'}
            envelope={'producto':'AUTENTIC_MPL','ambiente':recibo.ambiente,'clave':clave,
                'solicitudId':str(self.solicitud_id),'fingerprint':current['fingerprint'],
                'massiveProcessingId':recibo.massive_processing_id,'respuesta':recibo.respuesta}
            cipher=self._cifrar(json.dumps(envelope,sort_keys=True))
            if not cipher or json.loads(self._descifrar(cipher))!=envelope:
                raise ErrorCargasMasivas('MPL_RECIBO_NO_CUSTODIADO')
            return {**current,'estado':'CARGA_RECIBIDA','massiveProcessingId':recibo.massive_processing_id,
                'reciboMplCifrado':cipher}
        return self._transicion(finish)

    def recuperar_carga(self,clave,ambiente):
        saved=[]
        def read(current):
            if current is None or current['clave']!=clave or current['estado']!='CARGA_RECIBIDA':
                raise ErrorCargasMasivas('MPL_ENVIO_EXISTENTE_NO_REENVIAR')
            saved.append(dict(current));return current
        self._transicion(read);current=saved[0]
        envelope=json.loads(self._descifrar(current['reciboMplCifrado']))
        if (envelope['producto']!='AUTENTIC_MPL' or envelope['ambiente']!=ambiente
                or envelope['clave']!=clave or envelope['solicitudId']!=str(self.solicitud_id)
                or envelope['fingerprint']!=current['fingerprint']
                or envelope['massiveProcessingId']!=current['massiveProcessingId']):
            raise ErrorCargasMasivas('MPL_RECIBO_DE_OTRA_OPERACION')
        return RecepcionCarga(envelope['massiveProcessingId'],ambiente,envelope['respuesta'])


def preparar_colo001(config,case,documento_operacion_id,signer,reference,*,lector=None):
    """Use the official v13 custody, without regenerating or resigning an uncertain PDF."""
    from apps.documentos.formularios_credihoy import FORMATOS
    if lector is None:
        from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import _documento_contractual
        lector=_documento_contractual
    pdf,meta=lector(case,documento_operacion_id,historico=True)
    if (meta['codigo']!='COLO-FO.001' or meta['version']!='13' or meta['solicitudId']!=str(case.pk)
            or meta['originalSha256']!=FORMATOS['COLO-FO.001']['sha256'] or validar_pdf(pdf)!=meta['pdfSha256']):
        raise ErrorCargasMasivas('MPL_DOCUMENTO_OFICIAL_NO_ACREDITADO')
    for entry in case.consumo_detail.orchestration_data.get('credihoy_autentic_envios_v1',{}).values():
        if (entry.get('vinculo',{}).get('pdfSha256')==meta['pdfSha256']
                and entry.get('estado') in ('ENVIANDO','INDETERMINADA')):
            raise ErrorCargasMasivas('MPL_DOCUMENTO_CON_ENVIO_PREVIO_INCIERTO_NO_REENVIAR')
    return preparar_carga(config,pdf,'COLO-FO.001-v13.pdf',signer,reference),meta


class AutenticCargasMasivasClient:
    def __init__(self,config,*,session=None,clock=time.monotonic):
        if not isinstance(config,ConfiguracionCargasMasivas):raise ErrorCargasMasivas('MPL_PERFIL_INVALIDO')
        self.config=config;self.session=session or requests.Session();self.clock=clock
        if isinstance(self.session,requests.Session):
            self.session.mount('https://',requests.adapters.HTTPAdapter(max_retries=0))
        self._token=None;self._expires=0;self.ultimo_http=None
        self.peticion_creacion=0

    def _redact(self,value,extra=()):
        text=str(value or '')
        for secret in (self.config.client_id,self.config.client_secret,self._token or '',
                self.config.enterprise_id,self.config.sender_email,self.config.sender_identification,
                self.config.notification_email,*extra):
            if type(secret) is str and secret:text=text.replace(secret,'[omitido]')
        text=re.sub(r'(?i)Bearer\s+\S+','Bearer [omitido]',text)
        text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[correo]',text)
        text=re.sub(r'https?://\S+','[url]',text)
        text=re.sub(r'\b\d{5,}\b','[identificador]',text)
        return re.sub(r'[\x00-\x1f\x7f]',' ',text)[:500]

    def _request(self,method,path,*,payload=None,oauth=False,extra=()):
        headers={'Accept':'application/json'}
        if not oauth:
            self.autenticar();headers['Authorization']='Bearer '+self._token
        url=TOKEN_URL if oauth else self.config.base_url+path
        self.ultimo_http={'metodo':method,'ruta':'OAUTH' if oauth else path.split('?')[0],
            'http':None,'respuestaRecibida':False,'reintentos':0}
        try:
            response=self.session.request(method,url,json=payload,headers=headers,
                timeout=self.config.timeout,verify=True,allow_redirects=False,stream=True)
        except requests.Timeout:
            raise ErrorCargasMasivas('MPL_TIMEOUT_SIN_REINTENTO') from None
        except requests.RequestException:
            raise ErrorCargasMasivas('MPL_RED_SIN_REINTENTO') from None
        try:
            self.ultimo_http.update(http=response.status_code,respuestaRecibida=True)
            raw=bytearray()
            for block in response.iter_content(65536):
                raw.extend(block)
                if len(raw)>12_000_000:raise ErrorCargasMasivas('MPL_RESPUESTA_EXCEDE_LIMITE',http=response.status_code)
            try:data=json.loads(bytes(raw).decode('utf-8-sig'),object_pairs_hook=_config_unica)
            except (ValueError,TypeError,UnicodeError):data=None
            if response.status_code!=200 or (isinstance(data,dict) and (data.get('error') or
                    type(data.get('status')) is int and data['status']!=200)):
                code=data.get('code',data.get('status')) if isinstance(data,dict) else None
                safe_code=code if type(code) is int else self._redact(code,extra)
                message=(data.get('message') or data.get('error')) if isinstance(data,dict) else 'Respuesta no JSON del proveedor'
                raise ErrorCargasMasivas('MPL_HTTP_PROVEEDOR',http=response.status_code,
                    codigo_proveedor=safe_code,descripcion=self._redact(message,extra))
            if not isinstance(data,dict):raise ErrorCargasMasivas('MPL_RESPUESTA_JSON_INVALIDA',http=response.status_code)
            return data
        except requests.RequestException:
            raise ErrorCargasMasivas('MPL_RESPUESTA_INCOMPLETA_SIN_REINTENTO',http=response.status_code) from None
        finally:response.close()

    def autenticar(self):
        if self._token and self.clock()<self._expires:
            return {'http':200,'audience':self.config.audience,'enCache':True}
        data=self._request('POST','',oauth=True,payload={'client_id':self.config.client_id,
            'client_secret':self.config.client_secret,'audience':self.config.audience,'grant_type':'client_credentials'})
        token=data.get('access_token');ttl=data.get('expires_in',120)
        if type(token) is not str or not token or type(ttl) not in (int,float) or ttl<=0:
            raise ErrorCargasMasivas('MPL_TOKEN_INVALIDO',http=200)
        valid=vigencia_token(token,ttl)
        if valid<=0:raise ErrorCargasMasivas('MPL_TOKEN_EXPIRADO_AL_RECIBIR',http=200)
        self._token=token;self._expires=self.clock()+max(0,valid-5)
        return {'http':200,'audience':self.config.audience,'expires_in':valid,
            'expires_in_proveedor':ttl,'enCache':False}

    def crear_proceso(self,payload):
        """Exactly one HTTP call. The caller commits its durable reservation first."""
        if (type(payload) is not dict or set(payload)!={'sendCompletionNotification','emailForNotification','processes'}
                or type(payload['processes']) is not list or len(payload['processes'])!=1
                or len(payload['processes'][0].get('documents',[]))!=1
                or len(payload['processes'][0].get('signers',[]))!=1
                or payload['processes'][0]['signers'][0].get('authMethods')!=['OTP']):
            raise ErrorCargasMasivas('MPL_CARGA_INDIVIDUAL_OTP_REQUERIDA')
        self.autenticar()
        self.peticion_creacion+=1
        signer=payload['processes'][0]['signers'][0]
        data=self._request('POST','/v3/signing-process/',payload=payload,extra=tuple(signer.values()))
        body=data.get('body')
        value=body.get('massiveProcessingId') if type(body) is dict else None
        return RecepcionCarga(_identifier(value),self.config.ambiente,data)

    def crear_proceso_durable(self,payload,*,registro,identity):
        """The documentary owner must already have recorded a fresh personal acceptance."""
        process=payload['processes'][0];pdf=base64.b64decode(process['documents'][0]['content'],validate=True)
        digest=validar_pdf(pdf)
        key=clave_carga(registro.solicitud_id,self.config.ambiente,digest,process['signers'][0]['identification'])
        if registro.envio_id!=key:raise ErrorCargasMasivas('MPL_CLAVE_DURABLE_DE_OTRO_DOCUMENTO')
        registro.exigir_aceptacion(key,digest,self.config.ambiente,identity)
        fingerprint=hashlib.sha256(json.dumps({'producto':'AUTENTIC_MPL','ambiente':self.config.ambiente,
            'payload':payload},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        previous=registro.preparar(key,fingerprint)
        if previous.estado=='CARGA_RECIBIDA':return registro.recuperar_carga(key,self.config.ambiente)
        if previous.estado!='PREPARADA':raise ErrorCargasMasivas('MPL_ENVIO_EXISTENTE_NO_REENVIAR')
        self.autenticar();registro.iniciar(key)
        try:receipt=self.crear_proceso(payload)
        except ErrorCargasMasivas as error:
            registro.finalizar_carga(key,error=error);raise
        except Exception:
            registro.finalizar_carga(key);raise ErrorCargasMasivas('MPL_ENVIO_INCIERTO_NO_REENVIAR') from None
        registro.finalizar_carga(key,recibo=receipt)
        return receipt

    def consultar_carga(self,massive_processing_id):
        data=self._request('GET','/v3/signing-process/'+quote(_identifier(massive_processing_id),safe=''))
        body=data.get('body');rows=body.get('processes') if type(body) is dict else None
        if type(rows) is not list:raise ErrorCargasMasivas('MPL_ESTADO_RESPUESTA_INVALIDA',http=200)
        result=[]
        for row in rows:
            if type(row) is not dict or row.get('status') not in ('SIGNED','UNSIGNED'):
                raise ErrorCargasMasivas('MPL_ESTADO_PROCESO_NO_DOCUMENTADO',http=200)
            result.append({'processId':_identifier(row.get('processId')),'status':row['status']})
        if body.get('count')!=len(rows):raise ErrorCargasMasivas('MPL_CONTEO_PROCESOS_DIFIERE',http=200)
        return {'massiveProcessingId':massive_processing_id,'processes':result,'respuesta':data,'firmadoVerificado':False}

    def consultar_documentos(self,process_id):
        data=self._request('GET','/v3/signing-process/get-files/'+quote(_identifier(process_id),safe=''))
        body=data.get('body');rows=body.get('files') if type(body) is dict else None
        if type(body) is not dict or body.get('processId')!=process_id or type(rows) is not list or body.get('count')!=len(rows):
            raise ErrorCargasMasivas('MPL_ARCHIVOS_DE_OTRO_PROCESO',http=200)
        for row in rows:
            _identifier(row.get('idFile'))
            url=urlsplit(row.get('url',''))
            if (url.scheme!='https' or not url.hostname or url.username or url.password
                    or url.port not in (None,443) or url.fragment):
                raise ErrorCargasMasivas('MPL_URL_DOCUMENTAL_INVALIDA',http=200)
        return data

    def consultar_detalle(self,process_id):
        return self._request('GET','/v3/signing-process/get-details/'+quote(_identifier(process_id),safe=''))

    def recuperar_pdf(self,files,process_id,id_file,*,session=None):
        """Only a file from an authenticated response; never send the API Bearer to storage."""
        body=files.get('body',{})
        if body.get('processId')!=process_id:raise ErrorCargasMasivas('MPL_ARCHIVO_DE_OTRO_PROCESO')
        matches=[row for row in body.get('files',[]) if row.get('idFile')==id_file]
        if len(matches)!=1:raise ErrorCargasMasivas('MPL_ARCHIVO_NO_UNIVOCO')
        url=matches[0]['url'];parsed=urlsplit(url);host=parsed.hostname or ''
        admitted=(host in self.config.hosts_descarga or host.endswith('.autenticsign.com')
            or re.fullmatch(r'[a-z0-9.-]+\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com',host))
        if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in (None,443) or not admitted):
            raise ErrorCargasMasivas('MPL_ALMACEN_DOCUMENTAL_NO_ADMITIDO')
        try:
            addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):raise ValueError
        except (OSError,ValueError):raise ErrorCargasMasivas('MPL_ALMACEN_DOCUMENTAL_NO_PUBLICO') from None
        download=session or requests.Session()
        if session is None:download.trust_env=False
        try:
            result=download.get(url,headers={'Accept':'application/pdf'},timeout=self.config.timeout,
                verify=True,allow_redirects=False,stream=True)
        except requests.RequestException:
            if session is None:download.close()
            raise ErrorCargasMasivas('MPL_DESCARGA_FALLIDA_SIN_REENVIO') from None
        try:
            if result.status_code!=200:raise ErrorCargasMasivas('MPL_HTTP_DESCARGA',http=result.status_code)
            raw=bytearray()
            for chunk in result.iter_content(65536):
                raw.extend(chunk)
                if len(raw)>12_000_000:raise ErrorCargasMasivas('MPL_PDF_DESCARGADO_EXCEDE_LIMITE')
            pdf=bytes(raw)
            validar_pdf(pdf)
            return {'pdf':pdf,'sha256':hashlib.sha256(pdf).hexdigest(),'idFile':id_file,'processId':process_id,
                'http':200,'urlSha256':hashlib.sha256(url.encode()).hexdigest()}
        except requests.RequestException:
            raise ErrorCargasMasivas('MPL_DESCARGA_INCOMPLETA_SIN_REENVIO',http=result.status_code) from None
        finally:
            result.close()
            if session is None:download.close()

    def certificar(self,process_id,id_document):
        data=self._request('POST','/v3/signing-process/signature-certification',
            payload={'processId':_identifier(process_id),'idDocument':_identifier(id_document)})
        body=data.get('body')
        if type(body) is not dict or type(body.get('fileName')) is not str:
            raise ErrorCargasMasivas('MPL_CERTIFICADO_NO_DISPONIBLE',http=200)
        try:pdf=base64.b64decode(body['content'],validate=True)
        except (ValueError,KeyError,TypeError):raise ErrorCargasMasivas('MPL_CERTIFICADO_INVALIDO',http=200) from None
        validar_pdf(pdf)
        return {'pdf':pdf,'sha256':hashlib.sha256(pdf).hexdigest(),'fileName':body['fileName'],'respuesta':data}
