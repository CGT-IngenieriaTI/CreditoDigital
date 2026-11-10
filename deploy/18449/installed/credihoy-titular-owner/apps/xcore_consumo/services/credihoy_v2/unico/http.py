"""Endpoint local autenticado. OFF por defecto, sin acceso a datos financieros."""

from ipaddress import ip_address
from uuid import UUID, uuid4

from django.conf import settings
from django.http import Http404
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle



def laboratorio_habilitado(request):
    database = settings.DATABASES["default"]
    nombre = str(database.get("NAME", ""))
    memoria = nombre == ":memory:" or (nombre.startswith("file:memorydb_")
                                      and "mode=memory" in nombre and "cache=shared" in nombre)
    try:
        local = ip_address(request.META.get("REMOTE_ADDR", "")).is_loopback
    except ValueError:
        local = False
    historico = (getattr(settings, "CREDIHOY_UNICO_HISTORICO_ENABLED", False) is True
        and getattr(settings, "CREDIHOY_HISTORICOS", None) is not None
        and (memoria or nombre == str(getattr(settings, "CREDIHOY_HISTORICO_DB_AUTORIZADA", "")))
        and request.headers.get("X-Credihoy-Laboratorio") == "HISTORICO_CONTROLADO")
    return (getattr(settings, "CREDIHOY_UNICO_SHADOW_LAB_ENABLED", False) is True
        and settings.DEBUG is True and database.get("ENGINE") == "django.db.backends.sqlite3"
        and local and (historico or memoria and request.headers.get("X-Credihoy-Laboratorio") == "SINTETICO_CD3C"))


class CredihoyUnicoShadowLabView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def initial(self, request, *args, **kwargs):
        revision = (request.method == "GET" and request.query_params.get("revision") == "historica"
            and getattr(settings, "CREDIHOY_REVISION_HISTORICA_ENABLED", False) is True
            and request.META.get("REMOTE_ADDR") in {"127.0.0.1", "::1"})
        if not revision and not laboratorio_habilitado(request):
            raise Http404
        super().initial(request, *args, **kwargs)

    def post(self, request):
        from .laboratorio import RequestLaboratorioInvalido, evaluar_laboratorio
        raw_id = request.headers.get("X-Request-ID")
        try:
            request_id = str(uuid4()) if raw_id is None else str(UUID(raw_id))
            if raw_id is not None and request_id != raw_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            return Response({"codigo": "REQUEST_INVALIDO"}, status=400)
        try:
            if request.headers.get("X-Credihoy-Laboratorio") == "HISTORICO_CONTROLADO":
                from .historico import HistoricoInvalido
                from .historico_solicitud import ConflictoHistorico, reproducir_solicitud
                try:
                    payload, reused = reproducir_solicitud(request.data, usuario=request.user,
                        request_id=request_id, clave=request.headers.get("Idempotency-Key"))
                except ConflictoHistorico:
                    return Response({"codigo": "OPERACION_REQUIERE_CONCILIACION", "requestId": request_id}, status=409,
                                    headers={"X-Request-ID": request_id, "Cache-Control": "no-store"})
                except HistoricoInvalido as exc:
                    return Response({"codigo": str(exc), "requestId": request_id}, status=400,
                                    headers={"X-Request-ID": request_id, "Cache-Control": "no-store"})
                return Response(payload, headers={"X-Request-ID": request_id, "Cache-Control": "no-store",
                    "X-Idempotency-Status": "REUTILIZADO" if reused else "CREADO"})
            payload = evaluar_laboratorio(request.data, request_id)
        except RequestLaboratorioInvalido:
            return Response({"codigo": "REQUEST_INVALIDO", "requestId": request_id}, status=400,
                            headers={"X-Request-ID": request_id})
        except APIException:
            raise
        except Exception:
            # Nunca registrar body/identidad, stack financiero o evidencia completa.
            return Response({"codigo": "ERROR_TECNICO", "requestId": request_id}, status=500,
                            headers={"X-Request-ID": request_id})
        return Response(payload, headers={"X-Request-ID": request_id, "Cache-Control": "no-store"})

    def get(self, request):
        if request.query_params.get("revision") == "historica":
            import json
            from pathlib import Path
            if (getattr(settings, "CREDIHOY_REVISION_HISTORICA_ENABLED", False) is not True
                    or request.user.username != getattr(settings, "CREDIHOY_FIRMA_OPERADOR_USERNAME", None)):
                raise Http404
            try:
                path = Path(settings.CREDIHOY_REVISION_HISTORICA_PATH).resolve(strict=True)
                if (path.is_symlink() or path.stat().st_size > 200_000
                        or any((p / ".git").exists() for p in path.parents)):
                    raise ValueError
                paquete = json.loads(path.read_text(encoding="utf-8"))
                result = paquete["resultado"]
                if (paquete["solicitudOperativa"] != str(settings.CREDIHOY_FIRMA_SOLICITUD_ID)
                        or result["revisionHistorica"]["noPersistida"] is not True
                        or result["decisionCrediticia"] != "NO_AUTORIZADA"
                        or result["autorizadoDecisionCrediticia"] is not False
                        or result["radicacionLinix"] != "NO_EJECUTADA"):
                    raise ValueError
                return Response(result, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return Response({"codigo": "REVISION_HISTORICA_NO_DISPONIBLE"}, status=409,
                    headers={"Cache-Control": "no-store"})
        if request.headers.get("X-Credihoy-Laboratorio") != "HISTORICO_CONTROLADO":
            return Response(status=405)
        if "Idempotency-Key" in request.headers:
            from .historico import HistoricoInvalido
            from .historico_solicitud import ConflictoHistorico, ResultadoHistoricoNoEncontrado, recuperar_resultado
            try:
                request_id = str(UUID(request.headers.get("X-Request-ID", "")))
                if request_id != request.headers.get("X-Request-ID"):
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                return Response({"codigo": "REQUEST_INVALIDO"}, status=400)
            headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
            try:
                payload = recuperar_resultado(usuario=request.user, request_id=request_id,
                    clave=request.headers.get("Idempotency-Key"))
                return Response(payload, headers=headers | {"X-Idempotency-Status": "REUTILIZADO"})
            except ResultadoHistoricoNoEncontrado:
                return Response({"codigo": "RESULTADO_HISTORICO_NO_ENCONTRADO", "requestId": request_id}, status=404, headers=headers)
            except ConflictoHistorico:
                return Response({"codigo": "OPERACION_REQUIERE_CONCILIACION", "requestId": request_id}, status=409, headers=headers)
            except HistoricoInvalido as exc:
                return Response({"codigo": str(exc), "requestId": request_id}, status=400, headers=headers)
            except Exception:
                return Response({"codigo": "ERROR_TECNICO", "requestId": request_id}, status=500, headers=headers)
        from .historico_solicitud import descriptor_historico
        return Response(descriptor_historico(), headers={"Cache-Control": "no-store"})


class CredihoyPrecargaThrottle(UserRateThrottle):
    scope = "credihoy_precarga"
    rate = "10/min"


class CredihoyPrecargaView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]
    throttle_classes = [CredihoyPrecargaThrottle]

    def initial(self, request, *args, **kwargs):
        if getattr(settings, "CREDIHOY_PRECARGA_ASISTIDA_ENABLED", False) is not True:
            raise Http404
        super().initial(request, *args, **kwargs)

    def _response(self, call):
        from .precarga import DatosPrecargaInvalidos
        from ..errores_http import ApiDataCrediHoyV2Error, CodigoErrorApiDataV2
        from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
        headers = {"Cache-Control": "no-store"}
        try:
            return Response(call(), headers=headers)
        except OperacionError as e:
            return Response({"codigo": e.codigo}, status=409, headers=headers)
        except ApiDataCrediHoyV2Error as e:
            status = 404 if e.codigo == CodigoErrorApiDataV2.ASOCIADO_NO_DISPONIBLE else 503
            return Response({"codigo": "ASOCIADO_NO_DISPONIBLE" if status == 404 else "PRECARGA_NO_DISPONIBLE"},
                            status=status, headers=headers)
        except DatosPrecargaInvalidos as e:
            return Response({"codigo": "PRECARGA_DATOS_INVALIDOS", "campo": e.campo,
                "tipoRecibido": e.tipo}, status=400, headers=headers)
        except (ValueError, TypeError, KeyError):
            return Response({"codigo": "PRECARGA_DATOS_INVALIDOS"}, status=400, headers=headers)

    def post(self, request):
        from .precarga import precargar
        return self._response(lambda: precargar(request, request.data))

    def get(self, request):
        from .precarga import recuperar
        return self._response(lambda: recuperar(request)["resultado"])


class CredihoyTitularCreateView(APIView):
    """Misma ruta de captura, allowlist de firma sin exponer Consumo legacy."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def initial(self, request, *args, **kwargs):
        if (getattr(settings, "CREDIHOY_NUEVO_TITULAR_ENABLED", False) is not True
                or getattr(settings, "CREDIHOY_CONSENTIMIENTO_REAL_ENABLED", False) is not True):
            raise Http404
        super().initial(request, *args, **kwargs)

    def post(self, request):
        p = request.data
        if (type(p) is not dict or set(p) not in ({"modo", "titular"}, {"modo", "titular", "precargaReferencia", "datosConfirmados"})
                or p["modo"] != "CONSULTA_REAL_CONTROLADA" or type(p["titular"]) is not dict):
            return Response({"codigo": "REQUEST_INVALIDO"}, status=400)
        from .consentimiento_real import crear_para_titular
        if (getattr(settings, "CREDIHOY_PILOTO_ASISTIDO_ENABLED", False) is True
                and "precargaReferencia" not in p):
            return Response({"codigo": "VERIFICACION_PRESENCIAL_REQUERIDA"}, status=409)
        from .firma_http import respuesta_firma
        def crear():
            source = None
            if "precargaReferencia" in p:
                from .precarga import validar_para_creacion
                source = validar_para_creacion(request, p["titular"], p["precargaReferencia"], p["datosConfirmados"])
            return {"solicitudId": str(crear_para_titular(p["titular"], request.user, precarga=source).pk)}
        response = respuesta_firma(crear)
        response["Cache-Control"] = "no-store"
        return response


class CredihoyUnicoEvaluacionFuncionalView(APIView):
    """Expediente autorizado; lectura API explicita, sin transporte LINIX."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def initial(self, request, *args, **kwargs):
        if (getattr(settings, "CREDIHOY_FUNCIONAL_ENABLED", False) is not True
                or (request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}
                    and getattr(settings, "CREDIHOY_PILOTO_ASISTIDO_ENABLED", False) is not True)):
            raise Http404
        super().initial(request, *args, **kwargs)

    def get(self, request, solicitud_id):
        from .funcional import estado_funcional, ConflictoFuncional
        from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
        from django.core.exceptions import ObjectDoesNotExist
        try:
            result = estado_funcional(solicitud_id, request.user)
            if request.query_params.get('perspectiva') == 'asociado':
                result = {'versionContrato': 'credihoy-estado-asociado-v1', 'solicitudId': str(solicitud_id),
                    'soloLectura': True, 'asociado': result['estadoCredito']['asociado']}
            return Response(result, headers={"Cache-Control": "no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff"})
        except (OperacionError, ObjectDoesNotExist):
            raise Http404
        except ConflictoFuncional:
            return Response({"codigo": "OPERACION_FUNCIONAL_REQUIERE_CONCILIACION"}, status=409)
        except ValueError:
            return Response({"codigo": "CONSENTIMIENTO_FIRMADO_VERIFICABLE_REQUERIDO"}, status=409)
        except Exception:
            return Response({"codigo": "ERROR_TECNICO_FUNCIONAL_RECUPERAR_ESTADO"}, status=503)

    def post(self, request, solicitud_id):
        from .funcional import procesar_funcional, ConflictoFuncional
        from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
        from django.core.exceptions import ObjectDoesNotExist
        p = request.data
        if (type(p) is not dict or set(p) != {"accion", "datos"}
                or type(p["accion"]) is not str or p["accion"] not in {"guardar_datos", "evaluar", "consultar_condiciones"}):
            return Response({"codigo": "REQUEST_INVALIDO"}, status=400)
        try:
            request_id = str(UUID(request.headers.get("X-Request-ID", "")))
        except (ValueError, TypeError):
            return Response({"codigo": "REQUEST_ID_INVALIDO"}, status=400)
        try:
            if p["accion"] == "consultar_condiciones":
                if type(p["datos"]) is not dict or p["datos"] != {}:
                    return Response({"codigo": "REQUEST_INVALIDO"}, status=400)
                from .funcional import consultar_condiciones
                result = consultar_condiciones(solicitud_id, request.user,
                    clave=request.headers.get("Idempotency-Key"), request_id=request_id)
            else:
                result = procesar_funcional(solicitud_id, request.user, accion=p["accion"], payload=p["datos"],
                    clave=request.headers.get("Idempotency-Key"), request_id=request_id)
            return Response(result, headers={"Cache-Control": "no-store", "X-Request-ID": request_id})
        except (OperacionError, ObjectDoesNotExist):
            raise Http404
        except ConflictoFuncional:
            return Response({"codigo": "OPERACION_FUNCIONAL_REQUIERE_CONCILIACION"}, status=409)
        except ValueError:
            return Response({"codigo": "DATOS_O_CONSENTIMIENTO_FUNCIONAL_INVALIDOS"}, status=400)
        except Exception:
            return Response({"codigo": "ERROR_TECNICO_FUNCIONAL_RECUPERAR_ESTADO"}, status=503)
