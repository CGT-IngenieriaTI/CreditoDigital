"""Ejecutor privado por SSH o consola del clon aislado; sin HTTP ni autoarranque."""
import json
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sys

# Keep the existing writer's type identity when invoked with -m by the SSH executor.
if __name__ == '__main__' and __spec__ is not None:
    sys.modules[__spec__.name] = sys.modules[__name__]

from .condiciones import FichaError, SID, instante

CASE_ACTIONS = ('preparar-caso', 'enviar-caso', 'conciliar-caso', 'estado-caso',
                'preflight-caso', 'diagnosticar-caso', 'continuar-tarea-caso','conciliar-linpag-caso','conciliar-financiero-caso')


def verified_process_deadline():
    if os.name != 'posix':
        raise FichaError('LIMITE_PROCESO_NO_VERIFICADO')
    parent = (Path('/proc') / str(os.getppid()) / 'cmdline').read_bytes().split(b'\0')
    if (Path(os.fsdecode(parent[0])).name != 'timeout'
            or parent[1:4] != [b'-k', b'5', b'180']):
        raise FichaError('LIMITE_PROCESO_NO_VERIFICADO')
    return {'mecanismo':'GNU_TIMEOUT_PADRE','segundos':180,'killAfterSegundos':5,
            'alcance':'PROCESO_NO_CADA_LLAMADA','reversionOracleInterna':'NO_GARANTIZADA'}


class Port:
    """Existing institutional writer, with a case-bound production pilot capability.

    Internal commits and effects of 6964A/6783A are residual risks, not undone by Python.
    """
    number_type = None
    clob_type = None

    def __init__(self, *, pilot_case=False, read_only=False, personal_case=False):
        self.pilot_case = pilot_case
        self.personal_case = personal_case
        self.read_only = read_only
        self.timeout_evidence = None

    def open_connection(self):
        from django.conf import settings
        from incluirtec.services.oracle import get_connection
        from incluirtec.services.oracle_driver import oracle_driver
        if self.pilot_case or self.personal_case:
            target = settings.DATABASES['oracle']
            if (target['HOST'] != '192.168.15.145' or int(target['PORT']) != 1521
                    or target['NAME'].upper() != 'LINIX' or target['USER'].upper() != 'CGTE'
                    or oracle_driver.__name__ not in ('oracledb', 'cx_Oracle')):
                raise FichaError('PILOTO_DESTINO_INSTITUCIONAL_NO_COINCIDE')
        conn = get_connection()
        try:
            conn.autocommit = False
            timeout = 'callTimeout' if oracle_driver.__name__ == 'cx_Oracle' else 'call_timeout'
            try:
                setattr(conn, timeout, 15000)
                if getattr(conn, timeout) != 15000:
                    raise FichaError('PILOTO_CONEXION_NO_CONTROLADA')
                self.timeout_evidence = {'mecanismo':'SDK_CALL_TIMEOUT','milisegundos':15000}
            except Exception as exc:
                if (not (self.pilot_case or self.personal_case) or oracle_driver.__name__ != 'cx_Oracle'
                        or 'DPI-1050' not in str(exc) or oracle_driver.clientversion()[:2] >= (18,1)):
                    raise
                self.timeout_evidence = verified_process_deadline()
            value = conn.autocommit
            disabled = (type(value) is bool and value is False) or (type(value) is int and value == 0)
            if not disabled:
                raise FichaError('PILOTO_CONEXION_NO_CONTROLADA')
            if self.pilot_case or self.personal_case:
                from incluirtec.digital_operations.credihoy_oracle import SESSION_SQL
                cur = conn.cursor()
                try:
                    cur.execute(SESSION_SQL)
                    if cur.fetchone() != ('CGTE', 'CGTE'):
                        raise FichaError('PILOTO_IDENTIDAD_ORACLE_NO_COINCIDE')
                finally:
                    cur.close()
                conn.rollback()
            self.number_type, self.clob_type = oracle_driver.NUMBER, oracle_driver.CLOB
            if self.read_only:
                from incluirtec.digital_operations.oracle_readonly import READ_ONLY_SQL, ReadOnlyOracleConnection
                cur = conn.cursor()
                try:
                    cur.execute(READ_ONLY_SQL)
                finally:
                    cur.close()
                return ReadOnlyOracleConnection(conn, oracle_driver)
            return conn
        except BaseException:
            conn.close()
            raise


def operate(message, journal, port, workflow_user, *, now=None):
    from .gateway import GatewayAdministrativoUnico
    action = message.get('accion')
    if action in CASE_ACTIONS:
        return operate_case(message, journal, port, workflow_user, now=now)
    if action == 'reanudar-preflight':
        raise FichaError('REANUDACION_LEGACY_DESHABILITADA_SOLO_CONCILIAR')
    if journal.request_id == SID and action == 'enviar':
        raise FichaError('PILOTO_PROTEGIDO_NO_MAS_START')
    if action in ('traza', 'traza-cd7'):
        return trace_existing(journal, port, extended=action == 'traza-cd7')
    if action == 'estado':
        return journal.status()
    if action == 'conciliar':
        if not journal.exists() or journal.operation().state not in ('EXECUTING', 'INCIERTA'):
            return journal.status()
        adapter = GatewayAdministrativoUnico(journal, port, workflow_user=workflow_user)
        try:
            result = adapter.lookup(journal.operation(), checkpoint_flow=journal.checkpoint)
            settle_observation(journal, result)
        except Exception as exc:
            journal.settle('INCIERTA', safe_error(exc))
        return journal.status()
    raise FichaError('ACCION_NO_PERMITIDA_USAR_CASO_VERIFICADO')


def operate_personal(body,envelope,journal,port,policy,public_key,*,now=None,resolved_source=None):
    """Personal HTTP bridge over this worker/journal; no administrative grant."""
    from .grant import validar_delegacion_personal,aceptacion_personal_journal,validar_confirmacion_operacion
    from incluirtec.digital_operations.contracts import canonical,digest
    claim=validar_delegacion_personal(envelope,body,public_key,policy,now or instante())
    if journal.request_id!=claim['solicitudId']:raise FichaError('JOURNAL_NO_CORRESPONDE_AL_EXPEDIENTE')
    if body['accion']=='consultar':return journal.status()
    if body['accion']=='preparar':
        from incluirtec.digital_operations.production_preparation import prepare_personal,resolve_personal_source
        source=resolved_source if resolved_source is not None else resolve_personal_source(body['expediente'],claim,port,policy)
        packet=prepare_personal(source,claim)
        if journal.exists():
            old=journal.operation()
            if (str(old.intent_id)!=body['intencionId'] or canonical(old.payload)!=canonical(packet['preparacion']['payload'])):
                raise FichaError('INTENCION_EXISTENTE_INCOMPATIBLE_SOLO_CONCILIAR')
            previous=aceptacion_personal_journal(journal);current=packet['aceptacionSimulacion']
            if set(previous)!=set(current) or any(previous[k]!=current[k] for k in previous.keys()-{'decisionReferencia','decisionSha256'}):
                raise FichaError('ACEPTACION_PERSONAL_EXISTENTE_NO_SE_PUEDE_SUSTITUIR')
            if previous!=current and old.state=='PREPARADA' and old.claim_id is None:
                journal.record('REFERENCIA_DECISION_PERSONAL_ACTUALIZADA',{'aceptacionSimulacion':current,
                    'delegacion':envelope,'peticion':body,'fingerprint':old.fingerprint})
            return journal.status()
        journal.prepare(packet['preparacion']['payload'],{**packet,'usuarioWorkflow':policy['usuarioWorkflow'],
            'delegacionPreparacion':envelope,'peticionPreparacion':body})
        return journal.status()
    if not journal.exists():raise FichaError('INTENCION_PERSONAL_PREPARADA_REQUERIDA')
    row=journal.operation();packet=journal.packet()['evidencia']
    if (str(row.intent_id)!=body['intencionId'] or packet.get('canal')!='CONFIRMACION_PERSONAL_CD6'
            or row.payload['solicitudId']!=body['solicitudId']):
        raise FichaError('INTENCION_EXISTENTE_INCOMPATIBLE_SOLO_CONCILIAR')
    if body['accion']=='conciliar':
        return operate({'accion':'conciliar-caso','solicitudId':body['solicitudId']},journal,port,policy['usuarioWorkflow'],now=now)
    if set(body)!={'accion','solicitudId','intencionId','requestId','confirmacionPersonal'}:
        raise FichaError('CONFIRMACION_PERSONAL_CD6_NO_ACREDITADA')
    proof=aceptacion_personal_journal(journal);confirmation=body['confirmacionPersonal']
    validar_confirmacion_operacion(confirmation,row)
    if (proof['titularReferencia']!=claim['titularSha256'] or any(confirmation[k]!=proof[k] for k in
            ('aceptacionAuditoriaId','aceptacionSha256','simulacionSha256','revisionReferencia',
             'decisionReferencia','decisionSha256','versionCondiciones'))):
        raise FichaError('CONFIRMACION_PERSONAL_DE_OTRAS_CONDICIONES')
    if row.state in {'EXECUTING','INCIERTA'}:
        return operate({'accion':'conciliar-caso','solicitudId':body['solicitudId']},journal,port,policy['usuarioWorkflow'],now=now)
    if row.state!='PREPARADA':return journal.status()
    journal.record('CONFIRMACION_PERSONAL_CD6_VERIFICADA',{'peticion':body,'delegacion':envelope,
        'confirmacionSha256':digest(canonical(confirmation))})
    return execute_prepared(journal,port,policy['usuarioWorkflow'],now=now,verification=journal.status())


def personal_worker(message,settings):
    """HTTP facade uses the same private case journal and the same native executor."""
    import sqlite3
    from hashlib import sha256
    from .grant import perfil_personal,validar_delegacion_personal
    from .journal import Journal
    from incluirtec.digital_operations.production_preparation import selected_request
    cfg,key=perfil_personal();body=message['peticion'];envelope=message['delegacion']
    claim=validar_delegacion_personal(envelope,body,key,cfg,instante())
    sid=selected_request(claim['solicitudId']);action=body['accion']
    expected={'accion','solicitudId','intencionId','requestId'}
    if action=='preparar':expected.add('expediente')
    if action=='confirmar':expected.add('confirmacionPersonal')
    if set(body)!=expected:raise FichaError('PETICION_PERSONAL_CD6_INVALIDA')
    root=Path('/home/ayuda/.credihoy-lecturas-20261013/cd6-administrativo')
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode&0o077:
        raise FichaError('CUSTODIA_INSTITUCIONAL_NO_PRIVADA')
    legacy=root/'intencion-unica.sqlite3'
    if legacy.exists():
        if legacy.is_symlink():raise FichaError('JOURNAL_RUTA_NO_PRIVADA')
        db=sqlite3.connect(legacy.as_uri()+'?mode=ro',uri=True)
        try:
            db.execute('PRAGMA query_only=ON')
            if db.execute('SELECT 1 FROM cd6_envio WHERE solicitud=? LIMIT 1',(sid,)).fetchone():
                raise FichaError('INTENCION_LEGACY_EXISTENTE_SOLO_CONCILIAR')
        finally:db.close()
    folder=root/'casos'/sid;path=folder/'intencion-unica.sqlite3'
    if (folder.resolve()!=root.resolve()/'casos'/sid or folder.is_symlink()
            or (root/'casos').is_symlink() or path.is_symlink()):raise FichaError('CUSTODIA_DEL_EXPEDIENTE_NO_PRIVADA')
    if not path.exists() and action!='preparar':
        if action!='consultar':raise FichaError('INTENCION_PERSONAL_PREPARADA_REQUERIDA')
        return {'estado':'SIN_INTENCION','solicitudId':sid,'intencionId':body['intencionId'],'START':0}
    source=None
    if action=='preparar':
        from incluirtec.digital_operations.production_preparation import prepare_personal,resolve_personal_source
        source=resolve_personal_source(body['expediente'],claim,Port(personal_case=True,read_only=True),cfg)
        prepare_personal(source,claim)
        folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if folder.stat().st_mode&0o077 or path.exists() and path.stat().st_mode&0o077:
        raise FichaError('CUSTODIA_DEL_EXPEDIENTE_NO_PRIVADA')
    journal=Journal(path,settings.SECRET_KEY.encode(),request_id=sid,create=action=='preparar',read_only=action=='consultar')
    try:
        if journal.exists() and sha256(journal.operation().payload['titular']['numeroDocumento'].encode()).hexdigest()!=claim['titularSha256']:
            raise FichaError('JOURNAL_DE_OTRO_TITULAR')
        if action=='confirmar' and cfg.get('startEnabled') is not True:
            raise FichaError('EJECUCION_PERSONAL_CD6_NO_HABILITADA')
        port=Port(personal_case=True,read_only=action!='confirmar')
        result=operate_personal(body,envelope,journal,port,cfg,key,resolved_source=source)
        public={k:result.get(k) for k in ('estado','solicitudId','intencionId','K_FLUJO','fingerprint','STARTReclamado','STARTIntentado')}
        public['solicitudId']=sid
        fact=result.get('radicacionPersistida') or {}
        public['SC']=fact.get('SC') if fact.get('estado')=='CONFIRMADA_EN_CA090' else None
        public['actividadesActivas']=fact.get('actividadesActivas',[])
        public['codigo']=(result.get('resultado') or {}).get('codigo')
        public['puedeConfirmar']=result.get('estado')=='PREPARADA' and cfg.get('startEnabled') is True
        return public
    finally:journal.close()

def operate_case(message, journal, port, workflow_user, *, now=None):
    """Same journal/gateway, new case. No generated dossier or authorization."""
    from incluirtec.digital_operations.production_preparation import selected_request, verify
    sid = selected_request(message.get('solicitudId'))
    if sid != journal.request_id:
        raise FichaError('JOURNAL_NO_CORRESPONDE_AL_EXPEDIENTE')
    action = message['accion']
    if action == 'estado-caso':
        return journal.status()
    if action == 'conciliar-linpag-caso':
        from .gateway import GatewayAdministrativoUnico
        return GatewayAdministrativoUnico(journal,port,workflow_user=workflow_user,
            **({} if now is None else {'clock':lambda:now})).reconcile_native_signing()
    if action == 'conciliar-financiero-caso':
        from .gateway import GatewayAdministrativoUnico
        return GatewayAdministrativoUnico(journal,port,workflow_user=workflow_user,
            **({} if now is None else {'clock':lambda:now})).reconcile_financial_sources()
    if action == 'continuar-tarea-caso':
        from .gateway import GatewayAdministrativoUnico
        return GatewayAdministrativoUnico(journal,port,workflow_user=workflow_user,
            **({} if now is None else {'clock':lambda:now})).continue_workflow_task()
    if action == 'conciliar-caso':
        operate({'accion': 'conciliar'}, journal, port, workflow_user, now=now)
        if journal.exists() and journal.operation().flow_id is not None:
            try:
                collect_case_evidence(journal, port)
            except Exception as exc:
                journal.record('CONCILIACION_LECTURA_INCOMPLETA', safe_error(exc))
        return journal.status()
    if action == 'diagnosticar-caso':
        if not journal.exists():
            raise FichaError('DIAGNOSTICO_REQUIERE_INTENCION_EXISTENTE')
        from .gateway import PILOT_REQUEST, PILOT_INTENT
        row = journal.operation()
        unsent_pilot = (journal.request_id == PILOT_REQUEST and str(row.intent_id) == PILOT_INTENT
                        and row.flow_id is None and row.claim_id is None and row.attempted_at is None
                        and not journal.status()['STARTIntentado'])
        if not unsent_pilot:
            collect_case_evidence(journal, port)
        status = journal.status()
        public = {key: status[key] for key in ('estado', 'solicitudId', 'intencionId', 'K_FLUJO',
                  'STARTReclamado', 'STARTIntentado', 'operacionId', 'fingerprint') if key in status}
        private = journal.private_result() if unsent_pilot else None
        if journal.request_id == PILOT_REQUEST and str(row.intent_id) == PILOT_INTENT and row.flow_id is not None:
            private = {'financieroAntes': journal.last_evidence('PILOTO_FINANCIERO_ANTES_START'),
                       'financieroDespues': journal.last_evidence('PILOTO_FINANCIERO_DESPUES_START'),
                       'registroOracle': journal.last_evidence('EVIDENCIA_ORACLE_SOLO_SELECT')}
        return {**public, **({'radicacionPersistida': status['radicacionPersistida']}
                           if 'radicacionPersistida' in status else {}),
                'codigo': 'DIAGNOSTICO_CIFRADO_SIN_START',
                **({'detallePrivado': private} if private is not None else {})}
    if action == 'preflight-caso':
        from incluirtec.digital_operations.production_preflight import snapshot
        connection = port.open_connection()
        cursor = None
        try:
            connection.autocommit = False
            cursor = connection.cursor()
            from incluirtec.digital_operations.oracle_readonly import ReadOnlyOracleConnection
            if not isinstance(connection, ReadOnlyOracleConnection):
                cursor.execute('SET TRANSACTION READ ONLY')
            observed = snapshot(cursor, follow_paths=True)
            return {'estado': 'PREFLIGHT_SOLO_SELECT', 'solicitudId': sid, **observed, 'START': 0}
        finally:
            if cursor is not None: cursor.close()
            connection.rollback(); connection.close()
    if action == 'preparar-caso':
        if journal.exists():
            from .gateway import PILOT_REQUEST, PILOT_INTENT
            row = journal.operation()
            if (journal.request_id == PILOT_REQUEST and str(row.intent_id) == PILOT_INTENT
                    and row.state == 'BLOQUEADA'):
                prepared = message['preparacion']
                verify(prepared)
                from incluirtec.digital_operations.contracts import canonical
                if canonical(prepared) != canonical(journal.packet()['evidencia']['preparacion']):
                    raise FichaError('REAPERTURA_NO_PUEDE_CAMBIAR_CASO_SELLADO')
                journal.resume_legacy_timeout_preflight()
            return journal.status()
        prepared = message['preparacion']
        from incluirtec.digital_operations.production_preparation import LEGACY_VERSION, VERSION
        if not isinstance(prepared, dict) or prepared.get('versionContrato') not in (LEGACY_VERSION, VERSION):
            raise FichaError('VERSION_PREPARACION_NO_SOPORTADA')
        verify(prepared)
        payload = prepared['payload']
        if payload['solicitudId'] != sid:
            raise FichaError('PREPARACION_NO_CORRESPONDE_AL_EXPEDIENTE')
        execution_policy = {}
        if message.get('politicaEjecucion') is not None:
            from hashlib import sha256
            from incluirtec.digital_operations.production_preflight import verify_execution_policy
            policy = verify_execution_policy(message['politicaEjecucion'],request_id=sid,
                intent_id=payload['intencionId'],
                holder_reference=sha256(payload['titular']['numeroDocumento'].encode()).hexdigest(),
                now=now or instante(),for_start=True)
            execution_policy = {'politicaEjecucion':policy}
        profile = message.get('perfilCore')
        if (not isinstance(profile, dict) or set(profile) != {'sucursal', 'nomina', 'tipoNomina'}
                or any(not isinstance(v, str) or not v for v in profile.values())):
            raise FichaError('PAGADURIA_CORE_DEL_EXPEDIENTE_REQUERIDA')
        from incluirtec.digital_operations.production_preflight import require_boundary, require_configuration
        if message.get('preflight') is None:
            raise FichaError('PREFLIGHT_PRODUCTIVO_REQUERIDO')
        if prepared['versionContrato'] == VERSION:
            from incluirtec.digital_operations.credihoy_oracle import IsolatedOracleWritePort
            destination = {}
            if isinstance(port,IsolatedOracleWritePort):
                conn = port.open_connection()
                try:
                    from incluirtec.digital_operations.production_preflight import snapshot
                    cur = conn.cursor()
                    try:
                        actual = snapshot(cur,follow_paths=True)
                    finally:
                        cur.close()
                    from incluirtec.digital_operations.contracts import canonical
                    if canonical(actual) != canonical(message['preflight']):
                        raise FichaError('CD6_QA_PREFLIGHT_DESACTUALIZADO')
                finally:
                    conn.rollback();conn.close()
                require_configuration(actual,expected_identity=port.preflight_identity())
                destination = {'destinoOracle':port.identity()}
            else:
                require_configuration(message['preflight'])
            from .gateway import GatewayAdministrativoUnico
            GatewayAdministrativoUnico(journal, port, workflow_user=workflow_user)
            journal.prepare(payload, {'preparacion': prepared, 'perfilCore': profile,
                'preflight': message['preflight'], 'xml': prepared['xml'], 'xmlSha256': prepared['xmlSha256'],
                'usuarioWorkflow': workflow_user, **destination, **execution_policy})
            return journal.status()
        require_boundary(message['preflight'], message.get('certificaciones'), now=now or instante())
        from .grant import crear
        authority = prepared['autorizacion']
        grant = crear(payload, authority, workflow_user, now or instante())
        grant['xmlSha256'] = prepared['xmlSha256']
        journal.prepare(payload, {'preparacion': prepared, 'autorizacion': authority,
            'perfilCore': profile, 'certificaciones': message['certificaciones'],
            'preflight': message['preflight'], 'xml': prepared['xml'], 'xmlSha256': prepared['xmlSha256'],
            'usuarioWorkflow': workflow_user,
            'grantAdministrativo': grant, **execution_policy})
        return journal.status()
    if action != 'enviar-caso' or message.get('autorizarStartUnico') is not True:
        raise FichaError('START_ESPECIFICO_NO_AUTORIZADO')
    current = journal.operation()
    if str(current.intent_id) != message.get('intencionId') or current.fingerprint != message.get('fingerprint'):
        raise FichaError('CONFIRMACION_DE_INTENCION_NO_COINCIDE')
    if current.state in ('EXECUTING', 'INCIERTA'):
        return operate({'accion': 'conciliar'}, journal, port, workflow_user, now=now)
    if current.state != 'PREPARADA':
        return journal.status()
    return execute_prepared(journal, port, workflow_user, now=now, verification=journal.status())


def execute_prepared(journal, port, workflow_user, *, now, verification):
    from .gateway import GatewayAdministrativoUnico
    clock = {} if now is None else {'clock': lambda: now}
    adapter = GatewayAdministrativoUnico(journal, port, workflow_user=workflow_user, **clock)
    try:
        adapter.preflight_duplicate()
    except Exception as exc:
        journal.settle('BLOQUEADA', {**safe_error(exc), 'mensajePrivado': str(exc)}, prepared_only=True)
        return journal.status()
    operation = journal.claim()
    try:
        observation = adapter.submit(operation, checkpoint_flow=journal.checkpoint)
        settle_observation(journal, observation)
    except Exception as exc:
        journal.settle('INCIERTA', {**safe_error(exc), 'mensajePrivado': str(exc)})
    if journal.operation().state == 'INCIERTA':
        # One read-only reconciliation, never a retry of START; observations append.
        try:
            observation = adapter.lookup(journal.operation(), checkpoint_flow=journal.checkpoint)
            settle_observation(journal, observation)
        except Exception as exc:
            journal.settle('INCIERTA', {**safe_error(exc), 'mensajePrivado': str(exc)})
    if journal.request_id != SID and journal.operation().flow_id is not None:
        try:
            collect_case_evidence(journal, port)
        except Exception as exc:
            journal.record('CAPTURA_PARCIAL_PENDIENTE_SOLO_CONCILIAR', safe_error(exc))
    return {**journal.status(), 'verificacionPrevia': verification}


def summarize_incidents(evidence):
    """Classify recorded errors by observed task state, never erase or resolve them."""
    count = evidence['erroresWorkflow']
    tasks = {}
    for task in evidence['tareas']:
        key = str(task['K_SERIE'])
        tasks[key] = None if key in tasks else task['I_ESTTAR']
    executed = active = 0
    rows = evidence.get('erroresDetalle', [])
    complete = (evidence.get('erroresDetalleIncompleto') is False and len(rows) == count)
    if complete:
        for row in rows:
            state = tasks.get(str(row.get('K_SERIE'))) if str(row.get('K_FLUJO')) == str(evidence['K_FLUJO']) else None
            executed += state == 'E'
            active += state == 'A'
    return {'registradas': count, 'enTareasEjecutadas': executed, 'enTareasActivas': active,
        'sinClasificar': count - executed - active, 'detalleCompleto': complete,
        'estado': ('CONSERVADAS_EN_TAREAS_EJECUTADAS' if count and executed == count else
                   'SIN_ERRORES_REGISTRADOS' if count == 0 and complete else 'REQUIEREN_REVISION')}


def collect_case_evidence(journal, port):
    """Read only the exact checkpoint; encrypted XML/CA090, never public stdout."""
    from dataclasses import asdict
    from incluirtec.digital_operations.credihoy_repository import CredihoyReadRepository, WORKFLOW_ERROR_DETAILS_SQL
    from incluirtec.digital_operations.credihoy_oracle import OracleEvidenceRepository
    from incluirtec.digital_operations.credihoy import reference_from_reply, clob_text, CredihoyEvidenceValidator
    op = journal.operation()
    if journal.request_id == SID or str(op.flow_id) == '16087353' or op.flow_id is None:
        raise FichaError('EVIDENCIA_NUEVA_REQUIERE_CHECKPOINT_DISTINTO_DEL_PILOTO')
    conn = port.open_connection()
    cur = None
    try:
        conn.autocommit = False
        cur = conn.cursor()
        from incluirtec.digital_operations.oracle_readonly import ReadOnlyOracleConnection
        if not isinstance(conn, ReadOnlyOracleConnection):
            cur.execute('SET TRANSACTION READ ONLY')
        repository = CredihoyReadRepository(cur)
        records = repository.radication_records(op.flow_id)
        result = {'K_FLUJO': op.flow_id, 'intencionId': str(op.intent_id),
            'n_error113': {'disponible': False, 'motivo': 'NO_EXPUESTO_POR_START'},
            '113': [asdict(row) for row in records],
            'tareas': OracleEvidenceRepository(cur).task_rows(op.flow_id),
            'erroresWorkflow': OracleEvidenceRepository(cur).errors(op.flow_id)}
        journal.record('EVIDENCIA_113_Y_TAREAS_SOLO_SELECT', result)
        applications = []
        controls = []
        for record in records:
            if record.xml_new and record.xml_output:
                try:
                    reference = reference_from_reply(record.xml_new, record.xml_output, op.payload)
                    applications.extend(repository.application_rows(reference))
                    controls.append({'tipo': reference.application_type, 'numero': reference.application_number,
                        'filas': OracleEvidenceRepository(cur).controls(reference, administrative=True)})
                except Exception as exc:
                    result['SCNoConfirmada'] = safe_error(exc)
        result['CA090'] = applications
        result['controlesCA090'] = controls
        result['3883'] = repository.reference_records(op.flow_id)
        result['6783'] = repository.evaluation_records(op.flow_id)
        result['4965'] = repository.central_risk_records(op.flow_id)
        cur.execute(WORKFLOW_ERROR_DETAILS_SQL,
                    {'flow_id': int(op.flow_id)})
        keys = [column[0] for column in cur.description]
        rows = cur.fetchmany(101)
        result['erroresDetalleIncompleto'] = len(rows) > 100
        result['erroresDetalle'] = [dict(zip(keys, [clob_text(value) if hasattr(value, 'read') else value
                                                   for value in row])) for row in rows[:100]]
        # Oracle dates/decimals remain private and serializable in existing custody.
        journal.record('EVIDENCIA_ORACLE_SOLO_SELECT', json.loads(json.dumps(result, default=str)))
        # SC existence and workflow completion are independent facts. This does
        # not promote INCIERTA or release the durable START guard.
        validator = CredihoyEvidenceValidator(repository)
        fact = {'K_FLUJO': op.flow_id, 'intencionId': str(op.intent_id),
                'fingerprint': op.fingerprint, 'observadoEn': datetime.now(timezone.utc).isoformat(),
                'STARTNuevos': 0}
        try:
            reference = validator._reference_by_flow(op, op.flow_id, records=records)
            confirmed = validator._confirmed_observation(op, op.flow_id, reference)
            if not records or any(record.task_state != 'E' for record in records):
                raise FichaError('RADICACION_113_NO_EJECUTADA')
            active = [task for task in result['tareas'] if task['I_ESTTAR'] == 'A']
            fact.update({
                'estado': 'CONFIRMADA_EN_CA090', 'SC': confirmed.application_number,
                'erroresWorkflow': result['erroresWorkflow'],
                'workflow': 'CON_INCIDENCIA' if result['erroresWorkflow'] else 'SIN_ERRORES_REGISTRADOS',
                'incidencias': summarize_incidents(result),
                'actividadesActivas': [{'actividad': task['K_IDACTI'], 'K_SERIE': task['K_SERIE']}
                                      for task in active],
            })
            try:
                institution=repository.institutional_state(op,reference,fact['observadoEn'],incidents=fact['incidencias'])
                if institution is not None:
                    fact['institucional']=institution
            except Exception as exc:
                fact['lecturaInstitucional']={'estado':'NO_DISPONIBLE',**safe_error(exc)}
            try:
                fact['postfirmaNativa']=repository.native_signing_state(op,reference,fact['observadoEn'])
            except Exception as exc:
                fact['postfirmaNativa']={'estado':'NO_DISPONIBLE',**safe_error(exc)}
        except Exception as exc:
            fact.update(estado='NO_CONFIRMADA', **safe_error(exc))
        journal.record('RADICACION_PERSISTIDA_CONCILIADA', fact)
    finally:
        if cur is not None: cur.close()
        conn.rollback(); conn.close()


def trace_existing(journal, port, *, extended=False):
    """Read the already checkpointed flow only; private output is sealed by the CLI."""
    if not journal.exists():
        raise FichaError('TRAZA_REQUIERE_INTENCION_EXISTENTE')
    op = journal.operation()
    if (str(op.flow_id) != '16087353' or str(op.intent_id) != '0082f640-23fe-4ebd-b94a-99805dd3de60'
            or not journal.status()['STARTIntentado']):
        raise FichaError('TRAZA_SOLO_FLUJO_Y_START_ACREDITADOS')
    conn = port.open_connection()
    cur = None
    result = {'estado': 'TRAZA_PRIVADA_SOLO_SELECT', 'K_FLUJO': op.flow_id,
              'intencionId': str(op.intent_id), 'consultasPrivadas': {}}
    try:
        conn.autocommit = False
        value = conn.autocommit
        if not ((type(value) is bool and value is False) or (type(value) is int and value == 0)):
            raise FichaError('AUTOCOMMIT_NO_CONTROLADO')
        cur = conn.cursor()
        queries = (
            ('tareas', '''SELECT t.K_SERIE,t.K_IDACTI,t.K_IDRUTA,t.I_ESTTAR,t.K_REPOSI,
                a.I_EJECUC,a.N_ACTIVI,a.N_ACCION FROM LINIX.WF1711D0000 t
                LEFT JOIN LINIX.WF1706MACTIVID a ON a.K_IDACTI=t.K_IDACTI
                WHERE t.K_FLUJO=:flow_id AND ROWNUM<=20 ORDER BY t.K_SERIE''', 20),
            ('errores_total', 'SELECT COUNT(*) AS TOTAL FROM LINIX.WF1750DERREJEC WHERE K_FLUJO=:flow_id', 1),
            ('errores', 'SELECT * FROM LINIX.WF1750DERREJEC WHERE K_FLUJO=:flow_id AND ROWNUM<=20', 20),
            ('repositorio113', '''SELECT t.K_SERIE,t.I_ESTTAR,
                XMLSERIALIZE(CONTENT r.N_XML_ENT AS CLOB) AS ENTRADA,
                XMLSERIALIZE(CONTENT r.N_XML_ENT_NEW AS CLOB) AS ENTRADA_NUEVA,
                XMLSERIALIZE(CONTENT r.N_XML_SAL AS CLOB) AS SALIDA
                FROM LINIX.WF1711D0000 t LEFT JOIN LINIX.CE8002TREPOSIT r ON r.K_REPOSI=t.K_REPOSI
                WHERE t.K_FLUJO=:flow_id AND t.K_IDACTI=113 AND ROWNUM<=4''', 4),
        )
        if extended:
            queries += (
                ('entrada3883', '''SELECT t.K_SERIE,t.K_REPOSI,
                    XMLSERIALIZE(CONTENT t.N_XML_ENT AS CLOB) AS TAREA_ENTRADA,
                    XMLSERIALIZE(CONTENT t.N_XML_ENT_NEW AS CLOB) AS TAREA_ENTRADA_NUEVA,
                    XMLSERIALIZE(CONTENT t.N_XML_SAL AS CLOB) AS TAREA_SALIDA,
                    XMLSERIALIZE(CONTENT r.N_XML_ENT AS CLOB) AS ENTRADA,
                    XMLSERIALIZE(CONTENT r.N_XML_ENT_NEW AS CLOB) AS ENTRADA_NUEVA,
                    XMLSERIALIZE(CONTENT r.N_XML_SAL AS CLOB) AS SALIDA
                    FROM LINIX.WF1711D0000 t LEFT JOIN LINIX.CE8002TREPOSIT r ON r.K_REPOSI=t.K_REPOSI
                    WHERE t.K_FLUJO=:flow_id AND t.K_IDACTI=3883 AND ROWNUM<=4''', 4),
                ('flujo', '''SELECT K_FLUJO,N_FLUJO,K_MODELO,K_PROCES,N_REFERE,K_IDTERC_CLI,F_INICIO,I_FINALI
                    FROM LINIX.WF1710MFLUJO WHERE K_FLUJO=:flow_id''', 1),
                ('fuente_referencias', '''SELECT LINE,TEXT FROM ALL_SOURCE WHERE OWNER='LINIX'
                    AND NAME='PK_CA_WF_REFERENCIA' AND TYPE='PACKAGE BODY' AND ROWNUM<=3000 ORDER BY LINE''', 3000),
                ('fuente_radicacion113', '''SELECT LINE,TEXT FROM ALL_SOURCE WHERE OWNER='LINIX'
                    AND NAME='PK_CA_WF_RADICA_SOL_CREDITO' AND TYPE='PACKAGE BODY' AND ROWNUM<=3000 ORDER BY LINE''', 3000),
                ('referencia_oficial_flujo', '''SELECT * FROM LINIX.WF1797TREFER
                    WHERE K_FLUJO=:flow_id AND ROWNUM<=10''', 10),
                ('ubicacion_paquetes', '''SELECT OWNER,OBJECT_NAME,OBJECT_TYPE,STATUS FROM ALL_OBJECTS
                    WHERE OBJECT_NAME IN ('PK_CA_WF_REFERENCIA','PK_CA_WF_RADICA_SOL_CREDITO')
                    AND OBJECT_TYPE IN ('PACKAGE','PACKAGE BODY') AND ROWNUM<=10''', 10),
                ('sinonimos_paquetes', '''SELECT OWNER,SYNONYM_NAME,TABLE_OWNER,TABLE_NAME FROM ALL_SYNONYMS
                    WHERE SYNONYM_NAME IN ('PK_CA_WF_REFERENCIA','PK_CA_WF_RADICA_SOL_CREDITO') AND ROWNUM<=10''', 10),
                ('fuente_visible_referencias', '''SELECT OWNER,TYPE,LINE,TEXT FROM ALL_SOURCE
                    WHERE NAME='PK_CA_WF_REFERENCIA' AND TYPE IN ('PACKAGE','PACKAGE BODY')
                    AND ROWNUM<=3000 ORDER BY OWNER,TYPE,LINE''', 3000),
            )
        def private_value(value):
            if hasattr(value, 'read'):
                value = value.read(1, 512001)
            if isinstance(value, str) and len(value) > 512000:
                raise FichaError('TRAZA_EXCEDE_LIMITE_PRIVADO')
            return value
        for name, sql, limit in queries:
            try:
                if not sql.lstrip().upper().startswith('SELECT '):
                    raise FichaError('TRAZA_SOLO_SELECT')
                cur.execute(sql, {'flow_id': int(op.flow_id)} if ':flow_id' in sql else {})
                keys = [column[0] for column in cur.description]
                rows = cur.fetchmany(limit + 1)
                result['consultasPrivadas'][name] = {'incompleta': len(rows) > limit,
                    'filas': [{key: private_value(value) for key, value in zip(keys, row)}
                              for row in rows[:limit]]}
            except Exception as exc:
                result['consultasPrivadas'][name] = {**safe_error(exc), 'mensajePrivado': str(exc)}
        return result
    finally:
        for cleanup in (conn.rollback, cur.close if cur is not None else lambda: None, conn.close):
            try:
                cleanup()
            except Exception:
                pass


def safe_error(exc):
    code = getattr(exc.args[0], 'code', None) if exc.args else None
    public = getattr(exc, 'code', None)
    if isinstance(exc, FichaError):
        public = str(exc)
    if type(exc) is ValueError:
        import re
        if re.fullmatch('CD6_[A-Z0-9_]{1,100}',str(exc)):public=str(exc)
    return {'codigo': public or 'RESULTADO_INCIERTO_CONCILIAR_SIN_START', 'oracleCode': code}


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FichaError('JSON_CON_CAMPOS_DUPLICADOS')
        result[key] = value
    return result


def settle_observation(journal, observation):
    from dataclasses import asdict
    confirmed = (observation.outcome == 'RADICATED' and observation.cut_verified
                 and observation.activity_current in (6964,4965,6784) and observation.workflow_error_count == 0)
    journal.settle('RADICADA' if confirmed else 'INCIERTA', asdict(observation))


def main():
    # This entry point is only for the already authorized institutional SSH account.
    if os.name != 'posix' or not Path('/opt/api_data/manage.py').is_file():
        raise FichaError('EJECUTOR_SOLO_EN_API_DATA_INSTITUCIONAL')
    import pwd
    qa_path = os.environ.get('CD6_ORACLE_QA_CONFIG')
    if ((not qa_path and pwd.getpwuid(os.getuid()).pw_name != 'ayuda')
            or (qa_path and os.getuid() == 0)):
        raise FichaError('CUENTA_SSH_NO_AUTORIZADA')
    verified_process_deadline()
    os.umask(0o077)
    sys.dont_write_bytecode = True
    sys.path.insert(0, '/opt/api_data')
    os.chdir('/opt/api_data')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core_api.settings')
    import django
    django.setup()
    logging.disable(logging.CRITICAL)
    from django.conf import settings
    from incluirtec.services.oracle import get_connection
    import incluirtec
    import incluirtec.services
    bundle = str(Path(__file__).as_posix().split('/tools/cd6/')[0])
    overlay = bundle + '/api_overlay/incluirtec'
    incluirtec.__path__.insert(0, overlay)
    incluirtec.services.__path__.insert(0, overlay + '/services')
    for name in ('xml_builder', 'credit_line_config', 'credit_validators', 'xml_templates'):
        if 'incluirtec.services.' + name in sys.modules:
            raise FichaError('MODULO_FINANCIERO_PREVIAMENTE_CARGADO_NO_MEZCLAR_VERSIONES')
    from incluirtec.digital_operations.credihoy_oracle import IsolatedOracleWritePort
    qa_port = IsolatedOracleWritePort.from_file(qa_path) if qa_path else None
    from incluirtec.services.oracle_driver import oracle_driver
    raw = sys.stdin.buffer.read(24_000_001)
    if len(raw) > 24_000_000:
        raise FichaError('MENSAJE_EXCEDE_LIMITE')
    message = json.loads(raw, object_pairs_hook=unique)
    if message.get('accion')=='personal':
        if set(message)!={'accion','peticion','delegacion'}:raise FichaError('PETICION_PERSONAL_CD6_INVALIDA')
        print(json.dumps(personal_worker(message,settings),ensure_ascii=True))
        return 0
    new_case = message.get('accion') in CASE_ACTIONS
    if qa_port is not None and not new_case:
        raise FichaError('CD6_QA_SOLO_ACCIONES_DE_CASO')
    if not new_case and message.get('accion') in ('enviar', 'reanudar-preflight'):
        raise FichaError('PILOTO_PROTEGIDO_NO_MAS_START')
    root = (Path.home()/'.credihoy-cd6-qa'/str(qa_port.config['dbid']) if qa_port is not None
            else Path('/home/ayuda/.credihoy-lecturas-20261013/cd6-administrativo'))
    root.mkdir(mode=0o700, exist_ok=True,parents=qa_port is not None)
    if root.stat().st_mode & 0o077:
        raise FichaError('CUSTODIA_INSTITUCIONAL_NO_PRIVADA')
    from .journal import Journal
    if new_case:
        from incluirtec.digital_operations.production_preparation import selected_request
        sid = selected_request(message.get('solicitudId'))
        selected_root = root / 'casos' / sid
        selected_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if selected_root.resolve() != root.resolve() / 'casos' / sid or selected_root.stat().st_mode & 0o077:
            raise FichaError('CUSTODIA_DEL_EXPEDIENTE_NO_PRIVADA')
        journal = Journal(selected_root / 'intencion-unica.sqlite3', settings.SECRET_KEY.encode(), request_id=sid)
    else:
        journal = Journal(root / 'intencion-unica.sqlite3', settings.SECRET_KEY.encode())
    try:
        port = qa_port or Port()
        workflow_user = getattr(settings, 'INCLUIRTEC_WORKFLOW_USER', None)
        if journal.exists():
            workflow_user = journal.operation().workflow_user or workflow_user
        elif message.get('accion') == 'preparar-caso' and not workflow_user:
            # The existing institutional helper resolves only the configured/session actor.
            from incluirtec.services.procedures import resolve_workflow_user
            conn = port.open_connection()
            conn.autocommit = False
            cur = conn.cursor()
            try:
                workflow_user = resolve_workflow_user(cur)
            finally:
                conn.rollback()
                cur.close()
                conn.close()
        from incluirtec.digital_operations.contracts import CREDIHOY_TECHNICAL_VERSION
        from incluirtec.digital_operations.production_preparation import VERSION
        if qa_port is None and (message.get('accion') in ('preflight-caso','conciliar-financiero-caso')
                or (journal.exists() and journal.operation().payload.get('versionContrato') == CREDIHOY_TECHNICAL_VERSION)
                or message.get('preparacion', {}).get('versionContrato') == VERSION):
            from incluirtec.digital_operations.oracle_readonly import RealOracleReadPort
            port = RealOracleReadPort(call_timeout_ms=15000)
            # No public endpoint/flag. Only the already sealed unique intention can select the writer.
            from .gateway import PILOT_REQUEST, PILOT_INTENT
            if (message.get('accion') in ('enviar-caso','conciliar-caso','diagnosticar-caso','preflight-caso')
                    and journal.request_id == PILOT_REQUEST
                    and journal.exists() and str(journal.operation().intent_id) == PILOT_INTENT):
                port = Port(pilot_case=True, read_only=message['accion'] != 'enviar-caso')
        result = operate(message, journal, port, workflow_user)
        print(json.dumps(result, default=str, ensure_ascii=True))
    finally:
        journal.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps(safe_error(error)))
        raise SystemExit(2)
