# FASE 10C-A: clientes aislados Risk Gateway

## Base y alcance

- Worktree: `CreditoDigitalCongente-fase-10c-risk-gateway-clients` (Temp local).
- Branch: `fase-10c-risk-gateway-clients`.
- Base: `54dc3ba4394af12294419b2f0360cd7e37030b92`.
- Fuente contractual PRESELECTA: `78fff71328c95e2dc64d3fc9756db5adc34f522f`.
- Fuente leida en modo read-only: `C:/.vscode/Preselecta`.
- Los archivos auditados del proveedor no tienen diff contra ese commit.
- Sin startup/command nuevo, persistencia, proveedores, Celery ni wiring productivo.
- La configuracion de 10B permanece igual y OFF por defecto. Health conserva su contrato.

## Auditoria previa y decisiones de compatibilidad

Se contrastaron serializers, views, controles, DTOs, OpenAPI y tests del proveedor,
ademas del consumidor shadow local no versionado. No se copio el paquete shadow ni
sus reglas/calculos. El snapshot de 32 schemas conserva el contrato del proveedor
y su revision; no depende de archivos locales excluidos ni del otro repositorio
durante ejecucion o tests.

Fuentes concretas:
- `api/internal/serializers.py:13`: objetos estrictos, tipos string y nombres por persona.
- `api/internal/views.py:39`: resultado tecnico -> HTTP.
- `api/internal/views.py:62`: UUID; `:77`: HTTPS/auth/scopes/key.
- `api/internal/views.py:126`: headers y correlacion; `:141`: envelopes.
- `api/internal/views.py:161`, `:177`, `:210`: operaciones y scopes.
- `api/internal/controls.py:139`: formato key; `:145`: reserva durable sin replay.
- `integrations/services/preselecta_input.py:22`: estrategia/catalogos REST.
- `integrations/services/preselecta_contracts.py:94`: normalizado PRESELECTA.
- `api/services/hdc_contracts.py:55`: importes; `:126`: obligaciones;
  `:191`: agregado entregado por el productor.
- `api/services/report_files.py`: recuperacion/regeneracion local PDF, sin consulta HDC.
- `api/test_openapi_contract.py:118`: superset documentado del schema de pares.

Diferencias intencionales respecto de la referencia shadow:
1. Auth/config/TLS/timeouts viven en el transporte Health existente, no en otro cliente.
2. Un requestId invalido se rechaza; no se reemplaza silenciosamente por otro UUID.
3. Se verifica eco de headers de idempotencia; no se agrega retry/replay.
4. PDF incluye firma `%PDF-` y limite de streaming, no solo control de longitud.
5. No se incorpora capacidad, endeudamiento reconstruido ni consumo CrediHoy/shadow.
6. Los 32 schemas POST coinciden con el productor actual. Health conserva la
   tolerancia aprobada de 10B a propiedades extra; no se impone el schema estricto
   Health del proveedor como cambio retrospectivo.
7. `EstadoCompuestoHDC.entradas_originales`: OAS permite items string/null en el
   par; el DTO y productor declaran nombre string y valor string/null. Se conserva
   orden, null del valor y longitud 2; nombre null se rechaza. Esto es un superset
   documentado por el test del productor, no una equivalencia inventada.
8. `endeudamiento_actual` es opcional en OAS aunque el productor actual lo emite.
   Ausencia se preserva, sin default ni agregado reconstruido.
9. DTO generico inmutable `NodoContrato` representa todos los campos por schema;
   no se duplican las treinta clases del servidor. Solo los Decimal contractuales
   pasan de string a Decimal finito; originales y nulls permanecen intactos.

## Operaciones, headers y correlacion

| Endpoint | Metodo | Scope del servidor | Request | Response 200 |
|---|---|---|---|---|
| /api/internal/v1/preselecta/consultar/ | POST | preselecta:consultar | PreselectaRequest | PRESELECTA_NORMALIZADO_V1 |
| /api/internal/v1/hdc/consultar/ | POST | hdc:consultar | HDCRequest | HDC_NORMALIZADO_V1 |
| /api/internal/v1/hdc/consultas/<consulta_id>/pdf/ | GET | hdc:evidencia | UUID path, sin body | application/pdf |
| /api/internal/v1/health/ | GET | autenticacion interna, sin scope de consulta | sin body | ready, v1, providerChecks=false |

Scopes se asignan al consumidor en el servidor; no se inventa un header de scope.

| Campo | Tipo | Requerido | Origen | Endpoint | Semantica |
|---|---|---|---|---|---|
| Authorization | string Bearer + secret configurado | SI | Config 10B / controls.py:84 | todos | Auth S2S comun; nunca se registra |
| X-Internal-Client | string | SI | Config 10B / controls.py:84 | todos | Consumidor autenticado; no token nuevo |
| X-Request-ID request | string UUID canonico | SI en el consumidor | views.py:62 | todos | Llamador puede proveerlo; solo None genera UUID nuevo |
| X-Request-ID response | string UUID canonico | SI para POST/PDF; opcional Health 10B | views.py:126 | todos | Debe coincidir con enviado; no se sustituye |
| Idempotency-Key request | string ASCII 1..128 | OPCIONAL | controls.py:139 | POST | Solo explicita; regex descrita abajo |
| Idempotency-Key response | string | CONDICIONAL | views.py:126 | POST | Eco identico si se envio key; ausente sin key |
| X-Idempotency-Status response | string literal | CONDICIONAL | views.py:126 | POST | IDEMPOTENCIA_DE_NEGOCIO_PENDIENTE si se envio key |
| Content-Type request | application/json | SI | serializers.py / views.py:53 | POST | Sin SOAP ni XML de seguridad |
| Accept request | application/json o application/pdf | SI | views.py:53,210 | POST/GET PDF | JSON para consultas; PDF para evidencia |
| Content-Type response | JSON o PDF segun operacion | SI | views.py:141,210 | todos | JSON compatible application/json y application/*+json; PDF application/pdf |
| consulta_id path | string UUID | SI | views.py:210 | GET PDF | resultado.consulta_id de HDC, NO requestId |
| Content-Length response PDF | integer ASCII como header | OPCIONAL | FileResponse | GET PDF | Si existe: positivo <=20 MiB; limite tambien durante stream |

Las respuestas POST exigen coherencia entre header, envelope.requestId y
resultado.request_id; trazabilidad.version_contrato debe coincidir con envelope.
COMPLETADO y SIN_INFORMACION son respuestas 200 validas. RECHAZADO del proveedor
es un hecho PRESELECTA, no una decision del consumidor ni un error HTTP.

PRESELECTA tipo_documento usa catalogo REST del proveedor (codigos/alias aceptados
por DecisionPayloadSerializer); HDC person_id_type usa strings SOAP 1..9.
No hay homologacion automatica entre estos catalogos. El DTO request valida el
schema publicado; el proveedor conserva la validacion de alias REST.
El servidor trimmea campos de texto; el consumidor conserva el wire introducido.
No resuelve STRNAM, consultante, contrasenas ni defaults SOAP en el frontend/cliente.

## Tabla contractual exhaustiva

Fuente OAS: `docs/internal_api_v1.openapi.json#/components/schemas` en el commit
PRESELECTA indicado. Cada fila tiene el origen OAS `Schema/properties/campo` y
la clase/serializer/view se indica al inicio de su tabla. Rutas de uso indican
como componer la ruta JSON exacta: ruta del objeto + campo de la fila.
Los objetos no admiten extras salvo donde OAS lo permite expresamente.
Requerido se refiere a presencia de key: requerido nullable NO significa valor disponible.

Leyenda endpoints:
- P_REQUEST / P_RESPONSE: POST PRESELECTA, request / response.
- H_REQUEST / H_RESPONSE: POST HDC, request / response.
- ERROR: envelope tecnico de errores de todas las operaciones (PDF usa JSON en errores).
- JsonValue: solo string, integer, boolean, null, array y object recursivos; no float.

### ResultadoHDCNormalizado

Codigo productor: `api/services/hdc_contracts.py:236`; OAS `ResultadoHDCNormalizado`.

Rutas de uso: `H_RESPONSE: response.resultado`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `consulta_id` | string (uuid) | SI | `ResultadoHDCNormalizado/properties/consulta_id` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `request_id` | string (uuid) | SI | `ResultadoHDCNormalizado/properties/request_id` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fecha_consulta` | string o null | SI | `ResultadoHDCNormalizado/properties/fecha_consulta` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `tipo_documento` | CodigoHDC | SI | `ResultadoHDCNormalizado/properties/tipo_documento` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `tipo_documento_solicitado` | string o null | SI | `ResultadoHDCNormalizado/properties/tipo_documento_solicitado` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estado_tecnico` | string | SI | `ResultadoHDCNormalizado/properties/estado_tecnico` | H_RESPONSE | Enum literal ["COMPLETADO","SIN_INFORMACION","VALIDACION","ERROR_PROVEEDOR","ERROR_TECNICO"] |
| `codigo_respuesta_proveedor` | string | SI | `ResultadoHDCNormalizado/properties/codigo_respuesta_proveedor` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `atributos_informe_originales` | array<array<string>> | SI | `ResultadoHDCNormalizado/properties/atributos_informe_originales` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `obligaciones` | array<ObligacionHDC> | SI | `ResultadoHDCNormalizado/properties/obligaciones` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `cobertura` | CoberturaHDC | SI | `ResultadoHDCNormalizado/properties/cobertura` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `trazabilidad` | TrazabilidadHDC | SI | `ResultadoHDCNormalizado/properties/trazabilidad` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `endeudamiento_actual` | EndeudamientoActualHDC | OPCIONAL | `ResultadoHDCNormalizado/properties/endeudamiento_actual` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### CodigoHDC

Codigo productor: `api/services/hdc_contracts.py:28`; OAS `CodigoHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.tipo_documento`; `H_RESPONSE: response.resultado.obligaciones[].tipoObligacion`; `H_RESPONSE: response.resultado.obligaciones[].sector`; `H_RESPONSE: response.resultado.obligaciones[].calidadDeudor`; `H_RESPONSE: response.resultado.obligaciones[].garantia`; `H_RESPONSE: response.resultado.obligaciones[].estadoPago`; `H_RESPONSE: response.resultado.obligaciones[].estadoCuenta`; `H_RESPONSE: response.resultado.obligaciones[].estadoOrigen`; `H_RESPONSE: response.resultado.obligaciones[].estadoPlastico`; `H_RESPONSE: response.resultado.obligaciones[].formaPago`; `H_RESPONSE: response.resultado.obligaciones[].valores[].moneda`; `H_RESPONSE: response.resultado.obligaciones[].valores[].periodicidad`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `codigo_original` | string o null | SI | `CodigoHDC/properties/codigo_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `codigo_catalogo` | string o null | SI | `CodigoHDC/properties/codigo_catalogo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `descripcion` | string o null | SI | `CodigoHDC/properties/descripcion` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `nombre` | string o null | SI | `CodigoHDC/properties/nombre` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `comportamiento` | string o null | SI | `CodigoHDC/properties/comportamiento` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `clasificacion` | string o null | SI | `CodigoHDC/properties/clasificacion` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `semantica` | string | SI | `CodigoHDC/properties/semantica` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | string o null | SI | `CodigoHDC/properties/fuente` | H_RESPONSE | Enum literal ["DOCUMENTADO_PROVEEDOR","ADAPTACION_CONGENTE","LEGACY_NO_NORMATIVO",null] |
| `referencia` | string o null | SI | `CodigoHDC/properties/referencia` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### ObligacionHDC

Codigo productor: `api/services/hdc_contracts.py:126`; OAS `ObligacionHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[]`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `posicion_original` | integer | SI | `ObligacionHDC/properties/posicion_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `tipo_estructural` | string | SI | `ObligacionHDC/properties/tipo_estructural` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `nombre_estructural_original` | string | SI | `ObligacionHDC/properties/nombre_estructural_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `identificador` | IdentificadorHDC | SI | `ObligacionHDC/properties/identificador` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `entidad` | string o null | SI | `ObligacionHDC/properties/entidad` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `codSuscriptor` | string o null | SI | `ObligacionHDC/properties/codSuscriptor` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `tipoCuenta` | TipoCuentaHDC | SI | `ObligacionHDC/properties/tipoCuenta` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `tipoObligacion` | CodigoHDC o null | SI | `ObligacionHDC/properties/tipoObligacion` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `sector` | CodigoHDC | SI | `ObligacionHDC/properties/sector` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `calidadDeudor` | CodigoHDC o null | SI | `ObligacionHDC/properties/calidadDeudor` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `garantia` | CodigoHDC o null | SI | `ObligacionHDC/properties/garantia` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estadoPago` | CodigoHDC o null | SI | `ObligacionHDC/properties/estadoPago` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estadoCuenta` | CodigoHDC o null | SI | `ObligacionHDC/properties/estadoCuenta` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estadoOrigen` | CodigoHDC o null | SI | `ObligacionHDC/properties/estadoOrigen` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estadoPlastico` | CodigoHDC o null | SI | `ObligacionHDC/properties/estadoPlastico` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `formaPago` | CodigoHDC o null | SI | `ObligacionHDC/properties/formaPago` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `bloqueada_original` | string o null | SI | `ObligacionHDC/properties/bloqueada_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fechas` | FechasHDC | SI | `ObligacionHDC/properties/fechas` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `valores` | array<ValorHDC> | SI | `ObligacionHDC/properties/valores` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `valor_actual` | ValorActualHDC | SI | `ObligacionHDC/properties/valor_actual` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estado_obligacion_compuesto` | EstadoCompuestoHDC | SI | `ObligacionHDC/properties/estado_obligacion_compuesto` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `datos_originales` | NodoOriginalHDC | SI | `ObligacionHDC/properties/datos_originales` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `advertencias` | array<AdvertenciaHDC> | SI | `ObligacionHDC/properties/advertencias` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### IdentificadorHDC

Codigo productor: `api/services/hdc_contracts.py:114`; OAS `IdentificadorHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].identificador`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `numero` | string o null | SI | `IdentificadorHDC/properties/numero` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `identificacion` | string o null | SI | `IdentificadorHDC/properties/identificacion` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### TipoCuentaHDC

Codigo productor: `api/services/hdc_contracts.py:41`; OAS `TipoCuentaHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].tipoCuenta`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `codigo_original` | string o null | SI | `TipoCuentaHDC/properties/codigo_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `subtipo_original` | string o null | SI | `TipoCuentaHDC/properties/subtipo_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `codigo_catalogo` | string o null | SI | `TipoCuentaHDC/properties/codigo_catalogo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `subcodigo_catalogo` | string o null | SI | `TipoCuentaHDC/properties/subcodigo_catalogo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `abreviatura` | string o null | SI | `TipoCuentaHDC/properties/abreviatura` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `descripcion` | string o null | SI | `TipoCuentaHDC/properties/descripcion` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `semantica` | string | SI | `TipoCuentaHDC/properties/semantica` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | string o null | SI | `TipoCuentaHDC/properties/fuente` | H_RESPONSE | Enum literal ["DOCUMENTADO_PROVEEDOR","ADAPTACION_CONGENTE","LEGACY_NO_NORMATIVO",null] |
| `referencia` | string o null | SI | `TipoCuentaHDC/properties/referencia` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `alternativas` | array<array<string>> | SI | `TipoCuentaHDC/properties/alternativas` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### FechasHDC

Codigo productor: `api/services/hdc_contracts.py:120`; OAS `FechasHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].fechas`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `apertura` | string o null | SI | `FechasHDC/properties/apertura` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `vencimiento` | string o null | SI | `FechasHDC/properties/vencimiento` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### ValorHDC

Codigo productor: `api/services/hdc_contracts.py:81`; OAS `ValorHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].valores[]`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `posicion_original` | integer | SI | `ValorHDC/properties/posicion_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fecha` | string o null | SI | `ValorHDC/properties/fecha` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `moneda` | CodigoHDC | SI | `ValorHDC/properties/moneda` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `periodicidad` | CodigoHDC o null | SI | `ValorHDC/properties/periodicidad` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `cuota` | ImporteHDC o null | SI | `ValorHDC/properties/cuota` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `saldoActual` | ImporteHDC o null | SI | `ValorHDC/properties/saldoActual` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `saldoMora` | ImporteHDC o null | SI | `ValorHDC/properties/saldoMora` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `valorInicial` | ImporteHDC o null | SI | `ValorHDC/properties/valorInicial` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `cupoTotal` | ImporteHDC o null | SI | `ValorHDC/properties/cupoTotal` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `datos_originales` | NodoOriginalHDC | SI | `ValorHDC/properties/datos_originales` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `advertencias` | array<AdvertenciaHDC> | SI | `ValorHDC/properties/advertencias` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### ImporteHDC

Codigo productor: `api/services/hdc_contracts.py:55`; OAS `ImporteHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].valores[].cuota`; `H_RESPONSE: response.resultado.obligaciones[].valores[].saldoActual`; `H_RESPONSE: response.resultado.obligaciones[].valores[].saldoMora`; `H_RESPONSE: response.resultado.obligaciones[].valores[].valorInicial`; `H_RESPONSE: response.resultado.obligaciones[].valores[].cupoTotal`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `valor_original` | string o null | SI | `ImporteHDC/properties/valor_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `unidad_original` | string o null | SI | `ImporteHDC/properties/unidad_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `moneda_codigo` | string o null | SI | `ImporteHDC/properties/moneda_codigo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `moneda_homologada` | string o null | SI | `ImporteHDC/properties/moneda_homologada` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `valor_monetario` | string o null | SI | `ImporteHDC/properties/valor_monetario` | H_RESPONSE | Decimal exacto serializado sin float. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `disponible` | boolean | SI | `ImporteHDC/properties/disponible` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estado_conversion` | string | SI | `ImporteHDC/properties/estado_conversion` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `motivo` | string o null | SI | `ImporteHDC/properties/motivo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | string o null | SI | `ImporteHDC/properties/fuente` | H_RESPONSE | Enum literal ["DOCUMENTADO_PROVEEDOR","ADAPTACION_CONGENTE","LEGACY_NO_NORMATIVO",null] |
| `referencia` | string o null | SI | `ImporteHDC/properties/referencia` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### NodoOriginalHDC

Codigo productor: `api/services/hdc_contracts.py:73`; OAS `NodoOriginalHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].valores[].datos_originales`; `H_RESPONSE: response.resultado.obligaciones[].valores[].datos_originales.hijos[]`; `H_RESPONSE: response.resultado.obligaciones[].datos_originales`; `H_RESPONSE: response.resultado.obligaciones[].datos_originales.hijos[]`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `nombre` | string | SI | `NodoOriginalHDC/properties/nombre` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `atributos` | array<array<string>> | SI | `NodoOriginalHDC/properties/atributos` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `texto` | string o null | SI | `NodoOriginalHDC/properties/texto` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `hijos` | array<NodoOriginalHDC> | SI | `NodoOriginalHDC/properties/hijos` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### AdvertenciaHDC

Codigo productor: `api/services/hdc_contracts.py:21`; OAS `AdvertenciaHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].valores[].advertencias[]`; `H_RESPONSE: response.resultado.obligaciones[].advertencias[]`; `H_RESPONSE: response.resultado.cobertura.advertencias[]`; `H_RESPONSE: response.resultado.endeudamiento_actual.advertencias[]`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `codigo` | string | SI | `AdvertenciaHDC/properties/codigo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `campo` | string | SI | `AdvertenciaHDC/properties/campo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | string | SI | `AdvertenciaHDC/properties/fuente` | H_RESPONSE | Enum literal ["DOCUMENTADO_PROVEEDOR","ADAPTACION_CONGENTE","LEGACY_NO_NORMATIVO"] |

### ValorActualHDC

Codigo productor: `api/services/hdc_contracts.py:96`; OAS `ValorActualHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].valor_actual`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `disponible` | boolean | SI | `ValorActualHDC/properties/disponible` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `motivo` | string | SI | `ValorActualHDC/properties/motivo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `posicion_original` | integer o null | SI | `ValorActualHDC/properties/posicion_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | string | SI | `ValorActualHDC/properties/fuente` | H_RESPONSE | Enum literal ["DOCUMENTADO_PROVEEDOR","ADAPTACION_CONGENTE","LEGACY_NO_NORMATIVO"] |

### EstadoCompuestoHDC

Codigo productor: `api/services/hdc_contracts.py:104`; OAS `EstadoCompuestoHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.obligaciones[].estado_obligacion_compuesto`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `estado` | string | SI | `EstadoCompuestoHDC/properties/estado` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `nombre` | string o null | SI | `EstadoCompuestoHDC/properties/nombre` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `motivo` | string o null | SI | `EstadoCompuestoHDC/properties/motivo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `entradas_originales` | array<array<string o null>> | SI | `EstadoCompuestoHDC/properties/entradas_originales` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | string | SI | `EstadoCompuestoHDC/properties/fuente` | H_RESPONSE | Enum literal ["DOCUMENTADO_PROVEEDOR","ADAPTACION_CONGENTE","LEGACY_NO_NORMATIVO"] |
| `referencia` | string | SI | `EstadoCompuestoHDC/properties/referencia` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### CoberturaHDC

Codigo productor: `api/services/hdc_contracts.py:153`; OAS `CoberturaHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.cobertura`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `total_obligaciones` | integer | SI | `CoberturaHDC/properties/total_obligaciones` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `total_carteras` | integer | SI | `CoberturaHDC/properties/total_carteras` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `total_tarjetas` | integer | SI | `CoberturaHDC/properties/total_tarjetas` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `total_ahorros` | integer | SI | `CoberturaHDC/properties/total_ahorros` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `total_corrientes` | integer | SI | `CoberturaHDC/properties/total_corrientes` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `total_otros` | integer | SI | `CoberturaHDC/properties/total_otros` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `faltantes` | array<string> | SI | `CoberturaHDC/properties/faltantes` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `advertencias` | array<AdvertenciaHDC> | SI | `CoberturaHDC/properties/advertencias` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### TrazabilidadHDC

Codigo productor: `api/services/hdc_contracts.py:165`; OAS `TrazabilidadHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.trazabilidad`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `version_contrato` | string | SI | `TrazabilidadHDC/properties/version_contrato` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `version_catalogos` | string | SI | `TrazabilidadHDC/properties/version_catalogos` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | array<string> | SI | `TrazabilidadHDC/properties/fuente` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### ResultadoPreselectaNormalizadoV1

Codigo productor: `integrations/services/preselecta_contracts.py:143`; OAS `ResultadoPreselectaNormalizadoV1`.

Rutas de uso: `P_RESPONSE: response.resultado`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `consulta_id` | string (uuid) | SI | `ResultadoPreselectaNormalizadoV1/properties/consulta_id` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `request_id` | string (uuid) | SI | `ResultadoPreselectaNormalizadoV1/properties/request_id` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estado_tecnico` | string | SI | `ResultadoPreselectaNormalizadoV1/properties/estado_tecnico` | P_RESPONSE | Enum literal ["COMPLETADO","SIN_INFORMACION","VALIDACION","ERROR_PROVEEDOR","ERROR_TECNICO"] |
| `codigo_proveedor` | string | SI | `ResultadoPreselectaNormalizadoV1/properties/codigo_proveedor` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `codigo_proveedor_original` | string / integer / boolean / string o null | SI | `ResultadoPreselectaNormalizadoV1/properties/codigo_proveedor_original` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `http_status` | integer o null | SI | `ResultadoPreselectaNormalizadoV1/properties/http_status` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `resultado_proveedor` | ResultadoProveedorPreselecta | SI | `ResultadoPreselectaNormalizadoV1/properties/resultado_proveedor` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `cobertura` | CoberturaPreselecta | SI | `ResultadoPreselectaNormalizadoV1/properties/cobertura` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `trazabilidad` | TrazabilidadPreselecta | SI | `ResultadoPreselectaNormalizadoV1/properties/trazabilidad` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `error` | ErrorPreselecta o null | SI | `ResultadoPreselectaNormalizadoV1/properties/error` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### ResultadoProveedorPreselecta

Codigo productor: `integrations/services/preselecta_contracts.py:109`; OAS `ResultadoProveedorPreselecta`.

Rutas de uso: `P_RESPONSE: response.resultado.resultado_proveedor`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `decision` | CampoProveedorPreselecta o null | SI | `ResultadoProveedorPreselecta/properties/decision` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `riesgo` | CampoProveedorPreselecta o null | SI | `ResultadoProveedorPreselecta/properties/riesgo` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `score` | CampoProveedorPreselecta o null | SI | `ResultadoProveedorPreselecta/properties/score` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `campos_motor` | array<CampoProveedorPreselecta> | SI | `ResultadoProveedorPreselecta/properties/campos_motor` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `datos_documentados` | object | SI | `ResultadoProveedorPreselecta/properties/datos_documentados` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `naturaleza` | string | SI | `ResultadoProveedorPreselecta/properties/naturaleza` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### CampoProveedorPreselecta

Codigo productor: `integrations/services/preselecta_contracts.py:94`; OAS `CampoProveedorPreselecta`.

Rutas de uso: `P_RESPONSE: response.resultado.resultado_proveedor.decision`; `P_RESPONSE: response.resultado.resultado_proveedor.riesgo`; `P_RESPONSE: response.resultado.resultado_proveedor.score`; `P_RESPONSE: response.resultado.resultado_proveedor.campos_motor[]`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `codigo_original` | string | SI | `CampoProveedorPreselecta/properties/codigo_original` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `valor_original` | string / integer / boolean / string o null | SI | `CampoProveedorPreselecta/properties/valor_original` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `valor_homologado` | string o null | SI | `CampoProveedorPreselecta/properties/valor_homologado` | P_RESPONSE | Decimal exacto serializado sin float. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `fuente` | string | SI | `CampoProveedorPreselecta/properties/fuente` | P_RESPONSE | Enum literal ["DOCUMENTADO_PROVEEDOR","ADAPTACION_CONGENTE","LEGACY_NO_NORMATIVO"] |
| `referencia` | string | SI | `CampoProveedorPreselecta/properties/referencia` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### CoberturaPreselecta

Codigo productor: `integrations/services/preselecta_contracts.py:119`; OAS `CoberturaPreselecta`.

Rutas de uso: `P_RESPONSE: response.resultado.cobertura`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `campos_presentes` | array<string> | SI | `CoberturaPreselecta/properties/campos_presentes` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `campos_ausentes` | array<string> | SI | `CoberturaPreselecta/properties/campos_ausentes` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `advertencias` | array<AdvertenciaPreselecta> | SI | `CoberturaPreselecta/properties/advertencias` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### AdvertenciaPreselecta

Codigo productor: `integrations/services/preselecta_contracts.py:87`; OAS `AdvertenciaPreselecta`.

Rutas de uso: `P_RESPONSE: response.resultado.cobertura.advertencias[]`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `codigo` | string | SI | `AdvertenciaPreselecta/properties/codigo` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `campo` | string | SI | `AdvertenciaPreselecta/properties/campo` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | string | SI | `AdvertenciaPreselecta/properties/fuente` | P_RESPONSE | Enum literal ["DOCUMENTADO_PROVEEDOR","ADAPTACION_CONGENTE","LEGACY_NO_NORMATIVO"] |

### TrazabilidadPreselecta

Codigo productor: `integrations/services/preselecta_contracts.py:132`; OAS `TrazabilidadPreselecta`.

Rutas de uso: `P_RESPONSE: response.resultado.trazabilidad`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `fecha_consulta` | string o null | SI | `TrazabilidadPreselecta/properties/fecha_consulta` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fecha_registro` | string (date-time) | SI | `TrazabilidadPreselecta/properties/fecha_registro` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `consumidor` | string | SI | `TrazabilidadPreselecta/properties/consumidor` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `origen` | string | SI | `TrazabilidadPreselecta/properties/origen` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `reutilizada` | boolean | SI | `TrazabilidadPreselecta/properties/reutilizada` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `version_contrato` | string | SI | `TrazabilidadPreselecta/properties/version_contrato` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `fuente` | string | SI | `TrazabilidadPreselecta/properties/fuente` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### ErrorPreselecta

Codigo productor: `integrations/services/preselecta_contracts.py:126`; OAS `ErrorPreselecta`.

Rutas de uso: `P_RESPONSE: response.resultado.error`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `codigo` | string | SI | `ErrorPreselecta/properties/codigo` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `mensaje` | string | SI | `ErrorPreselecta/properties/mensaje` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### JsonValue

Codigo productor: `integrations/services/preselecta_contracts.py:177`; OAS `JsonValue`.

Rutas de uso: `P_RESPONSE: response.resultado.resultado_proveedor.datos_documentados.*`; `P_RESPONSE: response.resultado.resultado_proveedor.datos_documentados.*[]`; `P_RESPONSE: response.resultado.resultado_proveedor.datos_documentados.*.*`.

Tipo: string / integer / boolean / string o null / array<JsonValue> / object. Se preserva recursivamente, sin coercion a float.

### Identity

Codigo productor: `api/internal/serializers.py:38`; OAS `Identity`.

Rutas de uso: `P_REQUEST: request.identidad`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `tipo_documento` | string | SI | `Identity/properties/tipo_documento` | P_REQUEST | Codigo REST 1..9 o alias reconocido por el catalogo del servidor; PEP=9.. Longitud 1..30 |
| `numero_documento` | string | SI | `Identity/properties/numero_documento` | P_REQUEST | Longitud 1..50 |
| `primer_apellido` | string | SI | `Identity/properties/primer_apellido` | P_REQUEST | Longitud 1..200 |

### Metadata

Codigo productor: `api/internal/serializers.py:34`; OAS `Metadata`.

Rutas de uso: `P_REQUEST: request.metadata`; `H_REQUEST: request.metadata`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `consumidor` | string | OPCIONAL | `Metadata/properties/consumidor` | P_REQUEST, H_REQUEST | Debe coincidir con X-Internal-Client autenticado.. Longitud 1..120 |

### Parameters

Codigo productor: `integrations/services/preselecta_input.py:22`; OAS `Parameters`.

Rutas de uso: `P_REQUEST: request.parametros`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `LINEA_CREDITO` | string | SI | `Parameters/properties/LINEA_CREDITO` | P_REQUEST | Enum literal ["1","2","3"] |
| `TIPO_ASOCIADO` | string | SI | `Parameters/properties/TIPO_ASOCIADO` | P_REQUEST | Enum literal ["1","2","3"] |
| `MEDIO_PAGO` | string | SI | `Parameters/properties/MEDIO_PAGO` | P_REQUEST | Enum literal ["1","2","3"] |
| `ACTIVIDAD` | string | SI | `Parameters/properties/ACTIVIDAD` | P_REQUEST | Enum literal ["1","2","3"] |

### PreselectaRequest

Codigo productor: `api/internal/serializers.py:44`; OAS `PreselectaRequest`.

Rutas de uso: `P_REQUEST: request`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `identidad` | Identity | SI | `PreselectaRequest/properties/identidad` | P_REQUEST | Campo contractual conservado; no crea evidencia ni decision |
| `estrategia_id` | string | SI | `PreselectaRequest/properties/estrategia_id` | P_REQUEST | Enum literal ["25674"] |
| `parametros` | Parameters | SI | `PreselectaRequest/properties/parametros` | P_REQUEST | Campo contractual conservado; no crea evidencia ni decision |
| `metadata` | Metadata | OPCIONAL | `PreselectaRequest/properties/metadata` | P_REQUEST | Campo contractual conservado; no crea evidencia ni decision |

### HDCRequest

Codigo productor: `api/internal/serializers.py:62`; OAS `HDCRequest`.

Rutas de uso: `H_REQUEST: request`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `persona` | string | OPCIONAL | `HDCRequest/properties/persona` | H_REQUEST | Enum literal ["natural","juridica"]. Default SOLO servidor: "natural" |
| `person_id_type` | string | SI | `HDCRequest/properties/person_id_type` | H_REQUEST | Catalogo SOAP independiente de REST; se envia sin homologacion nueva.. Enum literal ["1","2","3","4","5","6","7","8","9"] |
| `person_id_number` | string | SI | `HDCRequest/properties/person_id_number` | H_REQUEST | Longitud 1..40 |
| `person_last_name` | string | SI si natural (tambien si persona omitida) | `HDCRequest/properties/person_last_name` | H_REQUEST | Longitud 1..120 |
| `razon_social` | string | SI si juridica | `HDCRequest/properties/razon_social` | H_REQUEST | Longitud 1..120 |
| `metadata` | Metadata | OPCIONAL | `HDCRequest/properties/metadata` | H_REQUEST | Campo contractual conservado; no crea evidencia ni decision |

### Error

Codigo productor: `api/internal/views.py:97`; OAS `Error`.

Rutas de uso: `ERROR: errorEnvelope`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `requestId` | string (uuid) | SI | `Error/properties/requestId` | ERROR | Campo contractual conservado; no crea evidencia ni decision |
| `estadoTecnico` | string | SI | `Error/properties/estadoTecnico` | ERROR | Enum literal ["VALIDACION","ERROR_TECNICO","ERROR_PROVEEDOR"] |
| `error` | object | SI | `Error/properties/error` | ERROR | Campo contractual conservado; no crea evidencia ni decision |
| `error.codigo` | string | SI | `Error/properties/error/properties/codigo` | ERROR | Longitud 1..sin maximo |
| `error.mensaje` | string | SI | `Error/properties/error/properties/mensaje` | ERROR | Longitud 1..sin maximo |
| `consultaId` | string (uuid) | OPCIONAL | `Error/properties/consultaId` | ERROR | Campo contractual conservado; no crea evidencia ni decision |
| `versionContrato` | string | OPCIONAL | `Error/properties/versionContrato` | ERROR | Enum literal ["PRESELECTA_NORMALIZADO_V1","HDC_NORMALIZADO_V1"] |
| `codigoProveedor` | string | OPCIONAL | `Error/properties/codigoProveedor` | ERROR | Patron ^(?:[0-9]{2})?$ |

### PreselectaResponse

Codigo productor: `api/internal/views.py:141`; OAS `PreselectaResponse`.

Rutas de uso: `P_RESPONSE: response`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `requestId` | string (uuid) | SI | `PreselectaResponse/properties/requestId` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `versionContrato` | string | SI | `PreselectaResponse/properties/versionContrato` | P_RESPONSE | Enum literal ["PRESELECTA_NORMALIZADO_V1"] |
| `resultado` | ResultadoPreselectaNormalizadoV1 | SI | `PreselectaResponse/properties/resultado` | P_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### HDCResponse

Codigo productor: `api/internal/views.py:141`; OAS `HDCResponse`.

Rutas de uso: `H_RESPONSE: response`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `requestId` | string (uuid) | SI | `HDCResponse/properties/requestId` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `versionContrato` | string | SI | `HDCResponse/properties/versionContrato` | H_RESPONSE | Enum literal ["HDC_NORMALIZADO_V1"] |
| `resultado` | ResultadoHDCNormalizado | SI | `HDCResponse/properties/resultado` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### DesgloseEndeudamientoHDC

Codigo productor: `api/services/hdc_contracts.py:172`; OAS `DesgloseEndeudamientoHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.endeudamiento_actual.desglose_tipo_cuenta[]`; `H_RESPONSE: response.resultado.endeudamiento_actual.totales[]`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `codigoTipo` | string o null | SI | `DesgloseEndeudamientoHDC/properties/codigoTipo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `tipo` | string o null | SI | `DesgloseEndeudamientoHDC/properties/tipo` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `calidadDeudor` | string o null | SI | `DesgloseEndeudamientoHDC/properties/calidadDeudor` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `cupo_original` | string o null | SI | `DesgloseEndeudamientoHDC/properties/cupo_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `cupo_pesos` | string o null | SI | `DesgloseEndeudamientoHDC/properties/cupo_pesos` | H_RESPONSE | Decimal exacto en COP serializado como string, nunca float.. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `saldo_original` | string o null | SI | `DesgloseEndeudamientoHDC/properties/saldo_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `saldo_pesos` | string o null | SI | `DesgloseEndeudamientoHDC/properties/saldo_pesos` | H_RESPONSE | Decimal exacto en COP serializado como string, nunca float.. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `saldo_mora_original` | string o null | SI | `DesgloseEndeudamientoHDC/properties/saldo_mora_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `saldo_mora_pesos` | string o null | SI | `DesgloseEndeudamientoHDC/properties/saldo_mora_pesos` | H_RESPONSE | Decimal exacto en COP serializado como string, nunca float.. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `cuota_original` | string o null | SI | `DesgloseEndeudamientoHDC/properties/cuota_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `cuota_pesos` | string o null | SI | `DesgloseEndeudamientoHDC/properties/cuota_pesos` | H_RESPONSE | Decimal exacto en COP serializado como string, nunca float.. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `estados_importes` | array<array<string>> | SI | `DesgloseEndeudamientoHDC/properties/estados_importes` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

### EndeudamientoActualHDC

Codigo productor: `api/services/hdc_contracts.py:191`; OAS `EndeudamientoActualHDC`.

Rutas de uso: `H_RESPONSE: response.resultado.endeudamiento_actual`.

| Campo | Tipo | Requerido | Origen OAS | Endpoint | Semantica |
|---|---|---|---|---|---|
| `disponible` | boolean | SI | `EndeudamientoActualHDC/properties/disponible` | H_RESPONSE | True solo para consulta efectiva, Resumen unico y una cuota Total Principal valida exactamente igual. Sin control no es utilizable. |
| `cuota_mensual_original` | string o null | SI | `EndeudamientoActualHDC/properties/cuota_mensual_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `cuota_mensual_pesos` | string o null | SI | `EndeudamientoActualHDC/properties/cuota_mensual_pesos` | H_RESPONSE | Decimal exacto en COP serializado como string, nunca float.. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `cuota_control_original` | string o null | SI | `EndeudamientoActualHDC/properties/cuota_control_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `saldo_total_original` | string o null | SI | `EndeudamientoActualHDC/properties/saldo_total_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `saldo_total_pesos` | string o null | SI | `EndeudamientoActualHDC/properties/saldo_total_pesos` | H_RESPONSE | Decimal exacto en COP serializado como string, nunca float.. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `saldo_mora_original` | string o null | SI | `EndeudamientoActualHDC/properties/saldo_mora_original` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `saldo_mora_pesos` | string o null | SI | `EndeudamientoActualHDC/properties/saldo_mora_pesos` | H_RESPONSE | Decimal exacto en COP serializado como string, nunca float.. String decimal contractual -> Decimal exacto; sin factor ni redondeo |
| `calidad_deudor` | string | SI | `EndeudamientoActualHDC/properties/calidad_deudor` | H_RESPONSE | Enum literal ["Principal"] |
| `unidad_original` | string | SI | `EndeudamientoActualHDC/properties/unidad_original` | H_RESPONSE | Enum literal ["MILES_COP"] |
| `moneda_homologada` | string | SI | `EndeudamientoActualHDC/properties/moneda_homologada` | H_RESPONSE | Enum literal ["COP"] |
| `fuente` | string | SI | `EndeudamientoActualHDC/properties/fuente` | H_RESPONSE | Enum literal ["ADAPTACION_CONGENTE"] |
| `referencia` | string | SI | `EndeudamientoActualHDC/properties/referencia` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |
| `estado_consistencia` | string | SI | `EndeudamientoActualHDC/properties/estado_consistencia` | H_RESPONSE | CONSISTENTE, INCONSISTENTE, SIN_CONTROL_CRUZADO, TOTAL_PRINCIPAL_MULTIPLE, ESTRUCTURA_AMBIGUA, AUSENTE, CONTROL_*, NO_REPORTADO_PROVEEDOR, FORMATO_INVALIDO, VALOR_NEGATIVO_NO_DOCUMENTADO o CONSULTA_NO_EFECTIVA. |
| `desglose_tipo_cuenta` | array<DesgloseEndeudamientoHDC> | SI | `EndeudamientoActualHDC/properties/desglose_tipo_cuenta` | H_RESPONSE | Evidencia preservada sin filtrar sectores ni reconstruir el total. |
| `totales` | array<DesgloseEndeudamientoHDC> | SI | `EndeudamientoActualHDC/properties/totales` | H_RESPONSE | Incluye Principal, Codeudor y Otros por separado; solo Principal controla cuota. |
| `advertencias` | array<AdvertenciaHDC> | SI | `EndeudamientoActualHDC/properties/advertencias` | H_RESPONSE | Campo contractual conservado; no crea evidencia ni decision |

## Idempotencia, sin replay

Generador: exclusivamente el llamador. Formato ASCII:
`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` (1..128).
No debe contener PII ni secretos. No se deriva de requestId ni de documento.

La misma key se conserva como identificador de una operacion de negocio. El servidor
reserva por consumidor + capability + digest key; fingerprint incluye request
operativo, no metadata/requestId. Misma key/payload retorna 409
IDEMPOTENCY_KEY_USED; key/payload diferente retorna 409 IDEMPOTENCY_CONFLICT.
No devuelve respuesta anterior y no invoca otra vez al proveedor.

El consumidor representa ambos como GatewayIdempotencyConflict con status409;
no inspecciona body remoto para distinguirlos. No reintenta ni cambia key.
Una reserva sobrevive a timeout/fallo; no hay TTL/release implicito.
Tras timeout/409 se debe conciliar la consulta/reserva con el propietario antes
de autorizar otro intento. No regenerar key para eludir la proteccion de cobro.
Sin key el contrato no ofrece deduplicacion.
requestId correlaciona un intento tecnico; key identifica la intencion de negocio.

## Taxonomia segura

| Condicion | Clase | Informacion conservada |
|---|---|---|
| Feature OFF | GatewayDisabled | codigo fijo |
| Config invalida | GatewayConfigurationError | codigo fijo |
| Request/UUID/key invalido; HTTP400/405/406/415/422 | GatewayValidationError | codigo, status y UUID si ya disponible |
| HTTP401 | GatewayUnauthorized | codigo, status, UUID |
| HTTP403 | GatewayForbidden | codigo, status, UUID |
| HTTP409 POST | GatewayIdempotencyConflict | codigo, status, UUID |
| HTTP404 PDF | GatewayEvidenceNotFound | codigo, status, UUID |
| HTTP409 PDF | GatewayEvidenceConflict | codigo, status, UUID |
| HTTP429 | GatewayRateLimited | codigo, status, UUID; sin retry |
| HTTP500/502/503 y otros5xx salvo504 | GatewayProviderError | codigo, status, UUID |
| HTTP504 o timeout connect/read/stream | GatewayTimeout | codigo, status si HTTP, UUID |
| DNS/conexion/TLS | GatewayNetworkError | codigo y UUID; sin excepcion remota |
| HTTP3xx POST/PDF | GatewayRedirectError | codigo, status, UUID; redirect no seguido |
| Content-Type incorrecto | GatewayContentTypeError | codigo, status200, UUID |
| JSON invalido/duplicados/NaN/Infinity | GatewayJSONError | codigo, status200, UUID |
| Schema/version/DTO invalido | GatewaySchemaError | codigo, status200, UUID |
| Correlacion/eco idempotencia invalido | GatewayCorrelationError | codigo, status200, UUID |
| PDF vacio/firma invalida/limite invalido | GatewayPDFError | codigo, status200, UUID |
| Cualquier otro HTTP no200 | GatewayInvalidResponse | codigo, status, UUID |

El proveedor tiene envelope ERROR con mensaje sanitizado, consultaId y codigo
proveedor opcionales. El consumidor NO retiene esos bodies ni mensajes arbitrarios,
no llama json() para errores HTTP y no registra auth, payload, XML, PII ni bytes PDF.
Health mantiene GatewayInvalidResponse para sus fallos de validacion como en10B.

## Evidencia PDF

consulta_id requiere string UUID valido y se canoniza antes de construir la URL.
El servidor filtra misma identidad de consumidor, provider y operacion HDC; otro
consumidor o consulta ausente responde404. No se prueba existencia por otra via.
409 evidencia no disponible/no efectiva/invalida; 503 fallo local al recuperarla.
La regeneracion PDF del servidor, si aplica, usa XML sanitizado ya guardado, no HDC real.
El consumidor solo retorna EvidenciaPDFHDC inmutable con bytes en memoria.
No escribe disco/DB, no expone URL publica ni endpoint frontend.
Limite20MiB es una proteccion DEL CONSUMIDOR, no cuota publicada del proveedor.
Se exige PDF no vacio con firma %PDF-, content-type y X-Request-ID coherentes.
Content-Length es opcional; control de tamano tambien en streaming.
Session/response se cierran incluso por error de schema, HTTP o timeout streaming.
La firma inicial no equivale a validacion de toda la estructura interna del PDF.

## Configuracion y transporte

Sin nuevas variables, token ni cambios en .env:
RISK_GATEWAY_ENABLED, BASE_URL, INTERNAL_CLIENT, INTERNAL_SECRET, VERIFY_SSL,
CA_BUNDLE, CONNECT_TIMEOUT, READ_TIMEOUT (con prefijo RISK_GATEWAY_ para cada una).
Timeouts default5/30; HTTPS y verify TLS conservados; CA opcional.
La excepcion DEBUG de verify=false preexistente10B no se amplifica.
Session propia trust_env=false, sin netrc/proxies del ambiente.
allow_redirects=false, sin retry automatico en consumidor ni politica Celery.
Los retries internos legacy del proveedor no se alteran en esta fase.

## Uso explicito (solo referencia, no ejecutado)

```python
from apps.integraciones.risk_gateway.clientes import PreselectaGatewayClient, HDCGatewayClient
from apps.integraciones.risk_gateway.consultas import PreselectaRequest, HDCRequest

# Payload sintetico. Ejecutar estas lineas CON clientes configurados hace HTTP:
pre = PreselectaRequest.desde_payload({
    "identidad": {"tipo_documento": "1", "numero_documento": "0000000000",
                 "primer_apellido": "SINTETICO"},
    "estrategia_id": "25674",
    "parametros": {"LINEA_CREDITO": "1", "TIPO_ASOCIADO": "1",
                  "MEDIO_PAGO": "1", "ACTIVIDAD": "1"},
})
hdc = HDCRequest.desde_payload({
    "persona": "natural", "person_id_type": "1",
    "person_id_number": "0000000000", "person_last_name": "SINTETICO",
})
# Invocar solo bajo autorizacion posterior, nunca en startup/check/test:
# resultado = PreselectaGatewayClient().consultar_preselecta(
#     pre, request_id="00000000-0000-4000-8000-000000000001",
#     idempotency_key="operacion-sintetica:1")
# resultado_hdc = HDCGatewayClient().consultar_hdc(hdc)
# evidencia = HDCGatewayClient().obtener_pdf_hdc(resultado_hdc["resultado"]["consulta_id"])
```

## Tests y gates offline

Desde backend con venv/dependencias instaladas:
```powershell
python -B -m apps.integraciones.risk_gateway.tests test apps.integraciones.risk_gateway.tests --noinput
python -B -m apps.integraciones.risk_gateway.tests test apps.integraciones.risk_gateway.tests.test_health --noinput
python -B -m apps.integraciones.risk_gateway.tests test --noinput
python -B -m apps.integraciones.risk_gateway.tests check
python -B -m apps.integraciones.risk_gateway.tests makemigrations --check --dry-run
```

Runner bloquea sockets/DNS/HTTP/Oracle antes del discovery; tests nuevos bloquean
tambien constructores PRESELECTA/HDC. Solo fixtures sinteticos .invalid y UUID ficticios.
Tests cubren ambos POST, correlacion, contratos, idempotencia, errores, null/cero,
Decimal, datos originales, ausencia del agregado, DTO inmutable, PDF efimero y AST.
Health45 casos se conservan: solo el test AST distingue POST explicitos del paquete
de la operacion Health que sigue siendo GET. Los resultados de gates se reportan
en la entrega; este documento no ejecuta consultas ni genera credenciales.

No se modifican pipeline/views/Celery/modelos/migraciones/scoring/capacidad,
CrediHoy/shadow/frontend/api_data_v1. AST protege dependencias y persistencia;
auditoria Git delimita archivos (la auditoria de un commit no es un unit test).
Sin commit/push/deploy/E2E.

## Resultado de validacion local 2026-10-07

- Risk Gateway completo: 130/130 (Health45 + consultas/PDF/arquitectura85).
- Health ejecutado tambien por separado: 45/45.
- Backend completo: 173/173 con SQLite en memoria y mocks.
- Auditoria global de red: 0 intentos HTTP/socket/DNS/Oracle interceptados.
- Django check: sin incidencias; makemigrations check/dry-run: sin cambios.
- Compile/import offline: 152 fuentes, 15 modulos, sin errores.
- git diff --check: sin incidencias; index vacio.
- Auditoria de rutas protegidas: sin diff; frontend sin acceso al Gateway.
- Worktree original mantiene exactamente el status previo; PRESELECTA read-only.
- HEAD sigue en la base aprobada; ningun commit/push/deploy ejecutado.
