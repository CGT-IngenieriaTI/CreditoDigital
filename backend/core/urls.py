from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from .views import CsrfTokenView, HealthcheckView

admin.site.site_header = "Congente Credito Digital"
admin.site.site_title = "Congente Admin"
admin.site.index_title = "Panel operativo"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", HealthcheckView.as_view(), name="health"),
    path("api/v1/csrf/", CsrfTokenView.as_view(), name="csrf-token"),
    path("api/v1/auth/", include("apps.usuarios.urls")),
    path("api/v1/solicitudes/", include("apps.solicitudes.urls")),
    path("api/v1/consumo/", include("apps.xcore_consumo.urls")),
    path("api/v1/documentos/", include("apps.documentos.urls")),
    path("api/v1/decisiones/", include("apps.decisiones.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
