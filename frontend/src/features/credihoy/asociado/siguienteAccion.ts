import type {AutorizacionTitular,SolicitudPublica} from '../services/publico';

export interface SiguienteAccion {
  estado:'ACCION_PERSONAL'|'PROCESANDO'|'REQUIERE_REVISION'|'CONTINUAR'|'COMPLETADO';
  mensaje:string;
  destino:'autorizaciones'|'evaluacion'|'solicitud'|'simulador'|null;
}

// Only persisted, validated DTOs can supply a completed act or a study.
export function resolverSiguienteAccion(s:SolicitudPublica,autorizacion?:AutorizacionTitular|null,
  consultando=false):SiguienteAccion {
  const intake=s.seguimientoRadicacion;
  if(intake?.confirmada)return {estado:'COMPLETADO',mensaje:'Tu solicitud fue radicada y se encuentra en revision institucional. Te informaremos cuando tengamos una actualizacion.',destino:null};
  if(intake&&['EN_PROCESO','POR_CONFIRMAR','NO_DISPONIBLE'].includes(intake.estado))return {
    estado:'PROCESANDO',mensaje:'Estamos verificando el estado de tu radicacion. No necesitas enviar otra solicitud.',destino:null};
  if(intake?.cuotaAceptada)return {estado:'ACCION_PERSONAL',mensaje:'Tu cuota ya esta aceptada. Continua con la revision de tu radicacion.',destino:'simulador'};
  if(s.estudio?.decisionPolitica?.continuidad?.estudioFavorable)return {estado:'ACCION_PERSONAL',
    mensaje:'Tu estudio es favorable. Revisa la cuota y las condiciones de tu solicitud.',destino:'simulador'};
  if(s.estudio){
    const pendiente=s.estudio.reglas.some(r=>['PENDIENTE_POLITICA','NO_EVALUABLE','NO_CUMPLE'].includes(r.estadoEvaluacion));
    return {estado:'REQUIERE_REVISION',mensaje:pendiente?
      'Tu estudio esta disponible. Congente debe revisar las validaciones pendientes antes de decidir. No necesitas repetir tus autorizaciones.':
      'Tu estudio esta disponible. La decision crediticia requiere confirmacion de Congente.',destino:'evaluacion'};
  }
  if(s.institucional)return {estado:'REQUIERE_REVISION',
    mensaje:'Existe una decision registrada. Congente debe confirmar los documentos y el siguiente paso de formalizacion.',destino:null};
  if(consultando)return {estado:'PROCESANDO',mensaje:'Consultando el estado guardado de tu solicitud.',destino:null};
  if(!autorizacion)return {estado:'REQUIERE_REVISION',
    mensaje:'No pudimos confirmar el siguiente paso. Actualiza el estado; tus datos guardados no han cambiado.',destino:null};
  const central=autorizacion.centrales?.estado;
  if(central==='OTP_CONFIRMADA')return {estado:'REQUIERE_REVISION',
    mensaje:'Tu autorizacion de consulta esta confirmada. Congente debe confirmar el resultado del estudio; no solicites otro codigo.',destino:null};
  if(central==='INDETERMINADA'||central==='RECHAZADA')return {estado:'REQUIERE_REVISION',
    mensaje:'Congente debe revisar el resultado de tu autorizacion. No repitas el envio del codigo.',destino:null};
  if(central&&['ENVIADO','PENDIENTE_OTP'].includes(central)&&!autorizacion.centrales?.presentacion)
    return {estado:'REQUIERE_REVISION',mensaje:'El envio de tu codigo quedo registrado. Congente debe conciliar su estado antes de permitir otro intento. No solicites otro codigo.',destino:null};
  return {estado:'ACCION_PERSONAL',mensaje:['AUTORIZACION_AUSENTE','PENDIENTE_ACEPTACION_PERSONAL'].includes(autorizacion.estado)?
    'Revisa el documento y registra personalmente tu aceptacion si estas de acuerdo.':
    'Completa la autorizacion de consulta pendiente. El codigo de ingreso no sustituye esta autorizacion.',destino:'autorizaciones'};
}
