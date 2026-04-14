# utils

Esta app contiene utilidades compartidas del backend.

Que hace:
- Define modelos base con timestamps.
- Guarda auditoria transversal del sistema.
- Sirve de apoyo comun para las demas apps.

Modelos principales:
- `TimeStampedModel`
- `AuditLog`

Cuando se usa:
- En todo el proyecto para trazabilidad y modelos comunes.
