import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,BadgeCheck,Calculator,CheckCircle2,ChevronDown,ChevronRight,Menu,
  Clock3,ExternalLink,FileCheck2,Files,History,Info,LayoutDashboard,LogOut,RefreshCw,ShieldCheck,UserRound,type LucideIcon} from 'lucide-react';
import {CredihoyAccesoAsociado,type SeguimientoAsociadoProps} from '../components/CredihoyAccesoAsociado';
import {CredihoyShell} from '../components/CredihoyShell';
import {crearEstadoInicial} from '../state';
import {moneda} from '../components/FormControls';
import type {CotizacionTitular,InstitucionalPublica,SolicitudPublica,AutorizacionTitular,PeticionAutorizacion,PeticionRadicacion,RadicacionTitular,EventoHistorial,ContractualStaging,PeticionContractual} from '../services/publico';
import {PdfScrollViewer} from '../../../components/PdfScrollViewer';
import {Aviso,Cargando,EstadoVacio,Etiqueta,NotaPrivacidad} from './ui';
import './asociado.css';
import {SimuladorLocal} from './SimuladorLocal';
import {resolverSiguienteAccion} from './siguienteAccion';

type Vista='estado'|'simulador'|'solicitud'|'autorizaciones'|'evaluacion'|'firma'|'historial';
const ESTADOS_ESTUDIO={CUMPLE:'Cumple',NO_CUMPLE:'No cumple',NO_EVALUABLE:'Datos pendientes',
  NO_APLICA:'No aplica',PENDIENTE_POLITICA:'Politica pendiente'};
const MOTIVOS_ESTUDIO:Record<string,string>={
  CAPACIDAD_PRELIMINAR_NO_DECISORIA_DEUDA_INCOMPLETA:'Falta conciliar el endeudamiento completo. La capacidad preliminar no permite decidir.',
  ACTUALIZACION_DATOS_FUERA_VIGENCIA:'Debes actualizar tus datos institucionales con Congente.',
  CAPACIDAD_PAGO_NO_DISPONIBLE:'La capacidad definitiva requiere informacion adicional.'};
function motivoEstudio(value:string){return MOTIVOS_ESTUDIO[value]??'Esta validacion requiere revision de Congente.';}
function motivoDecision(codigo:string,regla:string|null){
  if(regla==='UNICO_FILA_9')return 'Debes actualizar tus datos con Congente.';
  if(codigo==='CLASIFICACION_INSTITUCIONAL_PENDIENTE')return 'Congente debe verificar el estamento que corresponde a tu solicitud.';
  if(codigo==='REVISION_ESTAMENTO_INSTITUCIONAL')return 'La decision institucional sigue pendiente.';
  if(codigo==='CAPACIDAD_NO_EVALUABLE')return 'Falta informacion acreditada para decidir sobre tu capacidad de pago.';
  if(codigo==='REGLA_EXCLUYENTE_NO_CUMPLE')return 'Tu solicitud no cumple una condicion obligatoria del credito.';
  return 'Congente debe revisar una condicion pendiente de tu solicitud.';
}
export const VISTAS:{codigo:Vista;nombre:string;icono:LucideIcon;descripcion:string}[]=[
  {codigo:'estado',nombre:'Mi solicitud',icono:LayoutDashboard,descripcion:'El resumen de tu solicitud y los pasos que siguen.'},
  {codigo:'solicitud',nombre:'Mis datos',icono:UserRound,descripcion:'Tu identidad y la informacion guardada de tu solicitud.'},
  {codigo:'simulador',nombre:'Simulacion',icono:Calculator,descripcion:'Las condiciones disponibles para tu solicitud.'},
  {codigo:'autorizaciones',nombre:'Autorizaciones',icono:ShieldCheck,descripcion:'Cada autorizacion tiene un proposito diferente.'},
  {codigo:'evaluacion',nombre:'Resultado',icono:BadgeCheck,descripcion:'La decision institucional y las validaciones pendientes.'},
  {codigo:'firma',nombre:'Documentos y firma',icono:Files,descripcion:'Documentos contractuales del canal.'},
  {codigo:'historial',nombre:'Historial',icono:History,descripcion:'Los hechos registrados de tu solicitud.'},
];
export function siguienteVista(vista:Vista,direccion:1|-1):Vista{
  const index=VISTAS.findIndex(v=>v.codigo===vista);
  return VISTAS[Math.max(0,Math.min(VISTAS.length-1,index+direccion))].codigo;
}
export function fechaPublica(value:string){
  return new Date(/^\d{4}-\d{2}-\d{2}$/.test(value)?value+'T12:00:00-05:00':value)
    .toLocaleDateString('es-CO',{timeZone:'America/Bogota'});
}
export function fechaHoraPublica(value:string){
  return /^\d{4}-\d{2}-\d{2}$/.test(value)?fechaPublica(value):new Date(value).toLocaleString('es-CO',
    {timeZone:'America/Bogota',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
export function textoEventoHistorial(event:EventoHistorial){
  if(event.codigo==='FORMALIZACION')return {titulo:'Formalizacion contractual',
    descripcion:event.estado==='CONFIRMADO'?'Documento contractual firmado y verificado.':'La firma de COLO-FO.001 sigue pendiente.'};
  if(event.codigo==='PAGARE')return {titulo:'Pagare',descripcion:'El pagare se gestiona por el canal institucional en su oportunidad.'};
  return {titulo:event.titulo,descripcion:event.descripcion};
}
const INCIDENCIAS={NO_DISPONIBLE:'No hay informacion de incidencias disponible al corte.',
  EN_REVISION:'Congente esta revisando una incidencia del tramite.',
  ANTECEDENTES_SIN_PENDIENTE_ACTIVO:'El tramite conserva antecedentes de revision, sin incidencias activas registradas al corte.',
  SIN_INCIDENCIAS_REGISTRADAS:'No hay incidencias registradas al corte.'};

function Pendientes({items}:{items:string[]}){
  return items.length>0?<ul className="ca-pending">{items.map(p=><li key={p}>
    <Clock3 size={17} aria-hidden="true"/><span>{p}</span></li>)}</ul>:
    <p className="ca-muted">No hay requisitos pendientes informados en esta consulta.</p>;
}

export function ContextoCuota({laboratorio=typeof document!=='undefined'&&Boolean(document.querySelector('[data-testid="demo-sintetica"]'))}:{laboratorio?:boolean}={}){
  return <small className="ca-quote-context">{laboratorio?'Dato sintetico de laboratorio. ':''}Referencia informativa, no es una cuota contractual.</small>;
}

export function ProgresoSolicitud({solicitud}:{solicitud:SolicitudPublica}){
  const institucion=solicitud.institucional;
  const etapas=[
    {titulo:'Solicitud registrada',detalle:fechaPublica(solicitud.fechaCreacion),confirmada:true},
    {titulo:'Cuota y condiciones',detalle:solicitud.seguimientoRadicacion?.cuotaAceptada?'Aceptadas personalmente':'Pendientes',confirmada:Boolean(solicitud.seguimientoRadicacion?.cuotaAceptada)},
    {titulo:'Radicacion',detalle:solicitud.seguimientoRadicacion?.confirmada?'Confirmada; en revision institucional':'Pendiente de confirmacion',confirmada:solicitud.seguimientoRadicacion?.confirmada===true},
    {titulo:'Decision institucional',detalle:institucion?'Aprobacion registrada':'Sin resultado acreditado',confirmada:Boolean(institucion)},
    {titulo:'Documentos y firma',detalle:solicitud.formalizacionDocumental?.firmaAcreditada?'Documento verificado':'Preparacion documental pendiente',confirmada:solicitud.formalizacionDocumental?.firmaAcreditada===true},
  ];
  return <ol className="ca-credit-progress" aria-label="Progreso acreditado de la solicitud">{etapas.map((etapa,i)=><li
    key={etapa.titulo} className={etapa.confirmada?'is-confirmed':'is-pending'}>
    <span className="ca-progress-mark">{etapa.confirmada?<CheckCircle2 size={18} aria-hidden="true"/>:<Clock3 size={18} aria-hidden="true"/>}</span>
    <div><span className="ca-progress-label">{i+1} · {etapa.confirmada?'Confirmado':'Pendiente'}</span><h4>{etapa.titulo}</h4><p>{etapa.detalle}</p></div>
  </li>)}</ol>;
}

export function EstadoContractual({estado}:{estado:string}){
  const textos:Record<string,string>={
    PRESENTADA:'Documento disponible para tu revision.',
    ACEPTADA_PENDIENTE_OTP:'Documento aceptado. Falta verificar el codigo contractual.',
    OTP_ENVIO_RESERVADO:'Estamos confirmando el envio de tu codigo. Consulta el estado sin solicitar otro.',
    OTP_ENVIADA:'Codigo contractual enviado. Ingresalo para verificarlo.',
    OTP_ENVIO_INDETERMINADO:'El envio del codigo esta pendiente de confirmacion. No solicites otro.',
    OTP_COMPROBACION_RESERVADA:'Estamos confirmando la verificacion de tu codigo.',
    OTP_VALIDADA:'Codigo contractual verificado. La firma aun requiere tu confirmacion personal.',
    OTP_BLOQUEADA:'La verificacion del codigo quedo bloqueada. Comunicate con Congente.',
    OTP_EXPIRADA:'El codigo contractual vencio. No acredita una firma.',
    OTP_COMPROBACION_INDETERMINADA:'La verificacion del codigo esta pendiente de confirmacion.',
    ENVIANDO:'Estamos confirmando el envio de tu documento. No vuelvas a firmar ni solicites otro codigo.',
    INDETERMINADA:'El envio de tu documento esta pendiente de confirmacion. No vuelvas a firmar ni solicites otro codigo.',
    FIRMA_REPORTADA_NO_VERIFICADA:'Respuesta de firma recibida. El documento y su evidencia aun deben verificarse.',
    FALLO_DEFINITIVO_DOCUMENTADO:'El servicio informo que la firma no se completo. Congente debe revisar el resultado antes de otro intento.',
    CONCILIADA_PROCESADA:'El servicio confirmo el procesamiento. La recuperacion y verificacion documental siguen pendientes.',
    CONCILIADA_NO_PROCESADA:'Se confirmo que este envio no fue procesado. Otro intento requiere habilitacion y un nuevo acto personal.'};
  return <p role="status">{textos[estado]??'La formalizacion esta pendiente de revision. Consulta su estado sin repetir el envio.'}</p>;
}

export function FirmaContractualStaging({solicitud,operar}:{solicitud:SolicitudPublica;operar:(body:PeticionContractual)=>Promise<ContractualStaging>}){
  const prueba=solicitud.formalizacionStaging?.documento==='PRUEBA-FIRMA-STAGING';
  const paginas=solicitud.formalizacionStaging?.paginas??3;
  const storage='credihoy_contractual_acto:'+solicitud.referencia+(prueba?':prueba':'');
  const [clave,setClave]=useState(()=>{
    try{const old=sessionStorage.getItem(storage);if(old&&/^[a-f0-9-]{36}$/.test(old))return old;
      const id=crypto.randomUUID();sessionStorage.setItem(storage,id);return id;}catch{return crypto.randomUUID();}});
  const [data,setData]=useState<ContractualStaging|null>(null),[documento,setDocumento]=useState<ContractualStaging['documento']>(null);
  const [url,setUrl]=useState(''),[firmadoUrl,setFirmadoUrl]=useState(''),[leido,setLeido]=useState(false),[acepta,setAcepta]=useState(false),[confirma,setConfirma]=useState(false);
  const [codigo,setCodigo]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[incierta,setIncierta]=useState(false);
  const guard=useRef(false);
  const pdfFile=useMemo(()=>documento?{data:Uint8Array.from(atob(documento.pdfBase64),v=>v.charCodeAt(0))}:null,[documento]);
  useEffect(()=>{
    if(!documento){setUrl('');return;}
    const bytes=Uint8Array.from(atob(documento.pdfBase64),v=>v.charCodeAt(0));
    const next=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));setUrl(next);
    return()=>URL.revokeObjectURL(next);
  },[documento]);
  useEffect(()=>{
    if(!data?.firmado){setFirmadoUrl('');return;}
    const bytes=Uint8Array.from(atob(data.firmado.pdfBase64),v=>v.charCodeAt(0));
    const next=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));setFirmadoUrl(next);
    return()=>URL.revokeObjectURL(next);
  },[data?.firmado]);
  async function enviar(action:Omit<PeticionContractual,'operacion'|'solicitudId'|'clave'>){
    if(guard.current)return;guard.current=true;setBusy(true);setError('');
    try{const result=await operar({operacion:'autorizacion',solicitudId:solicitud.referencia,clave,...action} as PeticionContractual);
      setData(result);if(result.documento)setDocumento(result.documento);setIncierta(false);
    }catch{setIncierta(true);setError('No pudimos confirmar el resultado. Consulta el estado; no repitas el envio ni solicites otro codigo.');}
    finally{setCodigo('');guard.current=false;setBusy(false);}
  }
  const estado=data?.estado;
  const revisionVencida=Boolean(data?.pendientes.includes('REVISION_PERSONAL_CONTRACTUAL_VENCIDA'));
  const otpCerrado=Boolean(data?.pendientes.includes('TRANSPORTE_OTP_CONTRACTUAL_NO_HABILITADO'));
  const firmaCerrada=Boolean(data?.pendientes.includes('TRANSPORTE_FIRMA_STAGING_NO_HABILITADO'));
  function revisarNuevamente(){
    if(!revisionVencida||busy)return;
    const next=crypto.randomUUID();
    try{sessionStorage.setItem(storage,next);}catch{return;}
    setClave(next);setData(null);setDocumento(null);setAcepta(false);setLeido(false);setConfirma(false);setCodigo('');setError('');setIncierta(false);
  }
  return <section className="ca-document-band" aria-label="Formalizacion contractual STAGING" aria-busy={busy}>
    <h3>{prueba?'Prueba independiente de firma':'Formalizacion contractual'}</h3>
    <Aviso tono="warning" titulo="Validacion de firma en STAGING">
      {prueba?'Este documento se firma exclusivamente para validar la integracion. No formaliza tu credito ni sustituye el envio anterior.':
        'Este recorrido valida el servicio de firma en un ambiente de ensayo. No acredita formalizacion productiva, aprobacion institucional ni autoriza desembolsos. El documento oficial no contiene marcas de prueba.'}
    </Aviso>
    <dl className="ca-authorizations"><div><dt>Documento</dt><dd>{prueba?`Documento independiente, ${paginas} pagina${paginas===1?'':'s'}`:'COLO-FO.001, version 13, tres paginas'}</dd></div>
      {!prueba&&<><div><dt>Monto</dt><dd>{moneda(solicitud.monto)}</dd></div><div><dt>Plazo</dt><dd>{solicitud.plazoMeses} meses</dd></div>
      <div><dt>Cuota aceptada</dt><dd>{solicitud.seguimientoRadicacion?.cuotaAceptada?moneda(solicitud.seguimientoRadicacion.cuotaAceptada):'Sin lectura acreditada'}</dd></div></>}</dl>
    {!data&&!incierta&&<button type="button" className="btn btn-primary" disabled={busy} onClick={()=>void enviar({accion:'contractual_presentar'})}>
      <FileCheck2 size={17} aria-hidden="true"/>{prueba?'Revisar documento de prueba':'Revisar y firmar formalizacion'}</button>}
    {url&&pdfFile&&<><PdfScrollViewer file={pdfFile} requiredPages={paginas} title={prueba?'Documento independiente de prueba':'COLO-FO.001 v13'} onLoad={()=>{setLeido(false);setAcepta(false);}} onReachEnd={()=>setLeido(true)}/>
      <p className="ca-muted" style={{overflowWrap:'anywhere'}}>SHA-256: {documento?.pdfSha256}</p>
      <a className="btn btn-outline-primary" href={url} download={prueba?'documento-prueba-independiente.pdf':'COLO-FO.001-v13.pdf'}><ExternalLink size={16} aria-hidden="true"/>Abrir documento completo</a></>}
    {revisionVencida&&<><p role="status">La aceptacion anterior se conserva. Para continuar se requiere una nueva revision y aceptacion personal del documento.</p>
      <button type="button" className="btn btn-primary" disabled={busy} onClick={revisarNuevamente}><RefreshCw size={17} aria-hidden="true"/>Revisar documento nuevamente</button></>}
    {estado==='PRESENTADA'&&!incierta&&!revisionVencida&&<>
      <label className="ca-check"><input type="checkbox" checked={acepta} disabled={busy||!leido} onChange={e=>setAcepta(e.target.checked)}/>
        {prueba?'He revisado el documento y acepto expresamente firmarlo como prueba independiente en STAGING.':'He revisado las tres paginas y acepto expresamente el contenido de este documento.'}</label>
      <button className="btn btn-primary" type="button" disabled={busy||!leido||!acepta||!documento} onClick={()=>void enviar({accion:'contractual_aceptar',presentacion:documento!.presentacion,acepta:true} as PeticionContractual)}>
        <CheckCircle2 size={17} aria-hidden="true"/>Aceptar documento</button></>}
    {otpCerrado&&<p role="status">El envio del codigo contractual no esta habilitado.</p>}
    {estado==='ACEPTADA_PENDIENTE_OTP'&&!incierta&&!revisionVencida&&!otpCerrado&&<button type="button" className="btn btn-primary" disabled={busy} onClick={()=>void enviar({accion:'contractual_enviar_otp'})}>
      <ShieldCheck size={17} aria-hidden="true"/>Solicitar codigo contractual</button>}
    {estado==='OTP_ENVIADA'&&!incierta&&<form onSubmit={e=>{e.preventDefault();void enviar({accion:'contractual_comprobar_otp',codigo,comprobacionId:crypto.randomUUID()} as PeticionContractual);}}>
      <label className="ca-field">Codigo contractual<input className="form-control" inputMode="numeric" autoComplete="one-time-code" value={codigo} maxLength={6} disabled={busy}
        onChange={e=>setCodigo(e.target.value.replace(/\D/g,''))}/></label>
      <button type="submit" className="btn btn-primary" disabled={busy||!/^\d{6}$/.test(codigo)}><ShieldCheck size={17} aria-hidden="true"/>Verificar codigo</button></form>}
    {estado==='OTP_VALIDADA'&&!incierta&&!firmaCerrada&&<>
      <label className="ca-check"><input type="checkbox" checked={confirma} disabled={busy} onChange={e=>setConfirma(e.target.checked)}/>
        Confirmo personalmente la firma electronica de este documento en STAGING.</label>
      <button type="button" className="btn btn-primary" disabled={busy||!confirma} onClick={()=>void enviar({accion:'contractual_firmar',confirmaFirma:true} as PeticionContractual)}>
        <FileCheck2 size={17} aria-hidden="true"/>Confirmar firma</button></>}
    {data&&<EstadoContractual estado={data.estado}/>}
    {(incierta||data&&['ENVIANDO','INDETERMINADA','FIRMA_REPORTADA_NO_VERIFICADA','FALLO_DEFINITIVO_DOCUMENTADO',
      'CONCILIADA_PROCESADA','CONCILIADA_NO_PROCESADA'].includes(data.estado))&&
      <button type="button" className="btn btn-outline-primary" disabled={busy} onClick={()=>void enviar({accion:'contractual_consultar'})}>
        <RefreshCw size={16} aria-hidden="true"/>Consultar estado sin reenviar</button>}
    {data?.pendientes.some(p=>!['REVISION_PERSONAL_CONTRACTUAL_VENCIDA','TRANSPORTE_OTP_CONTRACTUAL_NO_HABILITADO',
      'TRANSPORTE_FIRMA_STAGING_NO_HABILITADO','RESPUESTA_PROVEEDOR_INCIERTA_NO_REENVIAR',
      'FIRMA_RECHAZADA_DOCUMENTADA_NO_REENVIAR','FIRMA_NO_PROCESADA_CONCILIADA_NO_REENVIAR'].includes(p))?
      <p role="status">La evidencia se conserva. La verificacion documental independiente sigue pendiente; no se acredita una firma productiva.</p>:null}
    {firmadoUrl&&<a className="btn btn-outline-primary" href={firmadoUrl} download={prueba?'prueba-firma-recuperada-STAGING.pdf':'COLO-FO.001-recuperado-STAGING.pdf'}>
      <ExternalLink size={16} aria-hidden="true"/>Descargar documento recuperado en STAGING</a>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}

export function DocumentosAsociado({formalizacion,staging}:{institucion?:InstitucionalPublica|null;formalizacion?:SolicitudPublica['formalizacionDocumental'];staging?:SolicitudPublica['formalizacionStaging']}){
  return <div className="ca-documents">
    <section className="ca-document-band" aria-labelledby="ca-document-title">
      <div className="ca-band-heading"><span className="ca-icon-tile"><FileCheck2 size={23} aria-hidden="true"/></span>
        <div><p className="ch-eyebrow">Documentos del canal</p><h3 id="ca-document-title">Documentos y firma electronica</h3></div></div>
      <dl className="ca-authorizations">
        <div><dt>Documento contractual</dt><dd>{formalizacion?.documento??staging?.documento??'Preparacion documental pendiente.'}</dd></div>
        <div><dt>Firma electronica</dt><dd>{formalizacion?.firmaAcreditada?'Documento firmado y verificado.':staging?'Recorrido STAGING disponible. La firma productiva sigue pendiente.':formalizacion?'Validacion documental pendiente.':'No hay un documento contractual disponible para firmar.'}</dd></div>
      </dl>
      <p className="ca-notice">La formalizacion de COLO-FO.001 utiliza firma electronica. El pagare se gestiona por el canal institucional en su oportunidad.</p>
    </section>
  </div>;
}

export function EspacioAsociado({solicitudes,busy,nombre,actualizar,salir,seguimientoHabilitado=true,formularioBorrador,cotizacionHabilitada=false,cotizar,radicacionHabilitada=false,radicar,horaServidor,autorizacionHabilitada=false,centralesHabilitadas=false,autorizar,simuladorLocalHabilitado=false,simular,contractual}:SeguimientoAsociadoProps){
  const [vista,setVista]=useState<Vista>('estado'),[seleccion,setSeleccion]=useState('');
  const [escritorio,setEscritorio]=useState(false),[menuAbierto,setMenuAbierto]=useState(false);
  const [autorizacionesVisitadas,setAutorizacionesVisitadas]=useState(false);
  const [edicionBorrador,setEdicionBorrador]=useState(false);
  const inicioPersonal=useRef(false);
  const heading=useRef<HTMLHeadingElement>(null),navigation=useRef<HTMLElement>(null);
  const s=solicitudes?.find(v=>v.referencia===seleccion)??solicitudes?.[0];
  const refreshStudy=useRef(actualizar),readingStudy=useRef(busy);
  refreshStudy.current=actualizar;readingStudy.current=busy;
  const reviewReads=useRef({sid:'',count:0,last:0});
  const esperaRevision=Boolean(s?.estudio&&(s.estudio.decision==='REQUIERE_SUBSANACION'
    ||s.estudio.decisionPolitica?.estado==='EN_REVISION'));
  useEffect(()=>{
    if(!s||!esperaRevision||!seguimientoHabilitado||!['estado','evaluacion'].includes(vista))return;
    if(reviewReads.current.sid!==s.referencia)reviewReads.current={sid:s.referencia,count:0,last:Date.now()};
    const read=()=>{
      const state=reviewReads.current,now=Date.now();
      if(document.visibilityState!=='visible'||readingStudy.current||state.count>=10||now-state.last<90000)return;
      state.last=now;state.count++;refreshStudy.current();
    };
    const timer=setInterval(read,90000);
    document.addEventListener('visibilitychange',read);window.addEventListener('focus',read);
    return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',read);window.removeEventListener('focus',read);};
  },[s?.referencia,esperaRevision,seguimientoHabilitado,vista]);
  const [acto,setActo]=useState<{sid:string;data:AutorizacionTitular|null}|null>(null);
  const [consultandoActo,setConsultandoActo]=useState(false);
  const leerActo=useRef(autorizar);leerActo.current=autorizar;
  const puedeLeerActo=Boolean(autorizar&&autorizacionHabilitada);
  useEffect(()=>{
    let vigente=true;
    setActo(null);
    const consultar=leerActo.current;
    if(!s||s.estudio||s.institucional||!consultar||!puedeLeerActo){setConsultandoActo(false);return;}
    setConsultandoActo(true);
    void consultar({operacion:'autorizacion',accion:'consultar',solicitudId:s.referencia}).then(async data=>{
      if(!vigente)return;
      if(!['AUTORIZACION_AUSENTE','PENDIENTE_ACEPTACION_PERSONAL'].includes(data.estado)&&centralesHabilitadas){
        data=await consultar({operacion:'autorizacion',accion:'centrales_consultar',solicitudId:s.referencia});
      }
      if(vigente)setActo({sid:s.referencia,data});
    }).catch(()=>{if(vigente)setActo({sid:s.referencia,data:null});})
      .finally(()=>{if(vigente)setConsultandoActo(false);});
    return()=>{vigente=false;};
  },[s?.referencia,s?.estudio?.referenciaVersion,puedeLeerActo,centralesHabilitadas]);
  const siguiente=s?resolverSiguienteAccion(s,acto?.sid===s.referencia?acto.data:null,consultandoActo):null;
  const quote=s?.simulacion,institucion=s?.institucional;
  const radicada=s?.seguimientoRadicacion?.confirmada===true;
  const estudioFavorable=s?.estudio?.decisionPolitica?.continuidad?.estudioFavorable===true;
  const puedeConsultarCuota=Boolean(cotizacionHabilitada&&cotizar&&s?.procedencia?.origen==='FUENTE_OPERATIVA');
  const editando=solicitudes!==null&&edicionBorrador&&!s?.envio;
  useEffect(()=>{if(s?.envio)setEdicionBorrador(false);},[s?.envio]);
  useEffect(()=>{
    if(!inicioPersonal.current&&solicitudes?.length===0&&formularioBorrador&&!busy){
      inicioPersonal.current=true;setEdicionBorrador(true);setVista('solicitud');
    }
  },[solicitudes?.length,Boolean(formularioBorrador),busy]);
  const index=VISTAS.findIndex(v=>v.codigo===vista),actual=VISTAS[index];
  useEffect(()=>{
    const media=window.matchMedia('(min-width:1000px)');
    const actualizar=()=>{setEscritorio(media.matches);setMenuAbierto(false);};
    actualizar();media.addEventListener('change',actualizar);
    return()=>media.removeEventListener('change',actualizar);
  },[]);
  useEffect(()=>{
    heading.current?.focus({preventScroll:true});
    navigation.current?.querySelector('[aria-current="page"]')?.scrollIntoView({block:'nearest',inline:'nearest'});
  },[vista,seleccion]);
  useEffect(()=>{if(vista==='autorizaciones')setAutorizacionesVisitadas(true);},[vista]);
  return <div className="ca-workspace" data-testid="espacio-asociado">
    <div className="ca-account"><div className="ca-greeting"><span className="ca-avatar" aria-hidden="true"><UserRound size={22}/></span>
      <div><p>Hola{nombre?', '+nombre:''}.</p><span><ShieldCheck size={13} aria-hidden="true"/> Identidad verificada</span></div></div>
      <button className="btn btn-outline-secondary ca-logout" title="Cerrar sesion" aria-label="Cerrar sesion" disabled={busy} onClick={salir}><LogOut size={16} aria-hidden="true"/><span>Cerrar sesion</span></button>
    </div>
    <div className={'ca-layout'+(!s||editando?' ca-layout--originacion':'')}>
      {s&&!editando&&<aside className="ca-navigation"><p className="ca-nav-label">Tu credito</p>
        <details className="ca-menu" open={escritorio||menuAbierto} onToggle={e=>{if(!escritorio)setMenuAbierto(e.currentTarget.open);}}>
        <summary className="ca-mobile-menu-label"><Menu size={19} aria-hidden="true"/><span>{actual.nombre}<small>Ver todas las secciones</small></span><ChevronDown size={17} aria-hidden="true"/></summary>
        <nav ref={navigation} className="ca-tabs" aria-label="Tu credito">
          {VISTAS.map(v=><button key={v.codigo} type="button" aria-current={vista===v.codigo?'page':undefined}
            disabled={!s&&v.codigo!=='estado'&&!(v.codigo==='solicitud'&&formularioBorrador)}
            onClick={()=>{setVista(v.codigo);setMenuAbierto(false);}}><v.icono size={18} aria-hidden="true"/><span>{v.nombre}</span>
            <ChevronRight className="ca-nav-arrow" size={15} aria-hidden="true"/></button>)}
        </nav>
        </details>
        <p className="ca-nav-footer"><ShieldCheck size={16} aria-hidden="true"/>Tu informacion es personal.</p>
      </aside>}
      <section className="ca-content" aria-busy={busy}>
        <div className="ca-view-heading"><div><p className="ch-eyebrow">CrediHoy Unico</p>
          <h2 ref={heading} tabIndex={-1}>{s&&!editando?(vista==='estado'?'¡Continuemos con tu CrediHoy!':actual.nombre):'Tu solicitud CrediHoy'}</h2>{s&&!editando&&vista!=='estado'&&<p>{actual.descripcion}</p>}</div>
          {(solicitudes?.length??0)>1&&<label className="ca-field ca-request-selector">Solicitud a consultar
            <select className="form-select" value={s?.referencia} disabled={busy} onChange={e=>setSeleccion(e.target.value)}>
              {solicitudes!.map(v=><option key={v.referencia} value={v.referencia}>{moneda(v.monto)} - {fechaPublica(v.fechaCreacion)}</option>)}</select></label>}
        </div>
        {formularioBorrador&&solicitudes!==null&&!s?.envio&&vista==='estado'&&<button className="btn btn-primary" type="button" disabled={busy} onClick={()=>{setEdicionBorrador(true);setVista('solicitud');}}>
          <UserRound size={17} aria-hidden="true"/>{s?'Crear o retomar mi borrador':'Solicitar mi CrediHoy'}</button>}
        {vista==='solicitud'&&editando&&formularioBorrador}
        {s?.procedencia&&!editando&&<p className="ca-notice" role="status">
          {s.procedencia.origen==='HISTORICO_PILOTO'?'Solicitud anterior para consulta':'Informacion de tu solicitud'}.
          {' Datos actualizados el '+fechaPublica(s.procedencia.actualizadoAt)+'. Estado consultado el '+fechaHoraPublica(s.seguimientoRadicacion?.consultadoEn??s.procedencia.consultadoAt)+'.'}
          {s.procedencia.origen==='HISTORICO_PILOTO'&&' Esta lectura no acredita aprobacion, firma del pagare ni avances de otro expediente.'}
        </p>}
        {!seguimientoHabilitado&&<EstadoVacio titulo="Tu acceso esta listo" icono={ShieldCheck}>
          Tu identidad esta verificada. La consulta de solicitudes aun no esta habilitada en este canal.</EstadoVacio>}
        {busy&&<Cargando/>}
        {seguimientoHabilitado&&solicitudes===null&&!busy&&<EstadoVacio titulo="No pudimos consultar tu solicitud en este momento" icono={Info}>
          Tus datos guardados no han cambiado.<br/>
          <button className="btn btn-outline-primary" type="button" disabled={busy} onClick={actualizar}>
            <RefreshCw size={17} aria-hidden="true"/>Reintentar consulta</button></EstadoVacio>}
        {solicitudes?.length===0&&vista!=='solicitud'&&<EstadoVacio titulo="Inicia tu solicitud CrediHoy" icono={Files}>
          {formularioBorrador?'Selecciona Solicitar mi CrediHoy para completar y guardar tu solicitud.':'Tu identidad esta verificada, pero el inicio de solicitudes no esta disponible en esta sesion. Actualiza el estado o contacta a Congente.'}</EstadoVacio>}
        {s&&vista==='estado'&&<div className="ca-dashboard">
          {radicada&&!institucion&&<Aviso tono="success" titulo="Solicitud radicada - En revision institucional">
            ¡Tu solicitud fue radicada exitosamente! Se encuentra en revisión institucional. Te informaremos cuando tengamos una actualización.
          </Aviso>}
          {s.envio&&<section className="ca-document-band" aria-label="Confirmacion de envio"><h3>Recibimos tu solicitud de CrediHoy.</h3>
            <p>Recibida el {fechaPublica(s.envio.confirmadoEn)}. Esta confirmacion no es una aprobacion de credito.</p>
            <p className="ca-muted">Referencia de seguimiento: <strong>{s.referencia.slice(-8).toUpperCase()}</strong></p>
          </section>}
          <div className="ca-credit-overview">
            <article className="ca-credit-card" aria-label="Resumen de tu credito">
              <div className="ca-credit-brand"><span>CrediHoy Unico</span><ShieldCheck size={21} aria-hidden="true"/></div>
              <Etiqueta tono={radicada||institucion?'success':'warning'}>{institucion?'Aprobacion registrada; firma pendiente':radicada?'Solicitud radicada':s.titulo}</Etiqueta>
              <p className="ca-summary-label">Monto solicitado</p><p className="ca-summary-amount">{moneda(s.monto)}<span>COP</span></p>
              <div className="ca-credit-foot"><span>{s.plazoMeses} cuotas · {s.periodicidad}</span><span>Solicitud registrada</span></div>
            </article>
            <section className="ca-next-step" aria-labelledby="ca-next-title">
              <span className="ca-icon-tile ca-icon-tile--green"><ArrowRight size={22} aria-hidden="true"/></span>
              <p className="ch-eyebrow">Lo que sigue</p><h3 id="ca-next-title">Tu siguiente paso</h3><p role="status">{siguiente?.mensaje}</p>
              {siguiente?.estado==='ACCION_PERSONAL'&&siguiente.destino&&autorizar&&
                <button className="btn btn-primary" type="button" disabled={busy} onClick={()=>setVista(siguiente.destino!)}>
                  <ArrowRight size={17} aria-hidden="true"/>Continuar mi solicitud</button>}
              {siguiente?.destino==='evaluacion'&&<button className="btn btn-outline-primary" type="button" disabled={busy} onClick={()=>setVista('evaluacion')}>
                <BadgeCheck size={17} aria-hidden="true"/>Ver mi estudio</button>}
              {estudioFavorable&&puedeConsultarCuota&&<button className="btn btn-primary" type="button" disabled={busy} onClick={()=>setVista('simulador')}>
                <Calculator size={17} aria-hidden="true"/>{radicada?'Consultar cuota aceptada':'Revisar cuota y condiciones'}</button>}
              {s.formalizacionStaging&&contractual&&<button className="btn btn-outline-primary" type="button" disabled={busy} onClick={()=>setVista('firma')}>
                <FileCheck2 size={17} aria-hidden="true"/>Revisar y firmar formalizacion</button>}
            </section>
          </div>
          <section className="ca-conditions" aria-labelledby="ca-conditions-title">
            <div className="ca-section-heading"><h3 id="ca-conditions-title">Condiciones de tu solicitud</h3><span>{s.seguimientoRadicacion?.cuotaAceptada?'Simulacion aceptada; firma contractual pendiente':'Condiciones por confirmar'}</span></div>
            <dl className="ca-condition-grid"><div><dt>Plazo</dt><dd>{s.plazoMeses} meses</dd></div>
              <div><dt>Periodicidad</dt><dd>{s.periodicidad}</dd></div>
              <div><dt>{s.seguimientoRadicacion?.cuotaAceptada?'Cuota aceptada':s.simulacionLocal?'Cuota estimada':quote?.origen==='REFERENCIA_INSTITUCIONAL_REGISTRADA'?'Cuota de referencia':'Cuota simulada'}</dt>
                <dd>{s.seguimientoRadicacion?.cuotaAceptada?moneda(s.seguimientoRadicacion.cuotaAceptada):s.simulacionLocal?.resultado?moneda(s.simulacionLocal.resultado.cuotaEstimada):quote?moneda(quote.cuota):'Por confirmar'}</dd>{!s.seguimientoRadicacion?.cuotaAceptada&&(quote||s.simulacionLocal)&&<ContextoCuota/>}</div>
              <div><dt>Condiciones contractuales</dt><dd className="ca-metric-text">Por confirmar</dd></div></dl>
          </section>
          <section className="ca-progress-section" aria-labelledby="ca-progress-title">
            <div className="ca-section-heading"><h3 id="ca-progress-title">El avance de tu credito</h3>
              <button className="btn btn-link" onClick={()=>setVista('historial')}>Ver historial<History size={16} aria-hidden="true"/></button></div>
            <ProgresoSolicitud solicitud={s}/>
            {institucion&&<p className="ca-cut"><Clock3 size={14} aria-hidden="true"/>Informacion verificada al {fechaPublica(institucion.actualizadoAt)}.</p>}
          </section>
          <div className="ca-review-grid">
            <section className="ca-channel-review" aria-labelledby="ca-review-title">
              <div className="ca-band-heading"><Clock3 size={20} aria-hidden="true"/><h3 id="ca-review-title">Validaciones del canal</h3></div>
              <p className="ca-notice">Se revisan por separado de la decision institucional.</p><Pendientes items={s.pendientes}/>
            </section>
            <section className="ca-decision-note" aria-labelledby="ca-decision-title">
              <div className="ca-band-heading"><BadgeCheck size={20} aria-hidden="true"/><h3 id="ca-decision-title">Decision institucional</h3></div>
              <p>{institucion?'Aprobacion registrada el '+fechaPublica(institucion.fechaDecision)+'.':'Sin resultado institucional acreditado.'}</p>
              <p className="ca-notice">{institucion?'La aprobacion registrada no acredita firma, liquidacion ni desembolso.':'Las validaciones del canal no equivalen a una aprobacion crediticia.'}</p>
            </section>
          </div>
        </div>}
        {s&&vista==='simulador'&&<>
          {simuladorLocalHabilitado&&simular&&(!cotizacionHabilitada||!cotizar)&&s.procedencia?.origen==='FUENTE_OPERATIVA'&&
            <SimuladorLocal key={s.referencia} sid={s.referencia} montoInicial={s.monto} plazoInicial={s.plazoMeses}
              operar={simular} continuar={()=>setVista('evaluacion')}/>}
          {cotizacionHabilitada&&cotizar&&s.procedencia?.origen==='FUENTE_OPERATIVA'&&
            <OfertaTitular key={s.referencia} sid={s.referencia} operar={cotizar} horaServidor={horaServidor}
              radicar={radicacionHabilitada&&!radicada?radicar:undefined} verSeguimiento={()=>{actualizar();setVista('estado');}}/>}
          {(!simuladorLocalHabilitado||!simular||s.procedencia?.origen!=='FUENTE_OPERATIVA')&&(!cotizacionHabilitada||!cotizar||s.procedencia?.origen!=='FUENTE_OPERATIVA')&&<>
          <Etiqueta tono="neutral">{quote?.origen==='REFERENCIA_INSTITUCIONAL_REGISTRADA'?'Referencia institucional':'Condiciones por confirmar'}</Etiqueta>
          <fieldset className="ca-form" disabled={!quote||busy}><legend>Condiciones de tu solicitud</legend>
            <label>Monto solicitado<input className="form-control" inputMode="decimal" value={moneda(s.monto)} readOnly/></label>
            <label>Numero de cuotas<input className="form-control" type="number" value={s.plazoMeses} readOnly/></label>
            <label>Periodicidad<select className="form-select" value="MENSUAL" disabled><option value="MENSUAL">Mensual</option></select></label>
          </fieldset>
          <dl className="ca-metrics ca-metrics--quote"><div><dt>Tasa nominal anual mes vencido</dt><dd>{quote?quote.tasaNominalNamv+' %':'Por confirmar'}</dd></div>
            <div><dt>{quote?.origen==='REFERENCIA_INSTITUCIONAL_REGISTRADA'?'Cuota de referencia':'Cuota simulada'}</dt><dd>{quote?moneda(quote.cuota):'No disponible'}</dd>{quote&&<ContextoCuota/>}</div></dl>
          <Aviso tono="warning" titulo="No es una oferta contractual">{quote?(quote.origen==='REFERENCIA_INSTITUCIONAL_REGISTRADA'?'Referencia institucional registrada; no es una oferta aceptada.':'Simulacion disponible. No constituye aprobacion ni oferta contractual completa.'):
            'No hay una cotizacion vigente disponible para esta sesion. No se ha calculado una cuota nueva.'}</Aviso>
          <dl className="ca-authorizations ca-financial-concepts"><div><dt>Seguro de vida</dt><dd>Por confirmar</dd></div>
            <div><dt>Comisiones y cargos</dt><dd>Por confirmar</dd></div><div><dt>Fechas de pago</dt><dd>Por confirmar</dd></div></dl>
          <p className="ca-notice">El calculo de una nueva cuota no esta habilitado en este acceso.</p>
          </>}
        </>}
        {s&&!editando&&vista==='solicitud'&&<>
          <Etiqueta tono="success">Identidad verificada</Etiqueta>
          {nombre&&<dl className="ca-authorizations"><div><dt>Titular</dt><dd>{nombre}</dd></div>
            {s.envio?.solicitud.datos.email&&<div><dt>Correo propuesto para la solicitud</dt><dd>{s.envio.solicitud.datos.email} · Sin verificacion adicional</dd></div>}</dl>}
          {s&&<fieldset disabled className="ca-form"><legend>{formularioBorrador?'Datos de la solicitud seleccionada':'Informacion guardada'}</legend>
            <label>Monto<input className="form-control" value={moneda(s.monto)} readOnly/></label>
            <label>Plazo en meses<input className="form-control" value={s.plazoMeses} readOnly/></label>
            <label>Periodicidad<input className="form-control" value={s.periodicidad} readOnly/></label></fieldset>}
          {(!formularioBorrador||s)&&<Aviso>{s?.procedencia?.origen==='FUENTE_OPERATIVA'&&autorizacionHabilitada&&autorizar?
            'El envio original de tu solicitud se conserva sin cambios.':formularioBorrador?'La solicitud seleccionada es de solo lectura. Tu borrador personal se guarda por separado.':'La modificacion de tu solicitud no esta habilitada para esta sesion.'}</Aviso>}
          {s.envio?.solicitud.datos.periodicidadPago&&typeof s.envio.solicitud.datos.recogerCredihoy==='boolean'&&<dl className="ca-authorizations">
            <div><dt>Condiciones enviadas</dt><dd>Pago mensual. {s.envio.solicitud.datos.recogerCredihoy?'Solicitaste pagar un credito vigente; su referencia debe verificarse.':'No solicitaste pagar otro credito con esta solicitud.'}</dd></div>
          </dl>}
          {!s.envio?.solicitud.datos.periodicidadPago&&s?.procedencia?.origen==='FUENTE_OPERATIVA'&&autorizacionHabilitada&&autorizar&&
            <ComplementoPersonal key={s.referencia} sid={s.referencia} operar={autorizar}/>}
        </>}
        {(vista==='autorizaciones'||autorizacionesVisitadas)&&<div hidden={vista!=='autorizaciones'}>
          <dl className="ca-authorizations ca-permissions">
            <div><dt><ShieldCheck size={19} aria-hidden="true"/>Aviso de acceso</dt><dd>Registrado durante la verificacion de esta sesion. No sustituye las autorizaciones crediticias.</dd></div>
            <div><dt><BadgeCheck size={19} aria-hidden="true"/>Identidad</dt><dd>Verificada con tu codigo de seguridad.</dd></div>
            <div><dt><FileCheck2 size={19} aria-hidden="true"/>Consulta de informacion crediticia</dt><dd>Debe comprobarse separadamente en tu solicitud. El codigo de acceso no la autoriza.</dd></div>
            <div><dt><Files size={19} aria-hidden="true"/>Condiciones del credito</dt><dd>Sin oferta contractual aceptada disponible en esta vista.</dd></div>
          </dl><Aviso>El acceso personal, la consulta crediticia y la aceptacion de condiciones son actos diferentes.</Aviso>
          {s&&s.procedencia?.origen==='FUENTE_OPERATIVA'&&autorizacionHabilitada&&!centralesHabilitadas&&autorizar&&
            <AutorizacionPersonal key={s.referencia} sid={s.referencia} operar={autorizar} centralesHabilitadas={centralesHabilitadas}/>}
          {s&&s.procedencia?.origen==='FUENTE_OPERATIVA'&&autorizacionHabilitada&&centralesHabilitadas&&autorizar&&
            <AutorizacionesPersonales key={'centrales:'+s.referencia} sid={s.referencia} operar={autorizar} horaServidor={horaServidor} onConfirmada={actualizar}/>}
        </div>}
        {s&&vista==='evaluacion'&&<>
          {s.formalizacionDocumental&&<Aviso tono={s.formalizacionDocumental.firmaAcreditada?'success':'warning'}
            titulo={s.formalizacionDocumental.firmaAcreditada?'Documento contractual verificado':'Validacion documental pendiente'}>
            <p>{s.formalizacionDocumental.firmaAcreditada?
              'Tu documento firmado esta conservado. La continuacion institucional sigue pendiente.':
              'Congente esta conciliando la evidencia documental. No necesitas repetir la firma.'}</p>
          </Aviso>}
          <div className={'ca-result'+(institucion||estudioFavorable?' ca-result--approved':'')}>
            <span className="ca-icon-tile">{institucion||estudioFavorable?<BadgeCheck size={27} aria-hidden="true"/>:<Clock3 size={27} aria-hidden="true"/>}</span>
            <h3>{institucion?'Aprobacion institucional registrada':estudioFavorable?'Estudio digital aprobado':'Resultado institucional por confirmar'}</h3>
            <p>{institucion?'La formalizacion y la firma de tus documentos siguen pendientes.':estudioFavorable?
              radicada?'Tu solicitud ya fue radicada y se encuentra en revision institucional. La aprobacion final del credito sigue pendiente.':
              'Las validaciones del estudio estan cumplidas. La radicacion y la decision final conservan sus propios requisitos.':
              'No hay una decision institucional acreditada disponible en esta consulta.'}</p>
          </div>
          {estudioFavorable&&puedeConsultarCuota&&<div className="ca-bottom-actions"><button className="btn btn-primary" type="button"
            disabled={busy} onClick={()=>setVista(radicada?'estado':'simulador')}><ArrowRight size={17} aria-hidden="true"/>{radicada?'Ver seguimiento de mi solicitud':'Revisar cuota y condiciones'}</button></div>}
          <dl className="ca-metrics"><div><dt>Decision institucional</dt><dd className="ca-metric-text">{institucion?'Aprobada; formalizacion pendiente':estudioFavorable?'Pendiente':'Sin resultado acreditado'}</dd></div>
            <div><dt>Fecha de la decision</dt><dd>{institucion?fechaPublica(institucion.fechaDecision):'No disponible'}</dd></div></dl>
          {institucion&&<p className="ca-notice">{INCIDENCIAS[institucion.incidencias]}</p>}
          <h3 className="ca-section-title">Validaciones del canal</h3><p className="ca-notice">Requisitos cumplidos: {s.requisitosCumplidos??'Por verificar'}. La evaluacion del canal se conserva separada de la decision institucional.</p>
          {s.estudio&&<div className="ca-study">
            <div className="ca-study-heading"><h3 className="ca-section-title">Estudio de tu solicitud</h3>
              <span>Version {s.estudio.version} - {fechaPublica(s.estudio.fechaEvaluacion)}</span></div>
            {s.estudio.decisionPolitica?<Aviso tono={estudioFavorable||s.estudio.decisionPolitica.estado==='APROBADO'?'success':s.estudio.decisionPolitica.estado==='RECHAZADO'?'error':'warning'}
              titulo={estudioFavorable?'Estudio digital aprobado':s.estudio.decisionPolitica.estado==='APROBADO'?'Aprobado por la politica de credito':s.estudio.decisionPolitica.estado==='RECHAZADO'?'Solicitud rechazada por la politica de credito':'Solicitud en revision'}>
              {s.estudio.decisionPolitica.motivos.some(m=>m.codigo!=='REVISION_ESTAMENTO_INSTITUCIONAL')&&<ul>{s.estudio.decisionPolitica.motivos.filter(m=>m.codigo!=='REVISION_ESTAMENTO_INSTITUCIONAL').map((m,i)=><li key={i}>{motivoDecision(m.codigo,m.regla)}</li>)}</ul>}
              <p>La oferta contractual, la formalizacion y el registro institucional conservan sus validaciones pendientes.</p>
            </Aviso>:<Aviso tono="warning" titulo={s.estudio.decision==='REQUIERE_SUBSANACION'?'Hay requisitos por subsanar':'Estudio pendiente de revision'}>
              Los datos incompletos y las validaciones pendientes no son un rechazo definitivo de tu credito.</Aviso>}
            {s.estudio.capacidad.metodologia?<dl className="ca-metrics ca-methodology">
              {([['disponibleAntesGastosFamiliares','Disponible antes de gastos familiares'],
                ['gastosFamiliaresAplicados','Gastos familiares aplicados'],
                ['capacidadDisponible','Capacidad disponible'],['cuotaNuevaEstimada','Cuota nueva estimada'],
                ['residualPosteriorEstimado','Residual posterior estimado']] as const).map(([key,label])=>{
                  const value=s.estudio!.capacidad.metodologia![key];
                  return <div key={key}><dt>{label}</dt><dd>{value==null?'Por determinar':moneda(value)}</dd></div>;
                })}
            </dl>:s.estudio.version>=8?<dl className="ca-metrics">
              <div><dt>Capacidad disponible antes de la cuota nueva</dt><dd data-testid="study-capacity-available">
                {s.estudio.capacidad.capacidadDisponible==null?'Por determinar':moneda(s.estudio.capacidad.capacidadDisponible)}</dd></div>
              <div><dt>Residual despues de la cuota estimada</dt><dd data-testid="study-capacity-residual">
                {s.estudio.capacidad.capacidadResidualPostCuota==null?'Por determinar':moneda(s.estudio.capacidad.capacidadResidualPostCuota)}</dd></div>
            </dl>:<dl className="ca-metrics"><div><dt>{s.estudio.version===7?'Residual del estudio':'Capacidad del estudio'}</dt><dd className="ca-metric-text">
              {s.estudio.capacidad.estado==='EVALUABLE'?moneda(s.estudio.capacidad.valor):'Por determinar'}</dd></div>
              <div><dt>{s.estudio.version===7?'Residual preliminar, no decisorio':'Calculo preliminar, no decisorio'}</dt><dd>{s.estudio.capacidad.preliminarNoDecisoria===null?'No disponible':moneda(s.estudio.capacidad.preliminarNoDecisoria)}</dd></div></dl>}
            <p className="ca-notice">La capacidad preliminar no acredita aprobacion ni una cuota contractual.</p>
            <ol className="ca-study-rules" aria-label="Las 16 reglas del estudio">
              {s.estudio.reglas.map(regla=><li key={regla.codigo}>
                <div className="ca-study-rule-heading"><div><span className="ca-study-rule-code">Fila {regla.codigo.replace('UNICO_FILA_','')}</span>
                  <h4>{regla.nombre}</h4></div>
                  <Etiqueta tono={regla.estadoEvaluacion==='CUMPLE'?'success':regla.estadoEvaluacion==='NO_APLICA'||regla.codigo==='UNICO_FILA_6'?'neutral':'warning'}>
                    {regla.codigo==='UNICO_FILA_6'&&regla.estadoEvaluacion==='NO_CUMPLE'?'Alerta informativa':
                      regla.codigo==='UNICO_FILA_9'&&regla.estadoEvaluacion==='NO_CUMPLE'?'Requiere subsanacion':ESTADOS_ESTUDIO[regla.estadoEvaluacion]}</Etiqueta></div>
                {regla.codigo==='UNICO_FILA_6'&&regla.estadoEvaluacion==='NO_CUMPLE'&&
                  <p className="ca-muted">Se reportaron embargos. Esta condicion es informativa y no implica un rechazo automatico.</p>}
                {(regla.motivos.length>0||regla.datosFaltantes.length>0)&&<details><summary>Detalle de la validacion</summary>
                  {regla.motivos.length>0&&<ul>{regla.motivos.map((motivo,index)=><li key={index}>{motivoEstudio(motivo)}</li>)}</ul>}
                  {regla.datosFaltantes.length>0&&<p>Por acreditar: {regla.datosFaltantes.map(motivoEstudio).join(', ')}.</p>}
                </details>}
              </li>)}
            </ol>
          </div>}
          {!radicada&&<Pendientes items={s.pendientes}/>}
          <Aviso tono={radicada?'success':'neutral'} titulo={radicada?'Radicacion confirmada':'Para continuar'}>
            {radicada?'Tu solicitud se encuentra en revision institucional. No necesitas radicarla nuevamente.':s.siguientePaso}</Aviso>
          {estudioFavorable&&!puedeConsultarCuota&&<Aviso titulo="Condiciones institucionales pendientes">La cuota institucional aun no esta disponible. La radicacion y la firma permanecen pendientes; tu estudio se conserva.</Aviso>}
          {s.estudio?.reglas.some(r=>r.codigo==='UNICO_FILA_9'&&r.estadoEvaluacion==='NO_CUMPLE')&&
            <div className="ca-bottom-actions"><a className="btn btn-outline-primary"
              href="https://consulta.congente.coop:8030/home/verificacion_view/."
              target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
              <ExternalLink size={16} aria-hidden="true"/>Actualiza tus datos para continuar
            </a></div>}
        </>}
        {s&&vista==='firma'&&<><Etiqueta tono={s.formalizacionDocumental?.firmaAcreditada?'success':'warning'}>{s.formalizacionDocumental?.firmaAcreditada?'Documento firmado y verificado':s.formalizacionStaging?'Documento disponible para revision STAGING':s.formalizacionDocumental?'Validacion documental pendiente':'Preparacion documental pendiente'}</Etiqueta>
          <DocumentosAsociado formalizacion={s.formalizacionDocumental} staging={s.formalizacionStaging}/>
          {s.formalizacionStaging&&contractual&&<FirmaContractualStaging key={s.referencia} solicitud={s} operar={contractual}/>}
          {estudioFavorable&&puedeConsultarCuota&&!s.formalizacionDocumental&&<button className="btn btn-outline-primary" type="button"
            disabled={busy} onClick={()=>setVista('simulador')}><Calculator size={17} aria-hidden="true"/>Revisar cuota y condiciones</button>}</>}
        {s&&vista==='historial'&&<>
          {s.historial?<ol className="ca-history">{s.historial.map(event=><li key={s.referencia+event.codigo}>
            <span className={'ca-history-mark'+(event.estado==='CONFIRMADO'?' ca-history-mark--success':'')}>
              {event.estado==='CONFIRMADO'?<CheckCircle2 size={18} aria-hidden="true"/>:<Clock3 size={18} aria-hidden="true"/>}</span>
            <div>{event.fecha?<time dateTime={event.fecha}>{fechaHoraPublica(event.fecha)}</time>:<span className="ca-muted">{event.estado==='CONFIRMADO'?'Fecha por acreditar':'Pendiente'}</span>}
              <h3>{textoEventoHistorial(event).titulo}</h3><p>{textoEventoHistorial(event).descripcion}</p></div></li>)}</ol>:<ol className="ca-history">
            <li><span className="ca-history-mark"><Files size={18} aria-hidden="true"/></span><div>
              <time dateTime={s.fechaCreacion}>{fechaPublica(s.fechaCreacion)}</time><h3>Solicitud registrada</h3><p>Tu solicitud forma parte de este tramite.</p></div></li>
            {institucion?<>
              <li><span className="ca-history-mark ca-history-mark--success"><CheckCircle2 size={18} aria-hidden="true"/></span><div>
                <time dateTime={institucion.fechaDecision}>{fechaPublica(institucion.fechaDecision)}</time><h3>Aprobacion institucional registrada</h3><p>La decision no acredita firma ni desembolso.</p></div></li>
              <li><span className="ca-history-mark"><Clock3 size={18} aria-hidden="true"/></span><div>
                <time dateTime={institucion.actualizadoAt}>{fechaPublica(institucion.actualizadoAt)}</time><h3>Firma pendiente al corte consultado</h3><p>Sin evidencia de firma disponible en esta consulta.</p></div></li>
            </>:<li><span className="ca-history-mark"><Clock3 size={18} aria-hidden="true"/></span><div><h3>{s.titulo}</h3><p>No hay una decision institucional acreditada disponible en esta vista.</p></div></li>}
          </ol>}{institucion&&<Aviso>{INCIDENCIAS[institucion.incidencias]}</Aviso>}
        </>}
        <div className="ca-bottom-actions"><button className="btn btn-outline-primary" onClick={actualizar} disabled={busy||!seguimientoHabilitado}>
          <RefreshCw size={16} aria-hidden="true"/>Actualizar estado</button></div>
      </section>
    </div>
  </div>;
}

export function ComplementoPersonal({sid,operar}:{sid:string;operar:NonNullable<SeguimientoAsociadoProps['autorizar']>}){
  const [data,setData]=useState<AutorizacionTitular['complemento']>(),[periodicidad,setPeriodicidad]=useState('');
  const [recoger,setRecoger]=useState(''),[obligacion,setObligacion]=useState(''),[confirma,setConfirma]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[incierta,setIncierta]=useState(false);
  const guard=useRef(false),alive=useRef(true);
  useEffect(()=>{alive.current=true;void consultar();return()=>{alive.current=false;};},[]);
  function recibir(value:AutorizacionTitular){
    if(!value.complemento)throw new Error('COMPLEMENTO_NO_DISPONIBLE');
    if(!alive.current)return;
    setData(value.complemento);setPeriodicidad('MENSUAL');
    setRecoger(value.complemento.datos?(value.complemento.datos.recogerCredihoy?'SI':'NO'):'');setObligacion(value.complemento.datos?.obligacionRecoger??'');setConfirma(false);setIncierta(false);
  }
  async function consultar(){
    if(guard.current)return;guard.current=true;setBusy(true);setError('');
    try{recibir(await operar({operacion:'autorizacion',accion:'consultar_complemento',solicitudId:sid}));}
    catch{if(alive.current)setError('No pudimos recuperar tus elecciones. Tus datos guardados no han cambiado.');}
    finally{guard.current=false;if(alive.current)setBusy(false);}
  }
  async function guardar(){
    if(guard.current||!data||!confirma||periodicidad!=='MENSUAL'||!['SI','NO'].includes(recoger)||incierta)return;
    guard.current=true;setBusy(true);setError('');
    try{recibir(await operar({operacion:'autorizacion',accion:'guardar_complemento',solicitudId:sid,
      operacionId:crypto.randomUUID(),version:data.version,confirma:true,datos:{periodicidadPago:'MENSUAL',
        recogerCredihoy:recoger==='SI',obligacionRecoger:recoger==='SI'?obligacion:null}}));}
    catch{if(alive.current){setIncierta(true);setError('No pudimos confirmar el guardado. Consulta las elecciones guardadas antes de continuar. Una recogida requiere verificacion institucional.');}}
    finally{guard.current=false;if(alive.current)setBusy(false);}
  }
  return <section aria-label="Elecciones de tu solicitud" aria-busy={busy}>
    <h3>Elecciones de tu solicitud</h3>
    {data?.version!==undefined&&data.version>0&&<Aviso tono="success">Elecciones guardadas el {fechaPublica(data.confirmadoEn!)}.</Aviso>}
    {data?.fuente==='ELECCION_PERSONAL_ENVIO'?<p>Tus condiciones se conservaron al enviar la solicitud. Pago mensual; {data.datos?.recogerCredihoy?'pago de credito vigente solicitado.':'sin pago de otro credito solicitado.'}</p>:<form onSubmit={event=>{event.preventDefault();void guardar();}}>
      <fieldset className="ca-form" disabled={busy||!data||incierta}>
        <p>Pago mensual.</p>
        <label>Recoger un CrediHoy existente<select className="form-select" value={recoger} required onChange={e=>{setRecoger(e.target.value);setObligacion('');setConfirma(false);}}>
          <option value="">Selecciona una opcion</option><option value="NO">No recoger</option><option value="SI">Si, recoger mi CrediHoy</option></select></label>
        {recoger==='SI'&&<label>Numero de obligacion CrediHoy<input className="form-control" inputMode="numeric" pattern="[0-9]{1,20}" maxLength={20} required value={obligacion} onChange={e=>{setObligacion(e.target.value);setConfirma(false);}}/></label>}
        <label className="cp-check"><input type="checkbox" checked={confirma} onChange={e=>setConfirma(e.target.checked)}/>Confirmo que estas son mis elecciones para esta solicitud.</label>
      </fieldset>
      <button className="btn btn-primary" type="submit" disabled={busy||!data||!confirma||incierta}><FileCheck2 size={17} aria-hidden="true"/>Guardar elecciones</button>
    </form>}
    {error&&<p role="alert">{error}</p>}
    <button className="btn btn-outline-secondary" type="button" disabled={busy} onClick={()=>void consultar()}><RefreshCw size={17} aria-hidden="true"/>Consultar elecciones guardadas</button>
  </section>;
}

export function CentralesPersonal({sid,operar,horaServidor=Date.now,onConfirmada}:{sid:string;operar:NonNullable<SeguimientoAsociadoProps['autorizar']>;horaServidor?:()=>number;onConfirmada?:()=>void}){
  const [data,setData]=useState<AutorizacionTitular|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [acepta,setAcepta]=useState(false),[visto,setVisto]=useState(false),[codigo,setCodigo]=useState('');
  const [incierta,setIncierta]=useState(false),[url,setUrl]=useState('');
  const [now,setNow]=useState(horaServidor);
  const guard=useRef(false),alive=useRef(true),operation=useRef<string>(crypto.randomUUID());
  const pendiente=useRef<Extract<PeticionAutorizacion,{accion:'centrales_comprobar'}>|null>(null);
  const actualizarProceso=useRef(onConfirmada);actualizarProceso.current=onConfirmada;
  const c=data?.centrales,terminal=!!c&&['OTP_CONFIRMADA','RECHAZADA','INDETERMINADA'].includes(c.estado);
  useEffect(()=>{alive.current=true;void run('consultar');return()=>{alive.current=false;pendiente.current=null;};},[]);
  useEffect(()=>{const timer=setInterval(()=>setNow(horaServidor()),1000);return()=>clearInterval(timer);},[horaServidor]);
  useEffect(()=>{
    if(c?.estado!=='OTP_CONFIRMADA')return;
    let remaining=12;actualizarProceso.current?.();
    const timer=setInterval(()=>{
      if(--remaining<=0){clearInterval(timer);return;}
      if(document.visibilityState==='visible')actualizarProceso.current?.();
    },5000);
    return()=>clearInterval(timer);
  },[c?.estado]);
  useEffect(()=>{
    if(!c?.presentacion||!['PENDIENTE_OTP','ENVIADO'].includes(c.estado)||incierta)return;
    let remaining=12;
    const timer=setInterval(()=>{
      if(--remaining<=0){clearInterval(timer);return;}
      if(document.visibilityState==='visible'&&!guard.current)void run('consultar');
    },5000);
    return()=>clearInterval(timer);
  },[c?.estado,c?.presentacion,incierta]);
  useEffect(()=>{
    if(!data?.documento){setUrl('');return;}
    const bytes=Uint8Array.from(atob(data.documento.pdfBase64),v=>v.charCodeAt(0));
    const next=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));setUrl(next);
    return()=>URL.revokeObjectURL(next);
  },[data?.documento]);
  async function run(action:'consultar'|'presentar'|'enviar'|'comprobar'|'recuperar'|'secuencia'){
    if(guard.current||action!=='consultar'&&action!=='recuperar'&&(incierta||terminal))return;
    if(action==='recuperar'&&(!pendiente.current||!incierta||terminal))return;
    if(action==='enviar'&&(!acepta||!visto||!presentacionCentralesVigente(data,horaServidor())))return;
    if(action==='comprobar'&&(!c?.presentacion||!/^[0-9]{6}$/.test(codigo)))return;
    const base={operacion:'autorizacion' as const,solicitudId:sid};
    let body:PeticionAutorizacion=action==='recuperar'?pendiente.current!:
      action==='consultar'||action==='secuencia'?{...base,accion:'centrales_consultar'}:
      action==='presentar'?{...base,accion:'centrales_presentar',operacionId:operation.current}:
      action==='enviar'?{...base,accion:'centrales_enviar',operacionId:c!.operacionId!,presentacion:c!.presentacion!,acepta:true}:
      {...base,accion:'centrales_comprobar',operacionId:c!.operacionId!,presentacion:c!.presentacion!,comprobacionId:crypto.randomUUID(),codigo};
    if(body.accion==='centrales_comprobar')pendiente.current=body;
    guard.current=true;setBusy(true);setError('');
    try{
      if(action==='secuencia'){
        // One deliberate code request after the original document acceptance;
        // keep the server's separate purpose/session binding, not a second PDF act.
        const presented=await operar({...base,accion:'centrales_presentar',operacionId:operation.current});
        if(!presentacionCentralesVigente(presented,horaServidor()))throw new Error('PRESENTACION_NO_VIGENTE');
        body={...base,accion:'centrales_enviar',operacionId:operation.current,
          presentacion:presented.centrales!.presentacion!,acepta:true};
      }
      const result=await operar(body);if(!result.centrales)throw new Error('AUTORIZACION_CENTRALES_NO_DISPONIBLE');
      if(alive.current){
        setData(result);setAcepta(false);
        if(action!=='consultar'||['OTP_CONFIRMADA','RECHAZADA','INDETERMINADA'].includes(result.centrales.estado))pendiente.current=null;
        setIncierta(pendiente.current!==null);
        if(result.centrales.operacionId)operation.current=result.centrales.operacionId;
        if((action==='comprobar'||action==='recuperar')&&result.centrales.estado==='ENVIADO')setError('El codigo no confirmo la autorizacion. Revisa el codigo recibido.');
        if(action==='presentar')setVisto(false);
      }
    }catch{if(alive.current){if(action!=='consultar')setIncierta(true);setError('No pudimos confirmar este paso. Consulta el estado antes de continuar; no solicitaremos otro codigo automaticamente.');}}
    finally{guard.current=false;if(alive.current){setBusy(false);setCodigo('');}}
  }
  return <section aria-label="Autorizacion especifica de consulta a centrales" aria-busy={busy}>
    <h3>Autorizar centrales</h3>
    {c?.estado==='OTP_CONFIRMADA'?<Aviso tono="success">Tu autorizacion de consulta quedo confirmada. El estado de tu solicitud se actualiza automaticamente. Esto no significa que tu credito este aprobado.</Aviso>:
      c?.estado==='RECHAZADA'?<Aviso tono="warning">La autorizacion no fue confirmada. No se habilito la consulta de centrales.</Aviso>:
      c?.estado==='INDETERMINADA'?<Aviso tono="warning">El resultado requiere conciliacion de Congente. No solicites otro codigo para esta autorizacion.</Aviso>:<>
        {(!data?.documento&&c?.estado==='SIN_ACEPTACION')&&<>
          <p>La aceptacion del documento quedo registrada. Confirma la autorizacion de consulta con un codigo personal, distinto del codigo de ingreso.</p>
          <button className="btn btn-primary" type="button" disabled={busy||incierta} onClick={()=>void run('secuencia')}><ShieldCheck size={17} aria-hidden="true"/>Verificar autorizacion con codigo</button></>}
        {!c&&!busy&&!error&&<p role="status">Consulta el estado guardado antes de solicitar un codigo.</p>}
        {data?.documento&&c?.estado==='PENDIENTE_ACEPTACION'&&<>
          <p>COLO-FO.023, version {data.documento.version}. Autorizacion para consultar tu informacion crediticia.</p>
          {url&&<a href={url} target="_blank" rel="noopener noreferrer" onClick={()=>setVisto(true)}>Abrir autorizacion oficial</a>}
          <label className="cp-check" htmlFor={'centrales-consent-'+sid}><input id={'centrales-consent-'+sid} type="checkbox" checked={acepta}
            disabled={busy||!visto||incierta||!presentacionCentralesVigente(data,now)} onChange={e=>setAcepta(e.target.checked)}/>
            He le&#237;do y autorizo expresamente la consulta a centrales de riesgo</label>
          {!presentacionCentralesVigente(data,now)&&<p role="status">La presentacion del documento vencio. Consulta el estado antes de continuar.</p>}
          <button className="btn btn-primary" type="button" disabled={busy||incierta||!acepta||!visto||!presentacionCentralesVigente(data,now)} onClick={()=>void run('enviar')}><ShieldCheck size={17} aria-hidden="true"/>Aceptar y solicitar codigo de autorizacion</button>
        </>}
        {c&&['PENDIENTE_OTP','ENVIADO'].includes(c.estado)&&<form onSubmit={event=>{event.preventDefault();void run('comprobar');}}>
          {!c.presentacion&&<Aviso tono="warning">El envio del codigo esta registrado, pero esta sesion ya no permite comprobarlo. Congente debe conciliar su estado. No solicites otro codigo.</Aviso>}
          <fieldset className="ca-form" disabled={busy||incierta||!c.presentacion}>
            <label>Codigo de autorizacion de consulta<input className="form-control" required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={codigo} onChange={e=>setCodigo(e.target.value)}/></label>
          </fieldset>
          <button className="btn btn-primary" type="submit" disabled={busy||incierta||!c.presentacion||!/^[0-9]{6}$/.test(codigo)}><ShieldCheck size={17} aria-hidden="true"/>Confirmar autorizacion de consulta</button>
        </form>}
      </>}
    {error&&<p role="alert">{error}</p>}
    {incierta&&pendiente.current&&!terminal&&<button className="btn btn-outline-secondary" type="button" disabled={busy}
      onClick={()=>void run('recuperar')}><RefreshCw size={17} aria-hidden="true"/>Recuperar comprobacion pendiente</button>}
    {(error||incierta)&&<button className="btn btn-outline-secondary" type="button" disabled={busy} onClick={()=>void run('consultar')}><RefreshCw size={17} aria-hidden="true"/>Reintentar consulta de estado</button>}
  </section>;
}

export function presentacionCentralesVigente(data:AutorizacionTitular|null,now=Date.now()){
  const doc=data?.documento,c=data?.centrales;
  return !!(doc&&c&&c.estado==='PENDIENTE_ACEPTACION'&&doc.codigo==='COLO-FO.023'&&doc.version==='10'
    &&doc.proposito==='AUTORIZACION_CENTRALES_PRECONSULTA'&&c.proposito==='CONSULTA_CENTRALES'
    &&c.textoVersion==='COLO-FO.023:v10'&&doc.pdfSha256===c.pdfSha256&&doc.presentacion===c.presentacion
    &&c.presentacion&&c.vigenteHasta&&Date.parse(c.vigenteHasta)>now);
}

export function AutorizacionesPersonales({sid,operar,horaServidor,onConfirmada}:{sid:string;operar:NonNullable<SeguimientoAsociadoProps['autorizar']>;horaServidor?:()=>number;onConfirmada?:()=>void}){
  const [estado,setEstado]=useState<'cargando'|'documento'|'centrales'|'error'>('cargando');
  const alive=useRef(true),guard=useRef(false);
  useEffect(()=>{alive.current=true;void consultar();return()=>{alive.current=false;};},[]);
  async function consultar(){
    if(guard.current)return;guard.current=true;setEstado('cargando');
    try{
      const data=await operar({operacion:'autorizacion',accion:'consultar',solicitudId:sid});
      if(alive.current)setEstado(['AUTORIZACION_AUSENTE','PENDIENTE_ACEPTACION_PERSONAL'].includes(data.estado)?'documento':
        ['PENDIENTE_FIRMA_AUTENTIC','FIRMA_AUTENTIC_PENDIENTE_VERIFICACION'].includes(data.estado)?'centrales':'error');
    }catch{if(alive.current)setEstado('error');}
    finally{guard.current=false;}
  }
  if(estado==='cargando')return <p role="status">Consultando el estado de tu autorizacion...</p>;
  if(estado==='error')return <section><p role="alert">No pudimos consultar tu autorizacion. No solicitamos otro codigo.</p>
    <button className="btn btn-outline-secondary" type="button" onClick={()=>void consultar()}><RefreshCw size={17} aria-hidden="true"/>Consultar estado</button></section>;
  if(estado==='documento')return <AutorizacionPersonal sid={sid} operar={operar} centralesHabilitadas onAceptacion={()=>setEstado('centrales')}/>;
  return <CentralesPersonal sid={sid} operar={operar} horaServidor={horaServidor} onConfirmada={onConfirmada}/>;
}

export function AutorizacionPersonal({sid,operar,centralesHabilitadas=false,onAceptacion}:{sid:string;operar:NonNullable<SeguimientoAsociadoProps['autorizar']>;centralesHabilitadas?:boolean;onAceptacion?:()=>void}){
  const [data,setData]=useState<AutorizacionTitular|null>(null),[nombre,setNombre]=useState(''),[ciudad,setCiudad]=useState('');
  const [acepta,setAcepta]=useState(false),[busy,setBusy]=useState(false),[incierta,setIncierta]=useState(false),[error,setError]=useState('');
  const [url,setUrl]=useState('');const guard=useRef(false),alive=useRef(true);
  const keys=useRef({presentar:crypto.randomUUID(),aceptar:crypto.randomUUID(),firma:crypto.randomUUID()});
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{void run('consultar');},[]);
  useEffect(()=>{
    if(!data?.documento){setUrl('');return;}
    const bytes=Uint8Array.from(atob(data.documento.pdfBase64),c=>c.charCodeAt(0));
    const next=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));setUrl(next);
    return()=>URL.revokeObjectURL(next);
  },[data]);
  async function run(accion:'consultar'|'presentar'|'aceptar'|'ver_firma'|'verificar_firmante'){
    if(guard.current||['aceptar','verificar_firmante'].includes(accion)&&(!acepta||!data?.documento)||incierta&&accion!=='consultar')return;
    const body:PeticionAutorizacion=accion==='ver_firma'?{operacion:'autorizacion',accion,solicitudId:sid,clave:keys.current.firma}:
      accion==='verificar_firmante'?{operacion:'autorizacion',accion,solicitudId:sid,clave:keys.current.firma,
        presentacion:data!.documento!.presentacion,autorizaFirma:true}:accion==='presentar'?{operacion:'autorizacion',accion,solicitudId:sid,
      clave:keys.current.presentar,campos:{nombre:nombre.trim(),ciudad:ciudad.trim()}}:accion==='aceptar'?{
      operacion:'autorizacion',accion,solicitudId:sid,clave:keys.current.aceptar,
      presentacion:data!.documento!.presentacion,acepta:true}:{operacion:'autorizacion',accion,solicitudId:sid};
    guard.current=true;setBusy(true);setError('');
    try{const value=await operar(body);if(alive.current){setData(value);setAcepta(false);if(accion==='consultar')setIncierta(false);
      if(onAceptacion&&['PENDIENTE_FIRMA_AUTENTIC','FIRMA_AUTENTIC_PENDIENTE_VERIFICACION'].includes(value.estado))onAceptacion();}}
    catch{if(alive.current){if(accion!=='consultar')setIncierta(true);setError('No pudimos confirmar la operacion. Consulta su estado antes de continuar.');}}
    finally{guard.current=false;if(alive.current)setBusy(false);}
  }
  return <section aria-label="Autorizacion de informacion crediticia" aria-busy={busy}>
    <h3>Autorizacion de informacion crediticia</h3>
    {!data?.documento&&(!data||data.estado==='AUTORIZACION_AUSENTE')&&<form onSubmit={e=>{e.preventDefault();void run('presentar');}}>
      <fieldset className="ca-form-grid" disabled={busy||incierta}><label>Nombre completo<input className="form-control" required maxLength={120} value={nombre} onChange={e=>setNombre(e.target.value)}/></label>
        <label>Ciudad<input className="form-control" required maxLength={80} value={ciudad} onChange={e=>setCiudad(e.target.value)}/></label></fieldset>
      <button className="btn btn-primary" type="submit" disabled={busy||incierta}><FileCheck2 size={17} aria-hidden="true"/>Preparar COLO-FO.023</button>
    </form>}
    {data?.documento&&<><p>COLO-FO.023, version {data.documento.version}</p>
      {url&&<a href={url} target="_blank" rel="noopener noreferrer">Abrir autorizacion oficial</a>}
      <label><input type="checkbox" checked={acepta} disabled={busy||incierta} onChange={e=>setAcepta(e.target.checked)}/>{' '}{data.firma?
        'Confirmo mi intencion personal de preparar la firma de este documento.':centralesHabilitadas?'He leido este documento y registro mi aceptacion para continuar con la autorizacion de consulta.':'He leido este documento y acepto continuar con su firma.'}</label>
      <button className="btn btn-primary" type="button" disabled={busy||!acepta||incierta} onClick={()=>void run(data.firma?'verificar_firmante':'aceptar')}><ShieldCheck size={17} aria-hidden="true"/>{data.firma?'Verificar identidad de firma':'Registrar mi aceptacion'}</button></>}
    {data&&['PENDIENTE_FIRMA_AUTENTIC','FIRMA_AUTENTIC_PENDIENTE_VERIFICACION'].includes(data.estado)&&
      <Aviso>{centralesHabilitadas?'Tu aceptacion documental esta registrada. La firma contractual sigue pendiente.':'Tu aceptacion esta registrada. La firma y la autorizacion para consultar centrales siguen pendientes.'}</Aviso>}
    {data&&!data.documento&&['PENDIENTE_FIRMA_AUTENTIC','FIRMA_AUTENTIC_PENDIENTE_VERIFICACION'].includes(data.estado)&&!data.firma?.identidadVerificada&&
      <button className="btn btn-outline-secondary" type="button" disabled={busy||incierta} onClick={()=>void run('ver_firma')}><FileCheck2 size={17} aria-hidden="true"/>Revisar documento aceptado</button>}
    {data?.firma?.identidadVerificada&&<Aviso>Identidad cotejada con el documento aceptado. La firma electronica sigue pendiente.</Aviso>}
    {error&&<p role="alert">{error}</p>}
    <button className="btn btn-outline-secondary" type="button" disabled={busy} onClick={()=>void run('consultar')}><RefreshCw size={17} aria-hidden="true"/>Consultar estado</button>
  </section>;
}

export function OfertaTitular({sid,operar,horaServidor=Date.now,radicar,verSeguimiento}:{sid:string;operar:NonNullable<SeguimientoAsociadoProps['cotizar']>;horaServidor?:()=>number;radicar?:SeguimientoAsociadoProps['radicar'];verSeguimiento?:()=>void}){
  const [data,setData]=useState<CotizacionTitular|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [acepta,setAcepta]=useState(false),[now,setNow]=useState(horaServidor);
  const [diaPago,setDiaPago]=useState('');
  const guard=useRef(false),alive=useRef(true);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{const timer=setInterval(()=>setNow(horaServidor()),1000);return()=>clearInterval(timer);},[horaServidor]);
  const offer=data?.oferta,sim=data?.simulacion,vencida=Boolean((offer||sim)&&Date.parse((offer||sim)!.vigenteHasta)<=now
    &&!(sim&&data?.aceptacionSimulacion?.aceptada));
  useEffect(()=>{void run('consultar');},[sid]);
  async function run(op:'consultar'|'cotizar'|'aceptar'|'aceptar_simulacion'){
    if(guard.current)return;guard.current=true;setBusy(true);setError('');
    try{const p=await operar(sid,op,op==='aceptar_simulacion'?sim?.sha256:offer?.sha256,op==='cotizar'?Number(diaPago):undefined);
      if(alive.current){setData(p);setAcepta(false);if(p.oferta?.calendarioPago)setDiaPago(String(p.oferta.calendarioPago.diaPago));}}
    catch{if(alive.current){setData(null);setAcepta(false);setError('No pudimos confirmar esta operacion. Consulta el estado antes de repetir.');}}
    finally{guard.current=false;if(alive.current)setBusy(false);}
  }
  return <section aria-label="Cotizacion y oferta institucional" aria-busy={busy}>
    {busy&&!data&&<Cargando texto="Consultando cuota y condiciones..."/>}
    {error&&<Aviso tono="warning" titulo="Operacion sin confirmar">{error}</Aviso>}
    {!data&&!busy&&<button className="btn btn-outline-primary" type="button" onClick={()=>void run('consultar')}>
      <RefreshCw size={17} aria-hidden="true"/>Consultar condiciones</button>}
    {sim&&<><h3>Cuota y condiciones de tu solicitud</h3><dl className="ca-metrics">
      <div><dt>Monto</dt><dd>{moneda(sim.monto)}</dd></div><div><dt>Cuota calculada</dt><dd>{moneda(sim.cuota)}</dd></div>
      <div><dt>Plazo</dt><dd>{sim.plazoMeses} meses</dd></div><div><dt>Tasa NAMV</dt><dd>{sim.tasaNominalNamv} %</dd></div>
      <div><dt>Periodicidad</dt><dd>Mensual</dd></div><div><dt>Seguro mensual sobre saldo</dt><dd>{sim.seguroMensualPorcentaje} %</dd></div></dl>
      <p>Este plan de pagos es aproximado. Una vez desembolsado tu crédito, recibirás por correo electrónico el plan de pagos definitivo generado al momento de tu desembolso.</p>
      <div style={{overflowX:'auto'}} tabIndex={0} aria-label="Plan de pagos aproximado"><table className="table" style={{minWidth:640,whiteSpace:'nowrap'}}><thead><tr>
        <th scope="col">Cuota</th><th scope="col">Capital</th><th scope="col">Intereses</th><th scope="col">Seguro</th><th scope="col">Total</th><th scope="col">Saldo</th>
      </tr></thead><tbody>{sim.amortizacion.map(r=><tr key={r.periodo}><th scope="row">{r.periodo}</th><td>{moneda(r.capital)}</td>
        <td>{moneda(r.intereses)}</td><td>{moneda(r.seguro)}</td><td>{moneda(r.cuota)}</td><td>{moneda(r.saldoFinal)}</td></tr>)}</tbody></table></div>
      {data?.aceptacionSimulacion?.aceptada?<Aviso tono="success" titulo="Simulación aceptada">
        Tu aceptación quedó registrada. La preparación de radicación conserva sus requisitos; la firma contractual y la decisión institucional están pendientes.
      </Aviso>:<><label><input type="checkbox" checked={acepta} disabled={busy||vencida} onChange={e=>setAcepta(e.target.checked)}/>
        {' '}He revisado y acepto la cuota y las condiciones de la simulación presentada.</label>
        <button className="btn btn-brand" type="button" disabled={busy||vencida||!acepta} onClick={()=>void run('aceptar_simulacion')}>
          <CheckCircle2 size={17} aria-hidden="true"/> Aceptar cuota y continuar</button></>}
      {vencida&&<p role="status">Las condiciones vencieron. Consulta el estado actualizado antes de aceptar.</p>}
    </>}
    {!sim&&data&&<><label className="ch-field">Dia mensual de pago<select className="form-select" value={diaPago} disabled={busy}
      onChange={e=>{setDiaPago(e.target.value);setData(null);setAcepta(false);}}>
      <option value="">Seleccionar</option>{Array.from({length:20},(_,i)=><option key={i+1} value={i+1}>{i+1}</option>)}
    </select></label>
    <button className="btn btn-outline-primary" type="button" disabled={busy} onClick={()=>void run('consultar')}><RefreshCw size={17} aria-hidden="true"/> Consultar oferta</button>
    <button className="btn btn-brand" type="button" disabled={busy||Boolean(error)||!diaPago} onClick={()=>void run('cotizar')}><Calculator size={17} aria-hidden="true"/> Cotizar condiciones guardadas</button></>}
    {sim&&data?.aceptacionSimulacion?.aceptada&&radicar&&<RadicacionPersonal sid={sid} operar={radicar} verSeguimiento={verSeguimiento}/>}
    {sim&&<button className="btn btn-outline-primary" type="button" disabled={busy} onClick={()=>void run('consultar')}><RefreshCw size={17} aria-hidden="true"/> Consultar condiciones</button>}
    {offer&&<><dl className="ca-metrics"><div><dt>Monto</dt><dd>{moneda(offer.monto)}</dd></div>
      <div><dt>Cuota institucional</dt><dd>{data?.cuota?moneda(data.cuota):'No disponible'}</dd></div><div><dt>Plazo</dt><dd>{offer.plazoMeses} meses</dd></div>
      <div><dt>Tasa NAMV</dt><dd>{offer.tasaNominalNamv} %</dd></div><div><dt>Tasa EA</dt><dd>{offer.tasaEA??'Por confirmar'}</dd></div>
      <div><dt>Primer vencimiento</dt><dd>{offer.primerVencimiento??'Por confirmar'}</dd></div></dl>
      {offer.calendarioPago&&<p>Desembolso {offer.calendarioPago.tipoBase==='PROYECTADO'?'proyectado':'efectivo'}: {offer.calendarioPago.fechaBaseDesembolso}.
        {' '}Intereses anticipados: {offer.calendarioPago.interesesAnticipados.desde} a {offer.calendarioPago.interesesAnticipados.hasta}; importe pendiente de confirmacion.</p>}
      <p>El seguro se gestiona en la cuota institucional; no se agrega otra prima desde el canal.</p>
      {!offer.condicionesCompletas&&<Aviso tono="warning" titulo="Condiciones pendientes">No puedes aceptar esta oferta hasta completar sus condiciones institucionales.</Aviso>}
      {vencida&&<p role="status">La oferta vencio. Consulta su estado actualizado.</p>}
      {data?.aceptacion.aceptada?<p role="status">Oferta aceptada. Esta aceptacion no constituye firma ni desembolso.</p>:
        <><label><input type="checkbox" checked={acepta} disabled={busy||vencida||!offer.condicionesCompletas}
          onChange={e=>setAcepta(e.target.checked)}/> He revisado y acepto expresamente estas condiciones de la oferta.</label>
        <button className="btn btn-brand" type="button" disabled={busy||vencida||!offer.condicionesCompletas||!acepta}
          onClick={()=>void run('aceptar')}><CheckCircle2 size={17} aria-hidden="true"/> Aceptar oferta</button></>}
    </>}
  </section>;
}

export function RadicacionPersonal({sid,operar,verSeguimiento}:{sid:string;operar:NonNullable<SeguimientoAsociadoProps['radicar']>;verSeguimiento?:()=>void}){
  const [data,setData]=useState<RadicacionTitular|null>(null),[busy,setBusy]=useState(true),[error,setError]=useState('');
  const [abierto,setAbierto]=useState(false),[diaPago,setDiaPago]=useState(''),[acepta,setAcepta]=useState(false),[incierta,setIncierta]=useState(false);
  const guard=useRef(false),alive=useRef(true);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{void run('consultar');},[sid]);
  const recuperar=incierta||data?.estado==='INCIERTA'||data?.estado==='EXECUTING';
  async function run(action:PeticionRadicacion['accion']){
    if(guard.current)return;guard.current=true;setBusy(true);setError('');
    const base={operacion:'radicacion' as const,solicitudId:sid};
    try{
      const body:PeticionRadicacion=action==='preparar'?{...base,accion:action,diaPagoPropuesto:Number(diaPago)}:
        action==='confirmar'?{...base,accion:action,presentacion:data!.presentacion!,aceptaRadicar:true}:{...base,accion:action};
      const p=await operar(body);
      if(alive.current){setData(p);setAcepta(false);setIncierta(p.estado==='INCIERTA'||p.estado==='EXECUTING');if(p.estado==='RADICADA')setAbierto(true);}
    }catch{if(alive.current){setAbierto(true);setData(null);setAcepta(false);setIncierta(true);setError('No pudimos confirmar el resultado. Consulta la operacion existente antes de continuar.');}}
    finally{guard.current=false;if(alive.current)setBusy(false);}
  }
  return <section aria-label="Radicacion personal" aria-busy={busy}>
    <h3>Radicacion de tu solicitud</h3>
    {busy&&<p role="status" aria-live="polite"><RefreshCw size={17} aria-hidden="true"/> Verificando el estado de tu solicitud. Espera un momento...</p>}
    {!abierto&&data?.estado!=='RADICADA'&&data&&!busy&&<button className="btn btn-brand" type="button" disabled={busy} onClick={()=>{setAbierto(true);void run('consultar');}}>
      <FileCheck2 size={17} aria-hidden="true"/> Radicar mi solicitud</button>}
    {abierto&&<>
      {error&&<Aviso tono="warning" titulo="Resultado por confirmar">{error}</Aviso>}
      {data?.codigo&&data.estado!=='RADICADA'&&<Aviso tono="warning" titulo="Radicacion por verificar">Estamos verificando tu solicitud. No necesitas enviarla nuevamente.</Aviso>}
      {data?.estado==='RADICADA'?<>
        <Aviso tono="success" titulo="¡Tu solicitud fue radicada exitosamente!">
          Se encuentra en revisión institucional. Te informaremos cuando tengamos una actualización.
        </Aviso>
        {verSeguimiento&&<button className="btn btn-brand" type="button" disabled={busy} onClick={verSeguimiento}>
          <ArrowRight size={17} aria-hidden="true"/> Ver seguimiento</button>}
      </>:<>
        {data&&!recuperar&&['SIN_INTENCION','PREPARADA'].includes(data.estado)&&<>
          <label className="ch-field">Dia mensual de pago<select className="form-select" value={diaPago}
            disabled={busy||data.puedeConfirmar} onChange={e=>{setDiaPago(e.target.value);setAcepta(false);}}>
            <option value="">Seleccionar</option>{Array.from({length:20},(_,i)=><option key={i+1} value={i+1}>{i+1}</option>)}
          </select></label>
          <p>El primer vencimiento propuesto respeta un minimo de 30 dias. LINIX conserva el calendario definitivo.</p>
          {!data.puedeConfirmar&&<button className="btn btn-outline-primary" type="button" disabled={busy||!diaPago} onClick={()=>void run('preparar')}>
            <FileCheck2 size={17} aria-hidden="true"/> Revisar radicacion</button>}
        </>}
        {data?.puedeConfirmar&&!recuperar&&<>
          {data.calendarioPropuesto&&<p>Primer vencimiento propuesto: {data.calendarioPropuesto.primerVencimiento}. No es el calendario definitivo del desembolso.</p>}
          <label><input type="checkbox" checked={acepta} disabled={busy} onChange={e=>setAcepta(e.target.checked)}/>
            {' '}Confirmo radicar mi solicitud con las condiciones y cuota aceptadas, el dia de pago seleccionado y el vencimiento propuesto. La decision institucional y la firma contractual continuan pendientes.</label>
          <button className="btn btn-brand" type="button" disabled={busy||!acepta} onClick={()=>void run('confirmar')}>
            <ArrowRight size={17} aria-hidden="true"/> Confirmar radicacion</button>
        </>}
      </>}
      <button className="btn btn-outline-primary" type="button" disabled={busy} onClick={()=>void run(recuperar?'conciliar':'consultar')}>
        <RefreshCw size={17} aria-hidden="true"/> {recuperar?'Recuperar operacion existente':'Consultar radicacion'}</button>
    </>}
  </section>;
}

export function PortalAsociado(){
  const [iniciado,setIniciado]=useState(()=>typeof window!=='undefined'&&window.location.hash==='#acceso');
  useEffect(()=>{if(iniciado)document.getElementById('acceso-9b')?.focus();},[iniciado]);
  if(iniciado)return <div id="acceso-9b" tabIndex={-1}><CredihoyAccesoAsociado seguimiento={p=><EspacioAsociado {...p}/>}/></div>;
  return <CredihoyShell state={crearEstadoInicial()} mostrarProgreso={false} mostrarResumen={false} asociado>
    <section className="ca-welcome">
      <Etiqueta tono="success">Acceso para asociados</Etiqueta>
      <h1>CrediHoy</h1><p className="ca-lead">Tu solicitud, en un solo lugar.</p>
      <p className="ca-welcome-copy">Ingresa con tu documento y un codigo a tu contacto registrado. Consulta tu informacion y los pasos de tu solicitud.</p>
      <button className="btn btn-brand" onClick={()=>{window.history.replaceState(null,'',window.location.pathname+window.location.search+'#acceso');setIniciado(true);}}>
        Ingresar a mi solicitud<ArrowRight size={19} aria-hidden="true"/></button>
      <NotaPrivacidad/>
      <div className="ca-welcome-next"><h2>Tu credito, paso a paso</h2><ol>
        {[{icono:UserRound,titulo:'Tu identidad',texto:'Valida tus datos con Congente.'},
          {icono:ShieldCheck,titulo:'Tu acceso',texto:'Confirma el codigo de tu contacto registrado.'},
          {icono:LayoutDashboard,titulo:'Tu solicitud',texto:'Conoce el estado y lo que sigue.'}].map((step,i)=><li key={step.titulo}>
            <span className="ca-icon-tile"><step.icono size={22} aria-hidden="true"/></span><div><span className="ca-step-label">0{i+1}</span><h3>{step.titulo}</h3><p>{step.texto}</p></div></li>)}
      </ol></div>
      <div className="ca-welcome-foot"><p>La aprobacion y las condiciones del credito dependen de la evaluacion de tu solicitud.</p>
        <a href="https://www.congente.coop/" rel="noreferrer">Canales de atencion Congente<ArrowRight size={15} aria-hidden="true"/></a></div>
    </section>
  </CredihoyShell>;
}
