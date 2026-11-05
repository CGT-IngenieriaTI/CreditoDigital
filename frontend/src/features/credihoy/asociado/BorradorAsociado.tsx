import {useEffect,useRef,useState} from 'react';
import {Calculator,RefreshCw,Save,Send} from 'lucide-react';
import type {AvisoTemporal,BorradorPublico,EstadoBorrador,GuardarBorrador,PerfilPersonal,SimulacionBorrador} from '../services/publico';
import type {EscenarioLocal} from '../services/simulacion';
import {AccesoPublicoError} from '../services/publico';
import {Aviso} from './ui';

type Command=GuardarBorrador|{operacion:'borrador';accion:'retomar';actividad?:boolean};
export function BorradorAsociado({operar,alEnviar,nombre,perfil,simular,recogidaDisponible,iniciarEnSimulacion=false}:{operar:(body:Command)=>Promise<EstadoBorrador>;alEnviar?:()=>Promise<void>;nombre?:string|null;perfil?:PerfilPersonal;recogidaDisponible?:boolean;iniciarEnSimulacion?:boolean;simular?:(id:string,escenario?:EscenarioLocal)=>Promise<SimulacionBorrador>}){
  const [draft,setDraft]=useState<BorradorPublico|null>(null),[ready,setReady]=useState(false);
  const [busy,setBusy]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [monto,setMonto]=useState(''),[plazo,setPlazo]=useState(''),[email,setEmail]=useState('');
  const [periodicidad,setPeriodicidad]=useState<''|'MENSUAL'>('MENSUAL'),[recoger,setRecoger]=useState<''|'SI'|'NO'>('');
  const [obligacion,setObligacion]=useState('');
  const [ayuda,setAyuda]=useState(false),[promotor,setPromotor]=useState('');
  const [aviso,setAviso]=useState<AvisoTemporal|null>(null),[acepta,setAcepta]=useState(false);
  const [etapa,setEtapa]=useState(iniciarEnSimulacion?1:0),[errores,setErrores]=useState<Record<string,string>>({});
  const [ahora,setAhora]=useState(performance.now()),[finLocal,setFinLocal]=useState<number|null>(null);
  const guard=useRef(false),key=useRef('');
  const [simulacion,setSimulacion]=useState<SimulacionBorrador|null>(null),[calculando,setCalculando]=useState(false),[errorCuota,setErrorCuota]=useState('');
  const secuenciaSim=useRef(0);
  const resultado=simulacion?.resultado;
  const cuotaVigente=Boolean(resultado&&Number(resultado.monto)===Number(monto)&&resultado.plazoMeses===Number(plazo));
  const moneda=(value:string)=>Number(value).toLocaleString('es-CO',{style:'currency',currency:'COP',minimumFractionDigits:2});
  function aplicar(packet:EstadoBorrador){
    const value=packet.borrador;
    if(aviso&&(aviso.version!==packet.avisoTemporal.version||aviso.sha256!==packet.avisoTemporal.sha256))setAcepta(false);
    setAviso(packet.avisoTemporal);
    setFinLocal(value?.venceAt?performance.now()+Math.max(0,Date.parse(value.venceAt)-Date.parse(value.consultadoAt??value.actualizadoAt)):null);
    setDraft(value);setMonto(value?.datos.monto??'');setPlazo(String(value?.datos.plazo??''));setEmail(value?.datos.email??perfil?.correoRegistrado??'');
    setPeriodicidad('MENSUAL');setRecoger(value?.datos.recogerCredihoy===undefined?'':value.datos.recogerCredihoy?'SI':'NO');
    setObligacion(value?.datos.obligacionRecoger??'');
    setAyuda(Boolean(value?.promotor));setPromotor(value?.promotor??'');
    key.current=value?.referencia??'';
  }
  useEffect(()=>{if(finLocal===null)return;const timer=setInterval(()=>setAhora(performance.now()),1000);return()=>clearInterval(timer);},[finLocal]);
  useEffect(()=>{
    let active=true;
    operar({operacion:'borrador',accion:'retomar'}).then(value=>{if(active){aplicar(value);setReady(true);}})
      .catch(()=>{if(active)setError('No pudimos recuperar tu borrador. No crearemos otro automaticamente.');})
      .finally(()=>{if(active)setBusy(false);});
    return()=>{active=false;};
  },[operar]);
  async function recuperar(){
    if(guard.current)return;guard.current=true;setBusy(true);setError('');setNotice('');
    try{const result=await operar({operacion:'borrador',accion:'retomar',actividad:true});
      if(result.borrador)aplicar(result);else{setAviso(result.avisoTemporal);setNotice('Todavia no tienes una solicitud guardada. Puedes comenzar una nueva.');}
      setReady(true);}
    catch{setReady(false);setError('No pudimos recuperar tu borrador. Intenta consultar mas tarde.');}
    finally{guard.current=false;setBusy(false);}
  }
  const enviada=draft?.fase==='ENVIADA';
  function avanzar(){
    const next:Record<string,string>={};
    if(etapa===0&&email&&!/^\S+@\S+\.\S+$/.test(email))next.email='Ingresa un correo valido.';
    if(etapa===1){
      if(!monto||!Number.isFinite(Number(monto))||Number(monto)<=0)next.monto='Ingresa el monto que deseas solicitar.';
      if(!plazo||!Number.isInteger(Number(plazo))||Number(plazo)<1||Number(plazo)>48)next.plazo='Elige un plazo entre 1 y 48 meses.';
      if(recogidaDisponible&&!recoger)next.recoger='Confirma si deseas pagar un credito vigente.';
      if(recogidaDisponible&&recoger==='SI'&&!/^[0-9]{1,20}$/.test(obligacion))next.obligacion='Revisa la referencia del credito.';
    }
    if(etapa===1&&simular&&!cuotaVigente)next.cuota='Calcula la cuota estimada de estas condiciones antes de continuar.';
    setErrores(next);if(!Object.keys(next).length)setEtapa(Math.min(2,etapa+1));
  }
  const vencido=finLocal!==null&&finLocal<=ahora;
  useEffect(()=>{
    if(!vencido)return;
    setDraft(null);setMonto('');setPlazo('');setEmail('');setAyuda(false);setPromotor('');
    setPeriodicidad('');setRecoger('');setObligacion('');
    setAcepta(false);setReady(false);setFinLocal(null);key.current='';
    setNotice('El progreso temporal vencio. Recupera el estado para comenzar de nuevo.');
  },[vencido]);
  async function guardar(accion:'guardar'|'enviar'){
    if(guard.current||!ready||!aviso||!acepta||vencido||enviada)return;
    guard.current=true;setBusy(true);setError('');setNotice('');
    key.current=key.current||crypto.randomUUID();
    const body:GuardarBorrador={operacion:'borrador',accion,clave:key.current,operacionId:crypto.randomUUID(),
      version:draft?.version??0,datos:{...(monto?{monto}:{}),...(plazo?{plazo:Number(plazo)}:{}),...(email?{email}:{}),
        ...(periodicidad?{periodicidadPago:periodicidad}:{}),...(recoger?{recogerCredihoy:recoger==='SI'}:{}),
        ...(recoger==='SI'&&obligacion?{obligacionRecoger:obligacion}:{})},
      promotor:ayuda?promotor:null,aceptacion:{version:aviso.version,sha256:aviso.sha256,presentacion:aviso.presentacion,acepta:true}};
    try{const packet=await operar(body);aplicar(packet);setNotice(accion==='enviar'?'Recibimos tu solicitud de CrediHoy. No constituye aprobacion.':'Tu informacion se guardo correctamente. Podras continuar despues.');
      if(accion==='enviar')await alEnviar?.();return packet.borrador;}
    catch(e){
      const status=e instanceof AccesoPublicoError?e.status:0;
      setReady(status===400||status===422);
      setError(status===401||status===403?'Tu sesion termino o no permite esta accion. Vuelve a ingresar para recuperar el progreso que siga vigente.':
        status===400||status===422?'Revisa los campos del formulario. Tus cambios siguen en pantalla.':
        status===409?'Tu solicitud tiene una version mas reciente. Recupera el borrador antes de continuar.':
        'No pudimos confirmar el guardado. Tus cambios siguen en pantalla. Recupera el estado antes de repetir la operacion.');
    }
    finally{guard.current=false;setBusy(false);}
  }
  async function calcular(reference=draft?.referencia){
    if(!simular||!monto||!plazo)return;
    const seq=++secuenciaSim.current;setCalculando(true);setErrorCuota('');
    try{
      if(!reference){const saved=await guardar('guardar');reference=saved?.referencia;}
      if(!reference)throw new Error('PROGRESO_NO_CONFIRMADO');
      const parameters=simulacion?.borradorId===reference?simulacion:await simular(reference);
      if(seq===secuenciaSim.current)setSimulacion(parameters);
      const limits=parameters.limites;
      if(Number(monto)<Number(limits.montoMinimo)||Number(monto)>Number(limits.montoMaximo)
        ||!Number.isInteger(Number(plazo))||Number(plazo)<limits.cuotasMinimas||Number(plazo)>limits.cuotasMaximas){
        if(seq===secuenciaSim.current)setErrorCuota(`Elige un monto entre ${moneda(limits.montoMinimo)} y ${moneda(limits.montoMaximo)}, y un plazo de ${limits.cuotasMinimas} a ${limits.cuotasMaximas} meses, sujeto al monto.`);
        return;
      }
      const value=await simular(reference,{monto,plazoMeses:Number(plazo)});
      if(seq===secuenciaSim.current)setSimulacion(value);
    }catch{if(seq===secuenciaSim.current)setErrorCuota('No pudimos calcular la cuota. Revisa los limites disponibles o vuelve a consultar; tus datos siguen guardados.');}
    finally{if(seq===secuenciaSim.current)setCalculando(false);}
  }
  useEffect(()=>{
    if(!draft||enviada||!simular||!monto||!plazo)return;
    const timer=setTimeout(()=>void calcular(draft.referencia),650);
    return()=>{clearTimeout(timer);secuenciaSim.current++;};
  },[monto,plazo,draft?.referencia,simular,enviada]);
  useEffect(()=>()=>{secuenciaSim.current++;},[]);
  return <section className="ca-document-band" aria-labelledby="ca-draft-title" aria-busy={busy}>
    <p className="ch-eyebrow">Tu solicitud</p><h3 id="ca-draft-title">{enviada?'Solicitud recibida':draft?'Retoma tu progreso':'Prepara tu solicitud'}</h3>
    <ol aria-label="Etapas de tu solicitud" className="ca-originacion-steps">{[iniciarEnSimulacion?'Identidad verificada':'Tus datos','Tu credito','Revisa y envia'].map((label,i)=><li key={label} aria-current={etapa===i?'step':undefined}>{label}</li>)}</ol>
    {nombre&&etapa===0&&<dl className="ca-authorizations"><div><dt>Titular verificado</dt><dd>{nombre}</dd></div></dl>}
    <p className="ca-notice">{enviada?'Tu solicitud fue recibida. La consulta de centrales y los documentos tienen autorizaciones independientes.':'Puedes guardar tus datos y continuar despues. Guardarlos no autoriza consultas a centrales ni acepta un credito.'}</p>
    {draft?.venceAt&&<p role="status">{vencido?'Tu progreso vencio. Recupera el estado para comenzar de nuevo.':`Disponible hasta ${new Date(draft.venceAt).toLocaleTimeString('es-CO')}.`}</p>}
    {error&&<Aviso tono="error" alerta>{error}</Aviso>}
    {notice&&<p role="status">{notice}</p>}
    {draft&&<p role="status">Ultimo guardado: {new Date(draft.actualizadoAt).toLocaleTimeString('es-CO')}.</p>}
    <form onSubmit={event=>{
      event.preventDefault();void guardar('guardar');
    }}>
      <fieldset className="ch-page-fields" disabled={busy||!ready||enviada||vencido}>
      {etapa===0&&<div className="ch-fields">
        {perfil&&<dl className="ca-authorizations"><div><dt>Correo registrado</dt><dd>{perfil.correoRegistrado??'Sin correo registrado'}{perfil.correoRegistrado&&(perfil.correoVerificado?' · Verificado en este ingreso':' · Registrado en Congente')}</dd></div>
          {perfil.contactoEnmascarado&&<div><dt>Contacto de acceso</dt><dd>{perfil.contactoEnmascarado}</dd></div>}</dl>}
        <label>Correo de contacto<input className="form-control" type="email" maxLength={254} value={email} onChange={e=>setEmail(e.target.value)} aria-invalid={Boolean(errores.email)} aria-describedby={errores.email?'draft-email-error':undefined}/></label>
        {email&&email!==perfil?.correoRegistrado&&<p className="ca-notice">Correo propuesto para esta solicitud, aun no verificado. No cambia tu contacto registrado.</p>}
        {errores.email&&<p id="draft-email-error" role="alert">{errores.email}</p>}
      </div>}
      {etapa===1&&<><p className="ca-notice">Estimacion sujeta a evaluacion. No representa un monto aprobado ni una oferta contractual.</p><div className="ch-fields">
        <label>Monto que deseas solicitar<input className="form-control" type="number" min="0.01" max="9999999999.99" step="0.01" value={monto} onChange={e=>setMonto(e.target.value)}/></label>
        {errores.monto&&<p role="alert">{errores.monto}</p>}
        <label>Plazo en meses<input className="form-control" type="number" min="1" max="48" step="1" value={plazo} onChange={e=>setPlazo(e.target.value)}/></label>
        {errores.plazo&&<p role="alert">{errores.plazo}</p>}
        <p>Pago mensual.</p>
      </div>
      {recogidaDisponible&&<fieldset><legend>¿Deseas pagar tu CrediHoy actual con este nuevo credito?</legend>
        <label className="ch-checkbox"><input type="radio" name="recogida" checked={recoger==='SI'} onChange={()=>setRecoger('SI')}/>Si</label>
        <label className="ch-checkbox"><input type="radio" name="recogida" checked={recoger==='NO'} onChange={()=>{setRecoger('NO');setObligacion('');}}/>No</label>
        {errores.recoger&&<p role="alert">{errores.recoger}</p>}
        {recoger==='SI'&&<label>Numero de la obligacion<input className="form-control" inputMode="numeric" pattern="[0-9]{1,20}" maxLength={20} value={obligacion} onChange={e=>setObligacion(e.target.value)}/></label>}
        {errores.obligacion&&<p role="alert">{errores.obligacion}</p>}
      </fieldset>}
      {simular&&<section aria-label="Cuota estimada de tu solicitud" aria-busy={calculando}>
        {simulacion?.limites&&<>
          <label>Ajustar monto<input type="range" min={simulacion.limites.montoMinimo} max={simulacion.limites.montoMaximo} step="0.01" value={monto} onChange={e=>setMonto(e.target.value)}/></label>
          <label>Ajustar plazo<input type="range" min={simulacion.limites.cuotasMinimas} max={simulacion.limites.cuotasMaximas} step="1" value={plazo} onChange={e=>setPlazo(e.target.value)}/></label>
          <p>Monto: {moneda(simulacion.limites.montoMinimo)} a {moneda(simulacion.limites.montoMaximo)}. Plazo: {simulacion.limites.cuotasMinimas} a {simulacion.limites.cuotasMaximas} meses, segun el monto.</p>
        </>}
        {calculando&&<p role="status">Calculando tu cuota...</p>}
        {cuotaVigente&&resultado&&<dl className="ca-metrics"><div><dt>Cuota estimada mensual</dt><dd>{moneda(resultado.cuotaEstimada)}</dd></div><div><dt>Capital e intereses</dt><dd>{moneda(resultado.cuotaCapitalInteres)}</dd></div><div><dt>Seguro mensual estimado</dt><dd>{moneda(resultado.seguro.valorMensualEstimado)}</dd></div></dl>}
        {errorCuota&&<p role="alert">{errorCuota}</p>}{errores.cuota&&<p role="alert">{errores.cuota}</p>}
        <button className="btn btn-outline-secondary" type="button" disabled={calculando||!acepta||!monto||!plazo} onClick={()=>void calcular()}><Calculator size={17} aria-hidden="true"/>{draft?'Calcular cuota estimada':'Guardar y calcular cuota'}</button>
        <p className="ca-notice">Estimacion no contractual. El valor definitivo se informa antes de aceptar el credito.</p>
      </section>}
      </>}
      {etapa===2&&<dl className="ca-authorizations">
        <div><dt>Correo de contacto</dt><dd>{email||'Sin correo adicional'}</dd></div>
        <div><dt>Monto solicitado</dt><dd>{Number(monto).toLocaleString('es-CO',{style:'currency',currency:'COP'})}</dd></div>
        <div><dt>Plazo</dt><dd>{plazo} meses, pago mensual</dd></div>
        {recoger&&<div><dt>Pago de credito vigente</dt><dd>{recoger==='SI'?'Solicitado':'No solicitado'}</dd></div>}
        <div><dt>Cuota estimada</dt><dd>{cuotaVigente&&resultado?moneda(resultado.cuotaEstimada):'Pendiente de calcular para estas condiciones.'}</dd></div>
      </dl>}
      {aviso&&<><details><summary>Aviso de progreso temporal</summary><p className="ca-notice">{aviso.texto}</p>
        <a href="https://www.congente.coop/images/Balancesocial/GER-PO.085-Politica_tratamiento_datos_personales.pdf" target="_blank" rel="noreferrer">Politica GER-PO.085 v03</a></details>
        <label className="ch-checkbox"><input type="checkbox" checked={acepta} onChange={e=>setAcepta(e.target.checked)}/>Acepto el tratamiento de mi informacion conforme al aviso presentado.</label></>}
      <div className="ch-actions"><button className={'btn '+(etapa===2?'btn-outline-secondary':'btn-primary')} type="submit" disabled={busy||!ready||!acepta}><Save size={17} aria-hidden="true"/>Guardar progreso</button>
        {etapa===2&&<button className="btn btn-primary" type="button" disabled={busy||!ready||!acepta||!draft||!monto||!plazo||!periodicidad||(recogidaDisponible&&!recoger)||(simular&&!cuotaVigente)||(recoger==='SI'&&!obligacion)} onClick={()=>void guardar('enviar')}><Send size={17} aria-hidden="true"/>Enviar mi solicitud</button>}</div>
      {!enviada&&<p role="status">{!acepta?'Para guardar, lee y acepta el aviso de progreso temporal.':!draft?'Guarda tu progreso antes de enviar.':!monto||!plazo?'Completa monto y plazo para enviar.':recogidaDisponible&&!recoger?'Confirma si deseas pagar un credito vigente.':recoger==='SI'&&!obligacion?'Completa la referencia del credito que deseas pagar.':''}</p>}
      {!enviada&&<div className="ch-actions">
        {etapa>0&&<button className="btn btn-outline-secondary" type="button" onClick={()=>setEtapa(etapa-1)}>Volver</button>}
        {etapa<2&&<button className="btn btn-outline-secondary" type="button" onClick={avanzar}>Continuar</button>}
      </div>}
      </fieldset>
    </form>
    <button className="btn btn-outline-secondary" type="button" disabled={busy} onClick={()=>void recuperar()}><RefreshCw size={17} aria-hidden="true"/>Recuperar borrador</button>
  </section>;
}
