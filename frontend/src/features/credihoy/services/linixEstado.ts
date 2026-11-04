import {validarInstitucionalPublica} from './publico';
export interface EstadoLinixV1 {
  versionContrato: "credihoy-linix-estado-v1"; solicitudId: string; intencionId: string;
  kFlujo: string; numeroSc: null; estado: "FLUJO_CREADO_INCIDENCIA_REFERENCIAS";
  actividad: { codigo: 3883; nombre: "REGISTRAR REFERENCIAS SOLICITUD" };
  actualizadoAt: string; startEjecutados: 1;
  detalleTecnico?: { codigo: "ORA-20000"; procedimiento: string; mensaje: string; erroresRegistrados: number };
}
export interface EstadoInstitucionalLinix {
  versionContrato:'credihoy-linix-estado-v2';solicitudId:string;intencionId:string;kFlujo:string;numeroSc:string;
  estado:'APROBADA_EN_LINIX_PENDIENTE_FIRMA';actualizadoAt:string;startEjecutados:1;evidenciaSha256:string;
  actividad:{codigo:99001065;nombre:string;kSerie:string;ruta:'10003122';estado:'A';tipoEjecucion:'H'};
  aprobacion:{estadoSolicitud:'A';aprobada:true;fecha:string;aprobadorReferencia:string;actividad:6774;kSerie:string;estadoActividad:'E'};
  liquidacion:{liquidada:false;fecha:null};pagare:{tipo:string;sucursal:string;numero:string;estado:'U'};
  firma:{estado:'PENDIENTE';acreditada:false};
  incidencias:{registradas:number|null;enTareasActivas:number|null;enTareasEjecutadas:number|null};
  procedencia:{sistema:'LINIX';metodo:'SELECT_CA090_WORKFLOW_CA662';perfil:['390','111','CFA']};
}
export type EstadoLinix=EstadoLinixV1|EstadoInstitucionalLinix;

function validarInstitucional(value:Record<string,unknown>,id:string):EstadoInstitucionalLinix {
  const fail=():never=>{throw new Error('ESTADO_LINIX_INVALIDO');};
  const object=(v:unknown):Record<string,unknown>=>v&&typeof v==='object'&&!Array.isArray(v)?v as Record<string,unknown>:fail();
  const number=(v:unknown):string=>typeof v==='string'&&/^[1-9][0-9]{0,29}$/.test(v)?v:fail();
  const uuid=(v:unknown):string=>typeof v==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v)?v:fail();
  const a=object(value.aprobacion),t=object(value.actividad),n=object(value.pagare),i=object(value.incidencias);
  const firma=object(value.firma),liq=object(value.liquidacion),source=object(value.procedencia);
  const count=(v:unknown):number|null=>v===null?null:typeof v==='number'&&Number.isSafeInteger(v)&&v>=0?v:fail();
  const counts={registradas:count(i.registradas),enTareasActivas:count(i.enTareasActivas),enTareasEjecutadas:count(i.enTareasEjecutadas)};
  const unknown=Object.values(counts).every(v=>v===null);
  if(value.solicitudId!==id||uuid(value.solicitudId)!==id||value.estado!=='APROBADA_EN_LINIX_PENDIENTE_FIRMA'
    ||value.startEjecutados!==1||a.aprobada!==true||a.estadoSolicitud!=='A'||a.actividad!==6774||a.estadoActividad!=='E'
    ||typeof a.aprobadorReferencia!=='string'||!/^[A-Za-z0-9_.$#-]{1,30}$/.test(a.aprobadorReferencia)
    ||t.codigo!==99001065||t.ruta!=='10003122'||t.estado!=='A'||t.tipoEjecucion!=='H'||t.kSerie===a.kSerie
    ||typeof t.nombre!=='string'||!t.nombre.length||t.nombre.length>200||n.estado!=='U'
    ||firma.estado!=='PENDIENTE'||firma.acreditada!==false||liq.liquidada!==false||liq.fecha!==null
    ||source.sistema!=='LINIX'||source.metodo!=='SELECT_CA090_WORKFLOW_CA662'||JSON.stringify(source.perfil)!=='["390","111","CFA"]'
    ||(!unknown&&(Object.values(counts).some(v=>v===null)
      ||counts.registradas!==(counts.enTareasActivas??0)+(counts.enTareasEjecutadas??0)))
    ||typeof value.evidenciaSha256!=='string'||!/^[a-f0-9]{64}$/.test(value.evidenciaSha256))return fail();
  const safe=validarInstitucionalPublica({estado:'APROBADA_PENDIENTE_FIRMA',fechaDecision:a.fecha,
    actualizadoAt:value.actualizadoAt,documentos:'NO_ACREDITADOS',firma:'PENDIENTE',incidencias:'NO_DISPONIBLE'})!;
  return {versionContrato:'credihoy-linix-estado-v2',solicitudId:id,intencionId:uuid(value.intencionId),
    kFlujo:number(value.kFlujo),numeroSc:number(value.numeroSc),estado:'APROBADA_EN_LINIX_PENDIENTE_FIRMA',
    actualizadoAt:safe.actualizadoAt,startEjecutados:1,evidenciaSha256:value.evidenciaSha256,
    actividad:{codigo:99001065,nombre:t.nombre,kSerie:number(t.kSerie),ruta:'10003122',estado:'A',tipoEjecucion:'H'},
    aprobacion:{estadoSolicitud:'A',aprobada:true,fecha:safe.fechaDecision,aprobadorReferencia:a.aprobadorReferencia,
      actividad:6774,kSerie:number(a.kSerie),estadoActividad:'E'},
    liquidacion:{liquidada:false,fecha:null},pagare:{tipo:number(n.tipo),sucursal:number(n.sucursal),numero:number(n.numero),estado:'U'},
    firma:{estado:'PENDIENTE',acreditada:false},incidencias:counts,
    procedencia:{sistema:'LINIX',metodo:'SELECT_CA090_WORKFLOW_CA662',perfil:['390','111','CFA']}};
}

export function validarEstadoLinix(value: unknown, id: string): EstadoLinix {
  const fail = (): never => { throw new Error("ESTADO_LINIX_INVALIDO"); };
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  const p = value as Record<string, unknown>, a = p.actividad as Record<string, unknown> | undefined;
  if(p.versionContrato==='credihoy-linix-estado-v2')return validarInstitucional(p,id);
  if (p.versionContrato !== "credihoy-linix-estado-v1" || p.solicitudId !== id
      || typeof p.intencionId !== "string" || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(p.intencionId)
      || typeof p.kFlujo !== "string" || !/^[1-9][0-9]{0,29}$/.test(p.kFlujo) || p.numeroSc !== null
      || p.estado !== "FLUJO_CREADO_INCIDENCIA_REFERENCIAS" || p.startEjecutados !== 1
      || a?.codigo !== 3883 || a.nombre !== "REGISTRAR REFERENCIAS SOLICITUD"
      || typeof p.actualizadoAt !== "string" || !Number.isFinite(Date.parse(p.actualizadoAt))) return fail();
  const safe: EstadoLinixV1 = { versionContrato: "credihoy-linix-estado-v1", solicitudId: id,
    intencionId: p.intencionId, kFlujo: p.kFlujo, numeroSc: null, estado: p.estado,
    actividad: { codigo: 3883, nombre: "REGISTRAR REFERENCIAS SOLICITUD" },
    actualizadoAt: p.actualizadoAt, startEjecutados: 1 };
  if (p.detalleTecnico !== undefined) {
    const d = p.detalleTecnico as Record<string, unknown> | null;
    if (!d || d.codigo !== "ORA-20000" || d.procedimiento !== "PK_CA_WF_REFERENCIA.PR_INSERTA_REFER_SOLICITUD"
        || d.mensaje !== "Leyendo Variables Trama" || d.erroresRegistrados !== 2) return fail();
    safe.detalleTecnico = { codigo: "ORA-20000", procedimiento: d.procedimiento,
      mensaje: d.mensaje, erroresRegistrados: 2 };
  }
  return safe;
}
