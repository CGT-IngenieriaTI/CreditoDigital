import { useMemo, useState } from "react";

import { ConsumoSolicitudResumen } from "../types/credit";

function formatDate(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return new Intl.DateTimeFormat("es-CO", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function decisionTone(value: string) {
  const normalized = String(value || "").toLowerCase();
  if (normalized.includes("aprobado")) return "success";
  if (normalized.includes("negado")) return "danger";
  if (normalized.includes("gris") || normalized.includes("revision")) return "warning";
  return "neutral";
}

export function AdvisorHistoryPage({
  items,
  loading,
  error,
  onRefresh,
  onOpenSolicitud,
  onOpenPdf,
  onDownloadPdf,
}: {
  items: ConsumoSolicitudResumen[];
  loading: boolean;
  error: string;
  onRefresh: () => Promise<void>;
  onOpenSolicitud: (solicitudId: string) => Promise<void>;
  onOpenPdf: (solicitudId: string) => void;
  onDownloadPdf: (solicitudId: string, numeroSolicitud: string) => Promise<void>;
}) {
  const [q, setQ] = useState("");
  const [estado, setEstado] = useState("TODOS");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return items.filter((item) => {
      const statusMatch = estado === "TODOS" || item.estado === estado;
      const queryMatch =
        !query ||
        item.numero_solicitud.toLowerCase().includes(query) ||
        item.numero_identificacion.includes(query) ||
        item.primer_apellido.toLowerCase().includes(query);
      const itemDate = item.created_at ? new Date(item.created_at) : null;
      const fromMatch = !dateFrom || (itemDate ? itemDate >= new Date(`${dateFrom}T00:00:00`) : false);
      const toMatch = !dateTo || (itemDate ? itemDate <= new Date(`${dateTo}T23:59:59`) : false);
      return statusMatch && queryMatch && fromMatch && toMatch;
    });
  }, [dateFrom, dateTo, estado, items, q]);

  const estados = useMemo(() => {
    const values = Array.from(new Set(items.map((item) => item.estado).filter(Boolean)));
    return ["TODOS", ...values];
  }, [items]);

  const summary = useMemo(() => ({
    visibles: filtered.length,
    aprobadas: filtered.filter((item) => decisionTone(item.decision_final) === "success").length,
    pendientes: filtered.filter((item) => !item.pdf_url).length,
    conPdf: filtered.filter((item) => Boolean(item.pdf_url)).length,
  }), [filtered]);

  return (
    <div className="row justify-content-center">
      <div className="col-12">
        <div className="content-card content-card--history history-board">
          <div className="history-board__hero">
            <div>
              <span className="eyebrow">Historial operativo</span>
              <h2 className="section-title mt-2 mb-2">Solicitudes del asesor</h2>
              <p className="section-copy mb-0">Consulta el histórico, la decisión final y el acceso al PDF con una lectura más ejecutiva.</p>
            </div>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => void onRefresh()}>
              {loading ? "Actualizando..." : "Actualizar"}
            </button>
          </div>

          <div className="history-board__filters">
            <input
              className="form-control"
              placeholder="Buscar por solicitud, identificación o apellido..."
              value={q}
              onChange={(event) => setQ(event.target.value)}
            />
            <input className="form-control" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
            <input className="form-control" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
            <select className="form-select" value={estado} onChange={(event) => setEstado(event.target.value)}>
              {estados.map((option) => (
                <option key={option} value={option}>
                  {option === "TODOS" ? "Todos los estados" : option}
                </option>
              ))}
            </select>
          </div>

          <div className="history-board__summary">
            <article className="history-board__metric">
              <span>Solicitudes visibles</span>
              <strong>{summary.visibles}</strong>
            </article>
            <article className="history-board__metric">
              <span>Crédito aprobado</span>
              <strong>{summary.aprobadas}</strong>
            </article>
            <article className="history-board__metric">
              <span>Pendientes de PDF</span>
              <strong>{summary.pendientes}</strong>
            </article>
            <article className="history-board__metric">
              <span>Con PDF final</span>
              <strong>{summary.conPdf}</strong>
            </article>
          </div>

          <div className="history-board__list">
            {filtered.length ? (
              filtered.map((item) => {
                const tone = decisionTone(item.decision_final);
                return (
                  <article key={item.id} className="history-board__item">
                    <div className="history-board__item-head">
                      <div>
                        <strong>{item.numero_solicitud}</strong>
                        <span>{formatDate(item.created_at)}</span>
                      </div>
                      <span className={`history-board__badge history-board__badge--${tone}`}>
                        {item.decision_final || "Pendiente"}
                      </span>
                    </div>

                    <div className="history-board__item-grid">
                      <div>
                        <span>Identificación</span>
                        <strong>{item.numero_identificacion}</strong>
                      </div>
                      <div>
                        <span>Apellido</span>
                        <strong>{item.primer_apellido}</strong>
                      </div>
                      <div>
                        <span>Estado</span>
                        <strong>{item.estado}</strong>
                      </div>
                      <div>
                        <span>Canal / agencia</span>
                        <strong>{item.agencia || item.canal || "Sin canal"}</strong>
                      </div>
                    </div>

                    <div className="history-board__actions">
                      <button type="button" className="btn btn-outline-primary btn-sm" onClick={() => void onOpenSolicitud(item.id)}>
                        Abrir
                      </button>
                      <button type="button" className="btn btn-outline-secondary btn-sm" disabled={!item.pdf_url} onClick={() => item.pdf_url && onOpenPdf(item.id)}>
                        Ver PDF
                      </button>
                      <button
                        type="button"
                        className="btn btn-outline-secondary btn-sm"
                        disabled={!item.pdf_url}
                        onClick={() => item.pdf_url && void onDownloadPdf(item.id, item.numero_solicitud)}
                      >
                        Descargar
                      </button>
                    </div>
                  </article>
                );
              })
            ) : (
              <div className="history-board__empty">{loading ? "Cargando historial..." : "No hay solicitudes para los filtros actuales."}</div>
            )}
          </div>

          {error ? <div className="alert alert-danger mt-3 mb-0">{error}</div> : null}
        </div>
      </div>
    </div>
  );
}
