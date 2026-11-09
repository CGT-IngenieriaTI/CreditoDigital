const POLITICA_URL='https://www.congente.coop/images/Balancesocial/GER-PO.085-Politica_tratamiento_datos_personales.pdf';
import {validarSimulacionLocal,type EscenarioLocal,type SimulacionLocal} from './simulacion';
export interface InicioPublico { avisoVersion:string; avisoTexto:string; botSitekey:string; csrfToken:string; canales:('SMS'|'EMAIL')[];
  politica:{codigo:'GER-PO.085';version:'03';url:string};duracionSesionSegundos:number;duracionOtpSegundos:number;
  proteccion?:'OPERADOR_RESTRINGIDO';controlAcceso?:string }
export interface IntentoPublico { intento:string;estado:'PREPARANDO'|'CONTACTO_DISPONIBLE'|'ENVIO_PENDIENTE'|'CODIGO_ENVIADO';
  contactoEnmascarado:string|null;canal:'SMS'|'EMAIL'|null;expiraAt:string|null }
export interface PerfilPersonal {correoRegistrado:string|null;correoVerificado:boolean;contactoEnmascarado:string|null;fuente:'COTEJO_INSTITUCIONAL_AUTENTICADO'}
export type SesionPublica = { autenticado:false } | { autenticado:true; nombre:string|null; expiraAt:string;perfil?:PerfilPersonal;
  sesionReferencia:string;
  acciones:{ seguimiento:boolean; solicitar:boolean; simular:boolean; documentos:false; autorizar?:boolean;centralesOtp?:boolean;simuladorLocal?:boolean;radicar?:boolean } };
export type PeticionRadicacion={operacion:'radicacion';solicitudId:string}&(
  {accion:'consultar'|'conciliar'}|{accion:'preparar';diaPagoPropuesto:number}|
  {accion:'confirmar';presentacion:string;aceptaRadicar:true});
export interface RadicacionTitular {versionContrato:'credihoy-radicacion-personal-v1';solicitudId:string;intencionId:string;
  estado:'SIN_INTENCION'|'PREPARADA'|'EXECUTING'|'INCIERTA'|'RADICADA'|'BLOQUEADA';codigo:string|null;
  puedeConfirmar:boolean;presentacion:string|null;K_FLUJO:string|null;SC:string|null;actividadesActivas:{actividad:number}[];
  decisionInstitucional:'PENDIENTE';firmaContractual:'PENDIENTE';autorizaFirma:false;autorizaDesembolso:false;
  calendarioPropuesto?:{diaPago:number;primerVencimiento:string;versionRegla:string;tipoBase:'PROYECTADO';definitivo:false}}
export function validarRadicacionTitular(raw:unknown,sid:string):RadicacionTitular {
  const p=obj(raw),identifier=(v:unknown)=>v===null||typeof v==='string'&&/^[1-9][0-9]{0,19}$/.test(v);
  if(Object.keys(p).filter(k=>k!=='calendarioPropuesto').sort().join(',')!=='K_FLUJO,SC,actividadesActivas,autorizaDesembolso,autorizaFirma,codigo,decisionInstitucional,estado,firmaContractual,intencionId,presentacion,puedeConfirmar,solicitudId,versionContrato'
    ||p.versionContrato!=='credihoy-radicacion-personal-v1'||p.solicitudId!==sid||!uuid.test(sid)||!uuid.test(text(p.intencionId))
    ||!['SIN_INTENCION','PREPARADA','EXECUTING','INCIERTA','RADICADA','BLOQUEADA'].includes(text(p.estado))
    ||p.codigo!==null&&(typeof p.codigo!=='string'||!/(^[A-Z][A-Z0-9_]{1,120}$)/.test(p.codigo))
    ||typeof p.puedeConfirmar!=='boolean'||(p.puedeConfirmar?(p.estado!=='PREPARADA'||typeof p.presentacion!=='string'||!p.presentacion||p.presentacion.length>8192||p.calendarioPropuesto===undefined):p.presentacion!==null)
    ||!identifier(p.K_FLUJO)||!identifier(p.SC)||p.SC!==null&&p.K_FLUJO===null
    ||!Array.isArray(p.actividadesActivas)||p.actividadesActivas.length>20||p.actividadesActivas.some(v=>{
      const r=obj(v);return Object.keys(r).join(',')!=='actividad'||!Number.isSafeInteger(r.actividad)||Number(r.actividad)<=0;})
    ||p.decisionInstitucional!=='PENDIENTE'||p.firmaContractual!=='PENDIENTE'||p.autorizaFirma!==false||p.autorizaDesembolso!==false)fail();
  if(p.calendarioPropuesto!==undefined){const c=obj(p.calendarioPropuesto),date=String(c.primerVencimiento);
    if(Object.keys(c).sort().join(',')!=='definitivo,diaPago,primerVencimiento,tipoBase,versionRegla'
      ||!Number.isInteger(c.diaPago)||Number(c.diaPago)<1||Number(c.diaPago)>20
      ||c.versionRegla!=='CREDIHOY_DIA_PAGO_30_CALENDARIO_20261028_V2'||c.tipoBase!=='PROYECTADO'||c.definitivo!==false
      ||!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(Date.parse(date+'T12:00:00Z'))
      ||new Date(date+'T12:00:00Z').toISOString().slice(0,10)!==date||Number(date.slice(-2))!==c.diaPago)fail();}
  return p as unknown as RadicacionTitular;
}
export type PeticionAutorizacion={operacion:'autorizacion';solicitudId:string}&(
  {accion:'consultar'}|{accion:'presentar';clave:string;campos:{nombre:string;ciudad:string}}|
  {accion:'aceptar';clave:string;presentacion:string;acepta:true}|{accion:'ver_firma';clave:string}|
  {accion:'verificar_firmante';clave:string;presentacion:string;autorizaFirma:true}|
  {accion:'consultar_complemento'}|{accion:'guardar_complemento';operacionId:string;version:number;
    datos:{periodicidadPago:'MENSUAL';recogerCredihoy:boolean;obligacionRecoger:string|null};confirma:true}|
  {accion:'centrales_consultar'}|{accion:'centrales_presentar';operacionId:string}|
  {accion:'centrales_enviar';operacionId:string;presentacion:string;acepta:true}|
  {accion:'centrales_comprobar';operacionId:string;presentacion:string;comprobacionId:string;codigo:string});
export interface CentralesPersonal {estado:'SIN_ACEPTACION'|'PENDIENTE_ACEPTACION'|'PENDIENTE_OTP'|'ENVIADO'|'OTP_CONFIRMADA'|'RECHAZADA'|'INDETERMINADA';
  operacionId:string|null;proposito:'CONSULTA_CENTRALES';metodo:'OTP';textoVersion:'COLO-FO.023:v10';textoSha256:string;
  pdfSha256:string;presentacion:string|null;confirmadoEn:string|null;verificacionReferencia:string|null;vigenteHasta:string|null}
export interface ComplementoPersonalGuardado {version:number;datos:null|{
  periodicidadPago:'MENSUAL';recogerCredihoy:boolean;obligacionRecoger:string|null};confirmadoEn:string|null;
  fuente:'ELECCION_PERSONAL_COMPLEMENTO'|'ELECCION_PERSONAL_ENVIO'|null}
export interface AutorizacionTitular {versionContrato:'credihoy-autorizacion-titular-v1';solicitudId:string;
  estado:'PENDIENTE_ACEPTACION_PERSONAL'|'PENDIENTE_FIRMA_AUTENTIC'|'AUTORIZACION_AUSENTE'|'FIRMA_AUTENTIC_PENDIENTE_VERIFICACION'|'COMPLEMENTO_REGISTRADO'|'COMPLEMENTO_PENDIENTE'|`CENTRALES_${CentralesPersonal['estado']}`;
  documento:null|{codigo:'COLO-FO.023';version:'10';pdfSha256:string;pdfBase64:string;presentacion:string;operacionId:string;
    proposito:'AUTORIZACION_CENTRALES_PRECONSULTA'};firmado:false;consultasHabilitadas:false;evaluacionHabilitada:false;
  firma?:{operacionId:string;envioHabilitado:false;identidadVerificada:boolean};complemento?:ComplementoPersonalGuardado;centrales?:CentralesPersonal}
export interface SimulacionAceptable {version:'credihoy-simulacion-aceptable-v1';solicitudId:string;sha256:string;
  revisionReferencia:string;versionCondiciones:string;calculadoEn:string;vigenteHasta:string;fuente:'CREDIHOY_CALCULO_PROPIO';
  monto:string;plazoMeses:number;tasaNominalNamv:string;periodicidad:'MENSUAL';cuota:string;seguroMensualPorcentaje:string;
  baseSeguro:'SALDO_INICIAL_PERIODO';unidadRedondeo:'1';versionMotor:string;versionParametros:string;planAproximado:true;
  amortizacion:{periodo:number;saldoInicial:string;capital:string;intereses:string;seguro:string;otros:string;cuota:string;saldoFinal:string}[];
  totales:{capital:string;intereses:string;seguro:string;cuotas:string};autorizaStart:false;firmaContractual:false;aprobacionInstitucional:false}
export interface CotizacionTitular {versionContrato:'credihoy-cotizacion-titular-v1';solicitudId:string;estado:string;cuota:string|null;
  simulacion?:SimulacionAceptable;aceptacionSimulacion?:{aceptada:boolean;fecha:string|null};
  oferta:null|{sha256:string;monto:string;plazoMeses:number;periodicidad:'MENSUAL';tasaNominalNamv:string;
    tasaEA:string|null;primerVencimiento:string|null;vigenteHasta:string;condicionesCompletas:boolean;seguroIncluidoPorMotor:true;
    calendarioPago?:null|{versionRegla:string;diaPago:number;fechaBaseDesembolso:string;tipoBase:'PROYECTADO'|'EFECTIVO';
      primerVencimiento:string;interesesAnticipados:{desde:string;hasta:string;importe:null}}};
  aceptacion:{aceptada:boolean;fecha:string|null};autorizaStart:false;autorizaDesembolso:false}
export function validarCotizacionTitular(raw:unknown,sid:string,now=Date.now()):CotizacionTitular {
  const p=obj(raw),a=obj(p.aceptacion),o=p.oferta===null?null:obj(p.oferta);
  const amount=(v:unknown)=>typeof v==='string'&&/^\d{1,12}(\.\d{1,8})?$/.test(v)&&Number(v)>0;
  const own=p.estado==='SIMULACION_PROPIA';
  if(own){
    const hash=/^[a-f0-9]{64}$/;
    const s=obj(p.simulacion),a=obj(p.aceptacionSimulacion),rows=s.amortizacion;
    const keys='amortizacion,aprobacionInstitucional,autorizaStart,baseSeguro,calculadoEn,cuota,firmaContractual,fuente,monto,periodicidad,planAproximado,plazoMeses,revisionReferencia,seguroMensualPorcentaje,sha256,solicitudId,tasaNominalNamv,totales,unidadRedondeo,version,versionCondiciones,versionMotor,versionParametros,vigenteHasta';
    if(Object.keys(s).sort().join(',')!==keys||s.version!=='credihoy-simulacion-aceptable-v1'||s.solicitudId!==sid
      ||s.fuente!=='CREDIHOY_CALCULO_PROPIO'||s.planAproximado!==true||s.autorizaStart!==false||s.firmaContractual!==false
      ||s.aprobacionInstitucional!==false||s.periodicidad!=='MENSUAL'||s.baseSeguro!=='SALDO_INICIAL_PERIODO'||s.unidadRedondeo!=='1'
      ||s.versionMotor!=='credihoy-plan-decimal-v1'||typeof s.versionParametros!=='string'||!s.versionParametros
      ||!uuid.test(String(s.revisionReferencia))||!hash.test(String(s.sha256))||!hash.test(String(s.versionCondiciones))
      ||!amount(s.monto)||!amount(s.cuota)||!amount(s.tasaNominalNamv)||Number(s.tasaNominalNamv)>100
      ||typeof s.seguroMensualPorcentaje!=='string'||!/^\d+(\.\d+)?$/.test(s.seguroMensualPorcentaje)||Number(s.seguroMensualPorcentaje)>100
      ||!Number.isInteger(s.plazoMeses)||Number(s.plazoMeses)<1||Number(s.plazoMeses)>48
      ||![s.calculadoEn,s.vigenteHasta].every(v=>typeof v==='string'&&/(?:Z|[+-]\d{2}:\d{2})$/.test(v)&&Number.isFinite(Date.parse(v)))
      ||Date.parse(String(s.calculadoEn))>now||Date.parse(String(s.vigenteHasta))<=
        (a.aceptada===true&&typeof a.fecha==='string'?Date.parse(a.fecha):now)
      ||Date.parse(String(s.vigenteHasta))-Date.parse(String(s.calculadoEn))<60000
      ||Date.parse(String(s.vigenteHasta))-Date.parse(String(s.calculadoEn))>86400000
      ||!Array.isArray(rows)||rows.length!==s.plazoMeses||Object.keys(a).sort().join(',')!=='aceptada,fecha'
      ||typeof a.aceptada!=='boolean'||(a.aceptada?(typeof a.fecha!=='string'||!Number.isFinite(Date.parse(a.fecha))
        ||Date.parse(a.fecha)>now||Date.parse(a.fecha)<Date.parse(String(s.calculadoEn))):a.fecha!==null))fail();
    let balance=Number(s.monto);const totals={capital:0,intereses:0,seguro:0,cuotas:0};
    const planRows=rows as unknown[];
    planRows.forEach((raw,index)=>{const r=obj(raw),money=(v:unknown)=>typeof v==='string'&&/^\d{1,15}(\.\d{1,8})?$/.test(v);
      if(Object.keys(r).sort().join(',')!=='capital,cuota,intereses,otros,periodo,saldoFinal,saldoInicial,seguro'||r.periodo!==index+1
        ||!['capital','cuota','intereses','otros','saldoFinal','saldoInicial','seguro'].every(k=>money(r[k]))
        ||Number(r.capital)<=0||Number(r.otros)!==0||Math.abs(Number(r.saldoInicial)-balance)>.001
        ||Math.abs(Number(r.saldoFinal)-(balance-Number(r.capital)))>.001
        ||Number(r.cuota)!==Number(r.capital)+Number(r.intereses)+Number(r.seguro)
        ||index<planRows.length-1&&Number(r.cuota)!==Number(s.cuota))fail();
      balance=Number(r.saldoFinal);totals.capital+=Number(r.capital);totals.intereses+=Number(r.intereses);
      totals.seguro+=Number(r.seguro);totals.cuotas+=Number(r.cuota);});
    const t=obj(s.totales);
    if(balance!==0||Math.abs(totals.capital-Number(s.monto))>.001||Object.keys(t).sort().join(',')!=='capital,cuotas,intereses,seguro'
      ||Object.entries(totals).some(([k,v])=>typeof t[k]!=='string'||Math.abs(Number(t[k])-v)>.001))fail();
  }
  if(Object.keys(p).sort().join(',')!==(own?'aceptacion,aceptacionSimulacion,autorizaDesembolso,autorizaStart,cuota,estado,oferta,simulacion,solicitudId,versionContrato':'aceptacion,autorizaDesembolso,autorizaStart,cuota,estado,oferta,solicitudId,versionContrato')
    ||p.versionContrato!=='credihoy-cotizacion-titular-v1'||p.solicitudId!==sid||!uuid.test(sid)
    ||!['SIMULACION_PROPIA','DISPONIBLE_PARCIAL','SIN_COTIZACION','NO_HABILITADA','REQUIERE_CONCILIACION','DESACTUALIZADA',
      'PARAMETROS_EXPEDIENTE_INCOMPLETOS','PARAMETROS_FALTANTES','NO_DISPONIBLE','ERROR_TEMPORAL'].includes(String(p.estado))
    ||p.autorizaStart!==false||p.autorizaDesembolso!==false||Object.keys(a).sort().join(',')!=='aceptada,fecha'
    ||typeof a.aceptada!=='boolean')fail();
  if(o){
    const keys=['condicionesCompletas','monto','periodicidad','plazoMeses','primerVencimiento','seguroIncluidoPorMotor','sha256','tasaEA','tasaNominalNamv','vigenteHasta'];
    if('calendarioPago' in o)keys.push('calendarioPago');
    const completo=o.tasaEA!==null&&o.primerVencimiento!==null&&(!('calendarioPago' in o)||o.calendarioPago!==null);
    if(Object.keys(o).sort().join(',')!==keys.sort().join(',')
      ||!amount(p.cuota)||!amount(o.monto)||!amount(o.tasaNominalNamv)||(o.tasaEA!==null&&!amount(o.tasaEA))
      ||!/^[a-f0-9]{64}$/.test(String(o.sha256))||o.periodicidad!=='MENSUAL'||!Number.isSafeInteger(o.plazoMeses)
      ||Number(o.plazoMeses)<1||Number(o.plazoMeses)>48||o.seguroIncluidoPorMotor!==true
      ||!/(?:Z|[+-]\d{2}:\d{2})$/.test(String(o.vigenteHasta))||!Number.isFinite(Date.parse(String(o.vigenteHasta)))
      ||Date.parse(String(o.vigenteHasta))<=now||o.condicionesCompletas!==completo
      ||(o.primerVencimiento!==null&&(!/^\d{4}-\d{2}-\d{2}$/.test(String(o.primerVencimiento))
        ||!Number.isFinite(Date.parse(String(o.primerVencimiento)+'T12:00:00Z'))
        ||new Date(String(o.primerVencimiento)+'T12:00:00Z').toISOString().slice(0,10)!==o.primerVencimiento)))fail();
    if(o.calendarioPago!==undefined&&o.calendarioPago!==null){
      const c=obj(o.calendarioPago),i=obj(c.interesesAnticipados);
      const fecha=(v:unknown)=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)
        &&Number.isFinite(Date.parse(v+'T12:00:00Z'))&&new Date(v+'T12:00:00Z').toISOString().slice(0,10)===v;
      if(Object.keys(c).sort().join(',')!=='diaPago,fechaBaseDesembolso,interesesAnticipados,primerVencimiento,tipoBase,versionRegla'
        ||c.versionRegla!=='CREDIHOY_DIA_PAGO_30_CALENDARIO_20261028_V2'||!Number.isInteger(c.diaPago)
        ||Number(c.diaPago)<1||Number(c.diaPago)>20||!['PROYECTADO','EFECTIVO'].includes(String(c.tipoBase))
        ||!fecha(c.fechaBaseDesembolso)||!fecha(c.primerVencimiento)||c.primerVencimiento!==o.primerVencimiento
        ||Number(String(c.primerVencimiento).slice(-2))!==c.diaPago
        ||Date.parse(String(c.primerVencimiento))-Date.parse(String(c.fechaBaseDesembolso))<30*86400000
        ||Object.keys(i).sort().join(',')!=='desde,hasta,importe'||i.importe!==null
        ||i.desde!==c.fechaBaseDesembolso||!fecha(i.hasta)||String(i.hasta)<=String(i.desde)
        ||String(i.hasta)>String(c.primerVencimiento))fail();
    }
  }else if(p.cuota!==null||a.aceptada!==false||a.fecha!==null)fail();
  if(a.aceptada){if(!o?.condicionesCompletas||typeof a.fecha!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(a.fecha)
      ||!Number.isFinite(Date.parse(a.fecha))||Date.parse(a.fecha)>now)fail();}
  else if(a.fecha!==null)fail();
  return p as unknown as CotizacionTitular;
}
export interface BorradorPublico {referencia:string;fase:'BORRADOR'|'ENVIADA';version:number;
  datos:{monto?:string;plazo?:number;email?:string;periodicidadPago?:'MENSUAL';recogerCredihoy?:boolean;obligacionRecoger?:string};promotor:string|null;actualizadoAt:string;
  venceAt?:string|null;consultadoAt?:string;acciones:{radicar:false;firmar:false};solicitudRegistrada:boolean}
export interface AvisoTemporal {version:string;sha256:string;texto:string;presentacion:string;inactividadSegundos:3600}
export interface EstadoBorrador {borrador:BorradorPublico|null;avisoTemporal:AvisoTemporal}
export type SimulacionBorrador=Omit<SimulacionLocal,'solicitudId'>&{borradorId:string};
export type GuardarBorrador={operacion:'borrador';accion:'guardar'|'enviar';clave:string;operacionId:string;
  version:number;datos:BorradorPublico['datos'];promotor:string|null;
  aceptacion:{version:string;sha256:string;presentacion:string;acepta:true}};
export interface SeguimientoRadicacion {versionContrato:'credihoy-seguimiento-radicacion-v1';
  estado:'SIN_INTENCION'|'PREPARADA'|'EN_PROCESO'|'RADICADA'|'POR_CONFIRMAR'|'NO_DISPONIBLE';confirmada:boolean;
  consultadoEn:string;cuotaAceptada:string|null;aceptadaEn:string|null;decisionInstitucional:'PENDIENTE';firmaContractual:'PENDIENTE'}
export function validarSeguimientoRadicacion(raw:unknown,now=Date.now()):SeguimientoRadicacion {
  const p=obj(raw),date=(v:unknown)=>typeof v==='string'&&/(Z|[+-]\d{2}:\d{2})$/.test(v)&&Number.isFinite(Date.parse(v));
  if(Object.keys(p).sort().join(',')!=='aceptadaEn,confirmada,consultadoEn,cuotaAceptada,decisionInstitucional,estado,firmaContractual,versionContrato'
    ||p.versionContrato!=='credihoy-seguimiento-radicacion-v1'||!['SIN_INTENCION','PREPARADA','EN_PROCESO','RADICADA','POR_CONFIRMAR','NO_DISPONIBLE'].includes(String(p.estado))
    ||typeof p.confirmada!=='boolean'||p.confirmada!==(p.estado==='RADICADA')||!date(p.consultadoEn)||Date.parse(String(p.consultadoEn))>now
    ||p.decisionInstitucional!=='PENDIENTE'||p.firmaContractual!=='PENDIENTE'
    ||(p.cuotaAceptada===null?(p.aceptadaEn!==null||p.estado!=='NO_DISPONIBLE'):
      (typeof p.cuotaAceptada!=='string'||!/^\d{1,12}(\.\d{1,2})?$/.test(p.cuotaAceptada)||Number(p.cuotaAceptada)<=0
        ||!date(p.aceptadaEn)||Date.parse(String(p.aceptadaEn))>Date.parse(String(p.consultadoEn)))))fail();
  return p as unknown as SeguimientoRadicacion;
}
export interface SolicitudPublica { referencia:string; titulo:string; monto:string; plazoMeses:number;
  periodicidad:'Mensual'; fechaCreacion:string; requisitosCumplidos:number|null; pendientes:string[];
  siguientePaso:string; ofertaDisponible:false; acciones:{ radicar:false; firmar:false };simulacion?:SimulacionPublica|null;
  institucional?:InstitucionalPublica|null; estudio?:EstudioPublico|null;
  seguimientoRadicacion?:SeguimientoRadicacion;
  formalizacionDocumental?:{versionContrato:'credihoy-formalizacion-publica-v1';documento:'COLO-FO.001';
    estado:'DOCUMENTO_VERIFICADO'|'VALIDACION_PENDIENTE';firmaAcreditada:boolean;actualizadoAt:string;
    continuacionInstitucionalPendiente:true};
  envio?:{operacionId:string;confirmadoEn:string;solicitud:BorradorPublico};
  simulacionLocal?:SimulacionLocal;
  procedencia?:{origen:'HISTORICO_PILOTO'|'FUENTE_OPERATIVA';estadoRegistrado:string;actualizadoAt:string;consultadoAt:string} }
export interface InstitucionalPublica {estado:'APROBADA_PENDIENTE_FIRMA';fechaDecision:string;actualizadoAt:string;
  documentos:'NO_ACREDITADOS';firma:'PENDIENTE';
  incidencias:'NO_DISPONIBLE'|'EN_REVISION'|'ANTECEDENTES_SIN_PENDIENTE_ACTIVO'|'SIN_INCIDENCIAS_REGISTRADAS'}
export interface EstudioPublico {
  versionContrato:'credihoy-estudio-publico-v1';solicitudId:string;evaluacionBaseId:number;version:number;
  referenciaVersion:string;fechaEvaluacion:string;
  reglas:{codigo:string;nombre:string;resultado:'CUMPLE'|'NO_CUMPLE'|'NO_EVALUABLE';
    estadoEvaluacion:'CUMPLE'|'NO_CUMPLE'|'NO_EVALUABLE'|'NO_APLICA'|'PENDIENTE_POLITICA';
    motivos:string[];datosFaltantes:string[]}[];
  capacidad:{estado:'EVALUABLE'|'NO_EVALUABLE'|'INFORMACION_INCONSISTENTE';valor:string|null;
    preliminarNoDecisoria:string|null;cuotaContractualDisponible:false;
    capacidadDisponible?:string|null;capacidadResidualPostCuota?:string|null;
    metodologia?:{version:'credihoy-capacidad-publica-v1';deudaConciliada:boolean;
      disponibleAntesGastosFamiliares:string|null;gastosFamiliaresAplicados:string|null;
      capacidadDisponible:string|null;cuotaNuevaEstimada:string|null;residualPosteriorEstimado:string|null}};
  decision:'PENDIENTE_REVISION'|'REQUIERE_SUBSANACION';autorizadoDecisionCrediticia:false;
  decisionPolitica?:{version:'credihoy-decision-publica-v1';versionPolitica:string;estado:'APROBADO'|'RECHAZADO'|'EN_REVISION';
    motivos:{codigo:string;regla:string|null}[];decisionLinixRegistrada:false;autorizaDesembolso:false;
    continuidad?:{version:'credihoy-continuidad-publica-v1';estudioFavorable:boolean;
      estadoRadicacion:'PREPARACION_PENDIENTE';decisionInstitucional:'PENDIENTE';firmaContractual:'PENDIENTE';
      radicacionHabilitada:false;firmaHabilitada:false}};
}
export interface SimulacionPublica {cuota:string;tasaNominalNamv:string;
  origen:'REFERENCIA_INSTITUCIONAL_REGISTRADA'|'CALCULO_INSTITUCIONAL';
  ofertaContractual:false;seguroDisponible:false;calendarioDisponible:false}
export class AccesoPublicoError extends Error {
  readonly codigo:string;
  constructor(readonly status:number,codigo?:string|null){
    super('ACCESO_NO_COMPLETADO');
    this.codigo=codigo==='ACCESS_WINDOW_EXPIRED'?codigo:'ACCESS_UNAVAILABLE';
  }
}
export interface DatosAcceso { numeroDocumento:string; primerApellido:string; fechaExpedicion:string; canal:'SMS'|'EMAIL';
  avisoVersion:string; aceptaTratamiento:true; botToken:string }
export function baseApiPublica(pathname:string):string {
  if(pathname==='/')return '/api/v1/credihoy/publico/';
  if(pathname==='/credihoy'||pathname.startsWith('/credihoy/'))return '/credihoy/api/v1/credihoy/publico/';
  throw new Error('RUTA_PUBLICA_NO_ADMITIDA');
}
const uuid=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const version='credihoy-acceso-publico-v1';
const fail=():never=>{throw new Error('ACCESO_NO_COMPLETADO');};
const obj=(v:unknown):Record<string,unknown>=>v!==null&&typeof v==='object'&&!Array.isArray(v)?v as Record<string,unknown>:fail();
const text=(v:unknown,max=500):string=>typeof v==='string'&&v.length<=max?v:fail();
export async function validarAutorizacionTitular(raw:unknown,sid:string,now=Date.now()):Promise<AutorizacionTitular>{
  const p=obj(raw);
  if(Object.keys(p).sort().join(',')!==(('centrales' in p?'centrales,':'')+('complemento' in p?'complemento,':'')+'consultasHabilitadas,documento,estado,evaluacionHabilitada,'+('firma' in p?'firma,':'')+'firmado,solicitudId,versionContrato')
    ||p.versionContrato!=='credihoy-autorizacion-titular-v1'||p.solicitudId!==sid||!uuid.test(sid)
    ||p.firmado!==false||p.consultasHabilitadas!==false||p.evaluacionHabilitada!==false
    ||!['PENDIENTE_ACEPTACION_PERSONAL','PENDIENTE_FIRMA_AUTENTIC','AUTORIZACION_AUSENTE','FIRMA_AUTENTIC_PENDIENTE_VERIFICACION','COMPLEMENTO_REGISTRADO','COMPLEMENTO_PENDIENTE',
      'CENTRALES_SIN_ACEPTACION','CENTRALES_PENDIENTE_ACEPTACION','CENTRALES_PENDIENTE_OTP','CENTRALES_ENVIADO','CENTRALES_OTP_CONFIRMADA','CENTRALES_RECHAZADA','CENTRALES_INDETERMINADA'].includes(text(p.estado)))fail();
  if('centrales' in p){
    const c=obj(p.centrales);
    if('firma' in p||'complemento' in p||Object.keys(c).sort().join(',')!=='confirmadoEn,estado,metodo,operacionId,pdfSha256,presentacion,proposito,textoSha256,textoVersion,verificacionReferencia,vigenteHasta'
      ||p.estado!=='CENTRALES_'+c.estado||c.proposito!=='CONSULTA_CENTRALES'||c.metodo!=='OTP'||c.textoVersion!=='COLO-FO.023:v10'
      ||!['SIN_ACEPTACION','PENDIENTE_ACEPTACION','PENDIENTE_OTP','ENVIADO','OTP_CONFIRMADA','RECHAZADA','INDETERMINADA'].includes(String(c.estado))
      ||!['pdfSha256','textoSha256'].every(k=>/^[a-f0-9]{64}$/.test(String(c[k]))))fail();
    if(c.estado==='SIN_ACEPTACION'){
      if(['operacionId','presentacion','confirmadoEn','verificacionReferencia','vigenteHasta'].some(k=>c[k]!==null))fail();
    }else if(!uuid.test(String(c.operacionId))||typeof c.vigenteHasta!=='string'
      ||!/(?:Z|[+-]\d{2}:\d{2})$/.test(c.vigenteHasta)||!Number.isFinite(Date.parse(c.vigenteHasta)))fail();
    if(c.presentacion!==null&&(typeof c.presentacion!=='string'||!c.presentacion||c.presentacion.length>4096))fail();
    if(c.estado==='OTP_CONFIRMADA'){
      if(typeof c.confirmadoEn!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(c.confirmadoEn)
        ||!Number.isFinite(Date.parse(c.confirmadoEn))||Date.parse(c.confirmadoEn)>now+5000
        ||!/^[a-f0-9]{64}$/.test(String(c.verificacionReferencia)))fail();
    }else if(c.confirmadoEn!==null||c.verificacionReferencia!==null)fail();
  }else if(String(p.estado).startsWith('CENTRALES_'))fail();
  if('complemento' in p){
    const c=obj(p.complemento);
    if('firma' in p||p.documento!==null||Object.keys(c).sort().join(',')!=='confirmadoEn,datos,fuente,version'
      ||!Number.isInteger(c.version)||Number(c.version)<0||Number(c.version)>20)fail();
    if(c.version===0){
      if(c.datos!==null||c.fuente!==null||c.confirmadoEn!==null||p.estado!=='COMPLEMENTO_PENDIENTE')fail();
    }else{
      const d=obj(c.datos);
      if(Object.keys(d).sort().join(',')!=='obligacionRecoger,periodicidadPago,recogerCredihoy'
        ||d.periodicidadPago!=='MENSUAL'||typeof d.recogerCredihoy!=='boolean'
        ||(d.recogerCredihoy?(c.fuente!=='ELECCION_PERSONAL_ENVIO'||typeof d.obligacionRecoger!=='string'||!/^\d{1,20}$/.test(d.obligacionRecoger)):d.obligacionRecoger!==null)
        ||!['ELECCION_PERSONAL_COMPLEMENTO','ELECCION_PERSONAL_ENVIO'].includes(String(c.fuente))||p.estado!=='COMPLEMENTO_REGISTRADO'
        ||typeof c.confirmadoEn!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(c.confirmadoEn)
        ||!Number.isFinite(Date.parse(c.confirmadoEn))||Date.parse(c.confirmadoEn)>now+5000)fail();
    }
  }else if(['COMPLEMENTO_REGISTRADO','COMPLEMENTO_PENDIENTE'].includes(String(p.estado)))fail();
  if('firma' in p){const f=obj(p.firma);
    if(Object.keys(f).sort().join(',')!=='envioHabilitado,identidadVerificada,operacionId'
      ||!uuid.test(text(f.operacionId))||f.envioHabilitado!==false||typeof f.identidadVerificada!=='boolean')fail();}
  if(p.documento!==null){
    const d=obj(p.documento);
    if(Object.keys(d).sort().join(',')!=='codigo,operacionId,pdfBase64,pdfSha256,presentacion,proposito,version'
      ||d.codigo!=='COLO-FO.023'||d.version!=='10'||d.proposito!=='AUTORIZACION_CENTRALES_PRECONSULTA'
      ||!uuid.test(text(d.operacionId))||!/^[a-f0-9]{64}$/.test(text(d.pdfSha256,64))
      ||!text(d.presentacion,4096)||!text(d.pdfBase64,800000))fail();
    const bytes=Uint8Array.from(atob(d.pdfBase64 as string),c=>c.charCodeAt(0));
    const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(v=>v.toString(16).padStart(2,'0')).join('');
    if(digest!==d.pdfSha256||new TextDecoder().decode(bytes.slice(0,5))!=='%PDF-')fail();
  }
  return p as unknown as AutorizacionTitular;
}
export function validarBorrador(raw:unknown):BorradorPublico|null{
  if(raw===null)return null;
  const p=obj(raw),d=obj(p.datos),a=obj(p.acciones);
  const temporal='venceAt' in p;
  if(Object.keys(p).sort().join(',')!==('acciones,actualizadoAt,'+('consultadoAt' in p?'consultadoAt,':'')+'datos,fase,promotor,referencia,solicitudRegistrada,'+(temporal?'venceAt,':'')+'version')
    ||!uuid.test(text(p.referencia))||!(['BORRADOR',...(temporal?['ENVIADA']:[])]).includes(text(p.fase))||p.solicitudRegistrada!==(p.fase==='ENVIADA')
    ||!Number.isSafeInteger(p.version)||Number(p.version)<1
    ||Object.keys(d).some(k=>!['monto','plazo','email','periodicidadPago','recogerCredihoy','obligacionRecoger'].includes(k))
    ||(d.periodicidadPago!==undefined&&d.periodicidadPago!=='MENSUAL')
    ||(d.recogerCredihoy!==undefined&&typeof d.recogerCredihoy!=='boolean')
    ||(d.obligacionRecoger!==undefined&&(typeof d.obligacionRecoger!=='string'||!/^\d{1,20}$/.test(d.obligacionRecoger)))
    ||(d.monto!==undefined&&(typeof d.monto!=='string'||!/^\d{1,10}(\.\d{1,2})?$/.test(d.monto)||Number(d.monto)<=0))
    ||(d.plazo!==undefined&&(!Number.isSafeInteger(d.plazo)||Number(d.plazo)<1||Number(d.plazo)>48))
    ||(d.email!==undefined&&(typeof d.email!=='string'||d.email.length>254||!/^\S+@\S+\.\S+$/.test(d.email)))
    ||(p.promotor!==null&&!/^[A-Za-z0-9_-]{1,32}$/.test(text(p.promotor)))
    ||!Number.isFinite(Date.parse(text(p.actualizadoAt)))||a.radicar!==false||a.firmar!==false
    ||Object.keys(a).sort().join(',')!=='firmar,radicar')fail();
  if(temporal&&(p.fase==='ENVIADA'?p.venceAt!==null:
    typeof p.venceAt!=='string'||Date.parse(p.venceAt)-Date.parse(p.actualizadoAt as string)!==3600000))fail();
  if('consultadoAt' in p&&(!Number.isFinite(Date.parse(text(p.consultadoAt)))||Date.parse(p.consultadoAt as string)<Date.parse(p.actualizadoAt as string)))fail();
  return {referencia:p.referencia as string,fase:p.fase as BorradorPublico['fase'],version:p.version as number,
    datos:{...d} as BorradorPublico['datos'],promotor:p.promotor as string|null,
    actualizadoAt:p.actualizadoAt as string,...(temporal?{venceAt:p.venceAt as string|null}:{}),
    ...('consultadoAt' in p?{consultadoAt:p.consultadoAt as string}:{}),
    acciones:{radicar:false,firmar:false},solicitudRegistrada:p.fase==='ENVIADA'};
}
const pendientesPermitidos=[
  'Debes actualizar tus datos para continuar con tu solicitud.',
  'Necesitamos completar algunas validaciones antes de tomar una decision.',
  'Hay requisitos pendientes que requieren revision con Congente.',
  'Comunicate con Congente para revisar el estado.',
];
export function validarInstitucionalPublica(raw:unknown,now=Date.now()):InstitucionalPublica|null {
  let institucional:InstitucionalPublica|null=null;
  if(raw!==null&&raw!==undefined){
    const i=obj(raw),date=text(i.fechaDecision),observed=text(i.actualizadoAt);
    if(Object.keys(i).sort().join(',')!=='actualizadoAt,documentos,estado,fechaDecision,firma,incidencias'
      ||i.estado!=='APROBADA_PENDIENTE_FIRMA'||i.documentos!=='NO_ACREDITADOS'||i.firma!=='PENDIENTE'
      ||!['NO_DISPONIBLE','EN_REVISION','ANTECEDENTES_SIN_PENDIENTE_ACTIVO','SIN_INCIDENCIAS_REGISTRADAS'].includes(text(i.incidencias))
      ||!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(Date.parse(date+'T00:00:00Z'))
      ||new Date(date+'T00:00:00Z').toISOString().slice(0,10)!==date
      ||!/(?:Z|[+-]\d{2}:\d{2})$/.test(observed)||!Number.isFinite(Date.parse(observed))
      ||Date.parse(observed)>now||date>observed.slice(0,10))fail();
    institucional={estado:'APROBADA_PENDIENTE_FIRMA',fechaDecision:date,actualizadoAt:observed,
      documentos:'NO_ACREDITADOS',firma:'PENDIENTE',incidencias:i.incidencias as InstitucionalPublica['incidencias']};
  }
  return institucional;
}
export function validarEstudioPublico(raw:unknown,sid:string,now=Date.now()):EstudioPublico|null {
  if(raw===undefined||raw===null)return null;
  const p=obj(raw),c=obj(p.capacidad);
  const fields=['versionContrato','solicitudId','evaluacionBaseId','version','referenciaVersion',
    'fechaEvaluacion','reglas','capacidad','decision','autorizadoDecisionCrediticia'];
  if(p.decisionPolitica!==undefined){
    fields.push('decisionPolitica');const d=obj(p.decisionPolitica);
    const decisionFields=['version','versionPolitica','estado','motivos','decisionLinixRegistrada','autorizaDesembolso'];
    if(d.continuidad!==undefined){
      decisionFields.push('continuidad');const c=obj(d.continuidad);
      if(Object.keys(c).sort().join(',')!=='decisionInstitucional,estadoRadicacion,estudioFavorable,firmaContractual,firmaHabilitada,radicacionHabilitada,version'
        ||c.version!=='credihoy-continuidad-publica-v1'||typeof c.estudioFavorable!=='boolean'
        ||c.estadoRadicacion!=='PREPARACION_PENDIENTE'||c.decisionInstitucional!=='PENDIENTE'
        ||c.firmaContractual!=='PENDIENTE'||c.radicacionHabilitada!==false||c.firmaHabilitada!==false
        ||c.estudioFavorable&&(d.estado==='RECHAZADO'||!Array.isArray(d.motivos)
          ||d.motivos.some(v=>obj(v).codigo!=='REVISION_ESTAMENTO_INSTITUCIONAL')))fail();
    }
    if(Object.keys(d).sort().join(',')!==decisionFields.sort().join(',')
      ||d.version!=='credihoy-decision-publica-v1'||!text(d.versionPolitica)||text(d.versionPolitica).length>120
      ||!['APROBADO','RECHAZADO','EN_REVISION'].includes(text(d.estado))
      ||d.decisionLinixRegistrada!==false||d.autorizaDesembolso!==false||!Array.isArray(d.motivos)||d.motivos.length>40)fail();
    for(const value of d.motivos as unknown[]){const reason=obj(value);
      if(Object.keys(reason).sort().join(',')!=='codigo,regla'||!/^([A-Z][A-Z0-9_]{1,120})$/.test(text(reason.codigo))
        ||reason.regla!==null&&!/^UNICO_FILA_[0-9]{1,2}$/.test(text(reason.regla)))fail();}
  }
  const sameKeys=(v:Record<string,unknown>,keys:string[])=>Object.keys(v).sort().join(',')===[...keys].sort().join(',');
  if(!sameKeys(p,fields)||p.versionContrato!=='credihoy-estudio-publico-v1'||p.solicitudId!==sid||!uuid.test(sid)
    ||!Number.isSafeInteger(p.evaluacionBaseId)||Number(p.evaluacionBaseId)<1
    ||!Number.isSafeInteger(p.version)||Number(p.version)<1||!uuid.test(text(p.referenciaVersion))
    ||!/(?:Z|[+-]\d{2}:\d{2})$/.test(text(p.fechaEvaluacion))||!Number.isFinite(Date.parse(text(p.fechaEvaluacion)))
    ||Date.parse(text(p.fechaEvaluacion))>now||p.autorizadoDecisionCrediticia!==false
    ||!['PENDIENTE_REVISION','REQUIERE_SUBSANACION'].includes(text(p.decision)))fail();
  const codes=[2,4,6,9,10,12,14,17,21,23,24,27,28,30,31,32].map(n=>'UNICO_FILA_'+n);
  if(!Array.isArray(p.reglas)||p.reglas.length!==16)fail();
  const rules=(p.reglas as unknown[]).map(value=>{
    const r=obj(value);
    if(!sameKeys(r,['codigo','nombre','resultado','estadoEvaluacion','motivos','datosFaltantes'])
      ||!codes.includes(text(r.codigo))||typeof r.nombre!=='string'||!r.nombre.length||r.nombre.length>120
      ||!['CUMPLE','NO_CUMPLE','NO_EVALUABLE'].includes(text(r.resultado))
      ||!['CUMPLE','NO_CUMPLE','NO_EVALUABLE','NO_APLICA','PENDIENTE_POLITICA'].includes(text(r.estadoEvaluacion)))fail();
    for(const key of ['motivos','datosFaltantes']){
      const list=r[key];
      if(!Array.isArray(list)||list.length>20||list.some(v=>typeof v!=='string'||!v.length||v.length>240))fail();
    }
    return {...r,motivos:[...r.motivos as string[]],datosFaltantes:[...r.datosFaltantes as string[]]} as EstudioPublico['reglas'][number];
  });
  if(new Set(rules.map(r=>r.codigo)).size!==16)fail();
  if(p.decisionPolitica&&obj(obj(p.decisionPolitica).continuidad??{}).estudioFavorable===true
    &&rules.some(r=>!['CUMPLE','NO_APLICA'].includes(r.estadoEvaluacion)
      &&!(r.codigo==='UNICO_FILA_6'&&r.estadoEvaluacion==='NO_CUMPLE')))fail();
  const capacityFields=['estado','valor','preliminarNoDecisoria','cuotaContractualDisponible'];
  if(c.metodologia!==undefined){
    capacityFields.push('metodologia');const m=obj(c.metodologia);
    const amounts=['disponibleAntesGastosFamiliares','gastosFamiliaresAplicados','capacidadDisponible','cuotaNuevaEstimada','residualPosteriorEstimado'];
    if(Number(p.version)<2||!sameKeys(m,['version','deudaConciliada',...amounts])
      ||m.version!=='credihoy-capacidad-publica-v1'||typeof m.deudaConciliada!=='boolean')fail();
    for(const key of amounts)if(m[key]!==null&&(typeof m[key]!=='string'||!/^\-?\d{1,15}(?:\.\d{1,8})?$/.test(String(m[key]))))fail();
  }
  const separatedCapacity=Number(p.version)>=8;
  if(separatedCapacity)capacityFields.push('capacidadDisponible','capacidadResidualPostCuota');
  if(!sameKeys(c,capacityFields)
    ||!['EVALUABLE','NO_EVALUABLE','INFORMACION_INCONSISTENTE'].includes(text(c.estado))
    ||c.cuotaContractualDisponible!==false)fail();
  for(const key of ['valor','preliminarNoDecisoria',...(separatedCapacity?['capacidadDisponible','capacidadResidualPostCuota']:[])]){
    if(c[key]!==null&&(typeof c[key]!=='string'||!/^\-?\d{1,15}(?:\.\d{1,8})?$/.test(String(c[key]))))fail();
  }
  if((c.estado==='EVALUABLE')!==(c.valor!==null))fail();
  return {...p,reglas:rules,capacidad:{...c}} as unknown as EstudioPublico;
}

export function validarSolicitudPublica(raw:unknown,now=Date.now()):SolicitudPublica {
  const p=obj(raw),a=obj(p.acciones),institucional=validarInstitucionalPublica(p.institucional,now);
  let simulacion:SimulacionPublica|null=null;
  if(p.simulacion!==null&&p.simulacion!==undefined){
    const q=obj(p.simulacion);
    if(typeof q.cuota!=='string'||!/^\d{1,10}(\.\d{1,7})?$/.test(q.cuota)||Number(q.cuota)<=0
      ||typeof q.tasaNominalNamv!=='string'||!/^\d{1,3}(\.\d{1,7})?$/.test(q.tasaNominalNamv)
      ||!['REFERENCIA_INSTITUCIONAL_REGISTRADA','CALCULO_INSTITUCIONAL'].includes(text(q.origen))
      ||q.ofertaContractual!==false||q.seguroDisponible!==false||q.calendarioDisponible!==false)fail();
    simulacion={cuota:q.cuota as string,tasaNominalNamv:q.tasaNominalNamv as string,origen:q.origen as SimulacionPublica['origen'],
      ofertaContractual:false,seguroDisponible:false,calendarioDisponible:false};
  }
  if (!uuid.test(text(p.referencia)) || !['Actualiza tus datos','Tu solicitud esta en revision','Estamos verificando tu solicitud'].includes(text(p.titulo))
      || typeof p.monto!=='string'||!/^\d{1,10}(\.\d{1,2})?$/.test(p.monto)
      || !Number.isSafeInteger(p.plazoMeses)||Number(p.plazoMeses)<1||Number(p.plazoMeses)>48
      || p.periodicidad!=='Mensual'||!Number.isFinite(Date.parse(text(p.fechaCreacion)))
      || (p.requisitosCumplidos!==null&&(!Number.isSafeInteger(p.requisitosCumplidos)||Number(p.requisitosCumplidos)<0||Number(p.requisitosCumplidos)>16))
      || !Array.isArray(p.pendientes)||p.pendientes.some(v=>!pendientesPermitidos.includes(v))
      || !['Actualiza tus datos con Congente.','Espera la revision de Congente.'].includes(text(p.siguientePaso))
      || p.ofertaDisponible!==false||a.radicar!==false||a.firmar!==false)fail();
  const estudio=validarEstudioPublico(p.estudio,text(p.referencia),now);
  if(estudio&&p.requisitosCumplidos!==estudio.reglas.filter(r=>r.resultado==='CUMPLE').length)fail();
  let procedencia:SolicitudPublica['procedencia'];
  if(p.procedencia!==undefined){
    const f=obj(p.procedencia),updated=text(f.actualizadoAt),consulted=text(f.consultadoAt);
    if(Object.keys(f).sort().join(',')!=='actualizadoAt,consultadoAt,estadoRegistrado,origen'
      ||!['HISTORICO_PILOTO','FUENTE_OPERATIVA'].includes(text(f.origen))
      ||!['Iniciada','Autorizada','Preselecta OK','Historial OK','Enviada a XCORE','Finalizada','OTP pendiente',
        'OTP validada','Consentimiento pendiente','Consentimiento firmado','Core consultado','Formulario XCORE OK',
        'Procesando','Error'].includes(text(f.estadoRegistrado))
      ||!/(?:Z|[+-]\d{2}:\d{2})$/.test(updated)||!/(?:Z|[+-]\d{2}:\d{2})$/.test(consulted)
      ||!Number.isFinite(Date.parse(updated))||!Number.isFinite(Date.parse(consulted))
      ||Date.parse(updated)>Date.parse(consulted)||Date.parse(consulted)>now
      ||(f.origen==='HISTORICO_PILOTO'&&(institucional!==null||simulacion!==null||p.requisitosCumplidos!==null)))fail();
    procedencia={origen:f.origen as 'HISTORICO_PILOTO'|'FUENTE_OPERATIVA',estadoRegistrado:f.estadoRegistrado as string,
      actualizadoAt:updated,consultadoAt:consulted};
  }
  if(estudio&&procedencia?.origen!=='FUENTE_OPERATIVA')fail();
  let envio:SolicitudPublica['envio'];
  if(p.envio!==undefined){
    const e=obj(p.envio),draft=validarBorrador(e.solicitud);
    if(Object.keys(e).sort().join(',')!=='confirmadoEn,operacionId,solicitud'||!uuid.test(text(e.operacionId))
      ||!Number.isFinite(Date.parse(text(e.confirmadoEn)))||Date.parse(e.confirmadoEn as string)>now
      ||procedencia?.origen!=='FUENTE_OPERATIVA'||!draft||draft.fase!=='ENVIADA'||draft.referencia!==p.referencia
      ||Number(draft.datos.monto)!==Number(p.monto)||draft.datos.plazo!==p.plazoMeses)fail();
    if(draft===null)throw new Error('ENVIO_NO_CONFIRMADO');
    envio={operacionId:e.operacionId as string,confirmadoEn:e.confirmadoEn as string,solicitud:draft};
  }
  let simulacionLocal:SimulacionLocal|undefined;
  if(p.simulacionLocal!==undefined){
    const q=obj(p.simulacionLocal),rate=obj(q.tasa);
    simulacionLocal=validarSimulacionLocal(q,p.referencia as string,q.requestId as string,
      {monto:p.monto as string,plazoMeses:p.plazoMeses as number,linea:rate.linea as 'VENTANILLA'|'LIBRANZA'});
  }
  let formalizacionDocumental:SolicitudPublica['formalizacionDocumental'];
  if(p.formalizacionDocumental!=null){
    const f=obj(p.formalizacionDocumental);
    const fields=['versionContrato','documento','estado','firmaAcreditada','actualizadoAt','continuacionInstitucionalPendiente'];
    if(Object.keys(f).length!==fields.length||fields.some(k=>!(k in f))
      ||f.versionContrato!=='credihoy-formalizacion-publica-v1'||f.documento!=='COLO-FO.001'
      ||typeof f.firmaAcreditada!=='boolean'||f.continuacionInstitucionalPendiente!==true
      ||f.estado!==(f.firmaAcreditada?'DOCUMENTO_VERIFICADO':'VALIDACION_PENDIENTE')
      ||typeof f.actualizadoAt!=='string'||!Number.isFinite(Date.parse(f.actualizadoAt))||Date.parse(f.actualizadoAt)>now)fail();
    formalizacionDocumental=f as unknown as NonNullable<SolicitudPublica['formalizacionDocumental']>;
  }
  return {referencia:p.referencia as string,titulo:p.titulo as string,monto:p.monto as string,plazoMeses:p.plazoMeses as number,
    periodicidad:'Mensual',fechaCreacion:p.fechaCreacion as string,requisitosCumplidos:p.requisitosCumplidos as number|null,
    pendientes:[...p.pendientes as string[]],siguientePaso:p.siguientePaso as string,ofertaDisponible:false,acciones:{radicar:false,firmar:false},simulacion,institucional,
    ...(procedencia?{procedencia}:{}),...(estudio?{estudio}:{}),...(envio?{envio}:{}),...(simulacionLocal?{simulacionLocal}:{}),
    ...(formalizacionDocumental?{formalizacionDocumental}:{}),
    ...(p.seguimientoRadicacion!==undefined?{seguimientoRadicacion:validarSeguimientoRadicacion(p.seguimientoRadicacion,now)}:{})};
}

export class AccesoAsociadoClient {
  private readonly base=baseApiPublica(globalThis.location?.pathname??'/');
  private csrf='';
  private acceso='';
  private revision=0;
  private responseNow=Date.now();
  private responseAt=performance.now();
  ahoraServidor(){return this.responseNow+Math.max(0,performance.now()-this.responseAt);}
  invalidarSesion(){this.revision++;this.csrf='';this.acceso='';}
  private async request(path:string,body?:unknown,rid?:string):Promise<unknown>{
    const revision=this.revision;
    const c=new AbortController(),timer=setTimeout(()=>c.abort(),20000);
    try {
      const r=await fetch(this.base+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',
        redirect:'error',cache:'no-store',signal:c.signal,headers:{Accept:'application/json',
          ...(body===undefined?{}:{'Content-Type':'application/json','X-CSRFToken':this.csrf}),
          ...(rid?{'X-Request-ID':rid}:{}),...(this.acceso?{'X-Acceso-Referencia':this.acceso}:{})},
        ...(body===undefined?{}:{body:JSON.stringify(body)})});
      if(revision!==this.revision)fail();
      if(!r.ok)throw new AccesoPublicoError(r.status,r.headers.get('X-Error-Code'));if(r.status===204)return null;
      if(!r.headers.get('Content-Type')?.includes('application/json'))fail();
      const p=await r.json();if(revision!==this.revision)fail();
      // The same-origin HTTP Date is authoritative; its wire precision is one second.
      const serverDate=Date.parse(r.headers.get('Date')??'');
      this.responseNow=Number.isFinite(serverDate)?serverDate+999:Date.now();
      this.responseAt=performance.now();
      return p;
    } finally {clearTimeout(timer);}
  }
  async inicio():Promise<InicioPublico>{
    const p=obj(await this.request('inicio/'));
    const politica=obj(p.politica);
    if(p.versionContrato!==version||!/^[A-Za-z0-9]{32,64}$/.test(text(p.csrfToken))
        ||politica.codigo!=='GER-PO.085'||politica.version!=='03'||politica.url!==POLITICA_URL
        ||!Array.isArray(p.canales)||!p.canales.length||p.canales.some(v=>!['SMS','EMAIL'].includes(v))
        ||!Number.isInteger(p.duracionSesionSegundos)||Number(p.duracionSesionSegundos)<=0||Number(p.duracionSesionSegundos)>900
        ||!Number.isInteger(p.duracionOtpSegundos)||Number(p.duracionOtpSegundos)<=0||Number(p.duracionOtpSegundos)>300)fail();
    this.csrf=p.csrfToken as string;
    if(p.proteccion!==undefined&&(p.proteccion!=='OPERADOR_RESTRINGIDO'||typeof p.controlAcceso!=='string'||p.controlAcceso.length<40||p.controlAcceso.length>2048))fail();
    return {csrfToken:this.csrf,avisoVersion:text(p.avisoVersion),avisoTexto:text(p.avisoTexto,20000),
      ...(p.proteccion==='OPERADOR_RESTRINGIDO'?{proteccion:p.proteccion,controlAcceso:p.controlAcceso as string}:{}),
      botSitekey:text(p.botSitekey),canales:[...p.canales as ('SMS'|'EMAIL')[]],
      politica:{codigo:'GER-PO.085',version:'03',url:POLITICA_URL},
      duracionSesionSegundos:p.duracionSesionSegundos as number,duracionOtpSegundos:p.duracionOtpSegundos as number};
  }
  async sesion():Promise<SesionPublica>{
    const p=obj(await this.request('sesion/'));
    if(p.versionContrato!==version)fail();if(p.autenticado===false){this.acceso='';return {autenticado:false};}
    const a=obj(p.acciones);
    if(p.autenticado!==true||!uuid.test(text(p.sesionReferencia))||!Number.isFinite(Date.parse(text(p.expiraAt)))||Date.parse(p.expiraAt as string)<=Date.now()
        ||typeof a.seguimiento!=='boolean'||typeof a.solicitar!=='boolean'||typeof a.simular!=='boolean'||a.documentos!==false)fail();
    this.acceso=p.sesionReferencia as string;
    let perfil:PerfilPersonal|undefined;
    if(p.perfil!==undefined){
      const v=obj(p.perfil);
      if(Object.keys(v).sort().join(',')!=='contactoEnmascarado,correoRegistrado,correoVerificado,fuente'
        ||v.fuente!=='COTEJO_INSTITUCIONAL_AUTENTICADO'||typeof v.correoVerificado!=='boolean'
        ||(v.correoRegistrado!==null&&(typeof v.correoRegistrado!=='string'||v.correoRegistrado.length>254||!/^\S+@\S+\.\S+$/.test(v.correoRegistrado)))
        ||(v.contactoEnmascarado!==null&&(typeof v.contactoEnmascarado!=='string'||!/^\*\*\*[0-9]{2}$/.test(v.contactoEnmascarado)))
        ||(v.correoVerificado&&v.correoRegistrado===null))fail();
      perfil=v as unknown as PerfilPersonal;
    }
    return {autenticado:true,nombre:p.nombre===null?null:text(p.nombre,255),expiraAt:p.expiraAt as string,sesionReferencia:this.acceso,...(perfil?{perfil}:{}),
      acciones:{seguimiento:a.seguimiento as boolean,solicitar:a.solicitar as boolean,simular:a.simular as boolean,documentos:false,
        ...(a.autorizar===true?{autorizar:true}:{}),...(a.centralesOtp===true?{centralesOtp:true}:{}),
        ...(a.simuladorLocal===true?{simuladorLocal:true}:{}),...(a.radicar===true?{radicar:true}:{})}};
  }
  async radicacion(body:PeticionRadicacion):Promise<RadicacionTitular>{
    if(!this.acceso)throw new AccesoPublicoError(401);
    const grant=this.acceso,p=obj(await this.request('solicitudes/',body,crypto.randomUUID()));
    if(this.acceso!==grant||p.sesionReferencia!==grant)fail();
    return validarRadicacionTitular(p.radicacion,body.solicitudId);
  }
  async autorizacion(body:PeticionAutorizacion):Promise<AutorizacionTitular>{
    if(!this.acceso)throw new AccesoPublicoError(401);
    const grant=this.acceso,p=obj(await this.request('solicitudes/',body));
    if(this.acceso!==grant||p.sesionReferencia!==grant)fail();
    return validarAutorizacionTitular(p.autorizacion,body.solicitudId,this.ahoraServidor());
  }
  async cotizacion(sid:string,operacion:'consultar'|'cotizar'|'aceptar'|'aceptar_simulacion',ofertaSha256?:string,diaPago?:number):Promise<CotizacionTitular>{
    if(!this.acceso)throw new AccesoPublicoError(401);
    if(!uuid.test(sid)||(['aceptar','aceptar_simulacion'].includes(operacion)&&!/^[a-f0-9]{64}$/.test(ofertaSha256??'')))fail();
    if(diaPago!==undefined&&(operacion!=='cotizar'||!Number.isInteger(diaPago)||diaPago<1||diaPago>20))fail();
    const grant=this.acceso;
    const p=await this.request('cotizacion/',{solicitudId:sid,operacion,
      ...(operacion==='aceptar'?{ofertaSha256,acepta:true}:{}),
      ...(operacion==='aceptar_simulacion'?{simulacionSha256:ofertaSha256,acepta:true}:{}),
      ...(diaPago!==undefined?{diaPago}:{})},crypto.randomUUID());
    if(this.acceso!==grant)fail();
    return validarCotizacionTitular(p,sid,this.responseNow);
  }
  async simular(sid:string,escenario?:EscenarioLocal):Promise<SimulacionLocal>{
    if(!this.acceso)throw new AccesoPublicoError(401);
    if(!uuid.test(sid)||escenario&&(!['monto plazoMeses','linea monto plazoMeses'].includes(Object.keys(escenario).sort().join(' '))
        ||escenario.linea!==undefined&&!['VENTANILLA','LIBRANZA'].includes(escenario.linea)
        ||typeof escenario.monto!=='string'||!/^\d{1,10}(\.\d{1,2})?$/.test(escenario.monto)
        ||Number(escenario.monto)<=0||!Number.isInteger(escenario.plazoMeses)||escenario.plazoMeses<1||escenario.plazoMeses>48))fail();
    const grant=this.acceso,rid=crypto.randomUUID();
    const p=await this.request('cotizacion/',{solicitudId:sid,operacion:'simular',...(escenario?{escenario}:{})},rid);
    if(this.acceso!==grant)fail();
    return validarSimulacionLocal(p,sid,rid,escenario);
  }
  async simularBorrador(borradorId:string,escenario?:EscenarioLocal):Promise<SimulacionBorrador>{
    if(!this.acceso)throw new AccesoPublicoError(401);
    if(!uuid.test(borradorId))fail();
    const grant=this.acceso,rid=crypto.randomUUID();
    const p=obj(await this.request('cotizacion/',{borradorId,operacion:'simular',...(escenario?{escenario}:{})},rid));
    if(this.acceso!==grant||p.borradorId!==borradorId||'solicitudId' in p)fail();
    const {borradorId:reference,...body}=p;
    const {solicitudId:unused,...validated}=validarSimulacionLocal({...body,solicitudId:reference},borradorId,rid,escenario);
    return {...validated,borradorId:reference as string};
  }
  async borrador(body:GuardarBorrador|{operacion:'borrador';accion:'retomar';actividad?:boolean}):Promise<EstadoBorrador>{
    if(!this.acceso)throw new AccesoPublicoError(401);
    const expected=this.acceso,p=obj(await this.request('solicitudes/',body));
    if(this.acceso!==expected||p.sesionReferencia!==expected)fail();
    const draft=validarBorrador(p.borrador);
    const n=obj(p.avisoTemporal);
    if(Object.keys(n).sort().join(',')!=='inactividadSegundos,presentacion,sha256,texto,version'
      ||n.inactividadSegundos!==3600||!/^[a-f0-9]{64}$/.test(text(n.sha256,64))
      ||!text(n.version,80)||!text(n.texto,4096)||!text(n.presentacion,2048)
      ||(draft&&!('venceAt' in draft)))fail();
    if(body.accion!=='retomar'&&(!draft||draft.referencia!==body.clave
      ||draft.fase!==(body.accion==='enviar'?'ENVIADA':'BORRADOR')))fail();
    return {borrador:draft,avisoTemporal:n as unknown as AvisoTemporal};
  }
  async iniciar(data:DatosAcceso,rid:string){
    if(!uuid.test(rid))fail();
    const p=obj(await this.request('acceso/',data,rid));
    if(p.versionContrato!==version||p.intento!==rid||p.estado!=='VERIFICACION_SOLICITADA')fail();
  }
  async verificar(intento:string,codigo:string){
    if(!uuid.test(intento)||!/^\d{6}$/.test(codigo))fail();
    const p=obj(await this.request('verificar/',{intento,codigo}));
    if(p.versionContrato!==version||p.autenticado!==true)fail();
  }
  async estadoIntento(intento:string):Promise<IntentoPublico>{
    if(!uuid.test(intento))fail();
    const p=obj(await this.request('acceso/?intento='+encodeURIComponent(intento)));
    if(p.versionContrato!==version||p.intento!==intento
        ||!['PREPARANDO','CONTACTO_DISPONIBLE','ENVIO_PENDIENTE','CODIGO_ENVIADO'].includes(text(p.estado)))fail();
    if(p.estado==='PREPARANDO'){
      if(p.contactoEnmascarado!==null||p.canal!==null||p.expiraAt!==null)fail();
    }else if(!['SMS','EMAIL'].includes(text(p.canal))
        ||!(p.canal==='SMS'?/^\*\*\*[0-9]{2}$/:/^\*\*\*@\*\*\*$/).test(text(p.contactoEnmascarado))
        ||!Number.isFinite(Date.parse(text(p.expiraAt))))fail();
    return {intento,estado:p.estado as IntentoPublico['estado'],canal:p.canal as IntentoPublico['canal'],
      contactoEnmascarado:p.contactoEnmascarado as string|null,expiraAt:p.expiraAt as string|null};
  }
  async confirmarEnvio(intento:string,canal:'SMS'|'EMAIL'){
    if(!uuid.test(intento))fail();
    const p=obj(await this.request('acceso/',{intento,canal}));
    if(p.versionContrato!==version||p.intento!==intento||p.estado!=='ENVIO_SOLICITADO')fail();
  }
  async cancelarIntento(intento:string){
    if(!uuid.test(intento))fail();
    const p=obj(await this.request('acceso/',{intento,cancelar:true}));
    if(p.versionContrato!==version||p.intento!==intento||p.estado!=='CANCELADO')fail();
  }
  async solicitudes():Promise<SolicitudPublica[]>{
    const grant=this.acceso;if(!grant)fail();
    const p=obj(await this.request('solicitudes/'));
    if(p.sesionReferencia!==grant||grant!==this.acceso||!Array.isArray(p.solicitudes)||p.solicitudes.length>20)fail();
    const rows=(p.solicitudes as unknown[]).map(row=>validarSolicitudPublica(row,this.responseNow));
    if(new Set(rows.map(r=>r.referencia)).size!==rows.length)fail();return rows;
  }
  async salir(){await this.request('salir/',{});this.invalidarSesion();}
}
