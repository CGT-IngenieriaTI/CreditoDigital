import { ReactNode, useEffect, useRef, useState } from "react";

import logo from "../assets/LogoHD.png";
import { AuthUser, StepKey } from "../types/credit";

import { ProgressIndicator } from "./ProgressIndicator";

export function AppShell({
  currentStep,
  user,
  onLogout,
  onToggleHistory,
  onGoHome,
  historyActive,
  children,
}: {
  currentStep: StepKey;
  user: AuthUser;
  onLogout: () => Promise<void>;
  onToggleHistory: () => void;
  onGoHome: () => void;
  historyActive: boolean;
  children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (!menuRef.current) return;
      if (!menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, []);

  const initials = (user.full_name || user.username || "U")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((chunk) => chunk.charAt(0).toUpperCase())
    .join("");

  return (
    <div className="app-shell">
      <div className="app-shell__backdrop app-shell__backdrop--orange" />
      <div className="app-shell__backdrop app-shell__backdrop--blue" />
      <div className="container py-3 py-lg-4 position-relative">
        <header className="topbar-shell mb-3 mb-lg-4">
          <div className="topbar-panel topbar-panel--compact topbar-panel--menu">
            <div className="topbar-panel__brand topbar-panel__brand--clean">
              <img src={logo} alt="Congente" className="brand-logo" />
              <div className="topbar-panel__copy topbar-panel__copy--clean">
                <span className="topbar-panel__eyebrow">Crédito digital de consumo</span>
                <strong>Originación asistida</strong>
                <span className="topbar-panel__caption">Solicitud, validación y decisión en un solo flujo.</span>
              </div>
            </div>

            <div className="topbar-user-menu" ref={menuRef}>
              <span className="topbar-user-menu__status">
                <span className="topbar-user-menu__status-dot" aria-hidden="true" />
                Sesión activa
              </span>
              <button
                type="button"
                className={`topbar-user-menu__trigger ${menuOpen ? "is-open" : ""}`}
                onClick={() => setMenuOpen((current) => !current)}
                aria-expanded={menuOpen}
                aria-haspopup="menu"
              >
                <span className="topbar-user-menu__avatar" aria-hidden="true">{initials}</span>
                <span className="topbar-user-menu__trigger-copy">
                  <strong>{user.full_name}</strong>
                  <span>{user.role}</span>
                </span>
                <span className="topbar-user-menu__chevron" aria-hidden="true">▾</span>
              </button>

              {menuOpen ? (
                <div className="topbar-user-menu__dropdown" role="menu">
                  <div className="topbar-user-menu__dropdown-header">
                    <strong>{user.full_name}</strong>
                    <span>Asesor · Sesión activa</span>
                  </div>
                  <button
                    type="button"
                    className="topbar-user-menu__item"
                    onClick={() => {
                      setMenuOpen(false);
                      onToggleHistory();
                    }}
                  >
                    {historyActive ? "Volver al flujo" : "Ver historial"}
                  </button>
                  <button
                    type="button"
                    className="topbar-user-menu__item"
                    onClick={() => {
                      setMenuOpen(false);
                      onGoHome();
                    }}
                  >
                    Iniciar solicitud
                  </button>
                  <button
                    type="button"
                    className="topbar-user-menu__item topbar-user-menu__item--danger"
                    onClick={() => {
                      setMenuOpen(false);
                      void onLogout();
                    }}
                  >
                    Cerrar sesión
                  </button>
                </div>
              ) : null}
            </div>
          </div>

          <div className="topbar-stepper">
            <ProgressIndicator currentStep={currentStep} />
          </div>
        </header>
        <main className="stage-shell stage-fade">{children}</main>
        <footer className="app-footer">© Congente. Desarrollado por Ingeniería TI</footer>
      </div>
    </div>
  );
}
