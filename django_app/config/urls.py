"""
URL configuration for Main Application (workspace CRM)
"""
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Public pages (landing, login, register)
    path('', include('core.urls')),
    
    # Workspace-scoped routes
    path('<slug:workspace_slug>/', include('workspaces.urls')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
