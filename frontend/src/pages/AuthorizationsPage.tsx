import { useMemo, useState } from "react";

import { PdfScrollViewer } from "../components/PdfScrollViewer";
import { LegalDocument } from "../types/credit";

interface AuthorizationsPageProps {
  documents: LegalDocument[];
  loading: boolean;
  error: string;
  onBack: () => void;
  onContinue: (
    acceptedDocuments: Array<{ document_id: number; viewed_seconds: number; reached_end: boolean }>
  ) => Promise<void>;
}

interface DocumentUiState {
  opened: boolean;
  loaded: boolean;
  accepted: boolean;
  reachedEnd: boolean;
  viewedSeconds: number;
  openedAt: number | null;
}

const defaultState: DocumentUiState = {
  opened: false,
  loaded: false,
  accepted: false,
  reachedEnd: false,
  viewedSeconds: 0,
  openedAt: null,
};

export function AuthorizationsPage({
  documents,
  loading,
  error,
  onBack,
  onContinue,
}: AuthorizationsPageProps) {
  const [documentState, setDocumentState] = useState<Record<number, DocumentUiState>>({});

  const allAccepted = useMemo(
    () =>
      documents.length > 0 &&
      documents.every((document) => {
        const ui = documentState[document.id];
        return ui?.accepted && ui.reachedEnd;
      }),
    [documentState, documents]
  );

  const getDocumentState = (documentId: number) => documentState[documentId] ?? defaultState;

  const resolvePdfUrl = (document: LegalDocument) => document.pdf_url;

  const closeOpenedDocuments = (
    current: Record<number, DocumentUiState>,
    now: number
  ): Record<number, DocumentUiState> => {
    const nextState: Record<number, DocumentUiState> = {};

    Object.entries(current).forEach(([key, value]) => {
      const documentId = Number(key);
      nextState[documentId] = value.opened
        ? {
            ...value,
            opened: false,
            openedAt: null,
            viewedSeconds:
              value.viewedSeconds +
              Math.max(1, Math.round((now - (value.openedAt ?? now)) / 1000)),
          }
        : value;
    });

    return nextState;
  };

  const toggleDocument = (documentId: number) => {
    setDocumentState((current) => {
      const now = Date.now();
      const nextState = closeOpenedDocuments(current, now);
      const currentState = nextState[documentId] ?? defaultState;
      const willOpen = !currentState.opened;

      nextState[documentId] = {
        ...currentState,
        opened: willOpen,
        openedAt: willOpen ? now : null,
      };

      return nextState;
    });
  };

  const updateDocumentState = (documentId: number, changes: Partial<DocumentUiState>) => {
    setDocumentState((current) => ({
      ...current,
      [documentId]: {
        ...(current[documentId] ?? defaultState),
        ...changes,
      },
    }));
  };

  const getViewedSeconds = (documentId: number) => {
    const state = getDocumentState(documentId);
    if (!state.opened || !state.openedAt) return state.viewedSeconds;
    return state.viewedSeconds + Math.max(1, Math.round((Date.now() - state.openedAt) / 1000));
  };

  const handleContinueClick = async () => {
    const acceptedDocuments = documents.map((document) => ({
      document_id: document.id,
      viewed_seconds: getViewedSeconds(document.id),
      reached_end: getDocumentState(document.id).reachedEnd,
    }));

    await onContinue(acceptedDocuments);
  };

  return (
    <div className="row justify-content-center">
      <div className="col-12">
        <div className="content-card authorization-card">
          <div className="d-flex flex-column flex-lg-row justify-content-between align-items-lg-end gap-3 mb-3">
            <div>
              <span className="eyebrow">Paso 2</span>
              <h2 className="section-title section-title--compact mt-2 mb-2">
                Autorización de centrales de riesgo
              </h2>
              <p className="section-copy section-copy--compact mb-0">
                Visualiza la autorización dentro de la aplicación, recórrela completa y luego acepta.
              </p>
            </div>
            <div className="summary-chip summary-chip--compact">
              Debes aceptar {documents.length} de {documents.length}
            </div>
          </div>

          <div className="row g-3">
            {!documents.length ? (
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
                            <span className="badge bg-success-subtle text-success-emphasis">
                              PDF cargado
                            </span>
                          ) : null}
                          {ui.reachedEnd ? (
                            <span className="badge bg-primary-subtle text-primary-emphasis">
                              Lectura completa
                            </span>
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
                          onClick={() => toggleDocument(document.id)}
                        >
                          {ui.opened ? "Ocultar PDF" : "Visualizar PDF"}
                        </button>

                        <div className="form-check document-card__checkbox">
                          <input
                            className="form-check-input"
                            type="checkbox"
                            id={`document-${document.id}`}
                            checked={ui.accepted}
                            disabled={!ui.reachedEnd}
                            onChange={(event) =>
                              updateDocumentState(document.id, { accepted: event.target.checked })
                            }
                          />
                          <label className="form-check-label" htmlFor={`document-${document.id}`}>
                            He leído y acepto esta autorización
                          </label>
                        </div>
                      </div>
                    </div>

                    {ui.opened ? (
                      <div className="document-card__viewer mt-3">
                        <PdfScrollViewer
                          title={document.titulo}
                          file={resolvePdfUrl(document)}
                          onLoad={() => updateDocumentState(document.id, { loaded: true })}
                          onReachEnd={() => updateDocumentState(document.id, { reachedEnd: true })}
                        />
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>

          {error ? <div className="alert alert-danger mt-3 mb-0">{error}</div> : null}

          <div className="d-flex flex-column flex-sm-row justify-content-between gap-3 mt-4">
            <button
              type="button"
              className="btn btn-light btn-lg"
              onClick={onBack}
              disabled={loading}
            >
              Volver
            </button>
            <button
              type="button"
              className="btn btn-brand btn-lg"
              onClick={handleContinueClick}
              disabled={!allAccepted || loading || !documents.length}
            >
              {loading ? "Enviando..." : "Autorizar y continuar"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
