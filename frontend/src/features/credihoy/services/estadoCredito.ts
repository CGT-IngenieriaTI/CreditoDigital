export type Elegibilidad = "ELEGIBLE_PARA_RADICACION" | "PENDIENTE_EVALUACION" | "REQUIERE_REVISION" | "NO_ELEGIBLE";
export type EstadoReglaCredito = "CUMPLE" | "NO_CUMPLE" | "NO_EVALUABLE" | "PENDIENTE_POLITICA" | "NO_APLICA" | "INACTIVA";
export interface FuentePendiente { campo: string; nombre: string; fuenteRequerida: string; accion: string }
export interface EstadoCredito {
  versionContrato: "credihoy-estado-credito-v1"; solicitudId: string; soloLectura: true;
  asociado: {
    estado: Elegibilidad; tituloEstado: string;
    resumen: { producto: string; codigoProducto: "390"; modalidad: "111"; monto: string; plazoMeses: number;
      periodicidad: "MENSUAL"; cuotaSimulada: { disponible: boolean; valor: string | null; fuente: string | null; fecha: string | null;
        referenciaTecnica?: string; evidenciaSha256?: string; tasaNamvObservada?: string; primeraCuota?: string } };
    capacidad: string; decision: string; score: null; pendientes: string[]; proximaAccion: string;
    linix: "INCIDENCIA_TECNICA" | "SIN_RADICACION_CONFIRMADA" | "APROBACION_INSTITUCIONAL_REGISTRADA";
    decisionInstitucional?:{aprobada:true;fecha:string;firmaAcreditada:false;liquidada:false;alCorte:string};
    consentimientoFirmado: boolean; hdcCompletada: boolean;
  };
  operador: {
    elegibilidad: { version: "credihoy-elegibilidad-v1"; estado: Elegibilidad; elegible: boolean;
      modo: "ORDINARIA"; esAprobacion: false; versionPolitica: string | null;
      causales: { codigo: string; categoria: "DATOS" | "REVISION" | "EXCLUSION"; regla: string | null }[];
      reglas: { codigo: string; nombre: string; estado: EstadoReglaCredito; obligatoria: boolean | null;
        excluyente: boolean | null; motivos: string[]; datosFaltantes: string[]; activaEnInventario: boolean;
        fuente?: { evaluacion: "RESULTADO_PERSISTIDO"; versionMatriz: string; referenciaDocumental: string | null; limitacionDocumental: string | null };
        accionPendiente?: string; fuentesPendientes?: FuentePendiente[] }[] };
    matrizDocumental?: { version: string; sha256: string; archivo: string; autorizacionEfectosAcreditada: false };
    queFaltaParaContinuar?: { codigo: string; nombre: string; estado: EstadoReglaCredito; accion: string; fuentes: FuentePendiente[] }[];
    capacidad?: { estadoCalculo: string; valor: string | null; estadoCriterio: string; referenciaPolitica: string | null; pendientes: FuentePendiente[] };
    concordanciaTasas?: { estado: string; linea: string | null; tasaComercialNamv: string | null; tasaLinixNamv: string | null;
      diferenciaPuntos: string | null; contratoConcordante: boolean; referenciaComercial: string | null; motivos: string[];
      lineaOrigen?: string | null; versionTarifa?: number; referenciaAsignacion?: string; fechaPagaduria?: string };
    historial: { id: string; fecha: string; capacidad: string; decision: string; activa: boolean }[];
    linix: import("./linixEstado").EstadoLinix | null;
    evaluacion: { capacidad: string; decision: "NO_AUTORIZADA"; fecha: string | null };
    modoLinix: "PRUEBA_ADMINISTRATIVA" | "SIN_ENVIO";
    transporteOrdinarioHabilitado: false; perspectivaAsociadoAsistida: true;
  };
}

import { validarEstadoLinix } from "./linixEstado";
import { CODIGOS_REGLAS_SHADOW } from "./shadow";
const fail = (): never => { throw new Error("ESTADO_CREDITO_INVALIDO"); };
function obj(x: unknown): Record<string, unknown> { return x && typeof x === "object" && !Array.isArray(x) ? x as Record<string, unknown> : fail(); }
function text(x: unknown): string { return typeof x === "string" && x.length > 0 && x.length <= 500 ? x : fail(); }
function texts(x: unknown): string[] { return Array.isArray(x) && x.length <= 100 ? x.map(text) : fail(); }
function bool(x: unknown): boolean { return typeof x === "boolean" ? x : fail(); }
function tri(x: unknown): boolean | null { return x === null ? null : bool(x); }
function fuentes(x: unknown): FuentePendiente[] {
  if (!Array.isArray(x) || x.length > 30) return fail();
  return x.map(raw => { const f = obj(raw); return { campo: text(f.campo), nombre: text(f.nombre), fuenteRequerida: text(f.fuenteRequerida), accion: text(f.accion) }; });
}
function date(x: unknown): string { const s = text(x); return Number.isFinite(Date.parse(s)) ? s : fail(); }
function eligibility(x: unknown): Elegibilidad {
  return ["ELEGIBLE_PARA_RADICACION", "PENDIENTE_EVALUACION", "REQUIERE_REVISION", "NO_ELEGIBLE"].includes(String(x)) ? x as Elegibilidad : fail();
}
export function validarEstadoCredito(value: unknown, id: string): EstadoCredito {
  const p = obj(value), a = obj(p.asociado), o = obj(p.operador), e = obj(o.elegibilidad), r = obj(a.resumen), q = obj(r.cuotaSimulada), v = obj(o.evaluacion);
  const linix=o.linix===null?null:validarEstadoLinix(o.linix,id);
  const institucional=linix?.versionContrato==='credihoy-linix-estado-v2'?linix:null;
  let decisionInstitucional:EstadoCredito['asociado']['decisionInstitucional'];
  if(institucional){
    const d=obj(a.decisionInstitucional);
    if(a.linix!=='APROBACION_INSTITUCIONAL_REGISTRADA'||d.aprobada!==true||d.fecha!==institucional.aprobacion.fecha
      ||d.firmaAcreditada!==false||d.liquidada!==false||d.alCorte!==institucional.actualizadoAt)return fail();
    decisionInstitucional={aprobada:true,fecha:institucional.aprobacion.fecha,firmaAcreditada:false,liquidada:false,alCorte:institucional.actualizadoAt};
  }else if(a.decisionInstitucional!==undefined)return fail();
  if (p.versionContrato !== "credihoy-estado-credito-v1" || p.solicitudId !== id || p.soloLectura !== true
      || r.codigoProducto !== "390" || r.modalidad !== "111" || r.periodicidad !== "MENSUAL"
      || typeof r.monto !== "string" || !/^[0-9]+(?:\.[0-9]{1,2})?$/.test(r.monto)
      || !Number.isSafeInteger(r.plazoMeses) || Number(r.plazoMeses) < 1 || Number(r.plazoMeses) > 48
      || a.score !== null || e.version !== "credihoy-elegibilidad-v1" || e.modo !== "ORDINARIA" || e.esAprobacion !== false
      || o.transporteOrdinarioHabilitado !== false || o.perspectivaAsociadoAsistida !== true
      || v.decision !== "NO_AUTORIZADA" || !["PRUEBA_ADMINISTRATIVA", "SIN_ENVIO"].includes(String(o.modoLinix))
      || !institucional&&!["INCIDENCIA_TECNICA", "SIN_RADICACION_CONFIRMADA"].includes(String(a.linix))
      || !Array.isArray(e.reglas) || e.reglas.length !== 16 || !Array.isArray(e.causales) || e.causales.length > 100
      || !Array.isArray(o.historial) || o.historial.length > 100) return fail();
  const estado = eligibility(e.estado);
  if (eligibility(a.estado) !== estado || bool(e.elegible) !== (estado === "ELEGIBLE_PARA_RADICACION")
      || (e.elegible && e.causales.length !== 0)) return fail();
  const reglas = e.reglas.map((raw, i) => {
    const row = obj(raw);
    if (row.codigo !== CODIGOS_REGLAS_SHADOW[i] || !["CUMPLE", "NO_CUMPLE", "NO_EVALUABLE", "PENDIENTE_POLITICA", "NO_APLICA", "INACTIVA"].includes(String(row.estado))) return fail();
    const f = row.fuente === undefined ? undefined : obj(row.fuente);
    if (f && f.evaluacion !== "RESULTADO_PERSISTIDO") return fail();
    return { codigo: String(row.codigo), nombre: text(row.nombre), estado: row.estado as EstadoReglaCredito,
      obligatoria: tri(row.obligatoria), excluyente: tri(row.excluyente), motivos: texts(row.motivos),
      datosFaltantes: texts(row.datosFaltantes), activaEnInventario: bool(row.activaEnInventario),
      ...(f ? { fuente: { evaluacion: "RESULTADO_PERSISTIDO" as const, versionMatriz: text(f.versionMatriz),
        referenciaDocumental: f.referenciaDocumental === null ? null : text(f.referenciaDocumental),
        limitacionDocumental: f.limitacionDocumental === null ? null : text(f.limitacionDocumental) },
        accionPendiente: text(row.accionPendiente), fuentesPendientes: fuentes(row.fuentesPendientes) } : {}) };
  });
  const disponible = bool(q.disponible);
  if (disponible ? typeof q.valor !== "string" || !/^[0-9]+(?:\.[0-9]{1,2})?$/.test(q.valor) || Number(q.valor) <= 0
      || q.fuente !== "SIMULACION_OFICIAL_LINIX" : q.valor !== null || q.fuente !== null || q.fecha !== null) return fail();
  const extra: Partial<EstadoCredito['operador']> = {};
  if (o.concordanciaTasas !== undefined) {
    const c=obj(o.concordanciaTasas);
    if(!['TARIFA_NO_DETERMINADA','TARIFA_PENDIENTE_DE_ASIGNACION','TARIFA_VIGENTE_NO_DISPONIBLE','CONCORDANTE','DISCREPANCIA_TASA','PENDIENTE_VALIDACION_CONTRACTUAL'].includes(String(c.estado))) return fail();
    const amount=(v:unknown)=>v===null?null:typeof v==='string'&&/^-?\d{1,3}(?:\.\d{1,6})?$/.test(v)?v:fail();
    if(bool(c.contratoConcordante)!==(c.estado==='CONCORDANTE')) return fail();
    extra.concordanciaTasas={estado:String(c.estado),linea:c.linea===null?null:text(c.linea),
      tasaComercialNamv:amount(c.tasaComercialNamv),tasaLinixNamv:amount(c.tasaLinixNamv),diferenciaPuntos:amount(c.diferenciaPuntos),
      contratoConcordante:bool(c.contratoConcordante),referenciaComercial:c.referenciaComercial===null?null:text(c.referenciaComercial),motivos:texts(c.motivos)};
    if(c.lineaOrigen!==undefined) extra.concordanciaTasas.lineaOrigen=c.lineaOrigen===null?null:text(c.lineaOrigen);
    if(c.versionTarifa!==undefined) {
      if(!Number.isSafeInteger(c.versionTarifa)||Number(c.versionTarifa)<1)return fail();
      extra.concordanciaTasas.versionTarifa=Number(c.versionTarifa);
    }
    if(c.referenciaAsignacion!==undefined)extra.concordanciaTasas.referenciaAsignacion=text(c.referenciaAsignacion);
    if(c.pagaduria!==undefined)extra.concordanciaTasas.fechaPagaduria=date(obj(c.pagaduria).fechaConsulta);
  }
  if (o.matrizDocumental !== undefined) {
    const m = obj(o.matrizDocumental), c = obj(o.capacidad);
    if (m.autorizacionEfectosAcreditada !== false || !/^[a-f0-9]{64}$/.test(String(m.sha256))
        || !Array.isArray(o.queFaltaParaContinuar) || o.queFaltaParaContinuar.length > 16
        || (c.valor !== null && (typeof c.valor !== 'string' || !/^-?[0-9]+(?:\.[0-9]+)?$/.test(c.valor)))) return fail();
    extra.matrizDocumental = { version: text(m.version), sha256: String(m.sha256), archivo: text(m.archivo), autorizacionEfectosAcreditada: false };
    extra.capacidad = { estadoCalculo: text(c.estadoCalculo), valor: c.valor as string | null, estadoCriterio: text(c.estadoCriterio),
      referenciaPolitica: c.referenciaPolitica === null ? null : text(c.referenciaPolitica), pendientes: fuentes(c.pendientes) };
    extra.queFaltaParaContinuar = o.queFaltaParaContinuar.map(raw => {
      const v = obj(raw), row = reglas.find(r => r.codigo === v.codigo);
      if (!row || row.estado !== v.estado) return fail();
      return { codigo: row.codigo, nombre: text(v.nombre), estado: row.estado, accion: text(v.accion), fuentes: fuentes(v.fuentes) };
    });
  }
  return { versionContrato: "credihoy-estado-credito-v1", solicitudId: id, soloLectura: true,
    asociado: { estado, tituloEstado: text(a.tituloEstado), resumen: { producto: text(r.producto), codigoProducto: "390", modalidad: "111",
      monto: r.monto, plazoMeses: Number(r.plazoMeses), periodicidad: "MENSUAL", cuotaSimulada: {
        disponible, valor: disponible ? String(q.valor) : null, fuente: disponible ? "SIMULACION_OFICIAL_LINIX" : null,
        fecha: disponible ? date(q.fecha) : null,
        ...(q.referenciaTecnica === undefined ? {} : { referenciaTecnica: text(q.referenciaTecnica), evidenciaSha256: /^[a-f0-9]{64}$/.test(String(q.evidenciaSha256)) ? String(q.evidenciaSha256) : fail() }),
        ...(q.tasaNamvObservada === undefined ? {} : { tasaNamvObservada: /^[0-9]{1,2}(?:\.[0-9]{1,6})?$/.test(String(q.tasaNamvObservada)) ? String(q.tasaNamvObservada) : fail(), primeraCuota: date(q.primeraCuota) })
      } }, capacidad: text(a.capacidad), decision: text(a.decision), score: null,
      pendientes: texts(a.pendientes), proximaAccion: text(a.proximaAccion), linix: a.linix as EstadoCredito['asociado']['linix'],
      ...(decisionInstitucional?{decisionInstitucional}:{}),
      consentimientoFirmado: bool(a.consentimientoFirmado), hdcCompletada: bool(a.hdcCompletada) },
    operador: { ...extra, elegibilidad: { version: "credihoy-elegibilidad-v1", estado, elegible: bool(e.elegible), modo: "ORDINARIA", esAprobacion: false,
      versionPolitica: e.versionPolitica === null ? null : text(e.versionPolitica), reglas,
      causales: e.causales.map(raw => { const c = obj(raw);
        if (!["DATOS", "REVISION", "EXCLUSION"].includes(String(c.categoria))) return fail();
        return { codigo: text(c.codigo), categoria: c.categoria as "DATOS" | "REVISION" | "EXCLUSION", regla: c.regla === null ? null : text(c.regla) }; }) },
      historial: o.historial.map(raw => { const h = obj(raw); return { id: text(h.id), fecha: date(h.fecha),
        capacidad: text(h.capacidad), decision: text(h.decision), activa: bool(h.activa) }; }),
      linix,
      evaluacion: { capacidad: text(v.capacidad), decision: "NO_AUTORIZADA", fecha: v.fecha === null ? null : date(v.fecha) },
      modoLinix: o.modoLinix as EstadoCredito['operador']['modoLinix'], transporteOrdinarioHabilitado: false, perspectivaAsociadoAsistida: true } };
}
