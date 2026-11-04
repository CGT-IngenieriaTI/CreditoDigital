"""Read-only disbursement composition; unknown deductions are not zero."""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from ..base import DTO, Hecho, EstadoCertificacion as EC
from ..aritmetica import precision_exacta


@dataclass(frozen=True, kw_only=True)
class PreparacionDesembolso(DTO):
    monto_bruto: Hecho[Decimal]
    saldo_liquidable_recogido: Hecho[Decimal]
    intereses_corrientes_iniciales: Hecho[Decimal]
    otros_descuentos: tuple[Hecho[Decimal], ...] = ()
    cobertura_descuentos: Hecho[bool]

    def __post_init__(self):
        super().__post_init__()
        for fact in (self.monto_bruto, self.saldo_liquidable_recogido,
                self.intereses_corrientes_iniciales, *self.otros_descuentos):
            if fact.disponible and fact.valor < 0:
                raise ValueError('DESCUENTO_O_BRUTO_NEGATIVO')
        refs = [(f.referencia_evidencia,f.ruta_origen,f.valor) for f in
            (self.saldo_liquidable_recogido,self.intereses_corrientes_iniciales,*self.otros_descuentos)
            if f.disponible and f.valor>0]
        if len(refs) != len(set(refs)):
            raise ValueError('DESCUENTO_DUPLICADO')

    def proyectar(self):
        amounts = (self.monto_bruto, self.saldo_liquidable_recogido,
            self.intereses_corrientes_iniciales, *self.otros_descuentos)
        def supported(h):
            return (h.disponible and h.estado_certificacion == EC.CERTIFICADO
                and bool(h.fuente and h.ruta_origen and h.referencia_evidencia and h.fecha_corte))
        complete = all(supported(h) for h in amounts) and supported(self.cobertura_descuentos)
        complete = complete and self.cobertura_descuentos.valor is True
        complete = complete and len({h.fecha_corte for h in (*amounts, self.cobertura_descuentos)}) == 1
        net = None
        if complete:
            with localcontext() as ctx:
                ctx.prec = precision_exacta(*(h.valor for h in amounts))
                net = amounts[0].valor - sum((h.valor for h in amounts[1:]), Decimal(0))
            if net < 0:
                raise ValueError('DESCUENTOS_SUPERAN_MONTO_BRUTO')
        return {'versionContrato': 'credihoy-preparacion-desembolso-v1',
            'componentes': self.a_dict(), 'valorNeto': format(net, 'f') if net is not None else None,
            'estado': 'COMPOSICION_CONCILIADA_NO_EJECUTADA' if complete else 'COMPONENTES_PENDIENTES',
            'esOfertaContractual': False, 'autorizaDesembolso': False,
            'compensacionEjecutada': False, 'liquidacionEjecutada': False}

    def conciliar_fuentes(self, evidencia, *, solicitud_id, flujo, sc, titular_referencia,
            pagare, ahora, evidencia_pago=None):
        """Separate accounting facts from cash settlement; never execute a task.

        CA106 amounts are kept as reported. A concept code such as NT/DS/CA is
        not by itself authority to treat its credit as net cash or compensation.
        """
        from datetime import datetime
        import hashlib,json
        historical=evidencia.get('versionContrato')=='credihoy-linix-fuentes-historicas-v1'
        scope={'solicitudId':None if solicitud_id is None else str(solicitud_id),
            'K_FLUJO':None if flujo is None else str(flujo),'SC':str(sc),
            'titularReferencia':titular_referencia}
        try:
            digest=lambda v:hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            observed=datetime.fromisoformat(evidencia['observadoEn'])
            if (evidencia['versionContrato'] not in {'credihoy-linix-fuentes-financieras-v1','credihoy-linix-fuentes-historicas-v1'}
                    or (historical and any(evidencia[k] is not None for k in ('solicitudId','intencionId','K_FLUJO')))
                    or (not historical and (solicitud_id is None or flujo is None))
                    or evidencia['fuente']!='LINIX' or any(evidencia[k]!=v for k,v in scope.items())
                    or evidencia['sha256']!=digest({k:v for k,v in evidencia.items() if k!='sha256'})
                    or observed.utcoffset() is None or not 0<=(ahora-observed).total_seconds()<=300
                    or evidencia['START']!=0 or type(evidencia['START']) is not int
                    or evidencia['autorizaEjecucionFinanciera'] is not False):raise ValueError()
            rows=evidencia['filas'];applications=rows['CA090MGSOLCRED']
            if len(applications)!=1:raise ValueError()
            s=applications[0]
            if (str(s['K_NUMDOC'])!=str(sc) or s['K_TIPODR']!='SC' or str(s['K_MODALI'])!='111'
                    or s['K_DESTIN']!='CFA' or str(s['A_OBLIGA'])!=str(pagare)
                    or s['I_LIQUID'] not in {'Y','N'}):raise ValueError()
            gross=Decimal(str(s['V_MONTO']))
            if (not gross.is_finite() or gross<=0 or
                    (self.monto_bruto.disponible and gross!=self.monto_bruto.valor)):raise ValueError()
            for table in ('CA106MDLIQUIDA','CA107TCONTLIQU','GR028G0000'):
                if type(rows[table]) is not list or len(rows[table])>100:raise ValueError()
            liquidated=s['I_LIQUID']=='Y' and bool(s['F_LIQUID'])
            if liquidated:
                date=datetime.fromisoformat(s['F_LIQUID']).date()
                if date>observed.date():raise ValueError()
            def key(row,fields):
                values=[row.get(k) for k in fields]
                return tuple(str(v) for v in values) if all(v is not None and str(v) for v in values) else None
            source_key=key(s,('K_ANO','K_SUCURS_MOV','K_TIPDOC_MOV','K_NUMDOC_MOV'))
            ledger=rows['GR028G0000']
            def live_voucher(voucher,expected):
                return (expected is not None and key(voucher,('K_ANO','K_SUCURS','K_TIPDOC','K_NUMDOC'))==expected
                    and voucher.get('I_ANULAD')=='N' and bool(voucher.get('F_MOVIMI'))
                    and datetime.fromisoformat(voucher['F_MOVIMI']).date()<=observed.date())
            posted=liquidated and len(ledger)==1 and live_voucher(ledger[0],source_key)
            config=rows['CA107TCONTLIQU'];order='DESCONOCIDA';compatible=None
            if len(config)==1 and (str(config[0]['K_CONLIQ'])==str(s['K_CONLIQ'])
                    and config[0]['I_ACTIVO']=='Y' and config[0]['I_ORDPAG'] in {'Y','N'}):
                indicator=config[0]['I_ORDPAG']
                if posted and str(config[0]['K_TIPMOV'])!=str(ledger[0]['K_TIPMOV']):
                    compatible=False
                else:
                    compatible=True
                    order='PENDIENTE' if indicator=='Y' else 'NO_APLICA'
            details=rows['CA106MDLIQUIDA'];binding=evidencia['vinculoCA106']
            if details and (not binding or binding.get('origen')!='ALL_CONSTRAINTS_ALL_CONS_COLUMNS'
                    or not binding.get('restriccion')):raise ValueError()
            if len({str(d['K_SERIE']) for d in details})!=len(details):raise ValueError()
            references={key(d,('K_ANO_OPG','K_SUCURS_OPG','K_TIPDOC_OPG','K_NUMDOC_OPG')) for d in details}
            references.discard(None)
            orders=evidencia['comprobantesOrden']
            if type(orders) is not list or len(orders)>100:raise ValueError()
            if order=='PENDIENTE' and references and all(
                    sum(live_voucher(v,r) for v in orders)==1 for r in references):order='GENERADA'
            elif order=='NO_APLICA' and (references or orders):compatible=False;order='CONTRADICTORIA'
            pickups=[d for d in details if d['I_RECOGE']=='R']
            previous=evidencia['obligacionesReferenciadas']
            matches=[]
            for d in pickups:
                candidates=[p for p in previous if str(p['A_TIPODR'])==str(d['A_TIPODR_REF'])
                    and str(p['A_OBLIGA'])==str(d['A_OBLIGA_REF'])]
                if len(candidates)!=1:continue
                p=candidates[0]
                if p.get('V_SALDO') is None:continue
                balance=Decimal(str(p['V_SALDO']))
                if (liquidated and str(p['K_IDTERC'])==str(s['K_IDTERC']) and str(p['K_MODALI'])=='111'
                        and str(p['K_NUMDOC'])!=str(sc) and str(p['A_OBLIGA'])!=str(pagare)
                        and p['I_CANCEL']=='Y' and p['F_CANCEL'] and balance.is_finite() and balance==0
                        and datetime.fromisoformat(p['F_CANCEL']).date()==datetime.fromisoformat(s['F_LIQUID']).date()):
                    matches.append((d,p))
            balance=self.saldo_liquidable_recogido
            required=(balance.valor>0 if balance.disponible and balance.estado_certificacion==EC.CERTIFICADO else None)
            collection=False;payment=None
            # Optional cash proof must come from the existing settlement reader,
            # never be manufactured from CA090, a voucher or an order of payment.
            if evidencia_pago is not None:
                if (required is None or not posted or compatible is not True
                        or order not in {'GENERADA','NO_APLICA'} or evidencia['pendientesFuente']
                        or (required and (len(pickups)!=1 or len(matches)!=1))
                        or (not required and pickups)):raise ValueError()
                if required:
                    d,p=matches[0]
                    amount=Decimal(str(d['V_CRPESO']))
                    movement=[r for r in evidencia_pago['movimientos'] if r['tipo']=='RECOGIDA_111']
                    if (not amount.is_finite() or amount!=self.saldo_liquidable_recogido.valor
                            or len(movement)!=1 or movement[0]['obligacionAnterior']!=str(p['A_OBLIGA'])):raise ValueError()
                payment=self.conciliar(evidencia_pago,solicitud_id=solicitud_id,flujo=flujo,sc=sc,
                    titular_referencia=titular_referencia,pagare=pagare,ahora=ahora)
                collection=required
            pending=list(evidencia['pendientesFuente'])
            if not liquidated:pending.append('LIQUIDACION_NO_ACREDITADA')
            if not posted:pending.append('COMPROBANTE_CONTABLE_NO_ACREDITADO')
            if compatible is not True:pending.append('CONFIGURACION_ORDEN_NO_ACREDITADA')
            if order=='PENDIENTE':pending.append('ORDEN_PAGO_PENDIENTE')
            if required is None:pending.append('RECOGIDA_PENDIENTE_DETERMINAR')
            if required and not collection:pending.append('RECOGIDA_NO_COMPENSADA_ACREDITADAMENTE')
            if payment is None:pending.append('NETO_Y_DESEMBOLSO_SIN_MOVIMIENTO_EFECTIVO')
            result={'versionContrato':'credihoy-conciliacion-financiera-v1','fuente':'LINIX',**scope,
                'pagare':str(pagare),'intencionId':evidencia['intencionId'],
                'observadoEn':evidencia['observadoEn'],'fuentesSha256':evidencia['sha256'],
                'liquidacion':{'acreditada':liquidated,'fecha':s['F_LIQUID']},
                'contabilizacion':{'acreditada':bool(posted),'clave':list(source_key) if source_key else None},
                'ordenPago':{'estado':order,'configuracionCompatible':compatible},
                'recogida':{'requerida':required,'referenciada':bool(pickups),'cancelacionesCoincidentes':len(matches),
                    'compensacionAcreditada':collection},
                'neto':{'acreditado':payment is not None,'valor':payment['valorNeto'] if payment else None,
                    'valorPreparado':self.proyectar()['valorNeto']},
                'desembolso':payment['desembolso'] if payment else {'acreditado':False,'referencia':None,'cantidad':0},
                'conciliacionDesembolso':payment,'pendientes':pending,
                'autorizaDesembolso':False,'transporteHabilitado':False}
            result['sha256']=digest(result)
            return result
        except (KeyError,ValueError,TypeError,AttributeError,ArithmeticError):
            raise ValueError('FUENTES_FINANCIERAS_NO_CONCILIADAS') from None

    def conciliar(self, evidencia, *, solicitud_id, flujo, sc, titular_referencia, pagare, ahora):
        """Reconcile native movements; this does not calculate or execute a payment."""
        from datetime import datetime
        import hashlib,json
        projection=self.proyectar()
        if projection['valorNeto'] is None:raise ValueError('NETO_SIN_COMPONENTES_ACREDITADOS')
        scope={'solicitudId':str(solicitud_id),'K_FLUJO':str(flujo),'SC':str(sc),
            'titularReferencia':titular_referencia,'pagare':str(pagare)}
        try:
            observed=datetime.fromisoformat(evidencia['observadoEn'])
            if (any(evidencia[k]!=v for k,v in scope.items()) or evidencia['fuente']!='LINIX'
                    or evidencia['completa'] is not True or not evidencia['consultaReferencia']
                    or observed.utcoffset() is None or not 0<=(ahora-observed).total_seconds()<=300
                    or evidencia['liquidacion']['liquidada'] is not True):raise ValueError()
            rows=evidencia['movimientos']
            if type(rows) is not list or not 1<=len(rows)<=2:raise ValueError()
            for row in rows:
                movement_date=datetime.fromisoformat(row['fecha'])
                if (type(row) is not dict or row['tipo'] not in {'DESEMBOLSO','RECOGIDA_111'}
                        or not row['referencia'] or row['ejecutado'] is not True or row['reversado'] is not False
                        or movement_date.utcoffset() is None or movement_date>observed
                        or any(row[k]!=v for k,v in scope.items())
                        or type(row['valor']) is not str or not Decimal(row['valor']).is_finite()
                        or Decimal(row['valor'])<=0):raise ValueError()
            if len({r['referencia'] for r in rows})!=len(rows):raise ValueError()
            payments=[r for r in rows if r['tipo']=='DESEMBOLSO']
            pickups=[r for r in rows if r['tipo']=='RECOGIDA_111']
            if len(payments)!=1 or Decimal(payments[0]['valor'])!=Decimal(projection['valorNeto']):raise ValueError()
            amount=self.saldo_liquidable_recogido.valor
            if amount>0:
                if (len(pickups)!=1 or Decimal(pickups[0]['valor'])!=amount
                        or pickups[0]['modalidadAnterior']!='111'
                        or not pickups[0]['obligacionAnterior']
                        or pickups[0]['obligacionAnterior']==str(pagare)):
                    raise ValueError()
            elif pickups:raise ValueError()
        except (KeyError,TypeError,ValueError,AttributeError,ArithmeticError):
            raise ValueError('MOVIMIENTOS_LINIX_NO_CONCILIADOS') from None
        body={'versionContrato':'credihoy-conciliacion-desembolso-v1','fuente':'LINIX',**scope,
            'observadoEn':evidencia['observadoEn'],'consultaReferencia':evidencia['consultaReferencia'],
            'liquidacion':{'liquidada':True},'valorNeto':projection['valorNeto'],
            'desembolso':{'acreditado':True,'referencia':payments[0]['referencia'],'cantidad':1},
            'fechaDesembolso':payments[0]['fecha'],
            'recogidaConciliada':True,'recogidaRequerida':amount>0,
            'composicionSha256':hashlib.sha256(json.dumps(projection,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'autorizaDesembolso':False,'transporteHabilitado':False}
        body['sha256']=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return body


def conciliar_abono_ah135(evidencia,*,ahora):
    """Reconcile reported MT/NT and credit legs, not a disbursement calculator.

    A debit or equal credit elsewhere in the same account/cut is conservatively
    ambiguous until native reversal evidence resolves it. No global absence of
    reversals is asserted beyond this bounded, complete SELECT observation.
    """
    from datetime import datetime
    from hashlib import sha256
    import json
    from zoneinfo import ZoneInfo
    try:
        digest=lambda v:sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if evidencia['sha256']!=digest({k:v for k,v in evidencia.items() if k!='sha256'}):raise ValueError()
        rows=evidencia['filas'];s=rows['CA090MGSOLCRED'][0];details=rows['CA106MDLIQUIDA']
        observed=datetime.fromisoformat(evidencia['observadoEn'])
        def number(v):
            if v is None:return Decimal(0)  # absent credit leg in a COMPLETE native detail row
            if type(v) not in {str,int,Decimal}:raise ValueError()
            n=Decimal(v)
            if not n.is_finite() or n<0:raise ValueError()
            return n
        def leg(row,prefix):
            parts=sum((number(row.get(k)) for k in ('V_'+prefix+'KAPI','V_'+prefix+'INTE','V_'+prefix+'MORA')),Decimal(0))
            total=row.get('V_'+prefix+'PESO')
            if total is not None:
                total=number(total)
                if parts and parts!=total:raise ValueError()
                return total
            return parts
        if not details or len({str(d['K_SERIE']) for d in details})!=len(details):raise ValueError()
        codes={d['I_LIQCON'] for d in details}
        if codes-{'MT','NT','CA','CO','DS'}:raise ValueError()
        mounts=[d for d in details if d['I_LIQCON']=='MT'];nets=[d for d in details if d['I_LIQCON']=='NT']
        if len(mounts)!=1 or len(nets)!=1:raise ValueError()
        gross=leg(mounts[0],'DB');net=leg(nets[0],'CR')
        if gross!=number(s['V_MONTO']) or leg(mounts[0],'CR') or leg(nets[0],'DB') or net<=0:raise ValueError()
        applications=[d for d in details if d['I_LIQCON'] not in {'MT','NT'}]
        if any(leg(d,'DB') for d in applications):raise ValueError()
        applied=sum((leg(d,'CR') for d in applications),Decimal(0))
        if gross-applied!=net:raise ValueError()
        pickups=[d for d in applications if d['I_LIQCON']=='CA' and d['I_RECOGE']=='R']
        if any(d['I_LIQCON']=='CA' and d not in pickups for d in applications):raise ValueError()
        pickup_amount=sum((leg(d,'CR') for d in pickups),Decimal(0))
        def fact(value,path):
            return Hecho(valor=value,disponible=True,fuente='LINIX.CA106MDLIQUIDA',
                ruta_origen=path,referencia_evidencia=evidencia['sha256'],fecha_consulta=observed,
                fecha_corte=observed.astimezone(ZoneInfo('America/Bogota')).date(),estado_certificacion=EC.CERTIFICADO)
        dto=PreparacionDesembolso(monto_bruto=fact(gross,'MT'),
            saldo_liquidable_recogido=fact(pickup_amount,'CA_REFERENCIADA'),
            intereses_corrientes_iniciales=Hecho(fuente='NO_INFERIDO_DE_CO_DS'),
            cobertura_descuentos=Hecho(fuente='CONCEPTOS_NATIVOS_SIN_REETIQUETAR'))
        historical=evidencia['versionContrato']=='credihoy-linix-fuentes-historicas-v1'
        result=dto.conciliar_fuentes(evidencia,solicitud_id=evidencia['solicitudId'],
            flujo=evidencia['K_FLUJO'],sc=evidencia['SC'],titular_referencia=evidencia['titularReferencia'],
            pagare=s['A_OBLIGA'],ahora=ahora)
        if historical:result['versionContrato']='credihoy-conciliacion-financiera-historico-v1'
        result['aplicacionesCA106']={'monto':format(gross,'f'),'aplicado':format(applied,'f'),'netoNT':format(net,'f'),
            'conceptos':[{'serie':str(d['K_SERIE']),'concepto':d['I_LIQCON'],'credito':format(leg(d,'CR'),'f')} for d in applications]}
        pending=result['pendientes']
        savings=rows.get('AH135D2026',[]);cut=evidencia.get('ahorrosCorte',[])
        voucher=rows['GR028G0000'][0] if result['contabilizacion']['acreditada'] else None
        valid=[]
        def local_date(v):
            t=datetime.fromisoformat(v)
            return (t.astimezone(ZoneInfo('America/Bogota')) if t.utcoffset() is not None else t).date()
        def credit_matches(r):
            return (str(r['K_IDTERC'])==str(s['K_IDTERC']) and str(r['K_CODCON'])=='21050501'
                and r['I_CONTAB']=='Y' and number(r['V_DBPESO'])==0 and number(r['V_CRPESO'])==net
                and r.get('EVIDENCIA_FILA') and r.get('K_CUENTA') is not None
                and local_date(r['F_MOVIMI'])==local_date(s['F_LIQUID']))
        if voucher:
            for r in savings:
                if (credit_matches(r) and all(str(r[k])==str(voucher[k]) for k in
                        ('K_PERANO','K_SUCURS','K_TIPDOC','K_NUMDOC'))):valid.append(r)
        unique=(len(valid)==1 and len(savings)==1 and evidencia.get('ahorrosCorteCompleto') is True
            and sum(r.get('EVIDENCIA_FILA')==valid[0]['EVIDENCIA_FILA'] for r in cut)==1)
        ambiguous=False;other_debits=0;other_equal_credits=0;other_rows=0
        if unique:
            own=valid[0]
            for r in cut:
                if str(r['K_CUENTA'])!=str(own['K_CUENTA']):raise ValueError()
                if r['EVIDENCIA_FILA']!=own['EVIDENCIA_FILA']:
                    other_rows+=1
                    other_debits+=number(r['V_DBPESO'])>0
                    other_equal_credits+=number(r['V_CRPESO'])==net
            ambiguous=other_debits>0 or other_equal_credits>0
        compensated=not pickups
        if pickups:
            compensated=len(pickups)==1
            for d in pickups:
                previous=[p for p in evidencia['obligacionesReferenciadas']
                    if str(p['A_TIPODR'])==str(d['A_TIPODR_REF']) and str(p['A_OBLIGA'])==str(d['A_OBLIGA_REF'])]
                compensated=compensated and len(previous)==1
                if len(previous)!=1:continue
                p=previous[0]
                compensated=compensated and (str(p['K_MODALI'])=='111' and str(p['K_IDTERC'])==str(s['K_IDTERC'])
                    and str(p['K_NUMDOC'])!=str(s['K_NUMDOC']) and str(p['A_OBLIGA'])!=str(s['A_OBLIGA'])
                    and p['I_CANCEL']=='Y' and p['V_SALDO'] is not None
                    and number(p['V_SALDO'])==0 and p['F_CANCEL'] and local_date(p['F_CANCEL'])==local_date(s['F_LIQUID']))
            compensated=bool(compensated and pickup_amount>0)
        if not unique:pending.append('ABONO_AH135_NO_UNICO_O_CORTE_INCOMPLETO')
        if ambiguous:pending.append('MOVIMIENTOS_DE_CUENTA_SIN_CORRELACION_CON_EL_COMPROBANTE')
        paid=(unique and not ambiguous and not evidencia['pendientesFuente'] and compensated
            and result['liquidacion']['acreditada'] and result['contabilizacion']['acreditada']
            and result['ordenPago']['estado']=='NO_APLICA' and result['ordenPago']['configuracionCompatible'] is True
            and len(rows['CA107TCONTLIQU'])==1 and rows['CA107TCONTLIQU'][0].get('I_CONLIQ')=='AH')
        result['controlAbono']={'unicoAlCorte':bool(unique),'sinReversoObservableAlCorte':bool(unique and not ambiguous),
            'alcance':'COMPROBANTE_VIGENTE_Y_MOVIMIENTOS_DE_LA_CUENTA_DESDE_LIQUIDACION_HASTA_CORTE',
            'fechaCorte':evidencia['observadoEn'],'otrosMovimientosCuenta':other_rows if unique else None,
            'debitosSinCorrelacion':other_debits if unique else None,'otrosCreditosEquivalentes':other_equal_credits if unique else None,
            'reversoConfirmado':None if ambiguous or not unique else False}
        # Preserve observed credit/cancellation independently of final closure.
        # Different documents on the same date are not proven later reversals.
        result['abonoRegistrado']={'acreditado':bool(unique),'fuente':'LINIX.AH135D2026',
            'valor':format(net,'f') if unique else None,'unicoEnComprobante':bool(unique)}
        result['netoObservado']={'coincideNT':bool(unique),'valor':format(net,'f') if unique else None}
        result['recogida']['cancelacion111Acreditada']=bool(pickups and compensated)
        # Quantified CA credit + bound prior cancellation + live accounting
        # voucher prove this stage independently of subsequent cash movements.
        result['recogida']['compensacionAcreditada']=bool(pickups and compensated
            and result['liquidacion']['acreditada'] and result['contabilizacion']['acreditada'])
        result['recogida']['importeAplicadoCartera']=format(pickup_amount,'f') if result['recogida']['compensacionAcreditada'] else None
        if result['recogida']['compensacionAcreditada']:
            result['pendientes']=[p for p in pending if p!='RECOGIDA_NO_COMPENSADA_ACREDITADAMENTE']
        if unique and not paid:
            result['pendientes']=[p for p in result['pendientes'] if p!='NETO_Y_DESEMBOLSO_SIN_MOVIMIENTO_EFECTIVO']
            result['pendientes'].append('CIERRE_DESEMBOLSO_PENDIENTE_DE_CONCILIACION')
        if paid:
            reference='AH135D2026:'+valid[0]['EVIDENCIA_FILA']
            result['recogida']['compensacionAcreditada']=bool(pickups)
            result['neto']={'acreditado':True,'valor':format(net,'f'),'valorPreparado':None,'fuente':'CA106_NT_AH135'}
            result['desembolso']={'acreditado':True,'referencia':reference,'cantidad':1}
            result['pendientes']=[]
            receipt={'versionContrato':'credihoy-conciliacion-desembolso-v1','fuente':'LINIX',
                **{k:result[k] for k in ('solicitudId','K_FLUJO','SC','titularReferencia','pagare')},
                'observadoEn':evidencia['observadoEn'],'consultaReferencia':evidencia['sha256'],
                'liquidacion':{'liquidada':True},'valorNeto':format(net,'f'),'desembolso':result['desembolso'],
                'fechaDesembolso':valid[0]['F_MOVIMI'],'recogidaConciliada':True,'recogidaRequerida':bool(pickups),
                'composicionSha256':digest(result['aplicacionesCA106']),'autorizaDesembolso':False,'transporteHabilitado':False}
            receipt['sha256']=digest(receipt);result['conciliacionDesembolso']=receipt
        result['sha256']=digest({k:v for k,v in result.items() if k!='sha256'})
        return result
    except (KeyError,TypeError,ValueError,AttributeError,ArithmeticError):
        raise ValueError('ABONO_AH135_NO_CONCILIADO') from None


def precondiciones_desembolso(solicitud_id, *, clasificacion, aprobacion, condiciones,
        documentos_autentic, pagare_linpag, liquidacion, cuenta, journal, ahora=None,decision_estamento=None):
    """Checks ready for intercepted E2E. Never releases a payment or a worker."""
    from .estamentos import ORDINARIO,FAMILIAR,COLABORADOR
    import re
    from datetime import datetime,timezone
    from .estamentos import VERSION as EST_VERSION
    ahora=ahora or datetime.now(timezone.utc)
    sources=((aprobacion,'LINIX'),(condiciones,'ACEPTACION_TITULAR'),
        (liquidacion,'LINIX'),(cuenta,'CUENTA_INSTITUCIONAL'))
    def bound(e,source):
        return (type(e) is dict and e.get('solicitudId')==str(solicitud_id)
            and e.get('fuente')==source and e.get('verificado') is True
            and bool(e.get('referencia')) and re.fullmatch(r'[a-f0-9]{64}',str(e.get('sha256',''))))
    pending=[]
    state=clasificacion.get('estado') if type(clasificacion) is dict else None
    if type(clasificacion) is not dict or clasificacion.get('solicitudId')!=str(solicitud_id) or state not in {ORDINARIO,FAMILIAR,COLABORADOR}:
        pending.append('ESTAMENTOS_NO_ORDINARIOS_O_NO_ACREDITADOS')
    else:
        try:
            when=datetime.fromisoformat(clasificacion['fechaConsulta'])
            if (clasificacion['version']!=EST_VERSION or clasificacion['autorizaAutomatizacion'] is not (state==ORDINARIO)
                    or not clasificacion['consultaReferencia'] or when.utcoffset() is None
                    or not 0<=(ahora-when).total_seconds()<=300):raise ValueError()
        except (KeyError,TypeError,ValueError):pending.append('CLASIFICACION_NO_VIGENTE_O_NO_ACREDITADA')
    if state in {FAMILIAR,COLABORADOR}:
        try:
            route=10002991 if state==FAMILIAR else 10002856
            d=decision_estamento;selector=d['seleccion121']
            human=lambda user:type(user) is str and bool(re.fullmatch('[A-Za-z0-9_.$#-]{1,30}',user)) and user.upper() not in {'CGTE','SYS','SYSTEM','SERVIC'}
            if (not bound(d,'LINIX') or d['ruta']!=route or d['estadoTarea']!='E'
                    or d['respuesta'] not in {'APRUEBA','APROBAR'} or not human(d['usuario'])
                    or not str(d['K_SERIE']).isdigit() or int(d['K_SERIE'])<=0
                    or any(not d[k] or d[k]!=aprobacion[k] for k in ('SC','K_FLUJO'))
                    or d.get('intervencionHumanaAcreditada') is not True
                    or selector['actividad']!=121 or selector['ruta']!=10002852
                    or selector['estado']!='E' or selector['destino']!=route
                    or not human(selector['usuario']) or not str(selector['K_SERIE']).isdigit()
                    or int(selector['K_SERIE'])<=0 or selector['K_SERIE']==d['K_SERIE']):raise ValueError()
        except (KeyError,TypeError,ValueError,AttributeError):pending.append('DECISION_HUMANA_ESTAMENTO_NO_ACREDITADA')
    for value,source in sources:
        if not bound(value,source):pending.append(source+'_NO_ACREDITADO')
    if type(aprobacion) is not dict or aprobacion.get('aprobada') is not True:pending.append('APROBACION_NO_REGISTRADA')
    # Same field as the institutional CA090 projection. A valid receipt may
    # truthfully report liquidation still pending; that is not readiness to pay.
    if type(liquidacion) is not dict or liquidacion.get('liquidada') is not True:
        pending.append('LIQUIDACION_NO_REGISTRADA')
    if type(cuenta) is not dict or cuenta.get('titularVerificado') is not True or cuenta.get('disponibleLegalmente') is not True:
        pending.append('CUENTA_NO_DISPONIBLE_LEGALMENTE')
    if (type(documentos_autentic) is not list or any(type(d) is not dict for d in documentos_autentic)
            or {d.get('codigo') for d in documentos_autentic}!={'COLO-FO.001','COLO-FO.023'}
            or len(documentos_autentic)!=2 or any(not bound(d,'AUTENTIC') or not d.get('eventoFirmante')
                or d.get('firmado') is not True or d.get('integridadVerificada') is not True for d in documentos_autentic)):
        pending.append('DOCUMENTOS_CANAL_SIN_FIRMA_AUTENTICA')
    if (not bound(pagare_linpag,'LINPAG') or pagare_linpag.get('firmado') is not True
            or not pagare_linpag.get('eventoFirmante') or pagare_linpag.get('integridadVerificada') is not True):
        pending.append('PAGARE_SIN_FIRMA_LINPAG')
    if (type(journal) is not dict or journal.get('solicitudId')!=str(solicitud_id)
            or journal.get('resultadoIncierto') is not False or journal.get('desembolsosPrevios')!=0
            or type(journal.get('desembolsosPrevios')) is not int
            or not journal.get('claveIdempotencia')):pending.append('JOURNAL_REQUIERE_CONCILIACION')
    return {'solicitudId':str(solicitud_id),'estado':'PRECONDICIONES_ACREDITADAS_SIN_EJECUCION' if not pending else 'BLOQUEADO',
        'pendientes':pending,'autorizaDesembolso':False,'transporteHabilitado':False}
