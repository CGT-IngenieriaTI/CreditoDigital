# Crédito Digital Congente

Base de proyecto para un flujo digital de crédito de consumo de Congente, con backend Django + DRF y frontend React + Bootstrap 5.

## Estructura

```text
backend/
  core/
  apps/
    usuarios/
    solicitudes/
    documentos/
    preselecta/
    historial_pago/
    xcore/
    decisiones/
    utils/
frontend/
  src/
docker-compose.yml
```

Los archivos `tsx` existentes en la raiz se dejaron intactos como referencia del front anterior. El flujo nuevo vive en `frontend/`.

## Backend

Incluye:

- API REST con Django REST Framework
- Apps desacopladas por dominio
- Pipeline automatizado para PRESELECTA, historial de pago y XCORE
- Auditoría y rate limiting
- Generación de PDFs con ReportLab
- Mocks configurables para integraciones externas REST y SOAP
- Soporte opcional para Celery si se instala y se activa `CREDIT_PIPELINE_ASYNC=1`

### Endpoints principales

- `GET /api/v1/health/`
- `GET /api/v1/csrf/`
- `POST /api/v1/solicitudes/`
- `POST /api/v1/solicitudes/<uuid>/autorizar/`
- `GET /api/v1/solicitudes/<uuid>/`
- `GET /api/v1/documentos/`
- `GET /api/v1/documentos/<codigo>/pdf/`
- `GET /api/v1/decisiones/<uuid>/`
- `GET /api/v1/decisiones/<uuid>/pdf/`

### Ejecucion local

1. Crear entorno virtual e instalar dependencias:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Copiar variables:

```powershell
Copy-Item .env.example .env
```

3. Ejecutar migraciones y servidor:

```powershell
python manage.py migrate
python manage.py runserver
```

4. Validar pruebas:

```powershell
python manage.py test apps.solicitudes
```

## Frontend

Incluye:

- Wizard mobile-first de 4 etapas
- Stepper y barra de progreso
- Visualizacion de PDFs antes de habilitar aceptacion
- Polling del pipeline hasta decision final
- Descarga del PDF generado por el backend
- Estetica fintech alineada a Congente con naranja y azul corporativos

### Ejecucion local

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Si PowerShell bloquea `npm`, usa `npm.cmd`.

## Integraciones

- `preselecta`: cliente REST, serializer, persistencia y logs
- `historial_pago`: cliente SOAP via `requests`, adaptador y normalizacion
- `xcore`: cliente REST y persistencia de resultado

Por defecto el proyecto trabaja con mocks para facilitar desarrollo. Para integrar servicios reales:

1. Cambia `CREDIT_USE_MOCK_SERVICES=0`
2. Configura `PRESELECTA_API_URL`, `HISTORIAL_PAGO_SOAP_URL` y `XCORE_API_URL`
3. Ajusta payloads y normalizadores segun contrato real

## Observaciones

- La validacion de mayoria de edad en esta primera base se resuelve por politica de producto y tipo documental. Si el negocio requiere validacion exacta por fecha de nacimiento, ese dato debe agregarse al formulario y al modelo.
- El pipeline hoy corre sincrono por defecto para simplificar la puesta en marcha. Se dejo preparado para migrar a Celery cuando se habilite la infraestructura de Redis/worker.


