import { useMemo, useState } from "react";
import CredihoyFlow from "./CredihoyFlow";
import { crearServicioLocal, DATOS_SINTETICOS, FINANZAS_SINTETICAS, type OpcionesLocal } from "./services/local";
import { ESCENARIOS_SHADOW, HttpShadowLaboratorio, laboratorioLocal, type EscenarioShadow } from "./services/shadow";
import { HttpHistoricoCredihoy } from "./services/historico";
import { solicitudFirmaReal } from "./services/consentimientoReal";
import { CredihoyFirmaReal } from "./components/CredihoyFirmaReal";

export default function CredihoyEntry() {
  const [opciones, setOpciones] = useState<OpcionesLocal>({ escenario: "CREDIHOY_ELEGIBLE", fallo: "NINGUNO" });
  const [escenarioShadow, setEscenarioShadow] = useState<EscenarioShadow>("POSITIVA");
  const [sesion, setSesion] = useState(false);
  const [sesionBusy, setSesionBusy] = useState(false);
  const [sesionError, setSesionError] = useState(false);
  const local = import.meta.env.DEV && ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  const lab = laboratorioLocal(import.meta.env.DEV, import.meta.env.VITE_CREDIHOY_SHADOW_LAB_ENABLED, window.location.hostname);
  const historico = lab && import.meta.env.VITE_CREDIHOY_HISTORICO_ENABLED === "1";
  const shadow = useMemo(() => lab ? new (historico ? HttpHistoricoCredihoy : HttpShadowLaboratorio)({ dev: import.meta.env.DEV,
    flag: import.meta.env.VITE_CREDIHOY_SHADOW_LAB_ENABLED, hostname: window.location.hostname }) : undefined, [lab, historico]);
  const service = useMemo(() => historico && shadow instanceof HttpHistoricoCredihoy ? shadow : local ? crearServicioLocal(opciones) : undefined, [local, opciones, shadow, historico]);
  const firmaRealId = solicitudFirmaReal(window.location.search)
    ?? solicitudFirmaReal("?firma-real=" + (import.meta.env.VITE_CREDIHOY_FIRMA_SOLICITUD_ID ?? ""));
  if (firmaRealId) return <CredihoyFirmaReal solicitudId={firmaRealId} />;
  const controls = local && <>
    {lab && <label htmlFor="ch-shadow-escenario">Caso sintético backend
      <select id="ch-shadow-escenario" className="form-select" value={escenarioShadow}
        onChange={e => setEscenarioShadow(e.target.value as EscenarioShadow)}>
        {ESCENARIOS_SHADOW.map(e => <option key={e} value={e}>{e}</option>)}
      </select>
    </label>}
    <label htmlFor="ch-local-escenario">Escenario local
      <select id="ch-local-escenario" className="form-select" value={opciones.escenario} onChange={e => setOpciones({ ...opciones, escenario: e.target.value as OpcionesLocal["escenario"] })}>
        <option value="SIN_CREDIHOY">Sin CrediHoy</option><option value="CREDIHOY_ELEGIBLE">CrediHoy disponible</option>
        <option value="CREDIHOY_NO_ELEGIBLE">CrediHoy no disponible para incluir</option><option value="PENDIENTE_CONSULTA">Consulta pendiente</option>
      </select>
    </label>
    <label htmlFor="ch-local-fallo">Prueba de integración
      <select id="ch-local-fallo" className="form-select" value={opciones.fallo} onChange={e => setOpciones({ ...opciones, fallo: e.target.value as OpcionesLocal["fallo"] })}>
        <option value="NINGUNO">Sin errores</option><option value="CONTEXTO">Error al cargar</option>
        <option value="CONSULTA">Error al consultar</option><option value="PREVISUALIZACION">Error al previsualizar</option>
        <option value="ENVIO">Error al enviar</option><option value="ESTADO">Error al consultar estado</option>
      </select>
    </label>
  </>;
  if (lab && !sesion) return <main className="container py-5" style={{ maxWidth: 560 }}>
    <h1>CrediHoy Único</h1><p>{historico ? "Reproducción histórica autorizada. Sin nuevas consultas ni decisión crediticia." : "Laboratorio shadow con hechos sintéticos. Sin decisión crediticia."}</p>
    <form onSubmit={async event => {
      event.preventDefault(); if (sesionBusy || !shadow) return;
      const data = new FormData(event.currentTarget); setSesionBusy(true); setSesionError(false);
      try { await shadow.iniciarSesion(String(data.get("username")), String(data.get("password"))); setSesion(true); }
      catch { setSesionError(true); } finally { setSesionBusy(false); }
    }}>
      <label className="d-block mb-3">Usuario del laboratorio<input name="username" className="form-control" autoComplete="username" required /></label>
      <label className="d-block mb-3">Clave del laboratorio<input name="password" type="password" className="form-control" autoComplete="current-password" required /></label>
      {sesionError && <p role="alert">No fue posible iniciar la sesión local.</p>}
      <button type="submit" className="btn btn-primary" disabled={sesionBusy}>Iniciar sesión local</button>
    </form>
  </main>;
  return <CredihoyFlow key={JSON.stringify([opciones, escenarioShadow])} service={service} localControls={controls}
    shadowService={shadow} escenarioShadow={escenarioShadow}
    datosDePrueba={local && !historico ? { datos: DATOS_SINTETICOS, finanzas: FINANZAS_SINTETICAS } : undefined} />;
}
