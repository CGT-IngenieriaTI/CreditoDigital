import { useEffect, useRef, useState } from 'react';
import { HttpConsentimientoReal, ErrorFirmaReal } from '../services/consentimientoReal';
import type { CotizacionInstitucional } from '../services/cotizacion';
import { ControlEscenariosCotizacion, escenarioDentroDeLimites, plazoMaximoDelMonto } from '../services/cotizacion';
import { moneda } from './FormControls';

const mensajes: Record<string, string> = {
  NO_HABILITADA: 'Cotizacion institucional no habilitada.',
  PARAMETROS_FALTANTES: 'Faltan parametros institucionales acreditados.',
  NO_DISPONIBLE: 'No se dispone de una cuota institucional para estas condiciones.',
  DISPONIBLE_HOMOLOGACION: 'Resultado de homologacion. No es una cotizacion contractual productiva.',
  DISPONIBLE_PARCIAL: 'Cuota calculada por LINIX. Seguro, cargos y calendario contractual no certificados.',
  EVIDENCIA_INSTITUCIONAL_MANUAL: 'Resultado de prueba institucional manual. No es una oferta contractual aceptada ni habilita ejecucion automatica.',
  ERROR_TEMPORAL: 'Resultado de ejecucion no confirmado. Requiere conciliacion tecnica.',
  PARAMETROS_EXPEDIENTE_INCOMPLETOS: 'Faltan condiciones verificadas del expediente o seleccion comercial vigente.',
  REQUIERE_CONCILIACION: 'No se repetira el calculo: el resultado requiere conciliacion.',
  DESACTUALIZADA: 'Las condiciones cambiaron; la cotizacion anterior no es aplicable.',
  SIN_COTIZACION: 'Sin cotizacion institucional vinculada a estas condiciones.',
};
export function numeroCotizacion(value: string, currency = false): string {
  if (!/^\d+(\.\d+)?$/.test(value)) return 'No disponible';
  const [whole, fraction] = value.split('.');
  const pesos = new Intl.NumberFormat('es-CO', {
    ...(currency ? { style: 'currency', currency: 'COP' } : {}), maximumFractionDigits: 0,
  }).format(BigInt(whole));
  return fraction === undefined ? pesos : pesos + ',' + fraction;
}
export const importeCotizacion = (value: string) => numeroCotizacion(value, true);
export function CredihoyCotizacion({ solicitudId, tecnico = true, onResult, permitirDinamica = false }: {
  solicitudId: string; tecnico?: boolean; onResult?: (value: CotizacionInstitucional | null) => void; permitirDinamica?: boolean;
}) {
  const api = useRef(new HttpConsentimientoReal()), lock = useRef(false);
  const [result, setResult] = useState<CotizacionInstitucional | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [incierta, setIncierta] = useState(false);
  const control = useRef<ControlEscenariosCotizacion | null>(null);
  const [dinamicaActiva,setDinamicaActiva] = useState(false), [dinamica,setDinamica] = useState<CotizacionInstitucional | null>(null);
  const [monto,setMonto] = useState(''), [cuotas,setCuotas] = useState('');
  const [preparado,setPreparado] = useState(false), [calculando,setCalculando] = useState(false), [errorDinamico,setErrorDinamico] = useState('');
  const limits = result?.simuladorDinamico?.limites;
  const plazoMonto=limits?.plazosPorMonto ? plazoMaximoDelMonto(monto,limits.plazosPorMonto) : limits?.cuotasMaximas;
  const habilitada = permitirDinamica && result?.simuladorDinamico?.habilitado && limits && result.condicionesSolicitadas;
  useEffect(() => {
    if (!dinamica?.disponible || !dinamica.vigenteHasta) return;
    const timer=setTimeout(() => setDinamica(p => p ? { ...p, disponible:false,
      cuotaInstitucional:null, proyeccionPreliminar:undefined, estado:'DESACTUALIZADA' } : p),
      Math.min(2147483647,Math.max(0,Date.parse(dinamica.vigenteHasta)-Date.now())));
    return () => clearTimeout(timer);
  },[dinamica]);
  useEffect(() => {
    if (!habilitada || control.current) return;
    let active = true;
    api.current.simularCuota(solicitudId).then(p => {
      if (!active) return;
      control.current = new ControlEscenariosCotizacion(solicitudId,p);
      setMonto(result!.condicionesSolicitadas!.monto);setCuotas(String(result!.condicionesSolicitadas!.numeroCuotas));setPreparado(true);
    }).catch(() => { if (active) setErrorDinamico('No se pudo recuperar la sesion del simulador. No se ejecutara un calculo.'); });
    return () => { active = false; };
  },[Boolean(habilitada),solicitudId]);
  useEffect(() => {
    if (!dinamicaActiva || !control.current || !limits) return;
    let active=true;
    setDinamica(null);setErrorDinamico('');setCalculando(false);
    if (!/^\d+$/.test(cuotas) || !escenarioDentroDeLimites(monto,Number(cuotas),limits)) {
      setErrorDinamico('Monto o plazo fuera de las reglas institucionales vigentes.');return;
    }
    const timer=setTimeout(() => {
      setCalculando(true);
      control.current!.cotizar(monto,Number(cuotas),p => api.current.simularCuota(solicitudId,p)).then(p => {
        if (active && p) setDinamica(p);
      }).catch(() => { if (active) setErrorDinamico('Resultado no confirmado. Recupera el estado; no se repetira automaticamente.'); })
        .finally(() => { if (active) setCalculando(false); });
    },600);
    return () => { active=false;clearTimeout(timer);control.current?.invalidar(); };
  },[dinamicaActiva,monto,cuotas,solicitudId]);
  useEffect(() => { onResult?.(result); }, [result, onResult]);
  useEffect(() => {
    let active = true;
    api.current.cotizacion(solicitudId).then(p => { if (active) setResult(p); })
      .catch(() => { if (active) setError('Cotizacion no habilitada o no disponible para esta sesion.'); });
    return () => { active = false; };
  }, [solicitudId]);
  useEffect(() => {
    if (!result?.disponible || !result.vigenteHasta) return;
    const timer = setTimeout(() => setResult(p => p ? { ...p, disponible: false,
      cuotaInstitucional: null, primeraFechaPago: null, parametrosAplicados: null,
      seguroVida: {factorAplicado: null, valorPorCuota: null, disponible: false}, estado: 'DESACTUALIZADA' } : p),
      Math.min(2147483647, Math.max(0, Date.parse(result.vigenteHasta) - Date.now())));
    return () => clearTimeout(timer);
  }, [result]);
  async function consultar(ejecutar: boolean) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const p = await api.current.cotizacion(solicitudId, ejecutar);
      setResult(p); setIncierta(p.estado === 'REQUIERE_CONCILIACION' || p.estado === 'ERROR_TEMPORAL');
    } catch (e) {
      if (ejecutar) setIncierta(true);
      setError(e instanceof ErrorFirmaReal && e.codigo === 'SESION_REQUERIDA'
        ? 'Recupera tu sesion institucional.' : 'Operacion no confirmada. Recupera el estado; no se repetira automaticamente.');
    } finally { lock.current = false; setBusy(false); }
  }
  const bloqueada = incierta || !result || result.disponible ||
    ['NO_HABILITADA', 'NO_DISPONIBLE', 'PARAMETROS_FALTANTES',
      'REQUIERE_CONCILIACION', 'ERROR_TEMPORAL', 'PARAMETROS_EXPEDIENTE_INCOMPLETOS'].includes(result.estado);
  const controlesDinamicos = habilitada && <fieldset className="ch-credit-band">
    <legend>Escenario de simulacion</legend>
    <div className="ch-credit-metrics">
      <label>Monto (COP)<input className="form-control" inputMode="decimal" value={monto} onChange={e => {
        control.current?.invalidar();setDinamica(null);setMonto(e.target.value);
      }} /></label>
      <label>Numero de cuotas<input className="form-control" type="number" min={limits.cuotasMinimas}
        max={Math.min(limits.cuotasMaximas,plazoMonto ?? 1)} disabled={plazoMonto===null}
        value={cuotas} onChange={e => { control.current?.invalidar();setDinamica(null);setCuotas(e.target.value); }} /></label>
    </div>
    <p>Modalidad 111. Mensual. Tasa comercial vigente: {numeroCotizacion(result!.condicionesSolicitadas!.tasaNominalNamv)} % N.A.M.V.</p>
    <p>Reglas del producto: {limits.version}. Este escenario no modifica las condiciones aceptadas de la solicitud.</p>
    {!dinamicaActiva && <button className="btn btn-outline-primary" type="button" disabled={!preparado}
      onClick={() => setDinamicaActiva(true)}>Calcular escenario con LINIX</button>}
  </fieldset>;
  if (dinamicaActiva) return <section className="ch-credit-band" data-testid="simulador-dinamico">
    <h3>Simulacion dinamica LINIX</h3>{controlesDinamicos}
    <p role="status">{calculando ? 'Calculando en LINIX...' : dinamica ? mensajes[dinamica.estado] : 'Sin una cuota calculada para este escenario.'}</p>
    {errorDinamico && <p role="alert">{errorDinamico}</p>}
    <dl className="ch-credit-metrics ch-quote-summary">
      <div><dt>Cuota simulada mensual</dt><dd>{dinamica?.disponible ? importeCotizacion(dinamica.cuotaInstitucional!) : 'No disponible'}</dd></div>
      <div><dt>Capacidad preliminar del escenario</dt><dd>{dinamica?.proyeccionPreliminar?.capacidad?.capacidad.disponible
        ? moneda(dinamica.proyeccionPreliminar.capacidad.capacidad.valor!) : 'No disponible'}</dd></div>
    </dl>
    <p>Seguro, otros cargos y calendario contractual no certificados. No constituye oferta ni aprobacion.</p>
    {dinamica?.proyeccionPreliminar && <>
      <p>{dinamica.proyeccionPreliminar.observacion}</p>
      <p>Partidas pendientes de deuda: {dinamica.proyeccionPreliminar.capacidad?.reservas.length ?? 'No disponible'}.</p>
      <p>Evaluacion y estado: sin autorizacion crediticia. Las reglas de referencia no se recalifican por simular.</p>
    </>}
    <button className="btn btn-outline-secondary" type="button" onClick={() => {
      control.current?.invalidar();setDinamicaActiva(false);setDinamica(null);
    }}>Volver a la referencia del expediente</button>
  </section>;
  return <section className="ch-credit-band" aria-labelledby="cotizacion-institucional" data-testid="cotizacion-institucional">
    <h3 id="cotizacion-institucional">{tecnico ? 'Cotizacion institucional' : 'Simulacion de referencia'}</h3>
    {controlesDinamicos}
    {errorDinamico && !dinamicaActiva && <p role="alert">{errorDinamico}</p>}
    <p role="status">{result ? mensajes[result.estado] : 'Sin resultado disponible.'}</p>
    {error && <p role="alert">{error}</p>}
    <dl className="ch-credit-metrics ch-quote-summary">
      <div><dt>Monto solicitado</dt><dd>{result?.condicionesSolicitadas ? importeCotizacion(result.condicionesSolicitadas.monto) : 'No disponible'}</dd></div>
      <div><dt>Numero de cuotas</dt><dd>{result?.condicionesSolicitadas?.numeroCuotas ?? 'No disponible'}</dd></div>
      <div><dt>Periodicidad</dt><dd>{result?.condicionesSolicitadas?.periodicidad === 'MENSUAL' ? 'Mensual' : 'No disponible'}</dd></div>
      <div><dt>{result?.fuente === 'LINIX_CALCULO_CUOTA' || result?.fuente === 'LINIX_EJECUCION_MANUAL' ? 'Cuota calculada por LINIX' : 'Cuota institucional'}</dt><dd>{result?.disponible ? importeCotizacion(result.cuotaInstitucional!) : 'No disponible'}</dd></div>
      <div><dt>Tasa N.A.M.V.</dt><dd>{result?.condicionesSolicitadas ? numeroCotizacion(result.condicionesSolicitadas.tasaNominalNamv)+' %' : 'No disponible'}</dd></div>
    </dl>
    {result?.fuente === 'LINIX_EJECUCION_MANUAL' && <p className="ch-credit-caption">Referencia institucional manual LINIX, exclusiva de las condiciones y del expediente consultado. No se recalcula automaticamente ni constituye oferta contractual.</p>}
    {tecnico && <><dl className="ch-credit-metrics ch-credit-band">
      <div><dt>Primera fecha devuelta</dt><dd>{result?.primeraFechaPago?.slice(0, 10) ?? 'No disponible'}</dd></div>
      <div><dt>Factor de seguro aplicado</dt><dd>{result?.seguroVida.factorAplicado ?? 'No disponible'}</dd></div>
      <div><dt>Seguro monetario por cuota</dt><dd>No disponible</dd></div>
      <div><dt>Calendario integral</dt><dd>No certificado</dd></div>
    </dl>
    {result?.evidenciaManual && <>
      <p>Calendario observado de prueba; vigencia contractual no acreditada. Fecha de ejecucion: {result.evidenciaManual.fechaEjecucion ?? 'No certificada'}. Evidencia: {result.evidenciaManual.sha256.slice(0, 12)}.</p>
      <div className="table-responsive"><table className="table table-sm">
        <caption>Composicion observada en LINIX; cobertura de otros cargos no certificada.</caption>
        <thead><tr><th>Cuota</th><th>Fecha</th><th>Capital</th><th>IC</th><th>IV</th><th>SV</th><th>Total</th></tr></thead>
        <tbody>{result.evidenciaManual.calendarioObservado.map(row => <tr key={row.numero}>
          <td>{row.numero}</td><td>{row.fecha}</td><td>{importeCotizacion(row.capital)}</td><td>{importeCotizacion(row.IC)}</td>
          <td>{importeCotizacion(row.IV)}</td><td>{importeCotizacion(row.SV)}</td><td>{importeCotizacion(row.total)}</td>
        </tr>)}</tbody>
      </table></div>
    </>}
    {result?.camposFaltantes.length ? <p>Parametros pendientes: {result.camposFaltantes.join(', ')}</p> : null}
    {result?.versionCondiciones && <p className="ch-credit-caption">Version de condiciones: {result.versionCondiciones.slice(0, 12)}</p>}
    </>}
    <div className="d-flex flex-wrap gap-2 ch-quote-actions">
      {tecnico &&
      <button className="btn btn-outline-primary" type="button" disabled={busy || bloqueada} onClick={() => void consultar(true)}>
        {busy ? 'Consultando...' : 'Obtener cotizacion institucional'}</button>}
      <button className="btn btn-outline-secondary" type="button" disabled={busy} onClick={() => void consultar(false)}>Recuperar cotizacion guardada</button>
    </div>
  </section>;
}
