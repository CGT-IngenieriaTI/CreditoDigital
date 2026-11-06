"""Solo DEV. Una llamada por envio, TLS verificado, sin redirects ni retries."""

import base64
from dataclasses import dataclass, field
import http.client
import ipaddress
import json
import logging
import socket
import time
from datetime import datetime,timezone
from uuid import uuid4
from urllib.parse import urlsplit

import requests

from .contratos import (AutenticError, ConfiguracionDEV, DEV_URL, SolicitudFirma,
                        TOKEN_URL, preparar_certificacion, texto, validar_pdf)
from .evidencia import EvidenciaPDF, MAX_ZIP, verificar_zip


@dataclass(frozen=True)
class ReciboFirma:
    transaccion: str
    fecha_proveedor: str
    url_documentos: str = field(repr=False)
    estado: str = "FIRMA_REPORTADA_NO_VERIFICADA"
    codigo_operacion: int = 1000
    mensaje_operacion: str = field(default="",repr=False)
    tipo_respuesta: str = field(default="",repr=False)
    respuesta_original: dict = field(default_factory=dict,repr=False)


def _pares_unicos(pares):
    result = {}
    for k, v in pares:
        if k in result:
            raise ValueError
        result[k] = v
    return result


def _constante_invalida(_):
    raise ValueError


class AutenticDEVClient:
    def __init__(self, config, *, session=None, reloj=time.monotonic):
        if not isinstance(config, ConfiguracionDEV):
            raise AutenticError("CONFIGURACION_DEV_INVALIDA")
        self.config = config
        self.session = session if session is not None else requests.Session()
        self.session.trust_env = False
        self.reloj = reloj
        self._token = None
        self._vence = 0
        self.auditoria_token = {'peticiones': 0, 'http': None, 'tiempoRespuestaMs': None}
        self.auditoria_firma = {'peticiones': 0, 'http': None, 'tiempoRespuestaMs': None, 'codigo': None}
        self.auditoria_descarga={}
        self._diagnosticos_http=[]
        self._evento_http=None
        self._excluir_diagnostico=()

    def close(self):
        self._token = None
        self._vence = 0
        self.session.close()
        self._diagnosticos_http.clear()
        self._excluir_diagnostico=()

    def diagnostico_cifrado(self,cifrar,*,solicitud_id,operacion_id):
        return cifrar(json.dumps({'version':'AUTENTIC_HTTP_REDACTADO_V1',
            'solicitudId':str(solicitud_id),'operacionId':str(operacion_id),
            'eventos':self._diagnosticos_http},sort_keys=True))

    def _diagnostico(self,raw=b'',*,completo=True,codigo=None):
        from .diagnostico_http import cuerpo_redactado
        if self._evento_http is not None:
            self._diagnosticos_http.append({**self._evento_http,'codigoLocal':codigo,
                'cuerpo':cuerpo_redactado(raw,completo=completo,excluir=(self.config.client_id,
                    self.config.client_secret,self._token or '',*self._excluir_diagnostico))})
            self._diagnosticos_http=self._diagnosticos_http[-8:]

    def _request(self, method, url, *, payload=None, bearer=False, limite=1_000_000,
                 mime="application/json", estados=(200,)):
        if (http.client.HTTPConnection.debuglevel
                or http.client.HTTPSConnection.debuglevel
                or any(logging.getLogger(name).isEnabledFor(logging.DEBUG)
                       for name in ('urllib3', 'urllib3.connectionpool', 'requests'))):
            raise AutenticError("TRANSPORTE_DEBUG_NO_AUTORIZADO")
        if isinstance(self.session, requests.Session) and (
                self.session.auth is not None or self.session.proxies or self.session.cert is not None
                or self.session.hooks != {'response': []}
                or any(type(adapter) is not requests.adapters.HTTPAdapter or adapter.max_retries.total != 0
                       for adapter in self.session.adapters.values())):
            raise AutenticError("TRANSPORTE_NO_AISLADO")
        if method == 'GET' and (getattr(self.session, 'auth', None)
                or getattr(self.session, 'cookies', None)
                or any(k.lower() in ('authorization', 'cookie', 'proxy-authorization')
                       for k in getattr(self.session, 'headers', {}))):
            raise AutenticError("TRANSPORTE_DESCARGA_NO_AISLADO")
        headers = {"Accept": mime}
        if bearer:
            self.autenticar()
            headers["Authorization"] = "Bearer " + self._token
        token_request = method == 'POST' and url == TOKEN_URL
        signing_request = method == 'POST' and url == self.config.base_url + '/v2/signature/'
        operation='TOKEN' if token_request else 'FIRMA' if signing_request else 'CERTIFICACION' if method=='POST' else 'DESCARGA'
        self._evento_http={'operacion':operation,'fecha':datetime.now(timezone.utc).isoformat(),
            'referenciaLocal':str(uuid4()),'http':None}
        self._excluir_diagnostico=tuple(v for v in (payload or {}).get('metadata',{}).values() if type(v) is str)
        inicio = time.monotonic()
        if token_request:
            self.auditoria_token['peticiones'] += 1
        if signing_request:
            self.auditoria_firma['peticiones'] += 1
        try:
            response = self.session.request(method, url, json=payload, headers=headers,
                                            timeout=self.config.timeout, verify=True,
                                            allow_redirects=False, stream=True)
            try:
                self._evento_http['http']=response.status_code
                if token_request:
                    self.auditoria_token['http'] = response.status_code
                if signing_request:
                    self.auditoria_firma['http'] = response.status_code
                if response.status_code not in estados:
                    from .diagnostico_http import MAX_ERROR
                    content=bytearray();complete=True
                    try:
                        for block in response.iter_content(4096):
                            remaining=MAX_ERROR-len(content)
                            content.extend(block[:remaining])
                            if len(block)>remaining:complete=False;break
                    except requests.RequestException:complete=False
                    self._diagnostico(bytes(content),completo=complete,codigo='HTTP_NO_EXITOSO')
                if response.status_code in (401, 403):
                    self._token = None
                    self._vence = 0
                    raise AutenticError("AUTENTICACION_RECHAZADA")
                if response.status_code not in estados:
                    raise AutenticError("HTTP_NO_EXITOSO")
                if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != mime:
                    self._diagnostico(completo=False,codigo='RESPUESTA_INVALIDA')
                    raise AutenticError("RESPUESTA_INVALIDA")
                content = bytearray()
                for block in response.iter_content(65536):
                    if len(content) + len(block) > limite:
                        self._diagnostico(bytes(content[:32768]),completo=False,codigo='RESPUESTA_EXCEDE_LIMITE')
                        raise AutenticError("RESPUESTA_EXCEDE_LIMITE")
                    content.extend(block)
                if method=='GET':
                    import hashlib
                    self.auditoria_descarga={'version':'AUTENTIC_DESCARGA_V1','ambiente':self.config.ambiente,
                        'http':response.status_code,'TLSVerificado':True,'sinRedirecciones':True,
                        'urlSha256':hashlib.sha256(url.encode()).hexdigest(),
                        'zipSha256':hashlib.sha256(content).hexdigest()}
                return bytes(content)
            finally:
                response.close()
        except requests.Timeout:
            self._diagnostico(completo=False,codigo='TIMEOUT_SIN_REINTENTO')
            raise AutenticError("TIMEOUT_SIN_REINTENTO") from None
        except requests.RequestException:
            self._diagnostico(completo=False,codigo='RED_SIN_REINTENTO')
            raise AutenticError("RED_SIN_REINTENTO") from None
        finally:
            if token_request:
                self.auditoria_token['tiempoRespuestaMs'] = round((time.monotonic() - inicio) * 1000)
            if signing_request:
                self.auditoria_firma['tiempoRespuestaMs'] = round((time.monotonic() - inicio) * 1000)

    def _json(self, url, payload, *, bearer=False, limite=1_000_000, estados=(200,)):
        raw = self._request("POST", url, payload=payload, bearer=bearer, limite=limite, estados=estados)
        try:
            result = json.loads(raw, object_pairs_hook=_pares_unicos, parse_constant=_constante_invalida)
            if type(result) is not dict:
                raise ValueError
            if url!=TOKEN_URL:self._diagnostico(raw[:32768],completo=len(raw)<=32768)
            return result
        except (ValueError, TypeError, UnicodeError, RecursionError):
            self._diagnostico(raw[:32768],completo=len(raw)<=32768,codigo='RESPUESTA_INVALIDA')
            raise AutenticError("RESPUESTA_INVALIDA") from None

    def autenticar(self):
        if self._token is not None and self.reloj() < self._vence:
            return {"autenticado": True, "token_en_cache": True}
        self._token = None
        self._vence = 0
        result = self._json(TOKEN_URL, {"audience": self.config.audience,
            "grant_type": "client_credentials", "client_id": self.config.client_id,
            "client_secret": self.config.client_secret})
        token, ttl = result.get("access_token"), result.get("expires_in")
        if (type(token) is not str or not 0 < len(token) <= 32000 or any(c.isspace() for c in token)
                or type(ttl) is not int or ttl <= 0 or type(result.get("token_type")) is not str
                or result["token_type"].lower() != "bearer" or not texto(result.get("scope"))):
            raise AutenticError("TOKEN_INVALIDO")
        self._token = token
        self._vence = self.reloj() + ttl - min(30, ttl / 10)
        return {"autenticado": True, "expires_in": ttl, "token_en_cache": False}

    def _documentos_autorizados(self, referencia):
        if self.config.permitir_documentos is not True or not texto(referencia):
            raise AutenticError("DOCUMENTOS_DEV_NO_AUTORIZADOS")

    def _huella_ambiente(self, fingerprint):
        if self.config.ambiente == 'DEV':
            return fingerprint
        from hashlib import sha256
        return sha256((self.config.audience+'\x00'+fingerprint).encode('ascii')).hexdigest()

    def firmar(self, solicitud, *, clave, registro, referencia_autorizacion):
        self._documentos_autorizados(referencia_autorizacion)
        if type(solicitud) is not SolicitudFirma:
            raise AutenticError("SOLICITUD_FIRMA_INVALIDA")
        # Revalidar el snapshot: no enviar un payload mutado despues de reservarlo.
        from .contratos import preparar_firma
        try:
            files = solicitud.payload['files']
            docs = {f['fileName']: base64.b64decode(f['fileContent'], validate=True) for f in files}
            checked = preparar_firma(solicitud.payload['metadata'], docs)
            if checked.fingerprint != solicitud.fingerprint or len(docs) != len(files):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise AutenticError("SOLICITUD_FIRMA_INVALIDA") from None
        state = registro.preparar(clave, self._huella_ambiente(solicitud.fingerprint))
        if state.estado == "FIRMA_REPORTADA_NO_VERIFICADA":
            return state
        if state.estado != "PREPARADA":
            raise AutenticError("REENVIO_BLOQUEADO_REQUIERE_CONCILIACION")
        self.autenticar()
        registro.iniciar(clave)
        try:
            p = self._json(self.config.base_url + "/v2/signature/", checked.payload, bearer=True, estados=(201,))
            if (type(p.get('operationCode')) is not int or p['operationCode'] != 1000
                    or not all(texto(p.get(k)) for k in ('transactionId', 'processedDate', 'urlDocuments'))):
                raise AutenticError("FIRMA_RESPUESTA_NO_CERTIFICADA")
            if any(k in p and (type(p[k]) is not str or len(p[k])>1000) for k in ('operationMsg','type')):
                raise AutenticError('FIRMA_RESPUESTA_NO_CERTIFICADA')
            receipt = ReciboFirma(p['transactionId'], p['processedDate'], p['urlDocuments'],
                codigo_operacion=p['operationCode'],mensaje_operacion=p.get('operationMsg',''),tipo_respuesta=p.get('type',''),
                respuesta_original=p)
        except Exception as exc:
            self.auditoria_firma['codigo'] = exc.codigo if isinstance(exc, AutenticError) else 'FALLO_INTERNO_DESPUES_DE_RESERVA'
            registro.terminar(clave, "INDETERMINADA")
            raise AutenticError("ENVIO_INDETERMINADO_NO_REPETIR") from None
        registro.terminar(clave, "FIRMA_REPORTADA_NO_VERIFICADA", receipt.transaccion)
        return receipt

    def consultar_estado(self, transaccion):
        raise AutenticError("CONSULTA_ESTADO_NO_DOCUMENTADA")

    def recuperar_documentos(self, recibo, nombres_esperados, *, referencia_autorizacion):
        self._documentos_autorizados(referencia_autorizacion)
        try:
            url = urlsplit(recibo.url_documentos)
            if (url.scheme != 'https' or url.username or url.password or url.fragment
                    or url.port not in (None, 443) or url.hostname not in self.config.hosts_descarga):
                raise ValueError
            try:
                ipaddress.ip_address(url.hostname)
            except ValueError:
                pass
            else:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise AutenticError("DESTINO_DESCARGA_NO_AUTORIZADO") from None
        if isinstance(self.session,requests.Session):
            try:
                addresses=socket.getaddrinfo(url.hostname,443,type=socket.SOCK_STREAM)
                if not addresses or any(not ipaddress.ip_address(v[4][0]).is_global for v in addresses):
                    raise ValueError
            except (OSError,ValueError):
                raise AutenticError('DESTINO_DESCARGA_NO_PUBLICO') from None
        # La URL firmada no recibe el Bearer de la API y no sigue redirects.
        raw = self._request('GET', recibo.url_documentos, limite=MAX_ZIP, mime='application/zip')
        return verificar_zip(raw, nombres_esperados)

    def certificar(self, transaccion, documento, *, clave=None, registro=None, referencia_autorizacion):
        self._documentos_autorizados(referencia_autorizacion)
        request = preparar_certificacion(transaccion, documento)
        from .operaciones import RegistroFirmas
        if not isinstance(registro, RegistroFirmas):
            raise AutenticError("JOURNAL_CERTIFICACION_REQUERIDO")
        state = registro.preparar(clave, self._huella_ambiente(request.fingerprint))
        if state.estado == 'CERTIFICACION_RECUPERADA_NO_VERIFICADA':
            return state
        if state.estado != 'PREPARADA':
            raise AutenticError("REENVIO_BLOQUEADO_REQUIERE_CONCILIACION")
        self.autenticar()
        registro.iniciar(clave)
        try:
            p = self._json(self.config.base_url + '/v2/certification/', request.payload, bearer=True, limite=9_000_000)
            if type(p.get('operationCode')) is not int or p['operationCode'] != 1000:
                raise AutenticError("CERTIFICACION_NO_DISPONIBLE")
            content = base64.b64decode(p['certificationContent'], validate=True)
            evidence = EvidenciaPDF(validar_pdf(content), content)
        except Exception:
            registro.terminar(clave, 'INDETERMINADA', transaccion)
            raise AutenticError("CERTIFICACION_INDETERMINADA_NO_REPETIR") from None
        registro.terminar(clave, 'CERTIFICACION_RECUPERADA_NO_VERIFICADA', transaccion)
        return evidence
