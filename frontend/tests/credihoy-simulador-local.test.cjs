const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/cd8-fixture.cjs');
const fixtures=require('./helpers/simulacion-local.json');
const {validarSimulacionLocal}=load('services/simulacion');
const {AccesoAsociadoClient}=load('services/publico');
const sid=fixtures.parametros.solicitudId;
const clone=v=>structuredClone(v);
const scenario=p=>({monto:p.resultado.monto,plazoMeses:p.resultado.plazoMeses});
const response=p=>new Response(JSON.stringify(p),{headers:{'Content-Type':'application/json'}});
const session=()=>({versionContrato:'credihoy-acceso-publico-v1',autenticado:true,nombre:'SINTETICO',sesionReferencia:sid,
  expiraAt:new Date(Date.now()+900000).toISOString(),acciones:{seguimiento:true,solicitar:false,simular:false,documentos:false,simuladorLocal:true}});

test('contrato real generado por Django: parametros, NAMV, seguro y centavos conciliados',()=>{
  const params=fixtures.parametros;
  assert.equal(validarSimulacionLocal(params,sid,params.requestId).resultado,null);
  for(const name of ['ventanilla','libranza','recalculo']){
    const p=fixtures[name],r=validarSimulacionLocal(p,sid,p.requestId,scenario(p)).resultado;
    assert.equal(r.seguro.base,'CAPITAL_INICIAL');assert.equal(r.amortizacion.at(-1).saldoFinal,'0.00');
    assert.equal(p.ofertable,false);assert.equal(p.autorizaDecision,false);assert.equal(p.autorizaStart,false);
    assert.equal(r.amortizacion[0].seguroEstimado,r.amortizacion.at(-1).seguroEstimado);
  }
  assert.equal(fixtures.ventanilla.resultado.cuotaCapitalInteres,'91532.52');
  assert.equal(fixtures.ventanilla.resultado.cuotaEstimada,'92432.52');
  assert.equal(fixtures.ventanilla.resultado.totalEstimado,'1109190.30');
});

test('rechaza oferta, datos privados, total alterado, otra sesion/request/escenario y base sobre saldo',()=>{
  for(const change of [p=>p.ofertable=true,p=>p.oferta={},p=>p.titular='PRIVATE',p=>p.resultado.totalEstimado='1.00',
    p=>p.resultado.amortizacion[0].capital='1.00',p=>p.seguro.base='SALDO',p=>p.tasa.valor='25.41',
    p=>p.resultado.plazoMeses=true,p=>p.resultado.amortizacion.pop(),p=>p.resultado.conceptosNoAcreditados=[],
    p=>p.resultado.seguro.valorMensualEstimado='0.00']){
      const p=clone(fixtures.ventanilla);change(p);assert.throws(()=>validarSimulacionLocal(p,sid,p.requestId,scenario(fixtures.ventanilla)));
  }
  const p=fixtures.ventanilla;
  assert.throws(()=>validarSimulacionLocal(p,crypto.randomUUID(),p.requestId,scenario(p)));
  assert.throws(()=>validarSimulacionLocal(p,sid,crypto.randomUUID(),scenario(p)));
  assert.throws(()=>validarSimulacionLocal(p,sid,p.requestId,{monto:'2000000',plazoMeses:12}));
  assert.throws(()=>validarSimulacionLocal(fixtures.parametros,sid,fixtures.parametros.requestId,scenario(p)));
});

test('cliente reutiliza cotizacion, sesion/CSRF y monto/plazo unicamente, sin reintento',async()=>{
  const original=global.fetch,calls=[];
  global.fetch=async(url,options)=>{
    if(url.endsWith('sesion/'))return response(session());
    calls.push({url,options});const p=clone(fixtures.ventanilla);p.requestId=options.headers['X-Request-ID'];return response(p);
  };
  try{
    const client=new AccesoAsociadoClient();await assert.rejects(client.simular(sid,scenario(fixtures.ventanilla)));
    const login=await client.sesion();assert.equal(login.acciones.simuladorLocal,true);assert.equal(login.acciones.simular,false);
    const p=await client.simular(sid,scenario(fixtures.ventanilla));assert.equal(p.resultado.cuotaEstimada,'92432.52');
    assert.equal(calls.length,1);assert.match(calls[0].url,/\/publico\/cotizacion\/$/);
    const o=calls[0].options;assert.equal(o.credentials,'same-origin');assert.equal(o.redirect,'error');assert.equal(o.cache,'no-store');
    assert.ok(Object.hasOwn(o.headers,'X-CSRFToken'));assert.equal(o.headers['X-Acceso-Referencia'],sid);
    assert.deepEqual(JSON.parse(o.body),{solicitudId:sid,operacion:'simular',escenario:scenario(fixtures.ventanilla)});
    await assert.rejects(client.simular(sid,{...scenario(fixtures.ventanilla),tasa:'1'}));assert.equal(calls.length,1);
    global.fetch=async()=>new Response('{}',{status:503,headers:{'Content-Type':'application/json'}});
    await assert.rejects(client.simular(sid,scenario(fixtures.ventanilla)));
  }finally{global.fetch=original;}
});

test('respuesta en vuelo se descarta al cerrar sesion; no se reenvia',async()=>{
  const original=global.fetch;let finish,calls=0;
  global.fetch=async(url,options)=>{
    if(url.endsWith('sesion/'))return response(session());calls++;
    const p=clone(fixtures.ventanilla);p.requestId=options.headers['X-Request-ID'];return new Promise(resolve=>{finish=()=>resolve(response(p));});
  };
  try{const client=new AccesoAsociadoClient();await client.sesion();const pending=client.simular(sid,scenario(fixtures.ventanilla));
    client.invalidarSesion();finish();await assert.rejects(pending);assert.equal(calls,1);
  }finally{global.fetch=original;}
});

test('formulario React separado de la oferta: monto y plazo editables, sin tasa libre ni aceptacion',()=>{
  const {SimuladorLocal}=load('asociado/SimuladorLocal.tsx');
  const html=renderToStaticMarkup(React.createElement(SimuladorLocal,{sid,montoInicial:'1000000',plazoInicial:12,
    operar:async()=>fixtures.parametros,continuar:()=>{}}));
  assert.match(html,/name="montoSimulado"/);assert.match(html,/name="plazoSimulado"/);
  assert.match(html,/Cuota estimada/);assert.match(html,/Calcular cuota estimada/);
  assert.doesNotMatch(html,/Aceptar oferta|Comision Mipyme|name="tasa"|name="seguro"/);
});
