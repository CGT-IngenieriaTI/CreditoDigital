const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const {load,id}=require('./helpers/cd8-fixture.cjs');
const {moneda}=load('money');
const {resolverSiguienteAccion}=load('asociado/siguienteAccion');
const {EspacioAsociado}=load('asociado/PortalAsociado.tsx');
const row={referencia:id,titulo:'Estudio pendiente',monto:'400000.00',plazoMeses:2,periodicidad:'Mensual',
 fechaCreacion:'2026-11-04T09:00:00-05:00',requisitosCumplidos:12,pendientes:[],
 siguientePaso:'Espera la revision de Congente.',ofertaDisponible:false,acciones:{radicar:false,firmar:false},
 procedencia:{origen:'FUENTE_OPERATIVA',estadoRegistrado:'RECIBIDA',actualizadoAt:'2026-11-04T09:00:00-05:00',consultadoAt:'2026-11-04T09:00:00-05:00'}};
const estudio={versionContrato:'credihoy-estudio-publico-v1',solicitudId:id,evaluacionBaseId:1,version:8,
 referenciaVersion:id,fechaEvaluacion:row.fechaCreacion,reglas:[{codigo:'UNICO_FILA_2',nombre:'Capacidad',resultado:'NO_EVALUABLE',estadoEvaluacion:'PENDIENTE_POLITICA',motivos:[],datosFaltantes:[]}],
 capacidad:{estado:'NO_EVALUABLE',valor:null,preliminarNoDecisoria:'614792',cuotaContractualDisponible:false,
 capacidadDisponible:'814791.750',capacidadResidualPostCuota:'609998.460'},decision:'PENDIENTE_REVISION',autorizadoDecisionCrediticia:false};
test('COP preserves scale, exact cents and large amounts without Number',()=>{
 for(const [value,expected] of [['814791.750','$ 814.791,75'],['609998.460','$ 609.998,46'],
  ['204793.29','$ 204.793,29'],['0','$ 0,00'],['-0.005','$ -0,01'],
  ['9007199254740993.12','$ 9.007.199.254.740.993,12']])assert.equal(moneda(value),expected);
 assert.notEqual(moneda('814791.750'),'$ 814.791.750');
 assert.match(moneda('814.791,75'),/Pendiente/);
});
test('V8 pending policy never links back to an empty form or repeat consent',()=>{
 const next=resolverSiguienteAccion({...row,estudio});
 assert.equal(next.estado,'REQUIERE_REVISION');assert.equal(next.destino,'evaluacion');
 assert.match(next.mensaje,/No necesitas repetir/);
});
test('persisted central confirmation never requests another authorization',()=>{
 const next=resolverSiguienteAccion(row,{estado:'CENTRALES_OTP_CONFIRMADA',centrales:{estado:'OTP_CONFIRMADA'}});
 assert.equal(next.estado,'REQUIERE_REVISION');assert.equal(next.destino,null);
});

test('sent OTP without current presentation requires reconciliation, not another send',()=>{
 const next=resolverSiguienteAccion(row,{estado:'CENTRALES_ENVIADO',centrales:{estado:'ENVIADO',presentacion:null}});
 assert.equal(next.estado,'REQUIERE_REVISION');assert.equal(next.destino,null);
 assert.match(next.mensaje,/No solicites otro codigo/);
});
test('personal act pending gives one truthful action; reads/errors cannot manufacture completion',()=>{
 assert.equal(resolverSiguienteAccion(row,{estado:'PENDIENTE_ACEPTACION_PERSONAL'}).estado,'ACCION_PERSONAL');
 assert.equal(resolverSiguienteAccion(row,null,true).estado,'PROCESANDO');
 assert.equal(resolverSiguienteAccion(row,null,false).estado,'REQUIERE_REVISION');
 assert.equal(resolverSiguienteAccion(row,{estado:'CENTRALES_INDETERMINADA',centrales:{estado:'INDETERMINADA'}}).destino,null);
});
test('existing dossier renders review without a false continue button or prominent UUID',()=>{
 const html=renderToStaticMarkup(React.createElement(EspacioAsociado,{solicitudes:[{...row,estudio}],busy:false,
 actualizar(){},salir(){},autorizacionHabilitada:true,centralesHabilitadas:true,autorizar:async()=>{throw Error('NO_PERSONAL_ACTION')}}));
 assert.match(html,/Continuemos con tu CrediHoy/);assert.match(html,/Ver mi estudio/);
 assert.doesNotMatch(html,/Continuar estudio|Continuar mi solicitud/);
 assert.doesNotMatch(html,new RegExp('>'+id+'<'));
});
