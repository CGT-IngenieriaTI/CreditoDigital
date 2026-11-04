import { useState, type ReactNode } from "react";
import type { Elegibilidad, EstadoCredito, EstadoReglaCredito } from "../services/estadoCredito";
import { moneda } from "./FormControls";
import { CredihoyEstadoLinix } from "./CredihoyEstadoLinix";
import type { CotizacionInstitucional } from "../services/cotizacion";

export function cuotaManualVinculada(estado: EstadoCredito, cuota?: CotizacionInstitucional | null) {
  const c = cuota?.condicionesSolicitadas;
  return cuota?.solicitudId === estado.solicitudId && cuota.fuente === 'LINIX_EJECUCION_MANUAL'
    && cuota.estado === 'EVIDENCIA_INSTITUCIONAL_MANUAL' && cuota.disponible && c
    && Number(c.monto) === Number(estado.asociado.resumen.monto)
    && c.numeroCuotas === estado.asociado.resumen.plazoMeses
    && c.periodicidad === estado.asociado.resumen.periodicidad
    && estado.operador.concordanciaTasas?.tasaComercialNamv != null
    && Number(c.tasaNominalNamv) === Number(estado.operador.concordanciaTasas.tasaComercialNamv)
    ? cuota : null;
}

export const TITULOS_ELEGIBILIDAD: Record<Elegibilidad, string> = {
  ELEGIBLE_PARA_RADICACION: "Elegible para radicación", PENDIENTE_EVALUACION: "Evaluación pendiente",
  REQUIERE_REVISION: "Requiere revisión", NO_ELEGIBLE: "No elegible para radicación",
};
const ESTADOS: EstadoReglaCredito[] = ["CUMPLE", "PENDIENTE_POLITICA", "NO_EVALUABLE", "NO_CUMPLE", "NO_APLICA", "INACTIVA"];
const LABELS: Record<EstadoReglaCredito, string> = { CUMPLE: "Cumple", NO_CUMPLE: "No cumple",
  PENDIENTE_POLITICA: "Política pendiente", NO_EVALUABLE: "Sin evaluación concluyente", NO_APLICA: "No aplica", INACTIVA: "Inactiva" };
export function resumenReglas(estado: EstadoCredito) {
  return ESTADOS.map(codigo => {
    const cantidad = estado.operador.elegibilidad.reglas.filter(r => r.estado === codigo).length;
    const titulo = { CUMPLE: cantidad === 1 ? "regla cumplida" : "reglas cumplidas",
      NO_CUMPLE: cantidad === 1 ? "regla no cumplida" : "reglas no cumplidas",
      PENDIENTE_POLITICA: cantidad === 1 ? "pendiente de política" : "pendientes de política",
      NO_EVALUABLE: "sin evaluación concluyente", NO_APLICA: cantidad === 1 ? "no aplica" : "no aplican",
      INACTIVA: cantidad === 1 ? "inactiva" : "inactivas" }[codigo];
    return { codigo, titulo, cantidad };
  });
}
const fecha = (s: string) => new Date(s).toLocaleString("es-CO", { timeZone: "America/Bogota", dateStyle: "medium", timeStyle: "short" });
const dia = (s: string) => new Date(s.slice(0, 10) + "T12:00:00-05:00").toLocaleDateString("es-CO", { timeZone: "America/Bogota" });

export function CredihoyEstadoCredito({ estado, perspectivaInicial = "ASOCIADO", cotizacion, cotizacionResultado }: {
  estado: EstadoCredito; perspectivaInicial?: "ASOCIADO" | "OPERADOR";
  cotizacion?: ReactNode | ((tecnico: boolean) => ReactNode); cotizacionResultado?: CotizacionInstitucional | null;
}) {
  const [perspectiva, setPerspectiva] = useState(perspectivaInicial);
  const a = estado.asociado, o = estado.operador, resumen = resumenReglas(estado), operador = perspectiva === "OPERADOR";
  const institucional=o.linix?.versionContrato==='credihoy-linix-estado-v2';
  const manual = cuotaManualVinculada(estado, cotizacionResultado);
  const tasa = manual?.condicionesSolicitadas?.tasaNominalNamv
    ?? (a.resumen.cuotaSimulada.disponible ? a.resumen.cuotaSimulada.tasaNamvObservada : o.concordanciaTasas?.tasaComercialNamv);
  const pasos = [
    { titulo: "Solicitud recibida y consentimiento firmado", estado: a.consentimientoFirmado ? "completo" : "pendiente" },
    { titulo: "Consulta HDC completada", estado: a.hdcCompletada ? "completo" : "pendiente" },
    { titulo: o.elegibilidad.elegible ? "Elegibilidad verificada para radicar" : "Evaluación pendiente de completar", estado: o.elegibilidad.elegible ? "completo" : "revision" },
    { titulo: institucional?"Registro institucional confirmado":o.linix ? "Registro LINIX con incidencia en referencias" : "Registro LINIX pendiente", estado: institucional?"completo":o.linix ? "revision" : "pendiente" },
    { titulo: institucional?"Aprobacion institucional registrada; firma pendiente":"Decisión y formalización pendientes", estado: "pendiente" },
  ];
  return <div className="ch-credit-state" data-testid="credit-state" data-perspectiva={perspectiva}>
    <div className="ch-credit-perspectives" role="tablist" aria-label="Perspectiva del estado" onKeyDown={e => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
      e.preventDefault();
      const next = e.key === "Home" ? "ASOCIADO" : e.key === "End" ? "OPERADOR" : operador ? "ASOCIADO" : "OPERADOR";
      setPerspectiva(next); document.getElementById(`cd8-tab-${next}`)?.focus();
    }}>
      {(["ASOCIADO", "OPERADOR"] as const).map(p => <button type="button" role="tab" id={`cd8-tab-${p}`}
        key={p} tabIndex={perspectiva === p ? 0 : -1} aria-selected={perspectiva === p} aria-controls="cd8-panel" onClick={() => setPerspectiva(p)}>
        {p === "ASOCIADO" ? "Asociado" : "Operador"}</button>)}
    </div>
    <div id="cd8-panel" role="tabpanel" aria-labelledby={`cd8-tab-${perspectiva}`}>
      <header className="ch-credit-heading"><div><p className="ch-eyebrow">Crédito Digital</p><h2>Mi solicitud CrediHoy</h2></div>
        <span className={`ch-credit-badge ch-credit-badge--${o.elegibilidad.estado}`}>{operador ? TITULOS_ELEGIBILIDAD[o.elegibilidad.estado] : a.tituloEstado}</span></header>
      <dl className="ch-credit-summary">
        <div><dt>Monto solicitado</dt><dd>{moneda(a.resumen.monto)}</dd></div>
        <div><dt>Plazo</dt><dd>{a.resumen.plazoMeses} meses</dd></div>
        <div><dt>Periodicidad</dt><dd>Mensual</dd></div>
        <div><dt>Tasa N.A.M.V.</dt><dd>{tasa ? Number(tasa).toLocaleString('es-CO',{maximumFractionDigits:6})+' %' : 'No disponible'}</dd></div>
        <div><dt>Cuota simulada mensual</dt><dd>{manual ? moneda(manual.cuotaInstitucional!) : a.resumen.cuotaSimulada.disponible ? moneda(a.resumen.cuotaSimulada.valor!) : "No disponible"}</dd></div>
      </dl>
      {manual && <p className="ch-credit-caption">Cálculo institucional manual de referencia LINIX. No es una oferta contractual ni un recálculo automático.</p>}
      {!operador && <div className="ch-credit-next ch-credit-required" role="status"><strong>Siguiente paso</strong><p>{a.proximaAccion}</p></div>}
      {!manual && <p className="ch-credit-caption">{a.resumen.cuotaSimulada.disponible ? "Simulación LINIX histórica de referencia. No constituye oferta contractual ni un crédito aprobado." : "La cuota se mostrará cuando su evidencia oficial esté vinculada al expediente."}</p>}
      {operador && a.resumen.cuotaSimulada.disponible && <dl className="ch-credit-metrics ch-credit-band">
        <div><dt>Fecha de simulación</dt><dd>{dia(a.resumen.cuotaSimulada.fecha!)}</dd></div>
        <div><dt>Referencia técnica del cálculo</dt><dd>{a.resumen.cuotaSimulada.referenciaTecnica ?? "Evidencia vinculada"}</dd></div>
        {a.resumen.cuotaSimulada.tasaNamvObservada && <div><dt>Tasa utilizada por el simulador</dt><dd>{a.resumen.cuotaSimulada.tasaNamvObservada} % N.A.M.V.</dd></div>}
        {a.resumen.cuotaSimulada.primeraCuota && <div><dt>Primera cuota simulada</dt><dd>{dia(a.resumen.cuotaSimulada.primeraCuota)}</dd></div>}
      </dl>}
      <section className="ch-credit-band" aria-labelledby="cd8-evaluacion"><h3 id="cd8-evaluacion">Evaluación de la solicitud</h3>
        <dl className="ch-credit-metrics">
          <div><dt>Capacidad de pago</dt><dd data-testid="cd8-capacidad">{a.capacidad}</dd></div>
          <div><dt>Decisión crediticia</dt><dd>{a.decision}</dd></div>
          <div><dt>Puntaje de riesgo</dt><dd>No disponible</dd></div>
          <div><dt>LINIX</dt><dd>{institucional?"Aprobacion registrada; firma pendiente":a.linix === "INCIDENCIA_TECNICA" ? "Incidencia técnica" : "Pendiente de registro"}</dd></div>
        </dl>
      </section>
      <section className="ch-credit-band" aria-labelledby="cd8-validaciones"><h3 id="cd8-validaciones">Validaciones realizadas</h3>
        <div className="ch-credit-rulebar" role="img" aria-label={resumen.map(r => `${r.cantidad} ${r.titulo}`).join(", ")}>
          {resumen.filter(r => r.cantidad > 0).map(r => <span key={r.codigo} className={`rule-${r.codigo}`} style={{ flexGrow: r.cantidad }} />)}
        </div>
        <ul className="ch-credit-legend">{resumen.filter(r => r.cantidad > 0).map(r => <li key={r.codigo}>
          <span className={`ch-credit-dot rule-${r.codigo}`} aria-hidden="true" />{r.cantidad} {r.titulo.toLocaleLowerCase("es")}</li>)}</ul>
      </section>
      <section className="ch-credit-band" aria-labelledby="cd8-progreso"><h3 id="cd8-progreso">Progreso del trámite</h3>
        <ol className="ch-credit-progress">{pasos.map(p => <li key={p.titulo} className={`is-${p.estado}`}>
          <span className="ch-credit-step" aria-hidden="true">{p.estado === "completo" ? "✓" : p.estado === "revision" ? "!" : ""}</span>
          <span>{p.titulo}</span></li>)}</ol>
      </section>
      {!operador && <section className="ch-credit-band"><h3>Condiciones pendientes</h3>
        <ul className="ch-credit-pending">{a.pendientes.map(p => <li key={p}>{p}</li>)}</ul>
        <div className="ch-credit-next"><strong>Próxima acción</strong><p>{a.proximaAccion}</p></div>
      </section>}
      {typeof cotizacion === 'function' ? cotizacion(operador) : cotizacion}
      {!operador && <section className="ch-credit-band"><h3>Historial de mi solicitud</h3>
        {o.historial.length ? <ul className="ch-credit-history">{o.historial.map((h,i) => <li key={h.id}>
          <time dateTime={h.fecha}>{fecha(h.fecha)}</time>
          <p>{h.activa ? 'Revisión vigente' : 'Revisión anterior'} · Evaluacion del canal</p>
          <span>Registro {i + 1} del expediente</span>
        </li>)}</ul> : <p>Sin evaluaciones registradas.</p>}
      </section>}
      {operador && <>
        {o.concordanciaTasas && <section className="ch-credit-band" data-testid="cd10-concordancia"><h3>Concordancia contractual de tasas</h3>
          <p className="ch-credit-mode">{o.concordanciaTasas.estado.replace(/_/g," ")}</p>
          <dl className="ch-credit-metrics">
            <div><dt>Línea comercial asignada</dt><dd>{o.concordanciaTasas.linea??"Pendiente de ratificación"}</dd></div>
            <div><dt>Clasificación automática LINIX</dt><dd>{o.concordanciaTasas.lineaOrigen??"Pagaduría no acreditada"}</dd></div>
            {o.concordanciaTasas.fechaPagaduria&&<div><dt>Fecha de evidencia de pagaduría</dt><dd>{o.concordanciaTasas.fechaPagaduria}</dd></div>}
            {o.concordanciaTasas.versionTarifa&&<div><dt>Versión tarifaria</dt><dd>{o.concordanciaTasas.versionTarifa}</dd></div>}
            {o.concordanciaTasas.referenciaAsignacion&&<div><dt>Asignación autorizada</dt><dd>{o.concordanciaTasas.referenciaAsignacion}</dd></div>}
            <div><dt>Tasa comercial configurada N.A.M.V.</dt><dd>{o.concordanciaTasas.tasaComercialNamv===null?"Sin tarifa aplicable":`${o.concordanciaTasas.tasaComercialNamv} %`}</dd></div>
            <div><dt>Tasa utilizada en LINIX N.A.M.V.</dt><dd>{o.concordanciaTasas.tasaLinixNamv===null?"Sin evidencia vinculada":`${o.concordanciaTasas.tasaLinixNamv} %`}</dd></div>
            <div><dt>Diferencia (puntos porcentuales)</dt><dd>{o.concordanciaTasas.diferenciaPuntos??"No comparable sin tarifa asignada"}</dd></div>
          </dl>
          <p className="ch-credit-caption">La selección comercial no modifica la simulación ni el flujo existente. La admisión LINIX, cuota, seguro y calendario requieren concordancia acreditada.</p>
        </section>}
        {o.capacidad && <section className="ch-credit-band"><h3>Capacidad de pago</h3>
          <dl className="ch-credit-metrics"><div><dt>Resultado del cálculo</dt><dd>{o.capacidad.estadoCalculo.replace(/_/g, " ")}</dd></div>
            <div><dt>Valor calculado</dt><dd>{o.capacidad.valor === null ? "No disponible" : moneda(o.capacidad.valor)}</dd></div>
            <div><dt>Criterio del producto</dt><dd>{o.capacidad.estadoCriterio.replace(/_/g, " ")}</dd></div>
            <div><dt>Política aplicable</dt><dd>{o.capacidad.referenciaPolitica ?? "Pendiente de ratificación"}</dd></div></dl>
        </section>}
        <section className="ch-credit-band" data-testid="cd8-eligibility"><h3>Elegibilidad para radicación ordinaria</h3>
          <p className="ch-credit-eligibility">{TITULOS_ELEGIBILIDAD[o.elegibilidad.estado]}</p>
          <p>Política: {o.elegibilidad.versionPolitica ?? "Sin versión vigente autorizada"}. No equivale a crédito aprobado.</p>
          {o.matrizDocumental && <p className="ch-credit-caption">Matriz documental: {o.matrizDocumental.version}. Sus efectos para radicación siguen sin autorización acreditada.</p>}
          <ul className="ch-credit-blockers">{o.elegibilidad.causales.map((c, i) => <li key={`${c.codigo}-${i}`}><code>{c.codigo}</code>{c.regla && <span> · {c.regla}</span>}</li>)}</ul>
          <p>Transporte ordinario deshabilitado.</p>
        </section>
        {o.queFaltaParaContinuar && <section className="ch-credit-band" data-testid="cd9-pendientes"><h3>¿Qué falta para continuar?</h3>
          <ul className="ch-credit-pending-details">{o.queFaltaParaContinuar.map(p => <li key={p.codigo}>
            <strong>{p.nombre}</strong><span className="ch-credit-pending-code">{p.codigo} · {LABELS[p.estado]}</span>
            <p>{p.accion}</p>{p.fuentes.length > 0 && <dl>{p.fuentes.map(f => <div key={f.campo}><dt>{f.nombre}</dt><dd>{f.fuenteRequerida}</dd></div>)}</dl>}
          </li>)}</ul>
          {o.capacidad && o.capacidad.pendientes.length > 0 && <details><summary>Fuentes faltantes del cálculo y evaluación</summary>
            <ul className="ch-credit-pending">{o.capacidad.pendientes.map(f => <li key={f.campo}><strong>{f.nombre}: </strong>{f.accion} Fuente: {f.fuenteRequerida}.</li>)}</ul>
          </details>}
        </section>}
        <section className="ch-credit-band"><h3>Resultado de las 16 reglas</h3>
          <div className="ch-credit-table" tabIndex={0} role="region" aria-label="Resultados individuales">
            <table><thead><tr><th>Regla</th><th>Resultado</th><th>Obligatoriedad</th><th>Datos y causales</th></tr></thead>
              <tbody>{o.elegibilidad.reglas.map(r => <tr key={r.codigo} data-rule={r.codigo}>
                <th scope="row"><small>{r.codigo}</small>{r.nombre}</th><td><span className={`ch-credit-rule-status status-${r.estado}`}>{LABELS[r.estado]}</span></td>
                <td>{r.obligatoria === null ? "Política pendiente" : r.obligatoria ? "Obligatoria" : "No obligatoria"}</td>
                <td>{r.datosFaltantes.length ? r.datosFaltantes.join(", ") : r.estado === "INACTIVA" ? "Tratamiento según política" : "Ninguno reportado"}
                  {r.motivos.length > 0 && <small>{r.motivos.map(m => m.replace(/_/g, " ").toLocaleLowerCase("es")).join(" · ")}</small>}
                  {r.fuente && <small>Fuente: evaluación persistida · {r.fuente.versionMatriz}{r.fuente.referenciaDocumental && <><br />{r.fuente.referenciaDocumental}</>}</small>}</td>
              </tr>)}</tbody></table>
          </div>
        </section>
        <section className="ch-credit-band"><h3>Historial de evaluaciones</h3>
          {o.historial.length ? <ul className="ch-credit-history">{o.historial.map(h => <li key={h.id}>
            <div><time dateTime={h.fecha}>{fecha(h.fecha)}</time>{h.activa && <span className="ch-credit-history-current">Vigente en el expediente</span>}</div>
            <span>{h.capacidad.replace(/_/g, " ")} · {h.decision.replace(/_/g, " ")}</span>
          </li>)}</ul> : <p>Sin evaluaciones registradas.</p>}
        </section>
        <section className="ch-credit-band"><h3>Conciliación LINIX</h3>
          <p className="ch-credit-mode">{institucional?"Lectura institucional conciliada. No habilita operaciones ni cambia reglas del canal.":o.modoLinix === "PRUEBA_ADMINISTRATIVA" ? "Prueba administrativa existente. No es una radicación ordinaria ni una aprobación." : "Sin envío a LINIX."}</p>
          {o.linix && <CredihoyEstadoLinix estado={o.linix} />}
        </section>
      </>}
    </div>
  </div>;
}
