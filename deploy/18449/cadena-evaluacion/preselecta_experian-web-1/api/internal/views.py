"""HTTP adapter; provider logic and functional audit stay in application services."""
from django.http import FileResponse, HttpResponse
from integrations.services.query_results import ErrorCode, QueryContext, TechnicalStatus, TechnicalOutcome, normalize_provider_code
import json
import logging
import math
import re
from time import monotonic
from uuid import UUID, uuid4
from django.conf import settings
from django.http import FileResponse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.parsers import JSONParser
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from api.services.hdc_contracts import CONTRACT_VERSION as HDC_VERSION
from api.services.hdc_query import HDCQueryService
from api.models import CreditBureauProvider, CreditReportQuery
from api.services.report_files import ReportEvidenceError, open_report_pdf
from integrations.services.preselecta_contracts import CONTRACT_VERSION as PRESELECTA_VERSION
from integrations.services.preselecta_input import PreselectaInputError
from integrations.services.preselecta_query import PreselectaQueryService
from integrations.services.query_results import ErrorCode, QueryContext, TechnicalStatus, normalize_provider_code
from .controls import CapabilityPermission, ConsumerCapabilityThrottle, InternalBearerAuthentication, InternalError, registry, reserve_call, unavailable, validate_key
from .serializers import HDCInternalSerializer, PreselectaInternalSerializer
from .transport import internal_transport_is_secure
logger = logging.getLogger('api.internal')

def outcome_http(outcome):
    if outcome.status in (TechnicalStatus.COMPLETED, TechnicalStatus.NO_INFORMATION):
        return 200
    if outcome.status == TechnicalStatus.VALIDATION:
        return 422
    if outcome.error_code == ErrorCode.TIMEOUT:
        return 504
    if outcome.error_code in (ErrorCode.INVALID_RESPONSE, ErrorCode.PROVIDER_AUTH):
        return 502
    if outcome.error_code in (ErrorCode.PROVIDER, ErrorCode.TRANSPORT, ErrorCode.CONFIGURATION):
        return 503
    return 500

class InternalAPIView(APIView):
    authentication_classes = [InternalBearerAuthentication]
    permission_classes = [CapabilityPermission]
    throttle_classes = [ConsumerCapabilityThrottle]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]
    capability = None
    http_method_names = ['get', 'post', 'options']

    def initialize_request(self, request, *args, **kwargs):
        self.started = monotonic()
        self.consumer, self.query_id, self.provider_code = ('', None, '')
        self.technical_status = TechnicalStatus.TECHNICAL_ERROR
        self.idempotency_key = None
        self.invalid_request_id = False
        supplied = request.headers.get('X-Request-ID')
        self.request_id = uuid4()
        if supplied is not None:
            if re.fullmatch('[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', supplied):
                self.request_id = UUID(supplied)
            else:
                self.invalid_request_id = True
        return super().initialize_request(request, *args, **kwargs)

    def initial(self, request, *args, **kwargs):
        if self.invalid_request_id:
            raise InternalError('INVALID_REQUEST_ID', 'X-Request-ID debe ser un UUID valido.')
        if not settings.INTERNAL_API_ENABLED:
            raise unavailable()
        if settings.INTERNAL_API_REQUIRE_HTTPS and (not internal_transport_is_secure(request)):
            raise InternalError('HTTPS_REQUIRED', 'La API interna requiere HTTPS.', 403)
        super().initial(request, *args, **kwargs)
        if request.query_params:
            raise InternalError('INVALID_REQUEST', 'La API interna no admite parametros de querystring.')
        if self.capability and request.method == 'POST':
            self.idempotency_key = validate_key(request.headers.get('Idempotency-Key'))

    def perform_authentication(self, request):
        super().perform_authentication(request)
        self.consumer = getattr(request.user, 'client_id', '')

    def context(self, request):
        return QueryContext(request_id=self.request_id, origin='API', consumer=self.consumer, ip_address=request.META.get('REMOTE_ADDR'))

    def handle_exception(self, exc):
        retry_after = None
        if isinstance(exc, InternalError):
            code, message, status = (exc.safe_code, exc.safe_message, exc.status_code)
        elif isinstance(exc, (exceptions.AuthenticationFailed, exceptions.NotAuthenticated)):
            code, message, status = ('INTERNAL_AUTH', 'Credencial interna ausente o no valida.', 401)
        elif isinstance(exc, exceptions.PermissionDenied):
            code, message, status = ('INTERNAL_SCOPE', 'El consumidor no tiene permiso para esta capacidad.', 403)
        elif isinstance(exc, exceptions.Throttled):
            code, message, status = ('INTERNAL_RATE_LIMIT', 'Limite interno de solicitudes alcanzado.', 429)
            retry_after = math.ceil(exc.wait) if exc.wait else None
        elif isinstance(exc, (exceptions.ValidationError, exceptions.ParseError, PreselectaInputError)):
            code, message, status = ('INVALID_REQUEST', 'Request interno no valido.', 400)
        elif isinstance(exc, exceptions.UnsupportedMediaType):
            code, message, status = ('JSON_REQUIRED', 'El request debe usar application/json.', 415)
        elif isinstance(exc, exceptions.NotAcceptable):
            code, message, status = ('JSON_REQUIRED', 'La respuesta de la API interna es application/json.', 406)
        elif isinstance(exc, exceptions.MethodNotAllowed):
            code, message, status = ('METHOD_NOT_ALLOWED', 'Metodo no permitido.', 405)
        else:
            code, message, status = ('INTERNAL', 'No se pudo completar la solicitud.', 500)
        self.technical_status = TechnicalStatus.VALIDATION if status == 400 else TechnicalStatus.TECHNICAL_ERROR
        response = Response({'requestId': str(self.request_id), 'estadoTecnico': self.technical_status, 'error': {'codigo': code, 'mensaje': message}}, status=status)
        if status == 401:
            response['WWW-Authenticate'] = 'Bearer realm="internal-api-v1"'
        if retry_after:
            response['Retry-After'] = str(retry_after)
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['X-Request-ID'] = str(self.request_id)
        response['Cache-Control'] = 'no-store'
        if self.idempotency_key is not None:
            response['Idempotency-Key'] = self.idempotency_key
            response['X-Idempotency-Status'] = 'IDEMPOTENCIA_DE_NEGOCIO_PENDIENTE'
        logger.info(json.dumps({'request_id': str(self.request_id), 'consumidor': self.consumer, 'capacidad': self.capability or 'health', 'consulta_id': str(self.query_id) if self.query_id else None, 'estado_tecnico': self.technical_status, 'duracion_ms': round((monotonic() - self.started) * 1000), 'http_status': response.status_code, 'codigo_proveedor': self.provider_code}, sort_keys=True))
        return response

    def result_response(self, result, dto, reservation, version):
        self.query_id, self.technical_status = (result.query_id, result.outcome.status)
        self.provider_code = normalize_provider_code(result.outcome.provider_code)
        if reservation is not None:
            reservation.query_id, reservation.technical_status = (result.query_id, result.outcome.status)
            reservation.completed_at = timezone.now()
            reservation.save(update_fields=['query_id', 'technical_status', 'completed_at'])
        status = outcome_http(result.outcome)
        if status == 200:
            if dto is None:
                raise InternalError('INVALID_RESPONSE', 'No se obtuvo un contrato normalizado valido.', 502)
            return Response({'requestId': str(self.request_id), 'versionContrato': version, 'resultado': dto.to_dict()})
        return Response({'requestId': str(self.request_id), 'consultaId': str(result.query_id), 'versionContrato': version, 'estadoTecnico': result.outcome.status, 'codigoProveedor': self.provider_code, 'error': {'codigo': result.outcome.error_code, 'mensaje': result.outcome.message}}, status=status)

class PreselectaConsultView(InternalAPIView):
    capability = 'preselecta:consultar'
    http_method_names = ['post', 'options']

    def post(self, request):
        serializer = PreselectaInternalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        context = self.context(request)
        dto = serializer.to_service(context)
        fingerprint = dto.to_dict()
        fingerprint.pop('metadata')
        reservation = reserve_call(self.consumer, self.capability, self.idempotency_key, fingerprint, self.request_id)
        result = PreselectaQueryService().execute(dto, context=context)
        return self.result_response(result, result.normalized_preselecta, reservation, PRESELECTA_VERSION)

class HDCConsultView(InternalAPIView):
    capability = 'hdc:consultar'
    http_method_names = ['post', 'options']

    def post(self, request):
        serializer = HDCInternalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        context = self.context(request)
        payload, juridica = serializer.to_service(context)
        fingerprint = {'payload': payload, 'juridica': juridica, 'versionContrato': HDC_VERSION}
        reservation = reserve_call(self.consumer, self.capability, self.idempotency_key, fingerprint, self.request_id)
        result = HDCQueryService().execute(payload, juridica=juridica, context=context)
        return self.result_response(result, result.normalized_hdc, reservation, HDC_VERSION)

class InternalHealthView(InternalAPIView):
    http_method_names = ['get']

    def get(self, request):
        registry()
        self.technical_status = TechnicalStatus.COMPLETED
        return Response({'requestId': str(self.request_id), 'status': 'ready', 'apiVersion': 'v1', 'providerChecks': False})

class PDFEvidenceRenderer(BaseRenderer):
    media_type = 'application/pdf'
    format = 'pdf'
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data if isinstance(data, bytes) else JSONRenderer().render(data)

class XMLEvidenceRenderer(PDFEvidenceRenderer):
    media_type = 'application/xml'
    format = 'xml'

class HDCXmlEvidenceView(InternalAPIView):
    """Read owned evidence; never query the bureau or create another query row."""
    capability = 'hdc:evidencia'
    http_method_names = ['get']
    renderer_classes = [XMLEvidenceRenderer, JSONRenderer]

    def get(self, request, consulta_id):
        import hashlib
        import base64
        if not isinstance(request.accepted_renderer, (XMLEvidenceRenderer, JSONRenderer)):
            raise exceptions.NotAcceptable()
        query = CreditReportQuery.objects.filter(request_uuid=consulta_id, provider=CreditBureauProvider.DATACREDITO, consumer=self.consumer, operation__in=('consultarHC2', 'consultarHC2PJ'), status='SUCCESS', technical_status=TechnicalStatus.COMPLETED).first()
        if query is None:
            raise InternalError('EVIDENCE_NOT_FOUND', 'Evidencia no disponible para el consumidor.', 404)
        raw = query.soap_response_xml.encode('utf-8')
        if not 0 < len(raw) <= 16 * 1024 * 1024:
            raise InternalError('EVIDENCE_UNAVAILABLE', 'Evidencia custodiada no disponible.', 409)
        self.query_id = query.request_uuid
        self.technical_status = TechnicalStatus.COMPLETED
        if isinstance(request.accepted_renderer, JSONRenderer):
            from api.services.hdc_normalizer import normalize_hdc
            normalized = normalize_hdc(query.soap_response_xml, consulta_id=query.request_uuid, request_id=query.request_id, tipo_documento_solicitado=query.person_id_type, resultado_tecnico=TechnicalOutcome(TechnicalStatus.COMPLETED, provider_code=query.provider_code, http_status=query.http_status))
            packet = {'versionContrato': 'credihoy-hdc-custodia-v1', 'xmlBase64': base64.b64encode(raw).decode(), 'xmlSha256': hashlib.sha256(raw).hexdigest(), 'normalizado': {'versionContrato': HDC_VERSION, 'requestId': str(query.request_id), 'resultado': normalized.to_dict()}, 'normalizadoReconstruidoDesdeXML': True, 'consultaNueva': False}
            content = json.dumps(packet, default=str).encode()
            response = HttpResponse(content, content_type='application/json')
        else:
            content = raw
            response = HttpResponse(content, content_type='application/xml')
        response['X-HDC-Request-ID'] = str(query.request_id)
        response['X-HDC-Holder-SHA256'] = hashlib.sha256(query.person_id_number.encode()).hexdigest()
        response['X-Content-SHA256'] = hashlib.sha256(content).hexdigest()
        response['Cache-Control'] = 'no-store'
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        if isinstance(response, Response):
            request.accepted_renderer = JSONRenderer()
            request.accepted_media_type = 'application/json'
        return super().finalize_response(request, response, *args, **kwargs)

class HDCPdfEvidenceView(InternalAPIView):
    capability = 'hdc:evidencia'
    http_method_names = ['get']
    renderer_classes = [PDFEvidenceRenderer, XMLEvidenceRenderer, JSONRenderer]

    def get(self, request, consulta_id):
        if isinstance(request.accepted_renderer, (XMLEvidenceRenderer, JSONRenderer)):
            return HDCXmlEvidenceView.get(self, request, consulta_id)
        if not isinstance(request.accepted_renderer, PDFEvidenceRenderer):
            raise exceptions.NotAcceptable()
        query = CreditReportQuery.objects.filter(request_uuid=consulta_id, provider=CreditBureauProvider.DATACREDITO, operation__in=('consultarHC2', 'consultarHC2PJ'), consumer=self.consumer).first()
        if query is None:
            raise InternalError('EVIDENCE_NOT_FOUND', 'Consulta HDC no disponible para el consumidor.', 404)
        self.query_id = query.request_uuid
        self.provider_code = normalize_provider_code(query.provider_code)
        try:
            document = open_report_pdf(query)
        except ReportEvidenceError as exc:
            raise InternalError(exc.code, exc.message, exc.status) from None
        self.technical_status = TechnicalStatus.COMPLETED
        return FileResponse(document, content_type='application/pdf', as_attachment=False, filename=f'hdc-{query.request_uuid}.pdf')

    def finalize_response(self, request, response, *args, **kwargs):
        if isinstance(response, Response):
            request.accepted_renderer = JSONRenderer()
            request.accepted_media_type = 'application/json'
        return super().finalize_response(request, response, *args, **kwargs)
