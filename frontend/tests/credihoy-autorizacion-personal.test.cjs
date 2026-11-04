const {test}=require('node:test'),assert=require('node:assert/strict');
const {createHash,webcrypto}=require('node:crypto');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load,id}=require('./helpers/cd8-fixture.cjs');
global.crypto??=webcrypto;
const {validarAutorizacionTitular}=load('services/publico');
const {AutorizacionPersonal}=load('asociado/PortalAsociado.tsx');
function packet(){
  const bytes=Buffer.from('%PDF-SYNTHETIC-NOT-A-SIGNED-DOCUMENT');
  return {versionContrato:'credihoy-autorizacion-titular-v1',solicitudId:id,
    estado:'PENDIENTE_ACEPTACION_PERSONAL',firmado:false,consultasHabilitadas:false,evaluacionHabilitada:false,
    documento:{codigo:'COLO-FO.023',version:'10',pdfSha256:createHash('sha256').update(bytes).digest('hex'),
      pdfBase64:bytes.toString('base64'),presentacion:'synthetic-bound-presentation',operacionId:id,
      proposito:'AUTORIZACION_CENTRALES_PRECONSULTA'}};
}
test('documento oficial conserva UUID, version y hash sin afirmar firma o consultas',async()=>{
  const p=packet();assert.deepEqual(await validarAutorizacionTitular(p,id),p);
});

test('identidad cotejada no habilita envio, firma ni consultas',async()=>{
  const p={...packet(),firma:{operacionId:id,envioHabilitado:false,identidadVerificada:true}};
  assert.deepEqual(await validarAutorizacionTitular(p,id),p);
  await assert.rejects(validarAutorizacionTitular({...p,firma:{...p.firma,envioHabilitado:true}},id));
  await assert.rejects(validarAutorizacionTitular({...p,firmado:true},id));
});
test('firma declarada, otro expediente, PDF alterado y version desconocida no habilitan autorizacion',async()=>{
  for(const update of [{firmado:true},{consultasHabilitadas:true},{solicitudId:'00000000-0000-4000-8000-000000000009'},
    {documento:{...packet().documento,pdfSha256:'0'.repeat(64)}},{documento:{...packet().documento,version:'11'}}]){
    await assert.rejects(validarAutorizacionTitular({...packet(),...update},id));
  }
});
test('la vista personal no acepta ni envia al montarse y no ofrece ejecutar firma o centrales',()=>{
  let calls=0;const html=renderToStaticMarkup(React.createElement(AutorizacionPersonal,{sid:id,operar:()=>{calls++;throw Error('NO_AUTO_CALL');}}));
  assert.equal(calls,0);assert.match(html,/Preparar COLO-FO.023/);assert.match(html,/Nombre completo/);
  assert.doesNotMatch(html,/checked=""|Ejecutar firma|Consultar centrales|Aprobado/);
});

function centrales(){return {...packet(),documento:null,estado:'CENTRALES_OTP_CONFIRMADA',centrales:{
  estado:'OTP_CONFIRMADA',operacionId:id,proposito:'CONSULTA_CENTRALES',metodo:'OTP',textoVersion:'COLO-FO.023:v10',
  textoSha256:'a'.repeat(64),pdfSha256:'b'.repeat(64),presentacion:null,confirmadoEn:'2026-10-30T17:00:00Z',
  verificacionReferencia:'c'.repeat(64),vigenteHasta:'2026-10-30T20:00:00Z'}};}
test('centrales OTP confirmation remains distinct from PDF signature and financial enablement',async()=>{
  const p=centrales();assert.deepEqual(await validarAutorizacionTitular(p,id),p);
  for(const update of [{firmado:true},{consultasHabilitadas:true},{centrales:{...p.centrales,proposito:'ACCESO'}},
    {centrales:{...p.centrales,verificacionReferencia:null}},{centrales:{...p.centrales,confirmadoEn:null}}]){
    await assert.rejects(()=>validarAutorizacionTitular({...p,...update},id));
  }
});
test('centrales does not send OTP or acceptance on initial render',()=>{
  const {CentralesPersonal}=load('asociado/PortalAsociado.tsx');let calls=0;
  const html=renderToStaticMarkup(React.createElement(CentralesPersonal,{sid:id,operar:()=>{calls++;throw Error('NO_AUTO_SEND');}}));
  assert.equal(calls,0);assert.match(html,/Revisar autorizacion de consulta/);assert.doesNotMatch(html,/checked=""|Ejecutar firma/);
});

test('authorization screen shows one personal path and never duplicates the DEV signature panel with centrales',()=>{
  const {EspacioAsociado}=load('asociado/PortalAsociado.tsx');
  const original=React.useState;let index=0,calls=0;
  const row={referencia:id,titulo:'En revision',monto:'400000.00',plazoMeses:2,periodicidad:'Mensual',
    fechaCreacion:'2026-10-30T12:00:00Z',pendientes:[],siguientePaso:'Pendiente',
    procedencia:{origen:'FUENTE_OPERATIVA',estadoRegistrado:'Iniciada',actualizadoAt:'2026-10-30T12:00:00Z',consultadoAt:'2026-10-30T12:00:00Z'}};
  function render(enabled,authorization=true,origin='FUENTE_OPERATIVA'){
    index=0;React.useState=initial=>original(index++===0?'autorizaciones':initial);
    return renderToStaticMarkup(React.createElement(EspacioAsociado,{solicitudes:[{...row,procedencia:{...row.procedencia,origen:origin}}],
      busy:false,actualizar:()=>{},salir:()=>{},autorizacionHabilitada:authorization,centralesHabilitadas:enabled,
      autorizar:()=>{calls++;throw Error('NO_AUTO_AUTHORIZATION');}}));
  }
  try{
    const enabled=render(true);assert.match(enabled,/Consultando el estado de tu autorizacion/);
    assert.doesNotMatch(enabled,/Autorizacion de informacion crediticia|Verificar identidad de firma|Preparar COLO-FO.023/);
    const disabled=render(false);assert.match(disabled,/Autorizacion de informacion crediticia/);
    assert.doesNotMatch(disabled,/Autorizar centrales/);
    for(const hidden of [render(true,false),render(true,true,'HISTORICO_PILOTO')]){
      assert.doesNotMatch(hidden,/Autorizar centrales|Preparar COLO-FO.023/);
    }
    assert.equal(calls,0);
  }finally{React.useState=original;}
});

test('new dossiers require document acceptance before exposing centrales and errors never send',()=>{
  const {AutorizacionesPersonales}=load('asociado/PortalAsociado.tsx');
  const original=React.useState;let calls=0;
  try{
    for(const stage of ['cargando','documento','centrales','error']){
      let index=0;React.useState=initial=>original(index++===0?stage:initial);
      const html=renderToStaticMarkup(React.createElement(AutorizacionesPersonales,{sid:id,
        operar:()=>{calls++;throw Error('NO_AUTO_SEND');}}));
      if(stage==='documento'){
        assert.match(html,/Preparar COLO-FO.023/);
        assert.doesNotMatch(html,/Revisar autorizacion de consulta/);
      }else if(stage==='centrales'){
        assert.match(html,/Revisar autorizacion de consulta/);
        assert.doesNotMatch(html,/Preparar COLO-FO.023/);
      }else{
        assert.doesNotMatch(html,/Preparar COLO-FO.023|Revisar autorizacion de consulta/);
      }
    }
    assert.equal(calls,0);
  }finally{React.useState=original;}
});

test('authorization prerequisite lookup is read-only, fails closed and resumes only after a recorded acceptance',async()=>{
  const {AutorizacionesPersonales}=load('asociado/PortalAsociado.tsx');
  const original={useState:React.useState,useRef:React.useRef,useEffect:React.useEffect};
  try{
    for(const response of ['AUTORIZACION_AUSENTE','PENDIENTE_ACEPTACION_PERSONAL','PENDIENTE_FIRMA_AUTENTIC','FIRMA_AUTENTIC_PENDIENTE_VERIFICACION','UNKNOWN','ERROR']){
      const states=[],refs=[],effects=[],calls=[];let si=0,ri=0;
      React.useState=initial=>{const i=si++;if(!(i in states))states[i]=initial;return[states[i],v=>{states[i]=v;}];};
      React.useRef=initial=>{const i=ri++;return refs[i]??(refs[i]={current:initial});};
      React.useEffect=fn=>{if(!effects.length)effects.push(fn);};
      const operate=async body=>{calls.push(body);if(response==='ERROR')throw Error('SYNTHETIC');return {estado:response};};
      const render=()=>{si=0;ri=0;return AutorizacionesPersonales({sid:id,operar:operate});};
      render();effects[0]();await new Promise(r=>setImmediate(r));
      assert.deepEqual(calls,[{operacion:'autorizacion',accion:'consultar',solicitudId:id}]);
      assert.equal(states[0],['AUTORIZACION_AUSENTE','PENDIENTE_ACEPTACION_PERSONAL'].includes(response)?'documento':response.includes('FIRMA_AUTENTIC')?'centrales':'error');
      if(response==='AUTORIZACION_AUSENTE'){
        const tree=render();assert.equal(tree.type,AutorizacionPersonal);
        tree.props.onAceptacion();assert.equal(states[0],'centrales');
      }
    }
  }finally{Object.assign(React,original);}
});

test('confirmed authorization refreshes tracking with a bounded timer, never sends provider commands',()=>{
  const {CentralesPersonal}=load('asociado/PortalAsociado.tsx');
  const original={useState:React.useState,useRef:React.useRef,useEffect:React.useEffect};
  const oldInterval=global.setInterval,oldClear=global.clearInterval,oldDocument=global.document;
  let index=0,refreshes=0,commands=0,tick,clears=0;const effects=[];
  try{
    React.useState=initial=>{const i=index++;return[i===0?centrales():initial,()=>{}];};
    React.useRef=initial=>({current:initial});React.useEffect=fn=>effects.push(fn);
    global.document={visibilityState:'visible'};
    global.setInterval=fn=>{tick=fn;return 1;};global.clearInterval=()=>{clears++;};
    CentralesPersonal({sid:id,operar:()=>{commands++;throw Error('NO_PROVIDER_COMMAND');},onConfirmada:()=>{refreshes++;}});
    const cleanup=effects[2]();assert.equal(refreshes,1);
    for(let i=0;i<12;i++)tick();
    assert.equal(refreshes,12);assert.ok(clears>0);assert.equal(commands,0);
    cleanup();
  }finally{Object.assign(React,original);global.setInterval=oldInterval;global.clearInterval=oldClear;global.document=oldDocument;}
});

test('a successful state read releases an uncertain documentary checkbox without repeating acceptance',async()=>{
  const original={useState:React.useState,useRef:React.useRef,useEffect:React.useEffect};
  const states=[],refs=[],calls=[];let si=0,ri=0;
  const elements=n=>Array.isArray(n)?n.flatMap(elements):n&&typeof n==='object'?[n,...elements(n.props?.children)]:[];
  const label=n=>typeof n==='string'?n:Array.isArray(n)?n.map(label).join(''):n?.props?label(n.props.children):'';
  try{
    React.useState=initial=>{const i=si++;if(!(i in states))states[i]=initial;return[states[i],v=>{states[i]=v;}];};
    React.useRef=initial=>{const i=ri++;return refs[i]??(refs[i]={current:initial});};React.useEffect=()=>{};
    const operate=async body=>{calls.push(body.accion);if(body.accion==='aceptar')throw Error('SYNTHETIC_FAILURE');return packet();};
    const render=()=>{si=0;ri=0;return AutorizacionPersonal({sid:id,operar:operate,centralesHabilitadas:true});};
    render();states[0]=packet();states[3]=true;
    elements(render()).find(n=>n.type==='button'&&label(n)==='Registrar mi aceptacion').props.onClick();
    await new Promise(r=>setImmediate(r));assert.equal(states[5],true);
    elements(render()).find(n=>n.type==='button'&&label(n)==='Consultar estado').props.onClick();
    await new Promise(r=>setImmediate(r));assert.equal(states[5],false);
    assert.equal(elements(render()).find(n=>n.type==='input'&&n.props.type==='checkbox').props.disabled,false);
    assert.deepEqual(calls,['aceptar','consultar']);
  }finally{Object.assign(React,original);}
});

test('continuar estudio is limited to the operational dossier and the enabled authorization path',()=>{
  const {EspacioAsociado}=load('asociado/PortalAsociado.tsx');let calls=0;
  const base={solicitudes:[{referencia:id,titulo:'En revision',monto:'400000.00',plazoMeses:2,
    periodicidad:'Mensual',fechaCreacion:'2026-10-30T12:00:00Z',pendientes:[],siguientePaso:'Pendiente',
    procedencia:{origen:'FUENTE_OPERATIVA',estadoRegistrado:'Iniciada',actualizadoAt:'2026-10-30T12:00:00Z',consultadoAt:'2026-10-30T12:00:00Z'}}],
    busy:false,actualizar:()=>{},salir:()=>{},autorizacionHabilitada:true,centralesHabilitadas:true,
    autorizar:()=>{calls++;throw Error('NO_AUTO_OPERATION');}};
  assert.match(renderToStaticMarkup(React.createElement(EspacioAsociado,base)),/Continuar estudio/);
  for(const update of [{centralesHabilitadas:false},{autorizacionHabilitada:false},{autorizar:undefined},
    {solicitudes:[{...base.solicitudes[0],procedencia:{...base.solicitudes[0].procedencia,origen:'HISTORICO_PILOTO'}}]}]){
    assert.doesNotMatch(renderToStaticMarkup(React.createElement(EspacioAsociado,{...base,...update})),/Continuar estudio/);
  }
  assert.equal(calls,0);
});

test('a lost confirmation response retains the exact check identity and never silently starts another check',async()=>{
  const {CentralesPersonal}=load('asociado/PortalAsociado.tsx');
  const original={useState:React.useState,useRef:React.useRef,useEffect:React.useEffect};
  const states=[],refs=[],calls=[];let stateIndex=0,refIndex=0;
  const pending={...centrales(),estado:'CENTRALES_ENVIADO',centrales:{...centrales().centrales,
    estado:'ENVIADO',presentacion:'SYNTHETIC_PURPOSE_TOKEN',confirmadoEn:null,verificacionReferencia:null}};
  function elements(node){
    if(!node||typeof node!=='object')return[];
    if(Array.isArray(node))return node.flatMap(elements);
    return[node,...elements(node.props?.children)];
  }
  function label(node){return typeof node==='string'?node:Array.isArray(node)?node.map(label).join(''):node?.props?label(node.props.children):'';}
  const operate=async body=>{
    calls.push({...body});
    if(calls.length===1)throw Error('SYNTHETIC_RESPONSE_LOST_AFTER_PROVIDER_APPROVED');
    if(body.accion==='centrales_consultar')return pending;
    return centrales();
  };
  function render(){stateIndex=0;refIndex=0;return CentralesPersonal({sid:id,operar:operate});}
  try{
    React.useState=initial=>{const i=stateIndex++;if(!(i in states))states[i]=initial;
      return[states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];};
    React.useRef=initial=>{const i=refIndex++;return refs[i]??(refs[i]={current:initial});};
    React.useEffect=()=>{};
    render();assert.equal(calls.length,0);states[0]=pending;states[5]='123456';
    elements(render()).find(n=>n.type==='form').props.onSubmit({preventDefault(){}});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls.length,1);assert.equal(calls[0].accion,'centrales_comprobar');
    assert.equal(states[5],'');assert.equal(states[6],true);
    let tree=render();
    elements(tree).find(n=>n.type==='button'&&label(n)==='Reintentar consulta de estado').props.onClick();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls.length,2);assert.equal(states[6],true);
    tree=render();elements(tree).find(n=>n.type==='button'&&label(n)==='Recuperar comprobacion pendiente').props.onClick();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls.length,3);assert.deepEqual(calls[2],calls[0]);
    assert.equal(states[0].centrales.estado,'OTP_CONFIRMADA');assert.equal(refs[3].current,null);
    assert.equal(elements(render()).some(n=>n.type==='button'&&label(n)==='Recuperar comprobacion pendiente'),false);
  }finally{Object.assign(React,original);}
});

test('bureau checkbox requires the same current official presentation, not PDF communication fields',()=>{
  const {presentacionCentralesVigente:valid}=load('asociado/PortalAsociado.tsx');
  const p={...packet(),estado:'CENTRALES_PENDIENTE_ACEPTACION',centrales:{...centrales().centrales,
    estado:'PENDIENTE_ACEPTACION',pdfSha256:packet().documento.pdfSha256,
    presentacion:packet().documento.presentacion,vigenteHasta:'2099-01-01T00:00:00Z'}};
  assert.equal(valid(p),true);
  for(const value of [null,{...p,documento:{...p.documento,version:'11'}},
    {...p,documento:{...p.documento,pdfSha256:'0'.repeat(64)}},
    {...p,centrales:{...p.centrales,presentacion:'OTHER_PRESENTATION'}},
    {...p,centrales:{...p.centrales,proposito:'ACCESO'}},
    {...p,centrales:{...p.centrales,vigenteHasta:'2000-01-01T00:00:00Z'}}])assert.equal(valid(value),false);
});

test('unchecked consent never sends; explicit checkbox sends once and never confirms authorization itself',async()=>{
  const {CentralesPersonal}=load('asociado/PortalAsociado.tsx');
  const original={useState:React.useState,useRef:React.useRef,useEffect:React.useEffect};
  const states=[],refs=[],calls=[];let si=0,ri=0,resolve;
  const pending={...packet(),estado:'CENTRALES_PENDIENTE_ACEPTACION',centrales:{...centrales().centrales,
    estado:'PENDIENTE_ACEPTACION',pdfSha256:packet().documento.pdfSha256,
    presentacion:packet().documento.presentacion,vigenteHasta:'2099-01-01T00:00:00Z',
    confirmadoEn:null,verificacionReferencia:null}};
  function elements(n){return Array.isArray(n)?n.flatMap(elements):n&&typeof n==='object'?[n,...elements(n.props?.children)]:[];}
  function label(n){return typeof n==='string'?n:Array.isArray(n)?n.map(label).join(''):n?.props?label(n.props.children):'';}
  function render(){si=0;ri=0;return CentralesPersonal({sid:id,operar:body=>{calls.push(body);return new Promise(r=>{resolve=r;});}});}
  function send(tree){return elements(tree).find(n=>n.type==='button'&&label(n)==='Aceptar y solicitar codigo de autorizacion');}
  try{
    React.useState=initial=>{const i=si++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;
      return[states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];};
    React.useRef=initial=>{const i=ri++;return refs[i]??(refs[i]={current:initial});};React.useEffect=()=>{};
    render();states[0]=pending;states[7]='blob:synthetic-document';
    assert.equal(send(render()).props.disabled,true);send(render()).props.onClick();assert.equal(calls.length,0);
    let tree=render();elements(tree).find(n=>n.type==='a').props.onClick();
    tree=render();const box=elements(tree).find(n=>n.type==='input'&&n.props.type==='checkbox');
    assert.equal(box.props.disabled,false);box.props.onChange({target:{checked:true}});
    tree=render();assert.equal(send(tree).props.disabled,false);
    send(tree).props.onClick();send(tree).props.onClick();assert.equal(calls.length,1);
    assert.equal(calls[0].accion,'centrales_enviar');assert.equal(calls[0].acepta,true);
    resolve({...pending,documento:null,estado:'CENTRALES_ENVIADO',centrales:{...pending.centrales,estado:'ENVIADO'}});
    await new Promise(r=>setImmediate(r));assert.equal(states[0].centrales.estado,'ENVIADO');
    assert.equal(states[0].firmado,false);assert.equal(states[0].centrales.confirmadoEn,null);
  }finally{Object.assign(React,original);}
});

test('a visited authorization panel remains mounted but hidden when changing workspace tabs',()=>{
  const {EspacioAsociado}=load('asociado/PortalAsociado.tsx');
  const original=React.useState;let index=0;
  try{
    React.useState=initial=>original(index++===4?true:initial);
    const html=renderToStaticMarkup(React.createElement(EspacioAsociado,{solicitudes:[{referencia:id,titulo:'En revision',
      monto:'400000.00',plazoMeses:2,periodicidad:'Mensual',fechaCreacion:'2026-10-30T12:00:00Z',pendientes:[],siguientePaso:'Pendiente',
      procedencia:{origen:'FUENTE_OPERATIVA',estadoRegistrado:'Iniciada',actualizadoAt:'2026-10-30T12:00:00Z',consultadoAt:'2026-10-30T12:00:00Z'}}],
      busy:false,actualizar:()=>{},salir:()=>{},autorizacionHabilitada:true,centralesHabilitadas:true,autorizar:()=>{throw Error('NO_OTP');}}));
    assert.match(html,/<div hidden="">/);assert.match(html,/Consultando el estado de tu autorizacion/);assert.doesNotMatch(html,/Preparar COLO-FO.023/);
  }finally{React.useState=original;}
});

test('returning from the PDF keeps the same live-session dossier mounted and clears it on authorization changes or failures',async()=>{
  const {CredihoyAccesoAsociado}=load('components/CredihoyAccesoAsociado.tsx');
  const original={useState:React.useState,useRef:React.useRef,useEffect:React.useEffect,useCallback:React.useCallback};
  const oldWindow=global.window,oldDocument=global.document;
  const grant={autenticado:true,sesionReferencia:id,nombre:null,expiraAt:'2099-01-01T00:00:00Z',
    acciones:{seguimiento:true,solicitar:false,simular:false,autorizar:true,centralesOtp:true}};
  const tick=()=>new Promise(r=>setImmediate(r));
  function nodes(n){return Array.isArray(n)?n.flatMap(nodes):n&&typeof n==='object'?[n,...nodes(n.props?.children)]:[];}
  try{
    for(const scenario of ['same','other-session','revoked','session-failure','rows-failure','tracking-disabled']){
      const states=[],refs=[],handlers={},effects=[];let si=0,ri=0,props,invalidated=0,rowsCalls=0,resolveSession,rejectSession,resolveRows,rejectRows;
      const rows=[{referencia:id}],sessionPromise=new Promise((resolve,reject)=>{resolveSession=resolve;rejectSession=reject;});
      const rowsPromise=new Promise((resolve,reject)=>{resolveRows=resolve;rejectRows=reject;});
      const api={sesion:()=>sessionPromise,solicitudes:()=>{rowsCalls++;return rowsPromise;},invalidarSesion:()=>{invalidated++;}};
      React.useState=initial=>{const i=si++;if(!(i in states))states[i]=i===0?{}:i===1?grant:i===2?rows:i===3?false:typeof initial==='function'?initial():initial;
        return[states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];};
      React.useRef=initial=>{const i=ri++;return refs[i]??(refs[i]={current:i===0?api:initial});};
      React.useCallback=fn=>fn;
      React.useEffect=(fn,deps)=>{if(deps?.[0]===true&&(deps.length===1||deps[1]===id))effects.push(fn);};
      global.document={visibilityState:'visible',addEventListener:(name,fn)=>{handlers[name]=fn;},removeEventListener(){}};
      global.window={addEventListener:(name,fn)=>{handlers[name]=fn;},removeEventListener(){}};
      function render(){si=0;ri=0;return CredihoyAccesoAsociado({seguimiento:p=>{props=p;return React.createElement('span');}});}
      render();assert.equal(effects.length,1);const cleanup=effects[0]();
      handlers.visibilitychange();
      assert.equal(states[2],rows,'a pending session check must not unmount the dossier');
      if(scenario==='session-failure'){
        rejectSession(Error('SYNTHETIC_TRANSPORT_FAILURE'));await tick();assert.equal(states[2],null);assert.equal(rowsCalls,0);
      }else{
        const next=scenario==='other-session'?{...grant,sesionReferencia:'00000000-0000-4000-8000-000000000099'}:
          scenario==='revoked'?{autenticado:false}:scenario==='tracking-disabled'?{...grant,acciones:{...grant.acciones,seguimiento:false}}:grant;
        resolveSession(next);await tick();
        if(scenario==='revoked'||scenario==='tracking-disabled'){
          assert.equal(states[2],null);assert.equal(rowsCalls,0);assert.equal(invalidated,scenario==='revoked'?1:0);
        }else{
          assert.equal(states[2],scenario==='other-session'?null:rows);
          if(scenario==='rows-failure'){
            rejectRows(Error('SYNTHETIC_GET_503'));await tick();assert.equal(states[2],null);
          }else{
            const updated=[{referencia:id}];resolveRows(updated);await tick();
            const tree=render();assert.equal(props.solicitudes,updated);
            assert.equal(nodes(tree).find(n=>n.type===React.Fragment&&n.key===next.sesionReferencia)?.key,next.sesionReferencia);
          }
        }
      }
      cleanup();
    }
  }finally{Object.assign(React,original);global.window=oldWindow;global.document=oldDocument;}
});
