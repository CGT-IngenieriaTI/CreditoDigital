import type { SeguimientoCredihoy, SolicitudCredihoy } from "../types";
import { moneda } from "../components/FormControls";
import { PRESENTACION_ESTADOS } from "../solicitudStates";
export { PRESENTACION_ESTADOS } from "../solicitudStates";

export function CredihoyEstadoPage({ seguimiento, solicitud, onReset, onUpdate, onResume, local = false, busy = false }: {
  seguimiento: SeguimientoCredihoy; solicitud: SolicitudCredihoy; onReset: () => void;
  onUpdate?: () => void; onResume?: () => void; local?: boolean; busy?: boolean;
}) {
  const vista = PRESENTACION_ESTADOS[seguimiento.estado];
  const desarrollo = import.meta.env.DEV && local && seguimiento.sintetico;
  const accion = vista.accion === "NUEVA" ? onReset : vista.accion === "RETOMAR" ? onResume : seguimiento.referencia ? onUpdate : undefined;
  return <section className={`ch-status ch-status--${vista.tono}`}>
    <div className="ch-status-mark" aria-hidden="true">{vista.icono}</div><span className="ch-eyebrow">CrediHoy</span>
    <h2>{vista.titulo}</h2><p className="ch-intro">{vista.texto}</p>
    {!seguimiento.sintetico && seguimiento.mensaje && <p className="ch-status-message">{seguimiento.mensaje}</p>}
    <dl className="ch-status-summary"><div><dt>Monto solicitado</dt><dd>{moneda(solicitud.monto)}</dd></div>
      <div><dt>Plazo</dt><dd>{solicitud.plazoMeses} meses</dd></div></dl>
    {seguimiento.referencia && (!seguimiento.sintetico || desarrollo) && <p className="ch-help">Referencia {seguimiento.referencia}</p>}
    {desarrollo && <div className="ch-note ch-note--neutral">Registro sintético local. Sin radicación ni decisión crediticia.</div>}
    <div className="ch-status-actions">
      <button className="btn btn-brand" type="button" disabled={busy || !accion} onClick={accion}>{busy ? "Consultando…" : vista.cta}</button>
      {vista.accion !== "NUEVA" && <button className="btn btn-outline-secondary" type="button" disabled={busy} onClick={onReset}>Nueva solicitud</button>}
    </div>
  </section>;
}
