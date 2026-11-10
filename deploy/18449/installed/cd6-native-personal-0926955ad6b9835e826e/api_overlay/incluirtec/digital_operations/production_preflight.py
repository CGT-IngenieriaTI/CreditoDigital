"""Targeted SELECT-only preflight. Metadata alone never certifies side effects."""
from datetime import datetime

from .contracts import canonical
from .production_preparation import digest

EXECUTION_POLICY_VERSION = 'credihoy-ejecucion-dos-recorridos-v1'
SPECIAL_APPROVALS = frozenset({149, 5997, 6094, 7238, 7239, 7240})
FINANCIAL_OR_SIGNATURE = frozenset({6741, 6096, 7281, 99001065, 99001082, 99001086, 99001088, 99001090})
ORDINARY_STEPS = {
    6076: (10002907, 'APROBAR'), 6711: (10002851, 'CONTINUAR ESTUDIO'),
    121: (10002852, 'COMITE FABRICA CREDIHOY'), 7265: (10003054, 'APRUEBA'),
    6759: (10003002, 'CUMPLE'), 6774: (10002875, None),
}
EVALUATION_ACTION = 'PK_WF_MOVIMIENTO.PR_EJECUTA_EVALUACION'
APPROVAL_ACTION = 'PK_CA_WF_SOLICITUD_CREDITO.PR_APRUEBA_SOLICITUD'
DATE_ADJUSTMENT_ACTION = 'LINIX.PK_CA_WF_FECHA_CICLO_CREDITO.PR_AJUSTE_FECHA_INICIAL_CICLO'
DATE_ADJUSTMENT_SIGNATURE = (
    (1,'V_K_FLUJO','IN','NUMBER','',''),
    (2,'V_K_SERIE','IN','NUMBER','',''),
    (3,'N_XML_ENT','IN','OPAQUE/XMLTYPE','PUBLIC','XMLTYPE'),
    (4,'N_ERROR','OUT','VARCHAR2','',''),
    (5,'N_XML_SAL','OUT','OPAQUE/XMLTYPE','PUBLIC','XMLTYPE'),
    (6,'N_XML_ENT_NEW','OUT','OPAQUE/XMLTYPE','PUBLIC','XMLTYPE'),
)
AUTOMATIC_STEPS = frozenset({5990, 6736, 7136, 115, 6737, 6738, 6758,
    6782, 99001081, 6838, 6839, 99000768})
NATIVE_FINANCIAL_ACTIONS = {
    6096:'PK_CA_WF_CONTABILIZA_CREDITO.PR_LIQUIDA_CREDITO',
    7281:'PK_CA_WF_CONTABILIZA_CREDITO.PR_CONTABILIZA_CREDITO',
}
SPECIAL_PREFIX = AUTOMATIC_STEPS - {6782, 99001081, 6838, 6839, 99000768}
SPECIAL_DESTINATIONS = {
    10002853:'COMITE DE CREDITOS INTERNO', 10002855:'COMITE DE GERENCIA',
    10002856:'CONSEJO DE ADMINISTRACION', 10002991:'COMIT\u00c9 INSTITUCIONAL DE CREDITO',
    10002987:'COMITE FABRICA DE CR\u00c9DITO',
}


def native_prefix_plan(report):
    """Trace only configured paths that can reach 121. Never certify execution/jobs."""
    closed={'prefijoConfiguradoAcreditado':False,'paradaNativaProbada':False,
        'autorizaStart':False,'handoffs':[],'rutasPrefijo':[],
        'limite':'CATALOGO_Y_CAMINOS_NO_SON_UNA_PRUEBA_DE_INSTANCIA_O_EXCLUSION_DE_JOBS'}
    try:
        model=report['modelo']
        if (report.get('soloLectura') is not True or len(model)!=1
                or model[0]['K_MODELO']!='CREDIHOY_VAL_EMPLEADO'
                or model[0]['I_ACTIVO']!='Y' or model[0]['K_PROCES']!='CREDITOS'):
            return closed
        root=model[0]['K_IDRUTA'];routes={};slots={}
        if type(root) is not int or root!=10002896:return closed
        for row in report['rutas']:
            route,slot=row['K_IDRUTA'],row['K_SERIE']
            if (type(route) is not int or type(slot) is not int or (route,slot) in slots
                    or type(row['Q_TRANSI']) is not int):return closed
            routes.setdefault(route,[]).append(row);slots[route,slot]=row
        if len(routes)>256 or root not in routes:return closed
        selector_rows=[r for rows in routes.values() for r in rows if r['K_IDACTI']==121]
        # The decision-control slot may repeat the activity ID of its AC slot.
        selectors=[r for r in selector_rows if r['Q_TRANSI']==1]
        if (len(selectors)!=1 or any(selectors[0].get(k)!=v for k,v in
                {'K_IDRUTA':10002852,'K_SERIE':7423,'I_EJECUC':'H',
                 'I_CAPTUR':'N','I_AUTORI':'N','N_ACCION':EVALUATION_ACTION}.items())
                or any(r['K_IDRUTA']!=10002852 for r in selector_rows)):
            return closed
        cuts={route:min((r['Q_TRANSI'] for r in rows if r['K_IDACTI']==121),default=float('inf'))
            for route,rows in routes.items()}
        targets={route for route,cut in cuts.items() if cut!=float('inf')}
        edges={route:set() for route in routes};missing={};direct=[]
        for path in report['caminos']:
            row=slots.get((path['K_IDRUTA'],path['K_SERIE']))
            if row is None:return closed
            if row['Q_TRANSI']>=cuts[row['K_IDRUTA']]:continue
            target=path['K_IDRUTA_CAM'];activity=path['K_IDACTI_CAM']
            if target is not None:
                if target in routes:edges[row['K_IDRUTA']].add(target)
                else:missing.setdefault(row['K_IDRUTA'],set()).add(target)
            if activity is not None:
                direct.append({'ruta':row['K_IDRUTA'],'slot':row['K_SERIE'],
                    'actividadDestino':activity,'respuesta':path['N_CONDIC']})
        forward={root};backward=set(targets)
        for _ in range(len(routes)):
            new=forward|{n for p in forward for n in edges[p]}
            if new==forward:break
            forward=new
        for _ in range(len(routes)):
            new=backward|{p for p,children in edges.items() if children&backward}
            if new==backward:break
            backward=new
        relevant=forward&backward
        missing_reachable=sorted({n for p in forward for n in missing.get(p,set())})
        # Direct activity destinations are reported, not followed as route IDs.
        # In particular, a jump to 121 cannot prove its required route/slot.
        unresolved_direct=[x for x in direct if x['ruta'] in forward and x['actividadDestino']==121]
        if root not in relevant or missing_reachable or unresolved_direct:
            return {**closed,'rutasNoVisibles':missing_reachable,
                'saltosDirectosNoResueltos':unresolved_direct}
        activities=[];seen=set()
        for route in sorted(relevant):
            for row in sorted(routes[route],key=lambda r:(r['Q_TRANSI'],r['K_SERIE'])):
                activity=row['K_IDACTI']
                if activity is None or row['Q_TRANSI']>cuts[route] or (route,activity) in seen:continue
                seen.add((route,activity))
                activities.append({k:row.get(k) for k in ('K_IDRUTA','K_SERIE','Q_TRANSI','K_IDACTI',
                    'I_EJECUC','I_CAPTUR','I_AUTORI','I_RESPON','K_GRUPO','K_USUARI','N_ACCION','N_REGLA')})
        return {**closed,'prefijoConfiguradoAcreditado':True,'rutaInicial':root,
            'rutasPrefijo':sorted(relevant),'rutasNoVisibles':missing_reachable,
            'actividadesPrefijo':activities,'saltosDirectos':[x for x in direct if x['ruta'] in relevant],
            'handoffs':[a for a in activities if a['I_EJECUC']=='H'],
            'snapshotSha256':digest(canonical({'modelo':model,'rutas':report['rutas'],'caminos':report['caminos']}))}
    except (KeyError,TypeError,ValueError,AttributeError):return closed


def verify_execution_policy(policy, *, request_id, intent_id, holder_reference, now, for_start=False):
    """Validate a policy SEALED in the existing journal, not browser assertions.

    The hash detects drift; authentication comes from the encrypted owner journal.
    A preparation contract never grants production START or financial transport.
    """
    import re
    from uuid import UUID
    try:
        fields = {'versionContrato', 'solicitudId', 'intencionId', 'titularReferencia',
            'tipo', 'clasificacionEstado', 'clasificacionReferencia', 'evaluacionReferencia',
            'decisionSha256', 'configuracionSha256', 'preparadoEn', 'clasificacionVigenteHasta',
            'precondicionesRadicacionAcreditadas', 'destinoHumano', 'parada',
            'autorizaStart', 'autorizaFirma', 'autorizaDesembolso', 'sha256'}
        if (type(policy) is not dict or set(policy) != fields
                or policy['versionContrato'] != EXECUTION_POLICY_VERSION
                or str(UUID(policy['solicitudId'])) != str(request_id)
                or str(UUID(policy['intencionId'])) != str(intent_id)
                or policy['titularReferencia'] != holder_reference
                or not re.fullmatch('[a-f0-9]{64}', holder_reference)
                or policy['tipo'] not in {'ORDINARIO', 'ESPECIAL', 'INDETERMINADO'}
                or any(policy[k] is not False for k in ('autorizaStart', 'autorizaFirma', 'autorizaDesembolso'))
                or type(policy['precondicionesRadicacionAcreditadas']) is not bool
                or not policy['clasificacionReferencia'] or not policy['evaluacionReferencia']
                or any(not re.fullmatch('[a-f0-9]{64}', policy[k]) for k in
                    ('decisionSha256', 'configuracionSha256', 'sha256'))
                or digest(canonical({k:v for k,v in policy.items() if k != 'sha256'})) != policy['sha256']):
            raise ValueError()
        prepared, end = (datetime.fromisoformat(policy[k]) for k in ('preparadoEn', 'clasificacionVigenteHasta'))
        if (now.utcoffset() is None or prepared.utcoffset() is None or end.utcoffset() is None
                or not prepared <= now or not 0 < (end-prepared).total_seconds() <= 300
                or (for_start and not now < end)):
            raise ValueError()
        if policy['tipo'] == 'ORDINARIO':
            if (policy['clasificacionEstado'] != 'APTO_PARA_AUTOMATIZACION_ORDINARIA'
                    or policy['destinoHumano'] is not None or policy['parada'] is not None):
                raise ValueError()
        elif policy['tipo'] == 'ESPECIAL':
            destination = policy['destinoHumano']
            if (policy['clasificacionEstado'] not in {'REQUIERE_CONSEJO_ADMINISTRACION',
                    'REQUIERE_COMITE_INSTITUCIONAL_CREDITO','REQUIERE_ESTAMENTO_INSTITUCIONAL_ACREDITADO'}
                    or type(destination) is not dict or set(destination) != {'ruta', 'respuestaSeleccion'}
                    or type(destination['ruta']) is not int or destination['ruta'] not in SPECIAL_DESTINATIONS
                    or destination['respuestaSeleccion'] != SPECIAL_DESTINATIONS[destination['ruta']]
                    or (policy['clasificacionEstado'] == 'REQUIERE_CONSEJO_ADMINISTRACION'
                        and destination['ruta'] != 10002856)
                    or (policy['clasificacionEstado'] == 'REQUIERE_COMITE_INSTITUCIONAL_CREDITO'
                        and destination['ruta'] != 10002991)
                    or policy['parada'] != {'actividad':121, 'ruta':10002852, 'slot':7423}):
                raise ValueError()
        return policy
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError('POLITICA_EJECUCION_NO_ACREDITADA') from None


def workflow_step_plan(policy, snapshot, *, request_id, intent_id, holder_reference, now):
    """One task at a time. No H/A flag or previous response is an authority."""
    verify_execution_policy(policy, request_id=request_id, intent_id=intent_id,
        holder_reference=holder_reference, now=now)
    stopped = {'permitido':False, 'autorizaStart':False, 'autorizaDesembolso':False,
        'codigo':'INDETERMINADO_NO_EJECUTAR', 'tipo':policy['tipo']}
    if policy['tipo'] == 'INDETERMINADO' or not policy['precondicionesRadicacionAcreditadas']:
        return stopped
    try:
        when = datetime.fromisoformat(snapshot['observadoEn'])
        if (snapshot['solicitudId'] != str(request_id) or snapshot['intencionId'] != str(intent_id)
                or not str(snapshot['K_FLUJO']).isdigit() or not str(snapshot['SC']).isdigit()
                or snapshot['fuente'] != 'LINIX' or snapshot['operacionIncierta'] is not False
                or snapshot.get('historialCompleto') is not True
                or snapshot['configuracionSha256'] != policy['configuracionSha256']
                or when.utcoffset() is None or not 0 <= (now-when).total_seconds() <= 300):
            raise ValueError()
        tasks = snapshot['tareas']
        if type(tasks) is not list or any(type(t) is not dict for t in tasks):raise ValueError()
        if any(t['I_ESTTAR'] not in {'A','E'} for t in tasks):raise ValueError()
        if len({str(t['K_SERIE']) for t in tasks}) != len(tasks):raise ValueError()
        active = [t for t in tasks if t['I_ESTTAR'] == 'A']
        if len(active) != 1:return {**stopped, 'codigo':'TAREA_ACTIVA_NO_UNICA_CONCILIAR'}
        task = active[0]
        activity, route = task['K_IDACTI'], task['K_IDRUTA']
        if (type(activity) is not int or type(route) is not int
                or not str(task['K_SERIE']).isdigit() or int(task['K_SERIE']) <= 0):raise ValueError()
        context = {'actividad':activity, 'K_SERIE':str(task['K_SERIE']),
            'K_FLUJO':str(snapshot['K_FLUJO']), 'SC':str(snapshot['SC']), 'ruta':route}
        def stop(code):return {**stopped, **context, 'codigo':code}
        if activity==7283:
            return stop('AJUSTE_FECHA_7283_REQUIERE_ANALISTA_Y_CONTRATO_NATIVO')
        if activity in FINANCIAL_OR_SIGNATURE and activity not in NATIVE_FINANCIAL_ACTIONS:
            return stop('FIRMA_O_EFECTO_FINANCIERO_REQUIERE_OTRO_CONTRATO')
        if activity in SPECIAL_APPROVALS:return stop('DECISION_ESTAMENTO_EXCLUSIVAMENTE_HUMANA')
        if any(t['I_ESTTAR']=='E' and t['K_IDACTI']==activity for t in tasks):
            return stop('ACTIVIDAD_YA_EJECUTADA_NO_REPETIR')
        if task.get('respuestaPropiaRegistrada') is not False:return stop('RESPUESTA_PREVIA_O_INDETERMINADA_SOLO_CONCILIAR')
        if (task.get('I_EJECUC') not in {'A','H'} or task.get('I_CAPTUR') != 'N'
                or task.get('I_AUTORI') != 'N'):
            return stop('CAPTURA_O_AUTORIZACION_ADICIONAL_NO_ACREDITADA')
        special = policy['tipo'] == 'ESPECIAL'
        if special:
            if activity in {6076,6711}:
                return {**stop('HANDOFF_CONCEPTO_DIRECTOR_6076' if activity==6076 else
                    'HANDOFF_CONTINUACION_ESTUDIO_6711'),
                    'requiereResultadoPropio':True,'objetivoPosterior':121,
                    'ejecucionCatalogo':task.get('I_EJECUC'),
                    'noReutilizarRespuestaOrdinaria':True}
            if activity == 121 or route == 10002852:return stop('ESPERA_SELECCION_HUMANA_121')
            if activity in ORDINARY_STEPS or route == policy['destinoHumano']['ruta']:
                return stop('CGTE_NO_APRUEBA_ESTAMENTOS_ESPECIALES')
            for previous in (t for t in tasks if t['K_IDACTI'] in {6076,6711} and t['I_ESTTAR']=='E'):
                handoffs = snapshot.get('handoffsHumanos', [])
                own = [h for h in handoffs if type(h) is dict
                    and str(h.get('K_SERIE'))==str(previous['K_SERIE'])] if type(handoffs) is list else []
                if len(own)!=1:return stop('HANDOFF_PREVIO_NO_CONCILIADO')
                h=own[0]
                if (h.get('fuente')!='LINIX' or h.get('solicitudId')!=str(request_id)
                        or h.get('intencionId')!=str(intent_id)
                        or str(h.get('K_FLUJO'))!=str(snapshot['K_FLUJO'])
                        or str(h.get('SC'))!=str(snapshot['SC']) or not h.get('referencia')
                        or h.get('intervencionHumanaAcreditada') is not True
                        or h.get('responsableVerificado') is not True
                        or not previous.get('K_USUARI') or h.get('usuario')!=previous['K_USUARI']
                        or h.get('respuesta')!=previous.get('respuesta')):
                    return stop('HANDOFF_PREVIO_NO_CONCILIADO')
                expected = 'APROBAR' if previous['K_IDACTI']==6076 else 'CONTINUAR ESTUDIO'
                if h['respuesta']!=expected:return stop('RECHAZO_O_RETORNO_HUMANO_NO_CONTINUAR')
            selected = [t for t in tasks if t['K_IDACTI'] == 121 and t['I_ESTTAR'] == 'E']
            if selected:
                decision = snapshot.get('decisionHumana', {})
                if (len(selected) != 1 or not selected[0].get('K_USUARI')
                        or selected[0]['K_USUARI'].upper() in {'CGTE','SYS','SYSTEM'}
                        or selected[0].get('respuesta') != policy['destinoHumano']['respuestaSeleccion']):
                    return stop('SELECCION_HUMANA_NO_ACREDITADA')
                if (type(decision) is not dict or decision.get('fuente') != 'LINIX'
                        or decision.get('solicitudId') != str(request_id)
                        or str(decision.get('K_FLUJO')) != str(snapshot['K_FLUJO'])
                        or str(decision.get('SC')) != str(snapshot['SC'])
                        or decision.get('ruta') != policy['destinoHumano']['ruta']
                        or decision.get('estadoTarea') != 'E' or not decision.get('referencia')
                        or not decision.get('usuario') or decision['usuario'].upper() in {'CGTE','SYS','SYSTEM'}):
                    return stop('ESPERA_DECISION_HUMANA_ACREDITADA')
                matches = [t for t in tasks if str(t['K_SERIE']) == str(decision.get('K_SERIE'))
                    and t['I_ESTTAR'] == 'E' and t['K_IDRUTA'] == decision['ruta']
                    and t.get('K_USUARI') == decision['usuario'] and t.get('respuesta') == decision.get('respuesta')]
                if len(matches) != 1:return stop('DECISION_HUMANA_NO_CONCILIA_TAREA')
                if decision.get('respuesta') in {'RECHAZAR','RECHAZA'}:return stop('RECHAZO_HUMANO_NO_CONTINUAR')
                if decision.get('respuesta') not in {'APROBAR','APRUEBA'}:return stop('DECISION_HUMANA_NO_CONCLUYENTE')
        if activity in NATIVE_FINANCIAL_ACTIONS:
            if special and not selected:return stop('ESPERA_SELECCION_Y_DECISION_HUMANA_121')
            proof=snapshot.get('postfirmaNativa')
            if not isinstance(proof,dict):return stop('FIRMA_O_EFECTO_FINANCIERO_REQUIERE_OTRO_CONTRATO')
            try:
                observed=datetime.fromisoformat(proof['observadoEn'])
                body={k:v for k,v in proof.items() if k!='sha256'}
                review=snapshot['revisionEfectos'];signature=proof['firma']
                if (proof['sha256']!=digest(canonical(body)) or proof['fuente']!='LINIX'
                        or proof['solicitudId']!=str(request_id) or proof['intencionId']!=str(intent_id)
                        or proof['K_FLUJO']!=context['K_FLUJO'] or proof['SC']!=context['SC']
                        or proof['titularReferencia']!=holder_reference or not proof['pagare']
                        or proof['aprobada'] is not True
                        or observed.utcoffset() is None or not 0<=(now-observed).total_seconds()<=300
                        or signature['acreditadaEnLINIX'] is not True or signature['liberadaPorWorkflow'] is not True
                        or signature['estadoInstitucional']!='DOCUMENTO_FIRMADO'
                        or snapshot.get('documentosCanalVerificados') is not True
                        or snapshot.get('condicionesAceptadasVerificadas') is not True
                        or type(snapshot.get('recogidaRequerida')) is not bool
                        or (snapshot['recogidaRequerida'] is True and snapshot.get('recogidaPreparada') is not True)
                        or task['I_EJECUC']!='A' or task['N_ACCION']!=NATIVE_FINANCIAL_ACTIONS[activity]
                        or route!=10002874 or task.get('K_USUARI')!='CGTE'
                        or str(review.get('K_SERIE'))!=context['K_SERIE']
                        or review.get('configuracionSha256')!=policy['configuracionSha256']
                        or not review.get('referencia') or review.get('accion')!=task['N_ACCION']
                        or review.get('conciliacionFinancieraDisponible') is not True
                        or review.get('actividadesNativasPermitidas')!=[activity]):raise ValueError()
                if activity==7281 and proof['liquidacion']['liquidada'] is not True:
                    return stop('LIQUIDACION_NO_CONCILIADA')
                if activity==6096 and proof['liquidacion']['liquidada'] is not False:
                    return stop('LIQUIDACION_PREVIA_O_INCIERTA_CONCILIAR')
                if snapshot.get('desembolsosPrevios')!=0 or type(snapshot.get('desembolsosPrevios')) is not int:
                    return stop('DESEMBOLSO_PREVIO_O_INCIERTO_CONCILIAR')
                return {**stopped,**context,'permitido':True,'codigo':'PASO_NATIVO_FINANCIERO_PREPARADO',
                    'respuesta':None,'usuarioWorkflow':'CGTE','accion':task['N_ACCION'],
                    'actividadesNativasPermitidas':[activity],'requiereConciliacionFinanciera':True}
            except (KeyError,TypeError,ValueError):return stop('PRECONDICIONES_POSTFIRMA_NO_ACREDITADAS')
        # A per-task effects review must cover engine chaining, not only the call.
        review = snapshot.get('revisionEfectos', {})
        if (type(review) is not dict or str(review.get('K_SERIE')) != str(task['K_SERIE'])
                or review.get('configuracionSha256') != policy['configuracionSha256']
                or review.get('sinFirmaLiquidacionContabilizacionDesembolso') is not True
                or not review.get('referencia')
                or (special and not selected and review.get('paradaNativaAntesSeleccion') is not True)):
            return stop('EFECTOS_Y_ENCADENAMIENTO_NO_ACREDITADOS')
        response = None
        if not special and activity in ORDINARY_STEPS:
            expected_route, response = ORDINARY_STEPS[activity]
            action = APPROVAL_ACTION if activity == 6774 else EVALUATION_ACTION
            if route != expected_route or task.get('N_ACCION') != action:return stop('CONTRATO_ACTIVIDAD_CAMBIO')
            if activity == 6759 and snapshot.get('garantiasCumplen') is not True:
                return stop('GARANTIA_327_NO_ACREDITADA')
        else:
            if (activity not in AUTOMATIC_STEPS or (special and not selected and activity not in SPECIAL_PREFIX)
                    or task.get('I_EJECUC') != 'A' or review.get('sinDecisionNegocio') is not True
                    or not task.get('N_ACCION')):
                return stop('ACTIVIDAD_NO_AUTORIZADA_AUTOMATICAMENTE')
            # No generic EXECUTE recovery: only explicitly reviewed automatic tasks.
            if review.get('accion') != task['N_ACCION']:return stop('ACCION_AUTOMATICA_NO_REVISADA')
        return {**stopped, **context, 'permitido':True, 'codigo':'PASO_LOCAL_AUTORIZADO_TRANSPORTE_CERRADO',
            'respuesta':response, 'usuarioWorkflow':'CGTE', 'accion':task['N_ACCION']}
    except (KeyError, ValueError, TypeError, AttributeError):
        return {**stopped, 'codigo':'LECTURA_WORKFLOW_NO_ACREDITADA'}


QUERIES = {
    "identidad": """SELECT SYS_CONTEXT('USERENV','DB_NAME') DB_NAME,
        SYS_CONTEXT('USERENV','DB_UNIQUE_NAME') DB_UNIQUE_NAME,
        SYS_CONTEXT('USERENV','SERVICE_NAME') SERVICE_NAME,
        SYS_CONTEXT('USERENV','INSTANCE_NAME') INSTANCE_NAME,
        SYS_CONTEXT('USERENV','SERVER_HOST') HOST_NAME,
        SYS_CONTEXT('USERENV','SESSION_USER') SESSION_USER FROM DUAL""",
    "modelo": """SELECT K_PROCES,K_MODELO,I_ACTIVO,K_IDRUTA FROM LINIX.WF1708MMODELO
        WHERE K_MODELO='CREDIHOY_VAL_EMPLEADO'""",
    "ruta": """SELECT d.K_SERIE,d.Q_TRANSI,d.K_IDACTI,a.I_EJECUC,a.I_CAPTUR,a.I_AUTORI,a.N_ACCION
        FROM LINIX.WF1712MRUTAFLU r JOIN LINIX.WF1713DRUTAFLU d ON d.K_IDRUTA=r.K_IDRUTA
        LEFT JOIN LINIX.WF1706MACTIVID a ON a.K_IDACTI=d.K_IDACTI
        WHERE r.K_MODELO='CREDIHOY_VAL_EMPLEADO' AND r.K_IDRUTA=10002896
        ORDER BY d.Q_TRANSI,d.K_SERIE""",
    "caminos": """SELECT K_SERIE,K_IDCAMI,I_DEFAUL,N_CONDIC,K_IDRUTA_CAM,K_IDACTI_CAM,Q_PRIORI
        FROM LINIX.WF1714DCAMINOR WHERE K_IDRUTA=10002896 ORDER BY K_SERIE,Q_PRIORI,K_IDCAMI""",
    "packages": """SELECT OBJECT_NAME,OBJECT_TYPE,STATUS,LAST_DDL_TIME FROM ALL_OBJECTS
        WHERE OWNER='LINIX' AND OBJECT_NAME IN
        ('PK_WF_FLUJO','PK_CA_WF_RADICA_SOL_CREDITO','PK_CA_WF_REFERENCIA')
        ORDER BY OBJECT_NAME,OBJECT_TYPE""",
    "triggers": """SELECT TRIGGER_NAME,TABLE_NAME,STATUS,TRIGGERING_EVENT FROM ALL_TRIGGERS
        WHERE TABLE_OWNER='LINIX' AND TABLE_NAME IN
        ('CA090MGSOLCRED','CA656DREFXSOL','WF1710MFLUJO','WF1711D0000') ORDER BY TABLE_NAME,TRIGGER_NAME""",
}

ROUTE_DETAIL_SQL = QUERIES['ruta'].replace(
    "r.K_MODELO='CREDIHOY_VAL_EMPLEADO' AND r.K_IDRUTA=10002896", "r.K_IDRUTA=:route_id")
ROUTE_PATHS_SQL = QUERIES['caminos'].replace('K_IDRUTA=10002896', 'K_IDRUTA=:route_id')


def _paths_before_human(route, paths):
    stop = min((row['Q_TRANSI'] for row in route if row['I_EJECUC'] == 'H'), default=float('inf'))
    automatic_prefix = {row['K_SERIE'] for row in route if row['Q_TRANSI'] < stop}
    return [path for path in paths if path['K_SERIE'] in automatic_prefix]


def snapshot(cursor, *, follow_paths=False):
    result = {}
    for name, query in QUERIES.items():
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        result[name] = [{key: value.isoformat() if isinstance(value, datetime) else value
                         for key, value in zip(columns, row)} for row in cursor.fetchall()]
    if follow_paths:
        pending = [row['K_IDRUTA_CAM'] for row in _paths_before_human(result['ruta'], result['caminos'])
                   if row['K_IDRUTA_CAM'] is not None]
        visited = {10002896}
        result['rutasDerivadas'] = []
        while pending and len(visited) < 16:
            route_id = pending.pop(0)
            if route_id in visited:
                continue
            visited.add(route_id)
            detail = {'K_IDRUTA': route_id}
            for name, sql in (('ruta', ROUTE_DETAIL_SQL), ('caminos', ROUTE_PATHS_SQL)):
                cursor.execute(sql, {'route_id': int(route_id)})
                columns = [col[0] for col in cursor.description]
                detail[name] = [dict(zip(columns, row)) for row in cursor.fetchall()]
            result['rutasDerivadas'].append(detail)
            pending.extend(row['K_IDRUTA_CAM'] for row in _paths_before_human(detail['ruta'], detail['caminos'])
                           if row['K_IDRUTA_CAM'] is not None and row['K_IDRUTA_CAM'] not in visited)
        result['rutasDerivadasIncompletas'] = any(route_id not in visited for route_id in pending)
    return {"configuracion": result, "sha256": digest(canonical(result))}


def require_configuration(observed, *, legacy_cut=False, expected_identity=None):
    """Validate observed configuration, not containment of package/trigger effects."""
    config = observed["configuracion"]
    if digest(canonical(config)) != observed["sha256"]:
        raise ValueError("PREFLIGHT_CONFIGURACION_ALTERADA")
    identity = config["identidad"]
    if expected_identity is not None:
        if len(identity) != 1 or identity[0] != expected_identity:
            raise ValueError('CD6_QA_CONFIGURACION_DE_OTRO_DESTINO')
    elif (len(identity) != 1 or identity[0]["DB_NAME"].lower() != "linix"
            or identity[0]["SERVICE_NAME"].lower() != "linix"
            or identity[0]["HOST_NAME"] != "svr-cgt-orabd"
            or identity[0]["SESSION_USER"].upper() in {"SYS", "SYSTEM", "SYSBACKUP", "SYSDG", "SYSKM"}):
        raise ValueError("DESTINO_PRODUCTIVO_NO_ACREDITADO")
    if config["modelo"] != [{"K_PROCES": "CREDITOS", "K_MODELO": "CREDIHOY_VAL_EMPLEADO", "I_ACTIVO": "Y", "K_IDRUTA": 10002896}]:
        raise ValueError("MODELO_PRODUCTIVO_CAMBIO")
    route = config["ruta"]
    expected = [(113, "A", "PK_CA_WF_RADICA_SOL_CREDITO.PR_RADICAR_SOLICITUD"),
                (3883, "A", "PK_CA_WF_REFERENCIA.PR_INSERTA_REFER_SOLICITUD")]
    prefix = 3 if legacy_cut else 2
    if legacy_cut:
        expected.append((6964, "H", "PK_WF_ACTIVIDAD_CRUD.PR_EJECUTAR_ACTIVIDAD_CRUD"))
    if len(route) < prefix or [(r["K_IDACTI"], r["I_EJECUC"], r["N_ACCION"]) for r in route[:prefix]] != expected:
        raise ValueError("BARRERA_HUMANA_6964_NO_ACREDITADA" if legacy_cut else "CONTRATO_RUTA_113_3883_CAMBIO")
    expected_modes = [(1, 'N', 'N'), (2, 'N', 'N')]
    if legacy_cut:
        expected_modes.append((3, 'D', 'N'))
    if [(r['Q_TRANSI'], r['I_CAPTUR'], r['I_AUTORI']) for r in route[:prefix]] != expected_modes:
        raise ValueError('CONFIGURACION_DE_EJECUCION_DEL_CORTE_CAMBIO')
    if any(r["K_SERIE"] in {x["K_SERIE"] for x in route[:prefix]} for r in config["caminos"]):
        raise ValueError("CAMINO_ALTERNATIVO_ANTES_DE_CORTE_REQUIERE_REVISION")
    required_packages = {"PK_WF_FLUJO", "PK_CA_WF_RADICA_SOL_CREDITO", "PK_CA_WF_REFERENCIA"}
    if ({r['OBJECT_NAME'] for r in config["packages"] if r['OBJECT_TYPE'] == 'PACKAGE'} != required_packages
            or any(r["STATUS"] != "VALID" for r in config["packages"])):
        raise ValueError("PACKAGES_NO_VALIDOS")
    return True


def require_personal_start_boundary(observed):
    """Allow only the documented native radication prefix, stopping at any human task."""
    require_configuration(observed)
    config=observed['configuracion']
    if config.get('rutasDerivadasIncompletas',False):raise ValueError('CD6_RUTAS_ANTES_DE_CORTE_INCOMPLETAS')
    allowed={113:'PK_CA_WF_RADICA_SOL_CREDITO.PR_RADICAR_SOLICITUD',
        3883:'PK_CA_WF_REFERENCIA.PR_INSERTA_REFER_SOLICITUD',
        6964:'PK_WF_ACTIVIDAD_CRUD.PR_EJECUTAR_ACTIVIDAD_CRUD',
        6783:EVALUATION_ACTION}
    routes={10002896:{'ruta':config['ruta'],'caminos':config['caminos']}}
    for row in config.get('rutasDerivadas',[]):
        if row['K_IDRUTA'] in routes:raise ValueError('CD6_RUTA_DUPLICADA')
        routes[row['K_IDRUTA']]=row
    cuts=[]
    def walk(route_id,visited):
        if route_id in visited or route_id not in routes:raise ValueError('CD6_RUTA_SIN_CORTE_ACREDITADO')
        route=routes[route_id];slots=set();has_cut=False
        for row in route['ruta']:
            if row['K_IDACTI'] is None:continue
            if row['I_EJECUC']=='H':
                cuts.append({'ruta':route_id,'actividad':row['K_IDACTI'],'slot':row['K_SERIE']});has_cut=True;break
            if (row['I_EJECUC']!='A' or row['K_IDACTI'] not in allowed
                    or row['N_ACCION']!=allowed[row['K_IDACTI']]
                    or row['I_AUTORI']!='N' or row['I_CAPTUR']!='N'):
                raise ValueError('CD6_AUTOMATICA_ANTES_DE_CORTE_NO_PERMITIDA')
            slots.add(row['K_SERIE'])
        paths=[p for p in route['caminos'] if p['K_SERIE'] in slots]
        if (not paths and not has_cut or any(p.get('K_IDACTI_CAM') is not None
                or p.get('K_IDRUTA_CAM') not in routes for p in paths)):
            raise ValueError('CD6_RUTA_SIN_CORTE_ACREDITADO')
        for path in paths:walk(path['K_IDRUTA_CAM'],visited|{route_id})
    walk(10002896,set())
    return {'paradasHumanas':cuts,'configuracionSha256':observed['sha256'],
        'autorizaAprobacion':False,'autorizaFirma':False,'autorizaDesembolso':False}


def matches_human_boundary(observed, tasks, *, allowed_human=None):
    """Match executed tasks against the recorded route, never execute its next activity."""
    config = observed['configuracion']
    if digest(canonical(config)) != observed['sha256'] or config.get('rutasDerivadasIncompletas',False):
        return False
    routes = {10002896: {'ruta':config['ruta'],'caminos':config['caminos']}}
    routes.update({row['K_IDRUTA']:row for row in config.get('rutasDerivadas',[])})
    actual = [(int(t['K_IDRUTA']),int(t['K_IDACTI']),t['I_ESTTAR'],t['I_EJECUC']) for t in tasks]
    pending = [(10002896,[],set())]
    while pending:
        route_id,prefix,visited = pending.pop()
        if route_id in visited or route_id not in routes or len(prefix) > len(actual):
            continue
        route = routes[route_id]
        for row in route['ruta']:
            if row['K_IDACTI'] is None:
                continue
            mode = row['I_EJECUC']
            if mode not in ('A','H'):
                break
            prefix = prefix + [(route_id,int(row['K_IDACTI']),'A' if mode == 'H' else 'E',mode)]
            if mode == 'H':
                if row['K_IDACTI'] in ((4965,6784,6964) if allowed_human is None else allowed_human) and prefix == actual:
                    return True
                break
        else:
            pending.extend((p['K_IDRUTA_CAM'],prefix,visited | {route_id})
                           for p in route['caminos'] if p['K_IDRUTA_CAM'] is not None)
    return False


def require_boundary(observed, certificates, *, now):
    require_configuration(observed, legacy_cut=True)
    for name, scope in (("efectos", "113_3883_HASTA_6964_SIN_EFECTOS_POSTERIORES"),
                        ("referencias3883", "REFERENCIAS_3883_ADMISIBLES")):
        cert = certificates.get(name) if isinstance(certificates, dict) else None
        if (not isinstance(cert, dict) or cert.get("configSha256") != observed["sha256"]
                or cert.get("alcance") != scope or not cert.get("referencia") or not cert.get("responsable")):
            raise ValueError("CERTIFICACION_INSTITUCIONAL_PENDIENTE_" + name.upper())
        try:
            begin, end = (datetime.fromisoformat(cert[key]) for key in ("desde", "hasta"))
            if begin.tzinfo is None or end.tzinfo is None or not begin <= now < end:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise ValueError("CERTIFICACION_INSTITUCIONAL_NO_VIGENTE") from None
    return True
