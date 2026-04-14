from rest_framework import serializers

from .models import DocumentoLegal


class DocumentoLegalSerializer(serializers.ModelSerializer):
    pdf_url = serializers.SerializerMethodField()

    class Meta:
        model = DocumentoLegal
        fields = (
            "id",
            "codigo",
            "tipo_documento",
            "titulo",
            "descripcion",
            "version",
            "orden",
            "pdf_url",
        )

    def get_pdf_url(self, obj):
        request = self.context.get("request")
        relative = f"/api/v1/documentos/{obj.codigo}/pdf/"
        return request.build_absolute_uri(relative) if request else relative
