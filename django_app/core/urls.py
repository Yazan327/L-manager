"""
Core app URLs - Public pages and authentication
"""
from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Landing/Home
    path('', views.home, name='home'),
    
    # Authentication
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
    
    # Password reset
    path('password-reset/', views.password_reset_request, name='password_reset'),
    path('password-reset/<uidb64>/<token>/', views.password_reset_confirm, name='password_reset_confirm'),
    
    # Workspace selection (after login)
    path('workspaces/', views.workspace_list, name='workspace_list'),
    path('workspaces/create/', views.workspace_create, name='workspace_create'),
    
    # Accept invitation
    path('invite/<str:token>/', views.accept_invitation, name='accept_invitation'),
]
