"""
Core app Admin Portal URLs
"""
from django.urls import path
from . import views_admin

app_name = 'admin_portal'

urlpatterns = [
    # Admin Portal Home
    path('', views_admin.dashboard, name='dashboard'),
    
    # Admin Authentication
    path('login/', views_admin.login_view, name='login'),
    path('logout/', views_admin.logout_view, name='logout'),
    
    # Workspace Management
    path('workspaces/', views_admin.workspace_list, name='workspaces'),
    path('workspaces/create/', views_admin.workspace_create, name='workspace_create'),
    path('workspaces/<uuid:workspace_id>/', views_admin.workspace_detail, name='workspace_detail'),
    path('workspaces/<uuid:workspace_id>/edit/', views_admin.workspace_edit, name='workspace_edit'),
    path('workspaces/<uuid:workspace_id>/delete/', views_admin.workspace_delete, name='workspace_delete'),
    
    # User Management
    path('users/', views_admin.user_list, name='users'),
    path('users/create/', views_admin.user_create, name='user_create'),
    path('users/<int:user_id>/', views_admin.user_detail, name='user_detail'),
    path('users/<int:user_id>/edit/', views_admin.user_edit, name='user_edit'),
    
    # System Settings
    path('settings/', views_admin.system_settings, name='settings'),
    path('settings/features/', views_admin.feature_flags, name='feature_flags'),
    
    # Audit Logs
    path('audit-logs/', views_admin.audit_logs, name='audit_logs'),
    
    # API Connections (global)
    path('connections/', views_admin.connection_list, name='connections'),
    
    # System Health/Stats
    path('health/', views_admin.system_health, name='health'),
]
