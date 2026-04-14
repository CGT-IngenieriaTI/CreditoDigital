const labels: Record<string, string> = {
  INICIADA: "Solicitud iniciada",
  AUTORIZADA: "Autorizaciones completadas",
  PRESELECTA_OK: "Preselección exitosa",
  HISTORIAL_OK: "Historial de pago consultado",
  ENVIADA_XCORE: "Evaluación principal en XCORE",
  FINALIZADA: "Resultado disponible",
  ERROR: "Error en el pipeline"
};

const orderedStates = [
  "INICIADA",
  "AUTORIZADA",
  "PRESELECTA_OK",
  "HISTORIAL_OK",
  "ENVIADA_XCORE",
  "FINALIZADA"
];

export function StatusTimeline({ currentState }: { currentState: string }) {
  const currentIndex = orderedStates.indexOf(currentState);

  return (
    <div className="timeline-panel">
      {orderedStates.map((state, index) => {
        const completed = currentIndex >= index || currentState === "FINALIZADA";
        const active = state === currentState;

        return (
          <div key={state} className={`timeline-item ${completed ? "is-completed" : ""} ${active ? "is-active" : ""}`}>
            <div className="timeline-item__dot" />
            <div>
              <div className="timeline-item__label">{labels[state]}</div>
              <div className="timeline-item__code">{state}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
