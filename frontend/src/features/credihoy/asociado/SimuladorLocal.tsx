import {useEffect,useRef,useState} from 'react';
import {ArrowRight,Calculator,RefreshCw} from 'lucide-react';
import type {EscenarioLocal,SimulacionLocal} from '../services/simulacion';
import {Aviso} from './ui';
import './simulador.css';

export type OperarSimulacion=(sid:string,escenario?:EscenarioLocal)=>Promise<SimulacionLocal>;
const cop=(v:string)=>new Intl.NumberFormat('es-CO',{style:'currency',currency:'COP',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(v));
export function SimuladorLocal({sid,montoInicial,plazoInicial,operar,continuar}:{sid:string;montoInicial:string;
  plazoInicial:number;operar:OperarSimulacion;continuar:()=>void}){
  const [monto,setMonto]=useState(montoInicial),[plazo,setPlazo]=useState(String(plazoInicial));
  const [data,setData]=useState<SimulacionLocal|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const alive=useRef(true),guard=useRef(false),generation=useRef(0);
  useEffect(()=>{alive.current=true;void run();return()=>{alive.current=false;};},[sid]);
  async function run(escenario?:EscenarioLocal){
    if(guard.current)return;
    guard.current=true;const token=++generation.current;setBusy(true);setError('');
    setData(previous=>previous?{...previous,resultado:null}:null);
    try{const value=await operar(sid,escenario);if(alive.current&&token===generation.current){setData(value);}}
    catch{if(alive.current&&token===generation.current)setError('No pudimos calcular esta simulacion. Revisa el monto, el plazo y los limites vigentes; puedes volver a consultar.');}
    finally{guard.current=false;if(alive.current)setBusy(false);}
  }
  function clear(){generation.current++;setError('');setData(previous=>previous?{...previous,resultado:null}:null);}
  const l=data?.limites,r=data?.resultado;
  useEffect(()=>{
    if(busy||error||!l||!monto||!plazo||Number(monto)<Number(l.montoMinimo)||Number(monto)>Number(l.montoMaximo)
      ||!Number.isInteger(Number(plazo))||Number(plazo)<l.cuotasMinimas||Number(plazo)>l.cuotasMaximas
      ||r&&Number(r.monto)===Number(monto)&&r.plazoMeses===Number(plazo))return;
    const timer=setTimeout(()=>void run({monto,plazoMeses:Number(plazo)}),650);
    return()=>clearTimeout(timer);
  },[monto,plazo,l?.version,busy,error,r?.monto,r?.plazoMeses]);
  return <section className="ca-local-simulator" aria-label="Simulador orientativo CrediHoy" aria-busy={busy}>
    <h3>Simula tu credito</h3>
    <p>Estimacion generica. No determina tu elegibilidad, capacidad de pago ni monto aprobado.</p>
    {error&&<Aviso tono="warning" titulo="Simulacion no disponible">{error}</Aviso>}
    {busy&&<p role="status">Calculando...</p>}
    {!l&&!busy&&<button type="button" className="btn btn-outline-primary" onClick={()=>void run()}>
      <RefreshCw size={17} aria-hidden="true"/>Consultar parametros</button>}
    <form onSubmit={e=>{e.preventDefault();void run({monto,plazoMeses:Number(plazo)});}}>
      <fieldset className="ca-form-grid" disabled={busy||!l}>
        <label>Monto a simular (COP)<input className="form-control" name="montoSimulado" type="number" inputMode="decimal"
          required min={l?.montoMinimo} max={l?.montoMaximo} step="0.01" value={monto}
          onChange={e=>{setMonto(e.target.value);clear();}}/></label>
        <label>Plazo en meses<input className="form-control" name="plazoSimulado" type="number" inputMode="numeric"
          required min={l?.cuotasMinimas??1} max={l?.cuotasMaximas??48} step="1" value={plazo}
          onChange={e=>{setPlazo(e.target.value);clear();}}/></label>
        {l&&<><label>Ajustar monto<input type="range" min={l.montoMinimo} max={l.montoMaximo} step="0.01" value={monto} onChange={e=>{setMonto(e.target.value);clear();}}/></label>
          <label>Ajustar plazo<input type="range" min={l.cuotasMinimas} max={r?.plazoMaximoParaMonto??l.cuotasMaximas} step="1" value={plazo} onChange={e=>{setPlazo(e.target.value);clear();}}/></label></>}
      </fieldset>
      {l&&<p className="ca-muted">Monto: {cop(l.montoMinimo)} a {cop(l.montoMaximo)}. Plazo: {l.cuotasMinimas} a {l.cuotasMaximas} meses, sujeto al rango del monto.</p>}
      <button className="btn btn-primary" type="submit" disabled={busy||!l}><Calculator size={17} aria-hidden="true"/>Calcular cuota estimada</button>
    </form>
    {data&&<p>CrediHoy {data.tasa.linea==='LIBRANZA'?'Libranza':'Ventanilla'}: {data.tasa.valor} % NAMV. Periodicidad mensual.</p>}
    {r&&<div aria-live="polite">
      <dl className="ca-metrics"><div><dt>Cuota estimada</dt><dd>{cop(r.cuotaEstimada)}</dd></div>
        <div><dt>Capital e interes por cuota</dt><dd>{cop(r.cuotaCapitalInteres)}</dd></div>
        <div><dt>Seguro mensual estimado</dt><dd>{cop(r.seguro.valorMensualEstimado)}</dd></div>
        <div><dt>Total estimado</dt><dd>{cop(r.totalEstimado)}</dd></div></dl>
      <p>Seguro de vida: {Number(r.seguro.tasaMensualDecimal)*100} % mensual sobre capital inicial de {cop(r.seguro.baseCOP)}.</p>
      <p>El seguro mostrado es estimado sobre el capital inicial. Su valor definitivo se informa en el plan de pagos.</p>
      <details><summary>Amortizacion estimada</summary>
        <p>Capital: {cop(r.totalCapital)}. Intereses: {cop(r.totalIntereses)}. Seguro: {cop(r.totalSeguroEstimado)}. La ultima cuota ajusta centavos.</p>
        <div className="ca-amortizacion" role="region" aria-label="Amortizacion mensual estimada" tabIndex={0}>
          <table><caption>Valores estimados en COP</caption><thead><tr>
            <th scope="col">Mes</th><th scope="col">Saldo inicial</th><th scope="col">Capital</th><th scope="col">Interes</th>
            <th scope="col">Capital + interes</th><th scope="col">Seguro</th><th scope="col">Cuota estimada</th><th scope="col">Saldo final</th>
          </tr></thead><tbody>{r.amortizacion.map(a=><tr key={a.numero}><th scope="row">{a.numero}</th>
            {[a.saldoInicial,a.capital,a.intereses,a.subtotalCapitalInteres,a.seguroEstimado,a.cuotaEstimada,a.saldoFinal].map((v,i)=><td key={i}>{cop(v)}</td>)}
          </tr>)}</tbody></table>
        </div>
      </details>
      <h4>Conceptos pendientes, no incluidos</h4>
      <ul><li>Capitalizacion de aportes, si aplica.</li><li>Otros conceptos aplicables por confirmar.</li>
        <li>Intereses iniciales y calendario definitivo de pagos.</li></ul>
      <p>El total estimado incluye capital, intereses mensuales y seguro estimado. No constituye aprobacion ni oferta contractual.</p>
      <button type="button" className="btn btn-outline-primary" onClick={continuar}>Continuar al estudio<ArrowRight size={17} aria-hidden="true"/></button>
    </div>}
    <p className="ca-notice">Cuota estimada. El valor definitivo y los conceptos aplicables ser&#225;n informados en el plan de pagos antes de la aceptaci&#243;n del cr&#233;dito.</p>
  </section>;
}
