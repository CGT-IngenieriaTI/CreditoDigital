"""Grant real del unico envio SSH; separado del servicio de laboratorio."""
from dataclasses import asdict
from datetime import datetime, timedelta

from incluirtec.digital_operations.contracts import AdministrativeGrant, decision_fingerprint

from .condiciones import FichaError, MODEL, SCOPE, codigo, huella


PERSONAL_PURPOSE='credihoy-cd6-personal-v1'
PERSONAL_DOMAIN=b'credihoy-cd6-personal-v1\x00'
PERSONAL_ACTIONS=frozenset({'preparar','consultar','confirmar','conciliar'})


def perfil_personal(path=None):
    import base64,json,os,stat
    from pathlib import Path
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        target=Path(path or os.environ.get('CD6_PERSONAL_PROFILE','/home/ayuda/.config/api-data-9a/cd6-personal.json'))
        if not target.is_absolute() or target.is_symlink():raise ValueError()
        with target.open('rb') as stream:
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size>16384
                    or os.name!='nt' and (info.st_uid not in (0,os.geteuid()) or info.st_mode&0o077)):
                raise ValueError()
            cfg=json.load(stream)
        if (cfg.get('version')!=PERSONAL_PURPOSE or cfg.get('enabled') is not True
                or cfg.get('usuarioWorkflow')!='CGTE' or not cfg.get('actorTecnico')):raise ValueError()
        public=Ed25519PublicKey.from_public_bytes(base64.b64decode(cfg['publicKeyBase64'],validate=True))
        return cfg,public
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        raise FichaError('PERFIL_TECNICO_PERSONAL_CD6_NO_HABILITADO') from None


def validar_delegacion_personal(envelope,body,public_key,policy,now):
    """Independent technical capability; never an administrative/credit grant."""
    import base64,hashlib,re
    from uuid import UUID
    from cryptography.exceptions import InvalidSignature
    from incluirtec.digital_operations.contracts import canonical
    try:
        if (type(envelope) is not dict or set(envelope)!={'algoritmo','payload','firmaBase64'}
                or envelope['algoritmo']!='Ed25519' or type(body) is not dict
                or body.get('accion') not in PERSONAL_ACTIONS or type(policy) is not dict
                or policy.get('enabled') is not True or policy.get('version')!=PERSONAL_PURPOSE
                or policy.get('usuarioWorkflow')!='CGTE'):
            raise ValueError()
        claim=envelope['payload']
        fields={'version','audiencia','accion','solicitudId','intencionId','requestId','titularSha256',
            'payloadSha256','emitidaEn','venceEn','actorTecnico','autorizaStart','autorizaFirma','autorizaDesembolso'}
        if type(claim) is not dict or set(claim)!=fields:raise ValueError()
        public_key.verify(base64.b64decode(envelope['firmaBase64'],validate=True),
            PERSONAL_DOMAIN+canonical(claim).encode())
        begin,end=(datetime.fromisoformat(claim[k]) for k in ('emitidaEn','venceEn'))
        allowed=policy['solicitudes']
        if (type(allowed) is not list or not 1<=len(allowed)<=20
                or len(allowed)!=len(set(allowed)) or any(str(UUID(k))!=k for k in allowed)
                or claim['version']!=PERSONAL_PURPOSE or claim['audiencia']!='CD6'
                or claim['actorTecnico']!=policy['actorTecnico']
                or claim['accion']!=body['accion'] or claim['solicitudId']!=body['solicitudId']
                or claim['intencionId']!=body['intencionId'] or claim['requestId']!=body['requestId']
                or claim['solicitudId'] not in allowed
                or any(str(UUID(claim[k]))!=claim[k] for k in ('solicitudId','intencionId','requestId'))
                or any(not re.fullmatch('[a-f0-9]{64}',claim[k]) for k in ('titularSha256','payloadSha256'))
                or claim['payloadSha256']!=hashlib.sha256(canonical(body).encode()).hexdigest()
                or now.utcoffset() is None or begin.utcoffset() is None or end.utcoffset() is None
                or not begin<=now<end or not 0<(end-begin).total_seconds()<=180
                or claim['autorizaStart'] is not (body['accion']=='confirmar')
                or claim['autorizaFirma'] is not False or claim['autorizaDesembolso'] is not False):
            raise ValueError()
        if body['accion']=='confirmar':
            confirmation=body['confirmacionPersonal']
            signed=datetime.fromisoformat(confirmation['confirmadaEn'])
            expires=datetime.fromisoformat(confirmation['sesionVenceEn'])
            if (type(confirmation) is not dict or set(confirmation)!={'auditoriaId','sha256','sesionReferencia',
                    'confirmadaEn','sesionVenceEn','aceptacionAuditoriaId','aceptacionSha256','simulacionSha256',
                    'revisionReferencia','decisionReferencia','decisionSha256','versionCondiciones','aceptaRadicar'}
                    or confirmation['aceptaRadicar'] is not True
                    or any(type(confirmation[k]) is not int or confirmation[k]<=0
                        for k in ('auditoriaId','aceptacionAuditoriaId'))
                    or confirmation['auditoriaId']==confirmation['aceptacionAuditoriaId']
                    or any(str(UUID(confirmation[k]))!=confirmation[k]
                        for k in ('sesionReferencia','revisionReferencia','decisionReferencia'))
                    or any(not re.fullmatch('[a-f0-9]{64}',confirmation[k]) for k in
                        ('sha256','aceptacionSha256','simulacionSha256','decisionSha256','versionCondiciones'))
                    or signed.utcoffset() is None or expires.utcoffset() is None
                    or not 0<=(now-signed).total_seconds()<=300 or not now<expires):
                raise ValueError()
        return dict(claim)
    except (ValueError,TypeError,KeyError,AttributeError,InvalidSignature):
        raise FichaError('DELEGACION_PERSONAL_CD6_NO_ACREDITADA') from None


def aceptacion_personal_journal(journal):
    current=journal.last_evidence('REFERENCIA_DECISION_PERSONAL_ACTUALIZADA')
    return current['aceptacionSimulacion'] if current else journal.packet()['evidencia']['aceptacionSimulacion']


def validar_confirmacion_operacion(confirmation,operation):
    import hashlib,json
    body={'solicitudId':operation.payload['solicitudId'],'intencionId':str(operation.intent_id),
        'fingerprint':operation.fingerprint,**{k:v for k,v in confirmation.items() if k not in {'sha256','auditoriaId'}}}
    if confirmation['sha256']!=hashlib.sha256(json.dumps(body,sort_keys=True,default=str).encode()).hexdigest():
        raise FichaError('CONFIRMACION_PERSONAL_NO_CUBRE_OPERACION')


def validar_operacion_personal(journal,workflow_user,now,*,sending):
    from incluirtec.digital_operations.contracts import canonical,digest
    cfg,key=perfil_personal()
    evidence=journal.packet()['evidencia'];record=journal.last_evidence('CONFIRMACION_PERSONAL_CD6_VERIFICADA')
    if type(record) is not dict:raise FichaError('CONFIRMACION_PERSONAL_DURABLE_REQUERIDA')
    body=record['peticion'];grant=record['delegacion']
    cut=now if sending else datetime.fromisoformat(grant['payload']['emitidaEn'])
    claim=validar_delegacion_personal(grant,body,key,cfg,cut)
    row=journal.operation();proof=aceptacion_personal_journal(journal);confirmation=body['confirmacionPersonal']
    validar_confirmacion_operacion(confirmation,row)
    if (evidence.get('canal')!='CONFIRMACION_PERSONAL_CD6' or workflow_user!=cfg['usuarioWorkflow']
            or body['accion']!='confirmar' or row.payload['solicitudId']!=claim['solicitudId']
            or str(row.intent_id)!=claim['intencionId'] or proof['titularReferencia']!=claim['titularSha256']
            or record['confirmacionSha256']!=digest(canonical(confirmation))
            or any(confirmation[k]!=proof[k] for k in ('aceptacionAuditoriaId','aceptacionSha256',
                'simulacionSha256','revisionReferencia','decisionReferencia','decisionSha256','versionCondiciones'))):
        raise FichaError('CONFIRMACION_PERSONAL_DURABLE_NO_CORRESPONDE')
    return claim


def crear(payload, authority, workflow_user, now):
    antecedent = authority.get('antecedente')
    if not isinstance(antecedent, str) or not antecedent.strip():
        raise FichaError('ANTECEDENTE_ADMINISTRATIVO_REQUERIDO')
    grant = AdministrativeGrant(client='CD6-SSH:' + workflow_user,
        fingerprint=decision_fingerprint(payload),
        authorization_id=payload['autorizacionAdministrativa']['autorizacionId'],
        reference=payload['autorizacionAdministrativa']['referencia'],
        issuer_id=codigo(authority['responsable'], 'responsable'), request_id=payload['solicitudId'],
        intent_id=payload['intencionId'], workflow=MODEL, scope=SCOPE, authorized=True,
        not_before=now, expires_at=now + timedelta(seconds=180), synthetic=False)
    raw = asdict(grant)
    for key in ('not_before', 'expires_at'):
        raw[key] = raw[key].isoformat()
    return {'grant': raw, 'antecedenteSha256': huella(antecedent)}


def validar(packet, payload, workflow_user, now, *, sending):
    evidence = packet['evidencia']
    raw = dict(evidence['grantAdministrativo']['grant'])
    try:
        for key in ('not_before', 'expires_at'):
            raw[key] = datetime.fromisoformat(raw[key])
        grant = AdministrativeGrant(**raw)
        authority = evidence['autorizacion']
        valid = (grant.synthetic is False and grant.authorized is True
            and grant.client == 'CD6-SSH:' + workflow_user
            and grant.fingerprint == decision_fingerprint(payload)
            and grant.request_id == payload['solicitudId']
            and grant.intent_id == payload['intencionId']
            and grant.workflow == MODEL and grant.scope == SCOPE
            and grant.authorization_id == payload['autorizacionAdministrativa']['autorizacionId']
            and grant.reference == payload['autorizacionAdministrativa']['referencia'] == authority['referencia']
            and grant.issuer_id == authority['responsable']
            and evidence['grantAdministrativo']['antecedenteSha256'] == huella(authority['antecedente'])
            and grant.not_before.tzinfo is not None and grant.expires_at.tzinfo is not None
            and grant.not_before < grant.expires_at
            and (not sending or grant.not_before <= now < grant.expires_at))
    except (ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise FichaError('GRANT_ADMINISTRATIVO_DURABLE_NO_VALIDO')
