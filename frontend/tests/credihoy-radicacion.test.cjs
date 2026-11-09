const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load,id}=require('./helpers/cd8-fixture.cjs');
const {validarRadicacionTitular,validarCotizacionTitular}=load('services/publico');
const {RadicacionPersonal}=load('asociado/PortalAsociado.tsx');
function packet(){return {versionContrato:'credihoy-radicacion-personal-v1',solicitudId:id,intencionId:id,
  estado:'SIN_INTENCION',codigo:null,puedeConfirmar:false,presentacion:null,K_FLUJO:null,SC:null,
  actividadesActivas:[],decisionInstitucional:'PENDIENTE',firmaContractual:'PENDIENTE',autorizaFirma:false,autorizaDesembolso:false};}
test('radicacion mantiene decision y firma pendientes y rechaza cruces o facultades financieras',()=>{
  assert.equal(validarRadicacionTitular(packet(),id).estado,'SIN_INTENCION');
  for(const changes of [{solicitudId:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'},{autorizaFirma:true},{autorizaDesembolso:true},
    {puedeConfirmar:true},{decisionInstitucional:'APROBADA'},{SC:'123',K_FLUJO:null}])
    assert.throws(()=>validarRadicacionTitular({...packet(),...changes},id));
});
test('preparada requiere presentacion, incertidumbre no permite confirmar',()=>{
  assert.equal(validarRadicacionTitular({...packet(),estado:'PREPARADA',puedeConfirmar:true,presentacion:'signed'},id).puedeConfirmar,true);
  assert.throws(()=>validarRadicacionTitular({...packet(),estado:'INCIERTA',puedeConfirmar:true,presentacion:'signed'},id));
});
test('pantalla inicial exige accion personal y nunca inicia transporte al renderizar',()=>{
  let calls=0;const html=renderToStaticMarkup(React.createElement(RadicacionPersonal,{sid:id,operar:async()=>{calls++;return packet();}}));
  assert.match(html,/Radicar mi solicitud/);assert.doesNotMatch(html,/Confirmar radicacion/);assert.equal(calls,0);
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
