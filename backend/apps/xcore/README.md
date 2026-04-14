# xcore

Esta app es una integracion mas pequena y generica con XCORE.

Que hace:
- Guarda una consulta simple a XCORE con request/response, estado, resultado y mensaje.
- Funciona como una capa historica o mas basica de integracion.

Modelo principal:
- `XcoreConsulta`

Diferencia frente a `xcore_consumo`:
- `xcore` no modela el flujo completo del producto.
- No maneja OTP, consentimiento, PRESELECTA, historial, configuraciones ni evaluacion robusta.
- Es mucho mas simple y acotada.
