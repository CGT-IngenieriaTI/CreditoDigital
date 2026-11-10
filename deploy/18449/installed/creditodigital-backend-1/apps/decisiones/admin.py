from django.contrib import admin

from .models import DecisionFinal


@admin.register(DecisionFinal)
class DecisionFinalAdmin(admin.ModelAdmin):
    list_display = ("solicitud", "resultado", "monto_aprobado", "created_at")
    list_filter = ("resultado", "created_at")
    search_fields = ("solicitud__numero_solicitud", "mensaje")

# Register your models here.
