# decisiones

Esta app guarda el resultado final de una solicitud.

Que hace:
- Persiste la decision final del flujo (`APROBADO`, `RECHAZADO`, `REVISION`).
- Guarda mensaje, observaciones, monto, plazo y tasa aprobada.
- Sirve como salida formal del proceso despues del analisis.

Modelo principal:
- `DecisionFinal`

Cuando se usa:
- Al final del flujo, cuando ya existe una evaluacion y se debe mostrar o persistir el resultado final.
