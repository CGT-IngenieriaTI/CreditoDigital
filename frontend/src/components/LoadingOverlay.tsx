import icono from "../assets/IconoHD.png";

interface LoadingOverlayProps {
  visible: boolean;
  message: string;
  variant?: "default" | "centrales" | "asociado" | "garantia" | "tasa" | "estamentos";
}

function LoaderOrbit() {
  return (
    <div className="processing-orbit" aria-hidden="true">
      <div className="processing-orbit__ring processing-orbit__ring--outer" />
      <div className="processing-orbit__ring processing-orbit__ring--inner" />
      <div className="processing-orbit__core">
        <img src={icono} alt="" className="processing-orbit__core-logo" />
      </div>
    </div>
  );
}

export function LoadingOverlay({ visible, message, variant = "default" }: LoadingOverlayProps) {
  if (!visible) {
    return null;
  }

  const title =
    variant === "centrales"
      ? "Consultando centrales..."
      : variant === "asociado"
        ? "Consultando calidad de asociado"
        : variant === "garantia"
          ? "Calculando cobro de comisi\u00f3n"
          : variant === "tasa"
            ? "Calculando tasa"
            : variant === "estamentos"
              ? "Consultando informaci\u00f3n familiar"
              : "Procesando informaci\u00f3n";

  const steps =
    variant === "centrales"
      ? [
          "Validando preselecci\u00f3n con el proveedor autorizado",
          "Consultando historial de pago interno del asociado",
          "Consolidando informaci\u00f3n financiera para continuar el flujo",
        ]
      : variant === "asociado"
        ? [
            "Validando pol\u00edticas iniciales del solicitante",
            "Consultando informaci\u00f3n base del asociado en LINIX",
            "Preparando el paso de autorizaci\u00f3n de centrales",
          ]
        : variant === "garantia"
          ? [
              "Validando el fondo o garant\u00eda seleccionada",
              "Calculando la comisi\u00f3n aplicable seg\u00fan perfil y plazo",
              "Confirmando si la solicitud puede continuar a decisi\u00f3n final",
            ]
          : variant === "tasa"
            ? [
                "Leyendo la configuraci\u00f3n vigente de tasas de inter\u00e9s",
                "Identificando l\u00ednea, forma de pago y categor\u00eda de riesgo",
                "Preparando la tasa efectiva para la evaluaci\u00f3n final",
              ]
            : variant === "estamentos"
              ? [
                  "Consultando informaci\u00f3n familiar relacionada con el asociado",
                  "Validando reglas internas de estamento y aprobaci\u00f3n",
                  "Preparando la decisi\u00f3n final del flujo",
                ]
              : [
                  "Consultando sistemas externos autorizados",
                  "Consolidando informaci\u00f3n del asociado",
                  "Preparando el siguiente paso del flujo",
                ];

  return (
    <div className="processing-overlay">
      <div className={`processing-card processing-card--${variant}`}>
        <LoaderOrbit />
        <span className="processing-card__label">Flujo en curso</span>
        <h3 className="h3 mb-3 text-primary">{title}</h3>
        <p className="text-secondary mb-4">{message}</p>
        <div className="processing-list">
          {steps.map((step) => (
            <span key={step}>{step}</span>
          ))}
        </div>
      </div>
    </div>
  );
}
