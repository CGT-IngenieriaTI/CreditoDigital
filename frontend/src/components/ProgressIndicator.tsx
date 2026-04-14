import { StepKey } from "../types/credit";

const steps = [
  { key: "formulario", label: "Datos básicos" },
  { key: "consentimiento", label: "Términos y docs" },
  { key: "analisis", label: "Formulario solicitud" },
  { key: "resultado", label: "Resultado" },
] as const;

const stepPositionByState: Record<StepKey, number> = {
  formulario: 0,
  consentimiento: 1,
  otp: 1,
  analisis: 2,
  resultado: 3,
};

export function ProgressIndicator({ currentStep }: { currentStep: StepKey }) {
  const currentIndex = stepPositionByState[currentStep];

  return (
    <div className="progress-stepper" aria-label="Flujo de crédito digital">
      {steps.map((step, index) => {
        const completed = index < currentIndex;
        const active = index === currentIndex;

        return (
          <div
            key={step.key}
            className={`progress-stepper__item ${completed ? "is-completed" : ""} ${active ? "is-active" : ""}`}
          >
            <div className="progress-stepper__circle" aria-hidden="true">
              {completed ? (
                <svg viewBox="0 0 20 20" className="progress-stepper__check">
                  <path d="M4.5 10.5 8.2 14 15.5 6.8" />
                </svg>
              ) : (
                <span>{index + 1}</span>
              )}
            </div>
            <div className="progress-stepper__label">{step.label}</div>
          </div>
        );
      })}
    </div>
  );
}
