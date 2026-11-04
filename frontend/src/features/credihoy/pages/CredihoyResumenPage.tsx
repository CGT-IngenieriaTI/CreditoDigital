import type { FormEvent } from "react";
import { Actions, moneda, valorFinanciero } from "../components/FormControls";
import { autorizacionesCompletas, urlDocumentoSegura } from "../state";
import type { CredihoyState, ErroresFormulario } from "../types";
import { IntegrationState } from "../components/IntegrationState";
import type { IntegracionCredihoy } from "../types";

export function CredihoyResumenPage({ state, onConsent, onContinue, onBack, busy, errores, shadow = false, historico = false, integracion, onRetry }: {
  state: CredihoyState; onConsent: (id: string, aceptado: boolean) => void;
  shadow?: boolean;
  historico?: boolean;
  integracion?: IntegracionCredihoy;
  onRetry?: () => void;
  onContinue: (event: FormEvent) => void; onBack: () => void; busy: boolean; errores: ErroresFormulario;
}) {
  const seleccionada = state.consultaObligaciones?.obligaciones.find(o => o.referencia === state.solicitud.obligacionRecogidaReferencia);
  const resultadosDisponibles = state.condiciones?.cuota.disponible || state.condiciones?.capacidad.disponible;
  return <form onSubmit={onContinue} noValidate>
    <div className="ch-summary-grid">
      <section><h2>Datos del solicitante</h2><dl className="ch-detail-list">
        <div><dt>Nombre</dt><dd>{state.datos.nombres} {state.datos.apellidos}</dd></div>
        <div><dt>Identificación</dt><dd>{state.datos.tipoIdentificacion} {state.datos.identificacion}</dd></div>
        <div><dt>Celular</dt><dd>{state.datos.celular}</dd></div><div><dt>Correo</dt><dd>{state.datos.correo}</dd></div>
      </dl></section>
      <section><h2>Confirma tu solicitud</h2><dl className="ch-detail-list">
        <div><dt>Monto solicitado</dt><dd>{moneda(state.solicitud.monto)}</dd></div>
        <div><dt>Plazo</dt><dd>{state.solicitud.plazoMeses} meses</dd></div>
        <div><dt>Periodicidad</dt><dd>Mensual</dd></div>
        <div><dt>CrediHoy vigente incluido</dt><dd>{state.solicitud.recogerCredihoy ? seleccionada?.descripcion : "No solicitado"}</dd></div>
      </dl></section>
    </div>
    <section className="ch-section-divider"><h2>Información declarada por ti</h2>
      <dl className="ch-detail-list ch-detail-list--wide">
        <div><dt>Ingresos mensuales</dt><dd>{moneda(state.finanzas.ingresos)}</dd></div>
        <div><dt>Otros ingresos mensuales</dt><dd>{moneda(state.finanzas.otrosIngresos)}</dd></div>
        <div><dt>Egresos / gastos mensuales</dt><dd>{moneda(state.finanzas.gastos)}</dd></div>
      </dl>
    </section>
    {resultadosDisponibles && <section className="ch-section-divider"><h2>Resultados de evaluación informados</h2>
      <dl className="ch-detail-list">
        {state.condiciones?.cuota.disponible && state.condiciones.cuota.valor !== null
          && <div><dt>Cuota mensual</dt><dd>{valorFinanciero(state.condiciones.cuota)}</dd></div>}
        {state.condiciones?.capacidad.disponible && state.condiciones.capacidad.valor !== null
          && <div><dt>Capacidad de pago</dt><dd>{valorFinanciero(state.condiciones.capacidad)}</dd></div>}
      </dl></section>}
    <section className="ch-section-divider"><div className="ch-section-heading"><h2>Autorizaciones</h2><p>Consulta cada documento antes de aceptar. Las autorizaciones obligatorias están señaladas.</p></div>
      <div className="ch-consents" id="ch-consentimientos" tabIndex={-1}>{state.contexto?.documentos.map(d => <div className="ch-consent" key={d.id}>
        <details><summary>{d.titulo}<span className="ch-document-version">Versión {d.version}</span></summary>
          {d.contenido && <p>{d.contenido}</p>}
          {urlDocumentoSegura(d.url) && <a href={urlDocumentoSegura(d.url)!} target="_blank" rel="noopener noreferrer">Consultar documento</a>}
        </details>
        <label className="ch-checkbox" htmlFor={`ch-consent-${d.id}`}>
          <input id={`ch-consent-${d.id}`} type="checkbox" checked={state.consentimientos[d.id] === true}
            aria-required={d.requerido} aria-describedby={errores.consentimientos ? "consentimientos-error" : undefined}
            onChange={event => onConsent(d.id, event.target.checked)} />
          <span>He leído y acepto {d.titulo.toLowerCase()}{d.requerido ? " (obligatorio)" : " (opcional)"}</span>
        </label>
      </div>)}</div>
      {errores.consentimientos && <p id="consentimientos-error" role="alert" className="text-danger mt-3">{errores.consentimientos}</p>}
    </section>
    {shadow && <p className="ch-note">{historico ? "Se creará una solicitud exclusivamente local con identidad autorizada y resultado shadow. Las declaraciones no se usarán como ingresos certificados. No se consultarán proveedores ni se radicará un crédito." : "Evaluación local con hechos financieros sintéticos. Los datos personales y económicos declarados no se enviarán al backend. No se creará una solicitud."}</p>}
    {integracion && <IntegrationState integracion={integracion} onRetry={onRetry} />}
    <Actions onBack={onBack} busy={busy} busyLabel={shadow ? "Evaluando shadow..." : "Un momento..."} label={shadow ? "Evaluar shadow" : "Enviar solicitud"} disabled={!autorizacionesCompletas(state)} />
  </form>;
}
