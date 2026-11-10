"""Wrappers del contrato OTP existente; sin pipeline/fixtures legacy."""
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from .consentimiento_real import aceptar, firmar, estado, accion_otp


def respuesta_firma(call):
    try:
        return Response(call(), headers={"Cache-Control": "no-store"})
    except ObjectDoesNotExist:
        return Response({"codigo": "EXPEDIENTE_NO_DISPONIBLE"}, status=404)
    except OperacionError as exc:
        return Response({"codigo": exc.codigo}, status=409)
    except Exception:
        return Response({"codigo": "CONSENTIMIENTO_OPERACION_NO_COMPLETADA"}, status=400)


class FirmaBase(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]


class CredihoyConsentView(FirmaBase):
    def get(self, request, solicitud_id):
        return respuesta_firma(lambda: estado(solicitud_id, request.user))

    def post(self, request, solicitud_id):
        class FirmaSerializer(serializers.Serializer):
            accion = serializers.ChoiceField(choices=["aceptar", "firmar"])
            aceptado = serializers.BooleanField(required=False)
            contacto_aceptado = serializers.BooleanField(required=False)
            canal = serializers.ChoiceField(choices=["SMS", "EMAIL"], required=False)
            destino = serializers.CharField(max_length=255, required=False, write_only=True)
        data = FirmaSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        def ejecutar():
            p = data.validated_data
            if p["accion"] == "aceptar":
                aceptar(solicitud_id, request.user, aceptado=p.get("aceptado"), contacto_aceptado=p.get("contacto_aceptado"),
                    canal=p.get("canal"), destino=p.get("destino", ""))
            else:
                if p.get("aceptado") is not True:
                    raise ValueError("ACEPTACION_EXPRESA_REQUERIDA")
                firmar(solicitud_id, request.user)
            return estado(solicitud_id, request.user)
        return respuesta_firma(ejecutar)


class CredihoyOtpSendView(FirmaBase):
    def post(self, request, solicitud_id):
        if not isinstance(request.data, dict) or request.data.get("confirmacion_envio") is not True:
            return Response({"codigo": "CONFIRMACION_ENVIO_OTP_REQUERIDA"}, status=409)
        class EnvioSerializer(serializers.Serializer):
            canal = serializers.ChoiceField(choices=["SMS", "EMAIL"], default="SMS")
        data = EnvioSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return respuesta_firma(lambda: accion_otp(solicitud_id, request.user,
            accion="enviar", canal=data.validated_data["canal"], request=request))


class CredihoyOtpVerifyView(FirmaBase):
    def post(self, request, solicitud_id):
        class VerificacionSerializer(serializers.Serializer):
            codigo = serializers.RegexField(r"^\d{4,8}$")
        data = VerificacionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return respuesta_firma(lambda: accion_otp(solicitud_id, request.user,
            accion="verificar", codigo=data.validated_data["codigo"], request=request))


class CredihoyOtpStatusView(FirmaBase):
    def get(self, request, solicitud_id):
        return respuesta_firma(lambda: estado(solicitud_id, request.user))
