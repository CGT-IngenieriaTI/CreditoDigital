"""SELECT-only repository with caller-owned cursor; never opens a connection."""

from .credihoy import FlowRadicationRecord, ScReference, clob_text, numeric_id
from .contracts import ProtocolError, digest


RADICATION_BY_FLOW_SQL = """
SELECT f.K_FLUJO, f.K_MODELO, t.K_SERIE, t.I_ESTTAR,
       XMLSERIALIZE(CONTENT r.N_XML_ENT AS CLOB),
       XMLSERIALIZE(CONTENT r.N_XML_ENT_NEW AS CLOB),
       XMLSERIALIZE(CONTENT r.N_XML_SAL AS CLOB)
  FROM LINIX.WF1710MFLUJO f
  LEFT JOIN LINIX.WF1711D0000 t
    ON t.K_FLUJO = f.K_FLUJO
   AND t.K_IDACTI = 113
  LEFT JOIN LINIX.CE8002TREPOSIT r
    ON r.K_REPOSI = t.K_REPOSI
 WHERE f.K_FLUJO = :flow_id
 ORDER BY t.K_SERIE
"""

APPLICATION_BY_REFERENCE_SQL = """
SELECT s.K_TIPODR, s.K_NUMDOC, s.K_IDTERC, s.K_MODALI,
       s.K_DESTIN, s.V_MONTO, s.A_OBLIGA, t.A_NUMNIT
  FROM LINIX.CA090MGSOLCRED s
  JOIN LINIX.GR001MTERCERO t ON t.K_IDTERC = s.K_IDTERC
 WHERE s.K_TIPODR = :application_type
   AND s.K_NUMDOC = :application_number
"""

REFERENCE_TASK_SQL = '''SELECT t.K_SERIE,t.I_ESTTAR,
    XMLSERIALIZE(CONTENT r.N_XML_ENT AS CLOB),
    XMLSERIALIZE(CONTENT r.N_XML_ENT_NEW AS CLOB),
    XMLSERIALIZE(CONTENT r.N_XML_SAL AS CLOB)
    FROM LINIX.WF1711D0000 t LEFT JOIN LINIX.CE8002TREPOSIT r ON r.K_REPOSI=t.K_REPOSI
    WHERE t.K_FLUJO=:flow_id AND t.K_IDACTI=3883 ORDER BY t.K_SERIE'''

IDENTITY_BY_DOCUMENT_SQL = 'SELECT K_IDTERC,I_ESTADO FROM LINIX.GR001MTERCERO WHERE A_NUMNIT=:documento'
PERSONAL_IDENTITY_NAME_SQL = ('SELECT N_NOMBR1,N_NOMBR2,N_APELL1,N_APELL2 FROM LINIX.GR001MTERCERO '
    'WHERE A_NUMNIT=:documento AND K_IDTERC=:tercero')
PAYROLL_BY_HOLDER_SQL = '''SELECT c.K_SUCURS,c.K_NOMINA,n.I_TIPNOM,n.I_ACTIVO FROM LINIX.AP014MCLIENTE c
    JOIN LINIX.AP016MDEFNOMIN n ON n.K_NOMINA=c.K_NOMINA WHERE c.K_IDTERC=:tercero'''
DUPLICATE_APPLICATION_SQL = """SELECT K_NUMDOC FROM LINIX.CA090MGSOLCRED WHERE K_TIPODR='SC'
    AND K_IDTERC=:tercero AND K_MODALI='111' AND K_DESTIN='CFA'
    AND V_MONTO=:monto AND F_SOLICI>=TO_DATE(:desde,'MM/DD/YYYY') AND ROWNUM<=2"""
WORKFLOW_ERROR_DETAILS_SQL = 'SELECT * FROM LINIX.WF1750DERREJEC WHERE K_FLUJO=:flow_id AND ROWNUM<=101'
EVALUATION_TASK_SQL = '''SELECT t.K_SERIE,t.I_ESTTAR,t.Q_TRANSI,
    XMLSERIALIZE(CONTENT r.N_XML_ENT AS CLOB),
    XMLSERIALIZE(CONTENT r.N_XML_ENT_NEW AS CLOB),
    XMLSERIALIZE(CONTENT r.N_XML_SAL AS CLOB)
    FROM LINIX.WF1711D0000 t LEFT JOIN LINIX.CE8002TREPOSIT r ON r.K_REPOSI=t.K_REPOSI
    WHERE t.K_FLUJO=:flow_id AND t.K_IDACTI=6783 ORDER BY t.K_SERIE'''
CENTRAL_RISK_TASK_SQL = '''SELECT t.K_SERIE,t.I_ESTTAR,t.Q_TRANSI,
    XMLSERIALIZE(CONTENT r.N_XML_ENT AS CLOB),
    XMLSERIALIZE(CONTENT r.N_XML_ENT_NEW AS CLOB),
    XMLSERIALIZE(CONTENT r.N_XML_SAL AS CLOB)
    FROM LINIX.WF1711D0000 t LEFT JOIN LINIX.CE8002TREPOSIT r ON r.K_REPOSI=t.K_REPOSI
    WHERE t.K_FLUJO=:flow_id AND t.K_IDACTI=4965 ORDER BY t.K_SERIE'''

INSTITUTIONAL_APPLICATION_SQL = (
    'SELECT K_NUMDOC,I_ESTSOL,I_APROBA,K_USUARI_APR,F_APROBA,I_LIQUID,F_LIQUID,'
    'I_CANCEL,N_PAGARE,A_TIPODR,A_OBLIGA FROM LINIX.CA090MGSOLCRED '
    "WHERE K_TIPODR='SC' AND K_NUMDOC=:s"
)
INSTITUTIONAL_TASKS_SQL = (
    'SELECT t.K_SERIE,t.K_IDACTI,t.K_IDRUTA,t.I_ESTTAR,t.IIEJECUC,t.K_USUARI,'
    't.F_FINAL,a.A_ACTIVI FROM LINIX.WF1711D0000 t JOIN LINIX.WF1706MACTIVID a ON a.K_IDACTI=t.K_IDACTI '
    "WHERE t.K_FLUJO=:f AND (t.K_IDACTI=6774 OR t.I_ESTTAR='A') ORDER BY t.K_SERIE"
)
INSTITUTIONAL_PAGARE_SQL = (
    'SELECT K_TIPODR,K_SUCURS,K_PAGARE,I_ESTPAG FROM LINIX.CA662MPAGARE '
    'WHERE K_TIPODR=:t AND K_PAGARE=:p'
)
NATIVE_SIGNING_TASKS_SQL = '''SELECT t.K_SERIE,t.K_IDACTI,t.K_IDRUTA,t.I_ESTTAR,t.K_USUARI,t.F_FINAL,
    XMLSERIALIZE(CONTENT r.N_XML_ENT AS CLOB),
    XMLSERIALIZE(CONTENT r.N_XML_ENT_NEW AS CLOB),
    XMLSERIALIZE(CONTENT r.N_XML_SAL AS CLOB)
    FROM LINIX.WF1711D0000 t LEFT JOIN LINIX.CE8002TREPOSIT r ON r.K_REPOSI=t.K_REPOSI
    WHERE t.K_FLUJO=:f AND (t.I_ESTTAR='A' OR t.K_IDACTI IN
    (99001065,99001080,99001082,99001083,99001086,99001088,99001090,99001091,7283,7288,6096,7281))
    ORDER BY t.K_SERIE'''

WORKFLOW_HISTORY_SQL = '''SELECT t.K_SERIE,t.K_IDACTI,t.K_IDRUTA,t.I_ESTTAR,
    t.K_USUARI,t.F_FINAL,a.I_EJECUC,a.I_CAPTUR,a.I_AUTORI,a.N_ACCION,
    XMLSERIALIZE(CONTENT r.N_XML_SAL AS CLOB)
    FROM LINIX.WF1711D0000 t
    LEFT JOIN LINIX.WF1706MACTIVID a ON a.K_IDACTI=t.K_IDACTI
    LEFT JOIN LINIX.CE8002TREPOSIT r ON r.K_REPOSI=t.K_REPOSI
    WHERE t.K_FLUJO=:flow_id ORDER BY t.K_SERIE'''

# Physical columns must be present before these SELECTs are issued. CSV aliases
# alone are not authority to guess a join or to sum accounting components.
FINANCIAL_COLUMNS = {
    'CA090MGSOLCRED': ('K_TIPODR','K_NUMDOC','K_IDTERC','K_MODALI','K_DESTIN','V_MONTO',
        'I_LIQUID','F_LIQUID','A_TIPODR','A_OBLIGA','K_CONLIQ','K_SUCURS_MOV','K_ANO','K_TIPDOC_MOV','K_NUMDOC_MOV'),
    'CA106MDLIQUIDA': ('K_SERIE','I_LIQCON','I_RECOGE','A_TIPODR_REF','A_OBLIGA_REF',
        'V_DBPESO','V_CRPESO','V_DBKAPI','V_CRKAPI','V_DBINTE','V_CRINTE','V_DBMORA','V_CRMORA',
        'K_ANO_OPG','K_SUCURS_OPG','K_TIPDOC_OPG','K_NUMDOC_OPG'),
    'CA107TCONTLIQU': ('K_CONLIQ','I_CONLIQ','N_CONLIQ','I_ORDPAG','I_ACTIVO','K_TIPMOV'),
    'GR028G0000': ('K_ANO','K_PERANO','K_SUCURS','K_TIPDOC','K_NUMDOC','K_TIPMOV','F_MOVIMI','I_ANULAD'),
    'AH135D2026': ('K_PERANO','K_SUCURS','K_TIPDOC','K_NUMDOC','K_IDTERC','K_CUENTA',
        'K_CODCON','F_MOVIMI','I_CONTAB','V_DBPESO','V_CRPESO'),
}
FINANCIAL_SCHEMA_SQL = """SELECT TABLE_NAME,COLUMN_NAME FROM ALL_TAB_COLUMNS
    WHERE OWNER='LINIX' AND TABLE_NAME IN
    ('CA090MGSOLCRED','CA106MDLIQUIDA','CA107TCONTLIQU','GR028G0000','AH135D2026')"""
FINANCIAL_FK_SQL = """SELECT fk.CONSTRAINT_NAME,child.COLUMN_NAME,parent.COLUMN_NAME,child.POSITION
    FROM ALL_CONSTRAINTS fk
    JOIN ALL_CONSTRAINTS pk ON pk.OWNER=fk.R_OWNER AND pk.CONSTRAINT_NAME=fk.R_CONSTRAINT_NAME
    JOIN ALL_CONS_COLUMNS child ON child.OWNER=fk.OWNER AND child.CONSTRAINT_NAME=fk.CONSTRAINT_NAME
    JOIN ALL_CONS_COLUMNS parent ON parent.OWNER=pk.OWNER AND parent.CONSTRAINT_NAME=pk.CONSTRAINT_NAME
        AND parent.POSITION=child.POSITION
    WHERE fk.OWNER='LINIX' AND fk.TABLE_NAME='CA106MDLIQUIDA' AND fk.CONSTRAINT_TYPE='R'
        AND pk.OWNER='LINIX' AND pk.TABLE_NAME='CA090MGSOLCRED'
        AND fk.STATUS='ENABLED' AND fk.VALIDATED='VALIDATED'
    ORDER BY fk.CONSTRAINT_NAME,child.POSITION"""
def financial_projection(table,prefix=''):
    # Oracle NUMBER must reach reconciliation as exact decimal text, not a
    # binary float selected by the client's default output type handler.
    return ','.join(("TO_CHAR("+prefix+c+",'TM9','NLS_NUMERIC_CHARACTERS=''.,''') AS "+c)
        if c.startswith('V_') else prefix+c for c in FINANCIAL_COLUMNS[table])


FINANCIAL_APPLICATION_SQL = 'SELECT '+financial_projection('CA090MGSOLCRED')+" FROM LINIX.CA090MGSOLCRED WHERE K_TIPODR='SC' AND K_NUMDOC=:s"
FINANCIAL_CONFIG_SQL = 'SELECT '+','.join(FINANCIAL_COLUMNS['CA107TCONTLIQU'])+' FROM LINIX.CA107TCONTLIQU WHERE K_CONLIQ=:c'
FINANCIAL_LEDGER_SQL = 'SELECT '+','.join(FINANCIAL_COLUMNS['GR028G0000'])+''' FROM LINIX.GR028G0000
    WHERE K_ANO=:y AND K_SUCURS=:b AND K_TIPDOC=:t AND K_NUMDOC=:n'''
# Installed CA106_CA090_FK binds the NEW obligation. *_REF_FK instead binds
# the prior obligation; K_TIPODR/K_NUMDOC are also references, not this join.
FINANCIAL_DETAIL_SQL = {pair:'SELECT '+financial_projection('CA106MDLIQUIDA')+
    ' FROM LINIX.CA106MDLIQUIDA WHERE '+pair[0]+'=:t AND '+pair[1]+'=:n ORDER BY K_SERIE'
    for pair in (('A_TIPODR','A_OBLIGA'),)}
FINANCIAL_PREVIOUS_SQL = """SELECT K_TIPODR,K_NUMDOC,K_IDTERC,K_MODALI,A_TIPODR,A_OBLIGA,
    I_CANCEL,F_CANCEL,TO_CHAR(V_SALDO,'TM9','NLS_NUMERIC_CHARACTERS=''.,''') AS V_SALDO FROM LINIX.CA090MGSOLCRED
    WHERE K_IDTERC=:h AND A_TIPODR=:t AND A_OBLIGA=:n"""
SAVINGS_BY_VOUCHER_SQL = 'SELECT ROWIDTOCHAR(h.ROWID),'+financial_projection('AH135D2026','h.')+''' FROM LINIX.AH135D2026 h
    WHERE h.K_SUCURS=:b AND h.K_TIPDOC=:t AND h.K_NUMDOC=:n
      AND h.F_MOVIMI>=:d AND h.F_MOVIMI<:u'''
SAVINGS_ACCOUNT_CUT_SQL = 'SELECT ROWIDTOCHAR(h.ROWID),'+financial_projection('AH135D2026','h.')+''' FROM LINIX.AH135D2026 h
    WHERE h.K_CUENTA=:c AND h.F_MOVIMI>=:d AND h.F_MOVIMI<:u'''

# Fixed SELECTs only. Values and audit snapshots are private journal evidence.
PILOT_FINANCIAL_SQL = {
    'CA090': """SELECT K_TIPODR,K_NUMDOC,I_CANCEL,F_CANCEL,I_APROBA,I_LIQUID,
        A_TIPODR,A_OBLIGA,N_PAGARE,V_MONTO,V_SALDO,V_SALINT,V_SALMOR
        FROM LINIX.CA090MGSOLCRED WHERE K_IDTERC=:tercero ORDER BY K_TIPODR,K_NUMDOC""",
    'CO917': """SELECT K_TIPODR,K_NUMDOC,F_DESDE,F_HASTA,V_DIFERI,I_CANCEL,F_CANCEL
        FROM LINIX.CO917MDIFERIDO WHERE K_IDTERC=:tercero ORDER BY K_TIPODR,K_NUMDOC""",
    'CA090Auditoria': """SELECT K_SERIE_ADT,K_TIPODR,K_NUMDOC,N_OPEMOV,N_CAMPACT,
        F_MOVIM_ADT,K_USUARI_ADT,I_CANCEL,F_CANCEL,I_APROBA,I_LIQUID,N_PAGARE,V_SALDO
        FROM LNXADT.CA090MAUDITAR WHERE K_IDTERC=:tercero ORDER BY K_SERIE_ADT""",
    'CO917Auditoria': """SELECT K_SERIE_ADT,K_TIPODR,K_NUMDOC,N_OPEMOV,N_CAMPACT,
        F_MOVIM_ADT,K_USUARI_ADT,F_DESDE,F_HASTA,V_DIFERI,I_CANCEL,F_CANCEL
        FROM LNXADT.CO917MAUDITAR WHERE K_IDTERC=:tercero ORDER BY K_SERIE_ADT""",
    'Pagares': """SELECT p.K_TIPODR,p.K_SUCURS,p.K_PAGARE,p.I_ESTPAG
        FROM LINIX.CA662MPAGARE p WHERE EXISTS (SELECT 1 FROM LINIX.CA090MGSOLCRED s
        WHERE s.K_IDTERC=:tercero AND s.N_PAGARE=p.K_PAGARE)
        ORDER BY p.K_TIPODR,p.K_SUCURS,p.K_PAGARE""",
    'PagaresAuditoria': """SELECT p.K_SERIE_ADT,p.K_TIPODR,p.K_SUCURS,p.K_PAGARE,
        p.I_ESTPAG,p.N_OPEMOV,p.N_CAMPACT,p.F_MOVIM_ADT,p.K_USUARI_ADT
        FROM LNXADT.CA662MAUDITAR p WHERE EXISTS (SELECT 1 FROM LINIX.CA090MGSOLCRED s
        WHERE s.K_IDTERC=:tercero AND s.N_PAGARE=p.K_PAGARE) ORDER BY p.K_SERIE_ADT""",
}


class CredihoyReadRepository:
    synthetic = False

    def __init__(self, cursor):
        self.cursor = cursor

    def radication_records(self, flow_id):
        flow_id = numeric_id(flow_id)
        self.cursor.execute(RADICATION_BY_FLOW_SQL, {"flow_id": int(flow_id)})
        return [FlowRadicationRecord(
            flow_id=numeric_id(row[0]), workflow=row[1],
            series_id=numeric_id(row[2]) if row[2] is not None else None,
            task_state=row[3], xml_input=clob_text(row[4]), xml_new=clob_text(row[5]), xml_output=clob_text(row[6]),
        ) for row in self.cursor.fetchall()]

    def application_rows(self, reference):
        if not isinstance(reference, ScReference) or reference.application_type != "SC":
            raise ProtocolError("CREDIHOY_IDENTIFICADOR_INVALIDO")
        self.cursor.execute(APPLICATION_BY_REFERENCE_SQL, {
            "application_type": "SC", "application_number": numeric_id(reference.application_number),
        })
        keys = ("K_TIPODR", "K_NUMDOC", "K_IDTERC", "K_MODALI", "K_DESTIN", "V_MONTO", "A_OBLIGA", "A_NUMNIT")
        return [dict(zip(keys, row)) for row in self.cursor.fetchall()]

    def workflow_snapshot(self, operation, observed_at, configuration_hash):
        """Own persisted task responses, not route defaults or another SC's reply.

        Configuration hash identifies the caller's reviewed catalogue. This read
        does not certify that catalogue, task effects or exclusion of native jobs.
        """
        from datetime import datetime
        from .credihoy import CredihoyEvidenceValidator, validate_profile_fields, xml_fields
        if datetime.fromisoformat(observed_at).utcoffset() is None:
            raise ProtocolError('CREDIHOY_FECHA_INVALIDA')
        validator = CredihoyEvidenceValidator(self)
        reference = validator._reference_by_flow(operation, operation.flow_id)
        validator._confirmed_observation(operation, operation.flow_id, reference)
        if (operation.application_type != 'SC'
                or operation.application_number != reference.application_number):
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA', 409)
        self.cursor.execute(WORKFLOW_HISTORY_SQL, {'flow_id': int(numeric_id(operation.flow_id))})
        rows = self.cursor.fetchmany(513)
        if len(rows) > 512:
            raise ProtocolError('CREDIHOY_TRAZA_NATIVA_INCOMPLETA')
        keys = ('K_SERIE', 'K_IDACTI', 'K_IDRUTA', 'I_ESTTAR', 'K_USUARI',
                'F_FINAL', 'I_EJECUC', 'I_CAPTUR', 'I_AUTORI', 'N_ACCION', 'salida')
        tasks = []
        for row in rows:
            if len(row) != len(keys):
                raise ProtocolError('CREDIHOY_TRAZA_NATIVA_INCOMPLETA')
            task = dict(zip(keys, row))
            task['K_SERIE'] = numeric_id(task['K_SERIE'])
            for key in ('K_IDACTI', 'K_IDRUTA'):
                task[key] = int(numeric_id(task[key]))
            if task['I_ESTTAR'] not in {'A', 'E'}:
                raise ProtocolError('CREDIHOY_TRAZA_NATIVA_INCOMPLETA')
            output = clob_text(task.pop('salida'))
            task['salidaSha256'] = digest(output) if output else None
            fields = xml_fields(output, additional_keys=('A_CODIGO_RESPUESTA',)) if output else {}
            validate_profile_fields(fields, operation.payload)
            if (('A_NUMERO_SOLI_CREDITO' in fields
                 and fields['A_NUMERO_SOLI_CREDITO'] != reference.application_number)
                    or fields.get('A_TIPO_SOLI_CREDITO', 'SC') != 'SC'):
                raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA', 409)
            task['respuesta'] = fields.get('A_CODIGO_RESPUESTA')
            task['respuestaPropiaRegistrada'] = task['respuesta'] is not None
            task['F_FINAL'] = str(task['F_FINAL']) if task['F_FINAL'] is not None else None
            tasks.append(task)
        if len({t['K_SERIE'] for t in tasks}) != len(tasks):
            raise ProtocolError('CREDIHOY_TRAZA_NATIVA_CONTRADICTORIA')
        return {'fuente': 'LINIX', 'solicitudId': str(operation.request_id),
                'intencionId': str(operation.intent_id), 'K_FLUJO': operation.flow_id,
                'SC': reference.application_number, 'observadoEn': observed_at,
                'configuracionSha256': configuration_hash, 'historialCompleto': True,
                'operacionIncierta': False, 'tareas': tasks}

    def financial_sources(self, operation, reference, observed_at):
        """Same SELECT transaction as the caller; no payment inferred from entries."""
        from hashlib import sha256
        from .credihoy import CredihoyEvidenceValidator
        validator=CredihoyEvidenceValidator(self)
        if validator._reference_by_flow(operation,operation.flow_id)!=reference:
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
        validator._confirmed_observation(operation,operation.flow_id,reference)
        return self._financial_sources(reference,observed_at,{
            'solicitudId':operation.payload['solicitudId'],'intencionId':str(operation.intent_id),
            'K_FLUJO':str(operation.flow_id),
            'titularReferencia':sha256(operation.payload['titular']['numeroDocumento'].encode()).hexdigest()})

    def financial_history(self, application_number, observed_at):
        """Historical SC only: explicitly no digital dossier, intention or flow."""
        reference=ScReference('SC',numeric_id(application_number))
        applications=self.application_rows(reference)
        if (len(applications)!=1 or str(applications[0]['K_MODALI'])!='111'
                or applications[0]['K_DESTIN']!='CFA'):
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
        from hashlib import sha256
        return self._financial_sources(reference,observed_at,{'solicitudId':None,'intencionId':None,
            'K_FLUJO':None,'titularReferencia':sha256(str(applications[0]['A_NUMNIT']).encode()).hexdigest()})

    def _financial_sources(self,reference,observed_at,scope):
        from datetime import date,datetime,timedelta
        from decimal import Decimal
        from hashlib import sha256
        from zoneinfo import ZoneInfo
        import json
        if datetime.fromisoformat(observed_at).utcoffset() is None:
            raise ProtocolError('CREDIHOY_FECHA_INVALIDA')
        def value(v):
            if isinstance(v,(datetime,date)):return v.isoformat()
            if isinstance(v,Decimal):return format(v,'f')
            return v
        def read(sql,binds,keys,limit=100):
            self.cursor.execute(sql,binds)
            rows=self.cursor.fetchmany(limit+1)
            if len(rows)>limit:raise ProtocolError('CREDIHOY_FINANCIERO_INCOMPLETO')
            return [{k:value(v) for k,v in zip(keys,row)} for row in rows]
        self.cursor.execute(FINANCIAL_SCHEMA_SQL)
        schema={name:set() for name in FINANCIAL_COLUMNS}
        for table,column in self.cursor.fetchall():
            if table in schema:schema[table].add(column)
        missing={t:sorted(set(cols)-schema[t]) for t,cols in FINANCIAL_COLUMNS.items()}
        sources={t:[] for t in FINANCIAL_COLUMNS};pending=[];binding=None
        previous=[];orders=[];account_cut=[];savings_complete=False
        for table,columns in missing.items():
            if columns:pending.append('COLUMNAS_NO_ACREDITADAS:'+table+':'+','.join(columns))
        if not missing['CA090MGSOLCRED']:
            sources['CA090MGSOLCRED']=read(FINANCIAL_APPLICATION_SQL,{'s':reference.application_number},FINANCIAL_COLUMNS['CA090MGSOLCRED'],1)
            if len(sources['CA090MGSOLCRED'])!=1:raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
            s=sources['CA090MGSOLCRED'][0]
            if str(s['K_MODALI'])!='111' or s['K_DESTIN']!='CFA':raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
            if not missing['CA107TCONTLIQU']:
                sources['CA107TCONTLIQU']=read(FINANCIAL_CONFIG_SQL,{'c':s['K_CONLIQ']},FINANCIAL_COLUMNS['CA107TCONTLIQU'])
            def ledger(key):
                if missing['GR028G0000'] or any(v is None for v in key):return []
                return read(FINANCIAL_LEDGER_SQL,dict(zip(('y','b','t','n'),key)),FINANCIAL_COLUMNS['GR028G0000'])
            sources['GR028G0000']=ledger([s[k] for k in ('K_ANO','K_SUCURS_MOV','K_TIPDOC_MOV','K_NUMDOC_MOV')])
            if not missing['AH135D2026'] and str(s['K_ANO'])=='2026' and s['F_LIQUID']:
                start=datetime.fromisoformat(s['F_LIQUID'])
                if start.utcoffset() is not None:start=start.astimezone(ZoneInfo('America/Bogota')).replace(tzinfo=None)
                start=start.replace(hour=0,minute=0,second=0,microsecond=0)
                end=datetime.fromisoformat(observed_at).astimezone(ZoneInfo('America/Bogota')).replace(tzinfo=None)
                # Oracle DATE has no offset. Read to the same observation cutoff,
                # not just the original voucher, so later debits are not hidden.
                if end>=start:
                    keys=('EVIDENCIA_FILA',*FINANCIAL_COLUMNS['AH135D2026'])
                    binds={'b':s['K_SUCURS_MOV'],'t':s['K_TIPDOC_MOV'],'n':s['K_NUMDOC_MOV'],
                        'd':start,'u':end+timedelta(seconds=1)}
                    sources['AH135D2026']=read(SAVINGS_BY_VOUCHER_SQL,binds,keys)
                    accounts={r['K_CUENTA'] for r in sources['AH135D2026'] if r['K_CUENTA'] is not None}
                    if len(accounts)==1:
                        account_cut=read(SAVINGS_ACCOUNT_CUT_SQL,{'c':next(iter(accounts)),
                            'd':start,'u':end+timedelta(seconds=1)},keys)
                        savings_complete=True
            elif not missing['AH135D2026']:
                pending.append('AH135_ANO_O_CORTE_NO_ACREDITADO')
            if not missing['CA106MDLIQUIDA']:
                self.cursor.execute(FINANCIAL_FK_SQL);groups={}
                for name,child,parent,position in self.cursor.fetchall():
                    groups.setdefault(name,[]).append((int(position),child,parent))
                candidates=[]
                for name,cols in groups.items():
                    mapping={child:parent for _,child,parent in cols}
                    # Constraint POSITION need not be our bind order. Match
                    # column pairs, then use the one fixed canonical query.
                    for pair in FINANCIAL_DETAIL_SQL:
                        if (len(cols)==len(pair) and set(mapping)==set(pair)
                                and all(mapping[c]==c and c in schema['CA106MDLIQUIDA'] for c in pair)):
                            candidates.append((name,pair,pair))
                if len(candidates)==1:
                    name,pair,parents=candidates[0]
                    binding={'restriccion':name,'columnas':list(zip(pair,parents)),
                        'origen':'ALL_CONSTRAINTS_ALL_CONS_COLUMNS'}
                    details=read(FINANCIAL_DETAIL_SQL[pair],{'t':s[parents[0]],'n':s[parents[1]]},FINANCIAL_COLUMNS['CA106MDLIQUIDA'])
                    sources['CA106MDLIQUIDA']=details
                    if 'I_CANCEL' in schema['CA090MGSOLCRED'] and 'F_CANCEL' in schema['CA090MGSOLCRED'] and 'V_SALDO' in schema['CA090MGSOLCRED']:
                        refs={(d['A_TIPODR_REF'],d['A_OBLIGA_REF']) for d in details if d['I_RECOGE']=='R'}
                        for typ,num in sorted(refs,key=str):
                            if typ and num:
                                previous.extend(read(FINANCIAL_PREVIOUS_SQL,{'h':s['K_IDTERC'],'t':typ,'n':num},
                                    ('K_TIPODR','K_NUMDOC','K_IDTERC','K_MODALI','A_TIPODR','A_OBLIGA','I_CANCEL','F_CANCEL','V_SALDO')))
                    keys={tuple(d[k] for k in ('K_ANO_OPG','K_SUCURS_OPG','K_TIPDOC_OPG','K_NUMDOC_OPG')) for d in details}
                    for key in sorted(keys,key=str):orders.extend(ledger(key))
                else:pending.append('RELACION_CA106_CA090_NO_UNIVOCA_ACREDITADA')
        body={'versionContrato':'credihoy-linix-fuentes-financieras-v1','fuente':'LINIX',
            **scope,'SC':reference.application_number,
            'observadoEn':observed_at,'filas':sources,'vinculoCA106':binding,
            'obligacionesReferenciadas':previous,'comprobantesOrden':orders,'pendientesFuente':pending,
            'ahorrosCorte':account_cut,'ahorrosCorteCompleto':savings_complete,
            'START':0,'autorizaEjecucionFinanciera':False}
        if scope['solicitudId'] is None:
            body['versionContrato']='credihoy-linix-fuentes-historicas-v1'
        body['sha256']=sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return body

    def native_signing_state(self,operation,reference,observed_at):
        """LINIX owns LINPAG. An executed/released task alone is never a signature."""
        from datetime import datetime
        from hashlib import sha256
        import json
        from .credihoy import CredihoyEvidenceValidator,xml_fields
        validator=CredihoyEvidenceValidator(self)
        if validator._reference_by_flow(operation,operation.flow_id)!=reference:
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
        validator._confirmed_observation(operation,operation.flow_id,reference)
        self.cursor.execute(INSTITUTIONAL_APPLICATION_SQL,{'s':reference.application_number})
        rows=self.cursor.fetchall()
        if len(rows)!=1:raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
        sc=dict(zip(('sc','estado','aprobada','actor','fecha','liquidada','fechaLiquidacion','cancelada',
            'pagare','tipoObligacion','obligacion'),rows[0]))
        when=datetime.fromisoformat(observed_at)
        if (when.utcoffset() is None or str(sc['sc'])!=reference.application_number
                or sc['liquidada'] not in {'Y','N'} or sc['aprobada'] not in {'Y','N'}
                or sc['cancelada'] not in {'Y','N'}):
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
        self.cursor.execute(NATIVE_SIGNING_TASKS_SQL,{'f':int(operation.flow_id)})
        rows=self.cursor.fetchmany(129)
        if len(rows)>128:raise ProtocolError('CREDIHOY_TRAZA_NATIVA_INCOMPLETA')
        tasks=[];result='PENDIENTE';proof=None;mechanism=None
        for row in rows:
            t=dict(zip(('K_SERIE','actividad','ruta','estado','usuario','fin','entrada','nueva','salida'),row))
            t={k:clob_text(v) if hasattr(v,'read') else v for k,v in t.items()}
            tasks.append(t)
        if len({str(t['K_SERIE']) for t in tasks})!=len(tasks):
            raise ProtocolError('CREDIHOY_TRAZA_NATIVA_CONTRADICTORIA')
        selectors=[t for t in tasks if t['actividad']==99001065 and t['estado']=='E']
        if selectors:
            own=xml_fields(selectors[-1]['salida'],additional_keys=('A_CODIGO_RESPUESTA',)).get('A_CODIGO_RESPUESTA')
            # These are persisted catalogue responses, not values sent by us.
            if own and 'FISICA' in own.upper():mechanism='FISICA'
            elif own and 'ELECTR' in own.upper():mechanism='ELECTRONICA'
        waits=[t for t in tasks if t['actividad'] in (99001086,99001088)]
        if waits and mechanism=='ELECTRONICA' and int(waits[-1]['K_SERIE'])>int(selectors[-1]['K_SERIE']):
            last=waits[-1]
            if last['estado']=='E' and last['usuario']=='SERVIC' and last['fin'] is not None:
                output=xml_fields(last['salida'],additional_keys=('A_CODIGO_RESPUESTA',))
                state=output.get('A_CODIGO_RESPUESTA')
                context=xml_fields(last['nueva'] or last['entrada'],additional_keys=('A_PAGARE',))
                # Never borrow AGENCIA/selector/old service output. Require the
                # same SC, holder and assigned note in this service task's input.
                bound=(context.get('A_NUMERO_SOLI_CREDITO')==reference.application_number
                    and context.get('A_CODIGO_CLIENTE')==operation.payload['titular']['numeroDocumento']
                    and context.get('A_PAGARE')==str(sc['pagare']))
                if bound and state in {'DOCUMENTO_FIRMADO','PENDIENTE_FIRMAS','RECHAZADO',
                        'RECHAZO_PROVEEDOR','RECHAZO_FIRMA','ANULADO'}:
                    result=state
                    proof={'K_SERIE':str(last['K_SERIE']),'actividad':last['actividad'],
                        'usuario':'SERVIC','respuesta':state,'repositorioSalidaSha256':
                            sha256(last['salida'].encode()).hexdigest()}
        signed=result=='DOCUMENTO_FIRMADO'
        released=(signed and any(t['actividad']==99001090 and t['estado']=='E'
            and int(t['K_SERIE'])>int(proof['K_SERIE']) for t in tasks)
            and not any(t['estado']=='A' and 99001065<=t['actividad']<=99001091 for t in tasks))
        body={'versionContrato':'credihoy-linix-liberacion-v1','fuente':'LINIX',
            'solicitudId':operation.payload['solicitudId'],'intencionId':str(operation.intent_id),
            'K_FLUJO':str(operation.flow_id),'SC':reference.application_number,
            'titularReferencia':sha256(operation.payload['titular']['numeroDocumento'].encode()).hexdigest(),
            'pagare':str(sc['pagare']) if sc['pagare'] else None,'observadoEn':observed_at,
            'aprobada':sc['aprobada']=='Y' and sc['cancelada']=='N',
            'firma':{'mecanismo':mechanism,'estadoInstitucional':result,'acreditadaEnLINIX':signed,
                'liberadaPorWorkflow':bool(released),'evidenciaServicio':proof,'archivoFirmadoRecuperado':False},
            'liquidacion':{'liquidada':sc['liquidada']=='Y',
                'fecha':str(sc['fechaLiquidacion']) if sc['fechaLiquidacion'] else None},
            'desembolso':{'acreditado':False,'referencia':None},
            'tareasActivas':[{'actividad':t['actividad'],'K_SERIE':str(t['K_SERIE']),
                'ruta':t['ruta'],'usuario':t['usuario']} for t in tasks if t['estado']=='A'],
            'autorizaEjecucionFinanciera':False,'START':0}
        body['sha256']=sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return body

    def institutional_state(self, operation, reference, observed_at, *, incidents=None):
        """Verified approval/prefirma snapshot, not a channel evaluation or grant."""
        from datetime import datetime
        import hashlib
        import json
        from .credihoy import CredihoyEvidenceValidator
        validator = CredihoyEvidenceValidator(self)
        if validator._reference_by_flow(operation,operation.flow_id) != reference:
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
        validator._confirmed_observation(operation,operation.flow_id,reference)
        self.cursor.execute(INSTITUTIONAL_APPLICATION_SQL, {'s':reference.application_number})
        rows = self.cursor.fetchall()
        if len(rows)!=1:
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
        sc=dict(zip(('sc','estado','aprobada','actor','fecha','liquidada','fechaLiquidacion','cancelada',
                     'pagare','tipoObligacion','obligacion'),rows[0]))
        if str(sc['sc'])!=reference.application_number:
            raise ProtocolError('CREDIHOY_CORRESPONDENCIA_INVALIDA')
        if (sc['estado']!='A' or sc['aprobada']!='Y' or not sc['actor'] or not sc['fecha']
                or sc['liquidada']!='N' or sc['cancelada']!='N' or not sc['pagare']
                or str(sc['obligacion'])!=str(sc['pagare'])):
            return None
        self.cursor.execute(INSTITUTIONAL_TASKS_SQL, {'f':int(operation.flow_id)})
        tasks=[dict(zip(('serie','actividad','ruta','estado','modo','actor','fin','nombre'),r)) for r in self.cursor.fetchall()]
        approved=[t for t in tasks if t['actividad']==6774]
        active=[t for t in tasks if t['estado']=='A']
        if (len(approved)!=1 or approved[0]['estado']!='E' or approved[0]['actor']!=sc['actor']
                or approved[0]['fin'] is None or len(active)!=1 or active[0]['actividad']!=99001065
                or active[0]['ruta']!=10003122 or active[0]['modo']!='H'):
            raise ProtocolError('CREDIHOY_APROBACION_PREFIRMA_NO_ACREDITADA')
        self.cursor.execute(INSTITUTIONAL_PAGARE_SQL, {'t':sc['tipoObligacion'],'p':sc['pagare']})
        notes=self.cursor.fetchall()
        if (len(notes)!=1 or notes[0][3]!='U' or str(notes[0][2])!=str(sc['pagare'])
                or str(notes[0][0])!=str(sc['tipoObligacion'])):
            raise ProtocolError('CREDIHOY_PAGARE_NO_CONCILIADO')
        observed=datetime.fromisoformat(observed_at)
        approval_date=sc['fecha'].date().isoformat() if isinstance(sc['fecha'],datetime) else sc['fecha'].isoformat()
        if observed.utcoffset() is None or approval_date>observed.date().isoformat():
            raise ProtocolError('CREDIHOY_FECHA_LECTURA_INVALIDA')
        result={'versionContrato':'credihoy-linix-estado-v2','solicitudId':operation.payload['solicitudId'],
            'intencionId':str(operation.intent_id),'kFlujo':str(operation.flow_id),'numeroSc':str(sc['sc']),
            'estado':'APROBADA_EN_LINIX_PENDIENTE_FIRMA','actualizadoAt':observed_at,'startEjecutados':1,
            'actividad':{'codigo':99001065,'nombre':active[0]['nombre'],'kSerie':str(active[0]['serie']),
                         'ruta':'10003122','estado':'A','tipoEjecucion':'H'},
            'aprobacion':{'estadoSolicitud':'A','aprobada':True,'fecha':approval_date,
                'aprobadorReferencia':sc['actor'],'actividad':6774,'kSerie':str(approved[0]['serie']),'estadoActividad':'E'},
            'liquidacion':{'liquidada':False,'fecha':None},
            'pagare':dict(zip(('tipo','sucursal','numero','estado'),map(str,notes[0]))),
            'firma':{'estado':'PENDIENTE','acreditada':False},
            'incidencias':{key:(incidents or {}).get(key) for key in
                          ('registradas','enTareasActivas','enTareasEjecutadas')},
            'procedencia':{'sistema':'LINIX','metodo':'SELECT_CA090_WORKFLOW_CA662','perfil':['390','111','CFA']}}
        result['evidenciaSha256']=hashlib.sha256(json.dumps(result,sort_keys=True,default=str).encode()).hexdigest()
        return result

    def reference_records(self, flow_id):
        self.cursor.execute(REFERENCE_TASK_SQL, {"flow_id": int(numeric_id(flow_id))})
        return [{"serie": row[0], "estado": row[1], "entrada": clob_text(row[2]),
                 "entradaNueva": clob_text(row[3]), "salida": clob_text(row[4])}
                for row in self.cursor.fetchall()]

    def evaluation_records(self, flow_id):
        return self._task_records(EVALUATION_TASK_SQL, flow_id)

    def central_risk_records(self, flow_id):
        return self._task_records(CENTRAL_RISK_TASK_SQL, flow_id)

    def _task_records(self, sql, flow_id):
        self.cursor.execute(sql, {'flow_id': int(numeric_id(flow_id))})
        return [{'serie': row[0], 'estado': row[1], 'transicion': row[2],
                 'entrada': clob_text(row[3]), 'entradaNueva': clob_text(row[4]),
                 'salida': clob_text(row[5])} for row in self.cursor.fetchall()]
