from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.usuarios.models import PerfilAsesor, RolAsesor


class AuthViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="asesor.auth",
            password="ClaveSegura123*",
            first_name="Ana",
            last_name="Asesora",
            email="ana@congente.test",
        )
        PerfilAsesor.objects.create(usuario=self.user, rol=RolAsesor.SUPERVISOR)

    def test_login_me_and_logout_flow(self):
        login_response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "asesor.auth", "password": "ClaveSegura123*"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(login_response.json()["user"]["role"], RolAsesor.SUPERVISOR)

        me_response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["user"]["username"], "asesor.auth")

        logout_response = self.client.post("/api/v1/auth/logout/", format="json")
        self.assertEqual(logout_response.status_code, 204)

        me_after_logout = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_after_logout.status_code, 403)
