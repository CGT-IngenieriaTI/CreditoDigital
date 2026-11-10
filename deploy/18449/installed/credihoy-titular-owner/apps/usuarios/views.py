from django.contrib.auth import login, logout
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import AuthUserSerializer, LoginSerializer


class AuthLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        from django.conf import settings
        permitido = getattr(settings, "CREDIHOY_FIRMA_OPERADOR_USERNAME", None)
        if permitido is not None and (user.username != permitido or not user.is_staff):
            return Response({"detail": "Acceso no autorizado."}, status=status.HTTP_403_FORBIDDEN)
        login(request, user)
        return Response({"user": AuthUserSerializer(user).data}, status=status.HTTP_200_OK)


class AuthLogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuthMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"user": AuthUserSerializer(request.user).data})
