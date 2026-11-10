from django.urls import path
from .tasas_http import TasasCredihoyView

urlpatterns=[path('api/v1/credihoy/administracion/tasas/',TasasCredihoyView.as_view(),name='credihoy-tasas')]
