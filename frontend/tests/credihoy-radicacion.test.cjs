const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load,id}=require('./helpers/cd8-fixture.cjs');
const {validarRadicacionTitular,validarCotizacionTitular,validarSeguimientoRadicacion}=load('services/publico');
const {RadicacionPersonal}=load('asociado/PortalAsociado.tsx');
function packet(){return {versionContrato:'credihoy-radicacion-personal-v1',solicitudId:id,intencionId:id,
  estado:'SIN_INTENCION',codigo:null,puedeConfirmar:false,presentacion:null,K_FLUJO:null,SC:null,
  actividadesActivas:[],decisionInstitucional:'PENDIENTE',firmaContractual:'PENDIENTE',autorizaFirma:false,autorizaDesembolso:false};}

test('textos visibles omiten proveedores sin cambiar evidencia ni acreditar firma pendiente',()=>{
  const {textoEventoHistorial,DocumentosAsociado}=load('asociado/PortalAsociado.tsx');
  for(const codigo of ['FORMALIZACION','PAGARE'])for(const estado of ['PENDIENTE','CONFIRMADO']){
    const event={codigo,estado,fecha:null,titulo:'AutenTIC LINPAG',descripcion:'AutenTIC LINPAG'};
    const original=JSON.stringify(event),visible=textoEventoHistorial(event);
    assert.doesNotMatch(JSON.stringify(visible),/AutenTIC|LINPAG|LINIX/i);
    assert.equal(JSON.stringify(event),original);
    if(codigo==='FORMALIZACION'&&estado==='PENDIENTE')assert.match(visible.descripcion,/pendiente/);
  }
  assert.doesNotMatch(renderToStaticMarkup(React.createElement(DocumentosAsociado,{})),/AutenTIC|LINPAG|LINIX/i);
});
test('radicacion mantiene decision y firma pendientes y rechaza cruces o facultades financieras',()=>{
  assert.equal(validarRadicacionTitular(packet(),id).estado,'SIN_INTENCION');
  for(const changes of [{solicitudId:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'},{autorizaFirma:true},{autorizaDesembolso:true},
    {puedeConfirmar:true},{decisionInstitucional:'APROBADA'},{SC:'123',K_FLUJO:null}])
    assert.throws(()=>validarRadicacionTitular({...packet(),...changes},id));
});
test('preparada requiere presentacion, incertidumbre no permite confirmar',()=>{
  const calendar={diaPago:10,primerVencimiento:'2026-12-10',versionRegla:'CREDIHOY_DIA_PAGO_30_CALENDARIO_20261028_V2',tipoBase:'PROYECTADO',definitivo:false};
  assert.equal(validarRadicacionTitular({...packet(),estado:'PREPARADA',puedeConfirmar:true,presentacion:'signed',calendarioPropuesto:calendar},id).puedeConfirmar,true);
  for(const diaPago of [0,21,true,'10'])assert.throws(()=>validarRadicacionTitular({...packet(),calendarioPropuesto:{...calendar,diaPago}},id));
  assert.throws(()=>validarRadicacionTitular({...packet(),estado:'PREPARADA',puedeConfirmar:true,presentacion:'signed'},id));
  assert.throws(()=>validarRadicacionTitular({...packet(),estado:'INCIERTA',puedeConfirmar:true,presentacion:'signed'},id));
});
test('pantalla inicial exige accion personal y nunca inicia transporte al renderizar',()=>{
  let calls=0;const html=renderToStaticMarkup(React.createElement(RadicacionPersonal,{sid:id,operar:async()=>{calls++;return packet();}}));
  assert.match(html,/Verificando el estado/);assert.doesNotMatch(html,/Confirmar radicacion|Radicar mi solicitud/);assert.equal(calls,0);
});
test('seguimiento persistido dirige al estado institucional y nunca solicita otra aceptacion o radicacion',()=>{
  const t={versionContrato:'credihoy-seguimiento-radicacion-v1',estado:'RADICADA',confirmada:true,
    consultadoEn:new Date().toISOString(),cuotaAceptada:'204448',aceptadaEn:'2026-11-07T21:00:28+00:00',decisionInstitucional:'PENDIENTE',firmaContractual:'PENDIENTE'};
  assert.equal(validarSeguimientoRadicacion(t).confirmada,true);
  for(const bad of [{estado:'PREPARADA'},{SC:'123'},{decisionInstitucional:'APROBADA'},{cuotaAceptada:'0'}])assert.throws(()=>validarSeguimientoRadicacion({...t,...bad}));
  const {resolverSiguienteAccion}=load('asociado/siguienteAccion');
  const action=resolverSiguienteAccion({seguimientoRadicacion:t});
  assert.equal(action.estado,'COMPLETADO');assert.equal(action.destino,null);assert.match(action.mensaje,/revision institucional/);
});
test('historial conserva fechas originales y rechaza aprobacion o desembolso no acreditados',()=>{
  const {publicInstitutionalFixture}=require('./helpers/cd8-fixture.cjs');
  const {validarSolicitudPublica}=load('services/publico');
  const now=Date.now(),created=new Date(now-7*86400000).toISOString(),accepted=new Date(now-2*86400000).toISOString(),registered=new Date(now-86400000).toISOString();
  const s={...publicInstitutionalFixture(),institucional:null,fechaCreacion:created,
    seguimientoRadicacion:{versionContrato:'credihoy-seguimiento-radicacion-v1',estado:'RADICADA',confirmada:true,
      consultadoEn:new Date(now-100).toISOString(),cuotaAceptada:'204448',aceptadaEn:accepted,radicadaEn:registered,decisionInstitucional:'PENDIENTE',firmaContractual:'PENDIENTE'}};
  const dates={SOLICITUD:created,ESTUDIO:null,CUOTA:accepted,RADICACION:registered,FORMALIZACION:null,DECISION:null,DESEMBOLSO:null};
  s.historial=Object.entries(dates).map(([codigo,fecha])=>({codigo,fecha,titulo:codigo,descripcion:'SYNTHETIC',estado:fecha?'CONFIRMADO':'PENDIENTE'}));
  assert.equal(validarSolicitudPublica(s,now).historial.find(e=>e.codigo==='RADICACION').fecha,registered);
  for(const bad of [e=>e.codigo==='RADICACION'?{...e,fecha:created}:e,e=>e.codigo==='DESEMBOLSO'?{...e,estado:'CONFIRMADO'}:e])
    assert.throws(()=>validarSolicitudPublica({...s,historial:s.historial.map(bad)},now));
  assert.throws(()=>validarSolicitudPublica({...s,seguimientoRadicacion:{...s.seguimientoRadicacion,radicadaEn:created}},now));
});
test('aceptacion historica vigente al aceptar se conserva sin extender TTL ni permitir nueva aceptacion tardia',()=>{
  const now=Date.now(),begin=now-7200000,end=now-3600000;
  const s={version:'credihoy-simulacion-aceptable-v1',solicitudId:id,sha256:'a'.repeat(64),revisionReferencia:id,
    versionCondiciones:'b'.repeat(64),calculadoEn:new Date(begin).toISOString(),vigenteHasta:new Date(end).toISOString(),
    fuente:'CREDIHOY_CALCULO_PROPIO',monto:'100',plazoMeses:1,tasaNominalNamv:'20',periodicidad:'MENSUAL',
    cuota:'102',seguroMensualPorcentaje:'0.09',baseSeguro:'SALDO_INICIAL_PERIODO',unidadRedondeo:'1',
    versionMotor:'credihoy-plan-decimal-v1',versionParametros:'SYNTHETIC',planAproximado:true,
    amortizacion:[{periodo:1,saldoInicial:'100',capital:'100',intereses:'1',seguro:'1',otros:'0',cuota:'102',saldoFinal:'0'}],
    totales:{capital:'100',intereses:'1',seguro:'1',cuotas:'102'},autorizaStart:false,firmaContractual:false,aprobacionInstitucional:false};
  const quote={versionContrato:'credihoy-cotizacion-titular-v1',solicitudId:id,estado:'SIMULACION_PROPIA',cuota:null,oferta:null,
    aceptacion:{aceptada:false,fecha:null},simulacion:s,aceptacionSimulacion:{aceptada:true,fecha:new Date(begin+60000).toISOString()},
    autorizaStart:false,autorizaDesembolso:false};
  assert.equal(validarCotizacionTitular(quote,id,now).simulacion.vigenteHasta,s.vigenteHasta);
  assert.throws(()=>validarCotizacionTitular({...quote,aceptacionSimulacion:{aceptada:false,fecha:null}},id,now));
  assert.throws(()=>validarCotizacionTitular({...quote,aceptacionSimulacion:{aceptada:true,fecha:new Date(end+1).toISOString()}},id,now));
});
