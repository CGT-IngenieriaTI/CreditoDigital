# FASE 10B: Health estructural aislado

Base: `fc034b75c995a23d9c89338b4e0ffc49290366ff` (`origin/main`).
El delta desde produccion `c5fb47c04e8966e5d97569686b45464f44aba41a`
solo agrega seleccion explicita de URL al comando diagnostico TLS existente.
No se ejecuto ese diagnostico. No se incorpora el commit frontend `93a363c`.

## Alcance

Operacion unica: `GET /api/internal/v1/health/`. No POST, proveedores,
scoring, PDF, persistencia, pipeline, tareas, ni modificaciones de modelos.
El comando reside en el app instalado `xcore_consumo`; el paquete nuevo
`apps.integraciones.risk_gateway` no requiere registro como app Django.
Importar el paquete/settings no realiza solicitudes ni valida secretos.

## Configuracion opt-in

```dotenv
RISK_GATEWAY_ENABLED=N
RISK_GATEWAY_BASE_URL=https://consulta.congente.coop:8046
RISK_GATEWAY_INTERNAL_CLIENT=credito-digital-congente
RISK_GATEWAY_INTERNAL_SECRET=
RISK_GATEWAY_VERIFY_SSL=Y
RISK_GATEWAY_CA_BUNDLE=
RISK_GATEWAY_CONNECT_TIMEOUT=5
RISK_GATEWAY_READ_TIMEOUT=30
```

Base URL HTTPS de origen, sin path, credenciales, query ni fragmento.
Secreto sin prefijo Bearer, formato URL-safe de 32 a 256 caracteres,
validado solo cuando se solicita Health con integracion habilitada.
CA bundle opcional: ruta existente de un archivo de confianza.
TLS no puede deshabilitarse con DEBUG=False. Timeouts en segundos:
conexion 5, lectura 30, positivos y finitos. Sin proxies/netrc implicitos,
redirects ni retries. No configurar ni activar produccion en esta fase.

## Contrato

Headers: Authorization Bearer, X-Internal-Client, X-Request-ID UUID y Accept JSON.
Exito solo 200 con Content-Type application/json o application/*+json;
objeto con apiVersion=v1, status=ready, providerChecks=false boolean y
requestId identico al enviado. Si viene X-Request-ID de respuesta, coincide
tambien. Claves adicionales se toleran pero no se conservan/imprimen.
No depende de orden de propiedades ni Content-Length.
Rechaza JSON ambiguo con claves duplicadas y constantes no JSON.
providerChecks=false significa readiness estructural, no salud del proveedor.

## Errores seguros

| Caso | Codigo |
| --- | --- |
| Disabled | GATEWAY_DISABLED |
| Configuracion | GATEWAY_CONFIGURACION_INVALIDA |
| UUID de entrada | GATEWAY_REQUEST_INVALIDO |
| 401 | GATEWAY_NO_AUTORIZADO |
| 403 | GATEWAY_PROHIBIDO |
| 429 | GATEWAY_LIMITE_ALCANZADO |
| 5xx excepto 504 | GATEWAY_ERROR_REMOTO |
| 504, timeout conexion/lectura | GATEWAY_TIMEOUT |
| DNS/conexion/TLS | GATEWAY_ERROR_CONEXION |
| HTTP inesperado/redirect, JSON/schema/correlacion | GATEWAY_RESPUESTA_INVALIDA |

No se conserva body remoto ni detalle de excepciones requests. El comando
falla con CommandError y codigo controlado. Nunca imprime configuracion,
headers, secretos, hashes ni cuerpos remotos. En exito imprime unicamente
httpStatus, apiVersion, status, providerChecks y requestId.

## Comando explicito (NO ejecutado contra gateway en esta fase)

```powershell
python manage.py check_risk_gateway
```

No chequeos automaticos de DB/migraciones del comando, ni escritura DB.
La configuracion viene de los settings backend existentes.

## Gates completamente offline

Desde backend, con dependencias instaladas:

```powershell
python -B -m apps.integraciones.risk_gateway.tests test apps.integraciones.risk_gateway.tests --noinput
python -B -m apps.integraciones.risk_gateway.tests test --noinput
python -B -m apps.integraciones.risk_gateway.tests check
python -B -m apps.integraciones.risk_gateway.tests makemigrations --check --dry-run
```

El runner usa SQLite en memoria y bloquea HTTP real, sockets, DNS y Oracle
antes de discovery/setup. La suite especifica usa exclusivamente respuestas
sinteticas. Cada prueba tambien bloquea red al ejecutarse fuera del runner.
Los invariantes de imports/operaciones se verifican sin snapshots de backend.
Ausencia de cambios frontend/pipeline/Celery se audita separadamente con Git,
no es una dependencia permanente de tests sobre el estado del worktree.
No utiliza .env, fuentes privadas, fixtures shadow, capturas ni Excel locales.

Esta fase no realiza commit, push, merge ni deploy. El worktree original
permanece intacto y las modificaciones del release quedan sin stagear.

## Resultado de validacion local 2026-10-07

- Health especifico: 45/45 PASS.
- Suite completa del candidato: 88 tests, 81 PASS, 2 FAIL, 5 ERROR.
- Suite del checkout limpio fc034b7: 43 tests, 36 PASS, mismos 2 FAIL y 5 ERROR.
- Django check: sin problemas.
- Makemigrations --check --dry-run: No changes detected.
- Frontend, pipeline, Celery y clientes de proveedores: sin diff contra base.
- Worktree original: status y HEAD 93a363c conservados.
- Index release: vacio. Ningun commit/push/merge/deploy.

Los siete fallos anteriores se reproducen sin codigo Health en un segundo
worktree limpio. Varias pruebas mockean PreselectaClient.evaluate, pero no
su constructor; sin configuracion OKTA local, el constructor falla antes
del metodo mockeado. Las consecuencias incluyen ERROR en vez de RECHAZADO,
ausencia de HistorialPagoConsulta y claves de snapshot ausentes. No se
agregaron credenciales ni se modificaron esos tests/pipeline para ocultarlo.

Todos pertenecen a apps.xcore_consumo.tests.ConsumoRobustFlowTests:

| Test | Punto de fallo en tests.py |
| --- | --- |
| test_otp_verify_moves_to_result_when_preselecta_rejects | 639 |
| test_preselecta_rechazo_no_consulta_historial | 686 |
| test_otp_verify_persists_historial_xml | 491 |
| test_process_blocks_when_comision_garantia_returns_error | 945 |
| test_process_returns_503_when_historial_cert_config_is_invalid | 752 |
| test_snapshot_reuses_persisted_historial_xml | 536 |
| test_snapshot_skips_stored_historial_when_identity_differs | 596 |

Veredicto: **NO LISTO PARA CONGELAR 10B** por gate de suite completa no verde,
aunque la implementacion especifica pasa y no agrega fallos sobre la base.
La correccion de aislamiento de tests legacy queda fuera de esta fase.

## Inventario exacto del release

Modificados (29 lineas agregadas en total):

- .env.docker.example
- backend/.env.example
- backend/core/settings.py

Creados:

- backend/apps/integraciones/__init__.py
- backend/apps/integraciones/risk_gateway/__init__.py
- backend/apps/integraciones/risk_gateway/configuracion.py
- backend/apps/integraciones/risk_gateway/contratos.py
- backend/apps/integraciones/risk_gateway/errores.py
- backend/apps/integraciones/risk_gateway/transporte.py
- backend/apps/integraciones/risk_gateway/clientes.py
- backend/apps/integraciones/risk_gateway/HEALTH_10B.md
- backend/apps/integraciones/risk_gateway/tests/__init__.py
- backend/apps/integraciones/risk_gateway/tests/__main__.py
- backend/apps/integraciones/risk_gateway/tests/settings.py
- backend/apps/integraciones/risk_gateway/tests/test_health.py
- backend/apps/xcore_consumo/management/commands/check_risk_gateway.py
