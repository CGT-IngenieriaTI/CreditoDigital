import { ErrorCredihoy } from "./contracts";
import { PRODUCTO_CREDIHOY, type SolicitudCredihoy, type ValorFinanciero, type EnvioSolicitudCredihoy } from "../types";

export const ESCENARIOS_SHADOW = ["POSITIVA", "CERO", "NEGATIVA", "SIN_INFORMACION", "RECOGIDA_PERMITIDA",
  "RECOGIDA_NO_PERMITIDA", "RECOGIDA_SIN_CORRESPONDENCIA", "ERROR_TECNICO"] as const;
export type EscenarioShadow = typeof ESCENARIOS_SHADOW[number];
export interface CorroboracionSalarialPiloto {
  clasificacion: "INGRESO_SALARIAL_MENSUAL_CORROBORADO_PILOTO"; alcance: "EXPEDIENTE_PILOTO_UNICO";
  fechaConfirmacion: string; precisionFecha: "DIA"; utilizableComoComponenteSalarial: true;
  declaraIngresoMensualTotal: false; autorizaDecisionCrediticia: false; autorizaRadicacionLinix: false;
}
export interface SueldoRegistradoHistorico {
  disponible: boolean; estadoCertificacion: "OBSERVADO" | "NO_DISPONIBLE";
  rutaOrigen: "LINIX.AP014MCLIENTE.V_SUELDO"; fechaConsulta: string; zonaHoraria: "America/Bogota";
  utilizableComoIngresoMensual: false;
  motivo: "HOMOLOGACION_INGRESO_MENSUAL_PENDIENTE" | "COMPONENTE_SALARIAL_NO_EQUIVALE_INGRESO_TOTAL";
  corroboracionPiloto?: CorroboracionSalarialPiloto;
}
export interface ResumenLinixHistorico {
  fuente: "LINIX_SELECT_MANUAL_S01_S05"; fechaConsulta: string | null; periodo: string;
  obligacionesAlCierre: number; documentos203: number; filasInventario: number; obligacionesObservadas: number;
  credihoy111Observado: boolean; saldoAportesUtilizable: false; vigenciaActualCertificada: false; advertencias: string[];
  sueldoRegistrado?: SueldoRegistradoHistorico;
}
export interface PreparacionRadicacion {
  estado: "BLOQUEADA"; contratoObjetivo: "credihoy-radicacion-controlada-v1";
  producto: { codigo: "390"; modalidad: "111"; destino: "CFA"; certificacionId: "CREDIHOY_390_111_CFA_CD4D" };
  camposPendientes: string[]; requestRadicacion: null; autorizadoEnvioOracle: false;
  decisionCrediticia: "NO_AUTORIZADA"; radicacionLinix: "NO_EJECUTADA";
}
export const CODIGOS_REGLAS_SHADOW = [2, 4, 6, 9, 10, 12, 14, 17, 21, 23, 24, 27, 28, 30, 31, 32]
  .map(fila => `UNICO_FILA_${fila}`);
export type EstadoEvaluacionRegla = "CUMPLE" | "NO_CUMPLE" | "NO_EVALUABLE" | "PENDIENTE_POLITICA" | "NO_APLICA";
const CAMPOS_REGLAS = ["capacidad_pago", "consulta_central_realizada", "cuentas_embargadas", "edad_anios",
  "calificacion_interna", "persona_natural", "monto_solicitado", "smmlv_vigente", "saldo_aportes_utilizable",
  "periodicidad_pago", "plazo_meses", "fecha_actualizacion_datos"];
const MOTIVOS_POLITICA = ["UMBRAL_Y_EFECTO_CAPACIDAD_PENDIENTES_APROBACION",
  "CORTE_ACTUALIZACION_Y_SEMANTICA_MES_PENDIENTES",
  "PROMEDIO_ULTIMO_CREDITO_Y_EXCEPCION_RENOVACION_PENDIENTES",
  "EFECTO_SALDO_PENDIENTE_NO_ESTABLECIDO_EN_POLITICA", "DEFINICION_FUNCIONAL_PENDIENTE"];
export interface ResultadoShadow {
  revisionHistorica?: RevisionHistorica;
  versionContrato: "credihoy-shadow-lab-v1"; requestId: string; fechaEvaluacion: string; fuenteHechos: "SINTETICO_CD3C" | "HISTORICO_CONTROLADO" | "EXPEDIENTE_OPERATIVO_SIN_DECISION" | "CONSULTA_REAL_CONTROLADA";
  solicitudId?: string; estadoSolicitud?: "EVALUADO_SHADOW"; modo?: "HISTORICO_CONTROLADO";
  preparacionRadicacion?: PreparacionRadicacion;
  evidenciaHistorica?: { PRESELECTA: { fechaOriginal: string; estado: string; versionOrigen: string; versionAdaptada: string; camposDisponibles: { decision: boolean; riesgo_score: boolean; score: boolean } };
    HDC: { fechaOriginal: string; estado: string; versionOrigen: string; versionAdaptada: string; cuentas: number; posiblesDuplicados: number; agregadoOficialDisponible: boolean; coberturaCertificada: false };
    LINIX?: ResumenLinixHistorico; vigenciaParaDecision: "NO_CERTIFICADA"; advertencias: string[] };
  capacidadMatematica: ValorFinanciero & { estado: string }; cuotaParaCapacidad: ValorFinanciero;
  cuotaContractual: { disponible: false; valor: null }; gastosFamiliares: ValorFinanciero;
  informacionInsuficiente: string[]; reglasPendientes: string[];
  reglas: { codigo: string; nombre: string; resultado: "CUMPLE" | "NO_CUMPLE" | "NO_EVALUABLE";
    efecto: "BLOQUEA" | "GENERA_NOVEDAD" | "INFORMATIVO" | "PENDIENTE_DEFINICION"; motivos: string[];
    estadoEvaluacion?: EstadoEvaluacionRegla; datosFaltantes?: string[] }[];
  recogida: { estado: string; motivos: string[]; observacionAplicacion: string | null };
  decisionCrediticia: "NO_AUTORIZADA"; conclusionFuncional: "NO_DETERMINADA";
  autorizadoDecisionCrediticia: false; radicacionLinix: "NO_EJECUTADA";
}
export interface RevisionHistorica {
  noPersistida: true; version: "REPLAY_XML_9FB_1"; huellaFuentes: string;
  fechaHdcOriginal: string; fechaSalarioObservado: string | null;
  ingresoObservado: ValorFinanciero; cuotaHdcBruta: ValorFinanciero; cuotaHdcAplicable: { disponible: false; valor: null };
  estadoConsistencia: string; bloqueosAplicabilidad: string[];
  escenarios: { codigo: "CDC_INCLUIDO" | "CDC_EXCLUIDO"; naturaleza: "CONDICIONADO_NO_DECISORIO";
    condiciones: string[]; ingresoUtilizado: ValorFinanciero; cuotaHdcAplicableHipotetica: ValorFinanciero;
    gastosFamiliares: ValorFinanciero; cuotaNuevaParaCapacidad: ValorFinanciero; capacidadMatematica: ValorFinanciero;
    autorizadoDecisionCrediticia: false }[];
}
export interface ShadowPort {
  tipoFuente?: "HISTORICO_CONTROLADO";
  evaluar(solicitud: SolicitudCredihoy, escenario: EscenarioShadow, signal: AbortSignal, envio?: EnvioSolicitudCredihoy, clave?: string): Promise<ResultadoShadow>;
  recuperarResultado?(clave: string, signal: AbortSignal): Promise<ResultadoShadow>;
}
const PATHS = { csrf: "/api/v1/csrf/", login: "/api/v1/auth/login/", evaluar: "/api/v1/credihoy/unico/shadow/evaluar/" } as const;
const fallo = () => new ErrorCredihoy("technical_error");
export function laboratorioLocal(dev: boolean, flag: unknown, hostname: string): boolean {
  return dev && flag === "1" && ["localhost", "127.0.0.1", "[::1]"].includes(hostname);
}
export function requestShadow(s: SolicitudCredihoy, escenario: EscenarioShadow) {
  if (!ESCENARIOS_SHADOW.includes(escenario) || !/^[0-9]{1,18}(?:\.[0-9]{1,2})?$/.test(s.monto)
      || !/^[0-9]{1,3}$/.test(s.plazoMeses) || s.periodicidadPago !== "MENSUAL"
      || typeof s.recogerCredihoy !== "boolean"
      || s.obligacionRecogidaReferencia !== (s.recogerCredihoy ? "LOCAL-OBLIGACION-FICTICIA" : null)) throw fallo();
  return { producto: PRODUCTO_CREDIHOY, escenario, monto: s.monto, plazoMeses: Number(s.plazoMeses),
    periodicidadPago: s.periodicidadPago, recogida: { aplica: s.recogerCredihoy, referencia: s.obligacionRecogidaReferencia } };
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw fallo();
  return value as Record<string, unknown>;
}
function text(value: unknown): value is string { return typeof value === "string" && value.length > 0 && value.length <= 500; }
function strings(value: unknown): value is string[] { return Array.isArray(value) && value.length <= 100 && value.every(text); }
function importe(value: unknown) {
  const v = object(value);
  if (typeof v.disponible !== "boolean" || (v.disponible
    ? typeof v.valor !== "string" || !/^-?[0-9]{1,30}(?:\.[0-9]{1,12})?$/.test(v.valor)
    : v.valor !== null)) throw fallo();
}
export function validarResultadoShadow(value: unknown, requestId: string, fuenteEsperada: ResultadoShadow["fuenteHechos"] = "SINTETICO_CD3C"): ResultadoShadow {
  const p = object(value);
  const revision = p.revisionHistorica === undefined ? undefined : validarRevisionHistorica(p.revisionHistorica);
  if (revision && fuenteEsperada !== "HISTORICO_CONTROLADO") throw fallo();
  if (p.versionContrato !== "credihoy-shadow-lab-v1" || p.requestId !== requestId || p.fuenteHechos !== fuenteEsperada
      || p.decisionCrediticia !== "NO_AUTORIZADA" || p.conclusionFuncional !== "NO_DETERMINADA"
      || p.autorizadoDecisionCrediticia !== false || p.radicacionLinix !== "NO_EJECUTADA"
      || typeof p.fechaEvaluacion !== "string" || !/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(p.fechaEvaluacion)
      || !Number.isFinite(Date.parse(p.fechaEvaluacion))) throw fallo();
  for (const key of ["capacidadMatematica", "cuotaParaCapacidad", "cuotaContractual", "gastosFamiliares"]) importe(p[key]);
  if (object(p.cuotaContractual).disponible !== false
      || !["EVALUABLE", "NO_EVALUABLE", "INFORMACION_INCONSISTENTE"].includes(String(object(p.capacidadMatematica).estado))
      || (object(p.capacidadMatematica).estado === "EVALUABLE") !== object(p.capacidadMatematica).disponible
      || !strings(p.informacionInsuficiente) || !strings(p.reglasPendientes)) throw fallo();
  if (!Array.isArray(p.reglas) || p.reglas.length !== 16) throw fallo();
  const codigos = new Set<string>();
  for (const raw of p.reglas) {
    const r = object(raw);
    if (!text(r.codigo) || !CODIGOS_REGLAS_SHADOW.includes(r.codigo) || codigos.has(r.codigo)
        || !text(r.nombre) || !["CUMPLE", "NO_CUMPLE", "NO_EVALUABLE"].includes(String(r.resultado))
        || !["BLOQUEA", "GENERA_NOVEDAD", "INFORMATIVO", "PENDIENTE_DEFINICION"].includes(String(r.efecto))
        || !strings(r.motivos)) throw fallo();
    codigos.add(r.codigo);
    if (r.estadoEvaluacion !== undefined || r.datosFaltantes !== undefined) {
      if (!["CUMPLE", "NO_CUMPLE", "NO_EVALUABLE", "PENDIENTE_POLITICA", "NO_APLICA"].includes(String(r.estadoEvaluacion))
          || !strings(r.datosFaltantes) || r.datosFaltantes.some(k => !CAMPOS_REGLAS.includes(k))
          || (r.resultado !== "NO_EVALUABLE" && r.estadoEvaluacion !== r.resultado)
          || (r.resultado === "NO_EVALUABLE" && ["CUMPLE", "NO_CUMPLE"].includes(String(r.estadoEvaluacion)))
          || (r.estadoEvaluacion !== "NO_EVALUABLE" && r.datosFaltantes.length !== 0)
          || (r.estadoEvaluacion === "PENDIENTE_POLITICA" && !r.motivos.some(k => MOTIVOS_POLITICA.includes(k)))
          || (r.estadoEvaluacion === "NO_APLICA" && (r.efecto !== "INFORMATIVO"
            || !((r.codigo === "UNICO_FILA_30" && r.motivos.includes("REGLA_INACTIVA_NO_APLICADA"))
              || (r.codigo === "UNICO_FILA_31" && r.motivos.includes("SOLO_RENOVACION_GER_PO_068_6_2_3_7")))))) throw fallo();
    }
  }
  const recogida = object(p.recogida);
  if (!["NO_APLICA", "PENDIENTE_VALIDACION", "VALIDA", "NO_VALIDA"].includes(String(recogida.estado)) || !strings(recogida.motivos)
      || (recogida.observacionAplicacion !== null && !text(recogida.observacionAplicacion))) throw fallo();
  // Only contract fields reach the UI, never an unexpected HTTP envelope.
  const money = (value: unknown) => { const v = object(value); return { disponible: v.disponible, valor: v.valor }; };
  let historico: Partial<ResultadoShadow> = {};
  if (fuenteEsperada === "HISTORICO_CONTROLADO") {
    const e = object(p.evidenciaHistorica), pre = object(e.PRESELECTA), hdc = object(e.HDC), campos = object(pre.camposDisponibles);
    if (p.modo !== "HISTORICO_CONTROLADO" || typeof p.solicitudId !== "string" || !/^[a-f0-9-]{36}$/.test(p.solicitudId)
        || p.estadoSolicitud !== "EVALUADO_SHADOW" || e.vigenciaParaDecision !== "NO_CERTIFICADA" || !strings(e.advertencias)
        || ![pre, hdc].every(o => text(o.fechaOriginal) && text(o.versionOrigen) && text(o.versionAdaptada) && ["COMPLETADO", "SIN_INFORMACION"].includes(String(o.estado)))
        || !["decision", "riesgo_score", "score"].every(k => typeof campos[k] === "boolean")
        || ![hdc.cuentas, hdc.posiblesDuplicados].every(n => Number.isSafeInteger(n) && Number(n) >= 0)
        || typeof hdc.agregadoOficialDisponible !== "boolean" || (!revision && hdc.agregadoOficialDisponible !== false)
        || hdc.coberturaCertificada !== false) throw fallo();
    historico = { modo: "HISTORICO_CONTROLADO", solicitudId: p.solicitudId, estadoSolicitud: "EVALUADO_SHADOW",
      evidenciaHistorica: { PRESELECTA: { fechaOriginal: pre.fechaOriginal, estado: pre.estado, versionOrigen: pre.versionOrigen,
        versionAdaptada: pre.versionAdaptada, camposDisponibles: { decision: campos.decision, riesgo_score: campos.riesgo_score, score: campos.score } },
        HDC: { fechaOriginal: hdc.fechaOriginal, estado: hdc.estado, versionOrigen: hdc.versionOrigen, versionAdaptada: hdc.versionAdaptada,
          cuentas: hdc.cuentas, posiblesDuplicados: hdc.posiblesDuplicados, agregadoOficialDisponible: hdc.agregadoOficialDisponible, coberturaCertificada: false },
        vigenciaParaDecision: "NO_CERTIFICADA", advertencias: e.advertencias } } as Partial<ResultadoShadow>;
    if (e.LINIX !== undefined) {
      const l = object(e.LINIX), prep = object(p.preparacionRadicacion), producto = object(prep.producto);
      if (l.fuente !== "LINIX_SELECT_MANUAL_S01_S05" || typeof l.periodo !== "string" || !/^[0-9]{6}$/.test(l.periodo)
          || (l.fechaConsulta !== null && (typeof l.fechaConsulta !== "string"
            || !/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(l.fechaConsulta) || !Number.isFinite(Date.parse(l.fechaConsulta))))
          || ![l.obligacionesAlCierre, l.documentos203, l.filasInventario, l.obligacionesObservadas].every(n => Number.isSafeInteger(n) && Number(n) >= 0)
          || typeof l.credihoy111Observado !== "boolean" || l.saldoAportesUtilizable !== false || l.vigenciaActualCertificada !== false
          || !strings(l.advertencias) || prep.estado !== "BLOQUEADA" || prep.contratoObjetivo !== "credihoy-radicacion-controlada-v1"
          || producto.codigo !== "390" || producto.modalidad !== "111" || producto.destino !== "CFA"
          || producto.certificacionId !== "CREDIHOY_390_111_CFA_CD4D" || !strings(prep.camposPendientes)
          || prep.camposPendientes.some(k => !/^[A-Za-z][A-Za-z.]+$/.test(k)) || prep.requestRadicacion !== null
          || prep.autorizadoEnvioOracle !== false || prep.decisionCrediticia !== "NO_AUTORIZADA" || prep.radicacionLinix !== "NO_EJECUTADA") throw fallo();
      historico.evidenciaHistorica!.LINIX = { fuente: l.fuente, fechaConsulta: l.fechaConsulta, periodo: l.periodo,
        obligacionesAlCierre: l.obligacionesAlCierre, documentos203: l.documentos203, filasInventario: l.filasInventario,
        obligacionesObservadas: l.obligacionesObservadas, credihoy111Observado: l.credihoy111Observado,
        saldoAportesUtilizable: false, vigenciaActualCertificada: false, advertencias: l.advertencias } as ResumenLinixHistorico;
      if (l.sueldoRegistrado !== undefined) {
        const s = object(l.sueldoRegistrado);
        const corroborado = s.corroboracionPiloto !== undefined;
        if (typeof s.disponible !== "boolean" || s.estadoCertificacion !== (s.disponible ? "OBSERVADO" : "NO_DISPONIBLE")
            || s.rutaOrigen !== "LINIX.AP014MCLIENTE.V_SUELDO" || s.zonaHoraria !== "America/Bogota"
            || typeof s.fechaConsulta !== "string" || !/^\d{4}-\d{2}-\d{2}T.+-05:00$/.test(s.fechaConsulta)
            || !Number.isFinite(Date.parse(s.fechaConsulta)) || s.utilizableComoIngresoMensual !== false
            || s.motivo !== (corroborado ? "COMPONENTE_SALARIAL_NO_EQUIVALE_INGRESO_TOTAL" : "HOMOLOGACION_INGRESO_MENSUAL_PENDIENTE")) throw fallo();
        historico.evidenciaHistorica!.LINIX.sueldoRegistrado = { disponible: s.disponible,
          estadoCertificacion: s.estadoCertificacion, rutaOrigen: s.rutaOrigen, fechaConsulta: s.fechaConsulta,
          zonaHoraria: s.zonaHoraria, utilizableComoIngresoMensual: false,
          motivo: s.motivo } as SueldoRegistradoHistorico;
        if (corroborado) {
          const c = object(s.corroboracionPiloto);
          if (!s.disponible || c.clasificacion !== "INGRESO_SALARIAL_MENSUAL_CORROBORADO_PILOTO"
              || c.alcance !== "EXPEDIENTE_PILOTO_UNICO" || c.precisionFecha !== "DIA"
              || typeof c.fechaConfirmacion !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(c.fechaConfirmacion)
              || !Number.isFinite(Date.parse(c.fechaConfirmacion))
              || new Date(c.fechaConfirmacion).toISOString().slice(0, 10) !== c.fechaConfirmacion
              || c.fechaConfirmacion < s.fechaConsulta.slice(0, 10) || c.utilizableComoComponenteSalarial !== true
              || c.declaraIngresoMensualTotal !== false || c.autorizaDecisionCrediticia !== false
              || c.autorizaRadicacionLinix !== false) throw fallo();
          historico.evidenciaHistorica!.LINIX.sueldoRegistrado!.corroboracionPiloto = {
            clasificacion: c.clasificacion, alcance: c.alcance, fechaConfirmacion: c.fechaConfirmacion,
            precisionFecha: "DIA", utilizableComoComponenteSalarial: true, declaraIngresoMensualTotal: false,
            autorizaDecisionCrediticia: false, autorizaRadicacionLinix: false } as CorroboracionSalarialPiloto;
        }
      }
      historico.preparacionRadicacion = { estado: "BLOQUEADA", contratoObjetivo: "credihoy-radicacion-controlada-v1",
        producto: { codigo: "390", modalidad: "111", destino: "CFA", certificacionId: "CREDIHOY_390_111_CFA_CD4D" },
        camposPendientes: prep.camposPendientes, requestRadicacion: null, autorizadoEnvioOracle: false,
        decisionCrediticia: "NO_AUTORIZADA", radicacionLinix: "NO_EJECUTADA" } as PreparacionRadicacion;
    }
  }
  return { versionContrato: p.versionContrato, requestId, fechaEvaluacion: p.fechaEvaluacion,
    fuenteHechos: p.fuenteHechos, capacidadMatematica: { ...money(p.capacidadMatematica), estado: object(p.capacidadMatematica).estado }, cuotaParaCapacidad: money(p.cuotaParaCapacidad),
    cuotaContractual: money(p.cuotaContractual), gastosFamiliares: money(p.gastosFamiliares),
    informacionInsuficiente: p.informacionInsuficiente, reglasPendientes: p.reglasPendientes,
    reglas: p.reglas.map(raw => { const r = object(raw); return { codigo: r.codigo, nombre: r.nombre,
      resultado: r.resultado, efecto: r.efecto, motivos: r.motivos,
      ...(r.estadoEvaluacion === undefined ? {} : { estadoEvaluacion: r.estadoEvaluacion, datosFaltantes: r.datosFaltantes }) }; }),
    recogida: { estado: recogida.estado, motivos: recogida.motivos, observacionAplicacion: recogida.observacionAplicacion },
    decisionCrediticia: p.decisionCrediticia, conclusionFuncional: p.conclusionFuncional,
    autorizadoDecisionCrediticia: p.autorizadoDecisionCrediticia, radicacionLinix: p.radicacionLinix, ...historico,
    ...(revision ? { revisionHistorica: revision } : {}) } as ResultadoShadow;
}
function validarRevisionHistorica(value: unknown): RevisionHistorica {
  const p = object(value);
  if (p.noPersistida !== true || p.version !== "REPLAY_XML_9FB_1" || typeof p.huellaFuentes !== "string"
      || !/^[a-f0-9]{64}$/.test(p.huellaFuentes) || !text(p.fechaHdcOriginal)
      || (p.fechaSalarioObservado !== null && !text(p.fechaSalarioObservado)) || !text(p.estadoConsistencia)
      || !strings(p.bloqueosAplicabilidad) || !Array.isArray(p.escenarios) || p.escenarios.length > 2) throw fallo();
  const money = (value: unknown): ValorFinanciero => { importe(value); const m = object(value); return { disponible: m.disponible as boolean, valor: m.valor as string | null }; };
  if (object(p.cuotaHdcAplicable).disponible !== false || object(p.cuotaHdcAplicable).valor !== null) throw fallo();
  const vistos = new Set();
  const escenarios = p.escenarios.map(value => {
    const e = object(value);
    if (!["CDC_INCLUIDO", "CDC_EXCLUIDO"].includes(String(e.codigo)) || vistos.has(e.codigo)
        || e.naturaleza !== "CONDICIONADO_NO_DECISORIO" || e.autorizadoDecisionCrediticia !== false
        || !strings(e.condiciones)) throw fallo();
    vistos.add(e.codigo);
    return { codigo: e.codigo, naturaleza: e.naturaleza, condiciones: e.condiciones,
      ingresoUtilizado: money(e.ingresoUtilizado), cuotaHdcAplicableHipotetica: money(e.cuotaHdcAplicableHipotetica),
      gastosFamiliares: money(e.gastosFamiliares), cuotaNuevaParaCapacidad: money(e.cuotaNuevaParaCapacidad),
      capacidadMatematica: money(e.capacidadMatematica), autorizadoDecisionCrediticia: false };
  });
  return { noPersistida: true, version: "REPLAY_XML_9FB_1", huellaFuentes: p.huellaFuentes,
    fechaHdcOriginal: p.fechaHdcOriginal, fechaSalarioObservado: p.fechaSalarioObservado,
    ingresoObservado: money(p.ingresoObservado), cuotaHdcBruta: money(p.cuotaHdcBruta),
    cuotaHdcAplicable: { disponible: false, valor: null }, estadoConsistencia: p.estadoConsistencia,
    bloqueosAplicabilidad: p.bloqueosAplicabilidad, escenarios } as RevisionHistorica;
}
export class HttpShadowLaboratorio implements ShadowPort {
  protected csrf: string | null = null;
  constructor(private readonly entorno: { dev: boolean; flag: unknown; hostname: string }, private readonly timeoutMs = 15000) {
    this.comprobar();
  }
  private comprobar() { if (!laboratorioLocal(this.entorno.dev, this.entorno.flag, this.entorno.hostname)) throw fallo(); }
  protected async errorHttp(_response: Response): Promise<ErrorCredihoy> { return fallo(); }
  protected async json(path: typeof PATHS[keyof typeof PATHS], init: RequestInit, signal?: AbortSignal): Promise<{ body: unknown; response: Response }> {
    this.comprobar();
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) controller.abort();
    const timer = setTimeout(abort, this.timeoutMs);
    try {
      const response = await fetch(path, { ...init, signal: controller.signal, credentials: "same-origin", redirect: "error", cache: "no-store" });
      if (!response.ok) throw await this.errorHttp(response);
      if (!response.headers.get("Content-Type")?.includes("application/json")) throw fallo();
      return { body: await response.json(), response };
    } catch (error) { throw error instanceof ErrorCredihoy ? error : fallo(); }
    finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
  }
  private async token(signal?: AbortSignal) {
    const { body } = await this.json(PATHS.csrf, { method: "GET" }, signal);
    const token = object(body).csrfToken;
    if (typeof token !== "string" || !/^[a-zA-Z0-9]{32,64}$/.test(token)) throw fallo();
    this.csrf = token;
  }
  async iniciarSesion(username: string, password: string, signal?: AbortSignal) {
    await this.token(signal);
    await this.json(PATHS.login, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": this.csrf! },
      body: JSON.stringify({ username, password }) }, signal);
    await this.token(signal);
  }
  async evaluar(solicitud: SolicitudCredihoy, escenario: EscenarioShadow, signal: AbortSignal) {
    const payload = requestShadow(solicitud, escenario);
    if (!this.csrf) throw fallo();
    const requestId = crypto.randomUUID();
    const { body, response } = await this.json(PATHS.evaluar, { method: "POST", headers: { "Content-Type": "application/json",
      "X-CSRFToken": this.csrf, "X-Credihoy-Laboratorio": "SINTETICO_CD3C", "X-Request-ID": requestId }, body: JSON.stringify(payload) }, signal);
    if (response.headers.get("X-Request-ID") !== requestId) throw fallo();
    return validarResultadoShadow(body, requestId);
  }
}
