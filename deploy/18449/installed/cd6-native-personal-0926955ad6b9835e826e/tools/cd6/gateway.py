"""Existing CD6 gateway: sealed V2, isolated clone writes, production still closed."""
from decimal import Decimal
from datetime import datetime
import re
from xml.etree import ElementTree as ET

from incluirtec.digital_operations.contracts import (
    CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION,
    canonical, decision_fingerprint, digest, normalize_request,
)
from incluirtec.digital_operations.credihoy_oracle import (
    ADMIN_USERS, SESSION_SQL, START_SQL, CredihoyOracleGateway,
    SimulatedOraclePort, SimulatedOracleConnection,
    IsolatedOracleWritePort,
)
from incluirtec.digital_operations.credihoy_repository import (
    CredihoyReadRepository, IDENTITY_BY_DOCUMENT_SQL, PAYROLL_BY_HOLDER_SQL, DUPLICATE_APPLICATION_SQL,
)

from .condiciones import SID, FichaError, instante


PILOT_REQUEST = '3ad81272-3652-42a6-859a-789b397dcfe3'
PILOT_INTENT = '57a482a2-c873-4adf-abc4-ef0cb40597ac'
PILOT_HOLDER_SHA = '11a5ff96719365476aa425c7b3abed0825d0e197c7d0c7fcd036b31d232de492'
PILOT_REFERENCES_SHA = '7687cff3d2afa5e54b0edd2aa78080f29bc638ed0b0d02e0ced8a6dbd8084f87'
PILOT_ROUTE_SHA = 'c27ae8c5d2a106588d5597bcdd8a981a7da741ebd6cc8854346b76c27185176d'


class AuditedConnection:
    """Observe explicit client transaction calls; not internal Oracle commits."""
    def __init__(self, native, journal):
        self.native, self.journal = native, journal

    def __getattr__(self, name):
        return getattr(self.native, name)

    def _transaction(self, method):
        self.journal.record(method.upper() + '_INTENTADO', {'origen': 'CLIENTE_CD6'})
        try:
            getattr(self.native, method)()
        except BaseException:
            self.journal.record(method.upper() + '_INCIERTO', {'origen': 'CLIENTE_CD6'})
            raise
        self.journal.record(method.upper() + '_RETORNO', {'origen': 'CLIENTE_CD6',
            'commitsInternos': 'NO_OBSERVABLES_POR_ESTA_EVIDENCIA'})

    def commit(self):
        return self._transaction('commit')

    def rollback(self):
        return self._transaction('rollback')


class StartGuardCursor:
    def __init__(self, cursor, gateway):
        self.cursor = cursor
        self.gateway = gateway

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def execute(self, sql, *args, **kwargs):
        if sql == START_SQL:
            journal = self.gateway.journal
            op = self.gateway._operation(journal.operation(), sending=True)
            evidence = journal.packet()['evidencia']
            if self.gateway.journal.request_id != SID:
                if not kwargs or kwargs.get('n_i_commit') != 'FALSE':
                    raise FichaError('START_SIN_CONTROL_DE_COMMIT')
                self.gateway._verify_new_case(op)
                self.gateway._require_execution_boundary(self.cursor)
                actual = kwargs.get('n_trama')
                actual = actual.getvalue() if hasattr(actual, 'getvalue') else None
                actual = actual.read() if hasattr(actual, 'read') else actual
                if actual != evidence['preparacion']['xml']:
                    raise FichaError('XML_BIND_DIFIERE_DEL_SELLADO')
                if (kwargs.get('n_flujo') != 'CD4G-' + str(op.intent_id)
                        or kwargs.get('n_k_modelo') != 'CREDIHOY_VAL_EMPLEADO'
                        or kwargs.get('n_k_proces') != 'CREDITOS'
                        or kwargs.get('n_k_usuari') != self.gateway.workflow_user):
                    raise FichaError('START_IDENTIDAD_O_MODELO_NO_COINCIDE')
            else:
                # Historical dossier can be reconciled, never sent again.
                raise FichaError('PILOTO_PROTEGIDO_NO_MAS_START')
            journal.mark_start()  # Durable single-use claim, also before financial SELECTs.
            if self.gateway._pilot_case():
                journal.record('PILOTO_FINANCIERO_ANTES_START',
                               self.gateway._pilot_financial_snapshot(self.cursor))
                journal.record('PILOTO_RIESGO_RESIDUAL', {
                    '6964_6783': 'AUTOMATICAS_ACTUALES_HUMANAS_EN_ANTECEDENTES',
                    'trigger': 'UPDATE_OF_I_CANCEL_NO_INSERT',
                    'efectosInternos': 'NO_CERTIFICADOS_SIN_GARANTIA_DE_REVERSIBILIDAD',
                    'observacionFinanciera': 'DETECTA_CAMBIOS_NO_CONTIENE_COMMITS_INTERNOS',
                    'limiteTransporte': self.gateway.port.timeout_evidence,
                })
        return self.cursor.execute(sql, *args, **kwargs)


class GatewayAdministrativoUnico(CredihoyOracleGateway):
    def __init__(self, journal, port, *, workflow_user, clock=instante):
        super().__init__(workflow_user=workflow_user)
        self.journal = journal
        self.port = port
        self.clock = clock

    def _technical(self):
        return self.journal.operation().payload.get('versionContrato') == CREDIHOY_TECHNICAL_VERSION

    def _pilot_case(self):
        row = self.journal.operation()
        return (self._technical() and self.journal.request_id == PILOT_REQUEST
                and str(row.intent_id) == PILOT_INTENT)

    def _pilot_port(self):
        from .worker import Port
        return type(self.port) is Port and self.port.pilot_case is True and self._pilot_case()

    def _pilot_writer(self):
        return self._pilot_port() and self.port.read_only is False

    def _personal_port(self):
        from .worker import Port
        return (type(self.port) is Port and self.port.personal_case is True
            and self.journal.packet()['evidencia'].get('canal')=='CONFIRMACION_PERSONAL_CD6')

    def _require_pilot_case(self, *, sending=False):
        row = self.journal.operation()
        payload = row.payload
        expected_credit = {'montoSolicitado': '400000.00', 'plazoMeses': 2,
            'periodicidad': 'MENSUAL', 'moneda': 'COP', 'tasaNominalAnual': '16.67',
            'cuotaContractual': '204448.00'}
        product = payload['producto']
        prepared = self.journal.packet()['evidencia']['preparacion']
        if (not self._pilot_case() or self.workflow_user != 'CGTE'
                or digest(canonical(payload['titular'])) != PILOT_HOLDER_SHA
                or payload['credito'] != expected_credit
                or (product['codigo'], product['modalidad'], product['destino']) != ('390', '111', 'CFA')
                or prepared['referenciasSha256'] != PILOT_REFERENCES_SHA):
            raise FichaError('PILOTO_PRODUCTIVO_FUERA_DEL_CASO_UNICO')
        if (canonical(prepared['payload']) != canonical(payload)
                or digest(prepared['xml']) != prepared['xmlSha256']
                or re.search(r'<!\s*(DOCTYPE|ENTITY)\b', prepared['xml'], re.I)):
            raise FichaError('PILOTO_XML_SELLADO_NO_CORRESPONDE')
        if sending:
            self._verify_new_case(row)
        root = ET.fromstring(prepared['xml'])
        values = root.findall('./Datos/A_VALOR_RECOGIDO')
        if len(values) != 1 or Decimal(values[0].text or 'NaN') != 0:
            raise FichaError('PILOTO_NO_ADMITE_RECOGIDA')
        for node in root.iter():
            name = node.tag.rsplit('}', 1)[-1]
            if (name in {'R_RegistroObligacionRecogida', 'A_NUMERO_OBLIGACION_CARTERA'}
                    or (name == 'R_ObligacionRecogida' and (len(node) or node.attrib))):
                raise FichaError('PILOTO_NO_ADMITE_RECOGIDA')

    def _pilot_financial_snapshot(self, cursor):
        from incluirtec.digital_operations.credihoy_repository import PILOT_FINANCIAL_SQL
        row = self.journal.operation()
        if not self._pilot_case():
            raise FichaError('OBSERVACION_FINANCIERA_FUERA_DEL_PILOTO')
        result = {}
        for label, sql in PILOT_FINANCIAL_SQL.items():
            cursor.execute(sql, {'tercero': int(row.payload['contextoLinix']['kIdTerc'])})
            columns = [column[0] for column in cursor.description]
            values = cursor.fetchmany(2001)
            if len(values) > 2000:
                raise FichaError('OBSERVACION_FINANCIERA_EXCEDE_ALCANCE')
            result[label] = [dict(zip(columns, items)) for items in values]
        import json
        return json.loads(json.dumps(result, default=str))

    def _observe_pilot_financials(self, cursor, observation):
        before = self.journal.last_evidence('PILOTO_FINANCIERO_ANTES_START')
        if before is None:
            raise FichaError('PILOTO_SIN_BASELINE_FINANCIERO')
        after = self._pilot_financial_snapshot(cursor)
        previous = {(r['K_TIPODR'], str(r['K_NUMDOC'])): r for r in before['CA090']}
        current = {(r['K_TIPODR'], str(r['K_NUMDOC'])): r for r in after['CA090']}
        changed = any(current.get(key) != value for key, value in previous.items())
        observed_sc = observation.application_number or observation.candidate_application_number
        new_keys = set(current) - set(previous)
        expected_new = ('SC', str(observed_sc)) if observed_sc else None
        changed = changed or bool(new_keys - ({expected_new} if expected_new else set()))
        if expected_new in current:
            new_row = current[expected_new]
            changed = changed or any(new_row[key] != 'N' for key in ('I_CANCEL', 'I_APROBA', 'I_LIQUID'))
            changed = changed or new_row['N_PAGARE'] is not None or Decimal(str(new_row['V_SALDO'])) != 0
        changed = changed or before['CO917'] != after['CO917']
        old_notes = {(r['K_TIPODR'], r['K_SUCURS'], str(r['K_PAGARE'])): r for r in before['Pagares']}
        new_notes = {(r['K_TIPODR'], r['K_SUCURS'], str(r['K_PAGARE'])): r for r in after['Pagares']}
        changed = changed or any(new_notes.get(key) != value for key, value in old_notes.items())
        self.journal.record('PILOTO_FINANCIERO_DESPUES_START', {
            'snapshot': after, 'cambiosPreexistentes': changed,
            'atribucion': 'DIFERENCIA_OBSERVADA_NO_PRUEBA_CAUSALIDAD',
            'alcance': 'TITULAR_DEL_CASO_NO_GARANTIA_SOBRE_OTROS_TITULARES',
        })
        if changed:
            raise FichaError('PILOTO_CAMBIO_FINANCIERO_PREEXISTENTE_SOLO_CONCILIAR')

    def _require_execution_boundary(self, cursor):
        if self._personal_port():
            from .grant import validar_operacion_personal
            from incluirtec.digital_operations.production_preflight import snapshot,require_personal_start_boundary
            validar_operacion_personal(self.journal,self.workflow_user,self.clock(),sending=True)
            observed=snapshot(cursor,follow_paths=True)
            require_personal_start_boundary(observed)
            if canonical(observed)!=canonical(self.journal.packet()['evidencia']['preflight']):
                raise FichaError('CD6_RUTA_CAMBIO_DESDE_PREPARACION')
            return
        if self.journal.packet()['evidencia'].get('politicaEjecucion') is not None:
            # Neither H in the catalog nor our worker exclusion proves containment
            # of native engine jobs, package chaining, or parallel routes.
            raise FichaError('DOS_RECORRIDOS_START_CERRADO_PARADA_NATIVA_PENDIENTE')
        from incluirtec.digital_operations.production_preflight import snapshot, require_boundary, require_configuration
        if self._pilot_writer():
            self._require_pilot_case(sending=True)
            observed = snapshot(cursor, follow_paths=True)
            recorded = self.journal.packet()['evidencia']['preflight']
            require_configuration(observed)
            if (observed['sha256'] != PILOT_ROUTE_SHA
                    or canonical(recorded) != canonical(observed)
                    or observed['configuracion'].get('rutasDerivadasIncompletas', True)):
                raise FichaError('PILOTO_RUTA_VIGENTE_NO_COINCIDE')
            return
        if isinstance(self.port,IsolatedOracleWritePort):
            if not self._technical():
                raise FichaError('CD6_QA_REQUIERE_CONTRATO_V2')
            self.port.assert_isolated(cursor)
            observed = snapshot(cursor,follow_paths=True)
            expected = self.journal.packet()['evidencia']
            if (expected.get('destinoOracle') != self.port.identity()
                    or canonical(expected['preflight']) != canonical(observed)):
                raise FichaError('CD6_QA_DESTINO_O_RUTA_CAMBIO')
            require_configuration(observed,expected_identity=self.port.preflight_identity())
            return
        observed = snapshot(cursor)
        if self._technical():
            require_configuration(observed)
            self._require_simulated_v2_port()
            # Retain the old laboratory cut only for the existing simulated scenario.
            require_configuration(observed, legacy_cut=True)
        else:
            require_boundary(observed, self.journal.packet()['evidencia']['certificaciones'], now=self.clock())

    def _require_simulated_v2_port(self):
        # No production containment is implemented. The existing offline port is not a real driver.
        if not isinstance(self.port, SimulatedOraclePort):
            raise FichaError('V2_EFECTOS_FINANCIEROS_INTERNOS_NO_CONTENIDOS')

    def reconcile_native_signing(self):
        """Read SERVIC's persisted result in this journal's owned flow; no execution."""
        from incluirtec.digital_operations.credihoy_repository import CredihoyReadRepository
        from incluirtec.digital_operations.credihoy import CredihoyEvidenceValidator
        op=self.journal.operation()
        if not op.flow_id or op.state not in {'RADICADA','INCIERTA'}:
            raise FichaError('FIRMA_NATIVA_REQUIERE_FLUJO_EXISTENTE')
        conn=self.port.open_connection();cur=None
        try:
            from incluirtec.digital_operations.oracle_readonly import ReadOnlyOracleConnection
            cur=conn.cursor()
            if not isinstance(conn,ReadOnlyOracleConnection):cur.execute('SET TRANSACTION READ ONLY')
            repository=CredihoyReadRepository(cur)
            reference=CredihoyEvidenceValidator(repository)._reference_by_flow(op,op.flow_id)
            result=repository.native_signing_state(op,reference,self.clock().isoformat())
            prior=self.journal.last_evidence('RADICACION_PERSISTIDA_CONCILIADA')
            if (not prior or prior.get('SC')!=result['SC'] or str(prior.get('K_FLUJO'))!=str(op.flow_id)
                    or prior.get('intencionId')!=str(op.intent_id)):
                raise FichaError('FIRMA_NATIVA_NO_CORRESPONDE_A_INTENCION_CONCILIADA')
            self.journal.record('LINPAG_ESTADO_NATIVO_CONCILIADO',result)
            return result
        finally:
            if cur is not None:cur.close()
            try:conn.rollback()
            finally:conn.close()

    def reconcile_financial_sources(self):
        """Private, SELECT-only financial observation of this journal's SC."""
        from incluirtec.digital_operations.credihoy_repository import CredihoyReadRepository
        from incluirtec.digital_operations.credihoy import CredihoyEvidenceValidator
        from incluirtec.digital_operations.oracle_readonly import ReadOnlyOracleConnection
        op=self.journal.operation()
        if not op.flow_id or op.state not in {'RADICADA','INCIERTA'}:
            raise FichaError('LECTURA_FINANCIERA_REQUIERE_FLUJO_EXISTENTE')
        conn=self.port.open_connection();cur=None
        try:
            cur=conn.cursor()
            if not isinstance(conn,ReadOnlyOracleConnection):cur.execute('SET TRANSACTION READ ONLY')
            repository=CredihoyReadRepository(cur)
            reference=CredihoyEvidenceValidator(repository)._reference_by_flow(op,op.flow_id)
            prior=self.journal.last_evidence('RADICACION_PERSISTIDA_CONCILIADA')
            if (not prior or prior.get('SC')!=reference.application_number
                    or str(prior.get('K_FLUJO'))!=str(op.flow_id) or prior.get('intencionId')!=str(op.intent_id)):
                raise FichaError('LECTURA_FINANCIERA_NO_CORRESPONDE_A_INTENCION')
            result=repository.financial_sources(op,reference,self.clock().isoformat())
            self.journal.record('LINIX_FUENTES_FINANCIERAS_SOLO_SELECT',result)
            try:
                from native_financial.unico.desembolso import conciliar_abono_ah135
            except ModuleNotFoundError as exc:
                if not exc.name.startswith('native_financial'):raise
                from apps.xcore_consumo.services.credihoy_v2.unico.desembolso import conciliar_abono_ah135
            try:
                closed=conciliar_abono_ah135(result,ahora=self.clock())
                self.journal.record('LINIX_CIERRE_FINANCIERO_CONCILIADO',closed)
            except ValueError:
                self.journal.record('LINIX_CIERRE_FINANCIERO_PENDIENTE',{'estado':'EVIDENCIA_INSUFICIENTE_SIN_REINTENTO',
                    'autorizaDesembolso':False})
            return result
        finally:
            if cur is not None:cur.close()
            try:conn.rollback()
            finally:conn.close()

    def continue_workflow_task(self):
        """One locally verifiable step. Institutional continuation remains closed.

        Reuse the journal and LINIX payload builder. Never trust a posted response,
        task number or human decision: read them through the owned port instead.
        """
        from hashlib import sha256
        from incluirtec.digital_operations.production_preflight import (
            workflow_step_plan, FINANCIAL_OR_SIGNATURE, SPECIAL_APPROVALS, ORDINARY_STEPS)
        from incluirtec.services.linix_web_client import LinixWebSessionClient
        op = self.journal.operation()
        policy = self.journal.packet()['evidencia'].get('politicaEjecucion')
        if (op.state != 'RADICADA' or not op.flow_id or self.journal.request_id in {SID,PILOT_REQUEST}
                or str(op.flow_id) in {'16097741','16087353'}
                or type(policy) is not dict or self.workflow_user != 'CGTE'):
            raise FichaError('CONTINUACION_SIN_POLITICA_CONCILIADA')
        # Only the existing simulated port has a continuation capability now.
        # Do not wrap a real writer/reader or open a connection to discover effects.
        if not isinstance(self.port, SimulatedOraclePort):
            raise FichaError('CONTINUACION_INSTITUCIONAL_CERRADA_PARADA_Y_EFECTOS_PENDIENTES')
        before = self.port.read_workflow_snapshot(str(op.flow_id))
        plan = workflow_step_plan(policy,before,request_id=self.journal.request_id,
            intent_id=op.intent_id, holder_reference=sha256(op.payload['titular']['numeroDocumento'].encode()).hexdigest(),
            now=self.clock())
        if not plan['permitido']:
            self.journal.record('WORKFLOW_PARADA_POLITICA',plan)
            return plan
        if plan['K_FLUJO'] != str(op.flow_id):raise FichaError('LECTURA_DE_OTRO_FLUJO')
        prior = self.journal.last_evidence('RADICACION_PERSISTIDA_CONCILIADA')
        if (not prior or prior.get('estado') != 'CONFIRMADA_EN_CA090'
                or prior.get('K_FLUJO') != str(op.flow_id) or str(prior.get('SC')) != plan['SC']
                or prior.get('intencionId') != str(op.intent_id) or prior.get('fingerprint') != op.fingerprint):
            raise FichaError('SC_NO_CONCILIADA_CON_INTENCION')
        payload = LinixWebSessionClient.build_execute_task_payload(plan['K_FLUJO'],plan['K_SERIE'],
            response_value=plan['respuesta'])
        claim = self.journal.claim_workflow_task(plan,policy['sha256'])
        error_type = None
        try:
            self.port.execute_workflow_task(payload)
        except Exception as exc:
            error_type = type(exc).__name__
        # ALWAYS reconcile; a return, rollback or exception is not the core result.
        result = {'estado':'INCIERTA', 'plan':plan, 'errorTipo':error_type, 'reintentoPermitido':False}
        try:
            after = self.port.read_workflow_snapshot(str(op.flow_id))
            when = datetime.fromisoformat(after['observadoEn'])
            rows = [t for t in after['tareas'] if str(t['K_SERIE']) == plan['K_SERIE']]
            old_done = {str(t['K_SERIE']) for t in before['tareas'] if t['I_ESTTAR'] == 'E'}
            unexpected = [t for t in after['tareas'] if t['I_ESTTAR'] == 'E'
                and str(t['K_SERIE']) not in old_done
                and ((t['K_IDACTI'] in FINANCIAL_OR_SIGNATURE
                    and t['K_IDACTI'] not in plan.get('actividadesNativasPermitidas',[])) or
                    (policy['tipo']=='ESPECIAL' and
                     (t['K_IDACTI'] in SPECIAL_APPROVALS or t['K_IDACTI'] in ORDINARY_STEPS)))]
            if unexpected:
                result['codigo']='ENCADENAMIENTO_NO_AUTORIZADO_SOLO_CONCILIAR'
                result['tareasNoAutorizadas']=[{'actividad':t['K_IDACTI'],
                    'K_SERIE':str(t['K_SERIE'])} for t in unexpected]
            if (after['fuente'] == 'LINIX' and after['solicitudId'] == self.journal.request_id
                    and after['intencionId'] == str(op.intent_id) and str(after['K_FLUJO']) == plan['K_FLUJO']
                    and str(after['SC']) == plan['SC'] and after['operacionIncierta'] is False
                    and after['configuracionSha256'] == policy['configuracionSha256']
                    and when.utcoffset() is not None and 0 <= (self.clock()-when).total_seconds() <= 300
                    and after.get('efectosFinancierosNoAutorizados') is False
                    and not unexpected and after.get('historialCompleto') is True
                    and len(rows) == 1 and rows[0]['I_ESTTAR'] == 'E'
                    and rows[0]['K_IDACTI'] == plan['actividad'] and rows[0]['K_IDRUTA'] == plan['ruta']
                    and rows[0].get('K_USUARI') == 'CGTE' and rows[0].get('respuesta') == plan['respuesta']):
                financial_ok=True
                if plan.get('requiereConciliacionFinanciera'):
                    native=after.get('postfirmaNativa',{})
                    financial_ok=(native.get('fuente')=='LINIX' and native.get('SC')==plan['SC']
                        and native.get('K_FLUJO')==plan['K_FLUJO']
                        and native.get('solicitudId')==self.journal.request_id
                        and native.get('intencionId')==str(op.intent_id)
                        and native.get('sha256')==digest(canonical({k:v for k,v in native.items() if k!='sha256'}))
                        and 0<=(self.clock()-datetime.fromisoformat(native['observadoEn'])).total_seconds()<=300
                        and native.get('firma',{}).get('acreditadaEnLINIX') is True)
                    if plan['actividad']==6096:
                        financial_ok=financial_ok and native.get('liquidacion',{}).get('liquidada') is True
                    else:
                        payment=native.get('desembolso',{})
                        reconciliation=after.get('conciliacionDesembolso',{})
                        financial_ok=(financial_ok and native.get('liquidacion',{}).get('liquidada') is True
                            and reconciliation.get('versionContrato')=='credihoy-conciliacion-desembolso-v1'
                            and reconciliation.get('fuente')=='LINIX'
                            and reconciliation.get('solicitudId')==self.journal.request_id
                            and reconciliation.get('SC')==plan['SC'] and reconciliation.get('K_FLUJO')==plan['K_FLUJO']
                            and reconciliation.get('titularReferencia')==native.get('titularReferencia')
                            and reconciliation.get('pagare')==native.get('pagare')
                            and reconciliation.get('sha256')==digest(canonical({k:v for k,v in reconciliation.items() if k!='sha256'}))
                            and 0<=(self.clock()-datetime.fromisoformat(reconciliation['observadoEn'])).total_seconds()<=300
                            and reconciliation.get('liquidacion',{}).get('liquidada') is True
                            and bool(reconciliation.get('consultaReferencia'))
                            and bool(re.fullmatch('[a-f0-9]{64}',str(reconciliation.get('composicionSha256',''))))
                            and type(reconciliation.get('valorNeto')) is str
                            and Decimal(reconciliation['valorNeto']).is_finite() and Decimal(reconciliation['valorNeto'])>0
                            and reconciliation.get('desembolso')==payment
                            and reconciliation.get('recogidaConciliada') is True
                            and payment.get('acreditado') is True
                            and bool(payment.get('referencia')) and payment.get('cantidad')==1
                            and type(payment.get('cantidad')) is int
                            and after.get('recogidaEjecutadaYConciliada') is True)
                if financial_ok:result.update(estado='CONFIRMADA_CONCILIADA',
                    tareasActivas=[{'actividad':t['K_IDACTI'],'K_SERIE':str(t['K_SERIE'])}
                        for t in after['tareas'] if t['I_ESTTAR'] == 'A'])
        except Exception as exc:
            result['conciliacionErrorTipo'] = type(exc).__name__
        self.journal.record('WORKFLOW_RESULTADO:'+claim,result)
        return result

    def _port(self):
        op = self.journal.operation()
        if op.payload['solicitudId'] != self.journal.request_id or op.state not in ('EXECUTING', 'INCIERTA', 'RADICADA'):
            raise FichaError('TRANSPORTE_ADMINISTRATIVO_NO_RECLAMADO')
        return self.port

    def _verify_new_case(self, row):
        from incluirtec.digital_operations.production_preparation import verify
        prepared = self.journal.packet()['evidencia']['preparacion']
        if canonical(prepared['payload']) != canonical(row.payload):
            raise FichaError('PREPARACION_NO_CORRESPONDE_AL_JOURNAL')
        from zoneinfo import ZoneInfo
        today = self.clock().astimezone(ZoneInfo('America/Bogota')).date()
        from datetime import datetime
        requested = datetime.strptime(row.payload['contextoLinix']['fechaSolicitud'],'%m/%d/%Y').date()
        if (requested > today or (not isinstance(self.port,IsolatedOracleWritePort) and requested != today)):
            raise FichaError('FECHA_CAMBIARON_ANTES_DE_START')
        return verify(prepared)

    def _radication_xml(self, row):
        if self.journal.request_id == SID:
            raise FichaError('PILOTO_PROTEGIDO_NO_MAS_START')
        return self._verify_new_case(row)

    _reader_port = _port

    def _without_promoter_offline(self):
        # Preserve the sealed omission, never substitute a titular ID for a promoter.
        return True

    def _operation(self, operation, *, sending=False):
        row = self.journal.operation()
        if row.payload.get('versionContrato') not in (CREDIHOY_ADMIN_VERSION, CREDIHOY_TECHNICAL_VERSION):
            raise FichaError('CANAL_ADMINISTRATIVO_NO_ADMITE_RADICACION_ORDINARIA')
        normalized = normalize_request(row.payload, allow_without_promoter_offline=True)
        if (row.claim_id is None or row.claim_id != operation.claim_id or row.attempted_at is None
                or row.fingerprint != operation.fingerprint or row.id != operation.id
                or digest(canonical(normalized)) != row.fingerprint
                or decision_fingerprint(normalized) != row.decision_fingerprint
                or canonical(normalized) != canonical(operation.payload)
                or (sending and row.state != 'EXECUTING')):
            raise FichaError('OPERACION_NO_RECLAMADA_O_MODIFICADA')
        self._validate_payload(normalized, allow_without_promoter_offline=True)
        if self._technical():
            evidence = self.journal.packet()['evidencia']
            if evidence.get('usuarioWorkflow') != self.workflow_user:
                raise FichaError('USUARIO_WORKFLOW_NO_CORRESPONDE_AL_JOURNAL')
            if evidence.get('canal')=='CONFIRMACION_PERSONAL_CD6':
                from .grant import validar_operacion_personal
                validar_operacion_personal(self.journal,self.workflow_user,self.clock(),sending=sending)
            if sending:
                self._verify_new_case(row)
            return row
        from .grant import validar
        validar(self.journal.packet(), normalized, self.workflow_user, self.clock(), sending=sending)
        if self.journal.request_id != SID:
            evidence = self.journal.packet()['evidencia']
            if evidence['grantAdministrativo'].get('xmlSha256') != evidence['preparacion']['xmlSha256']:
                raise FichaError('AUTORIZACION_NO_CUBRE_XML_SELLADO')
        return row

    def _connect(self, port):
        from incluirtec.digital_operations.oracle_readonly import RealOracleReadPort, ReadOnlyOracleConnection
        if self._technical():
            if (port is not self.port or (not self._pilot_port() and not self._personal_port()
                    and not isinstance(port, (SimulatedOraclePort, RealOracleReadPort,IsolatedOracleWritePort)))):
                raise FichaError('V2_PUERTO_SIN_CAPACIDAD_SEGURA')
            if self._pilot_port():
                self._require_pilot_case()
        conn = port.open_connection()
        try:
            if self._technical():
                expected_type = SimulatedOracleConnection if isinstance(port, SimulatedOraclePort) else ReadOnlyOracleConnection
                if not self._pilot_writer() and not (self._personal_port() and not port.read_only) and not isinstance(port,IsolatedOracleWritePort) and not isinstance(conn, expected_type):
                    raise FichaError('V2_PUERTO_SIN_CAPACIDAD_SEGURA')
            conn.autocommit = False
            value = conn.autocommit
            disabled = (type(value) is bool and value is False) or (type(value) is int and value == 0)
            self.autocommit_evidence = {'tipo': type(value).__name__,
                'valor': value if type(value) in (bool, int) else None, 'desactivadoConfirmado': disabled}
            if not disabled:
                raise FichaError('AUTOCOMMIT_NO_CONTROLADO')
            cur = conn.cursor()
            cur.execute(SESSION_SQL)
            users = cur.fetchone()
            if (not users or len(users) != 2 or any(not isinstance(u, str) or not u.strip()
                    or u.strip().upper() in ADMIN_USERS for u in users)):
                raise FichaError('USUARIO_ORACLE_NO_PERMITIDO')
            audited = conn if self.journal.request_id == SID else AuditedConnection(conn, self.journal)
            return audited, StartGuardCursor(cur, self)
        except Exception:
            self._dispose(conn, locals().get('cur'), rollback=True)
            raise

    def _workflow_cut(self,operation,tasks,controls,error_count,legacy_cut):
        if not isinstance(self.port,IsolatedOracleWritePort) and not self._pilot_case() and not self._personal_port():
            return legacy_cut
        from incluirtec.digital_operations.production_preflight import matches_human_boundary
        if self._pilot_case() and self.journal.packet()['evidencia']['preflight']['sha256'] != PILOT_ROUTE_SHA:
            return False
        allowed_human=None
        if self._personal_port():
            from incluirtec.digital_operations.production_preflight import require_personal_start_boundary
            boundary=require_personal_start_boundary(self.journal.packet()['evidencia']['preflight'])
            allowed_human={v['actividad'] for v in boundary['paradasHumanas']}
        return (error_count == 0 and len(controls) == 1 and len(controls[0]) == 4
                and controls[0][:2] == ('N','N') and controls[0][3] is None
                and matches_human_boundary(self.journal.packet()['evidencia']['preflight'],tasks,allowed_human=allowed_human))

    def preflight_duplicate(self):
        payload = self.journal.operation().payload
        conn, cur = self._connect(self.port)
        try:
            cur.execute(IDENTITY_BY_DOCUMENT_SQL,
                        {'documento': payload['titular']['numeroDocumento']})
            rows = cur.fetchall()
            if len(rows) != 1 or str(rows[0][0]) != payload['contextoLinix']['kIdTerc'] or rows[0][1] != 'Y':
                raise FichaError('IDENTIDAD_INSTITUCIONAL_NO_COINCIDE')
            cur.execute(PAYROLL_BY_HOLDER_SQL,
                {'tercero': int(payload['contextoLinix']['kIdTerc'])})
            profile = cur.fetchall()
            if self.journal.request_id == SID:
                expected_profile = ('101', '100', 'N', 'Y')
            else:
                evidence = self.journal.packet()['evidencia']
                self._verify_new_case(self.journal.operation())
                self._require_execution_boundary(cur)
                p = evidence['perfilCore']
                expected_profile = (p['sucursal'], p['nomina'], p['tipoNomina'], 'Y')
                if p['sucursal'] != payload['contextoLinix']['sucursal']:
                    raise FichaError('SUCURSAL_DEL_EXPEDIENTE_NO_COINCIDE')
            if len(profile) != 1 or tuple(str(v) for v in profile[0]) != expected_profile:
                raise FichaError('AGENCIA_O_CONVENIO_NOMINA_ACTUAL_NO_COINCIDE')
            cur.execute(DUPLICATE_APPLICATION_SQL,
                {'tercero': int(payload['contextoLinix']['kIdTerc']),
                 'monto': Decimal(payload['credito']['montoSolicitado']),
                 'desde': '10/13/2026' if self.journal.request_id == SID else payload['contextoLinix']['fechaSolicitud']})
            if cur.fetchone() is not None:
                raise FichaError('SC_EQUIVALENTE_POSIBLE_CONCILIAR_ANTES_DE_START')
        finally:
            self._dispose(conn, cur, rollback=True)

    def _observe(self, cursor, operation, flow_id):
        observation = super()._observe(cursor, operation, flow_id)
        if self._pilot_case():
            self._observe_pilot_financials(cursor, observation)
        if observation.outcome == 'RADICATED':
            records = CredihoyReadRepository(cursor).radication_records(flow_id)
            expected = {'V_TASA_NOMINAL': Decimal(operation.payload['credito']['tasaNominalAnual']),
                        'V_VALOR_CUOTA': Decimal(operation.payload['credito']['cuotaContractual'])}
            entry = operation.payload['contextoLinix'].get('entrada113')
            if entry is not None:
                expected['V_VALOR_CUOTA_CAL'] = expected['V_VALOR_CUOTA']
                expected['V_VALOR_CUOTA'] = Decimal(entry['valorCuota'])
            for record in records:
                if re.search(r'<!\s*(DOCTYPE|ENTITY)\b', record.xml_new, re.I):
                    raise FichaError('XML_RESPUESTA_INVALIDO')
                tree = ET.fromstring(record.xml_new)
                for name, value in expected.items():
                    try:
                        values = {node.text.strip() for node in tree.iter()
                                  if node.tag.rsplit('}', 1)[-1] == name and node.text and node.text.strip()}
                        values.update(node.attrib[name] for node in tree.iter() if name in node.attrib)
                        if len(values) != 1:
                            raise ValueError
                        actual = Decimal(next(iter(values)))
                    except (KeyError, TypeError, ValueError, ArithmeticError):
                        raise FichaError('TASA_CUOTA_NO_ACREDITADAS_EN_RESPUESTA_PERSISTIDA') from None
                    if actual != value:
                        raise FichaError('TASA_CUOTA_PERSISTIDAS_DIFIEREN')
            if self._technical():
                from incluirtec.digital_operations.credihoy import xml_fields, reference_from_fields, validate_profile_fields
                from incluirtec.digital_operations.cd16_xml import _signature
                references = CredihoyReadRepository(cursor).reference_records(flow_id)
                if len(references) != 1 or references[0]['estado'] != 'E':
                    raise FichaError('ENTRADA_3883_NO_ACREDITADA')
                text = references[0]['entrada']
                fields = xml_fields(text)
                validate_profile_fields(fields, operation.payload)
                expected_sc = (observation.application_type, observation.application_number)
                received_sc = reference_from_fields(fields)
                if (received_sc.application_type, received_sc.application_number) != expected_sc:
                    raise FichaError('SC_3883_DIFIERE_DE_113')
                root = ET.fromstring(text)
                original = ET.fromstring(self.journal.packet()['evidencia']['preparacion']['xml'])
                actual_refs = root.findall('./Datos/R_REFERSOLCRED')
                expected_refs = original.findall('./Datos/R_REFERSOLCRED')
                if (len(actual_refs) != 1 or len(expected_refs) != 1
                        or _signature(actual_refs[0]) != _signature(expected_refs[0])):
                    raise FichaError('REFERENCIAS_3883_DIFIEREN_DEL_XML_SELLADO')
        return observation
