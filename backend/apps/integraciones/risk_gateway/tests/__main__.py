"""Ejecuta management checks/tests con bloqueo global de red, incluso en discovery."""

from contextlib import ExitStack
import os
import sys
from unittest.mock import patch


def main():
    os.environ["DJANGO_SETTINGS_MODULE"] = "apps.integraciones.risk_gateway.tests.settings"
    from django.core.management import execute_from_command_line

    with ExitStack() as stack:
        for target in ("requests.sessions.Session.send", "socket.socket", "socket.getaddrinfo",
                       "socket.create_connection", "http.client.HTTPConnection.connect",
                       "oracledb.connect"):
            stack.enter_context(patch(target, side_effect=AssertionError("RED PROHIBIDA: runner offline")))
        argumentos = sys.argv[1:] or ["test", "apps.integraciones.risk_gateway.tests", "--noinput"]
        execute_from_command_line(["manage.py", *argumentos])


if __name__ == "__main__":
    main()
