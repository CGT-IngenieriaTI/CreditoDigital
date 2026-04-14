import { ClipboardEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import { fetchLegalDocuments } from "../api/creditApi";
import { PdfScrollViewer } from "../components/PdfScrollViewer";
import {
  AcceptedDocumentPayload,
  ConsumoSolicitudStatus,
  LegalDocument,
  OtpChannel,
  StepKey,
} from "../types/credit";

interface ConsentPageProps {
  solicitud: ConsumoSolicitudStatus;
  currentStep: StepKey;
  loading: boolean;
  loadingSend: boolean;
  loadingVerify: boolean;
  error: string;
  onSubmit: (payload: {
    accepted: boolean;
    version: string;
    canal: OtpChannel;
    text_snapshot: string;
    accepted_documents: AcceptedDocumentPayload[];
  }) => Promise<void>;
  onSend: (channel: OtpChannel) => Promise<void>;
  onVerify: (code: string) => Promise<void>;
}

interface DocumentUiState {
  opened: boolean;
  loaded: boolean;
  accepted: boolean;
  reachedEnd: boolean;
  viewedSeconds: number;
  openedAt: number | null;
  openedExternally: boolean;
}

const defaultDocumentState: DocumentUiState = {
  opened: false,
  loaded: false,
  accepted: false,
  reachedEnd: false,
  viewedSeconds: 0,
  openedAt: null,
  openedExternally: false,
};

const OTP_LENGTH = 6;

function isMobileViewport() {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 767.98px)").matches;
}

function maskPhone(value: string) {
  if (value.length < 4) return value;
  return `${value.slice(0, 3)}*****${value.slice(-3)}`;
}

function maskEmail(value: string) {
  const [user, domain] = value.split("@");
  if (!user || !domain) return value;
  return `${user.slice(0, 2)}***@${domain}`;
}

export function ConsentPage({
  solicitud,
  currentStep,
  loading,
  loadingSend,
  loadingVerify,
  error,
  onSubmit,
  onSend,
  onVerify,
}: ConsentPageProps) {
  const [documents, setDocuments] = useState<LegalDocument[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [documentsError, setDocumentsError] = useState("");
  const [documentState, setDocumentState] = useState<Record<number, DocumentUiState>>({});
  const [otpModalOpen, setOtpModalOpen] = useState(false);
  const [channel, setChannel] = useState<OtpChannel>(solicitud.otp?.canal ?? "SMS");
  const [otpDigits, setOtpDigits] = useState<string[]>(() => Array.from({ length: OTP_LENGTH }, () => ""));
  const consentCopy = solicitud.consent_copy;
  const otp = solicitud.otp;
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const mobileViewport = isMobileViewport();

  const resolvePdfUrl = (document: LegalDocument) => document.pdf_url;

  useEffect(() => {
    let mounted = true;

    const loadDocuments = async () => {
      setDocumentsLoading(true);
      setDocumentsError("");
      try {
        const response = await fetchLegalDocuments();
        if (!mounted) return;
        setDocuments(response);
      } catch (apiError) {
        if (!mounted) return;
        setDocumentsError(
          apiError instanceof Error ? apiError.message : "No fue posible cargar la autorización de centrales."
        );
      } finally {
        if (mounted) {
          setDocumentsLoading(false);
        }
      }
    };

    void loadDocuments();

    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    setChannel(solicitud.otp?.canal ?? "SMS");
  }, [solicitud.otp?.canal]);

  useEffect(() => {
    setOtpModalOpen(currentStep === "otp");
  }, [currentStep]);

  useEffect(() => {
    if (otpModalOpen && otp?.enviado_at) {
      inputRefs.current[0]?.focus();
    }
  }, [otpModalOpen, otp?.enviado_at]);

  const getDocumentState = (documentId: number) => documentState[documentId] ?? defaultDocumentState;

  const closeOpenedDocuments = (now: number, current: Record<number, DocumentUiState>) => {
    const nextState: Record<number, DocumentUiState> = {};

    Object.entries(current).forEach(([key, value]) => {
      const documentId = Number(key);
      nextState[documentId] = value.opened
        ? {
            ...value,
            opened: false,
            openedAt: null,
            viewedSeconds:
              value.viewedSeconds + Math.max(1, Math.round((now - (value.openedAt ?? now)) / 1000)),
          }
        : value;
    });

    return nextState;
  };

  const openDocument = (documentId: number) => {
    const document = documents.find((item) => item.id === documentId);
    setDocumentState((current) => {
      const now = Date.now();
      const nextState = closeOpenedDocuments(now, current);
      const currentState = current[documentId] ?? defaultDocumentState;

      if (mobileViewport && document) {
        const targetUrl = resolvePdfUrl(document);
        window.open(targetUrl, "_blank", "noopener,noreferrer");
        nextState[documentId] = {
          ...currentState,
          opened: false,
          openedExternally: true,
          loaded: true,
          openedAt: now,
        };
        return nextState;
      }

      nextState[documentId] = {
        ...currentState,
        opened: true,
        openedExternally: false,
        openedAt: now,
      };

      return nextState;
    });
  };

  const closeDocument = (documentId: number) => {
    setDocumentState((current) => {
      const now = Date.now();
      const state = current[documentId] ?? defaultDocumentState;
      if (!state.opened && !state.openedExternally) {
        return current;
      }
      return {
        ...current,
        [documentId]: {
          ...state,
          opened: false,
          openedExternally: false,
          openedAt: null,
          viewedSeconds: state.viewedSeconds + Math.max(1, Math.round((now - (state.openedAt ?? now)) / 1000)),
        },
      };
    });
  };

  const updateDocumentState = (documentId: number, changes: Partial<DocumentUiState>) => {
    setDocumentState((current) => ({
      ...current,
      [documentId]: {
        ...(current[documentId] ?? defaultDocumentState),
        ...changes,
      },
    }));
  };

  const confirmExternalRead = (documentId: number) => {
    setDocumentState((current) => {
      const state = current[documentId] ?? defaultDocumentState;
      const now = Date.now();
      return {
        ...current,
        [documentId]: {
          ...state,
          openedExternally: false,
          accepted: true,
          reachedEnd: true,
          openedAt: null,
          viewedSeconds: state.viewedSeconds + Math.max(1, Math.round((now - (state.openedAt ?? now)) / 1000)),
        },
      };
    });
  };

  const getViewedSeconds = (documentId: number) => {
    const state = getDocumentState(documentId);
    if (!state.opened || !state.openedAt) {
      return state.viewedSeconds;
    }
    return state.viewedSeconds + Math.max(1, Math.round((Date.now() - state.openedAt) / 1000));
  };

  const allAccepted = useMemo(
    () =>
      documents.length > 0 &&
      documents.every((document) => {
        const ui = getDocumentState(document.id);
        return ui.accepted && ui.reachedEnd;
      }),
    [documentState, documents]
  );

  const openedDocument = useMemo(
    () => documents.find((document) => getDocumentState(document.id).opened) ?? null,
    [documentState, documents]
  );

  const otpCode = otpDigits.join("");
  const canResend = !otp || otp.resend_available_in_seconds === 0;
  const selectedDestination =
    channel === "SMS" ? maskPhone(solicitud.solicitud.solicitante.celular) : maskEmail(solicitud.solicitud.solicitante.email);

  const handleCheckboxIntent = (documentId: number) => {
    const state = getDocumentState(documentId);
    if (!state.reachedEnd) {
      openDocument(documentId);
    }
  };

  const handleContinue = async () => {
    const acceptedDocuments = documents.map((document) => ({
      document_id: document.id,
      viewed_seconds: getViewedSeconds(document.id),
      reached_end: getDocumentState(document.id).reachedEnd,
    }));

    await onSubmit({
      accepted: true,
      version: consentCopy.version,
      canal: channel,
      text_snapshot: consentCopy.summary,
      accepted_documents: acceptedDocuments,
    });
  };

  const handleDigitChange = (index: number, nextValue: string) => {
    const digit = nextValue.replace(/\D/g, "").slice(-1);
    setOtpDigits((current) => {
      const next = [...current];
      next[index] = digit;
      return next;
    });
    if (digit && index < OTP_LENGTH - 1) {
      inputRefs.current[index + 1]?.focus();
    }
  };

  const handleDigitKeyDown = (index: number, event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Backspace" && !otpDigits[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
    if (event.key === "ArrowLeft" && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
    if (event.key === "ArrowRight" && index < OTP_LENGTH - 1) {
      inputRefs.current[index + 1]?.focus();
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault();
    const pasted = event.clipboardData.getData("text").replace(/\D/g, "").slice(0, OTP_LENGTH);
    if (!pasted) return;
    setOtpDigits((current) => current.map((_, index) => pasted[index] ?? ""));
    const focusIndex = Math.min(pasted.length, OTP_LENGTH - 1);
    inputRefs.current[focusIndex]?.focus();
  };

  const handleVerify = async () => {
    if (otpCode.length !== OTP_LENGTH) return;
    await onVerify(otpCode);
    setOtpDigits(Array.from({ length: OTP_LENGTH }, () => ""));
  };

  return (
    <div className="row justify-content-center">
      <div className="col-12">
        <div className="content-card authorization-card">
          <div className="d-flex flex-column flex-lg-row justify-content-between gap-3 mb-4">
            <div>
              <span className="eyebrow">Paso 2</span>
              <h2 className="section-title mt-3 mb-2">Autorización de centrales de riesgo</h2>
              <p className="section-copy mb-0">
                Revisa la autorización, llévala hasta el final y registra la aceptación antes de continuar con la validación de identidad.
              </p>
            </div>
          </div>

          <div className="approval-summary-card mb-4">
            <div className="approval-summary-card__item">
              <span>Solicitud</span>
              <strong>{solicitud.solicitud.numero_solicitud}</strong>
              <small>Crédito digital</small>
            </div>
            <div className="approval-summary-card__item">
              <span>Solicitante</span>
              <strong>{solicitud.solicitud.solicitante.primer_apellido}</strong>
              <small>{solicitud.solicitud.solicitante.numero_identificacion}</small>
            </div>
            <div className="approval-summary-card__item approval-summary-card__item--status">
              <span>Estado del flujo</span>
              <strong>{otp?.verificado_at ? "OTP validada" : "Pendiente de OTP"}</strong>
              <small>Paso 2 de 4</small>
            </div>
          </div>

          <div className="row g-3">
            {documentsLoading ? (
              <div className="col-12">
                <div className="alert alert-light border mb-0">Cargando autorización de centrales...</div>
              </div>
            ) : null}

            {documents.map((document) => {
              const ui = getDocumentState(document.id);
              const viewedSeconds = getViewedSeconds(document.id);

              return (
                <div key={document.id} className="col-12">
                  <div className="document-card">
                    <div className="d-flex flex-column flex-lg-row justify-content-between gap-3 align-items-lg-start">
                      <div className="flex-grow-1">
                        <div className="document-card__type">{document.tipo_documento}</div>
                        <h3 className="document-card__title">{document.titulo}</h3>
                        <p className="document-card__description mb-2">{document.descripcion}</p>
                        <div className="document-card__meta">
                          {ui.loaded ? (
                            <span className="badge bg-success-subtle text-success-emphasis">PDF cargado</span>
                          ) : null}
                          {ui.reachedEnd ? (
                            <span className="badge bg-primary-subtle text-primary-emphasis">Lectura completa</span>
                          ) : null}
                          {viewedSeconds > 0 ? (
                            <span className="badge bg-secondary-subtle text-secondary-emphasis">
                              {viewedSeconds}s visualizados
                            </span>
                          ) : null}
                        </div>
                      </div>

                      <div className="document-card__actions">
                        <button
                          type="button"
                          className="btn btn-outline-primary btn-sm"
                          onClick={() => (ui.opened ? closeDocument(document.id) : openDocument(document.id))}
                        >
                          {ui.opened ? "Cerrar visor" : mobileViewport ? "Abrir documento" : "Ver documento"}
                        </button>

                        <button
                          type="button"
                          className={`document-card__accept-trigger ${ui.accepted ? "is-accepted" : ""}`}
                          onClick={() => handleCheckboxIntent(document.id)}
                        >
                          <span className={`document-card__checkbox-fake ${ui.accepted ? "is-checked" : ""}`}>
                            {ui.accepted ? "✓" : ""}
                          </span>
                          <span>
                            {ui.accepted
                              ? "Autorización aceptada"
                              : ui.reachedEnd
                              ? "Aceptar autorización"
                              : "Leer para habilitar la aceptación"}
                          </span>
                        </button>
                      </div>
                    </div>

                    {ui.opened ? (
                      <div className="document-card__viewer mt-3 document-card__viewer-placeholder">
                        Visor abierto en modal.
                      </div>
                    ) : null}

                    {mobileViewport && ui.openedExternally && !ui.accepted ? (
                      <div className="document-mobile-review mt-3">
                        <strong>Autorización abierta en una vista legible del navegador.</strong>
                        <p className="mb-2">Cuando termines de revisarlo, confirma la lectura para habilitar la aceptación.</p>
                        <div className="d-flex flex-wrap gap-2">
                          <button
                            type="button"
                            className="btn btn-outline-primary btn-sm"
                            onClick={() => openDocument(document.id)}
                          >
                            Abrir de nuevo
                          </button>
                          <button
                            type="button"
                            className="btn btn-brand btn-sm"
                            onClick={() => confirmExternalRead(document.id)}
                          >
                            Confirmar lectura
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>

          {documentsError ? <div className="alert alert-danger mt-3 mb-0">{documentsError}</div> : null}
          {error && !otpModalOpen ? <div className="alert alert-danger mt-3 mb-0">{error}</div> : null}

          <button
            type="button"
            className="btn btn-brand btn-lg w-100 mt-4"
            disabled={loading || documentsLoading || !documents.length || !allAccepted}
            onClick={() => {
              if (currentStep === "otp") {
                setOtpModalOpen(true);
                return;
              }
              void handleContinue();
            }}
          >
            {loading ? "Guardando autorización..." : currentStep === "otp" ? "Continuar con verificación" : "Continuar"}
          </button>
        </div>
      </div>

      {openedDocument && !mobileViewport ? (
        <div className="document-modal" role="dialog" aria-modal="true" aria-label={openedDocument.titulo}>
          <div className="document-modal__dialog">
            <div className="document-modal__header">
              <div>
                <strong>{openedDocument.titulo}</strong>
                <span>PDF de prueba embebido para validación del flujo de lectura.</span>
              </div>
              <button
                type="button"
                className="btn document-modal__close"
                onClick={() => closeDocument(openedDocument.id)}
              >
                Cerrar
              </button>
            </div>

            <div className="document-modal__body">
              <PdfScrollViewer
                title={openedDocument.titulo}
                file={resolvePdfUrl(openedDocument)}
                onLoad={() => updateDocumentState(openedDocument.id, { loaded: true })}
                onReachEnd={() =>
                  updateDocumentState(openedDocument.id, {
                    reachedEnd: true,
                    accepted: true,
                  })
                }
              />
            </div>
          </div>
        </div>
      ) : null}

      {otpModalOpen ? (
        <div className="otp-modal" role="dialog" aria-modal="true" aria-label="Verificación de identidad">
          <div className="otp-modal__dialog">
            <div className="otp-modal__header">
              <div>
                <span className="otp-modal__eyebrow">Validación de identidad</span>
                <h3>Confirma la identidad para continuar</h3>
                <p>Usa el canal autorizado y verifica el código recibido. Al confirmarlo continuaremos con la evaluación del flujo.</p>
              </div>
              <button type="button" className="btn otp-modal__close" onClick={() => setOtpModalOpen(false)}>
                Cerrar
              </button>
            </div>

            <div className="otp-modal__summary">
              <strong>Solicitud {solicitud.solicitud.numero_solicitud}</strong>
              <span>
                {solicitud.solicitud.solicitante.numero_identificacion} - {solicitud.solicitud.solicitante.primer_apellido}
              </span>
            </div>

            <div className="otp-modal__body">
              <div className="otp-channel-group">
                <span className="otp-channel-group__label">Canal</span>
                <label className={`otp-channel-option ${channel === "SMS" ? "is-active" : ""}`}>
                  <input
                    type="radio"
                    name="otp-channel"
                    checked={channel === "SMS"}
                    onChange={() => setChannel("SMS")}
                  />
                  <div>
                    <strong>SMS al celular</strong>
                    <span>{maskPhone(solicitud.solicitud.solicitante.celular)}</span>
                  </div>
                </label>
                <label className={`otp-channel-option ${channel === "EMAIL" ? "is-active" : ""}`}>
                  <input
                    type="radio"
                    name="otp-channel"
                    checked={channel === "EMAIL"}
                    onChange={() => setChannel("EMAIL")}
                  />
                  <div>
                    <strong>Email</strong>
                    <span>{maskEmail(solicitud.solicitud.solicitante.email)}</span>
                  </div>
                </label>
              </div>

              <div className="otp-modal__actions">
                <button type="button" className="btn btn-outline-primary" disabled={loadingSend || !canResend} onClick={() => void onSend(channel)}>
                  {loadingSend
                    ? "Enviando..."
                    : otp?.enviado_at
                    ? canResend
                      ? "Reenviar código"
                      : `Reenviar en ${otp.resend_available_in_seconds}s`
                    : "Enviar código"}
                </button>
              </div>

              <div className="otp-status-card">
                <div className="otp-status-card__row">
                  <span>Destino</span>
                  <strong>{otp?.destino || selectedDestination}</strong>
                </div>
                <div className="otp-status-card__row">
                  <span>Estado</span>
                  <strong>{otp?.estado || "PENDIENTE"}</strong>
                </div>
                <div className="otp-status-card__row">
                  <span>Intentos</span>
                  <strong>
                    {otp?.intentos ?? 0} / {otp?.max_intentos ?? 3}
                  </strong>
                </div>
                {otp?.debug_code ? <div className="otp-debug">Código de prueba: {otp.debug_code}</div> : null}
              </div>

              <div className="otp-code-group">
                <span className="otp-channel-group__label">Código de verificación</span>
                <div className="otp-code-inputs">
                  {otpDigits.map((digit, index) => (
                    <input
                      key={`otp-digit-${index}`}
                      ref={(element) => {
                        inputRefs.current[index] = element;
                      }}
                      className="otp-code-inputs__digit"
                      inputMode="numeric"
                      maxLength={1}
                      value={digit}
                      onChange={(event) => handleDigitChange(index, event.target.value)}
                      onKeyDown={(event) => handleDigitKeyDown(index, event)}
                      onPaste={handlePaste}
                    />
                  ))}
                </div>
              </div>

              {error ? <div className="alert alert-danger mb-0">{error}</div> : null}

              <button
                type="button"
                className="btn btn-brand btn-lg w-100"
                disabled={loadingVerify || otpCode.length !== OTP_LENGTH}
                onClick={() => void handleVerify()}
              >
                {loadingVerify ? (<>
                  <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
                  Verificando...
                </>) : "Verificar"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}



