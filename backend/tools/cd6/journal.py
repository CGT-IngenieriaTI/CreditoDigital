"""Journal de una solicitud: metadata minima, payload/evidencia cifrados en SQLite."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import sqlite3
from types import SimpleNamespace
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .condiciones import SID, FichaError, canonical


class Journal:
    def __init__(self, path, secret, *, request_id=SID, create=True, read_only=False):
        self.request_id = str(UUID(str(request_id)))
        self.path = path
        if not secret or len(secret) < 16:
            raise FichaError('CLAVE_CUSTODIA_NO_DISPONIBLE')
        self.key = hmac.new(secret, b'CD6-JOURNAL-ADMINISTRATIVO-V1', hashlib.sha256).digest()
        if type(create) is not bool or type(read_only) is not bool or read_only and create:
            raise FichaError('MODO_JOURNAL_INVALIDO')
        if not create:
            from pathlib import Path
            existing=Path(path)
            if existing.is_symlink() or not existing.is_file():raise FichaError('JOURNAL_EXISTENTE_REQUERIDO')
            self.db=sqlite3.connect(existing.resolve().as_uri()+('?mode=ro' if read_only else '?mode=rw'),uri=True,timeout=15)
            if read_only:self.db.execute('PRAGMA query_only=ON')
        else:self.db = sqlite3.connect(path, timeout=15)
        try:
            rows = self.db.execute('SELECT solicitud FROM cd6_envio').fetchall()
        except sqlite3.OperationalError:
            if not create:
                self.db.close();raise FichaError('ESQUEMA_JOURNAL_EXISTENTE_NO_VALIDO') from None
            rows = []
        if any(row[0] != self.request_id for row in rows):
            self.db.close()
            raise FichaError('JOURNAL_DE_OTRO_EXPEDIENTE_NO_MODIFICAR')
        if not create:
            try:
                self.db.execute('SELECT solicitud FROM cd6_start_guard LIMIT 0')
                self.db.execute('SELECT evidencia FROM cd6_observaciones LIMIT 0')
            except sqlite3.OperationalError:
                self.db.close();raise FichaError('ESQUEMA_JOURNAL_EXISTENTE_NO_VALIDO') from None
            return
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS cd6_envio (
            solicitud TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL, intent TEXT UNIQUE NOT NULL,
            claim TEXT, estado TEXT NOT NULL, fingerprint TEXT NOT NULL, decision_hash TEXT NOT NULL,
            payload BLOB NOT NULL, attempted TEXT, flow TEXT, result BLOB)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS cd6_start_guard (solicitud TEXT PRIMARY KEY, instante TEXT NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS cd6_observaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT, solicitud TEXT NOT NULL,
            instante TEXT NOT NULL, estado_observado TEXT NOT NULL, evidencia BLOB NOT NULL)''')
        self.db.commit()

    def close(self):
        self.db.close()

    def encrypt(self, value):
        nonce = os.urandom(12)
        return nonce + AESGCM(self.key).encrypt(nonce, canonical(value).encode(), self.request_id.encode())

    def decrypt(self, value):
        return json.loads(AESGCM(self.key).decrypt(value[:12], value[12:], self.request_id.encode()))

    def exists(self):
        return self.db.execute('SELECT 1 FROM cd6_envio WHERE solicitud=?', (self.request_id,)).fetchone() is not None

    def prepare(self, payload, evidence):
        if payload['solicitudId'] != self.request_id:
            raise FichaError('OTRO_EXPEDIENTE_NO_PERMITIDO')
        from incluirtec.digital_operations.contracts import canonical as api_canonical, digest, decision_fingerprint
        fingerprint = digest(api_canonical(payload))
        identifier = str(uuid4())
        try:
            self.db.execute('INSERT INTO cd6_envio VALUES (?,?,?,NULL,?,?,?, ?,NULL,NULL,NULL)',
                (self.request_id, identifier, payload['intencionId'], 'PREPARADA', fingerprint,
                 decision_fingerprint(payload), self.encrypt({'request': payload, 'evidencia': evidence})))
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            raise FichaError('EXPEDIENTE_YA_TIENE_INTENCION_NO_REPETIR') from None
        return self.operation()

    def operation(self):
        row = self.db.execute('SELECT id,intent,claim,estado,fingerprint,decision_hash,payload,attempted,flow FROM cd6_envio WHERE solicitud=?', (self.request_id,)).fetchone()
        if row is None:
            raise FichaError('INTENCION_NO_EXISTE')
        packet = self.decrypt(row[6])
        payload = packet['request']
        if payload['solicitudId'] != self.request_id:
            raise FichaError('JOURNAL_IDENTIDAD_CONTRADICTORIA')
        return SimpleNamespace(id=row[0], intent_id=row[1], claim_id=row[2], state=row[3],
            fingerprint=row[4], decision_fingerprint=row[5], payload=payload,
            attempted_at=row[7], flow_id=row[8], workflow_user=packet['evidencia'].get('usuarioWorkflow'))

    def reopen_autocommit_preflight(self, payload, evidence):
        if self.request_id != SID:
            raise FichaError('CASO_NUEVO_NO_PUEDE_CAMBIAR_XML_NI_REABRIR_INTENCION')
        from copy import deepcopy
        from incluirtec.digital_operations.contracts import canonical as api_canonical, digest, decision_fingerprint
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.operation()
            status = self.status()
            if (old.state != 'BLOQUEADA' or old.claim_id is not None or old.attempted_at is not None
                    or old.flow_id is not None or status['STARTIntentado']
                    or status['resultado'].get('codigo') != 'AUTOCOMMIT_NO_CONTROLADO'):
                raise FichaError('REAPERTURA_PROHIBIDA_SOLO_CONCILIAR')
            prior, candidate = deepcopy(old.payload), deepcopy(payload)
            for request in (prior, candidate):
                request['contextoLinix'].pop('fechaSolicitud')
            if api_canonical(prior) != api_canonical(candidate):
                raise FichaError('REAPERTURA_NO_PUEDE_CAMBIAR_IDENTIDAD_INTENCION_O_CONDICIONES')
            self.db.execute('INSERT INTO cd6_observaciones (solicitud,instante,estado_observado,evidencia) VALUES (?,?,?,?)',
                (self.request_id, datetime.now(timezone.utc).isoformat(), 'PREFLIGHT_REABIERTO_SIN_START', self.encrypt(self.packet())))
            self.db.execute("UPDATE cd6_envio SET estado='PREPARADA',fingerprint=?,decision_hash=?,payload=?,result=NULL WHERE solicitud=?",
                (digest(api_canonical(payload)), decision_fingerprint(payload),
                 self.encrypt({'request': payload, 'evidencia': evidence}), self.request_id))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.operation()

    def packet(self):
        row = self.db.execute('SELECT payload FROM cd6_envio WHERE solicitud=?', (self.request_id,)).fetchone()
        if row is None:
            raise FichaError('INTENCION_NO_EXISTE')
        return self.decrypt(row[0])

    def mark_start(self):
        op = self.operation()
        if op.state != 'EXECUTING' or not op.claim_id:
            raise FichaError('START_NO_RECLAMADO')
        try:
            self.db.execute('INSERT INTO cd6_start_guard VALUES (?,?)',
                            (self.request_id, datetime.now(timezone.utc).isoformat()))
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            raise FichaError('START_YA_INTENTADO_SOLO_CONCILIAR') from None

    def claim(self):
        claim = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        changed = self.db.execute("UPDATE cd6_envio SET estado='EXECUTING',claim=?,attempted=? WHERE solicitud=? AND estado='PREPARADA' AND claim IS NULL",
                                  (claim, now, self.request_id)).rowcount
        self.db.commit()
        if changed != 1:
            raise FichaError('START_YA_RECLAMADO_NO_REPETIR')
        return self.operation()

    def checkpoint(self, flow):
        if self.request_id != SID and str(flow) == '16087353':
            raise FichaError('FLUJO_PILOTO_PROTEGIDO')
        current = self.operation()
        if current.flow_id is not None and current.flow_id != str(flow):
            raise FichaError('FLUJO_CONTRADICTORIO')
        self.db.execute("UPDATE cd6_envio SET flow=? WHERE solicitud=? AND estado IN ('EXECUTING','INCIERTA') AND (flow IS NULL OR flow=?)",
                        (str(flow), self.request_id, str(flow)))
        self.db.commit()
        if self.operation().flow_id != str(flow):
            raise FichaError('CHECKPOINT_NO_PERSISTIDO')

    def settle(self, state, result, *, prepared_only=False):
        if state not in ('RADICADA', 'INCIERTA', 'BLOQUEADA'):
            raise FichaError('TRANSICION_INVALIDA')
        if prepared_only and state != 'BLOQUEADA':
            raise FichaError('TRANSICION_INVALIDA')
        # A failed preflight must not overwrite another executor's durable claim.
        condition = ("estado='PREPARADA' AND claim IS NULL AND attempted IS NULL" if prepared_only
                     else "estado IN ('PREPARADA','EXECUTING','INCIERTA')")
        changed = self.db.execute("UPDATE cd6_envio SET estado=?,result=? WHERE solicitud=? AND " + condition,
                        (state, self.encrypt(result), self.request_id)).rowcount
        self.db.execute('INSERT INTO cd6_observaciones (solicitud,instante,estado_observado,evidencia) VALUES (?,?,?,?)',
            (self.request_id, datetime.now(timezone.utc).isoformat(), state,
             self.encrypt({'resultado': result, 'aplicado': changed == 1})))
        self.db.commit()

    def record(self, event, evidence):
        self.db.execute('INSERT INTO cd6_observaciones (solicitud,instante,estado_observado,evidencia) VALUES (?,?,?,?)',
            (self.request_id, datetime.now(timezone.utc).isoformat(), event, self.encrypt(evidence)))
        self.db.commit()

    def claim_workflow_task(self, plan, policy_hash):
        """Durable single-use task claim in the SAME journal, including timeouts."""
        event = 'WF_TAREA_RECLAMADA:' + str(plan['K_FLUJO']) + ':' + str(plan['K_SERIE'])
        self.db.execute('BEGIN IMMEDIATE')
        try:
            op = self.operation()
            policy = self.packet()['evidencia'].get('politicaEjecucion', {})
            if (op.state != 'RADICADA' or str(op.flow_id) != str(plan['K_FLUJO'])
                    or policy.get('sha256') != policy_hash or plan.get('permitido') is not True
                    or self.request_id == SID or str(op.flow_id) in {'16097741','16087353'}):
                raise FichaError('TAREA_SIN_POLITICA_O_RADICACION_CONCILIADA')
            if self.db.execute('SELECT 1 FROM cd6_observaciones WHERE solicitud=? AND estado_observado=?',
                    (self.request_id,event)).fetchone():
                raise FichaError('TAREA_YA_RECLAMADA_SOLO_CONCILIAR')
            # Same transaction as the claim: another task must not bypass an
            # in-flight, crashed or uncertain operation on this owned workflow.
            previous=self.db.execute('SELECT estado_observado FROM cd6_observaciones '
                "WHERE solicitud=? AND estado_observado LIKE 'WF_TAREA_RECLAMADA:%'",
                (self.request_id,)).fetchall()
            for (claimed,) in previous:
                result=self.last_evidence('WORKFLOW_RESULTADO:'+claimed)
                if not isinstance(result,dict) or result.get('estado')!='CONFIRMADA_CONCILIADA':
                    raise FichaError('FLUJO_CON_TAREA_INCIERTA_SOLO_CONCILIAR')
            self.db.execute('INSERT INTO cd6_observaciones (solicitud,instante,estado_observado,evidencia) VALUES (?,?,?,?)',
                (self.request_id,datetime.now(timezone.utc).isoformat(),event,
                 self.encrypt({'plan':plan,'politicaSha256':policy_hash})))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return event

    def status(self):
        if not self.exists():
            return {'estado': 'SIN_INTENCION', 'START': 0}
        op = self.operation()
        row = self.db.execute('SELECT result FROM cd6_envio WHERE solicitud=?', (self.request_id,)).fetchone()
        result = self.decrypt(row[0]) if row[0] else None
        if isinstance(result, dict):
            result.pop('mensajePrivado', None)
        response = {'estado': op.state, 'intencionId': op.intent_id, 'K_FLUJO': op.flow_id,
                'STARTReclamado': op.attempted_at is not None,
                'STARTIntentado': self.db.execute('SELECT 1 FROM cd6_start_guard WHERE solicitud=?', (self.request_id,)).fetchone() is not None,
                'resultado': result}
        if self.request_id != SID:
            response.update(solicitudId=self.request_id, operacionId=op.id, fingerprint=op.fingerprint)
        fact = self.last_evidence('RADICACION_PERSISTIDA_CONCILIADA')
        if (fact and fact.get('K_FLUJO') == op.flow_id
                and fact.get('intencionId') == str(op.intent_id)
                and fact.get('fingerprint') == op.fingerprint):
            response['radicacionPersistida'] = {**fact, 'presentacion': self.presentation(fact, op)}
        return response

    def presentation(self, fact, op):
        confirmed = fact.get('estado') == 'CONFIRMADA_EN_CA090'
        result = {'soloLectura': True, 'observadoEn': fact.get('observadoEn'),
            'asociado': {'estado': 'SOLICITUD_REGISTRADA' if confirmed else 'REGISTRO_POR_VERIFICAR',
                'mensaje': ('Tu solicitud esta registrada. Continua en tramite; no constituye aprobacion.'
                    if confirmed else 'Estamos verificando el registro de tu solicitud.'),
                'proximaAccion': 'No envies otra solicitud. Consulta el seguimiento con el equipo de credito.',
                'aprobacionAcreditada': False},
            'administrador': {'estadoJournal': op.state, 'radicacion': fact['estado'],
                'scPersistida': fact.get('SC') if confirmed else None,
                'actividadesActivasAlCorte': fact.get('actividadesActivas', []),
                'incidencias': fact.get('incidencias', {'estado': 'SIN_CLASIFICAR'}),
                'accionPermitida': 'SOLO_CONCILIAR', 'nuevoStartPermitido': False}}
        institution=fact.get('institucional')
        approval=institution.get('aprobacion') if isinstance(institution,dict) else None
        task=institution.get('actividad') if isinstance(institution,dict) else None
        if (confirmed and isinstance(institution,dict) and institution.get('solicitudId')==self.request_id
                and institution.get('intencionId')==str(op.intent_id) and institution.get('kFlujo')==op.flow_id
                and institution.get('numeroSc')==fact.get('SC')
                and institution.get('versionContrato')=='credihoy-linix-estado-v2'
                and institution.get('estado')=='APROBADA_EN_LINIX_PENDIENTE_FIRMA'
                and isinstance(approval,dict) and approval.get('aprobada') is True
                and approval.get('estadoSolicitud')=='A' and approval.get('actividad')==6774
                and approval.get('estadoActividad')=='E' and approval.get('aprobadorReferencia')
                and approval.get('fecha') and approval.get('kSerie')
                and isinstance(task,dict) and task.get('codigo')==99001065
                and task.get('estado')=='A' and task.get('tipoEjecucion')=='H'
                and task.get('ruta')=='10003122'
                and any(t.get('actividad')==task['codigo'] and str(t.get('K_SERIE'))==task.get('kSerie')
                        for t in fact.get('actividadesActivas',[]) if isinstance(t,dict))
                and institution.get('firma')=={'estado':'PENDIENTE','acreditada':False}
                and institution.get('liquidacion')=={'liquidada':False,'fecha':None}):
            digest=hashlib.sha256(json.dumps({k:v for k,v in institution.items() if k!='evidenciaSha256'},
                                           sort_keys=True,default=str).encode()).hexdigest()
            if hmac.compare_digest(digest,str(institution.get('evidenciaSha256',''))):
                result['asociado'].update(estado='APROBACION_INSTITUCIONAL_REGISTRADA',
                    mensaje='Tu solicitud tiene una aprobacion registrada. La formalizacion y firma siguen pendientes.',
                    aprobacionAcreditada=True)
                result['administrador']['institucional']=institution
        return result

    def last_evidence(self, event):
        row = self.db.execute('SELECT evidencia FROM cd6_observaciones WHERE solicitud=? '
            'AND estado_observado=? ORDER BY id DESC LIMIT 1', (self.request_id, event)).fetchone()
        return self.decrypt(row[0]) if row else None

    def private_result(self):
        row = self.db.execute('SELECT result FROM cd6_envio WHERE solicitud=?', (self.request_id,)).fetchone()
        return self.decrypt(row[0]) if row and row[0] else None

    def resume_legacy_timeout_preflight(self):
        from .gateway import PILOT_REQUEST, PILOT_INTENT
        self.db.execute('BEGIN IMMEDIATE')
        try:
            op = self.operation()
            previous = self.private_result() or {}
            if (self.request_id != PILOT_REQUEST or str(op.intent_id) != PILOT_INTENT
                    or op.state != 'BLOQUEADA' or op.claim_id is not None or op.attempted_at is not None
                    or op.flow_id is not None or self.status()['STARTIntentado']
                    or ('DPI-1050' not in previous.get('mensajePrivado', '')
                        and previous.get('codigo') != 'PILOTO_CONEXION_NO_CONTROLADA')):
                raise FichaError('REAPERTURA_PROHIBIDA_SOLO_CONCILIAR')
            self.db.execute('INSERT INTO cd6_observaciones '
                '(solicitud,instante,estado_observado,evidencia) VALUES (?,?,?,?)',
                (self.request_id,datetime.now(timezone.utc).isoformat(),'PREFLIGHT_CLIENTE_LEGADO_REABIERTO_SIN_START',
                 self.encrypt({'resultadoAnterior':previous,'fingerprintConservado':op.fingerprint})))
            self.db.execute("UPDATE cd6_envio SET estado='PREPARADA',result=NULL WHERE solicitud=?",
                            (self.request_id,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
