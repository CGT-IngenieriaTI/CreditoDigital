import {Fragment,useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {AccesoAsociadoClient,AccesoPublicoError,type InicioPublico,type SesionPublica,type SolicitudPublica,type IntentoPublico} from '../services/publico';
import {CredihoyShell} from './CredihoyShell';
import {crearEstadoInicial} from '../state';
import {moneda} from './FormControls';
import {ArrowLeft,ArrowRight,Clock3,Fingerprint,LockKeyhole,MessageSquare,ShieldCheck} from 'lucide-react';
import {Aviso,Cargando,NotaPrivacidad,ProgresoAcceso} from '../asociado/ui';
import {BorradorAsociado} from '../asociado/BorradorAsociado';
import type {GuardarBorrador,CotizacionTitular,AutorizacionTitular,PeticionAutorizacion,PeticionRadicacion,RadicacionTitular} from '../services/publico';
import type {OperarSimulacion} from '../asociado/SimuladorLocal';
import './credihoy-publico.css';

declare global {interface Window {turnstile?:{
  render:(node:HTMLElement,options:Record<string,unknown>)=>string;
  remove:(id:string)=>void;
}}}
const DRAFT='credihoy_publico_intento_v1';
function leerIntento():string {
  try {const p=JSON.parse(sessionStorage.getItem(DRAFT)??'null');
    return p&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(p.id)
      &&typeof p.venceAt==='number'&&p.venceAt>Date.now()&&p.venceAt<=Date.now()+300000?p.id:'';
  }catch{return '';}
}
function guardarIntento(id:string,ttl?:number){try{if(id&&ttl&&ttl>0&&ttl<=300)sessionStorage.setItem(DRAFT,JSON.stringify({id,venceAt:Date.now()+ttl*1000}));else sessionStorage.removeItem(DRAFT);}catch{/* No personal data is persisted. */}}
export interface SeguimientoAsociadoProps { solicitudes:SolicitudPublica[]|null;busy:boolean;nombre:string|null;
  actualizar:()=>void;salir:()=>void;seguimientoHabilitado?:boolean;formularioBorrador?:ReactNode;
  cotizacionHabilitada?:boolean;horaServidor?:()=>number;radicacionHabilitada?:boolean;
  radicar?:(body:PeticionRadicacion)=>Promise<RadicacionTitular>;
  simuladorLocalHabilitado?:boolean;simular?:OperarSimulacion;
  autorizacionHabilitada?:boolean;centralesHabilitadas?:boolean;autorizar?:(body:PeticionAutorizacion)=>Promise<AutorizacionTitular>;
  cotizar?:(sid:string,operacion:'consultar'|'cotizar'|'aceptar'|'aceptar_simulacion',sha?:string,diaPago?:number)=>Promise<CotizacionTitular> }

export function CredihoyAccesoAsociado({seguimiento}:{seguimiento?:(props:SeguimientoAsociadoProps)=>ReactNode}={}){
  const api=useRef(new AccesoAsociadoClient()),guard=useRef(false),generation=useRef(0);
  const operarBorrador=useCallback(async(body:GuardarBorrador|{operacion:'borrador';accion:'retomar';actividad?:boolean})=>{
    try{return await api.current.borrador(body);}
    catch(e){if(e instanceof AccesoPublicoError&&[401,403].includes(e.status))expirarSesion();throw e;}
  },[]);
  const simularBorrador=useCallback((id:string,escenario?:import('../services/simulacion').EscenarioLocal)=>api.current.simularBorrador(id,escenario),[]);
  const [inicio,setInicio]=useState<InicioPublico|null>(null),[sesion,setSesion]=useState<SesionPublica>({autenticado:false});
  const [solicitudes,setSolicitudes]=useState<SolicitudPublica[]|null>(null);
  const [busy,setBusy]=useState(true),[error,setError]=useState(''),[mensaje,setMensaje]=useState('');
  const [documento,setDocumento]=useState(''),[apellido,setApellido]=useState(''),[expedicion,setExpedicion]=useState('');
  const [canal,setCanal]=useState<'SMS'|'EMAIL'>('SMS'),[acepta,setAcepta]=useState(false),[bot,setBot]=useState('');
  const [intento,setIntento]=useState(leerIntento),[codigo,setCodigo]=useState('');
  const [contacto,setContacto]=useState<IntentoPublico|null>(null);
  const [ahora,setAhora]=useState(Date.now);
  const vencido=Boolean(intento)&&(contacto?.expiraAt?Date.parse(contacto.expiraAt)<=ahora:leerIntento()!==intento);
  const botNode=useRef<HTMLDivElement>(null);
  function expirarSesion(){
    generation.current++;api.current.invalidarSesion();guardarIntento('');setIntento('');setContacto(null);
    setSesion({autenticado:false});setSolicitudes(null);setInicio(null);setCodigo('');setDocumento('');
    setApellido('');setExpedicion('');setBot('');setAcepta(false);
    setError('Tu sesion termino. Vuelve a verificar tu identidad.');
  }
  async function recuperar(){
    const v=generation.current;
    let p:SesionPublica;
    try{p=await api.current.sesion();}
    catch(e){if(v===generation.current)setSolicitudes(null);throw e;}
    if(v!==generation.current)return;
    if(!p.autenticado||!p.acciones.seguimiento||!sesion.autenticado||p.sesionReferencia!==sesion.sesionReferencia)setSolicitudes(null);
    setSesion(p);
    if(!p.autenticado){if(sesion.autenticado)expirarSesion();setSolicitudes(null);setContacto(null);setCodigo('');return;}
    if(p.autenticado){guardarIntento('');setIntento('');setCodigo('');setDocumento('');setApellido('');setExpedicion('');
      if(p.acciones.seguimiento){
        try{
          const rows=await api.current.solicitudes();if(v===generation.current)setSolicitudes(rows);
        }catch(e){
          if(v!==generation.current)return;
          setSolicitudes(null);
          if(e instanceof AccesoPublicoError&&[401,403].includes(e.status))expirarSesion();
          else setError('No pudimos actualizar tu solicitud. Tu sesion sigue activa; intenta consultar mas tarde.');
        }}}
  }
  useEffect(()=>{
    if(!inicio||!intento||sesion.autenticado||['CONTACTO_DISPONIBLE','CODIGO_ENVIADO'].includes(contacto?.estado??''))return;
    let active=true,timer:ReturnType<typeof setTimeout>|undefined;
    const id=intento,version=generation.current;
    const limiteEspera=performance.now()+Math.min(60000,inicio.duracionOtpSegundos*1000);
    async function poll(){
      try{
        if(performance.now()>=limiteEspera){
          if(active)setError('La verificacion esta tardando mas de lo esperado. Contacta a Congente antes de volver a enviar tus datos; no solicitaremos otro codigo automaticamente.');
          return;
        }
        const p=await api.current.estadoIntento(id);
        if(!active||version!==generation.current)return;
        if(!(contacto?.estado==='ENVIO_PENDIENTE'&&p.estado==='CONTACTO_DISPONIBLE'))setContacto(p);
        if(p.estado!=='CODIGO_ENVIADO'&&leerIntento()===id)timer=setTimeout(()=>void poll(),2500);
      }catch{if(active)setError('No fue posible recuperar la verificacion. No enviaremos otro codigo automaticamente.');}
    }
    void poll();return()=>{active=false;if(timer)clearTimeout(timer);};
  },[inicio,intento,sesion.autenticado,contacto?.estado]);
  useEffect(()=>{if(!intento)return;const timer=setInterval(()=>setAhora(Date.now()),1000);return()=>clearInterval(timer);},[intento]);
  useEffect(()=>{
    let active=true;
    api.current.inicio().then(async p=>{if(!active)return;setInicio(p);setCanal(p.canales[0]);await recuperar();})
      .catch(error=>{if(active)setError(error instanceof AccesoPublicoError&&error.codigo==='ACCESS_WINDOW_EXPIRED'
        ?'El horario de acceso de este ensayo ha finalizado. Contacta a Congente para confirmar el siguiente horario; no solicites otro codigo.'
        :'El acceso digital no esta disponible en este momento. Comunicate con Congente.');})
      .finally(()=>{if(active)setBusy(false);});
    return()=>{active=false;generation.current++;};
  },[]);
  useEffect(()=>{
    if(!inicio||sesion.autenticado||intento||!botNode.current)return;
    if(inicio.proteccion==='OPERADOR_RESTRINGIDO'){
      setBot(inicio.controlAcceso??'');return;
    }
    let active=true,widget:string|undefined;
    const node=botNode.current;
    const script=document.createElement('script');script.src='https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.async=true;script.defer=true;
    const render=()=>{if(!active||!window.turnstile)return;
      widget=window.turnstile.render(node,{sitekey:inicio.botSitekey,action:'credihoy_acceso',size:node.clientWidth<300?'compact':'flexible',
        callback:(token:string)=>{if(active)setBot(token);},'expired-callback':()=>{if(active)setBot('');},
        'error-callback':()=>{if(active){setBot('');setError('No se pudo completar la verificacion de seguridad.');}}});};
    script.onload=render;script.onerror=()=>{if(active)setError('No se pudo cargar la verificacion de seguridad.');};
    if(window.turnstile)render();else document.head.appendChild(script);
    return()=>{active=false;if(widget)window.turnstile?.remove(widget);script.remove();};
  },[inicio,sesion.autenticado,intento]);
  useEffect(()=>{
    if(!sesion.autenticado)return;
    const timer=setTimeout(expirarSesion,Math.max(0,Date.parse(sesion.expiraAt)-Date.now()));
    return()=>clearTimeout(timer);
  },[sesion]);
  async function operar(action:()=>Promise<void>){
    if(guard.current)return;guard.current=true;setBusy(true);setError('');
    try{await action();}catch(e){
      if(sesion.autenticado&&e instanceof AccesoPublicoError&&[401,403].includes(e.status))expirarSesion();
      else setError('No fue posible confirmar la operacion. No solicitaremos otro codigo automaticamente.');}
    finally{guard.current=false;setBusy(false);}
  }
  useEffect(()=>{
    if(!sesion.autenticado)return;
    const volver=()=>{if(document.visibilityState==='visible')void operar(recuperar);};
    document.addEventListener('visibilitychange',volver);window.addEventListener('pageshow',volver);
    return()=>{document.removeEventListener('visibilitychange',volver);window.removeEventListener('pageshow',volver);};
  },[sesion.autenticado,sesion.autenticado?sesion.sesionReferencia:null]);
  return <CredihoyShell state={crearEstadoInicial()} mostrarProgreso={false} mostrarResumen={false} asociado>
    <section className={'cp-access'+(sesion.autenticado&&seguimiento?' cp-access--workspace':'')} data-testid="acceso-asociado" aria-busy={busy}>
      <div className="cp-access-heading">
        {!sesion.autenticado&&<span className="ca-icon-tile cp-identity-mark"><Fingerprint size={28} aria-hidden="true"/></span>}
        <p className="ch-eyebrow">{sesion.autenticado?'Tu espacio personal':'Credito digital Congente'}</p>
        <h1>{sesion.autenticado?'Mis solicitudes CrediHoy':'Verifica tu identidad'}</h1>
        {!sesion.autenticado&&<p className="cp-intro">{intento?'Un paso mas para acceder a tu informacion.':'Tu informacion, en un espacio personal. Valida los datos de tu documento con Congente.'}</p>}
      </div>
      {!sesion.autenticado&&<ProgresoAcceso etapa={intento?1:0}/>}
      {busy&&(!sesion.autenticado||!seguimiento)&&<Cargando texto="Verificando..."/>}
      {error&&<Aviso tono="error" alerta titulo="No pudimos completar este paso">{error}</Aviso>}
      {mensaje&&!sesion.autenticado&&(!contacto||contacto.estado==='PREPARANDO')&&<Aviso tono="neutral">{mensaje}</Aviso>}
      {!inicio&&!busy&&<button className="btn btn-outline-secondary" onClick={()=>window.location.reload()}><ArrowLeft size={17} aria-hidden="true"/>Recuperar acceso</button>}
      {inicio&&!sesion.autenticado&&!intento&&<form onSubmit={event=>{event.preventDefault();if(!acepta||!bot)return;
        void operar(async()=>{const id=crypto.randomUUID();guardarIntento(id,inicio.duracionOtpSegundos);setIntento(id);setContacto(null);
          await api.current.iniciar({numeroDocumento:documento,primerApellido:apellido,fechaExpedicion:expedicion,
            canal,avisoVersion:inicio.avisoVersion,aceptaTratamiento:true,botToken:bot},id);
          setBot('');setMensaje('Si los datos coinciden, podras confirmar el envio al medio registrado en Congente.');});
      }} className="cp-form-surface">
        <fieldset disabled={busy} className="cp-fields">
          <div className="cp-form-heading"><ShieldCheck size={19} aria-hidden="true"/><h2>Tu documento de identidad</h2></div>
          <div className="cp-identity-grid">
          <label>Tipo de documento<select className="form-select" aria-label="Tipo de documento" value="CC" disabled><option value="CC">Cedula de ciudadania</option></select></label>
          <label>Numero de documento<input className="form-control" inputMode="numeric" autoComplete="off" required
            pattern="[0-9]{6,15}" maxLength={15} value={documento} onChange={e=>setDocumento(e.target.value)}/></label>
          <label>Primer apellido<input className="form-control" autoComplete="family-name" required maxLength={80}
            value={apellido} onChange={e=>setApellido(e.target.value)}/></label>
          <label>Fecha de expedicion del documento<input className="form-control" type="date" required max={new Date().toISOString().slice(0,10)}
            value={expedicion} onChange={e=>setExpedicion(e.target.value)}/></label>
          </div>
          <details className="cp-privacy"><summary>Aviso de tratamiento de datos</summary><p className="cp-notice">{inicio.avisoTexto}</p>
            <a href={inicio.politica.url} target="_blank" rel="noopener noreferrer">Politica de tratamiento {inicio.politica.codigo}, version {inicio.politica.version}</a>
          </details>
          <label className="cp-check"><input type="checkbox" checked={acepta} onChange={e=>setAcepta(e.target.checked)}/>
            He leido y acepto el aviso de tratamiento para verificar mi identidad.</label>
          <div ref={botNode} aria-label="Verificacion de seguridad" className="cp-bot"/>
          <button className="btn btn-brand" disabled={!acepta||!bot}>Continuar<ArrowRight size={18} aria-hidden="true"/></button>
        </fieldset>
      </form>}
      {inicio&&!sesion.autenticado&&intento&&contacto?.estado==='CONTACTO_DISPONIBLE'&&<form onSubmit={event=>{
        event.preventDefault();if(!contacto.canal||vencido)return;
        void operar(async()=>{setContacto({...contacto,estado:'ENVIO_PENDIENTE'});
          await api.current.confirmarEnvio(intento,contacto.canal!);
          guardarIntento(intento,inicio.duracionOtpSegundos);
        });
      }} className="cp-form-surface"><fieldset disabled={busy||vencido} className="cp-fields">
        <span className="ca-icon-tile"><MessageSquare size={24} aria-hidden="true"/></span>
        <h2>Tu medio registrado</h2>
        <p className="cp-intro">Confirma el envio de un codigo al contacto que tienes registrado en Congente.</p>
        <label className="cp-channel"><input type="radio" checked readOnly name="contacto"/>
          <span><small>{contacto.canal==='SMS'?'Mensaje de texto':'Correo electronico'}</small>
            {contacto.canal==='SMS'?'SMS a tu celular':'Correo registrado'} {contacto.contactoEnmascarado}</span></label>
        <button className="btn btn-brand">Enviar codigo<ArrowRight size={18} aria-hidden="true"/></button>
      </fieldset></form>}
      {inicio&&!sesion.autenticado&&intento&&vencido&&contacto?.estado!=='CODIGO_ENVIADO'&&
        <Aviso tono="warning" titulo="Verificacion vencida">La verificacion vencio. Verifica tus datos nuevamente para continuar.</Aviso>}
      {inicio&&!sesion.autenticado&&intento&&!vencido&&(!contacto||['PREPARANDO','ENVIO_PENDIENTE'].includes(contacto.estado))&&
        <Cargando texto={contacto?.estado==='ENVIO_PENDIENTE'?'Confirmando el envio del codigo...':'Verificando los datos proporcionados...'}/>}
      {inicio&&!sesion.autenticado&&intento&&contacto?.estado==='CODIGO_ENVIADO'&&<form onSubmit={event=>{event.preventDefault();void operar(async()=>{
        await api.current.verificar(intento,codigo);await recuperar();setMensaje('');});}} className="cp-form-surface cp-form-surface--code">
        <fieldset disabled={busy} className="cp-fields">
          <span className="ca-icon-tile"><LockKeyhole size={24} aria-hidden="true"/></span>
          <h2>Confirma tu acceso</h2><p className="cp-intro">Ingresa el codigo recibido en tu medio registrado <strong>{contacto.contactoEnmascarado}</strong>. Nunca lo compartas.</p>
          <label>Codigo de verificacion<input className="form-control cp-code" autoComplete="one-time-code" inputMode="numeric"
            pattern="[0-9]{6}" maxLength={6} value={codigo} required onChange={e=>setCodigo(e.target.value)}/></label>
          <p className="cp-timer" role="timer" aria-label="Tiempo restante"><Clock3 size={16} aria-hidden="true"/>{contacto.expiraAt&&Date.parse(contacto.expiraAt)>ahora?
            'Tiempo restante: '+Math.ceil((Date.parse(contacto.expiraAt)-ahora)/1000)+' segundos':'El codigo vencio. Verifica tus datos nuevamente.'}</p>
          <button className="btn btn-brand" disabled={!contacto.expiraAt||Date.parse(contacto.expiraAt)<=ahora}>Verificar y continuar<ArrowRight size={18} aria-hidden="true"/></button>
        </fieldset>
      </form>}
      {inicio&&!sesion.autenticado&&intento&&<button className="btn btn-link cp-back" disabled={busy}
        onClick={()=>void operar(async()=>{
          if(contacto?.expiraAt&&Date.parse(contacto.expiraAt)>Date.now())await api.current.cancelarIntento(intento);
          generation.current++;guardarIntento('');setIntento('');setContacto(null);setCodigo('');setBot('');setMensaje('');
        })}><ArrowLeft size={16} aria-hidden="true"/>Solicitar otro codigo con nuevo cotejo</button>}
      {!sesion.autenticado&&<NotaPrivacidad/>}
      {sesion.autenticado&&seguimiento?<Fragment key={sesion.sesionReferencia}>{seguimiento({solicitudes,busy,nombre:sesion.nombre,seguimientoHabilitado:sesion.acciones.seguimiento,
        autorizacionHabilitada:sesion.acciones.autorizar===true,centralesHabilitadas:sesion.acciones.centralesOtp===true,autorizar:async body=>{
          try{return await api.current.autorizacion(body);}
          catch(e){if(e instanceof AccesoPublicoError&&[401,403].includes(e.status))expirarSesion();throw e;}
        },
        simuladorLocalHabilitado:sesion.acciones.simuladorLocal===true,simular:async(sid,escenario)=>{
          try{return await api.current.simular(sid,escenario);}
          catch(e){if(e instanceof AccesoPublicoError&&[401,403].includes(e.status))expirarSesion();throw e;}
        },
        radicacionHabilitada:sesion.acciones.radicar===true,radicar:async body=>{
          try{return await api.current.radicacion(body);}
          catch(e){if(e instanceof AccesoPublicoError&&[401,403].includes(e.status))expirarSesion();throw e;}
        },
        cotizacionHabilitada:sesion.acciones.simular,horaServidor:()=>api.current.ahoraServidor(),cotizar:async(sid,operacion,sha,diaPago)=>{
          try{return await api.current.cotizacion(sid,operacion,sha,diaPago);}
          catch(e){if(e instanceof AccesoPublicoError&&[401,403].includes(e.status))expirarSesion();throw e;}
        },
        formularioBorrador:sesion.acciones.solicitar?<BorradorAsociado key={sesion.sesionReferencia} operar={operarBorrador} nombre={sesion.nombre} perfil={sesion.perfil} alEnviar={recuperar} iniciarEnSimulacion
          simular={sesion.acciones.simuladorLocal?simularBorrador:undefined}/>:undefined,
        actualizar:()=>void operar(recuperar),salir:()=>void operar(async()=>{
          await api.current.salir();generation.current++;setSesion({autenticado:false});setSolicitudes(null);
          setInicio(null);setContacto(null);setBot('');setAcepta(false);setMensaje('Tu sesion se cerro de forma segura.');
        })})}</Fragment>:sesion.autenticado&&<>
        <p>Hola{sesion.nombre?', '+sesion.nombre:''}. Este acceso es temporal y personal.</p>
        <div className="cp-session-actions">
          {sesion.acciones.seguimiento&&<button className="btn btn-outline-primary" disabled={busy} onClick={()=>void operar(recuperar)}>Actualizar estado</button>}
          <button className="btn btn-outline-secondary" disabled={busy} onClick={()=>void operar(async()=>{
            await api.current.salir();generation.current++;setSesion({autenticado:false});setSolicitudes(null);
            setInicio(null);setBot('');setAcepta(false);setMensaje('Tu sesion se cerro de forma segura.');
          })}>Cerrar sesion</button>
        </div>
        {!sesion.acciones.seguimiento&&<p role="status">Tu identidad esta verificada. La consulta de solicitudes aun no esta habilitada en este canal.</p>}
        {solicitudes?.length===0&&<p role="status">No tienes solicitudes disponibles para seguimiento en este canal.</p>}
        {solicitudes?.map(s=><article className="cp-request" key={s.referencia} data-testid="solicitud-publica">
          <h2>Mi solicitud CrediHoy</h2><p className="cp-business-status">{s.institucional?'Aprobacion registrada; firma pendiente':s.titulo}</p>
          <dl className="cp-metrics"><div><dt>Monto solicitado</dt><dd>{moneda(s.monto)}</dd></div>
            <div><dt>Plazo</dt><dd>{s.plazoMeses} meses</dd></div><div><dt>Periodicidad</dt><dd>{s.periodicidad}</dd></div>
            <div><dt>Requisitos cumplidos</dt><dd>{s.requisitosCumplidos??'Por verificar'}</dd></div></dl>
          {s.pendientes.length>0&&<><h3>Para continuar</h3><ul>{s.pendientes.map(p=><li key={p}>{p}</li>)}</ul></>}
          <p className="cp-next">{s.siguientePaso}</p>
          {s.institucional?<p>Aprobacion registrada el {s.institucional.fechaDecision}. Firma y formalizacion pendientes; no acredita desembolso.</p>:
            <p>Condiciones de oferta: pendientes de validacion. No hay una aprobacion confirmada.</p>}
          <p className="cp-created">Solicitud creada el {new Date(s.fechaCreacion).toLocaleDateString('es-CO')}.</p>
        </article>)}
      </>}
    </section>
  </CredihoyShell>;
}
