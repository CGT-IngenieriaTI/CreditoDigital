import { downloadConsumoDecisionPdf } from "../api/creditApi";
import { ConsumoProcessResponse, ConsumoSolicitudStatus, DecisionFinal } from "../types/credit";

function currency(value?: string | number | null) {
  if (value === null || value === undefined || value === "") return "No aplica";
  return new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

function metricValue(value?: string | number | null) {
  if (value === null || value === undefined || value === "") {
    return "No aplica";
  }
  return String(value);
}

export function DecisionPage({
  solicitud,
  decision,
  pdfUrl,
  onReset,
}: {
  solicitud: ConsumoSolicitudStatus;
  decision: ConsumoProcessResponse | DecisionFinal;
  pdfUrl: string;
  onReset: () => void;
}) {
  const finalDecision = String(
    "decision" in decision
      ? decision.decision?.mensaje || decision.decision_final || ""
      : decision.mensaje || decision.resultado
  );
  const normalizedDecision = finalDecision.toLowerCase();
  const resultTone = normalizedDecision.includes("aprobado")
    ? "success"
    : normalizedDecision.includes("gris")
      ? "warning"
      : "danger";
  const isPreselectaDecision = !("puntaje_xcore" in decision);
  const resultados = "resultados" in decision ? decision.resultados || {} : {};
  const hasCommission = Boolean(resultados?.comision_tipo);

  const openPdf = () => {
    window.open(pdfUrl, "_blank", "noopener,noreferrer");
  };

  const downloadPdf = async () => {
    await downloadConsumoDecisionPdf(
      solicitud.solicitud.id,
      `${solicitud.solicitud.numero_solicitud}_consumo.pdf`
    );
  };

  if (isPreselectaDecision) {
    return (
      <div className="row justify-content-center">
        <div className="col-12">
          <div className="content-card content-card--result decision-board">
            <span className={`decision-board__badge decision-board__badge--${resultTone}`}>
              {finalDecision || "Resultado disponible"}
            </span>
            <h2 className="decision-board__title mt-3 mb-4">Resultado de la solicitud</h2>
            <section className="decision-board__hero">
              <div className="decision-board__hero-main">
                <span className="decision-board__label">Decisión final</span>
                <strong>{metricValue((decision as DecisionFinal).resultado)}</strong>
                <p>{metricValue(finalDecision)}</p>
              </div>
            </section>
            <div className="decision-board__actions">
              <button type="button" className="btn btn-brand btn-sm" onClick={onReset}>
                Nueva solicitud
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const consumoDecision = decision as ConsumoProcessResponse;
  const montoMaximoVisible = String(consumoDecision.decision_final || "").toLowerCase().includes("negado")
    ? 0
    : consumoDecision.monto_max_posible;
  const chips = [
    consumoDecision.perfil_credito,
    consumoDecision.capacidad_pago_final,
    consumoDecision.perfil_riesgo,
  ].filter(Boolean);

  return (
    <div className="row justify-content-center">
      <div className="col-12">
        <div className="content-card content-card--result decision-board">
          <span className={`decision-board__badge decision-board__badge--${resultTone}`}>
            {finalDecision || "Resultado disponible"}
          </span>
          <h2 className="decision-board__title mt-3 mb-4">
            Resultado <span>XCORE</span> Consumo
          </h2>

          <section className="decision-board__hero">
            <div className="decision-board__hero-main">
              <span className="decision-board__label">Decisión final</span>
              <strong>{metricValue(consumoDecision.decision_final)}</strong>
              <div className="decision-board__chips">
                {chips.map((chip) => (
                  <span key={chip} className={`decision-board__chip decision-board__chip--${resultTone}`}>
                    {metricValue(chip)}
                  </span>
                ))}
              </div>
            </div>
            <div className="decision-board__hero-side">
              <span className="decision-board__label">Puntaje XCORE</span>
              <strong>{metricValue(consumoDecision.puntaje_xcore)}</strong>
              <p>{metricValue(consumoDecision.estamento)}</p>
            </div>
          </section>

          {consumoDecision.tiene_novedad ? (
            <section className="decision-board__notice">
              <span className="decision-board__notice-icon">!</span>
              <div>
                <strong>Novedad:</strong> {metricValue(consumoDecision.novedad_descripcion)}
              </div>
            </section>
          ) : null}

          <section className="decision-board__section">
            <div className="decision-board__section-header">Condiciones del crédito</div>
            <div className="decision-board__metrics">
              <article className="decision-board__metric decision-board__metric--emphasis">
                <span>Monto máximo</span>
                <strong>{currency(montoMaximoVisible)}</strong>
              </article>
              <article className="decision-board__metric decision-board__metric--emphasis">
                <span>Valor cuota</span>
                <strong>{currency(consumoDecision.valor_cuota)}</strong>
              </article>
              <article className="decision-board__metric decision-board__metric--emphasis">
                <span>Capacidad de pago</span>
                <strong>{metricValue(consumoDecision.capacidad_pago_final)}</strong>
              </article>
            </div>
          </section>

          <section className="decision-board__section">
            <div className="decision-board__section-header">Garantía y costos</div>
            <div className="decision-board__metrics decision-board__metrics--secondary">
              <article className="decision-board__metric">
                <span>Garantía / fondo</span>
                <strong>{metricValue((resultados.comision_tipo as string | number | null | undefined) || "No aplica")}</strong>
              </article>
              <article className="decision-board__metric">
                <span>Tasa base comisión</span>
                <strong>
                  {hasCommission
                    ? `${metricValue(resultados.comision_tasa_base as string | number | null | undefined)} %`
                    : "No aplica"}
                </strong>
              </article>
              <article className="decision-board__metric">
                <span>Comisión total</span>
                <strong>{hasCommission ? currency(Number(resultados.comision_total_valor || 0)) : "No aplica"}</strong>
              </article>
            </div>
          </section>

          <div className="decision-board__actions">
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={openPdf}>
              Ver PDF final
            </button>
            <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => void downloadPdf()}>
              Descargar PDF final
            </button>
            <button type="button" className="btn btn-brand btn-sm" onClick={onReset}>
              Nueva solicitud
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
