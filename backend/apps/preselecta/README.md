# preselecta

Esta app integra la consulta PRESELECTA.

Que hace:
- Construye el payload real del proveedor.
- Autentica con Okta si aplica.
- Envia la consulta de preseleccion.
- Normaliza la respuesta de negocio (`APROBADO`, `ZONA_GRIS`, `RECHAZADO`, `ERROR`).
- Persiste request, response y mensaje de la consulta.

Modelo principal:
- `PreselectaConsulta`

Cuando se usa:
- Despues de autorizaciones y OTP. Es la compuerta principal antes de historial de pago.
