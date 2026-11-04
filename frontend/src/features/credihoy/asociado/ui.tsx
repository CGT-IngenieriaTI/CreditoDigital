import {AlertCircle,Check,CheckCircle2,Clock3,Info,LockKeyhole,type LucideIcon} from 'lucide-react';
import type {ReactNode} from 'react';
import './tokens.css';

type Tono='neutral'|'success'|'warning'|'error';
const ICONOS:Record<Tono,LucideIcon>={neutral:Info,success:CheckCircle2,warning:Clock3,error:AlertCircle};

export function Aviso({children,titulo,tono='neutral',alerta=false}:{children:ReactNode;titulo?:string;tono?:Tono;alerta?:boolean}){
  const Icono=ICONOS[tono];
  return <div className={'ca-alert ca-alert--'+tono} role={alerta?'alert':'status'}>
    <Icono size={20} aria-hidden="true"/><div>{titulo&&<strong>{titulo}</strong>}<div>{children}</div></div>
  </div>;
}

export function Etiqueta({children,tono='neutral'}:{children:ReactNode;tono?:Tono}){
  const Icono=ICONOS[tono];
  return <span className={'ca-tag ca-tag--'+tono}><Icono size={14} aria-hidden="true"/>{children}</span>;
}

export function EstadoVacio({titulo,children,icono:Icono=Clock3}:{titulo:string;children:ReactNode;icono?:LucideIcon}){
  return <div className="ca-empty"><span className="ca-icon-tile"><Icono size={25} aria-hidden="true"/></span>
    <h3>{titulo}</h3><p>{children}</p></div>;
}

export function Cargando({texto='Recuperando tu informacion...'}:{texto?:string}){
  return <div className="ca-skeleton" role="status"><p><span className="ca-spinner" aria-hidden="true"/>{texto}</p>
    <div className="ca-skeleton-lines" aria-hidden="true"><span/><span/><span/></div></div>;
}

export function ProgresoAcceso({etapa}:{etapa:number}){
  return <ol className="cp-steps" aria-label="Progreso de acceso">
    {['Tus datos','Verificacion','Tu solicitud'].map((label,i)=><li key={label}
      className={i<etapa?'is-complete':''} aria-current={etapa===i?'step':undefined}>
      <span aria-hidden="true">{i<etapa?<Check size={15}/>:i+1}</span><b>{label}</b>
    </li>)}
  </ol>;
}

export function NotaPrivacidad(){
  return <p className="ca-private-note"><LockKeyhole size={15} aria-hidden="true"/>
    Tu acceso es personal. Nunca compartas tu codigo.</p>;
}
