import { ReactNode, createContext, startTransition, useContext, useState } from "react";

import {
  AuthUser,
  ConsumoProcessResponse,
  ConsumoSolicitudStatus,
  DecisionFinal,
  StepKey,
} from "../types/credit";

interface CreditFlowContextValue {
  currentStep: StepKey;
  solicitud: ConsumoSolicitudStatus | null;
  decision: ConsumoProcessResponse | DecisionFinal | null;
  user: AuthUser | null;
  busy: boolean;
  busyMessage: string;
  error: string;
  setCurrentStep: (step: StepKey) => void;
  setSolicitud: (solicitud: ConsumoSolicitudStatus | null) => void;
  setDecision: (decision: ConsumoProcessResponse | DecisionFinal | null) => void;
  setUser: (user: AuthUser | null) => void;
  setBusy: (busy: boolean, message?: string) => void;
  setError: (error: string) => void;
  resetFlow: () => void;
}

const CreditFlowContext = createContext<CreditFlowContextValue | undefined>(undefined);

export function CreditFlowProvider({ children }: { children: ReactNode }) {
  const [currentStep, setCurrentStepState] = useState<StepKey>("formulario");
  const [solicitud, setSolicitud] = useState<ConsumoSolicitudStatus | null>(null);
  const [decision, setDecision] = useState<ConsumoProcessResponse | DecisionFinal | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [busy, setBusyState] = useState(false);
  const [busyMessage, setBusyMessage] = useState("Procesando solicitud...");
  const [error, setError] = useState("");

  const setCurrentStep = (step: StepKey) => {
    startTransition(() => setCurrentStepState(step));
  };

  const setBusy = (nextBusy: boolean, message = "Procesando solicitud...") => {
    setBusyState(nextBusy);
    setBusyMessage(message);
  };

  const resetFlow = () => {
    setCurrentStepState("formulario");
    setSolicitud(null);
    setDecision(null);
    setBusyState(false);
    setBusyMessage("Procesando solicitud...");
    setError("");
  };

  return (
    <CreditFlowContext.Provider
      value={{
        currentStep,
        solicitud,
        decision,
        user,
        busy,
        busyMessage,
        error,
        setCurrentStep,
        setSolicitud,
        setDecision,
        setUser,
        setBusy,
        setError,
        resetFlow,
      }}
    >
      {children}
    </CreditFlowContext.Provider>
  );
}

export function useCreditFlow() {
  const context = useContext(CreditFlowContext);
  if (!context) {
    throw new Error("useCreditFlow must be used within CreditFlowProvider");
  }
  return context;
}
