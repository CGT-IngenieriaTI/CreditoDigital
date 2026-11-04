import type { FormEvent } from "react";
import { Actions, Field, valorFinanciero } from "../components/FormControls";
import type { ConsultaObligaciones, ErroresFormulario, CondicionesProducto, SolicitudCredihoy } from "../types";
import { obligacionesElegibles } from "../validation";

export function CredihoySolicitudPage({ solicitud, parametros, consulta, onChange, onContinue, onBack, busy, errores }: {
  solicitud: SolicitudCredihoy; parametros: CondicionesProducto; consulta: ConsultaObligaciones | null;
  onChange: (value: Partial<SolicitudCredihoy>) => void; onContinue: (event: FormEvent) => void;
  onBack: () => void; busy: boolean; errores: ErroresFormulario;
}) {
  const obligaciones = obligacionesElegibles(consulta);
  const seleccionada = obligaciones.find(o => o.referencia === solicitud.obligacionRecogidaReferencia);
  const configurado = parametros.plazoMinimoMeses !== null && parametros.plazoMaximoMeses !== null;
  const mensajes = {
    SIN_CREDIHOY: "No tienes un CrediHoy vigente para incluir en esta solicitud.",
    CREDIHOY_NO_ELEGIBLE: "Tu CrediHoy vigente no está disponible para incluirlo en esta solicitud.",
    PENDIENTE_CONSULTA: "La información de tu CrediHoy vigente está pendiente de consulta.",
    CREDIHOY_ELEGIBLE: "No hay una obligación identificable disponible para seleccionar.",
  };
  return <form onSubmit={onContinue} noValidate>
    <div className="ch-section-heading"><h2>Tu nuevo CrediHoy</h2><p>Elige el monto y el plazo de tu solicitud.</p></div>
    <div className="ch-fields">
      <Field name="monto" money label="Monto solicitado (COP)" value={solicitud.monto}
        onChange={value => onChange({ monto: value })} error={errores.monto} />
      <div className="ch-field">
        <label className="form-label" htmlFor="ch-plazoMeses">Plazo en meses</label>
        <input id="ch-plazoMeses" name="plazoMeses" type="number" min={parametros.plazoMinimoMeses ?? undefined} max={parametros.plazoMaximoMeses ?? undefined}
          step="1" disabled={!configurado} value={solicitud.plazoMeses} className={`form-control ${errores.plazoMeses ? "is-invalid" : ""}`}
          aria-invalid={Boolean(errores.plazoMeses)} aria-describedby={errores.plazoMeses ? "plazo-help plazo-error" : "plazo-help"}
          onChange={event => onChange({ plazoMeses: event.target.value })} />
        {configurado && <input type="range" className="form-range ch-range" aria-label="Seleccionar plazo en meses"
          min={parametros.plazoMinimoMeses!} max={parametros.plazoMaximoMeses!} step="1"
          value={Number(solicitud.plazoMeses) || parametros.plazoMinimoMeses!}
          onChange={event => onChange({ plazoMeses: event.target.value })} />}
        <div className="ch-help" id="plazo-help">{configurado
          ? `De ${parametros.plazoMinimoMeses} a ${parametros.plazoMaximoMeses} meses.` : "Condiciones de plazo pendientes de confirmación."}</div>
        {errores.plazoMeses && <div className="invalid-feedback d-block" id="plazo-error">{errores.plazoMeses}</div>}
      </div>
    </div>
    <div className="ch-payment-line"><span>Periodicidad de pago</span><strong>{parametros.periodicidades.includes("MENSUAL") ? "Mensual" : "Pendiente de confirmación"}</strong></div>
    {errores.periodicidadPago && <p className="text-danger" role="alert">{errores.periodicidadPago}</p>}
    <section className="ch-section-divider ch-pickup">
      <div className="ch-section-heading"><h2>Tu CrediHoy vigente</h2><p>Si está disponible, puedes incluirlo con tu selección expresa.</p></div>
      {obligaciones.length ? <>
        <label className="ch-checkbox" htmlFor="ch-recoger">
          <input id="ch-recoger" type="checkbox" checked={solicitud.recogerCredihoy} onChange={event => onChange({ recogerCredihoy: event.target.checked })} />
          <span>Incluir mi CrediHoy vigente en esta solicitud</span>
        </label>
        {solicitud.recogerCredihoy && <div className="ch-pickup-selection">
          <Field name="obligacionRecogidaReferencia" label="CrediHoy que deseas incluir" value={solicitud.obligacionRecogidaReferencia ?? ""}
            error={errores.obligacionRecogidaReferencia} onChange={value => onChange({ obligacionRecogidaReferencia: value || null })}>
            <option value="">Selecciona tu CrediHoy</option>
            {obligaciones.map(o => <option key={o.referencia} value={o.referencia!}>{o.descripcion}</option>)}
          </Field>
          {seleccionada?.cuotaMensual.disponible && seleccionada.cuotaMensual.valor !== null
            && <dl className="ch-detail-list"><div><dt>Cuota vigente informada</dt><dd>{valorFinanciero(seleccionada.cuotaMensual)}</dd></div></dl>}
        </div>}
      </> : <div className="ch-note ch-note--neutral" role="status">{mensajes[consulta?.estado ?? "PENDIENTE_CONSULTA"]}</div>}
    </section>
    <Actions onBack={onBack} busy={busy} disabled={!configurado || !parametros.periodicidades.length} />
  </form>;
}
