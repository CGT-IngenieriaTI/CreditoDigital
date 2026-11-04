// Only synthetic data. No real expediente, operator credentials or provider calls.
const fs = require('node:fs'), path = require('node:path'), ts = require('typescript'), Module = require('node:module');
const root = path.resolve(__dirname, '../../src/features/credihoy'), cache = new Map();
function load(name) {
  const candidate = path.resolve(root, /\.tsx?$/.test(name) ? name : name + '.ts');
  const file = fs.existsSync(candidate) ? candidate : candidate + 'x';
  if (cache.has(file)) return cache.get(file).exports;
  const m = new Module(file); cache.set(file, m);
  m.require = target => target.endsWith('.css') ? {} : target.endsWith('.png') ? 'asset-sintetico.png' : target.startsWith('.') ? load(path.relative(root, path.resolve(path.dirname(file), target))) : require(target);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8').replace(/import\.meta\.env\.DEV/g, 'false'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText, file);
  return m.exports;
}
const id = '00000000-0000-4000-8000-000000000008';
function fixture() {
  const states = [...Array(5).fill('CUMPLE'), ...Array(3).fill('PENDIENTE_POLITICA'), ...Array(7).fill('NO_EVALUABLE'), 'INACTIVA'];
  const reglas = load('services/shadow').CODIGOS_REGLAS_SHADOW.map((codigo, i) => ({ codigo,
    nombre: 'Validacion sintetica ' + (i + 1), estado: states[i], obligatoria: null, excluyente: null,
    motivos: ['EVIDENCIA_SINTETICA'], datosFaltantes: states[i] === 'NO_EVALUABLE' ? ['hecho_sintetico'] : [], activaEnInventario: states[i] !== 'INACTIVA' }));
  const linix = { versionContrato: 'credihoy-linix-estado-v1', solicitudId: id, intencionId: id,
    kFlujo: '12345', numeroSc: null, estado: 'FLUJO_CREADO_INCIDENCIA_REFERENCIAS',
    actividad: { codigo: 3883, nombre: 'REGISTRAR REFERENCIAS SOLICITUD' },
    actualizadoAt: '2026-10-15T09:00:00-05:00', startEjecutados: 1,
    detalleTecnico: { codigo: 'ORA-20000', procedimiento: 'PK_CA_WF_REFERENCIA.PR_INSERTA_REFER_SOLICITUD',
      mensaje: 'Leyendo Variables Trama', erroresRegistrados: 2 } };
  return { versionContrato: 'credihoy-estado-credito-v1', solicitudId: id, soloLectura: true,
    asociado: { estado: 'REQUIERE_REVISION', tituloEstado: 'En revision', resumen: { producto: 'CrediHoy Unico',
      codigoProducto: '390', modalidad: '111', monto: '900000.00', plazoMeses: 3, periodicidad: 'MENSUAL',
      cuotaSimulada: { disponible: true, valor: '320000.00', fuente: 'SIMULACION_OFICIAL_LINIX', fecha: '2026-10-14T17:00:00-05:00' } },
      capacidad: 'Por determinar', decision: 'Sin aprobacion', score: null,
      pendientes: ['Faltan datos acreditados para determinar la capacidad de pago.', 'Credito debe ratificar la politica vigente.'],
      proximaAccion: 'Credito revisara los datos pendientes. No envies otra solicitud.',
      linix: 'INCIDENCIA_TECNICA', consentimientoFirmado: true, hdcCompletada: true },
    operador: { elegibilidad: { version: 'credihoy-elegibilidad-v1', estado: 'REQUIERE_REVISION', elegible: false,
      modo: 'ORDINARIA', esAprobacion: false, versionPolitica: null,
      causales: [{ codigo: 'MATRIZ_POLITICA_VIGENTE_AUTORIZADA_AUSENTE', categoria: 'REVISION', regla: null },
        { codigo: 'EXPEDIENTE_CON_INTENCION_O_START_PREVIO', categoria: 'REVISION', regla: null }], reglas },
      historial: [{ id: 'evaluacion_sintetica', fecha: '2026-10-14T17:00:00-05:00', capacidad: 'NO_EVALUABLE', decision: 'NO_AUTORIZADA', activa: true }],
      linix, evaluacion: { capacidad: 'NO_EVALUABLE', decision: 'NO_AUTORIZADA', fecha: '2026-10-14T17:00:00-05:00' },
      modoLinix: 'PRUEBA_ADMINISTRATIVA', transporteOrdinarioHabilitado: false, perspectivaAsociadoAsistida: true } };
}
function functionalFixture() {
  const cd8 = fixture(), money = () => ({ disponible: false, valor: null });
  return { versionContrato: 'credihoy-resultado-funcional-v1', solicitudId: id, idempotencyKey: id,
    solicitud: { monto: cd8.asociado.resumen.monto, plazoMeses: 3, periodicidadPago: 'MENSUAL', recogerCredihoy: false }, datosGuardados: true,
    consentimiento: { estado: 'CONSENTIMIENTO_FIRMADO', version: 'CREDIHOY-REAL-1', fechaFirma: '2026-10-14T12:00:00-05:00' },
    estadoObtencionDatos: [{ fuente: 'HDC', estado: 'DISPONIBLE_EN_CONTEXTO_NO_APROBACION' },
      { fuente: 'LINIX', estado: 'INGRESO_COMPUTABLE_Y_HECHOS_GLOBALES_PENDIENTES' },
      { fuente: 'POLITICA', estado: 'CONDICION_Y_EFECTOS_CAPACIDAD_PENDIENTES' }],
    estadoTramite: 'PENDIENTE_REVISION', decisionCrediticia: 'NO_AUTORIZADA', autorizadoDecisionCrediticia: false,
    radicacionLinix: 'NO_EJECUTADA', estadoLinix: cd8.operador.linix, estadoCredito: cd8,
    resultado: { versionContrato: 'credihoy-shadow-lab-v1', versionFuncional: 'credihoy-resultado-funcional-v1',
      solicitudId: id, requestId: id, fechaEvaluacion: '2026-10-14T17:00:00-05:00', fuenteHechos: 'EXPEDIENTE_OPERATIVO_SIN_DECISION',
      capacidadMatematica: { ...money(), estado: 'NO_EVALUABLE' },
      resultadoCapacidad: { estado: 'NO_EVALUABLE', motivos: ['DATOS_SINTETICOS_PENDIENTES'], referenciaPolitica: null },
      cuotaParaCapacidad: money(), gastosFamiliares: money(), cuotaContractual: money(),
      informacionInsuficiente: ['ingresos_mensuales'], reglasPendientes: [],
      reglas: cd8.operador.elegibilidad.reglas.map(r => ({ codigo: r.codigo, nombre: r.nombre,
        resultado: ['CUMPLE', 'NO_CUMPLE'].includes(r.estado) ? r.estado : 'NO_EVALUABLE', efecto: 'PENDIENTE_DEFINICION', motivos: r.motivos })),
      recogida: { estado: 'NO_APLICA', motivos: [], observacionAplicacion: null },
      conclusionFuncional: 'NO_DETERMINADA', decisionCrediticia: 'NO_AUTORIZADA', autorizadoDecisionCrediticia: false,
      radicacionLinix: 'NO_EJECUTADA', estadoTramite: 'PENDIENTE_REVISION', revisionHumanaHabilitada: false,
      secuencia: ['SOLICITUD', 'DATOS', 'CAPACIDAD', 'REGLAS', 'RESULTADO', 'ESTADO'].map(paso => ({ paso, estado: 'NO_EVALUABLE' })) } };
}
function publicInstitutionalFixture() {
  return {referencia:id,titulo:'Actualiza tus datos',monto:'400000',plazoMeses:2,periodicidad:'Mensual',
    fechaCreacion:'2026-10-20T12:00:00Z',requisitosCumplidos:11,
    pendientes:['Debes actualizar tus datos para continuar con tu solicitud.'],
    siguientePaso:'Actualiza tus datos con Congente.',ofertaDisponible:false,acciones:{radicar:false,firmar:false},
    simulacion:{cuota:'123456',tasaNominalNamv:'16.67',origen:'REFERENCIA_INSTITUCIONAL_REGISTRADA',
      ofertaContractual:false,seguroDisponible:false,calendarioDisponible:false},
    institucional:{estado:'APROBADA_PENDIENTE_FIRMA',fechaDecision:'2026-10-23',actualizadoAt:new Date(Date.now()-1000).toISOString(),
      documentos:'NO_ACREDITADOS',firma:'PENDIENTE',incidencias:'ANTECEDENTES_SIN_PENDIENTE_ACTIVO'}};
}
module.exports = { fixture, functionalFixture, publicInstitutionalFixture, load, id };
