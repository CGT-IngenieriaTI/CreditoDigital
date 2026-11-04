import type { ReactNode } from "react";
import logo from "../../../assets/LogoHD.png";
import { PASOS_CREDIHOY, type CredihoyState, type PasoCredihoy } from "../types";
import { moneda } from "./FormControls";
import {ShieldCheck} from 'lucide-react';

export const TITULOS_PASOS: Record<PasoCredihoy, string> = {
  inicio: "Inicio", datos: "Datos básicos", financiero: "Información financiera", solicitud: "Monto y plazo", resumen: "Resumen y autorizaciones", estado: "Estado de solicitud",
};
const TITULOS_CORTOS = ["Inicio", "Datos básicos", "Finanzas", "Monto y plazo", "Resumen", "Estado"];

export function CredihoyShell({ state, children, local = false, resultadoShadow = false, mostrarProgreso = true, mostrarResumen = true, asociado = false }: { state: CredihoyState; children: ReactNode; local?: boolean; resultadoShadow?: boolean; mostrarProgreso?: boolean; mostrarResumen?: boolean; asociado?: boolean }) {
  const pasoVisible = resultadoShadow ? "estado" : state.paso;
  const indice = PASOS_CREDIHOY.indexOf(pasoVisible);
  const lateral = mostrarResumen && !resultadoShadow && state.paso !== "inicio" && state.paso !== "estado" && state.paso !== "resumen";
  return <div className={'ch-app'+(asociado?' ch-app--asociado':'')}>
    <header className="ch-header"><div className="ch-container ch-header-inner">
      <img className="ch-logo" src={logo} alt="Congente" />
      <div className="ch-header-product"><strong>CrediHoy</strong><span>Crédito Digital Congente</span></div>
      {asociado&&<span className="ca-header-security"><ShieldCheck size={18} aria-hidden="true"/>Acceso personal</span>}
      {import.meta.env.DEV && local && <span className="ch-local">Entorno local · Datos ficticios</span>}
    </div></header>
    {mostrarProgreso && <nav className="ch-progress" aria-label="Progreso de la solicitud"><div className="ch-container">
      <p className="ch-mobile-step">Paso {indice + 1} de 6 · {resultadoShadow ? "Evaluación shadow" : TITULOS_PASOS[state.paso]}</p>
      <ol>{PASOS_CREDIHOY.map((paso, i) => <li key={paso} aria-label={`Paso ${i + 1}: ${resultadoShadow && i === 5 ? "Evaluación shadow" : TITULOS_PASOS[paso]}`} aria-current={pasoVisible === paso ? "step" : undefined}
        className={`${indice === i ? "is-current" : ""} ${indice > i ? "is-complete" : ""}`}>
        <span className="ch-step-number" aria-hidden="true">{indice > i ? "✓" : i + 1}</span><span>{resultadoShadow && i === 5 ? "Resultado" : TITULOS_CORTOS[i]}</span>
      </li>)}</ol>
    </div></nav>}
    <main className="ch-main ch-container">
      {(state.paso !== "inicio" || resultadoShadow) && <div className="ch-page-heading"><p className="ch-eyebrow">CrediHoy</p>
        <h1 id="ch-step-title" tabIndex={-1}>{resultadoShadow ? "Evaluación shadow" : TITULOS_PASOS[state.paso]}</h1></div>}
      <div className={`ch-workspace ${lateral ? "ch-workspace--summary" : ""}`}>
        <div className="ch-workspace-content">{children}</div>
        {lateral && <aside className="ch-side-summary" aria-label="Resumen de tu solicitud">
          <p className="ch-eyebrow">CrediHoy</p><h2>Resumen de tu solicitud</h2>
          <dl className="ch-detail-list">
            <div><dt>Monto solicitado</dt><dd>{state.solicitud.monto ? moneda(state.solicitud.monto) : "Por definir"}</dd></div>
            <div><dt>Plazo</dt><dd>{state.solicitud.plazoMeses ? `${state.solicitud.plazoMeses} meses` : "Por definir"}</dd></div>
            <div><dt>Recogida</dt><dd>{state.solicitud.recogerCredihoy ? "Solicitada" : "No solicitada"}</dd></div>
          </dl>
          <p className="ch-help">La aprobación y las condiciones definitivas dependen de la evaluación de la solicitud.</p>
        </aside>}
      </div>
    </main>
    <footer className="ch-footer"><div className="ch-container"><span>Crédito Digital Congente</span><span>Tu información, con tu autorización.</span></div></footer>
  </div>;
}
