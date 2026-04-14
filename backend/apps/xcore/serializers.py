from rest_framework import serializers


class XcoreResponseSerializer(serializers.Serializer):
    estado = serializers.CharField()
    resultado = serializers.ChoiceField(choices=("APROBADO", "RECHAZADO", "REVISION"))
    mensaje = serializers.CharField()
    monto_aprobado = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    plazo_aprobado = serializers.IntegerField(required=False)
    tasa_interes = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    detalle = serializers.JSONField(required=False)
