const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load,id}=require('./helpers/cd8-fixture.cjs');
const {AccesoAsociadoClient,validarSolicitudPublica}=load('services/publico');
const {CredihoyAccesoAsociado}=load('components/CredihoyAccesoAsociado.tsx');
const versionContrato='credihoy-acceso-publico-v1';
test('base publica usa solo raiz heredada o prefijo credihoy, nunca otro origen',()=>{
  const {baseApiPublica}=load('services/publico');
  assert.equal(baseApiPublica('/'),'/api/v1/credihoy/publico/');
  for(const value of ['/credihoy','/credihoy/','/credihoy/resultado'])
    assert.equal(baseApiPublica(value),'/credihoy/api/v1/credihoy/publico/');
  for(const value of ['https://evil.invalid/','//evil.invalid/','/credihoy-otro','/demo/credihoy'])
    assert.throws(()=>baseApiPublica(value));
});
test('sesion y POST usan prefijo con credenciales same-origin y sin redireccion',async()=>{
  const originalFetch=global.fetch,location=Object.getOwnPropertyDescriptor(global,'location'),calls=[];
  Object.defineProperty(global,'location',{value:{pathname:'/credihoy/'},configurable:true});
  global.fetch=async(url,options)=>{calls.push({url,options});return url.endsWith('salir/')?
    new Response(null,{status:204}):new Response(JSON.stringify({versionContrato,autenticado:false}),
      {headers:{'Content-Type':'application/json'}});};
  try {const client=new AccesoAsociadoClient();await client.sesion();await client.salir();
    assert.deepEqual(calls.map(v=>v.url),['/credihoy/api/v1/credihoy/publico/sesion/','/credihoy/api/v1/credihoy/publico/salir/']);
    assert.equal(calls[0].options.method,'GET');assert.equal(calls[1].options.method,'POST');
    assert.ok(calls.every(v=>v.options.credentials==='same-origin'&&v.options.redirect==='error'));
  } finally {global.fetch=originalFetch;if(location)Object.defineProperty(global,'location',location);else delete global.location;}
});
test('sesion sin seguimiento conserva autenticacion y permite cierre sin lista ficticia',async()=>{
  const original=global.fetch,calls=[];
  global.fetch=async url=>{calls.push(url);return url.endsWith('salir/')?new Response(null,{status:204}):
    new Response(JSON.stringify({versionContrato,autenticado:true,nombre:null,
      expiraAt:new Date(Date.now()+900000).toISOString(),sesionReferencia:id,
      acciones:{seguimiento:false,solicitar:false,simular:false,documentos:false}}),
      {headers:{'Content-Type':'application/json'}});};
  try{const client=new AccesoAsociadoClient(),p=await client.sesion();
    assert.equal(p.autenticado,true);assert.equal(p.acciones.seguimiento,false);await client.salir();
    assert.equal(calls.length,2);assert.ok(calls[1].endsWith('salir/'));assert.ok(calls.every(v=>!v.endsWith('solicitudes/')));
  }finally{global.fetch=original;}
});
function solicitud(){return {referencia:id,titulo:'Actualiza tus datos',monto:'400000',plazoMeses:2,
  periodicidad:'Mensual',fechaCreacion:'2026-10-20T12:00:00Z',requisitosCumplidos:11,
  pendientes:['Debes actualizar tus datos para continuar con tu solicitud.'],
  siguientePaso:'Actualiza tus datos con Congente.',ofertaDisponible:false,acciones:{radicar:false,firmar:false}};}

test('formalizacion proyecta un documento verificado sin declarar aprobacion o desembolso',()=>{
  const f={versionContrato:'credihoy-formalizacion-publica-v1',documento:'COLO-FO.001',
    estado:'DOCUMENTO_VERIFICADO',firmaAcreditada:true,actualizadoAt:'2026-11-06T10:00:00Z',
    continuacionInstitucionalPendiente:true};
  const now=Date.parse('2026-11-06T11:00:00Z');
  assert.deepEqual(validarSolicitudPublica({...solicitud(),formalizacionDocumental:f},now).formalizacionDocumental,f);
  for(const change of [{estado:'VALIDACION_PENDIENTE'},{firmaAcreditada:'true'},
    {continuacionInstitucionalPendiente:false},{urlDocuments:'https://private.invalid'},
    {actualizadoAt:'2026-11-07T00:00:00Z'}])
    assert.throws(()=>validarSolicitudPublica({...solicitud(),formalizacionDocumental:{...f,...change}},now));
});

test('seguimiento usa Date HTTP aunque el reloj del asociado difiera, sin admitir fechas posteriores',async()=>{
  const original=global.fetch,server=Math.floor(Date.now()/1000)*1000+47000;
  const row={...solicitud(),requisitosCumplidos:null,procedencia:{origen:'HISTORICO_PILOTO',
    estadoRegistrado:'Consentimiento firmado',actualizadoAt:new Date(server-1000).toISOString(),
    consultadoAt:new Date(server+500).toISOString()}};
  let future=false;
  global.fetch=async url=>new Response(JSON.stringify(url.endsWith('sesion/')?{
    versionContrato,autenticado:true,nombre:null,expiraAt:new Date(server+900000).toISOString(),sesionReferencia:id,
    acciones:{seguimiento:true,solicitar:false,simular:false,documentos:false}}:{sesionReferencia:id,
      solicitudes:[future?{...row,procedencia:{...row.procedencia,consultadoAt:new Date(server+1000).toISOString()}}:row]}),
    {headers:{'Content-Type':'application/json',Date:new Date(server).toUTCString()}});
  try{
    assert.throws(()=>validarSolicitudPublica(row));
    const client=new AccesoAsociadoClient();await client.sesion();
    assert.equal((await client.solicitudes())[0].procedencia.consultadoAt,row.procedencia.consultadoAt);
    future=true;await assert.rejects(client.solicitudes());
  }finally{global.fetch=original;}
});

test('confirmed bureau OTP uses HTTP Date instead of rejecting a provider confirmation ahead of the browser clock',async()=>{
  const {validarAutorizacionTitular}=load('services/publico');
  const original=global.fetch,server=Math.floor(Date.now()/1000)*1000+49000;
  const proof={versionContrato:'credihoy-autorizacion-titular-v1',solicitudId:id,
    estado:'CENTRALES_OTP_CONFIRMADA',documento:null,firmado:false,consultasHabilitadas:false,evaluacionHabilitada:false,
    centrales:{estado:'OTP_CONFIRMADA',operacionId:id,proposito:'CONSULTA_CENTRALES',metodo:'OTP',
      textoVersion:'COLO-FO.023:v10',textoSha256:'a'.repeat(64),pdfSha256:'b'.repeat(64),presentacion:null,
      confirmadoEn:new Date(server).toISOString(),verificacionReferencia:'c'.repeat(64),vigenteHasta:new Date(server+3600000).toISOString()}};
  let future=false;const actions=[];
  global.fetch=async(url,options)=>{
    if(options.body)actions.push(JSON.parse(options.body).accion);
    return new Response(JSON.stringify(url.endsWith('sesion/')?{versionContrato,autenticado:true,nombre:null,
      expiraAt:new Date(server+900000).toISOString(),sesionReferencia:id,
      acciones:{seguimiento:true,solicitar:false,simular:false,documentos:false,autorizar:true,centralesOtp:true}}:
      {sesionReferencia:id,autorizacion:future?{...proof,centrales:{...proof.centrales,
        confirmadoEn:new Date(server+60000).toISOString()}}:proof}),
      {headers:{'Content-Type':'application/json',Date:new Date(server).toUTCString()}});
  };
  try{
    await assert.rejects(validarAutorizacionTitular(proof,id));
    const client=new AccesoAsociadoClient();await client.sesion();
    assert.equal((await client.autorizacion({operacion:'autorizacion',accion:'centrales_consultar',solicitudId:id})).centrales.estado,'OTP_CONFIRMADA');
    future=true;await assert.rejects(client.autorizacion({operacion:'autorizacion',accion:'centrales_consultar',solicitudId:id}));
    assert.deepEqual(actions,['centrales_consultar','centrales_consultar']);
  }finally{global.fetch=original;}
});

test('procedencia historica mantiene fechas y estado propio, sin atribuir aprobacion CD6',()=>{
  const row={...solicitud(),requisitosCumplidos:null,procedencia:{origen:'HISTORICO_PILOTO',
    estadoRegistrado:'Consentimiento firmado',actualizadoAt:'2026-10-20T12:00:00Z',consultadoAt:'2026-10-20T13:00:00Z'}};
  assert.deepEqual(validarSolicitudPublica(row).procedencia,row.procedencia);
  for(const change of [{host:'PRIVATE'},{origen:'ORACLE'},{estadoRegistrado:'APROBADA'},
    {actualizadoAt:'2026-10-21T00:00:00Z'},{consultadoAt:'2039-01-01T00:00:00Z'}]){
    assert.throws(()=>validarSolicitudPublica({...row,procedencia:{...row.procedencia,...change}}));
  }
  assert.throws(()=>validarSolicitudPublica({...row,requisitosCumplidos:16}));
});

test('proyeccion publica descarta detalles tecnicos y rechaza aprobaciones o estados crudos',()=>{
  const raw={...solicitud(),operador:{oracle:'PRIVATE'},hdc:'PRIVATE',numeroDocumento:'PRIVATE'};
  const p=validarSolicitudPublica(raw);assert.equal(p.operador,undefined);assert.equal(p.numeroDocumento,undefined);
  for(const change of [p=>p.titulo='NO_AUTORIZADA',p=>p.pendientes=['PENDIENTE_POLITICA'],
      p=>p.ofertaDisponible=true,p=>p.acciones.radicar=true,p=>p.monto='NaN',p=>p.requisitosCumplidos=99]){
    const p=solicitud();change(p);assert.throws(()=>validarSolicitudPublica(p));
  }
});

test('acceso publico usa cookie y CSRF separados, sin contraseña, contacto editable ni reintento',async()=>{
  const original=global.fetch,calls=[];
  global.fetch=async(url,options)=>{
    calls.push([url,options]);let p;
    if(url.endsWith('inicio/'))p={versionContrato,csrfToken:'x'.repeat(64),avisoVersion:'TEST',avisoTexto:'SYNTHETIC',botSitekey:'TEST',canales:['SMS'],
      duracionSesionSegundos:900,duracionOtpSegundos:300,
      politica:{codigo:'GER-PO.085',version:'03',url:'https://www.congente.coop/images/Balancesocial/GER-PO.085-Politica_tratamiento_datos_personales.pdf'}};
    else if(url.endsWith('acceso/'))p={versionContrato,intento:id,estado:'VERIFICACION_SOLICITADA'};
    else if(url.endsWith('verificar/'))p={versionContrato,autenticado:true};
    else if(url.endsWith('sesion/'))p={versionContrato,autenticado:false};
    else throw Error('PROVIDER_FORBIDDEN');
    return new Response(JSON.stringify(p),{headers:{'Content-Type':'application/json'}});
  };
  try{
    const client=new AccesoAsociadoClient();await client.inicio();
    await client.iniciar({numeroDocumento:'00000000',primerApellido:'PRUEBA',fechaExpedicion:'2001-08-20',canal:'SMS',
      avisoVersion:'TEST',aceptaTratamiento:true,botToken:'synthetic'},id);
    await client.verificar(id,'123456');assert.deepEqual(await client.sesion(),{autenticado:false});
    for(const [url,o] of calls){assert.ok(url.startsWith('/api/v1/credihoy/publico/'));assert.equal(o.credentials,'same-origin');
      assert.equal(o.redirect,'error');if(o.method==='POST')assert.equal(o.headers['X-CSRFToken'],'x'.repeat(64));}
    assert.ok(!JSON.stringify(calls).includes('password'));assert.ok(!JSON.stringify(calls).includes('Authorization'));
  }finally{global.fetch=original;}
});

test('fallo de acceso no reintenta ni fabrica sesion',async()=>{
  const original=global.fetch;let calls=0;
  global.fetch=async()=>{calls++;return new Response('{"mensaje":"No disponible"}',{status:503,headers:{'Content-Type':'application/json'}});};
  try{await assert.rejects(new AccesoAsociadoClient().verificar(id,'123456'));assert.equal(calls,1);}
  finally{global.fetch=original;}
});

test('lista publica rechaza duplicados y no habilita acciones financieras',async()=>{
  const original=global.fetch;
  global.fetch=async url=>new Response(JSON.stringify(url.endsWith('sesion/')?{
    versionContrato,autenticado:true,nombre:'SYNTHETIC',expiraAt:new Date(Date.now()+900000).toISOString(),sesionReferencia:id,
    acciones:{seguimiento:true,solicitar:false,simular:false,documentos:false}}:
    {sesionReferencia:id,solicitudes:[solicitud(),solicitud()]}),{headers:{'Content-Type':'application/json'}});
  try{const client=new AccesoAsociadoClient();await client.sesion();await assert.rejects(client.solicitudes());}
  finally{global.fetch=original;}
});

test('respuesta de seguimiento tardia se rechaza al cambiar la sesion verificada',async()=>{
  const original=global.fetch;let grant=id,finish;
  global.fetch=async(url,options)=>{
    if(url.endsWith('solicitudes/')){
      assert.equal(options.headers['X-Acceso-Referencia'],id);
      return new Promise(resolve=>{finish=()=>resolve(new Response(JSON.stringify({sesionReferencia:id,solicitudes:[solicitud()]}),
        {headers:{'Content-Type':'application/json'}}));});
    }
    return new Response(JSON.stringify({versionContrato,autenticado:true,nombre:'SYNTHETIC',
      expiraAt:new Date(Date.now()+900000).toISOString(),sesionReferencia:grant,
      acciones:{seguimiento:true,solicitar:false,simular:false,documentos:false}}),{headers:{'Content-Type':'application/json'}});
  };
  try{
    const client=new AccesoAsociadoClient();await client.sesion();const pending=client.solicitudes();
    grant='00000000-0000-4000-8000-000000000099';await client.sesion();finish();await assert.rejects(pending);
  }finally{global.fetch=original;}
});

test('superficie publica inicial no incluye autenticacion administrativa ni datos personales',()=>{
  const html=renderToStaticMarkup(React.createElement(CredihoyAccesoAsociado));
  assert.match(html,/Verifica tu identidad/);assert.match(html,/Congente/);
  assert.doesNotMatch(html,/type="password"|name="username"|NO_EVALUABLE|PENDIENTE_POLITICA|NO_AUTORIZADA|Oracle|HDC|START|SC\b|LINIX/);
  const entry=fs.readFileSync(path.join(__dirname,'../src/credihoy-publico.tsx'),'utf8');
  assert.doesNotMatch(entry,/CredihoyCanalValidacion|CredihoyTasasAdmin|validacion\.tsx/);
});

test('contacto exige intento exacto y mascara minima; confirmar y cancelar no aceptan destinos libres',async()=>{
  const original=global.fetch,calls=[];let foreign=false,full=false;
  global.fetch=async(url,o)=>{
    calls.push([url,o]);let p;
    if(o.method==='GET')p={versionContrato,intento:foreign?'00000000-0000-4000-8000-000000000099':id,
      estado:'CONTACTO_DISPONIBLE',contactoEnmascarado:full?'+570000000000':'***00',canal:'SMS',expiraAt:new Date(Date.now()+300000).toISOString()};
    else p={versionContrato,intento:id,estado:JSON.parse(o.body).cancelar?'CANCELADO':'ENVIO_SOLICITADO'};
    return new Response(JSON.stringify(p),{headers:{'Content-Type':'application/json'}});
  };
  try{
    const c=new AccesoAsociadoClient();assert.equal((await c.estadoIntento(id)).contactoEnmascarado,'***00');
    foreign=true;await assert.rejects(c.estadoIntento(id));foreign=false;full=true;await assert.rejects(c.estadoIntento(id));
    await c.confirmarEnvio(id,'SMS');await c.cancelarIntento(id);
    const posts=calls.filter(([,o])=>o.method==='POST').map(([,o])=>JSON.parse(o.body));
    assert.deepEqual(posts,[{intento:id,canal:'SMS'},{intento:id,cancelar:true}]);
  }finally{global.fetch=original;}
});

test('revocar localmente invalida incluso una respuesta de sesion en vuelo',async()=>{
  const original=global.fetch;let finish;
  global.fetch=()=>new Promise(resolve=>{finish=()=>resolve(new Response(JSON.stringify({versionContrato,autenticado:true,
    nombre:'SYNTHETIC',expiraAt:new Date(Date.now()+900000).toISOString(),sesionReferencia:id,
    acciones:{seguimiento:true,solicitar:false,simular:false,documentos:false}}),{headers:{'Content-Type':'application/json'}}));});
  try{const c=new AccesoAsociadoClient(),p=c.sesion();c.invalidarSesion();finish();await assert.rejects(p);}
  finally{global.fetch=original;}
});
