import type { EstadoLinix } from "../services/linixEstado";

export function CredihoyEstadoLinix({ estado }: { estado: EstadoLinix }) {
  if(estado.versionContrato==='credihoy-linix-estado-v2')return <section data-testid="linix-institucional">
    <h3>Decision institucional LINIX</h3><p role="status">Aprobada; firma y liquidacion pendientes.</p>
    <dl className="ch-detail-list"><div><dt>SC / flujo</dt><dd>{estado.numeroSc} / {estado.kFlujo}</dd></div>
      <div><dt>Aprobador</dt><dd>{estado.aprobacion.aprobadorReferencia}</dd></div>
      <div><dt>Fecha de aprobacion</dt><dd>{estado.aprobacion.fecha}</dd></div>
      <div><dt>Pagare asignado</dt><dd>{estado.pagare.numero}</dd></div>
      <div><dt>Actividad / tarea pendiente</dt><dd>{estado.actividad.codigo} / {estado.actividad.kSerie}</dd></div>
      <div><dt>Incidencias historicas / activas</dt><dd>{estado.incidencias.enTareasEjecutadas??'No disponible'} / {estado.incidencias.enTareasActivas??'No disponible'}</dd></div>
      <div><dt>Lectura institucional al corte</dt><dd>{estado.actualizadoAt}</dd></div></dl>
    <p>La decision del core no modifica la evaluacion normativa del canal ni acredita una firma.</p>
  </section>;
  return <section data-testid="linix-administrativo">
    <h3>Estado LINIX</h3>
    <p role="status">FLUJO CREADO - INCIDENCIA EN REFERENCIAS</p>
    <dl className="ch-detail-list">
      <div><dt>K_FLUJO</dt><dd>{estado.kFlujo}</dd></div>
      <div><dt>SC</dt><dd>Sin identificar. Radicación no acreditada.</dd></div>
      <div><dt>Resultado de actividad 113</dt><dd>Sin consecutivo SC acreditado; la terminación de la tarea no confirma la radicación.</dd></div>
      <div><dt>Actividad</dt><dd>{estado.actividad.nombre}</dd></div>
      <div><dt>Última conciliación</dt><dd>{estado.actualizadoAt}</dd></div>
      <div><dt>START ejecutados</dt><dd>{estado.startEjecutados}</dd></div>
    </dl>
    {estado.detalleTecnico && <details data-testid="linix-error-operador">
      <summary>Detalle técnico para el operador</summary>
      <p>{estado.detalleTecnico.codigo}: {estado.detalleTecnico.mensaje}</p>
      <p className="text-break">{estado.detalleTecnico.procedimiento}</p>
    </details>}
  </section>;
}
