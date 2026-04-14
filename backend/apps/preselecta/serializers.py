from rest_framework import serializers


class PreselectaResponseSerializer(serializers.Serializer):
    estado = serializers.CharField()
    preaprobado = serializers.BooleanField()
    score = serializers.IntegerField(required=False)
    mensaje = serializers.CharField()
