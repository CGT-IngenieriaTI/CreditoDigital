# historial_pago

Esta app integra la consulta HC2 / HC2PJ y procesa el XML financiero.

Que hace:
- Construye y envia el request SOAP a DataCredito.
- Extrae el XML interno de la respuesta SOAP.
- Limpia y parsea el XML de forma robusta.
- Calcula metricas financieras como pasivos, saldos, cupos y cuotas.
- Persiste la consulta, el XML y el resultado normalizado.

Modelo principal:
- `HistorialPagoConsulta`

Cuando se usa:
- Despues de PRESELECTA, cuando la preseleccion permite continuar.
