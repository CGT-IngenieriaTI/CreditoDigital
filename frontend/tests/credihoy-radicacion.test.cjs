const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load,id}=require('./helpers/cd8-fixture.cjs');
const {validarRadicacionTitular}=load('services/publico');
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
