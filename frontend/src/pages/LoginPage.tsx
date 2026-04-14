import { FormEvent, useState } from "react";

import logoCongente from "../assets/LogoHD.png";
import iconoCongente from "../assets/IconoHD.png";

interface LoginPageProps {
  loading: boolean;
  error: string;
  onSubmit: (payload: { username: string; password: string }) => Promise<void>;
}

export function LoginPage({ loading, error, onSubmit }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onSubmit({ username, password });
  };

  return (
    <div className="login-screen">
      <div className="login-screen__shape login-screen__shape--left" />
      <div className="login-screen__shape login-screen__shape--right" />
      <img src={iconoCongente} alt="" className="login-screen__watermark login-screen__watermark--left" aria-hidden="true" />
      <img src={iconoCongente} alt="" className="login-screen__watermark login-screen__watermark--right" aria-hidden="true" />

      <div className="container login-screen__container">
        <div className="login-card">
          <div className="login-card__brand">
            <img src={logoCongente} alt="Congente" className="login-card__logo" />
          </div>
          <div className="login-card__divider" />

          <div className="login-card__body">
            <form className="login-form" onSubmit={handleSubmit}>
              <div className="login-field">
                <label className="login-field__label" htmlFor="username">
                  Usuario
                </label>
                <div className="login-field__control">
                  <span className="login-field__icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24">
                      <path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z" />
                      <path d="M4 19a8 8 0 0 1 16 0" />
                    </svg>
                  </span>
                  <input
                    id="username"
                    className="form-control login-field__input"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="Usuario"
                    autoComplete="username"
                  />
                </div>
              </div>

              <div className="login-field">
                <label className="login-field__label" htmlFor="password">
                  Contraseña
                </label>
                <div className="login-field__control">
                  <span className="login-field__icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24">
                      <rect x="6" y="10" width="12" height="10" rx="2" />
                      <path d="M9 10V7a3 3 0 1 1 6 0v3" />
                    </svg>
                  </span>
                  <input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    className="form-control login-field__input login-field__input--password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="Contraseña"
                    autoComplete="current-password"
                  />
                  <button
                    type="button"
                    className="login-field__toggle"
                    onClick={() => setShowPassword((current) => !current)}
                    aria-label={showPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
                  >
                    {showPassword ? (
                      <svg viewBox="0 0 24 24">
                        <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
                        <circle cx="12" cy="12" r="3" />
                      </svg>
                    ) : (
                      <svg viewBox="0 0 24 24">
                        <path d="m3 3 18 18" />
                        <path d="M10.6 6.3A11.6 11.6 0 0 1 12 6c6.5 0 10 6 10 6a18.1 18.1 0 0 1-4.1 4.5" />
                        <path d="M6 6.7A18.8 18.8 0 0 0 2 12s3.5 6 10 6a11.2 11.2 0 0 0 3.1-.4" />
                        <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              {error ? <div className="alert alert-danger login-card__alert mb-0">{error}</div> : null}

              <button type="submit" className="btn login-card__submit w-100" disabled={loading}>
                <span aria-hidden="true">
                  <svg viewBox="0 0 24 24">
                    <path d="M11 7 16 12 11 17" />
                    <path d="M5 12h11" />
                    <path d="M8 5H4v14h4" />
                  </svg>
                </span>
                {loading ? "Ingresando..." : "Ingresar"}
              </button>
            </form>
          </div>

          <div className="login-card__footer">© 2026 Congente - Todos los derechos reservados</div>
        </div>
      </div>
    </div>
  );
}
