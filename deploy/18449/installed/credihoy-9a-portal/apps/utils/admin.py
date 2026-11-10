from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event", "level", "actor", "solicitud")
    list_filter = ("level", "event", "created_at")
    search_fields = ("event", "actor", "request_id")
    readonly_fields = ("created_at", "updated_at", "payload")
