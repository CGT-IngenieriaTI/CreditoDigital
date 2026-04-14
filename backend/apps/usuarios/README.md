# usuarios

Esta app maneja al solicitante y el rol del asesor.

Que hace:
- Guarda la informacion base del solicitante.
- Define el perfil del asesor (`ASESOR`, `SUPERVISOR`, `ADMIN`).
- Resuelve el rol operativo del usuario autenticado.

Modelos principales:
- `Solicitante`
- `PerfilAsesor`

Cuando se usa:
- En el login interno y en la creacion/continuidad de solicitudes.
