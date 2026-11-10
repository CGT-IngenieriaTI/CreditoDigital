from dataclasses import dataclass, field
import hmac
from uuid import UUID, uuid4

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.solicitudes.models import Solicitud
from apps.utils.models import AuditLog
from ..clientes import HDCGatewayClient, PreselectaGatewayClient
from ..contratos import HDCNormalizado, HDCRequest, PreselectaNormalizado, PreselectaRequest
from ..errores import GatewayError, GatewayForbidden, GatewayUnauthorized, GatewayValidationError
from .errores import ConsentimientoInvalido, IdentidadInconsistente, OperacionError, OperacionIncompatible, OrquestacionDesactivada
from .models import EstadoOperacion as Estado, OperacionGateway, ProveedorOperacion
from .validacion import huella, validar


@dataclass(frozen=True, slots=True)
class ResultadoOperacion:
    operacion_id: UUID
    proveedor: str
    estado: str
    request_id: UUID
    consulta_id: UUID | None
    version_contrato: str
    codigo_error: str
    reutilizada: bool = False
    respuesta: PreselectaNormalizado | HDCNormalizado | None = field(default=None, repr=False, compare=False)


def _enabled():
    if getattr(settings, "RISK_GATEWAY_ORCHESTRATION_ENABLED", False) is not True:
        raise OrquestacionDesactivada()
    if connection.in_atomic_block or not connection.get_autocommit():
        raise OperacionError("OPERACION_REQUIERE_COMMIT_DURABLE")
    if connection.vendor not in {"sqlite", "postgresql"}:
        raise OperacionError("BACKEND_CONCURRENCIA_NO_VALIDADO")
    if connection.vendor == "sqlite" and settings.DEBUG is not True:
        raise OperacionError("ORQUESTACION_SQLITE_SOLO_LOCAL")
    if connection.vendor == "postgresql" and getattr(settings, "RISK_GATEWAY_POSTGRESQL_CONCURRENCY_CERTIFIED", False) is not True:
        raise OperacionError("CONCURRENCIA_POSTGRESQL_PENDIENTE_CERTIFICACION")


def _proveedor(request):
    if type(request) is PreselectaRequest:
        return ProveedorOperacion.PRESELECTA
    if type(request) is HDCRequest:
        return ProveedorOperacion.HDC
    raise OperacionError("DTO_GATEWAY_INVALIDO")


def _audit(op):
    # No usar audit_event: absorbe errores, pero aqui la transicion exige auditoria atomica.
    AuditLog.objects.create(solicitud_id=op.solicitud_id, event="gateway_operacion_estado",
        request_id=str(op.request_id), payload={"operacion_id": str(op.pk), "proveedor": op.proveedor,
                                               "estado": op.estado, "codigo_error": op.codigo_error})


def _resultado(op, *, respuesta=None, reutilizada=False):
    return ResultadoOperacion(op.pk, op.proveedor, op.estado, op.request_id, op.consulta_id,
                              op.version_contrato, op.codigo_error, reutilizada, respuesta)


def _recibo(op):
    return huella("recibo", {"operacion_id": str(op.pk), "request_id": str(op.request_id),
        "consulta_id": str(op.consulta_id), "version": op.version_contrato, "estado": op.estado,
        "request_hmac": op.request_hmac})


class OrquestadorGateway:
    def __init__(self, *, preselecta=None, hdc=None):
        self.preselecta = preselecta
        self.hdc = hdc

    def preparar(self, solicitud_id, request):
        """Confirma identidad/consentimiento y COMMIT de la clave antes de llamar."""
        _enabled()
        provider = _proveedor(request)
        try:
            with transaction.atomic(durable=True):
                solicitud = Solicitud.objects.select_related("solicitante", "consentimiento_consumo__otp").get(pk=solicitud_id)
                consent, identity, consent_hash, request_hash = validar(solicitud, request)
                op = OperacionGateway.objects.filter(solicitud=solicitud, proveedor=provider).first()
                created = False
                if op is None:
                    operation_id = uuid4()
                    op, created = OperacionGateway.objects.get_or_create(solicitud=solicitud, proveedor=provider,
                        defaults={"id": operation_id, "consentimiento": consent, "identidad_hmac": identity,
                                  "consentimiento_hmac": consent_hash, "request_hmac": request_hash,
                                  "idempotency_key": "cd2b:" + operation_id.hex})
                self._compatible(op, consent.pk, identity, consent_hash, request_hash)
                if created:
                    _audit(op)
            return op
        except DatabaseError:
            raise OperacionError("RESERVA_LOCAL_NO_CONFIRMADA") from None

    @staticmethod
    def _compatible(op, consent_id, identity, consent_hash, request_hash):
        if (op.idempotency_key != "cd2b:" + op.pk.hex
                or op.consentimiento_id != consent_id or not hmac.compare_digest(op.identidad_hmac, identity)
                or not hmac.compare_digest(op.consentimiento_hmac, consent_hash)
                or not hmac.compare_digest(op.request_hmac, request_hash)):
            raise OperacionIncompatible()

    def ejecutar(self, solicitud_id, request):
        op = self.preparar(solicitud_id, request)
        if op.estado in {Estado.COMPLETADA, Estado.SIN_INFORMACION}:
            if op.consulta_id is None or not op.recibo_hmac or not hmac.compare_digest(op.recibo_hmac, _recibo(op)):
                return self._terminar(op, Estado.PENDIENTE_CONCILIACION, "RECIBO_LOCAL_INVALIDO", desde=op.estado)
            return _resultado(op, reutilizada=True)
        if op.estado != Estado.PENDIENTE:
            return _resultado(op)
        # UPDATE condicional es el arbitro entre procesos; no depende de locks Python/SQLite.
        try:
            with transaction.atomic(durable=True):
                won = OperacionGateway.objects.filter(pk=op.pk, estado=Estado.PENDIENTE).update(
                    estado=Estado.EN_EJECUCION, inicio_at=timezone.now(), updated_at=timezone.now())
                op.refresh_from_db()
                if won:
                    _audit(op)
            if not won:
                return _resultado(op)
        except DatabaseError:
            raise OperacionError("EJECUCION_LOCAL_NO_CONFIRMADA") from None
        try:
            # Consentimiento o identidad pudieron cambiar entre reserva y claim.
            current = Solicitud.objects.select_related("solicitante", "consentimiento_consumo__otp").get(pk=solicitud_id)
            consent, identity, consent_hash, request_hash = validar(current, request)
            self._compatible(op, consent.pk, identity, consent_hash, request_hash)
            client = (self.preselecta or PreselectaGatewayClient()) if op.proveedor == ProveedorOperacion.PRESELECTA else (self.hdc or HDCGatewayClient())
            response = client.consultar(request, request_id=str(op.request_id), idempotency_key=op.idempotency_key)
            expected = PreselectaNormalizado if op.proveedor == ProveedorOperacion.PRESELECTA else HDCNormalizado
            if type(response) is not expected:
                raise OperacionError("RESPUESTA_GATEWAY_INCOMPATIBLE")
            expected.desde_payload(response.a_dict())
            if (UUID(response["requestId"]) != op.request_id
                    or response["resultado"]["request_id"] != str(op.request_id)):
                raise OperacionError("RESPUESTA_GATEWAY_INCOMPATIBLE")
            state = Estado.COMPLETADA if response["resultado"]["estado_tecnico"] == "COMPLETADO" else Estado.SIN_INFORMACION
            return self._terminar(op, state, respuesta=response)
        except (GatewayValidationError, GatewayUnauthorized, GatewayForbidden,
                ConsentimientoInvalido, IdentidadInconsistente, OperacionIncompatible) as exc:
            return self._terminar(op, Estado.ERROR_DEFINITIVO, exc.codigo, getattr(exc, "status", None))
        except GatewayError as exc:
            # Timeout, 409, respuesta invalida, red y errores remotos: no sabemos si se consulto.
            return self._terminar(op, Estado.PENDIENTE_CONCILIACION, exc.codigo, exc.status)
        except Exception:
            return self._terminar(op, Estado.PENDIENTE_CONCILIACION, "RESULTADO_GATEWAY_DESCONOCIDO")

    def _terminar(self, op, estado, codigo="", status=None, *, respuesta=None, desde=Estado.EN_EJECUCION):
        try:
            with transaction.atomic(durable=True):
                values = {"estado": estado, "codigo_error": codigo, "http_status": status,
                          "fin_at": timezone.now(), "updated_at": timezone.now()}
                if respuesta is not None:
                    op.estado = estado
                    op.consulta_id = UUID(respuesta["resultado"]["consulta_id"])
                    op.version_contrato = respuesta["versionContrato"]
                    values.update(consulta_id=op.consulta_id, version_contrato=op.version_contrato, recibo_hmac=_recibo(op))
                changed = OperacionGateway.objects.filter(pk=op.pk, estado=desde).update(**values)
                op.refresh_from_db()
                if changed:
                    _audit(op)
            return _resultado(op, respuesta=respuesta if changed else None)
        except DatabaseError:
            # La reserva EN_EJECUCION ya esta confirmada: nunca volver a enviar.
            raise OperacionError("RESULTADO_LOCAL_NO_CONFIRMADO_REQUIERE_CONCILIACION") from None

    def marcar_interrumpida(self, operation_id, *, worker_detenido_confirmado=False):
        """Accion explicita de recuperacion, solo tras comprobar que el worker termino."""
        _enabled()
        if worker_detenido_confirmado is not True:
            raise OperacionError("RECUPERACION_REQUIERE_CONFIRMACION_EXPLICITA")
        op = OperacionGateway.objects.get(pk=operation_id)
        if op.estado == Estado.EN_EJECUCION:
            return self._terminar(op, Estado.PENDIENTE_CONCILIACION, "EJECUCION_INTERRUMPIDA")
        return _resultado(op)
