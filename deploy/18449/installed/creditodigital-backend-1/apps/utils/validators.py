from datetime import date

from rest_framework import serializers


def validate_document_issue_date(value):
    if value > date.today():
        raise serializers.ValidationError("La fecha de expedicion no puede estar en el futuro.")
    return value


def validate_credit_policy(tipo_identificacion: str, fecha_expedicion):
    validate_document_issue_date(fecha_expedicion)
    if tipo_identificacion == "TI":
        raise serializers.ValidationError(
            "La linea de credito de consumo solo esta disponible para mayores de edad."
        )
    if (date.today() - fecha_expedicion).days < 30:
        raise serializers.ValidationError(
            "El documento debe tener al menos 30 dias de expedido para continuar."
        )
