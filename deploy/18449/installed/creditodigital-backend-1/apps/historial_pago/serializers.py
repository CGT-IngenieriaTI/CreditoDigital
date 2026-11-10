from rest_framework import serializers


class HistorialPagoResponseSerializer(serializers.Serializer):
    estado = serializers.CharField()
    score_pago = serializers.IntegerField()
    mora_maxima = serializers.IntegerField()
    categoria = serializers.CharField()
    resumen = serializers.CharField()
