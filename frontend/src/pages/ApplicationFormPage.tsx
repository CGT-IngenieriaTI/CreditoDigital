import { FormEvent, useMemo, useState } from "react";

import { StatusModal } from "../components/StatusModal";
import { ConsumoOrchestrationSnapshot, FormularioBasico } from "../types/credit";

const initialForm: FormularioBasico = {
  tipo_identificacion: "CC",
  numero_identificacion: "",
  primer_apellido: "",
  fecha_expedicion: "",
  celular: "",
  email: "",
};

interface ApplicationFormPageProps {
  loading: boolean;
  error: string;
  onSubmit: (payload: FormularioBasico) => Promise<void>;
  onPreview: (payload: FormularioBasico) => Promise<ConsumoOrchestrationSnapshot>;
}

const pause = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
const normalizeColombiaMobileInput = (value: string) => {
  const digits = value.replace(/\D/g, "");
  if (digits.startsWith("57") && digits.length >= 12) {
    return digits.slice(2, 12);
  }
  return digits.slice(0, 10);
};

const APELLIDO_SANITIZER = /[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]/g;
const APELLIDO_VALIDATOR = /^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ\s]+$/;

export function ApplicationFormPage({
  loading,
  error,
  onSubmit,
  onPreview,
}: ApplicationFormPageProps) {
  const [formData, setFormData] = useState<FormularioBasico>(initialForm);
  const [localErrors, setLocalErrors] = useState<Partial<Record<keyof FormularioBasico, string>>>({});
  const [preview, setPreview] = useState<ConsumoOrchestrationSnapshot | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [showSuccessModal, setShowSuccessModal] = useState(false);

  const maxDate = useMemo(() => new Date().toISOString().split("T")[0], []);
  const isSubmitting = previewLoading || loading;
  const duplicateRequest = preview?.duplicate_request;
  const hasDuplicateRequest = Boolean(duplicateRequest?.has_active);

  const setField = (field: keyof FormularioBasico, value: string) => {
    setFormData((current) => ({
      ...current,
      [field]: value,
    }));
    setLocalErrors((current) => ({
      ...current,
      [field]: "",
    }));
    if (preview) {
      setPreview(null);
    }
    if (previewError) {
      setPreviewError("");
    }
  };

  const validateForm = () => {
    const errors: Partial<Record<keyof FormularioBasico, string>> = {};

    if (!/^\d{6,10}$/.test(formData.numero_identificacion)) {
      errors.numero_identificacion = "Ingresa un número de identificación válido de hasta 10 dígitos.";
    }
    if (!formData.primer_apellido.trim()) {
      errors.primer_apellido = "El primer apellido es obligatorio.";
    } else if (!APELLIDO_VALIDATOR.test(formData.primer_apellido.trim())) {
      errors.primer_apellido = "El primer apellido solo permite letras y espacios.";
    }
    if (!formData.fecha_expedicion) {
      errors.fecha_expedicion = "La fecha de expedición es obligatoria.";
    }
    if (!/^3\d{9}$/.test(formData.celular)) {
      errors.celular = "El celular debe iniciar en 3 y tener 10 dígitos.";
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email)) {
      errors.email = "Ingresa un correo electrónico válido.";
    }

    setLocalErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const normalizedPayload: FormularioBasico = {
    ...formData,
    primer_apellido: formData.primer_apellido.trim().toUpperCase(),
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !validateForm()) {
      return;
    }

    setPreviewLoading(true);
    setPreviewError("");
    setPreview(null);

    try {
      const response = await onPreview(normalizedPayload);
      setPreview(response);
      setFormData((current) => ({
        ...current,
        primer_apellido: response.form_defaults?.primer_apellido || normalizedPayload.primer_apellido,
        fecha_expedicion: response.form_defaults?.fecha_expedicion || current.fecha_expedicion,
        celular: response.form_defaults?.celular || current.celular,
        email: response.form_defaults?.email || current.email,
      }));

      if (!response.can_continue) {
        return;
      }

      setShowSuccessModal(true);
      await pause(2700);
      setShowSuccessModal(false);
      await onSubmit(normalizedPayload);
    } catch (apiError) {
      setPreviewError(
        apiError instanceof Error
          ? apiError.message
          : "No fue posible consultar el asociado en este momento."
      );
    } finally {
      setPreviewLoading(false);
    }
  };

  const successTitle = preview?.duplicate_request?.resume_available
    ? "Solicitud localizada correctamente"
    : "Calidad de asociado validada";
  const successMessage = preview?.duplicate_request?.resume_available
    ? "Encontramos una solicitud vigente del mismo asesor. La retomaremos para continuar el proceso sin repetir pasos ya completados."
    : "Confirmamos la información base del asociado en LINIX. A continuación te llevaremos al paso de autorización de centrales.";
  const successDetails = [preview?.form_defaults?.nombre?.trim(), formData.numero_identificacion]
    .filter(Boolean)
    .map((value) => String(value));

  return (
    <>
      <div className="row justify-content-center">
        <div className="col-12">
          <div className="content-card form-card">
            <div className="form-stage text-center mb-3">
              <span className="eyebrow">Datos básicos</span>
              <h2 className="section-title mt-3 mb-2">Identificación y consulta inicial</h2>
              <p className="section-copy mb-0">
                Completa los datos del asociado. Al continuar validaremos la calidad del asociado y la información base en LINIX.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="row g-3">
              <div className="col-md-6">
                <label className="form-label">Tipo de Identificación</label>
                <select
                  className="form-select"
                  value={formData.tipo_identificacion}
                  onChange={(event) =>
                    setField(
                      "tipo_identificacion",
                      event.target.value as FormularioBasico["tipo_identificacion"]
                    )
                  }
                >
                  <option value="CC">Cédula de Ciudadanía</option>
                  <option value="CE">Cédula de Extranjería</option>
                  <option value="TI">Tarjeta de Identidad</option>
                  <option value="PAS">Pasaporte</option>
                </select>
              </div>

              <div className="col-md-6">
                <label className="form-label">Número de Identificación *</label>
                <input
                  className={`form-control ${localErrors.numero_identificacion ? "is-invalid" : ""}`}
                  value={formData.numero_identificacion}
                  onChange={(event) =>
                    setField(
                      "numero_identificacion",
                      event.target.value.replace(/\D/g, "").slice(0, 10)
                    )
                  }
                  placeholder="86080032"
                  inputMode="numeric"
                  maxLength={10}
                />
                {localErrors.numero_identificacion ? (
                  <div className="invalid-feedback d-block">{localErrors.numero_identificacion}</div>
                ) : null}
              </div>

              <div className="col-md-6">
                <label className="form-label">Primer apellido *</label>
                <input
                  className={`form-control ${localErrors.primer_apellido ? "is-invalid" : ""}`}
                  value={formData.primer_apellido}
                  onChange={(event) =>
                    setField(
                      "primer_apellido",
                      event.target.value.replace(APELLIDO_SANITIZER, "").replace(/\s+/g, " ").toUpperCase()
                    )
                  }
                  placeholder="ORTIZ"
                />
                <div className="invalid-feedback">{localErrors.primer_apellido}</div>
              </div>

              <div className="col-md-6">
                <label className="form-label">Fecha de Expedición del Documento *</label>
                <input
                  type="date"
                  max={maxDate}
                  className={`form-control ${localErrors.fecha_expedicion ? "is-invalid" : ""}`}
                  value={formData.fecha_expedicion}
                  onChange={(event) => setField("fecha_expedicion", event.target.value)}
                />
                <div className="invalid-feedback">{localErrors.fecha_expedicion}</div>
              </div>

              <div className="col-md-6">
                <label className="form-label">Celular *</label>
                <input
                  className={`form-control ${localErrors.celular ? "is-invalid" : ""}`}
                  value={formData.celular}
                  onChange={(event) =>
                    setField("celular", normalizeColombiaMobileInput(event.target.value))
                  }
                  placeholder="3001234567"
                  inputMode="numeric"
                  maxLength={10}
                />
                <div className="invalid-feedback">{localErrors.celular}</div>
              </div>

              <div className="col-md-6">
                <label className="form-label">Correo electrónico *</label>
                <input
                  type="email"
                  className={`form-control ${localErrors.email ? "is-invalid" : ""}`}
                  value={formData.email}
                  onChange={(event) => setField("email", event.target.value)}
                  placeholder="correo@ejemplo.com"
                />
                <div className="invalid-feedback">{localErrors.email}</div>
              </div>

              {hasDuplicateRequest ? (
                <div className="col-12">
                  <div
                    className={`lookup-notice ${
                      duplicateRequest?.resume_available ? "lookup-notice--info" : "lookup-notice--warning"
                    }`}
                    role="status"
                    aria-live="polite"
                  >
                    <div className="lookup-notice__icon" aria-hidden="true">
                      {duplicateRequest?.resume_available ? "i" : "!"}
                    </div>
                    <div className="lookup-notice__content">
                      <strong>
                        {duplicateRequest?.resume_available
                          ? "Solicitud vigente localizada"
                          : "Solicitud activa encontrada"}
                      </strong>
                      <span>{duplicateRequest?.message}</span>
                    </div>
                  </div>
                </div>
              ) : null}

              {preview?.validation_credito_digital?.message &&
              !preview.validation_credito_digital.ok &&
              !hasDuplicateRequest ? (
                <div className="col-12">
                  <div
                    className={`alert ${preview.validation_credito_digital.blocked ? "alert-danger" : "alert-warning"} mb-0`}
                  >
                    {preview.validation_credito_digital.message}
                  </div>
                </div>
              ) : null}

              {preview?.core && !preview.core.found && preview.validation_credito_digital?.ok ? (
                <div className="col-12">
                  <div className="alert alert-warning mb-0">{preview.core.message}</div>
                </div>
              ) : null}

              {previewError ? (
                <div className="col-12">
                  <div className="alert alert-warning mb-0">{previewError}</div>
                </div>
              ) : null}

              {error ? (
                <div className="col-12">
                  <div className="alert alert-danger mb-0">{error}</div>
                </div>
              ) : null}

              <div className="col-12 pt-2">
                <button type="submit" className="btn btn-brand btn-lg w-100" disabled={isSubmitting}>
                  {previewLoading ? "Consultando calidad de asociado..." : loading ? "Creando solicitud..." : "Continuar"}
                </button>
                <p className="form-helper-copy mt-3 mb-0 text-center">
                  Validaremos la información base del asociado y, si la consulta es exitosa, continuaremos automáticamente al paso de autorización de centrales.
                </p>
              </div>
            </form>
          </div>
        </div>
      </div>

      <StatusModal
        visible={showSuccessModal}
        tone="success"
        label="Validación completada"
        title={successTitle}
        message={successMessage}
        details={successDetails}
      />
    </>
  );
}
