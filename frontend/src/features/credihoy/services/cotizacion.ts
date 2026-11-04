export interface CotizacionInstitucional {
  versionContrato: 'credihoy-cotizacion-institucional-v1'; solicitudId: string; requestId: string;
  versionCondiciones: string | null; fechaConsulta: string; fechaEvaluacion: string | null;
  vigenteHasta: string | null;
  habilitadoDecisionProductiva: false; estado: string; disponible: boolean;
  cuotaInstitucional: string | null; primeraFechaPago: string | null;
  condicionesSolicitadas: null | { monto: string; numeroCuotas: number; tasaNominalNamv: string;
    periodicidad: string; pagaduria: string; tipoPagaduria: string };
  parametrosAplicados: null | { tasaFija: string; tasaVariable: string; P_EXTRAO: string }
    | { periodicidadInterna: string; factorAdicional: string; tasaNominalNamv: string };
  seguroVida: { factorAplicado: string | null; valorPorCuota: null; disponible: false };
  calendario: { disponible: false; cuotas: null }; cotizacionIntegral: false;
  ambiente: null | 'HOMOLOGACION' | 'PRODUCCION'; fuente: 'LINIX_MOTOR_FORMS' | 'LINIX_CALCULO_CUOTA' | 'LINIX_EJECUCION_MANUAL'; camposFaltantes: string[]; motivo?: string;
  evidenciaManual?: { sha256: string; version: string; origen: string; referencia: string;
    fechaRecepcion: string; fechaEjecucion: string | null; fechaInicialObservada: string;
    calendarioObservado: { numero: number; fecha: string; capital: string; IC: string; IV: string; SV: string; total: string }[];
    coberturaContractualCompleta: false; aceptacionAsociado: false; ejecucionAutomaticaHabilitada: false };
  simulacion?: { sesion: string; secuencia: number; tarifa: Record<string, string | number> };
  simuladorDinamico?: { habilitado: boolean; motivo: string | null; limites: null | {
    version: string; referencia: string; modalidad: string; montoMinimo: string; montoMaximo: string;
    cuotasMinimas: number; cuotasMaximas: number; plazosPorMonto?: PlazosPorMonto; aportes?: {
      montoMaximo: string; limiteExacto: string; fechaCorte: string; sujetoACapacidad: true; esCupoAprobado: false } } };
  proyeccionPreliminar?: { decision: 'NO_AUTORIZADA'; reglasReevaluadas: false; condicionesAceptadasModificadas: false;
    capacidad: null | { capacidad: { disponible: boolean; valor: string | null }; reservas: unknown[]; autorizadoDecisionCrediticia: false };
    reglasReferencia: { codigo: string; estado: string }[]; observacion: string };
}
const estados = ['NO_HABILITADA', 'PARAMETROS_FALTANTES', 'NO_DISPONIBLE', 'DISPONIBLE_HOMOLOGACION', 'DISPONIBLE_PARCIAL', 'EVIDENCIA_INSTITUCIONAL_MANUAL',
  'ERROR_TEMPORAL', 'PARAMETROS_EXPEDIENTE_INCOMPLETOS', 'REQUIERE_CONCILIACION', 'DESACTUALIZADA', 'SIN_COTIZACION'];
const decimal = (v: unknown): v is string => typeof v === 'string' && /^\d+(\.\d+)?$/.test(v) && Number.isFinite(Number(v));
const fecha = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)
  && !v.startsWith('0000') && Number.isFinite(Date.parse(v + 'T00:00:00Z'))
  && new Date(v + 'T00:00:00Z').toISOString().slice(0, 10) === v;
const instante = (v: unknown): v is string => typeof v === 'string' && fecha(v.slice(0, 10))
  && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(v)
  && Number.isFinite(Date.parse(v));
// Exact reconciliation of supplied amounts, not a financial formula.
function unidadesObservadas(value: unknown): bigint {
  if (typeof value !== 'string' || !/^\d{1,15}(\.\d{1,8})?$/.test(value)) throw new Error('COTIZACION_RESPUESTA_INVALIDA');
  const [whole, fraction = ''] = value.split('.');
  return BigInt(whole + fraction.padEnd(8, '0'));
}
export function validarCotizacion(raw: unknown, id: string): CotizacionInstitucional {
  const p = raw as CotizacionInstitucional;
  const manual = p?.fuente === 'LINIX_EJECUCION_MANUAL';
  const scalar = p?.fuente === 'LINIX_CALCULO_CUOTA' || manual;
  const params = p?.parametrosAplicados;
  if (!p || p.versionContrato !== 'credihoy-cotizacion-institucional-v1' || p.solicitudId !== id
      || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(p.requestId) || p.habilitadoDecisionProductiva !== false
      || !estados.includes(p.estado) || typeof p.disponible !== 'boolean'
      || p.cotizacionIntegral !== false || !['LINIX_MOTOR_FORMS', 'LINIX_CALCULO_CUOTA', 'LINIX_EJECUCION_MANUAL'].includes(p.fuente)
      || !instante(p.fechaConsulta) || Date.parse(p.fechaConsulta) > Date.now()
      || (p.versionCondiciones !== null && !/^[a-f0-9]{64}$/.test(p.versionCondiciones))
      || (p.disponible ? p.estado !== (manual ? 'EVIDENCIA_INSTITUCIONAL_MANUAL' : scalar ? 'DISPONIBLE_PARCIAL' : 'DISPONIBLE_HOMOLOGACION')
          || (manual ? p.ambiente !== null || p.vigenteHasta !== null
            : !(scalar ? ['PRODUCCION', 'HOMOLOGACION'] : ['HOMOLOGACION']).includes(p.ambiente ?? '')
              || !instante(p.vigenteHasta) || Date.parse(p.vigenteHasta) <= Date.now())
          || !decimal(p.cuotaInstitucional) || !p.parametrosAplicados : p.cuotaInstitucional !== null)
      || (p.vigenteHasta !== null && !instante(p.vigenteHasta))
      || (!p.disponible && ['DISPONIBLE_HOMOLOGACION', 'DISPONIBLE_PARCIAL', 'EVIDENCIA_INSTITUCIONAL_MANUAL'].includes(p.estado))
      || (scalar && ((!manual && p.primeraFechaPago !== null) || p.seguroVida?.factorAplicado !== null
          || (p.disponible && Number(p.cuotaInstitucional) <= 0)
          || (params !== null && (!('periodicidadInterna' in params)
              || Object.keys(params).sort().join(',') !== 'factorAdicional,periodicidadInterna,tasaNominalNamv'
              || params.periodicidadInterna !== '1' || params.factorAdicional !== '0'
              || !decimal(params.tasaNominalNamv)
              || unidadesObservadas(params.tasaNominalNamv) !== unidadesObservadas(p.condicionesSolicitadas?.tasaNominalNamv)))))
      || !p.seguroVida || p.seguroVida.disponible !== false || p.seguroVida.valorPorCuota !== null
      || (p.seguroVida.factorAplicado !== null && !decimal(p.seguroVida.factorAplicado))
      || !p.calendario || p.calendario.disponible !== false || p.calendario.cuotas !== null
      || (p.primeraFechaPago !== null && !(fecha(p.primeraFechaPago) || instante(p.primeraFechaPago)))
      || !Array.isArray(p.camposFaltantes) || p.camposFaltantes.some(v => typeof v !== 'string')) {
    throw new Error('COTIZACION_RESPUESTA_INVALIDA');
  }
  if (manual) {
    const e = p.evidenciaManual;
    if (!e || !/^[a-f0-9]{64}$/.test(e.sha256) || e.version !== 'credihoy-cuota-linix-manual-v1'
        || e.origen !== 'DECLARACION_RESPONSABLE_Y_CAPTURA_DBEAVER'
        || e.coberturaContractualCompleta !== false || e.aceptacionAsociado !== false
        || e.ejecucionAutomaticaHabilitada !== false || !fecha(e.fechaRecepcion) || !fecha(e.fechaInicialObservada)
        || (e.fechaEjecucion !== null && (!instante(e.fechaEjecucion) || Date.parse(e.fechaEjecucion) > Date.now()))
        || !Array.isArray(e.calendarioObservado) || e.calendarioObservado.length !== p.condicionesSolicitadas?.numeroCuotas
        || !e.calendarioObservado.length || e.calendarioObservado[0].fecha !== p.primeraFechaPago
        || e.calendarioObservado.some(r => !fecha(r.fecha)
            || ![r.capital, r.IC, r.IV, r.SV, r.total].every(decimal))) {
      throw new Error('COTIZACION_RESPUESTA_INVALIDA');
    }
    let previous = e.fechaInicialObservada, capital = 0n;
    e.calendarioObservado.forEach((row, index) => {
      const parts = [row.capital, row.IC, row.IV, row.SV].map(unidadesObservadas);
      if (Object.keys(row).sort().join(',') !== 'IC,IV,SV,capital,fecha,numero,total'
          || row.numero !== index + 1 || row.fecha <= previous
          || unidadesObservadas(row.total) !== unidadesObservadas(p.cuotaInstitucional)
          || parts.reduce((sum, value) => sum + value, 0n) !== unidadesObservadas(row.total)) {
        throw new Error('COTIZACION_RESPUESTA_INVALIDA');
      }
      previous = row.fecha; capital += parts[0];
    });
    if (capital !== unidadesObservadas(p.condicionesSolicitadas?.monto)) throw new Error('COTIZACION_RESPUESTA_INVALIDA');
  }
  if (p.simulacion !== undefined && (!/^[a-f0-9]{64}$/.test(p.simulacion.sesion)
      || !Number.isSafeInteger(p.simulacion.secuencia) || p.simulacion.secuencia < 1
      || !p.simulacion.tarifa || manual)) throw new Error('COTIZACION_RESPUESTA_INVALIDA');
  if (p.proyeccionPreliminar !== undefined && (p.proyeccionPreliminar.decision !== 'NO_AUTORIZADA'
      || p.proyeccionPreliminar.reglasReevaluadas !== false || p.proyeccionPreliminar.condicionesAceptadasModificadas !== false
      || (p.proyeccionPreliminar.capacidad && (p.proyeccionPreliminar.capacidad.autorizadoDecisionCrediticia !== false
        || !Array.isArray(p.proyeccionPreliminar.capacidad.reservas)
        || (p.proyeccionPreliminar.capacidad.capacidad.disponible
          && (typeof p.proyeccionPreliminar.capacidad.capacidad.valor !== 'string'
            || !/^-?\d+(\.\d+)?$/.test(p.proyeccionPreliminar.capacidad.capacidad.valor))))))) {
    throw new Error('COTIZACION_RESPUESTA_INVALIDA');
  }
  return p;
}

export interface EscenarioCotizacion { monto: string; plazoMeses: number; secuencia: number }

export interface PlazosPorMonto {
  version: string; plazoMinimo: number; smmlv: { valor: string; disponible: boolean };
  rangosSMMLV: { desde: string; hasta: string; incluyeDesde: boolean; incluyeHasta: boolean; plazoMaximo: number }[];
}
export function plazoMaximoDelMonto(monto: string, reglas?: PlazosPorMonto): number | null {
  if (!reglas) return null;
  try {
    if (!reglas.smmlv.disponible) return null;
    const amount=unidadesObservadas(monto), wage=unidadesObservadas(reglas.smmlv.valor), scale=100000000n;
    if (amount<=0n || wage<=0n) return null;
    const hits=reglas.rangosSMMLV.filter(r => {
      const lower=wage*unidadesObservadas(r.desde), upper=wage*unidadesObservadas(r.hasta), value=amount*scale;
      if (lower>=upper || !Number.isSafeInteger(r.plazoMaximo) || r.plazoMaximo<1 || r.plazoMaximo>48) throw new Error();
      return (r.incluyeDesde ? value>=lower : value>lower) && (r.incluyeHasta ? value<=upper : value<upper);
    });
    return hits.length ? Math.min(...hits.map(r=>r.plazoMaximo)) : null;
  } catch { return null; }
}

export function escenarioDentroDeLimites(monto: string, cuotas: number,
  limites: NonNullable<NonNullable<CotizacionInstitucional['simuladorDinamico']>['limites']>): boolean {
  try {
    if (!/^\d{1,10}(\.\d{1,2})?$/.test(monto) || !Number.isSafeInteger(cuotas)
      || cuotas < 1 || cuotas > 48 || cuotas < limites.cuotasMinimas || cuotas > limites.cuotasMaximas
      || (limites.plazosPorMonto && (plazoMaximoDelMonto(monto,limites.plazosPorMonto)===null
        || cuotas>plazoMaximoDelMonto(monto,limites.plazosPorMonto)!))) return false;
    const amount = unidadesObservadas(monto);
    return amount > 0n && amount >= unidadesObservadas(limites.montoMinimo)
      && amount <= unidadesObservadas(limites.montoMaximo)
      && (!limites.aportes || amount <= unidadesObservadas(limites.aportes.montoMaximo));
  } catch { return false; }
}

// Owns display freshness, not money calculation or retry policy.
export class ControlEscenariosCotizacion {
  private revision = 0;
  private secuencia: number;
  private sesion: string | undefined;
  private limites: NonNullable<CotizacionInstitucional['simuladorDinamico']>['limites'] | undefined;
  constructor(private id: string, inicial?: CotizacionInstitucional) {
    this.secuencia = inicial?.simulacion?.secuencia ?? 0;
    this.sesion = inicial?.simulacion?.sesion;
    this.limites = inicial?.simuladorDinamico?.limites;
  }
  invalidar() { this.revision++; }
  async cotizar(monto: string, plazoMeses: number,
    enviar: (escenario: EscenarioCotizacion) => Promise<CotizacionInstitucional>): Promise<CotizacionInstitucional | null> {
    if (!/^\d{1,10}(\.\d{1,2})?$/.test(monto) || !Number.isSafeInteger(plazoMeses) || plazoMeses < 1 || plazoMeses > 48)
      throw new Error('ESCENARIO_FUERA_DE_LIMITES');
    if (this.limites && !escenarioDentroDeLimites(monto,plazoMeses,this.limites)) throw new Error('ESCENARIO_FUERA_DE_LIMITES');
    const revision = ++this.revision, escenario = { monto, plazoMeses, secuencia: ++this.secuencia };
    const p = await enviar(escenario);
    if (revision !== this.revision) return null;
    if (p.solicitudId !== this.id || p.simulacion?.secuencia !== escenario.secuencia
        || !p.simulacion.sesion || (this.sesion !== undefined && this.sesion !== p.simulacion.sesion)
        || Number(p.condicionesSolicitadas?.monto) !== Number(monto)
        || p.condicionesSolicitadas?.numeroCuotas !== plazoMeses) throw new Error('ESCENARIO_RESPUESTA_INCOMPATIBLE');
    this.sesion = p.simulacion.sesion;
    return p;
  }
}
