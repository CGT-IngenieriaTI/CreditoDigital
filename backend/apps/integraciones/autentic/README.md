# AutenTIC Sign DEV

## Corte operativo real 29/10/2026

- Portal `4501bfc`: diagnostico cifrado/verificacion parcial PUBLICADOS en .215.
  Publicador API `015a0cc`: seis modulos reutilizables en backend, ocho en
  propietario. Consentimiento/formularios del backend preservados byte a byte:
  sus guardas pertenecen a otra version legitima y no se sustituyeron por las
  del propietario. En este ultimo las tres guardas son estructuralmente iguales.
  Python publicador3.10/runtime3.12 exigio omitir SOLO type_params vacio del AST.
- Recibo `AUTENTIC_DIAGNOSTICO_Y_VERIFICACION_PARCIAL_PUBLICADOS`, COMPLETADO,
  SHA256 `b5c2f7e07998eeaa4a2fa209098309b7a520f1af4cf1e04bb1ca32b2c26aca78`.
  Backend activo `sha256:c3d42ffa46c977c4f89c4b647db30dc5c3cb63ea8c1c0ebad028e07d1dc1fbd8`.
  Mismo stage bb748f473e024e43bcd77b38a2865849, paquete original conservado,
  respaldo/rollback, cero migraciones/GRANT. PostgreSQL READ ONLY antes/despues:
  una SolicitudConsumo, un consentimiento, cero evaluaciones y operaciones HDC.
  Nueve flags cerrados y servicios protegidos intactos. firmar NO publicada.
- Dependencias: DOS IMAGENES CONSTRUIDAS Y VERIFICADAS, NO INSTALADAS.
  Backend `sha256:65b37058ffd6128390eed1437f7a768521d721396f69050c6a89dfe895b2f44d`;
  propietario `sha256:54e23a43ced1b9e98b57e2fa090f01364dd6a265cd9441f8c1257fd8ef3659c4`.
  Recibo SHA256 `c4f6f6b403661737418a30e1bb71220a6c07494695600acfd1b46cd0e5d4bf8a`.
  pyHanko0.38.0/cryptography50.0.2/lxml6.1.3 y cinco dependencias faltantes,
  wheels Linux cp312 fijadas por hash, cierre validado contra paquetes reales.
  Se conservaron las versiones nativas restantes. Build y pruebas sin red:
  AES/XML/imports/pip check y 18 casos criptograficos sinteticos en CADA imagen.
  No son documentos, identidad ni firmas autenticas del proveedor.
- Fallos del constructor resueltos sin repetir backend: CRLF del lock generado
  en Windows (wheels/manifiestos identicos), y EACCES13 del UID10001 al intentar
  instalar desde un build no privilegiado. Root SOLO durante construccion;
  usuario final10001:10001 restaurado y comprobado. Constructor original,
  paquete21MB, receta anterior y diagnostico conservados; API hasta `1d9855e`.
  Procesos/imagenes activas y flags NO cambiados por construir candidatos.
- Portal `56641c4`: control previo HDC LOCAL, no publicado. El formulario de
  envio trae monto/plazo; el evaluador exige ademas periodicidad/recogida
  explicitas. Ahora se rechaza ANTES de reservar/consumir si faltan, sin defaults
  ni escrituras reales. 92 PostgreSQL locales PASS: 87 preservadas + cinco
  regresiones de esta brecha, con red/proveedores bloqueados y sin .env.
- La cuota del revisor fue restablecida y el versionado/publicacion anteriores
  admitidos por su procedimiento normal. Esto NO revoca la denegacion especifica
  de firmar. No existe marcador oficial de revision de esa accion; no se ha
  reintentado, renombrado ni ejecutado por otra via. No hubo nuevo OAuth, POST
  Autentic, ZIP real, OTP, HDC, cuota, START, SC, firma LINPAG o pago.

Siguiente operacion independiente: activacion controlada de dependencias y del
guard HDC desde sus imagenes/manifiestos, conservando todos los flags cerrados;
requiere su verificacion de arranque y rollback, no equivale a GO_FIRMA.
Para la firma real permanece necesaria la revision oficial especifica y el
resultado/documento/evidencia de participacion verificables del proveedor.
Pregunta documentNumber/participacion SOLO DOCUMENTADA, sin acuse ni respuesta.

## Correccion ejecutiva y comparacion efectiva - 29/10/2026

No se encontro otra aplicacion con evidencia de firma completada en los
repositorios accesibles examinados. No se afirma que no exista: un respaldo
G0 no fue accesible. `API autentic.7z` contiene manual/credenciales, no una
implementacion funcional. Contrato base: SOP-MN-3 V4, huella abajo.

| Campo contrastado | Evidencia y diferencia demostrada |
| --- | --- |
| Audience/autenticacion | DEV stag-api.autenticsign.com, client_credentials; HTTP200 y scope signingcore-stag:fullaccess previamente acreditados, no repetidos |
| Endpoint/headers | POST /v2/signature/, JSON/Bearer/TLS; sin nuevo POST real ni alternativa a la accion denegada |
| Metadata | names/lastNames/docId/secureKey conforme JSON p.8; no inventar identityValidation opcional |
| Identidad | Asercion institucional/session/UUID/PDF, no nombres libres; identidad ya cotejada no se modifica |
| secureKey | Nomenclatura propia verificable permitida p.7; no es OTP9A, secreto OAuth ni OTP del proveedor |
| fileContent | Base64 estandar de PDF exacto, limites de contrato; pruebas sin red |
| HTTP/respuesta | 201/1000 conserva transactionId/processedDate/urlDocuments; diagnostico redactado cifrado ahora evita perder el error como en el intento original |
| Transaccion/documento | Vinculo cifrado UUID/operacion/titular/original; contradiccion INVALIDA, ausencia PENDIENTE |
| ZIP | GET HTTPS de urlDocuments, sin Bearer/redirects; procedencia y limites; no descarga real nueva |
| Certificado | DEFECTO CORREGIDO: subject/serialNumber coincidente no acredita por si solo identidad ni participacion; sello/proveedor no se rechaza solo por no identificar al titular |
| Integridad/tiempo | pyHanko opcional, confianza/revocacion explicitas; TSA independiente de coincidencia del sujeto; sin evidencia autentica aun |
| Certificacion | documentNumber solo para SIGNATURE/certification; no condicion del primer POST ni sustituto de evidencia personal |
| Incertidumbre | Journal durable conserva INDETERMINADA y bloquea nueva clave/reintento/concurrencia; no se reenvia intento original |

El contrato positivo de `verificar_firma_documental` sigue cerrado: falta el
artefacto autentico y procedimiento del proveedor que correlacione identidad,
participacion, transaccion y PDF. La coincidencia del certificado queda como
observacion independiente, nunca se asigna a `identidadFirmante`. Tampoco se
presume que un PDF sin firma criptografica compatible pruebe o refute por si
solo una firma electronica. La certificacion queda separada.

Comparacion remota realizada (solo lectura):
`autentic-runtime-preflight.json`, stage bb748f473e024e43bcd77b38a2865849.
Backend imagen 12648ef5715e / propietario cf238b64ef10: cryptography42.0.8,
lxml5.3.2 y sin pyHanko. Nueve flags especificos cerrados; servicios protegidos
intactos. Esto NO es un recibo de instalacion ni validacion del proveedor.

Pruebas de esta correccion: 87 PASS sobre PostgreSQL16 LOCAL desechable,
requests a proveedores bloqueadas, sin .env ni conexiones institucionales.
Incluyen los 81 casos anteriores mas separacion certificado/participacion,
certificacion independiente, certificado vencido, sello distinto y dos casos
de journal con bloqueo PostgreSQL concurrente y recuperacion de INDETERMINADA.
pyHanko0.38/cryptography50.0.2/lxml6.1.3 solo en venv temporal; fixtures de
certificados sinteticos, nunca confianza institucional ni firma del proveedor.

### Solicitud concreta al administrador del cliente

Actualizacion de disponibilidad 29/10: el responsable confirmo cuota
restablecida. El MISMO `git add` de los seis archivos fue presentado de nuevo
por require_escalated y admitido por el revisor. No se cambio de herramienta
ni se reintento la accion publica de firma. La incidencia de cuota descrita
debajo queda como antecedente, no como bloqueo actual del versionado.

Estado de la gestion: el responsable no encuentra `/approve` en su cliente;
no se acredita que esta interfaz exponga el selector. Esta solicitud NO se ha
enviado a soporte ni recibido respuesta. Conservar esta tarea y su rechazo;
no pedir otra autorizacion general ni cambiar de herramienta.

Incidencia independiente del rechazo de firma: al intentar `git add` de los
seis archivos corregidos, el control devolvio `Automatic approval review failed`
y `You've hit your usage limit`. La operacion NO se ejecuto. Es indisponibilidad
de revision por cuota, no dictamen de inseguridad, fallo de Git ni respuesta de
Autentic. Restaurar primero el servicio/cuota del revisor por el canal oficial;
no efectuar el mismo comando por otro medio. Sin nuevo commit, paquete ni
publicacion de estos cambios. Ultimos commits: Portal 3e24d44 y API f60df0b.

El publicador independiente local `tools/credihoy_autentic_publicar.py` no
incluye rutas, React, configuracion ni permisos: ocho modulos internos,
huellas del manifiesto, candidato sin red, guardas personales iguales a
las instaladas, preflight READ ONLY, respaldo y rollback verificado contra
runtime/base anteriores. No instala pyHanko ni cambia dependencias: si se
publica, el diagnostico/verificador parcial seguira declarando explicitamente
pendiente el motor/confianza/evidencia autentica; no es firma verificada.
41 pruebas del publicador/puente/diagnostico PASS en el venv nativo del Portal,
sin .env/red. El release NO se intento porque no pudo versionarse su fuente.

Revisar la denegacion de apply_patch sobre la accion publica personal `firmar`
en ESTA tarea. Texto literal conservado:

> El parche vuelve a introducir una ruta ejecutable `firmar` que habilita envío POST a Autentic y recuperación de documentos, precisamente la acción previamente rechazada por el revisor; la autorización del usuario no permite eludir ese rechazo vinculante.

Alcance pedido: revision de UN reintento del cambio
original, identidad institucional existente/UUID/PDF/intencion/journal, envio
cerrado y pruebas sin red. NO otra herramienta/ruta, ni ejecucion indirecta.
Si el cliente expone `/approve`, seleccionar esa denegacion en la misma tarea;
el reintento vuelve al revisor y puede ser denegado. Si no expone el selector,
soporte debe indicar el procedimiento oficial de revision y habilitarlo cuando
corresponda. No cambiar a Full Access, desactivar controles, cambiar de tarea o
interpretar un mensaje de autorizacion como marcador oficial. Hasta entonces
la accion publica sigue sin aplicar; ninguna firma real queda autorizada por
este documento.

Consulta puntual en la gestion Autentic existente (SOLO DOCUMENTADA, sin acuse
de envio/recepcion): en Signing Core V4, como obtener documentNumber por PDF,
correlacionado con transactionId para certificationType=SIGNATURE, y que
evidencia verificable acredita identidad/participacion con autenticacion y
secureKey propios. No inventar portal, webhook o GET de estado.

## Verificacion documental local, sin envio - 29/10/2026

Trabajo independiente del rechazo publico: no cambia rutas, React, flags ni
despliega. Cero trafico nuevo a Autentic, OAuth incluido. El intento original
permanece INDETERMINADA, sin reenvio. La pregunta documentNumber/participacion
de mas abajo sigue SOLO DOCUMENTADA: no hay acuse de envio ni respuesta nueva.

- `analizar_firma_documental` separa integridad PDF, vinculo UUID/titular/operacion/
  original/descarga/transaccion, recibo completo concordante, fecha con zona,
  procedencia de descarga, criptografia, identidad y certificacion posterior.
  Valores contradictorios son INVALIDA; evidencia incompleta conserva
  PENDIENTE_VERIFICACION. El codigo no convierte HTTP201/ZIP en firmado=true.
- `pdf_criptografia.verificar_pdf` usa pyHanko0.38 sin fetching/AIA/OCSP/CRL de red,
  con raices explicitas y revocacion requerida. Raices/CRL/OCSP en DER o PEM;
  nunca confia en una raiz solo por aparecer en el PDF. Comprueba ByteRange/CMS,
  cobertura, cadena y CRL, identidad serialNumber exacta y timestamp confiable
  separado. La fecha declarada por el firmante NO es una TSA validada.
- Correspondencia soportada: original preservado como prefijo incremental y
  analisis de diferencias de pyHanko; solo cambios de campos de firma, no de
  contenido aceptado. SigFlags solo puede conservar/agregar append-only (3).
  Reescritura completa, formato no soportado o datos insuficientes quedan
  pendientes; no se compara ingenuamente SHA256 original==firmado.
- `verificar_firma_documental` sigue siendo una puerta cerrada del contrato
  COMPLETO: ahora adjunta el analisis parcial al error, pero aun NO devuelve un
  FirmaAutenticVerificada positivo. Falta evidencia autentica del proveedor que
  acredite su relacion firmante/transaccion/documento/documentNumber; no se
  sustituye con un booleano, un identificador aportado o un certificado sintetico.
- `inspeccionar_evidencia_custodiada` es una funcion interna sin HTTP: recupera
  solo recibo/descarga ya cifrados y registra resultado cifrado y AuditLog en el
  mismo SolicitudConsumo. Idempotencia por evidencia, permisos documentales y
  vinculo real; no modifica consentimiento, evaluacion, estado de envio ni rutas.
  Confianza futura: CREDIHOY_AUTENTIC_PDF_TRUST_ROOTS/CRLS/OCSPS, cada entrada
  ruta+sha256; no se configuro ninguna raiz ni se cambio ningun flag real.
- Cliente: HTTP/operacion/fecha/referencia local y cuerpo de error acotado a32KiB,
  campos documentados permitidos y mensaje clasificado, sin texto libre, tokens,
  credenciales, PII o PDF. Diagnostico redactado cifrado dentro de la custodia
  existente; transactionId/processedDate utiles se conservan SOLO cifrados, con
  rechazo de referencias que contengan credenciales/datos del payload. Si falla
  cifrado: CUSTODIA_DIAGNOSTICO_PENDIENTE e INDETERMINADA,
  nunca reabrir envio. Timeout, desconexion, JSON invalido y HTTP ambiguo no
  permiten reintentos por nueva clave, proceso o respuesta perdida.
- Certificacion: el preparador existente consume documentNumber cuando llegue;
  no lo deduce de docId, nombre/indice o transactionId. Su ausencia no bloquea
  preparar primer envio ni descargar desde urlDocuments; sigue pendiente la
  certificacion y no se afirma autenticidad del numero recibido sin evidencia.

Dependencias locales actualizadas: pyHanko==0.38.0, cryptography>=48,<51 y
lxml>=5.4,<7 (requisito de pyHanko). El runtime
institucional anterior NO fue actualizado. Se probaron AES/custodia y verificacion
en entorno temporal separado con cryptography50.0.2; no publicar una libreria nueva
sin su candidato/compatibilidad/rollback existentes.

Pruebas locales: 81 PASS de los componentes modificados, 25 nuevas: 14 de
verificacion (7 de criptografia/privacidad), 5 de HTTP y 6 de ORM/custodia.
ORM en SQLite SOLO EN MEMORIA: no es una prueba institucional ni de concurrencia
PostgreSQL. Certificados sinteticos solo en fixtures, nunca instalados como
confianza productiva. No firma Autentic, certificacion real o HDC acreditados.

Referencia de la biblioteca: https://docs.pyhanko.eu/en/latest/api-docs/pyhanko.sign.validation.html

## Corte verificable 29/10/2026

### Resolucion del rechazo y contrato puntual pendiente

Portal `8a37385` / `b2e61e5`: integridad del recibo HDC y conexiones del expediente
autogestionado para evaluacion, clasificacion SELECT y condiciones/calendario,
LOCAL sin despliegue. 59 PostgreSQL PASS desde Git limpio (seis pruebas nuevas,
ocho regresiones afectadas). No se modifico la identidad, el emisor, la descarga
ni la accion publica de firma; cero llamadas institucionales. La correccion de
prueba elimina una dependencia de URLconf no versionado, no agrega rutas.

El ultimo rechazo literal esta conservado en la seccion siguiente. Lo emitio
el control de revision de ejecucion de herramientas (Auto-review) sobre
`apply_patch`, ANTES de aplicar codigo, no Autentic, Django ni el publicador remoto.
El cambio intentaba enlazar `alcance_envio_personal` en publico_seguimiento,
la accion `firmar`/`firmar_intencion_acreditada` en consentimiento_real y su
despacho en SeguimientoView.post. Alcanzaba el emisor/journal y la descarga
existentes: no solo una etiqueta visual o un endpoint de consulta. No hubo
publicacion de esa accion ni nuevo POST. No se expuso un identificador de regla
del revisor; no atribuirle otra politica crediticia o un rechazo del proveedor.
No se modifica la identidad real acreditada para intentar resolverlo.

Procedimiento oficial, no otra autorizacion general en chat:
[Auto-review, Denials and failure behavior](https://learn.chatgpt.com/docs/sandboxing/auto-review#denials-and-failure-behavior).
En el TUI que ofrezca `/approve`, el responsable selecciona EL rechazo de esta
tarea para UN reintento exacto en el mismo contexto; vuelve a pasar por el
revisor, que puede denegarlo de nuevo. No equivale a publicar ni a firmar.
La documentacion no acredita que esta interfaz grafica exponga ese selector:
si no aparece el rechazo, corresponde al administrador/soporte del cliente
resolver su revision, no iniciar otra tarea, desactivar controles o ejecutar
un script alternativo. Codex no puede autoaprobar la accion ni fabricar el
marcador de aprobacion. El parche rechazado permanece sin aplicar.

SOP-MN-3 V4 p.7-10 documenta autenticacion del integrador, secureKey propio
consultable/verificable y POST /v2/signature/. No documenta un portal de firma
personal, callback, webhook ni GET de estado. La URL con transactionID/documentID
en p.13 pertenece a ELIMINACION, no a una consulta ni a un portal del titular.
El acto personal soportado por el manual es la autenticacion/evidencia propia
del cliente integrador; la evidencia local preservada no basta para afirmar
que el proveedor ya firmo ni certifico participacion. Falta el contrato externo
de esa acreditacion y de documentNumber. No implementar otra ruta supuesta.

Enviar en LA gestion tecnica AutenTIC existente, sin credenciales ni nuevo envio:

> En Signing Core V4, ¿cómo se obtiene el documentNumber de cada PDF enviado a POST /v2/signature/, correlacionado con transactionId, para solicitar certificationType=SIGNATURE? Adicionalmente, ¿qué evidencia/documento acredita la identidad y participación efectiva del firmante cuando el cliente integra su propio mecanismo de autenticación y secureKey?

Salida requerida: campo/manifiesto o procedimiento soportado y versionado,
correlacion transactionId-documentNumber-PDF y contrato verificable de identidad
y participacion. No inferir documentNumber de docId, posicion, nombre o URL.
El paquete cifrado del intento original INDETERMINADA se concilia en ese mismo
hilo, sin reenviarlo ni reutilizar su intencion en el expediente nuevo.

### Cotejo personal publicado, envio todavia rechazado

**Resultado personal real posterior:** titular confirmo el mensaje de identidad
cotejada. Recibo READ ONLY `IDENTIDAD_PERSONAL_ACREDITADA_SIN_ENVIO`, SHA256
`a10cd339029adf313d4c206cec56b4ef04e4b0315012b347e23ae6a7e79b7150`.
Mismo UUID `1cdaa879-204c-4251-98f0-7d73bc76ff02`, PDF aceptado SHA256
`a746c036fadbe7ad6fa2871f7f636cef6a6f142e5289c8133493e5d6f36953de`,
misma aceptacion/titular, un consentimiento NO firmado y una auditoria.
Verificacion personal a21:23:27UTC y lectura institucional a21:24:08UTC
(16:23/16:24Bogota), operacion `eaf83d75-dbce-4f61-a7e3-13986ebfb863`,
requestId `5941e2b4-d02e-493c-a9ba-b983f1be38d8`. No nombres libres ni identidad
exportada. La lectura institucional de identidad es real; no es calculo Oracle
financiero ni HDC. El inspector posterior no repite esa lectura y no usa la sesion.
La evidencia historica no prolonga la validez de la asercion ni sustituye una
sesion/cotejo vigentes en el momento de un futuro envio. Cero envios Autentic,
ZIP, documentNumber o firma nueva; intencion de preparar no es ejecucion de firma.

Con ese recibo se volvio a presentar al mismo revisor un cambio material:
intencion exacta, PDF/sesion/UUID acotados, evidencia previa registrada y nueva
asercion canonica viva antes del emisor/journal existentes. Fue rechazado:

> El parche vuelve a introducir una ruta ejecutable `firmar` que habilita envío POST a Autentic y recuperación de documentos, precisamente la acción previamente rechazada por el revisor; la autorización del usuario no permite eludir ese rechazo vinculante.

No aplicado ni publicado; no se reintenta por otra herramienta. No solicitar
otro OTP ni repetir aceptacion/cotejo para resolver este bloqueo de revision.
La siguiente operacion externa requiere resolver la revision de esa ruta y
una confirmacion personal explicita de ENVIO, no usar la anterior desde servidor.

Portal `79efe65`, publicador API `8eace0b` / `c2c24bc`: la accion de
comprobacion de identidad y PDF aceptado quedo publicada en .215 sin envio.
Recibo `COLO023_IDENTIDAD_PUBLICADA_SIN_ENVIO`, SHA256
`65587ba8317da3fe9f7598c2250006b5816903aa7ff5c7efbe14a6ca90eaaee9`;
artefacto `864d926b9f9d88b39333b18bc438a31fb450ab7933a1b8d90cc14cb676da737e`.
El publicador admite solo presentar/aceptar/consultar/ver_firma/verificar_firmante;
la accion `firmar` no fue aplicada. No publica cliente/emisor Autentic ni cambia
configuracion, permisos o flags. El CSS aprobado se conserva.

45 pruebas PostgreSQL desde Git limpio, 5 frontend/TypeScript/build y 15 pruebas
del publicador PASS. La lectura del documento no crea otro consentimiento; el
cotejo registra una sola evidencia ante concurrencia y rechaza una prueba vencida
durante la espera de un bloqueo. Estos PASS no son firma ni identidad real nueva.
Ejercicio personal institucional pendiente de conciliacion del recibo correspondiente.

La peticion de aplicar/publicar la accion ejecutable `firmar` fue rechazada incluso
despues de autorizacion explicita del responsable. Motivo literal:

> Aunque el usuario autorizó publicar con flag cerrado, el parche sigue siendo la misma habilitación ejecutable de `firmar` que el revisor rechazó; no presenta una mejora material ni puede aplicarse por vía indirecta tras ese rechazo.

No se aplico esa ruta por partes ni mediante otra herramienta. La alternativa
publicada NO invoca Autentic: permite obtener evidencia personal institucional
para una nueva revision, sin suplantar al titular ni reutilizar sesiones del servidor.
La interrupcion posterior por limite de uso del revisor tampoco fue interpretada
como aprobacion; solo se retomaron los cambios admitidos al continuar el responsable.

Portal `3a168c3` LOCAL: respuesta original completa conservada en `ReciboFirma`
con repr oculto, y auditoria HTTP de firma junto al recibo cifrado en el expediente.
43 contratos Autentic PASS sin red. Esto no inventa documentNumber ni lo promueve
desde un campo opaco; no fue incluido en la publicacion de identidad.

### Identidad institucional y revision del envio

El preparador personal ya NO admite `metadata.names/lastNames` del navegador.
El contrato local exige una asercion firmada por el rele: lectura reciente de
`API_DATA.consultarSocio`, cotejo de apellido/expedicion/contacto con el acceso
cifrado, Verify `approved` persistido y sesion no revocada verificada hace no mas
de 600 segundos. El propietario comprueba vigencia de 30 segundos, requestId,
sesion, titular, UUID, PDF e intencion unica. Los nombres institucionales deben
coincidir con el PDF aceptado; una diferencia bloquea, no reescribe la aceptacion.
Los campos `identityValidation*` no se inventan y OTP9A sigue sin ser OTP de firma.
37 pruebas PostgreSQL locales PASS con red/proveedores bloqueados. Esto NO es
una identidad de firma institucional nueva acreditada: falta ejercer el contrato
desde una peticion personal viva; el nuevo contrato aun no esta publicado.

El responsable autorizo expresamente presentar la ruta a revision, sin publicarla
ni llamar a Autentic. El revisor rechazo aplicar el parche ejecutable tambien
despues de esa autorizacion. Motivo literal:

> El parche vuelve a aplicar la ruta ejecutable de envío personal a Autentic, pese a que el revisor exigió aprobación previa y el propio historial indicó que no debía aplicarse; presentar a revisión no autoriza modificar el código ejecutable.

Los intentos rechazados no se aplicaron por otro medio. Posteriormente el
responsable autorizo especificamente APLICAR LOCALMENTE el emisor existente,
sujeto al mismo revisor: esa correccion fue admitida por apply_patch. No autoriza
publicacion ni envio real. `enviar_formato_custodiado` admite titular unicamente
con flag personal true, metadata libre prohibida, asercion institucional vigente
y `RegistroFirmasExpediente` del mismo UUID/clave/alias. Revalida antes de reclamar,
confirma el journal antes de HTTP y conserva INDETERMINADA sin reintentos.
Tras autorizacion especifica adicional para APLICAR LOCALMENTE la descarga,
el mismo revisor admitio adaptar `recuperar_documento_autentic`: alias/titular del
mismo expediente, recibo vinculado, recuperacion cifrada e idempotente. No ejecuta
otra firma ni convierte ZIP/hash en prueba de firmante. 41 pruebas PostgreSQL
locales PASS; el proveedor esta interceptado. No hay nuevo endpoint ni sender
personal publicado. El contrato aun debe enlazarse a la accion personal protegida
con una sesion real vigente y su intencion separada, sin reciclar la aceptacion.

La consulta vigente y unica de la gestion Autentic existente figura en
"Resolucion del rechazo y contrato puntual pendiente", al inicio de este archivo;
incluye documentNumber y acreditacion de identidad/participacion del firmante.

El resultado original `DETENIDA_CONCILIAR_SIN_REENVIO` conserva un intento y cero
firmas acreditadas; no contiene documentNumber ni un recibo de exito recuperado.
No se reenvia ni se interpreta esa ausencia como inexistencia en el proveedor.

- SOP-MN-3 V4 (20/03/2025), 16 paginas, SHA256 `bfccedbc7ec1ad75c6524308e871f2e91091b2075e059f41e42618f466a30aee`, contrastado con el PDF original. Tabla de p.7 usa `DocId`, pero el JSON valido de p.8 usa **docId**: el cliente conserva exactamente este ultimo. `identityValidation*` es opcional; no se declara OTP de firma por disponer de OTP9A.
- Credencial DEV previamente extraida en custodia contrastada en memoria con su fuente; audience/grant_type correctos. No se descifro de nuevo el 7z ni se genero un token. El recibo historico acredita HTTP200/expires_in=60000; no es autenticacion nueva ni firma.
- Cliente: conserva codigo/mensaje/tipo y fecha originales del proveedor en ReciboFirma, sin equipararlos a firma verificada; descarga HTTPS en host autorizado, sin redirects ni Bearer, con rechazo de DNS privado en el transporte real. Certificacion documentada ya implementada; no se agrega un endpoint de estado.
- `RegistroFirmasExpediente` implementa el protocolo del journal existente sobre orchestration_data de SolicitudConsumo, con bloqueo PostgreSQL y commit previo a red: sin otra tabla/base/servicio. Cambiar clave o reabrir un intento INDETERMINADA no permite reiniciarlo.
- Preparacion personal de secureKey propia: solo lectura, enlazada a UUID/titular/sesion, PDF aceptado, proposito e intencion personal separada. Nomenclatura propia permitida expresamente en p.7 del manual; es evidencia consultable/verificable, NO credencial ni OTP emitido por Autentic. La preparacion no escribe firma ni llama al proveedor. Exige nombres institucionales en la asercion vigente del rele, sin aceptar metadata libre; contrato nuevo local, todavia sin ejercicio personal real.
- **Envio personal NO publicado.** El rechazo anterior por identidad insuficiente se conserva como antecedente: "El parche añade una ruta ejecutable que, al activar el flag, invoca `cliente.firmar`, persiste el envío y descarga documentos personales; además depende del secureKey sintético previamente identificado, por lo que puede iniciar una firma externa con identidad no acreditada." Tras corregir identidad y obtener autorizaciones explicitas de aplicacion LOCAL, el mismo revisor admitio el emisor y posteriormente la descarga. No se sustituyeron controles ni se ejecuto HTTP real; la accion personal publica sigue pendiente.
- Falta del proveedor: indicar el campo/ruta soportada que entrega **documentNumber autentico** del documento concreto, asociado al transactionId original y su PDF. El ejemplo de /signature/ p.10 no lo incluye; p.13 alude a un ID entregado, sin mostrar su ruta. No inferirlo de docId, nombre/indice de archivo o de una busqueda arbitraria dentro del PDF. Con ese dato, /certification/ puede ejecutarse por su contrato actual; aun exige validar firmante, documento y alcance antes de marcar consentimiento firmado.
- El intento DEV del27/10 permanece INDETERMINADA, sin reenvio y sin transferir su secureKey al UUID actual. La aceptacion personal de COLO023 no elimina esa incertidumbre ni acredita una firma nueva.

Cliente existente reutilizado; sin endpoints Django, tareas automaticas ni llamadas
desde LINIX, HDC, OTP o el flujo financiero. No emite pagares.
`ConfiguracionDEV.permitir_documentos=False` permanece como valor predeterminado.

## Contrato comprobado

Manual local SOP-MN-3 V4, 20/03/2025, paginas 4-10 y 13-15.
SHA-256 del PDF original:
`bfccedbc7ec1ad75c6524308e871f2e91091b2075e059f41e42618f466a30aee`.

| Operacion | Destino / contrato | Estado |
| --- | --- | --- |
| Token | `POST https://authorizer.autenticsign.com/v2/authorizer/getToken` | HTTP 200 real previamente acreditado; NO repetido |
| Primera firma | `POST https://stag-api.autenticsign.com/v2/signature/` | Implementada y probada con mocks; sin envio real |
| Descarga | GET de `urlDocuments`, host HTTPS expresamente autorizado | ZIP/PDF validado offline; sin descarga real |
| Certificacion | `POST https://stag-api.autenticsign.com/v2/certification/` | Preparacion/procesamiento offline; sin envio real |

Token: JSON `audience=stag-api.autenticsign.com`,
`grant_type=client_credentials`, `client_id`, `client_secret`. Credenciales sin
transformaciones Base64/hashing/recorte; Bearer solo en memoria con expiracion y
margen, eliminado al cerrar o al recibir 401/403. No leer `.env` desde la prueba.

Firma: `metadata.names`, `lastNames`, `docId`, `secureKey`; `files` con
`fileName` y `fileContent` Base64 estandar. Hasta cinco PDF, total seis MB.
`secureKey` evidencia una autenticacion verificable; no es el secreto OAuth
ni se deriva automaticamente del OTP de Credito Digital. La pagina 7 permite
una nomenclatura propia consultable y verificable: NO exige una clave emitida
por Autentic. Debe vincular la autenticacion del firmante y su aceptacion del
PDF concreto; una sesion historica de acceso no acredita esa aceptacion.
Exito: HTTP 201, `operationCode=1000`, `transactionId`, `processedDate`,
`urlDocuments`. El texto de error del proveedor nunca se incorpora a excepciones.

Certificacion: `certificationType=SIGNATURE`, `transactionId`, `documentNumber`.
Exito HTTP 200 / codigo 1000 y `certificationContent` PDF Base64.
1010 indica transaccion/documento no encontrado; 1020, estado incompatible.
**Faltante documental:** el manual exige el identificador de documento devuelto
por firma, pero su ejemplo de respuesta no identifica ese campo. Se necesita
el `documentNumber` oficial y su correspondencia con el archivo/transaccion.
No sustituirlo por `docId`, nombre de PDF, indice del ZIP o `transactionId`.
Esto no impide preparar/probar firma y descarga.

## Ejecucion local sin proveedor

Desde la raiz del repositorio, PowerShell:

```powershell
.\backend\venv\Scripts\python.exe -B backend\tools\autentic_prueba_offline.py
```

Genera un directorio temporal nuevo, tres PDF visiblemente sinteticos, un
journal SQLite y `resultado.json` saneado con hashes. Nunca carga credenciales
privadas. No tiene modo real ni flag que habilite red: bloquea DNS/conexiones
y `requests.Session.request`; inyecta solamente cuatro respuestas mock.
Los PDF de salida NO son firmas/certificaciones reales ni documentos personales.
`--salida <directorio-nuevo>` conserva los artefactos en una ruta elegida sin
sobrescribir contenido existente. No usar el runner con documentos reales.

Pruebas focalizadas desde `backend`:

```powershell
.\venv\Scripts\python.exe -B -m unittest apps.integraciones.autentic.tests.test_cliente apps.integraciones.autentic.tests.test_preflight apps.integraciones.autentic.tests.test_flujo_documental
```

## Seguridad y recuperacion

- Se reutilizan `contratos.py`, `cliente.py`, `operaciones.py`, `evidencia.py`.
- Journal SQLite exclusivo y durable: reservar antes de autenticar; reclamar
  atomicamente antes del POST. Misma clave con otro payload o mismo fingerprint
  con otra clave quedan bloqueados. Firma y certificacion usan reservas distintas.
- Persistir el journal real en custodia privada fuera de Git, no en `:memory:`;
  los tokens, PDF, identidad y `secureKey` no se guardan en el journal.
- Tras timeout, error HTTP, codigo inesperado o respuesta invalida posterior al
  inicio: `INDETERMINADA`, sin reenvio. Tras caida: `ENVIANDO` tampoco se reenvia.
  Reabrir el journal o cambiar de clave no autoriza otro consumo equivalente.
- Replay exitoso devuelve solo estado y transaccion, NO vuelve a firmar/certificar
  ni inventa el artefacto perdido. Conservar el recibo/documentos en custodia
  privada; si se perdieron, conciliar con proveedor sin repetir POST.
- No se inventa un endpoint de estado: `consultar_estado` sigue bloqueado.
- TLS verificado, sin redirects, retries, proxies implicitos ni depuracion HTTP.
  La descarga no lleva Bearer y rechaza sesiones con cookies/auth heredadas.
- ZIP limitado, nombres exactos, sin rutas/symlinks/cifrado; CRC, estructura PDF
  y SHA-256. PDF firmado y certificacion son evidencias distintas.
  `firma_criptografica_validada=False`: no se acredita PKI con estas comprobaciones.

## Operacion DEV autorizada por el responsable el 27/10/2026

1. El responsable autorizo probar la integracion DEV y usar al titular A.
   No se solicita otra autorizacion administrativa. OAuth/manual ya localizados
   en custodia. El token previo se descarto; no se reutiliza ni imprime.
2. PDF de prueba definitivo, sin contenido contractual atribuido falsamente,
   mostrado al firmante. Metadata autentica de A solo desde custodia y
   autenticacion/aceptacion propias vigentes. secureKey puede ser una referencia
   propia verificable de ese acto; no inventar autenticacion ni firmar por A.
3. Clave de operacion y journal privado durables, host de descarga autorizado,
   custodia privada del recibo/PDF y plan de conciliacion ante incertidumbre.
   Para certificacion agregar el `documentNumber` oficial, nunca inferido.

La integracion documental esta lista para desarrollo aislado. No se conecta
automaticamente al expediente real, no modifica consentimiento ni el frontend,
no despliega ni cambia los servicios 8014/8046/8047.

## Conciliacion del intento DEV real del 27/10/2026

La prueba personal de A alcanzo lectura propia HTTP200, revision y aceptacion
del PDF exacto. Se intento una operacion y el journal termino INDETERMINADA,
sin transactionId acreditado. No reenviar ni cambiar clave para eludirla.
Ese intento no conservo HTTP/causa de la firma; no inferir timeout, rechazo o
recepcion exitosa. La nueva auditoria conserva HTTP/tiempo/codigo local sin
cuerpos, tokens, identidad o URL privada. No recupera informacion ya perdida.

Conciliacion SIN OAuth ni HTTP, desde backend y con custodia autorizada:

```powershell
.\venv\Scripts\python.exe -B tools/autentic_verificar_dev.py --conciliar-envio-dev <directorio-privado-del-intento> --custodia-cifrado <custodia-existente-de-la-clave-local>
```

El comando abre SQLite en modo lector, verifica PDF/fingerprint/secureKey y
sella una solicitud minima usando el cifrado existente. No altera journal,
aceptacion o resultado originales. Solo el custodio accede a identidad y
referencia propias dentro del paquete. Gestionar con soporte Autentic DEV por
el canal institucional privado: existencia/ausencia de la transaccion para ese
secureKey, firmante y fecha; si existe, recibo original transactionId,
processedDate, operationCode y urlDocuments. No inventar una consulta de estado
ni presentar la aceptacion local o el token OAuth como firma acreditada.

## Frente B: cierre offline del 06/11/2026

Incidencia externa unica (generada, no enviada):

- ID: `AUTENTIC-OAUTH-de28e7a909d16af7c252ca3c422a96d3`.
- `POST https://authorizer.autenticsign.com/v2/authorizer/getToken`, HTTP 401.
- Fecha observada local del intento: `2026-11-06T03:58:26.444189+00:00`.
  No se conserva una fecha HTTP del proveedor independiente de esa observacion.
- Audience: `api.autenticsign.com`; grant: `client_credentials`.
- Correlacion LOCAL / SHA256 del recibo original:
  `de28e7a909d16af7c252ca3c422a96d39e541411cb71a3765450ffc849b2ea0a`.
  No se conservo correlacion del proveedor; no se afirma que no haya existido.
- Version: `AUTENTIC_DIAGNOSTICO_OFFLINE_V2`; API v2; SOP-MN-3 V4, 20/03/2025.
- Cliente, SHA256 con dominio `AUTENTIC_CLIENTE_V1` y separador NUL:
  `0189ccefe19cffc959ceace7869b49e2ba46c4eedd1772d88ee1365a9fb2b4d4`.
  Calculado en memoria desde el TXT suministrado; el recibo historico no contiene
  una vinculacion criptografica del cliente con la peticion HTTP original.
- Alcance, vigencia y habilitacion de produccion: desconocidos/no acreditados
  por HTTP 401. Cero peticiones nuevas, firmas, certificaciones y documentos.

Dependencia externa genuina: confirmar credencial/habilitacion de produccion y
causa del 401; conciliar el intento DEV original sin reenviarlo; documentar el
origen oficial de `documentNumber` por PDF/transaccion y entregar evidencia
verificable de identidad, participacion, correspondencia documental y tiempo.
No se inventa consulta GET de estado, renovacion de URL ni comprobante de firma.

El diagnostico existente reside en `C:\.vscode\api_data_v1\tools`, no en
`backend/tools`. `--incidencia-externa` solo lee recibo/archivo y emite la misma
incidencia saneada; ambos modos OAuth quedan bloqueados incluso con otra ruta.
No se crearon herramientas duplicadas en el backend.

`ReciboFirma.validar_recuperacion` comprueba consistencia y ambiente antes de
descargar. Una descarga fallida elimina la auditoria anterior; no hay OAuth en
la descarga ni reenvio de firma. Un recibo o transactionId no acredita firma.
`custodiar_descarga` y `leer_descarga_custodiada` usan callbacks de cifrado
autenticado de la aplicacion (contrato str -> str de encrypt_text/decrypt_text).
Validan PDF, hashes, expediente, envio, documento, transaccion, ambiente y
procedencia. No crean claves, persistencia o servicios alternativos: el custodio
debe guardar exclusivamente el cifrado sin sobreescribir el intento original.
Los tests usan AES-GCM sintetico; no se altero ningun estado APP ni journal real.

Pruebas exactas desde backend, con pyHanko 0.38.0 y dependencias declaradas
instaladas aisladamente en `%TEMP%\frente-b-autentic-crypto-20261106`:

```powershell
$env:PYTHONPATH = "$env:TEMP\frente-b-autentic-crypto-20261106"
.\venv\Scripts\python.exe -B -m unittest apps.integraciones.autentic.tests.test_cliente apps.integraciones.autentic.tests.test_flujo_documental apps.integraciones.autentic.tests.test_verificacion_documental apps.integraciones.autentic.tests.test_diagnostico_http apps.integraciones.autentic.tests.test_recuperacion_offline apps.integraciones.autentic.tests.test_preflight
```

Desde `C:\.vscode\api_data_v1`:

```powershell
C:\.vscode\CreditoDigitalCongente\backend\venv\Scripts\python.exe -B -m unittest tools.test_credihoy_autentic_diagnostico
```

Solo mocks/criptografia local. No se ejecutan pruebas ORM/PostgreSQL, llamadas
al proveedor, commits, push ni despliegues. El venv compartido no fue modificado;
sin dependencias temporales, no tiene pyHanko/asn1crypto disponible.
Resultado: 102 pruebas de adaptadores/preflight y 14 de diagnostico PASS;
`git diff --check` sin errores. No se cambiaron rutas ni imports de otros APP.

## Frente B: HTTP 200, perfiles y journal durable

Actualizacion de la incidencia historica: el usuario confirma que el HTTP 401
se debio al uso de credencial STAGING con audience de PRODUCCION. Es una causa
confirmada por usuario, no una nueva respuesta del proveedor. Se mantienen el
recibo y el ID historicos anteriores; no se reejecuto OAuth. El 401 no acredita
scope, vigencia ni habilitacion productiva. La credencial DEV/STAGING expuesta
debe rotarse; no fue leida, usada ni reproducida en esta implementacion.

El contrato de transporte ahora acepta HTTP 200 (reporte Postman descrito por
usuario) y conserva HTTP 201 (manual SOP-MN-3 V4). Ambos exigen operationCode
entero 1000, transactionId no vacio, processedDate parseable y urlDocuments HTTPS
sin credenciales, fragmento o puerto alternativo. Las URLs privadas se conservan
completas hasta 8192 caracteres, nunca en repr/logs. operationMsg, si aparece,
debe ser DOCS_SIGNED; type es opcional y, si aparece, debe ser reply_signature.
No se exige requesttype ni se inventa su significado. Campos adicionales como
account, tokens o secretos reflejados no se incorporan al recibo custodiado.
Esto reporta firma NO VERIFICADA; no modifica el verificador contractual de Main.

ConfiguracionDEV y ConfiguracionSTAGING son alias del mismo ambiente de
proveedor: ambiente DEV, audience stag-api.autenticsign.com y base
https://stag-api.autenticsign.com. perfil conserva DEV o STAGING para diagnostico.
Comparten la huella historica: cambiar de DEV a STAGING NO evita el bloqueo de
un envio INDETERMINADA. ConfiguracionProduccion usa ambiente/perfil PRODUCCION,
audience api.autenticsign.com y base https://api.autenticsign.com; no reutiliza
el journal DEV. Ningun JSON puede cambiar endpoints ni habilitar nuevos envios.

Credenciales: constructor para inyeccion privada o
ConfiguracionSTAGING.desde_archivos(archivo_cliente, archivo_secreto).
desde_archivo(metadatafile) rechaza client_id/client_secret inline: solo acepta
referencias a archivos UTF-8 privados (sin Base64 ni transformacion del secreto).
Se preservan espacios y se elimina solo el fin de linea del archivo. Perfil
JSON hasta 16 KiB, archivos de credencial hasta 4 KiB, errores sin contenido.

### API estable para la tarea de Main

```python
from apps.integraciones.autentic.cliente import cargar_cliente_recuperacion

client = cargar_cliente_recuperacion(
    "STAGING", metadatafile,
    referencia_recuperacion=referencia_custodiada_existente,
)
try:
    archivos = client.recuperar_documentos(
        recibo_custodiado, nombres_esperados,
        referencia_autorizacion=referencia_custodiada_existente,
    )
finally:
    client.close()
```

Firma exacta: cargar_cliente_recuperacion(perfil, archivo_config, *,
referencia_recuperacion, session=None). Retorna AutenticClient, alias del cliente
existente, con close() normal; no requiere context manager. Acepta DEV, STAGING
o PRODUCCION, exige hosts de descarga explicitos y referencia de recuperacion.
El llamador Main debe obtener/verificar esa referencia desde la custodia real;
la fabrica no interpreta una cadena como prueba de consentimiento o firma.

Ejemplo de esquema metadata, con referencias ficticias y host NO operativo:

```json
{
  "audience": "stag-api.autenticsign.com",
  "grant_type": "client_credentials",
  "client_id_file": "private/client-id.txt",
  "client_secret_file": "private/rotated-client-secret.txt",
  "hosts_descarga": ["downloads.example.invalid"],
  "timeout": [5, 20]
}
```

Las referencias relativas se resuelven respecto del metadatafile. PRODUCCION
requiere su audience y archivos propios, no una conversion del perfil STAGING.
La fabrica carga el perfil pero no envia nada. Devuelve una capacidad solo GET:
autenticar, firmar, certificar, POST directo, Bearer en GET y GET fuera de una
recuperacion validada quedan bloqueados antes de tocar journal/transporte.
permitir_documentos=True habilita aqui exclusivamente la descarga. La restriccion
permanece en el config congelado incluso si se construye otro AutenticClient con
ese config. Cada descarga debe usar la misma referencia custodiada recibida.
No renueva URLs ni genera tokens. La politica de retryGET/huella privada y la
conciliacion pertenecen a Main; el adaptador no reintenta automaticamente.

### Persistencia del recibo

RegistroFirmasExpediente persiste reciboCifrado dentro del journalFirma existente,
en la MISMA transaccion durable PostgreSQL que estado/transaccion. El cifrado
usa por defecto encrypt_text/decrypt_text existentes; admite callbacks cifrar y
descifrar (str -> str) para pruebas/inyeccion. Valida roundtrip antes de commit
y liga el sobre a solicitud, envio, alias, seccion, clave y fingerprint.
No crea tabla, servicio o clave nuevos para PostgreSQL. La URL y el recibo no
se guardan como JSON claro. No modifica consentimiento ni formalizacion.

Interface: terminar(clave, estado, transaccion="", *, recibo=None) y
recuperar_recibo(clave). El cliente guarda el recibo automaticamente cuando el
journal tiene custodia. Reabrir/replay retorna el mismo ReciboFirma sin OAuth,
POST ni descarga. recuperar_recibo retorna None SOLO para un historico exitoso
que nunca tuvo recibo custodiado; no fabrica URL/recibo ni reabre el envio.
Una custodia corrupta o ajena arroja RECIBO_JOURNAL_NO_INTEGRO sin trafico.
Si el cifrado falla despues de recibir exito, se conserva transactionId conocido
con INDETERMINADA y queda bloqueado cualquier reenvio o cambio de clave.
Si falla el commit, ENVIANDO tambien permanece bloqueado; no se presume exito.

SQLite conserva la tabla historica intacta y, SOLO con callbacks de cifrado,
agrega autentic_recibos para el recibo cifrado atomico. Sin callbacks sigue el
contrato legacy de estado/transaccion sin fabricar recibos al reabrir.

### Validacion reproducible de este cambio

Desde C:\.vscode\CreditoDigitalCongente\backend, con dependencias temporales ya
existentes en C:\Users\USR-CG~1\AppData\Local\Temp\frente-b-autentic-crypto-20261106:

```powershell
$env:PYTHONPATH = "$env:TEMP\frente-b-autentic-crypto-20261106"
.\venv\Scripts\python.exe -B -m unittest apps.integraciones.autentic.tests.test_cliente apps.integraciones.autentic.tests.test_flujo_documental apps.integraciones.autentic.tests.test_verificacion_documental apps.integraciones.autentic.tests.test_diagnostico_http apps.integraciones.autentic.tests.test_recuperacion_offline apps.integraciones.autentic.tests.test_preflight
```

No se descargaron dependencias nuevas ni se modifico el venv. En el sandbox
Codex, la carga de DLL temporales requiere ejecucion escalada; fuera del sandbox
los comandos usan el mismo interprete y directorio temporal. No hay wheelhouse:
la instalacion temporal anterior uso --no-cache-dir --no-compile --target.
Las pruebas PostgreSQL de este cambio usan ORM/transacciones SIMULADOS; no
acreditan concurrencia ni commit sobre un servidor PostgreSQL real. Se verifican
el bloqueo durable solicitado, cifrado/contexto, guardado atomico y replay sin
nuevas llamadas con fixtures. No se ejecutaron tests ORM de expedientes reales.

Dependencias externas pendientes: rutas de JSON Postman saneado/ZIP original
autentico, metadatafile privado con referencias a credenciales ya ROTADAS,
host exacto de urlDocuments y fuente institucional de confianza/politicas.
No se inventaron esas rutas, ni se activo un perfil expuesto, ni hubo red,
git, despliegue o cambios en unico/formalizacion/evidencia/pdf_criptografia.

Resultado final de esta iteracion: 121 pruebas conjuntas PASS y 14 pruebas del
diagnostico existente PASS, sin editar las herramientas. AST de los seis Python
editados correcto y sin whitespace final. Incluye regresion HTTP 200 sin type ni
requesttype; la fabrica usa try/finally close() sin exigir context manager.

### Invariante de seguridad del recibo contractual

HTTP 200 y DOCS_SIGNED solo producen FIRMA_REPORTADA_NO_VERIFICADA. El analizador
conserva intactos tipo_respuesta/reply.type == reply_signature y
fechaProveedorConZona; no se relajaron por compatibilidad de transporte.
La regresion test_http200_without_provider_type_or_zoned_date_never_promotes_contractual_signature
comprueba ausencia de tipo, fecha sin zona y ambos casos juntos, aun con todos
los checks criptograficos simulados positivos: verificar_firma_contractual debe
rechazar los tres. No se completa type ni se presume UTC. El recibo real sigue
pendiente del usuario; no se modificaron analizador, criptografia ni tasks.py.
