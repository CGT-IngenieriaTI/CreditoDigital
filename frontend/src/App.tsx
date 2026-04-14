import { useCallback, useEffect, useRef, useState } from "react";

import {
  consultConsumoCore,
  createConsumoSolicitud,
  downloadConsumoDecisionPdf,
  fetchConsumoDecision,
  fetchConsumoSolicitudStatus,
  fetchCsrfToken,
  fetchMe,
  getConsumoDecisionPdfUrl,
  listConsumoSolicitudes,
  login,
  logout,
  processConsumoSolicitud,
  previewConsumoOrchestration,
  saveConsumoForm,
  sendOtp,
  submitConsent,
  verifyOtp,
} from "./api/creditApi";
import { AppShell } from "./components/AppShell";
import { LoadingOverlay } from "./components/LoadingOverlay";
import { StatusModal } from "./components/StatusModal";
import { useCreditFlow } from "./context/CreditFlowContext";
import { ApplicationFormPage } from "./pages/ApplicationFormPage";
import { AdvisorHistoryPage } from "./pages/AdvisorHistoryPage";
import { ConsentPage } from "./pages/ConsentPage";
import { ConsumoAnalysisPage } from "./pages/ConsumoAnalysisPage";
import { DecisionPage } from "./pages/DecisionPage";
import { LoginPage } from "./pages/LoginPage";
import {
  AcceptedDocumentPayload,
  ConsumoProcessConflict,
  ConsumoSolicitudResumen,
  ConsumoSolicitudStatus,
  DecisionFinal,
  FormularioBasico,
  StepKey,
} from "./types/credit";

const STORAGE_KEY = "congente_consumo_solicitud_id";
type BusyVariant = "default" | "centrales" | "asociado" | "garantia" | "tasa" | "estamentos";
const SUCCESS_MODAL_MS = 2700;
const LOADING_STAGE_MS = 1700;
const SESSION_TIMEOUT_MS = 30 * 60 * 1000;
const SESSION_WARNING_MS = 2 * 60 * 1000;
const pause = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

function App() {
  const {
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
  } = useCreditFlow();

  const [bootstrapping, setBootstrapping] = useState(true);
  const [loggingIn, setLoggingIn] = useState(false);
  const [submittingForm, setSubmittingForm] = useState(false);
  const [sendingOtp, setSendingOtp] = useState(false);
  const [verifyingOtp, setVerifyingOtp] = useState(false);
  const [submittingConsent, setSubmittingConsent] = useState(false);
  const [loadingCore, setLoadingCore] = useState(false);
  const [savingForm, setSavingForm] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [hc2Conflict, setHc2Conflict] = useState<ConsumoProcessConflict | null>(null);
  const [busyVariant, setBusyVariant] = useState<BusyVariant>("default");
  const [statusModal, setStatusModal] = useState<
    | {
        tone: "success" | "info" | "warning";
        label?: string;
        title: string;
        message: string;
        details?: string[];
      }
    | null
  >(null);
  const [sessionWarningVisible, setSessionWarningVisible] = useState(false);
  const [historyView, setHistoryView] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyItems, setHistoryItems] = useState<ConsumoSolicitudResumen[]>([]);
  const [sessionWarningSeconds, setSessionWarningSeconds] = useState(
    Math.ceil(SESSION_WARNING_MS / 1000)
  );
  const warningTimerRef = useRef<number | null>(null);
  const logoutTimerRef = useRef<number | null>(null);
  const countdownTimerRef = useRef<number | null>(null);

  const syncSolicitud = (payload: ConsumoSolicitudStatus) => {
    setSolicitud(payload);
    setCurrentStep(payload.wizard_step);
    sessionStorage.setItem(STORAGE_KEY, payload.solicitud.id);
  };

  const clearStoredFlow = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
    setStatusModal(null);
    resetFlow();
  }, [resetFlow]);

  const clearSessionTimers = useCallback(() => {
    if (warningTimerRef.current) {
      window.clearTimeout(warningTimerRef.current);
      warningTimerRef.current = null;
    }
    if (logoutTimerRef.current) {
      window.clearTimeout(logoutTimerRef.current);
      logoutTimerRef.current = null;
    }
    if (countdownTimerRef.current) {
      window.clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const items = await listConsumoSolicitudes();
      setHistoryItems(items);
    } catch (apiError) {
      setHistoryError(apiError instanceof Error ? apiError.message : "No fue posible cargar el historial.");
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const showStatusModalFor = async (
    modal: {
      tone: "success" | "info" | "warning";
      label?: string;
      title: string;
      message: string;
      details?: string[];
    },
    duration = SUCCESS_MODAL_MS
  ) => {
    setStatusModal(modal);
    await pause(duration);
    setStatusModal(null);
  };

  const runBusyStages = async <T,>(
    stages: Array<{ variant: BusyVariant; message: string }>,
    task: Promise<T>
  ): Promise<T> => {
    let settled = false;
    let result: T | undefined;
    let failure: unknown;

    const tracked = task
      .then((value) => {
        settled = true;
        result = value;
      })
      .catch((stageError) => {
        settled = true;
        failure = stageError;
      });

    for (const stage of stages) {
      setBusyVariant(stage.variant);
      setBusy(true, stage.message);
      await pause(LOADING_STAGE_MS);
      if (settled) {
        break;
      }
    }

    await tracked;
    setBusy(false);
    setBusyVariant("default");

    if (failure) {
      throw failure;
    }
    return result as T;
  };

  const handleLogout = useCallback(async () => {
    setError("");
    try {
      await logout();
    } finally {
      clearSessionTimers();
      setSessionWarningVisible(false);
      setHistoryView(false);
      setHistoryItems([]);
      setUser(null);
      clearStoredFlow();
    }
  }, [clearSessionTimers, clearStoredFlow, setError, setUser]);

  const resetSessionWatch = useCallback(() => {
    if (!user) {
      return;
    }
    clearSessionTimers();
    setSessionWarningVisible(false);
    setSessionWarningSeconds(Math.ceil(SESSION_WARNING_MS / 1000));

    warningTimerRef.current = window.setTimeout(() => {
      setSessionWarningVisible(true);
      setSessionWarningSeconds(Math.ceil(SESSION_WARNING_MS / 1000));

      countdownTimerRef.current = window.setInterval(() => {
        setSessionWarningSeconds((current) => (current > 1 ? current - 1 : 1));
      }, 1000);

      logoutTimerRef.current = window.setTimeout(() => {
        setSessionWarningVisible(false);
        void handleLogout();
      }, SESSION_WARNING_MS);
    }, SESSION_TIMEOUT_MS - SESSION_WARNING_MS);
  }, [clearSessionTimers, handleLogout, user]);

  useEffect(() => {
    const bootstrap = async () => {
      try {
        await fetchCsrfToken();
        const me = await fetchMe().catch(() => null);
        if (!me) {
          setUser(null);
          clearStoredFlow();
          setBootstrapping(false);
          return;
        }
        setUser(me.user);

        const solicitudId = sessionStorage.getItem(STORAGE_KEY);
        if (!solicitudId) {
          setBootstrapping(false);
          return;
        }

        const status = await fetchConsumoSolicitudStatus(solicitudId).catch(() => null);
        if (!status) {
          clearStoredFlow();
          setBootstrapping(false);
          return;
        }
        syncSolicitud(status);
        if (status.wizard_step === "resultado") {
          const finalDecision = await fetchConsumoDecision(solicitudId).catch(() => null);
          if (finalDecision) {
            setDecision(finalDecision);
          }
        }
      } catch (apiError) {
        setError(apiError instanceof Error ? apiError.message : "No fue posible iniciar la aplicación.");
      } finally {
        setBootstrapping(false);
      }
    };

    void bootstrap();
  }, []);

  useEffect(() => {
    if (!user) {
      clearSessionTimers();
      setSessionWarningVisible(false);
      return;
    }

    const markActivity = () => {
      resetSessionWatch();
    };

    const events: Array<keyof WindowEventMap> = [
      "mousemove",
      "mousedown",
      "keydown",
      "scroll",
      "touchstart",
      "click",
    ];

    resetSessionWatch();
    events.forEach((eventName) => window.addEventListener(eventName, markActivity, { passive: true }));

    return () => {
      events.forEach((eventName) => window.removeEventListener(eventName, markActivity));
      clearSessionTimers();
    };
  }, [clearSessionTimers, resetSessionWatch, user]);

  const runCoreConsultation = async (
    baseSolicitud: ConsumoSolicitudStatus,
    message = "Consultando centrales autorizadas y consolidando la información financiera de la solicitud."
  ) => {
    setLoadingCore(true);
    setBusyVariant("centrales");
    setBusy(true, message);
    try {
      const response = await consultConsumoCore(
        baseSolicitud.solicitud.id,
        baseSolicitud.solicitud.solicitante.numero_identificacion
      );
      syncSolicitud(response);
      return response;
    } finally {
      setBusy(false);
      setBusyVariant("default");
      setLoadingCore(false);
    }
  };

  const handleLogin = async (payload: { username: string; password: string }) => {
    setLoggingIn(true);
    setError("");
    try {
      const response = await login(payload.username, payload.password);
      setUser(response.user);
    } catch (apiError) {
      setError(apiError instanceof Error ? apiError.message : "No fue posible iniciar sesión.");
    } finally {
      setLoggingIn(false);
    }
  };

  const handlePreviewLookup = async (payload: FormularioBasico) => {
    setBusyVariant("asociado");
    setBusy(true, "Validando calidad de asociado y consultando información base en LINIX.");
    try {
      return await previewConsumoOrchestration(payload);
    } finally {
      setBusy(false);
      setBusyVariant("default");
    }
  };

  const handleFormSubmit = async (payload: FormularioBasico) => {
    setSubmittingForm(true);
    setError("");
    try {
      const response = await createConsumoSolicitud(payload);
      syncSolicitud(response);
    } catch (apiError) {
      setError(apiError instanceof Error ? apiError.message : "No fue posible crear la solicitud.");
    } finally {
      setSubmittingForm(false);
    }
  };

  const handleSendOtp = async (channel: "SMS" | "EMAIL") => {
    if (!solicitud) return;
    setSendingOtp(true);
    setError("");
    try {
      const response = await sendOtp(solicitud.solicitud.id, channel);
      syncSolicitud(response);
    } catch (apiError) {
      setError(apiError instanceof Error ? apiError.message : "No fue posible enviar la OTP.");
    } finally {
      setSendingOtp(false);
    }
  };

  const handleVerifyOtp = async (code: string) => {
    if (!solicitud) return;
    setVerifyingOtp(true);
    setError("");
    try {
      const response = await verifyOtp(solicitud.solicitud.id, code);
      syncSolicitud(response);

      await showStatusModalFor({
        tone: "success",
        label: "Verificación completada",
        title: "OTP validado exitosamente",
        message:
          "La identidad fue confirmada. A continuación consultaremos centrales y el historial interno para preparar el formulario de solicitud.",
        details: [response.solicitud.numero_solicitud],
      });
      const consulted = await runCoreConsultation(
        response,
        "Consultando centrales autorizadas y el historial de pago interno para consolidar la información financiera."
      );
      const estadoNegocio = String(consulted.orchestration?.datos_preselecta?.estado_negocio || "");
      if (consulted.wizard_step === "analisis" && ["APROBADO", "ZONA_GRIS"].includes(estadoNegocio)) {
        await showStatusModalFor({
          tone: "success",
          label: "Filtro superado",
          title: "Centrales consultadas correctamente",
          message:
            estadoNegocio === "ZONA_GRIS"
              ? "El filtro preliminar fue superado y la solicitud pasará al formulario con la información consolidada."
              : "El filtro aprobado permite continuar con el formulario de solicitud y la información consolidada.",
        });
      }
    } catch (apiError) {
      setError(apiError instanceof Error ? apiError.message : "No fue posible validar la OTP.");
    } finally {
      setStatusModal(null);
      setVerifyingOtp(false);
    }
  };

  const handleConsent = async (payload: {
    accepted: boolean;
    version: string;
    canal: "SMS" | "EMAIL";
    text_snapshot: string;
    accepted_documents: AcceptedDocumentPayload[];
  }) => {
    if (!solicitud) return;
    setSubmittingConsent(true);
    setError("");
    try {
      const response = await submitConsent(solicitud.solicitud.id, payload);
      syncSolicitud(response);
    } catch (apiError) {
      setError(apiError instanceof Error ? apiError.message : "No fue posible registrar el consentimiento.");
    } finally {
      setSubmittingConsent(false);
    }
  };

  const handleConsultCore = async () => {
    if (!solicitud) return;
    setError("");
    try {
      await runCoreConsultation(
        solicitud,
        "Reintentando la consulta de centrales autorizadas y consolidando la información disponible."
      );
    } catch (apiError) {
      setError(apiError instanceof Error ? apiError.message : "No fue posible actualizar las consultas.");
    }
  };

  const handleSaveForm = async (formData: Record<string, unknown>) => {
    if (!solicitud) return;
    setSavingForm(true);
    setError("");
    try {
      const response = await saveConsumoForm(solicitud.solicitud.id, formData);
      syncSolicitud(response);
    } catch (apiError) {
      setError(apiError instanceof Error ? apiError.message : "No fue posible guardar el formulario.");
    } finally {
      setSavingForm(false);
    }
  };

  const handleProcess = async (formData: Record<string, unknown>, selectedKeys?: string[]) => {
    if (!solicitud) return;
    setProcessing(true);
    setError("");
    try {
      const saved = await saveConsumoForm(solicitud.solicitud.id, formData);
      syncSolicitud(saved);
      const garantiaSeleccionada = String(formData.tipo_garantia || "").trim().toLowerCase();
      const tieneGarantia = Boolean(garantiaSeleccionada) && garantiaSeleccionada !== "sin garantia";
      const stages = tieneGarantia
        ? [
            {
              variant: "garantia" as const,
              message: "Calculando cobro de comisión y validando la garantía seleccionada.",
            },
            {
              variant: "tasa" as const,
              message: "Calculando capacidad de pago y la tasa vigente para la solicitud.",
            },
            {
              variant: "estamentos" as const,
              message: "Generando el resultado final y validando el estamento aplicable.",
            },
          ]
        : [
            {
              variant: "tasa" as const,
              message: "Calculando capacidad de pago y la tasa vigente para la solicitud.",
            },
            {
              variant: "estamentos" as const,
              message: "Generando el resultado final y validando el estamento aplicable.",
            },
          ];
      const response = await runBusyStages(
        stages,
        processConsumoSolicitud(solicitud.solicitud.id, selectedKeys)
      );
      if ("requires_hc2_selection" in response) {
        setHc2Conflict(response);
        return;
      }
      setHc2Conflict(null);
      await showStatusModalFor({
        tone: "success",
        label: "Cálculos completados",
        title: "Tasa y estamentos calculados correctamente",
        message:
          "La solicitud fue evaluada con la información financiera consolidada, la tasa vigente y la validación de estamentos.",
      });
      setDecision(response);
      setCurrentStep("resultado");
    } catch (apiError) {
      const message = apiError instanceof Error ? apiError.message : "No fue posible procesar la solicitud.";
      setBusy(false);
      setBusyVariant("default");
      await showStatusModalFor({
        tone: "warning",
        label: "Validación de la solicitud",
        title: "No fue posible continuar con el proceso",
        message,
      });
      setError(message);
    } finally {
      setProcessing(false);
    }
  };

  const handleReset = () => {
    clearStoredFlow();
    setDecision(null);
  };

  const handleGoHome = () => {
    setHistoryView(false);
    clearStoredFlow();
    setDecision(null);
    setError("");
  };

  const handleToggleHistory = async () => {
    const next = !historyView;
    setHistoryView(next);
    if (next) {
      await loadHistory();
    }
  };

  const handleOpenHistoryPdf = (solicitudId: string) => {
    window.open(getConsumoDecisionPdfUrl(solicitudId), "_blank", "noopener,noreferrer");
  };

  const handleDownloadHistoryPdf = async (solicitudId: string, numeroSolicitud: string) => {
    await downloadConsumoDecisionPdf(solicitudId, `${numeroSolicitud}_consumo.pdf`);
  };

  const handleOpenSolicitudFromHistory = async (solicitudId: string) => {
    setHistoryError("");
    try {
      const status = await fetchConsumoSolicitudStatus(solicitudId);
      syncSolicitud(status);
      if (status.wizard_step === "resultado") {
        const finalDecision = await fetchConsumoDecision(solicitudId).catch(() => null);
        setDecision(finalDecision);
      } else {
        setDecision(null);
      }
      setHistoryView(false);
    } catch (apiError) {
      setHistoryError(apiError instanceof Error ? apiError.message : "No fue posible abrir la solicitud.");
    }
  };


  const consentFlowStep: StepKey =
    currentStep === "otp" || currentStep === "consentimiento" ? currentStep : "consentimiento";

  const fallbackDecision: DecisionFinal | null =
    currentStep === "resultado" && solicitud && !decision && solicitud.orchestration?.datos_preselecta?.puede_continuar === false
      ? {
          numero_solicitud: solicitud.solicitud.numero_solicitud,
          resultado: "RECHAZADO",
          mensaje:
            String(solicitud.orchestration?.datos_preselecta?.mensaje_usuario || "") ||
            "Por ahora no es posible continuar con la solicitud de crédito.",
          observaciones: String(solicitud.orchestration?.datos_preselecta?.estado_negocio || "PRESELECTA"),
          created_at: solicitud.updated_at,
          solicitante: solicitud.solicitud.solicitante,
        }
      : null;

  if (bootstrapping) {
    return <LoadingOverlay visible={true} message="Preparando sesión operativa..." />;
  }

  if (!user) {
    return <LoginPage loading={loggingIn} error={error} onSubmit={handleLogin} />;
  }

  const sessionWarningMinutes = Math.max(1, Math.ceil(sessionWarningSeconds / 60));

  return (
    <AppShell
      currentStep={currentStep}
      user={user}
      onLogout={handleLogout}
      onToggleHistory={() => void handleToggleHistory()}
      onGoHome={handleGoHome}
      historyActive={historyView}
    >
      {historyView ? (
        <AdvisorHistoryPage
          items={historyItems}
          loading={historyLoading}
          error={historyError}
          onRefresh={loadHistory}
          onOpenSolicitud={handleOpenSolicitudFromHistory}
          onOpenPdf={handleOpenHistoryPdf}
          onDownloadPdf={handleDownloadHistoryPdf}
        />
      ) : currentStep === "consentimiento" || currentStep === "otp" ? (
        solicitud ? (
          <ConsentPage
            solicitud={solicitud}
            currentStep={consentFlowStep}
            loading={submittingConsent}
            loadingSend={sendingOtp}
            loadingVerify={verifyingOtp}
            error={error}
            onSubmit={handleConsent}
            onSend={handleSendOtp}
            onVerify={handleVerifyOtp}
          />
        ) : null
      ) : currentStep === "analisis" && solicitud ? (
        <ConsumoAnalysisPage
          solicitud={solicitud}
          loadingCore={loadingCore}
          saving={savingForm}
          processing={processing}
          error={error}
          hc2Conflict={hc2Conflict}
          maskSensitiveData={busy || Boolean(statusModal) || loadingCore || processing}
          onConsultCore={handleConsultCore}
          onSave={handleSaveForm}
          onProcess={handleProcess}
        />
      ) : currentStep === "resultado" && solicitud && (decision || fallbackDecision) ? (
        <DecisionPage
          solicitud={solicitud}
          decision={decision || fallbackDecision!}
          pdfUrl={getConsumoDecisionPdfUrl(solicitud.solicitud.id)}
          onReset={handleReset}
        />
      ) : (
        <ApplicationFormPage
          loading={submittingForm}
          error={error}
          onSubmit={handleFormSubmit}
          onPreview={handlePreviewLookup}
        />
      )}
      <LoadingOverlay visible={busy} message={busyMessage} variant={busyVariant} />
      <StatusModal
        visible={Boolean(statusModal)}
        tone={statusModal?.tone ?? "success"}
        label={statusModal?.label}
        title={statusModal?.title ?? ""}
        message={statusModal?.message ?? ""}
        details={statusModal?.details}
      />
      {sessionWarningVisible ? (
        <div className="status-modal" role="dialog" aria-modal="true" aria-label="Sesión inactiva">
          <div className="status-modal__card session-warning-card">
            <span className="status-modal__label">Sesión inactiva</span>
            <h3>Tu sesión está inactiva</h3>
            <p>
              Tu sesión se cerrará en {sessionWarningMinutes} minuto{sessionWarningMinutes === 1 ? "" : "s"} por
              inactividad.
            </p>
            <div className="session-warning-card__actions">
              <button type="button" className="btn btn-brand btn-sm" onClick={resetSessionWatch}>
                Continuar sesión
              </button>
              <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => void handleLogout()}>
                Cerrar ahora
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}

export default App;




