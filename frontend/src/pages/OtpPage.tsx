import { FormEvent, useMemo, useState } from "react";

import { ConsumoSolicitudStatus, OtpChannel } from "../types/credit";

interface OtpPageProps {
  solicitud: ConsumoSolicitudStatus;
  loadingSend: boolean;
  loadingVerify: boolean;
  error: string;
  onSend: (channel: OtpChannel) => Promise<void>;
  onVerify: (code: string) => Promise<void>;
}

export function OtpPage({ solicitud, loadingSend, loadingVerify, error, onSend, onVerify }: OtpPageProps) {
  const [channel, setChannel] = useState<OtpChannel>(solicitud.otp?.canal ?? "SMS");
  const [code, setCode] = useState("");
  const otp = solicitud.otp;

  const canResend = useMemo(() => !otp || otp.resend_available_in_seconds === 0, [otp]);

  const handleVerify = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onVerify(code);
  };

  return (
    <div className="row justify-content-center">
      <div className="col-12 col-lg-8 col-xl-6">
        <div className="content-card form-card">
          <div className="form-stage text-center mb-4">
            <span className="eyebrow">Paso 3</span>
            <h2 className="section-title mt-3 mb-2">Verificacion de identidad</h2>
            <p className="section-copy mb-0">
              Despues de aceptar terminos y documentos, valida el codigo de seguridad para continuar.
            </p>
          </div>

          <div className="otp-summary mb-4">
            <strong>{solicitud.solicitud.numero_solicitud}</strong>
            <span>
              Solicitante: {solicitud.solicitud.solicitante.numero_identificacion} ·{" "}
              {solicitud.solicitud.solicitante.primer_apellido}
            </span>
          </div>

          <div className="row g-3 mb-4">
            <div className="col-sm-6">
              <label className="form-label">Canal</label>
              <select
                className="form-select"
                value={channel}
                onChange={(event) => setChannel(event.target.value as OtpChannel)}
              >
                <option value="SMS">SMS al celular</option>
                <option value="EMAIL">Correo electronico</option>
              </select>
            </div>
            <div className="col-sm-6 d-flex align-items-end">
              <button
                type="button"
                className="btn btn-outline-primary w-100"
                disabled={loadingSend || !canResend}
                onClick={() => onSend(channel)}
              >
                {loadingSend
                  ? "Enviando..."
                  : otp?.enviado_at
                  ? canResend
                    ? "Reenviar OTP"
                    : `Reenviar en ${otp.resend_available_in_seconds}s`
                  : "Enviar OTP"}
              </button>
            </div>
          </div>

          <div className="otp-status-card mb-4">
            <div className="otp-status-card__row">
              <span>Destino</span>
              <strong>{otp?.destino || "Pendiente de envio"}</strong>
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
            {otp?.debug_code ? <div className="otp-debug mt-3">Codigo de prueba: {otp.debug_code}</div> : null}
          </div>

          <form className="row g-3" onSubmit={handleVerify}>
            <div className="col-12">
              <label className="form-label">Codigo de verificacion</label>
              <input
                className="form-control"
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="Ingresa el codigo de 6 digitos"
              />
            </div>

            {error ? (
              <div className="col-12">
                <div className="alert alert-danger mb-0">{error}</div>
              </div>
            ) : null}

            <div className="col-12 pt-2">
              <button type="submit" className="btn btn-brand btn-lg w-100" disabled={loadingVerify || code.length < 4}>
                {loadingVerify ? (<>
                  <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
                  Validando...
                </>) : "Confirmar verificacion"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
