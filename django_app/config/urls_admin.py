"""
URL configuration for Admin Portal (admin.domain.com)
"""
from django.urls import path, include
from django.contrib import admin as django_admin

urlpatterns = [
    # Django Admin (system admin only)
    path('django-admin/', django_admin.site.urls),
    
    # Custom Admin Portal
    path('', include('core.urls_admin')),
]
