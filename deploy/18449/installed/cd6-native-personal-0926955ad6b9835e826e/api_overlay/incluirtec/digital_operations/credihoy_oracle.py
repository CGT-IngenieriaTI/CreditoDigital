"""Shared START protocol. Production closed; isolated clone transport is explicit."""

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

from django.db import connections

from .contracts import (
    ADMIN_SCOPE, CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION, Observation, ProtocolError, State, canonical,
    decision_fingerprint, digest, normalize_request,
)
from .credihoy import (
    CredihoyEvidenceValidator, LINE, build_radication_xml, numeric_id, validate_request,
)
from .credihoy_repository import CredihoyReadRepository


START_SQL = """
DECLARE
    n_trama_x XMLTYPE;
BEGIN
    n_trama_x := XMLTYPE(:n_trama);
    LINIX.PK_WF_FLUJO.PR_INICIAR_FLUJO_RET(
        :n_k_proces, :n_k_modelo, :n_flujo, SYSDATE, :n_k_usuari,
        n_trama_x, :n_refere, :n_modapl, :n_k_sucurs, :n_i_commit, :v_k_flujo
    );
END;
"""
SESSION_SQL = """
SELECT SYS_CONTEXT('USERENV', 'SESSION_USER'), SYS_CONTEXT('USERENV', 'CURRENT_USER')
FROM DUAL
"""
TASKS_SQL = """
SELECT t.K_SERIE, t.K_IDACTI, t.K_IDRUTA, t.I_ESTTAR, a.I_EJECUC
FROM LINIX.WF1711D0000 t
LEFT JOIN LINIX.WF1706MACTIVID a ON a.K_IDACTI = t.K_IDACTI
WHERE t.K_FLUJO = :flow_id
ORDER BY t.K_SERIE
"""
ERRORS_SQL = "SELECT COUNT(*) FROM LINIX.WF1750DERREJEC WHERE K_FLUJO = :flow_id"
CONTROLS_SQL = """
SELECT I_APROBA, I_LIQUID, K_IDTERC_PRM
FROM LINIX.CA090MGSOLCRED
WHERE K_TIPODR = :application_type AND K_NUMDOC = :application_number
"""
ADMIN_CONTROLS_SQL = """
SELECT I_APROBA, I_LIQUID, K_IDTERC_PRM, N_PAGARE
FROM LINIX.CA090MGSOLCRED
WHERE K_TIPODR = :application_type AND K_NUMDOC = :application_number
"""
FLOW_BY_INTENT_SQL = """
SELECT K_FLUJO, K_MODELO, K_PROCES
FROM LINIX.WF1710MFLUJO
WHERE N_FLUJO = :intent_name
"""
ADMIN_USERS = frozenset({"SYS", "SYSTEM", "SYSBACKUP", "SYSDG", "SYSKM"})
BLOCKED = "CREDIHOY_ORACLE_NO_AUTORIZADO"


def contractual_stage_requirements():
    """Source-specific diagnostics, not an invented pre-START signing order."""
    return [
        {"etapa": "PRE_START", "requisito": "CONTENCION_NATIVA_NO_ACREDITADA",
         "estado": "NO_CONTRASTADO",
         "mecanismo": "START devuelve flujo, no SC; 113 crea SC despues de START. "
                      "121/10002852: AC7423, control DH7424; Consejo6094/10002856 humano",
         "artefacto": "PK_WF_FLUJO y cinco ejecutores wrapped: exclusion efectiva de "
                      "cola/recuperacion/masiva/concurrencia para 121 y estamentos; "
                      "WF1706MACTIVID H no basta. WF1713DRUTAFLU 7423/7424, "
                      "WF1714DCAMINOR y version/efectos del prefijo del modelo"},
        {"etapa": "POST_SC_DOCUMENTO_DEFINITIVO",
         "requisito": "CALENDARIO_DEFINITIVO_REQUIERE_CONCILIACION_O_NUEVA_ACEPTACION",
         "estado": "NO_CONTRASTADO",
         "artefacto": "cotizacion.exigir_calendario_formalizacion: oferta aceptada, SC/flujo propios "
                      "en WF1710MFLUJO/WF1711D0000/CE8002TREPOSIT/CA090MGSOLCRED "
                      "y calendario nativo conciliado; dependencias CA083/SOLIRESUM/AMORTIZACION/CICLO"},
        {"etapa": "FIRMA_DOCUMENTO_DEFINITIVO", "requisito": "FIRMA_VERIFICABLE_NO_ACREDITADA",
         "estado": "NO_CONTRASTADO",
         "artefacto": "Documento definitivo recuperado y evidencia autentica de integridad, "
                      "identidad y participacion; LINPAG/SERVIC y salida propia de tarea "
                      "en CE8002TREPOSIT. No OTP de centrales, PDF simulado ni bandera administrativa"},
        {"etapa": "CONTINUIDAD_POSTFIRMA", "requisito": "RECUPERACION_POSTFIRMA_REQUERIDA",
         "estado": "NO_CONTRASTADO",
         "artefacto": "Reserva durable postfirma; archivo recuperado, verificador tipado, integridad "
                      "y custodia, SC/pagare/calendario/condiciones aceptadas propios. Conciliar "
                      "liberacion nativa actual y cada paso reservado; nunca repetir START, 113 o firma. "
                      "Puerto financiero exclusivamente sintetico; Consejo/Comite exclusivamente humano"},
    ]


class SimulatedOracleConnection:
    """Offline port type, NOT a wrapper for an Oracle driver's connection."""

    def cursor(self):
        raise NotImplementedError

    def commit(self):
        raise NotImplementedError

    def rollback(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class SimulatedOraclePort:
    """Test-only interface. No connection factory, DSN, credentials or real driver."""

    number_type = "SIMULATED_NUMBER"
    clob_type = "SIMULATED_CLOB"

    def open_connection(self):
        raise NotImplementedError

    def workflow_context(self, operation, snapshot):
        """Explicit synthetic effects/human evidence for the offline planner only."""
        raise NotImplementedError

    def execute_workflow_step(self, operation, plan):
        """No SQL implementation: only an offline fixture can implement this port."""
        raise NotImplementedError

    def recover_post_signature(self, operation):
        """Return RecoveredSigningEvidence from a trusted offline verifier/custody.

        Read-only: do not submit signatures, continue tasks or synthesize success
        from transactionId, HTTP status or a native released-task flag.
        """
        raise NotImplementedError


ISOLATED_IDENTITY_SQL = """SELECT d.DBID,d.NAME,d.DB_UNIQUE_NAME,d.DATABASE_ROLE,d.OPEN_MODE,
    SYS_CONTEXT('USERENV','SERVICE_NAME'),SYS_CONTEXT('USERENV','SERVER_HOST'),
    SYS_CONTEXT('USERENV','INSTANCE_NAME'),SYS_CONTEXT('USERENV','SESSION_USER'),
    SYS_CONTEXT('USERENV','CURRENT_USER'),SYS_CONTEXT('USERENV','ISDBA'),p.SPID
    FROM V$DATABASE d JOIN V$SESSION s ON s.SID=TO_NUMBER(SYS_CONTEXT('USERENV','SID'))
    JOIN V$PROCESS p ON p.ADDR=s.PADDR"""
ISOLATED_PARAMETERS_SQL = "SELECT NAME,VALUE FROM V$PARAMETER WHERE NAME IN ('job_queue_processes','aq_tm_processes')"
ISOLATED_LINKS_SQL = 'SELECT COUNT(*) FROM DBA_DB_LINKS'


class IsolatedOracleWritePort:
    """Real SDK connection confined to a co-resident, network-disconnected Oracle clone."""

    synthetic = False
    number_type = None
    clob_type = None
    FIELDS = frozenset(('version','host','port','service','user','passwordFile','dbid',
                        'productionDbid','dbName','dbUniqueName','serverHost','instanceName'))

    def __init__(self, config):
        from .oracle_readonly import _technical_user
        if not isinstance(config, dict) or set(config) != self.FIELDS:
            raise ProtocolError('CD6_QA_CONFIG_INVALIDA')
        if (config['version'] != 'cd6-oracle-aislado-v1' or config['host'] != '127.0.0.1'
                or type(config['port']) is not int or not 1 <= config['port'] <= 65535
                or type(config['dbid']) is not int or type(config['productionDbid']) is not int
                or min(config['dbid'], config['productionDbid']) <= 0
                or config['dbid'] == config['productionDbid'] or not _technical_user(config['user'])):
            raise ProtocolError('CD6_QA_DESTINO_PRODUCTIVO_O_INVALIDO')
        for field in ('service','dbName','dbUniqueName','serverHost','instanceName'):
            value = config[field]
            if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.$#-]{1,128}',value)
                    or value.lower() == '192.168.15.145'
                    or value.lower().split('.')[0] in ('linix','svr-cgt-orabd')):
                raise ProtocolError('CD6_QA_DESTINO_PRODUCTIVO_O_INVALIDO')
        if not isinstance(config['passwordFile'], str) or not Path(config['passwordFile']).is_absolute():
            raise ProtocolError('CD6_QA_SECRETO_PRIVADO_REQUERIDO')
        self.config = MappingProxyType(dict(config))

    @staticmethod
    def _private_bytes(path):
        path = Path(path)
        if os.name != 'posix':
            raise ProtocolError('CD6_QA_CUSTODIA_NO_PRIVADA')
        fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 16384):
                raise ProtocolError('CD6_QA_CUSTODIA_NO_PRIVADA')
            return stream.read(16384)

    @classmethod
    def from_file(cls, path):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ProtocolError('CD6_QA_CONFIG_INVALIDA')
                result[key] = value
            return result
        return cls(json.loads(cls._private_bytes(path).decode('utf-8'), object_pairs_hook=unique))

    def identity(self):
        return {key: self.config[key] for key in ('host','port','service','dbid','productionDbid','dbName',
                    'dbUniqueName','serverHost','instanceName','user')}

    def preflight_identity(self):
        c = self.config
        return {'DB_NAME':c['dbName'],'DB_UNIQUE_NAME':c['dbUniqueName'],'SERVICE_NAME':c['service'],
                'INSTANCE_NAME':c['instanceName'],'HOST_NAME':c['serverHost'],'SESSION_USER':c['user']}

    @staticmethod
    def _require_local_isolation(spid=None):
        if os.name != 'posix' or not Path('/proc/self/ns/net').exists():
            raise ProtocolError('CD6_QA_REQUIERE_LINUX_AISLADO')
        if {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'}:
            raise ProtocolError('CD6_QA_INTERFAZ_EXTERNA_PRESENTE')
        net = os.readlink('/proc/self/ns/net')
        if net != os.readlink('/proc/1/ns/net'):
            raise ProtocolError('CD6_QA_REQUIERE_VM_DEDICADA')
        mountinfo = Path('/proc/self/mountinfo').read_text()
        for line in mountinfo.splitlines():
            kind = line.split(' - ', 1)[1].split()[0]
            if kind in {'nfs','nfs4','cifs','smb3','9p','virtiofs','ceph','glusterfs','autofs'} or kind.startswith('fuse'):
                raise ProtocolError('CD6_QA_VOLUMEN_COMPARTIDO_PRESENTE')
        if spid is not None:
            if not str(spid).isdigit():
                raise ProtocolError('CD6_QA_PROCESO_ORACLE_NO_LOCAL')
            root = Path('/proc') / str(spid)
            if (root.stat().st_uid != os.getuid() or os.readlink(root / 'ns/net') != net
                    or Path(os.readlink(root / 'exe')).name.lower() != 'oracle'):
                raise ProtocolError('CD6_QA_PROCESO_ORACLE_NO_LOCAL')

    def assert_isolated(self, cursor):
        self._require_local_isolation()
        cursor.execute(ISOLATED_IDENTITY_SQL)
        rows = cursor.fetchall()
        c = self.config
        expected = (c['dbid'],c['dbName'],c['dbUniqueName'],'PRIMARY','READ WRITE',
                    c['service'],c['serverHost'],c['instanceName'],c['user'],c['user'],'FALSE')
        if len(rows) != 1 or tuple(rows[0][:11]) != expected or rows[0][0] == c['productionDbid']:
            raise ProtocolError('CD6_QA_IDENTIDAD_NO_COINCIDE')
        self._require_local_isolation(rows[0][11])
        cursor.execute(ISOLATED_PARAMETERS_SQL)
        if dict(cursor.fetchall()) != {'job_queue_processes':'0','aq_tm_processes':'0'}:
            raise ProtocolError('CD6_QA_PROCESOS_ASINCRONOS_ACTIVOS')
        cursor.execute(ISOLATED_LINKS_SQL)
        if cursor.fetchone() != (0,):
            raise ProtocolError('CD6_QA_DATABASE_LINKS_PRESENTES')

    def open_connection(self):
        self._require_local_isolation()
        from incluirtec.services.oracle_driver import oracle_driver
        if oracle_driver.__name__ not in {'oracledb','cx_Oracle'}:
            raise ProtocolError('CREDIHOY_DRIVER_NO_COMPATIBLE')
        c = self.config
        password = self._private_bytes(c['passwordFile']).decode('utf-8').rstrip('\r\n')
        if not password:
            raise ProtocolError('CD6_QA_SECRETO_PRIVADO_REQUERIDO')
        dsn = oracle_driver.makedsn(c['host'],c['port'],service_name=c['service'])
        connection = oracle_driver.connect(user=c['user'],password=password,dsn=dsn)
        cursor = None
        try:
            connection.autocommit = False
            if connection.autocommit is not False:
                raise ProtocolError('CREDIHOY_AUTOCOMMIT_NO_CONTROLADO')
            setattr(connection, 'callTimeout' if oracle_driver.__name__ == 'cx_Oracle' else 'call_timeout',15000)
            cursor = connection.cursor()
            self.assert_isolated(cursor)
            cursor.close()
            connection.rollback()
            self.number_type,self.clob_type = oracle_driver.NUMBER,oracle_driver.CLOB
            return connection
        except BaseException:
            self._dispose(connection,cursor)
            raise

    @staticmethod
    def _dispose(connection,cursor):
        from .oracle_readonly import _cleanup_native
        _cleanup_native(connection,cursor)


class OracleEvidenceRepository(CredihoyReadRepository):
    def task_rows(self, flow_id):
        self.cursor.execute(TASKS_SQL, flow_id=int(numeric_id(flow_id)))
        keys = ("K_SERIE", "K_IDACTI", "K_IDRUTA", "I_ESTTAR", "I_EJECUC")
        return [dict(zip(keys, row)) for row in self.cursor.fetchall()]

    def errors(self, flow_id):
        self.cursor.execute(ERRORS_SQL, flow_id=int(numeric_id(flow_id)))
        row = self.cursor.fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0:
            raise ProtocolError("CREDIHOY_EVIDENCIA_INCOMPLETA")
        return row[0]

    def controls(self, reference, *, administrative=False):
        self.cursor.execute(ADMIN_CONTROLS_SQL if administrative else CONTROLS_SQL, application_type="SC",
                            application_number=reference.application_number)
        return self.cursor.fetchall()

    def flow_by_intent(self, intent_id):
        self.cursor.execute(FLOW_BY_INTENT_SQL, intent_name="CD4G-" + str(intent_id))
        rows = self.cursor.fetchall()
        if (len(rows) > 1 or any(row[1:] != (LINE.workflow_model, LINE.workflow_process) for row in rows)):
            raise ProtocolError("FLUJO_REMOTO_CONTRADICTORIO", 409)
        return numeric_id(rows[0][0]) if rows else None


class CredihoyOracleGateway:
    """Prepared protocol, but submit/lookup cannot open a real Oracle connection."""

    synthetic = False
    administrative_scope = ADMIN_SCOPE
    validate_request = staticmethod(validate_request)

    def __init__(self, *, workflow_user, database="default", read_port=None):
        from .contracts import identifier
        self.workflow_user = identifier(workflow_user)
        if self.workflow_user.upper() in ADMIN_USERS:
            raise ProtocolError("CREDIHOY_USUARIO_TECNICO_REQUERIDO")
        self.database = database
        if read_port is not None:
            from .oracle_readonly import RealOracleReadPort
            if not isinstance(read_port, RealOracleReadPort):
                raise ProtocolError(BLOCKED, 503)
        self.read_port = read_port

    def _port(self):
        # No env flag, injected real factory, driver import or DSN can lift this gate.
        if type(self) is not SimulatedCredihoyOracleGateway:
            raise ProtocolError(BLOCKED, 503)
        return self.simulated_port

    def _reader_port(self):
        if type(self) is SimulatedCredihoyOracleGateway:
            return self._port()
        if self.read_port is None:
            raise ProtocolError(BLOCKED, 503)
        return self.read_port

    @staticmethod
    def _validate_payload(payload, *, allow_without_promoter_offline=False):
        if canonical(normalize_request(payload,
                allow_without_promoter_offline=allow_without_promoter_offline)) != canonical(payload):
            raise ProtocolError("INTEGRIDAD_OPERACION_INVALIDA", 409)
        validate_request(payload, allow_without_promoter_offline=allow_without_promoter_offline)
        if "datosRadicacion" not in payload["contextoLinix"]:
            raise ProtocolError("CREDIHOY_DATOS_RADICACION_REQUERIDOS")

    def _operation(self, operation, *, sending=False):
        from .models import RadicationOperation
        local = connections[self.database]
        if local.in_atomic_block or not local.get_autocommit():
            raise ProtocolError("TRANSACCION_EXTERNA_NO_PERMITIDA", 409)
        row = RadicationOperation.objects.using(self.database).get(id=operation.id)
        if (row.claim_id is None or row.claim_id != operation.claim_id
                or str(row.request_id) != row.payload.get("solicitudId")
                or str(row.intent_id) != row.payload.get("intencionId")
                or str(row.request_id) != str(operation.request_id)
                or str(row.intent_id) != str(operation.intent_id)
                or row.attempted_at is None or row.fingerprint != operation.fingerprint
                or digest(canonical(row.payload)) != row.fingerprint
                or decision_fingerprint(row.payload) != row.decision_fingerprint
                or canonical(row.payload) != canonical(operation.payload)
                or (sending and row.state != State.EXECUTING)):
            raise ProtocolError("OPERACION_NO_RECLAMADA", 409)
        self._validate_payload(row.payload,
            allow_without_promoter_offline=self._without_promoter_offline())
        return row

    def _without_promoter_offline(self):
        return (type(self) is SimulatedCredihoyOracleGateway
                and self.allow_without_promoter_offline is True)

    def _connect(self, port):
        from .oracle_readonly import ReadOnlyOracleConnection
        connection = port.open_connection()
        if not isinstance(connection, (SimulatedOracleConnection, ReadOnlyOracleConnection)):
            raise ProtocolError(BLOCKED, 503)
        try:
            connection.autocommit = False
            if connection.autocommit is not False:
                raise ProtocolError("CREDIHOY_AUTOCOMMIT_NO_CONTROLADO")
            cursor = connection.cursor()
            cursor.execute(SESSION_SQL)
            users = cursor.fetchone()
            if (not users or len(users) != 2 or
                    any(not isinstance(user, str) or not user.strip()
                        or user.strip().upper() in ADMIN_USERS for user in users)):
                raise ProtocolError("CREDIHOY_USUARIO_TECNICO_REQUERIDO")
            return connection, cursor
        except Exception:
            self._dispose(connection, locals().get("cursor"), rollback=True)
            raise

    @staticmethod
    def _dispose(connection, cursor, *, rollback):
        # Cleanup failure never turns an uncertain remote result into a safe retry.
        if rollback:
            try:
                connection.rollback()
            except Exception:
                pass
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        try:
            connection.close()
        except Exception:
            pass

    def _observe(self, cursor, operation, flow_id):
        repository = OracleEvidenceRepository(cursor)
        tasks = repository.task_rows(flow_id)
        active = [task for task in tasks if task["I_ESTTAR"] == "A"]
        activity = int(active[0]["K_IDACTI"]) if len(active) == 1 else None
        pending = Observation("STARTED", str(operation.intent_id), operation.fingerprint,
                              flow_id, activity_current=activity,
                              pending_reason="CREDIHOY_SC_NO_CONFIRMADA")
        validator = CredihoyEvidenceValidator(repository)
        try:
            reference = validator._reference_by_flow(operation, flow_id)
        except ProtocolError as exc:
            if exc.code in {"CREDIHOY_XML_INCOMPLETO", "CREDIHOY_SIN_EVIDENCIA_RADICACION"}:
                return pending
            raise
        try:
            observation = validator._confirmed_observation(operation, flow_id, reference)
        except ProtocolError as exc:
            if exc.code == "CREDIHOY_SC_NO_EXISTE":
                return pending
            raise
        administrative = operation.payload["versionContrato"] in (CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION)
        controls = repository.controls(reference, administrative=administrative)
        promoter = operation.payload["contextoLinix"]["datosRadicacion"]["promotor"]
        if promoter is None:
            matches = (administrative and self._without_promoter_offline()
                       and len(controls) == 1 and controls[0][2] is None)
        else:
            matches = len(controls) == 1 and str(controls[0][2]) == promoter["kIdTerc"]
        if not matches:
            raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)
        error_count = repository.errors(flow_id)
        pending = replace(pending, candidate_application_number=reference.application_number,
                          workflow_error_count=error_count)
        # Evidence of this specific human boundary, not certification of financial reversal.
        cut = (len(tasks) == 3 and {int(task["K_IDACTI"]) for task in tasks} == {113, 3883, 6964}
               and all(int(task["K_IDRUTA"]) == 10002896 for task in tasks)
               and len(active) == 1 and activity == 6964 and active[0]["I_EJECUC"] == "H"
               and all(task["I_ESTTAR"] == "E" for task in tasks if int(task["K_IDACTI"]) != 6964)
               and controls[0][:2] == ("N", "N") and error_count == 0)
        if administrative:
            cut = (cut and len(controls[0]) == 4 and controls[0][3] is None
                   and [int(task["K_IDACTI"]) for task in tasks] == [113, 3883, 6964]
                   and all(task["I_EJECUC"] == "A" for task in tasks
                           if int(task["K_IDACTI"]) in {113, 3883}))
        cut = self._workflow_cut(operation,tasks,controls,error_count,cut)
        if not cut:
            reason = ("CREDIHOY_ACTIVIDAD_INESPERADA" if
                      any(int(task["K_IDACTI"]) not in {113, 3883, 6964} for task in tasks)
                      else "CREDIHOY_CORTE_NO_VERIFICADO")
            return replace(pending, pending_reason=reason)
        return replace(observation, activity_current=activity, cut_verified=True,
                       workflow_error_count=error_count)

    def _workflow_cut(self, operation,tasks,controls,error_count,legacy_cut):
        policy = getattr(operation, 'workflow_evidence', {}).get('politicaEjecucion')
        if policy is not None:
            from .service import validate_lab_execution_policy
            validate_lab_execution_policy(policy, operation.payload)
            if policy['tipo'] == 'INDETERMINADO':
                return False
            if policy['tipo'] == 'ESPECIAL':
                from .production_preflight import SPECIAL_PREFIX
                if legacy_cut:
                    return True  # Earlier human boundary, before either branch.
                active = [t for t in tasks if t['I_ESTTAR'] == 'A']
                # Observation of a synthetic selector is not exclusion of native
                # executors. Never accept ordinary/approval effects on this path.
                return (type(self) is SimulatedCredihoyOracleGateway and error_count == 0
                        and len(controls) == 1 and controls[0][:2] == ('N', 'N')
                        and len(active) == 1 and int(active[0]['K_IDACTI']) == 121
                        and int(active[0]['K_IDRUTA']) == 10002852 and active[0]['I_EJECUC'] == 'H'
                        and len({str(t['K_SERIE']) for t in tasks}) == len(tasks)
                        and {113, 3883}.issubset({int(t['K_IDACTI']) for t in tasks})
                        and all(t['I_ESTTAR'] == 'E' and int(t['K_IDACTI']) in
                                ({113, 3883, 6076, 6711} | SPECIAL_PREFIX)
                                for t in tasks if t is not active[0]))
        return legacy_cut

    def workflow_snapshot(self, operation):
        from copy import deepcopy
        port = self._port()
        row = self._operation(operation)
        policy = row.workflow_evidence.get('politicaEjecucion')
        from .service import validate_lab_execution_policy
        from django.utils import timezone
        validate_lab_execution_policy(policy, row.payload)
        connection, cursor = self._connect(port)
        try:
            repository = OracleEvidenceRepository(cursor)
            snapshot = repository.workflow_snapshot(row, timezone.now().isoformat(),
                                                    policy['configuracionSha256'])
            if repository.errors(row.flow_id) != 0:
                raise ProtocolError('CREDIHOY_CORTE_NO_VERIFICADO', 409)
            context = port.workflow_context(row, deepcopy(snapshot))
            allowed = {'revisionEfectos', 'handoffsHumanos', 'decisionHumana', 'garantiasCumplen',
                       'postfirmaNativa', 'documentoDefinitivoVigente',
                       'recogidaRequerida', 'recogidaPreparada', 'desembolsosPrevios'}
            if type(context) is not dict or not set(context) <= allowed:
                raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA', 409)
            return {**snapshot, **context, 'simulada': True}
        finally:
            self._dispose(connection, cursor, rollback=True)

    def execute_workflow_step(self, operation, plan):
        port = self._port()
        row = self._operation(operation)
        reservation = row.workflow_evidence.get('pasoWorkflow', {})
        if (reservation.get('estado') != 'RESERVADO' or reservation.get('plan') != plan):
            raise ProtocolError('OPERACION_NO_RECLAMADA', 409)
        # Re-read after the durable claim. The port never receives a browser plan.
        from .service import lab_workflow_plan, validate_lab_execution_policy
        validate_lab_execution_policy(row.workflow_evidence.get('politicaEjecucion'), row.payload, sending=True)
        actual = lab_workflow_plan(row, self.workflow_snapshot(row))
        if actual != plan or actual.get('permitido') is not True:
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA', 409)
        port.execute_workflow_step(row, plan)

    def _radication_xml(self, row):
        return build_radication_xml(row.payload,
            allow_without_promoter_offline=self._without_promoter_offline())

    def submit(self, operation, *, checkpoint_flow):
        port = self._port()
        row = self._operation(operation, sending=True)
        if row.flow_id is not None:
            return self.lookup(row)
        xml = self._radication_xml(row)
        connection, cursor = self._connect(port)
        committed = False
        try:
            if row.payload["versionContrato"] in (CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION):
                existing_flow = OracleEvidenceRepository(cursor).flow_by_intent(row.intent_id)
                if existing_flow is not None:
                    checkpoint_flow(existing_flow)
                    row = self._operation(row, sending=True)
                    return self._observe(cursor, row, existing_flow)
            flow_out = cursor.var(port.number_type)
            xml_bind = cursor.var(port.clob_type)
            xml_bind.setvalue(0, xml)
            cursor.execute(START_SQL,
                n_k_proces=LINE.workflow_process, n_k_modelo=LINE.workflow_model,
                n_flujo="CD4G-" + str(row.intent_id), n_k_usuari=self.workflow_user,
                n_trama=xml_bind, n_refere=None, n_modapl="19",
                n_k_sucurs=row.payload["contextoLinix"]["sucursal"],
                n_i_commit="FALSE", v_k_flujo=flow_out)
            raw_flow = flow_out.getvalue()
            # Drivers may return integral NUMBER values as float; never truncate a fraction.
            if isinstance(raw_flow, float) and raw_flow.is_integer():
                raw_flow = int(raw_flow)
            flow_id = numeric_id(raw_flow)
            checkpoint_flow(flow_id)
            row = self._operation(row, sending=True)
            if row.flow_id != flow_id:
                raise ProtocolError("CREDIHOY_CHECKPOINT_NO_CONFIRMADO")
            observation = self._observe(cursor, row, flow_id)
            if observation.outcome != "RADICATED":
                return observation
            connection.commit()
            committed = True
        finally:
            self._dispose(connection, cursor, rollback=not committed)
        # An OUT/own-session SC is not proof that another session sees a committed SC.
        return self.lookup(row)

    def lookup(self, operation, *, checkpoint_flow=None):
        port = self._reader_port()
        row = self._operation(operation)
        administrative = row.payload["versionContrato"] in (CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION)
        if row.flow_id is None and not administrative:
            return Observation("UNKNOWN", str(row.intent_id), row.fingerprint)
        connection, cursor = self._connect(port)
        try:
            if administrative:
                found = OracleEvidenceRepository(cursor).flow_by_intent(row.intent_id)
                if found is None:
                    if row.flow_id is not None:
                        raise ProtocolError("FLUJO_REMOTO_CONTRADICTORIO", 409)
                    return Observation("UNKNOWN", str(row.intent_id), row.fingerprint)
                if row.flow_id is not None and found != row.flow_id:
                    raise ProtocolError("FLUJO_REMOTO_CONTRADICTORIO", 409)
                if row.flow_id is None:
                    if checkpoint_flow is None:
                        return Observation("STARTED", str(row.intent_id), row.fingerprint, found)
                    checkpoint_flow(found)
                    row = self._operation(row)
                    if row.flow_id != found:
                        raise ProtocolError("CREDIHOY_CHECKPOINT_NO_CONFIRMADO")
            return self._observe(cursor, row, numeric_id(row.flow_id))
        finally:
            self._dispose(connection, cursor, rollback=True)


class SimulatedCredihoyOracleGateway(CredihoyOracleGateway):
    """Explicit laboratory gateway. Only the offline port type is accepted."""

    synthetic = True

    def recover_post_signature(self, operation):
        row = self._operation(operation)
        reservation = row.workflow_evidence.get('postfirma', {})
        if reservation.get('estado') not in {'RESERVADO', 'INCIERTO'}:
            raise ProtocolError('OPERACION_NO_RECLAMADA', 409)
        return self._port().recover_post_signature(row)

    def __init__(self, simulated_port, *, workflow_user, database="default",
                 allow_without_promoter_offline=False):
        if type(allow_without_promoter_offline) is not bool:
            raise ProtocolError(BLOCKED, 503)
        if not isinstance(simulated_port, SimulatedOraclePort):
            raise ProtocolError(BLOCKED, 503)
        self.simulated_port = simulated_port
        self.allow_without_promoter_offline = allow_without_promoter_offline
        super().__init__(workflow_user=workflow_user, database=database)

    def validate_request(self, payload):
        CredihoyOracleGateway._validate_payload(payload,
            allow_without_promoter_offline=self._without_promoter_offline())
