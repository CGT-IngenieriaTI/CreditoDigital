import type { IntegracionCredihoy } from "../types";

export function IntegrationState({ integracion, onRetry }: { integracion: IntegracionCredihoy; onRetry?: () => void }) {
  switch (integracion.estado) {
    case "idle": return null;
    case "success": return <span className="visually-hidden" role="status">Información disponible.</span>;
    case "loading": return <div className="ch-note ch-note--neutral" role="status"><span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />Consultando información…</div>;
    case "empty": return <div className="ch-note ch-note--neutral" role="status">No hay información adicional disponible para este paso.</div>;
    default: return <div className="ch-note ch-integration-error" role="alert" id="ch-operation-feedback" tabIndex={-1}>
      <strong>{integracion.estado === "validation_error" ? "Revisa los datos indicados." : "No pudimos completar la consulta."}</strong>
      <p>{integracion.mensaje ?? (integracion.estado === "technical_error" ? "No se pudo confirmar el resultado. Tu información permanece en este recorrido; cualquier reintento conserva la misma referencia." : "Tu información permanece en este recorrido.")}</p>
      {integracion.estado === "retryable_error" && onRetry && <button type="button" className="btn btn-outline-secondary" onClick={onRetry}>Reintentar</button>}
    </div>;
  }
}
