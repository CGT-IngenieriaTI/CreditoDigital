"""Minimal error evidence; arbitrary provider text and unknown fields are dropped."""

import socket
import ssl
import requests
from hashlib import sha256
from datetime import datetime
import json
import re

MAX_ERROR=32768
MESSAGES={'PDF header signature not found.':'PDF_INVALIDO','DOCS_SIGNED':'DOCS_SIGNED','OK':'OK'}


def cuerpo_redactado(raw,*,completo=True,excluir=()):
    result={'bytesAnalizados':len(raw),'sha256Fragmento':sha256(raw).hexdigest(),
        'completo':completo,'camposNoDocumentadosDescartados':True}
    try:
        value=json.loads(raw)
        if type(value) is not dict:raise ValueError
    except (ValueError,UnicodeError,RecursionError):
        result['formato']='NO_JSON_O_INCOMPLETO';return result
    result['formato']='JSON_REDACTADO'
    for name in ('operationCode',):
        if type(value.get(name)) is int:result[name]=value[name]
    message=value.get('operationMsg',value.get('operationMessage'))
    if type(message) is str:
        result['mensajeClasificado']=MESSAGES.get(message,'MENSAJE_PROVEEDOR_REDACTADO')
        result['operationMsgSha256']=sha256(message.encode()).hexdigest()
    kind=value.get('type')
    if kind in ('reply','reply_signature'):result['type']=kind
    reference=value.get('transactionId')
    if (type(reference) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',reference)
            and not any(v and v in reference for v in excluir)):
        result['transactionId']=reference
    when=value.get('processedDate')
    if type(when) is str and len(when)<=100:
        try:
            datetime.fromisoformat(when)
            result['processedDate']=when
        except ValueError:pass
    return result


class CausaAutenticSanitizada(Exception):
    """Keep the original in memory, never interpolate its potentially sensitive text."""
    def __init__(self, original):
        self.original = original
        name = type(original).__name__
        self.tipo_original = name if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,99}', name) else 'Exception'
        super().__init__('CAUSA_EXTERNA:' + self.tipo_original)



def causa_segura(error):
    return CausaAutenticSanitizada(error)



def correlacion_redactada(headers, *, excluir=()):
    result = {}
    for name in ('X-Request-ID', 'X-Correlation-ID', 'X-Amzn-RequestId', 'Traceparent'):
        value = headers.get(name)
        if type(value) is not str or not 0 < len(value) <= 512:
            continue
        if any(secret and secret in value for secret in excluir):
            continue
        result[name] = {'sha256': sha256(value.encode()).hexdigest()}
    return result



def excepcion_redactada(error, *, http=None):
    queue, seen, names, codes = [error], set(), set(), set()
    dns = tls = False
    while queue and len(seen) < 20:
        current = queue.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        name = type(current).__name__
        if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,99}', name):
            names.add(name)
        if type(getattr(current, 'errno', None)) is int:
            codes.add(current.errno)
        dns |= isinstance(current, socket.gaierror)
        tls |= isinstance(current, (ssl.SSLError, requests.exceptions.SSLError))
        for value in (getattr(current, '__cause__', None), getattr(current, '__context__', None),
                      getattr(current, 'reason', None), *getattr(current, 'args', ())):
            if isinstance(value, BaseException):
                queue.append(value)
    if isinstance(error, requests.ConnectTimeout):
        phase = 'TIMEOUT_CONEXION'
    elif isinstance(error, requests.ReadTimeout):
        phase = 'TIMEOUT_LECTURA'
    elif isinstance(error, requests.Timeout):
        phase = 'TIMEOUT_NO_DETERMINADO'
    else:
        phase = 'DNS' if dns else 'TLS' if tls else 'TRANSPORTE'
    return {'tipos': sorted(names), 'errno': sorted(codes), 'fase': phase,
            'respuestaHttpRecibida': http is not None, 'http': http,
            'transmisionOriginalAcreditada': None}

