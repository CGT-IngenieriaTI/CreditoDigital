import { useEffect, useReducer, useRef, useState, type FormEvent, type ReactNode } from "react";
import { CredihoyShell } from "./components/CredihoyShell";
import { IntegrationState } from "./components/IntegrationState";
import { CredihoyInicioPage } from "./pages/CredihoyInicioPage";
import { CredihoyDatosPage } from "./pages/CredihoyDatosPage";
import { CredihoyFinancieroPage } from "./pages/CredihoyFinancieroPage";
import { CredihoySolicitudPage } from "./pages/CredihoySolicitudPage";
import { CredihoyResumenPage } from "./pages/CredihoyResumenPage";
import { CredihoyEstadoPage } from "./pages/CredihoyEstadoPage";
import { crearEstadoInicial, credihoyReducer, construirEnvio, pasoAnterior, documentosValidos, autorizacionesCompletas } from "./state";
import { normalizarDatos, validarDatos, validarFinanzas, validarSolicitud, condicionesValidas } from "./validation";
import { ESTADOS_SOLICITUD, type DatosSolicitante, type FinanzasDeclaradas,
  type ErroresFormulario, type IntegracionCredihoy, type SeguimientoCredihoy } from "./types";
import { ErrorCredihoy, mensajeErrorCredihoy, type CredihoyPort } from "./services/contracts";
import { claveHistorica } from "./services/historico";
import { type EscenarioShadow, type ShadowPort, type ResultadoShadow } from "./services/shadow";
import { CredihoyShadowResultado } from "./components/CredihoyShadowResultado";
import { HttpConsentimientoReal } from "./services/consentimientoReal";
import "./credihoy.css";

export default function CredihoyFlow({ service, localControls, datosDePrueba, shadowService, escenarioShadow = "POSITIVA" }: {
  service?: CredihoyPort; localControls?: ReactNode; datosDePrueba?: { datos: DatosSolicitante; finanzas: FinanzasDeclaradas };
  shadowService?: ShadowPort; escenarioShadow?: EscenarioShadow;
}) {
  const [resultadoShadow, setResultadoShadow] = useState<ResultadoShadow | null>(null);
  const [state, dispatch] = useReducer(credihoyReducer, undefined, crearEstadoInicial);
  const [integracion, setIntegracion] = useState<IntegracionCredihoy>({ estado: "idle" });
  const [errores, setErrores] = useState<ErroresFormulario>({});
  const [intento, setIntento] = useState(0);
  const [listo, setListo] = useState(false);
  const pendiente = useRef<AbortController | null>(null);
  const idEnvio = useRef<string | null>(null);
  const local = import.meta.env.DEV && service?.modo === "LOCAL";
  const historico = import.meta.env.DEV && shadowService?.tipoFuente === "HISTORICO_CONTROLADO";
  const permitido = service && (service.modo === "API" || local);
  const busy = integracion.estado === "loading";

  function reportarError(error: unknown) {
    const tipo = error instanceof ErrorCredihoy ? error.tipo : "technical_error";
    // No se muestran cuerpos HTTP, errores de infraestructura ni mensajes crudos.
    const campos = error instanceof ErrorCredihoy
      ? Object.fromEntries(Object.keys(error.campos).filter(key => ["identificacion", "tipoIdentificacion", "nombres", "apellidos", "celular", "correo",
        "ingresos", "otrosIngresos", "gastos", "monto", "plazoMeses", "periodicidadPago", "obligacionRecogidaReferencia", "consentimientos"].includes(key))
        .map(key => [key, "Revisa este campo antes de continuar."])) : {};
    setErrores(campos); setIntegracion({ estado: tipo, errores: campos, mensaje: mensajeErrorCredihoy(error) });
  }
  useEffect(() => {
    if (!permitido || !service) return;
    const controller = new AbortController();
    setListo(false); setIntegracion({ estado: "loading" });
    async function cargar() {
      try {
        const contexto = await service!.cargarContextoSolicitud(controller.signal);
        const condicionesProducto = await service!.cargarCondicionesProducto(controller.signal);
        if (controller.signal.aborted) return;
        if (!documentosValidos(contexto) || !condicionesValidas(condicionesProducto, local)) throw new ErrorCredihoy("technical_error");
        dispatch({ type: "CONTEXTO", contexto, condicionesProducto, historico }); setListo(true);
        if (historico && shadowService?.recuperarResultado) {
          idEnvio.current ??= claveHistorica(window.location.hash);
          if (idEnvio.current) {
            const resultado = await shadowService.recuperarResultado(idEnvio.current, controller.signal);
            if (controller.signal.aborted) return;
            setResultadoShadow(resultado);
          }
        }
        setIntegracion({ estado: "success" });
      } catch (error) { if (!controller.signal.aborted) reportarError(error); }
    }
    void cargar();
    return () => controller.abort();
  }, [service, intento, permitido, historico, shadowService]);
  useEffect(() => () => pendiente.current?.abort(), []);
  useEffect(() => {
    if (!["validation_error", "technical_error", "retryable_error"].includes(integracion.estado)) return;
    const feedback = document.getElementById("ch-operation-feedback");
    feedback?.scrollIntoView({ block: "center", behavior: "instant" });
    feedback?.focus({ preventScroll: true });
  }, [integracion]);
  useEffect(() => {
    document.title = "CrediHoy | Crédito Digital Congente";
    document.querySelector<HTMLElement>(".ch-main h1")?.focus();
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [state.paso, listo, resultadoShadow]);
  useEffect(() => {
    const hayDatos = !resultadoShadow && state.paso !== "inicio" && state.paso !== "estado" && Boolean(state.datos.identificacion || state.solicitud.monto);
    if (!hayDatos) return;
    const advertir = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", advertir);
    return () => window.removeEventListener("beforeunload", advertir);
  }, [state.paso, state.datos.identificacion, state.solicitud.monto, resultadoShadow]);

  function validar(next: ErroresFormulario): boolean {
    setErrores(next);
    const primero = Object.keys(next)[0];
    setIntegracion({ estado: primero ? "validation_error" : "idle" });
    if (primero) requestAnimationFrame(() => document.getElementById(`ch-${primero}`)?.focus());
    return !primero;
  }
  function limpiarErrores(keys: string[]) {
    if (!historico) idEnvio.current = null;
    setIntegracion({ estado: "idle" });
    setErrores(prev => Object.fromEntries(Object.entries(prev).filter(([key]) => !keys.includes(key))));
  }
  function verificarSeguimiento(s: SeguimientoCredihoy) {
    if (!ESTADOS_SOLICITUD.includes(s.estado) || (!local && s.sintetico)) throw new ErrorCredihoy("technical_error");
  }
  async function continuar(event?: FormEvent) {
    event?.preventDefault();
    if (!permitido || !service || !listo || pendiente.current) return;
    const controller = new AbortController(); pendiente.current = controller;
    try {
      if (state.paso === "datos") {
        const datos = normalizarDatos(state.datos);
        if (!validar(validarDatos(datos, historico))) return;
        dispatch({ type: "DATOS", datos }); setIntegracion({ estado: "loading" });
        const consulta = await service.consultarCredihoyVigente(datos, controller.signal);
        if (controller.signal.aborted) return;
        if (!["SIN_CREDIHOY", "CREDIHOY_ELEGIBLE", "CREDIHOY_NO_ELEGIBLE", "PENDIENTE_CONSULTA"].includes(consulta.estado)) throw new ErrorCredihoy("technical_error");
        dispatch({ type: "OBLIGACIONES", consulta }); dispatch({ type: "PASO", paso: "financiero" });
        setIntegracion({ estado: consulta.estado === "SIN_CREDIHOY" ? "empty" : "success" });
      } else if (state.paso === "financiero") {
        if (validar(validarFinanzas(state.finanzas, historico))) { dispatch({ type: "PASO", paso: "solicitud" }); setIntegracion({ estado: "success" }); }
      } else if (state.paso === "solicitud") {
        if (!validar(validarSolicitud(state.solicitud, state.condicionesProducto, state.consultaObligaciones))) return;
        setIntegracion({ estado: "loading" });
        const condiciones = await service.previsualizarSolicitud(construirEnvio(state, false), controller.signal);
        if (controller.signal.aborted) return;
        dispatch({ type: "CONDICIONES", condiciones }); dispatch({ type: "PASO", paso: "resumen" }); setIntegracion({ estado: "success" });
      } else if (state.paso === "resumen") {
        if (!validar({ ...validarDatos(state.datos, historico), ...validarFinanzas(state.finanzas, historico),
          ...validarSolicitud(state.solicitud, state.condicionesProducto, state.consultaObligaciones),
          ...(!autorizacionesCompletas(state) ? { consentimientos: "Acepta las autorizaciones obligatorias." } : {}) })) return;
        setIntegracion({ estado: "loading" });
        idEnvio.current ??= crypto.randomUUID();
        if (historico) window.history.replaceState(null, "", "#shadow=" + idEnvio.current);
        if (shadowService && (local || historico)) {
          const resultado = await shadowService.evaluar(state.solicitud, escenarioShadow, controller.signal,
            historico ? construirEnvio(state) : undefined, historico ? idEnvio.current : undefined);
          if (!controller.signal.aborted) { setResultadoShadow(resultado); setIntegracion({ estado: "success" }); }
          return;
        }
        idEnvio.current ??= crypto.randomUUID();
        const seguimiento = await service.enviarSolicitud(construirEnvio(state), idEnvio.current, controller.signal);
        if (controller.signal.aborted) return;
        verificarSeguimiento(seguimiento); dispatch({ type: "ENVIADA", seguimiento }); setIntegracion({ estado: "success" });
      }
    } catch (error) { if (!controller.signal.aborted) reportarError(error); }
    finally { pendiente.current = null; }
  }
  async function actualizarEstado() {
    if (!service || !state.seguimiento?.referencia || pendiente.current) return;
    const controller = new AbortController(); pendiente.current = controller; setIntegracion({ estado: "loading" });
    try {
      const seguimiento = await service.consultarEstado(state.seguimiento.referencia, controller.signal);
      if (!controller.signal.aborted) { verificarSeguimiento(seguimiento); dispatch({ type: "SEGUIMIENTO", seguimiento }); setIntegracion({ estado: "success" }); }
    } catch (error) { if (!controller.signal.aborted) reportarError(error); }
    finally { pendiente.current = null; }
  }
  async function prepararFirmaReal() {
    if (!resultadoShadow?.solicitudId || pendiente.current) return;
    pendiente.current = new AbortController(); setIntegracion({ estado: "loading" });
    try {
      const origen = resultadoShadow.solicitudId;
      const url = new URL(window.location.href);
      const clave = url.searchParams.get("clave-firma") ?? crypto.randomUUID();
      url.searchParams.set("clave-firma", clave);
      window.history.replaceState(null, "", url);
      const id = await new HttpConsentimientoReal().crear(origen, clave);
      window.location.assign("/credihoy?firma-real=" + id);
    } catch { setIntegracion({ estado: "technical_error", mensaje: "No se confirmo la preparacion de firma real. La clave se conserva; no se consultaron centrales." }); }
    finally { pendiente.current = null; }
  }
  const atras = () => { setErrores({}); setIntegracion({ estado: "idle" }); dispatch({ type: "PASO", paso: pasoAnterior(state.paso) }); };
  const reset = () => { idEnvio.current = null; setErrores({}); setIntegracion({ estado: "success" }); dispatch({ type: "REINICIAR" }); };
  const retry = !listo ? () => setIntento(i => i + 1) : state.paso === "estado" ? actualizarEstado : () => void continuar();
  const common = { onContinue: continuar, onBack: atras, busy, errores };
  return <CredihoyShell state={state} local={local} resultadoShadow={Boolean(resultadoShadow)}>
    {historico && !resultadoShadow && <p className="ch-note">HISTORICO CONTROLADO. Sin consultas nuevas ni radicación. Contacto e información declarada pueden permanecer ausentes; no sustituyen hechos financieros certificados.</p>}
    {local && <details className="ch-dev-tools"><summary>Herramientas de desarrollo local</summary><div className="ch-dev-tool-controls">{localControls}
      {datosDePrueba && <button type="button" className="btn btn-outline-secondary" disabled={!listo || busy || state.paso === "estado"} onClick={() => {
        limpiarErrores(Object.keys(state.datos));
        dispatch({ type: "DATOS", datos: datosDePrueba.datos }); dispatch({ type: "FINANZAS", finanzas: datosDePrueba.finanzas });
      }}>Cargar datos de prueba</button>}</div></details>}
    {!permitido ? <div className="ch-note" role="status"><h2>Solicitud no disponible</h2><p>Este canal todavía no está habilitado. No se ha enviado ninguna información.</p></div>
      : <>
        {state.paso !== "resumen" && <IntegrationState integracion={integracion} onRetry={retry} />}
        {resultadoShadow && <CredihoyShadowResultado resultado={resultadoShadow} onBack={state.paso === "resumen" ? () => setResultadoShadow(null) : undefined} />}
        {historico && resultadoShadow?.solicitudId && <button type="button" className="btn btn-outline-primary mt-3" disabled={busy} onClick={() => void prepararFirmaReal()}>Firmar consultas reales</button>}
        {historico && resultadoShadow && <IntegrationState integracion={integracion} />}
        {listo && !resultadoShadow && <fieldset disabled={busy} className="ch-page-fields" aria-busy={busy}>
          {state.paso === "inicio" && <CredihoyInicioPage busy={busy} onStart={() => dispatch({ type: "PASO", paso: "datos" })} />}
          {state.paso === "datos" && <CredihoyDatosPage {...common} datos={state.datos} onChange={datos => { limpiarErrores(Object.keys(datos)); dispatch({ type: "DATOS", datos }); }} />}
          {state.paso === "financiero" && <CredihoyFinancieroPage {...common} historico={historico} finanzas={state.finanzas} onChange={finanzas => { limpiarErrores(Object.keys(finanzas)); dispatch({ type: "FINANZAS", finanzas }); }} />}
          {state.paso === "solicitud" && state.condicionesProducto && <CredihoySolicitudPage {...common} solicitud={state.solicitud} parametros={state.condicionesProducto}
            consulta={state.consultaObligaciones} onChange={solicitud => { limpiarErrores([...Object.keys(solicitud), ...(solicitud.recogerCredihoy === false ? ["obligacionRecogidaReferencia"] : [])]); dispatch({ type: "SOLICITUD", solicitud }); }} />}
          {state.paso === "resumen" && <CredihoyResumenPage {...common} state={state} integracion={integracion} onRetry={retry} shadow={Boolean(shadowService && (local || historico))} historico={historico} onConsent={(id, aceptado) => {
            if (!historico) idEnvio.current = null; setErrores({}); setIntegracion({ estado: "idle" }); dispatch({ type: "CONSENTIMIENTO", id, aceptado });
          }} />}
          {state.paso === "estado" && state.seguimiento && <CredihoyEstadoPage seguimiento={state.seguimiento} solicitud={state.solicitud}
            local={local} busy={busy} onUpdate={actualizarEstado} onReset={reset} onResume={() => {
              setErrores({}); setIntegracion({ estado: "idle" }); dispatch({ type: "RETOMAR" });
            }} />}
        </fieldset>}
      </>}
  </CredihoyShell>;
}
