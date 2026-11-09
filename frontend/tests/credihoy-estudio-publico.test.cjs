// Synthetic fixtures only. No provider, session or institutional data.
const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load,id}=require('./helpers/cd8-fixture.cjs');
const {validarSolicitudPublica,validarEstudioPublico}=load('services/publico');
const {EspacioAsociado}=load('asociado/PortalAsociado.tsx');
function fixture(sid=id){
 const codes=[2,4,6,9,10,12,14,17,21,23,24,27,28,30,31,32];
 return {versionContrato:'credihoy-estudio-publico-v1',solicitudId:sid,evaluacionBaseId:1,version:2,
  referenciaVersion:id,fechaEvaluacion:'2026-10-20T14:00:00Z',
  reglas:codes.map((n,index)=>({codigo:'UNICO_FILA_'+n,nombre:'SYNTHETIC_RULE_'+n,
   resultado:index===0?'CUMPLE':index===1?'NO_CUMPLE':'NO_EVALUABLE',
   estadoEvaluacion:index===0?'CUMPLE':index===1?'NO_CUMPLE':n===30||n===31?'NO_APLICA':n===32?'PENDIENTE_POLITICA':'NO_EVALUABLE',
   motivos:['SYNTHETIC_REASON'],datosFaltantes:index>1?['SYNTHETIC_INPUT']:[]})),
  capacidad:{estado:'NO_EVALUABLE',valor:null,preliminarNoDecisoria:'1234567.00',cuotaContractualDisponible:false},
  decision:'REQUIERE_SUBSANACION',autorizadoDecisionCrediticia:false};
}
function row(estudio=fixture()){
 return {referencia:estudio.solicitudId,titulo:'Tu solicitud esta en revision',monto:'123456',plazoMeses:3,
  periodicidad:'Mensual',fechaCreacion:'2026-10-20T12:00:00Z',requisitosCumplidos:estudio.reglas.filter(r=>r.estadoEvaluacion==='CUMPLE').length,
  pendientes:['Necesitamos completar algunas validaciones antes de tomar una decision.'],
  siguientePaso:'Espera la revision de Congente.',ofertaDisponible:false,acciones:{radicar:false,firmar:false},
  procedencia:{origen:'FUENTE_OPERATIVA',estadoRegistrado:'Iniciada',actualizadoAt:'2026-10-20T12:00:00Z',
   consultadoAt:'2026-10-20T15:00:00Z'},estudio};
}
function version8(){
 const original=fixture();
 return {...original,version:8,capacidad:{...original.capacidad,
  preliminarNoDecisoria:'614792',capacidadDisponible:'814791.75',capacidadResidualPostCuota:'609998.46'}};
}
function renderStudy(estudio,extra={},vista='evaluacion'){
 const useState=React.useState;
 React.useState=initial=>useState(initial==='estado'?vista:initial);
 try{return renderToStaticMarkup(React.createElement(EspacioAsociado,{nombre:'SYNTHETIC',
  solicitudes:[validarSolicitudPublica(row(estudio))],busy:false,actualizar:()=>{},salir:()=>{},...extra}));}
 finally{React.useState=useState;}
}

test('continuidad acreditada separa estudio favorable de decision final sin inventar radicacion o firma',()=>{
 const study=fixture();
 study.reglas=study.reglas.map(r=>({...r,resultado:'CUMPLE',estadoEvaluacion:'CUMPLE',motivos:[],datosFaltantes:[]}));
 study.decisionPolitica={version:'credihoy-decision-publica-v1',versionPolitica:'SYNTHETIC',estado:'EN_REVISION',
  motivos:[{codigo:'REVISION_ESTAMENTO_INSTITUCIONAL',regla:null}],decisionLinixRegistrada:false,autorizaDesembolso:false,
  continuidad:{version:'credihoy-continuidad-publica-v1',estudioFavorable:true,estadoRadicacion:'PREPARACION_PENDIENTE',
   decisionInstitucional:'PENDIENTE',firmaContractual:'PENDIENTE',radicacionHabilitada:false,firmaHabilitada:false}};
 const html=renderStudy(study);
 assert.match(html,/Estudio digital aprobado/);assert.match(html,/ca-result ca-result--approved/);
 assert.match(html,/lucide-badge-check/);assert.match(html,/Condiciones institucionales pendientes/);
 assert.match(html,/Decision institucional<\/dt><dd class="ca-metric-text">Pendiente/);
 assert.doesNotMatch(html,/Revisar cuota y condiciones/);
 const ready=renderStudy(study,{cotizacionHabilitada:true,cotizar:()=>Promise.reject(new Error('NO_HTTP_IN_SSR'))});
 assert.match(ready,/Revisar cuota y condiciones/);
 assert.ok(ready.indexOf('Revisar cuota y condiciones')<ready.indexOf('Las 16 reglas del estudio'));
 for(const view of ['estado','firma']){
  const action=renderStudy(study,{cotizacionHabilitada:true,cotizar:()=>Promise.reject(new Error('NO_HTTP_IN_SSR'))},view);
  assert.match(action,/Revisar cuota y condiciones/);
 }
 const signature=renderStudy(study,{},'firma');assert.match(signature,/Preparacion documental pendiente/);
 assert.doesNotMatch(signature,/Firma no acreditada/);
 assert.doesNotMatch(html,/Tu solicitud requiere revision del estamento institucional correspondiente|APROBADO PARA RADICACION|Credito aprobado/);
 for(const mutation of [c=>c.radicacionHabilitada=true,c=>c.firmaHabilitada=true,
   c=>c.decisionInstitucional='APROBADA',c=>c.estudioFavorable='true']){
  const changed=structuredClone(study);mutation(changed.decisionPolitica.continuidad);
  assert.throws(()=>validarEstudioPublico(changed,id));
 }
 const rejected=structuredClone(study);rejected.decisionPolitica.estado='RECHAZADO';
 assert.throws(()=>validarEstudioPublico(rejected,id));
});

test('documentos del canal no muestran pagare de LINIX ni infieren una firma por la aprobacion',()=>{
 const {DocumentosAsociado}=load('asociado/PortalAsociado.tsx');
 const pending=renderToStaticMarkup(React.createElement(DocumentosAsociado,{institucion:{estado:'APROBADA_PENDIENTE_FIRMA'}}));
 assert.match(pending,/Preparacion documental pendiente/);
 assert.match(pending,/No hay un documento contractual disponible para firmar/);
 assert.doesNotMatch(pending,/Pagare|pagare|LINPAG|Documento firmado y verificado|<button|<a /);
 const verified=renderToStaticMarkup(React.createElement(DocumentosAsociado,{formalizacion:{documento:'COLO-FO.001',firmaAcreditada:true}}));
 assert.match(verified,/COLO-FO.001/);assert.match(verified,/Documento firmado y verificado/);
 assert.doesNotMatch(verified,/Pagare|pagare|LINPAG/);
});

test('V8 consume capacidad disponible y residual separados sin reinterpretar el legacy',()=>{
 const original=version8(),clean=validarEstudioPublico(original,id),html=renderStudy(clean);
 assert.equal(clean.capacidad.capacidadDisponible,'814791.75');
 assert.equal(clean.capacidad.capacidadResidualPostCuota,'609998.46');
 assert.equal(clean.capacidad.preliminarNoDecisoria,'614792');
 assert.equal(clean.autorizadoDecisionCrediticia,false);
 assert.match(html,/Version 8/);
 assert.match(html,/Capacidad disponible antes de la cuota nueva/);
 assert.match(html,/Residual despues de la cuota estimada/);
 assert.match(html,/814\.791,75/);assert.match(html,/609\.998,46/);
 assert.doesNotMatch(html,/614\.792|Calculo preliminar, no decisorio|Credito aprobado|Firmar pagare/);
 assert.match(html,/no acredita aprobacion ni una cuota contractual/);
});

test('V8 exige ambos campos exactos, dinero decimal y ausencia de aprobacion',()=>{
 const original=version8();
 for(const key of ['capacidadDisponible','capacidadResidualPostCuota']){
  const missing={...original,capacidad:{...original.capacidad}};delete missing.capacidad[key];
  assert.throws(()=>validarEstudioPublico(missing,id));
  for(const value of [814791.75,undefined,'NaN','Infinity','814.791,75','',{},true]){
   assert.throws(()=>validarEstudioPublico({...original,capacidad:{...original.capacidad,[key]:value}},id));
  }
 }
 assert.throws(()=>validarEstudioPublico({...original,autorizadoDecisionCrediticia:true},id));
 assert.throws(()=>validarEstudioPublico({...original,capacidad:{...original.capacidad,cuotaContractualDisponible:true}},id));
 assert.throws(()=>validarEstudioPublico({...original,capacidad:{...original.capacidad,campoPrivado:'SYNTHETIC_PRIVATE'}},id));
});

test('V8 null no recurre a V7 y conserva cero y residual negativo sin forzar decision',()=>{
 const original=version8();
 const pending={...original,capacidad:{...original.capacidad,capacidadDisponible:null,capacidadResidualPostCuota:null}};
 const html=renderStudy(pending);
 assert.equal((html.match(/Por determinar/g)||[]).length,2);
 assert.doesNotMatch(html,/614\.792|814\.791|609\.998/);
 for(const [available,residual] of [['0.00','-204793.29'],['900000.25','695206.96']]){
  const changed={...original,capacidad:{...original.capacidad,capacidadDisponible:available,capacidadResidualPostCuota:residual}};
  const clean=validarEstudioPublico(changed,id);
  assert.equal(clean.capacidad.capacidadDisponible,available);
  assert.equal(clean.capacidad.capacidadResidualPostCuota,residual);
  assert.equal(clean.decision,original.decision);assert.equal(clean.autorizadoDecisionCrediticia,false);
  assert.match(renderStudy(changed),new RegExp(available==='0.00'?'0,00':'900\\.000,25'));
 }
});

test('V7 mantiene residual historico sin inventar disponible ni adoptar los campos V8',()=>{
 const original=fixture(),legacy={...original,version:7,capacidad:{...original.capacidad,
  estado:'EVALUABLE',valor:'614792',preliminarNoDecisoria:'614792'}};
 const html=renderStudy(legacy);
 assert.match(html,/Version 7/);assert.match(html,/Residual del estudio/);
 assert.match(html,/Residual preliminar, no decisorio/);assert.match(html,/614\.792/);
 assert.doesNotMatch(html,/Capacidad disponible|Residual despues de la cuota estimada|814\.791|609\.998/);
 for(const version of [2,5,7]){
  assert.throws(()=>validarEstudioPublico({...version8(),version},id));
 }
});
test('el mismo contrato acepta estudios de diferentes expedientes sin UUID fijo',()=>{
 for(const sid of [id,'00000000-0000-4000-8000-000000000009']){
  const value=row(fixture(sid)),clean=validarSolicitudPublica(value);
  assert.deepEqual(clean.estudio,value.estudio);assert.equal(clean.acciones.radicar,false);
 }
 assert.equal(validarEstudioPublico(null,id),null);
});
test('rechaza otro UUID, aprobacion fabricada, datos privados y matriz parcial o repetida',()=>{
 const original=fixture();
 for(const change of [{solicitudId:'00000000-0000-4000-8000-000000000009'},
  {decision:'APROBADA'},{decision:'NO_AUTORIZADA'},{autorizadoDecisionCrediticia:true},
  {numeroDocumento:'SYNTHETIC_PRIVATE'},{reglas:original.reglas.slice(1)},
  {reglas:original.reglas.map((r,i)=>i===1?original.reglas[0]:r)},
  {fechaEvaluacion:'2026-10-20T14:00:00'},{fechaEvaluacion:'2999-10-20T14:00:00Z'}]){
  assert.throws(()=>validarEstudioPublico({...original,...change},id));
 }
 assert.throws(()=>validarSolicitudPublica({...row(),requisitosCumplidos:16}));
 assert.throws(()=>validarSolicitudPublica({...row(),procedencia:{...row().procedencia,origen:'HISTORICO_PILOTO'}}));
});
test('no convierte el preliminar en capacidad definitiva o cuota contractual',()=>{
 const original=fixture();
 assert.equal(validarEstudioPublico(original,id).capacidad.valor,null);
 for(const change of [{valor:'123'},{cuotaContractualDisponible:true},{estado:'EVALUABLE'},
  {preliminarNoDecisoria:'NaN'},{preliminarNoDecisoria:'Infinity'}]){
  assert.throws(()=>validarEstudioPublico({...original,capacidad:{...original.capacidad,...change}},id));
 }
});
test('Resultado muestra reglas y pendientes sin rechazo definitivo ni acciones financieras',()=>{
 const useState=React.useState;
 React.useState=initial=>useState(initial==='estado'?'evaluacion':initial);
 let html;
 const source=row();source.estudio.reglas.find(r=>r.codigo==='UNICO_FILA_9').estadoEvaluacion='NO_CUMPLE';
 try{html=renderToStaticMarkup(React.createElement(EspacioAsociado,{nombre:'SYNTHETIC',
  solicitudes:[validarSolicitudPublica(source)],busy:false,actualizar:()=>{},salir:()=>{}}));}
 finally{React.useState=useState;}
 assert.match(html,/Version 2/);assert.match(html,/Las 16 reglas del estudio/);
 assert.equal((html.match(/SYNTHETIC_RULE_/g)||[]).length,16);
 for(const text of ['Requiere subsanacion','Datos pendientes','No aplica','Politica pendiente',
  'Por determinar','Calculo preliminar, no decisorio','no son un rechazo definitivo','1.234.567']){
  assert.ok(html.includes(text),text);
 }
 assert.doesNotMatch(html,/NO_AUTORIZADA|Desembolsado|Firmar pagare|SYNTHETIC_PRIVATE/);
 assert.doesNotMatch(html,new RegExp(id));
});
