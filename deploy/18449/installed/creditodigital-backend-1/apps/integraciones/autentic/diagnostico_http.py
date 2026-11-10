"""Minimal error evidence; arbitrary provider text and unknown fields are dropped."""
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
