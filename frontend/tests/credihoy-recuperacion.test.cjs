// Synthetic React states only. PDF rendering is covered by the browser regression.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load}=require('./helpers/cd8-fixture.cjs');
const root=path.resolve(__dirname,'../src/features/credihoy');
const file=path.join(root,'asociado/PortalAsociado.tsx'),m=new Module(file);
m.require=target=>target.endsWith('.css')?{}:target.endsWith('PdfScrollViewer')?{PdfScrollViewer:()=>null}:
 target.startsWith('.')?load(path.relative(root,path.resolve(path.dirname(file),target))):require(target);
m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,
 target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
const {EspacioAsociado,EstadoContractual}=m.exports;
const props={nombre:'SYNTHETIC',busy:false,actualizar:()=>{},salir:()=>{},
 formularioBorrador:React.createElement('form',{'data-testid':'nuevo-borrador'},'SYNTHETIC_DRAFT')};
test('fallo de lectura no ofrece crear solicitud y conserva reintento sin formulario',()=>{
 const html=renderToStaticMarkup(React.createElement(EspacioAsociado,{...props,solicitudes:null}));
 assert.match(html,/No pudimos consultar tu solicitud en este momento/);
 assert.match(html,/Reintentar consulta/);
 assert.doesNotMatch(html,/Solicitar mi CrediHoy|Inicia tu solicitud|nuevo-borrador|SYNTHETIC_DRAFT/);
});
test('solo una lista vacia acreditada ofrece iniciar solicitud',()=>{
 const html=renderToStaticMarkup(React.createElement(EspacioAsociado,{...props,solicitudes:[]}));
 assert.match(html,/Solicitar mi CrediHoy/);assert.doesNotMatch(html,/Reintentar consulta/);
});
test('carga pendiente no muestra error ni inicio de otra solicitud',()=>{
 const html=renderToStaticMarkup(React.createElement(EspacioAsociado,{...props,solicitudes:null,busy:true}));
 assert.doesNotMatch(html,/Solicitar mi CrediHoy|Reintentar consulta|SYNTHETIC_DRAFT/);
});

test('firma incierta no se presenta como OTP o documento firmado y no muestra codigos internos',()=>{
 for(const estado of ['ENVIANDO','INDETERMINADA']){
  const html=renderToStaticMarkup(React.createElement(EstadoContractual,{estado}));
  assert.match(html,/confirmando|pendiente de confirmacion/);
  assert.match(html,/No vuelvas a firmar ni solicites otro codigo/);
  assert.doesNotMatch(html,/Codigo contractual verificado|documento firmado|INDETERMINADA|ENVIANDO/);
 }
});

test('recibo, conciliacion y rechazo tienen estados veraces sin autorizar reenvio automatico',()=>{
 for(const [estado,text] of [['FIRMA_REPORTADA_NO_VERIFICADA',/evidencia aun deben verificarse/],
   ['CONCILIADA_PROCESADA',/verificacion documental siguen pendientes/],
   ['CONCILIADA_NO_PROCESADA',/nuevo acto personal/],
   ['FALLO_DEFINITIVO_DOCUMENTADO',/revisar el resultado antes de otro intento/],
   ['OTP_VALIDADA',/firma aun requiere tu confirmacion personal/],
   ['NEW_PRIVATE_DIAGNOSTIC',/Consulta su estado sin repetir el envio/]]){
  const html=renderToStaticMarkup(React.createElement(EstadoContractual,{estado}));
  assert.match(html,text);assert.doesNotMatch(html,new RegExp(estado));
 }
});
