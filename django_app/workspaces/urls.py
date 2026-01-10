"""
Workspace-scoped URLs - CRM and Listings for each workspace
"""
from django.urls import path, include
from . import views

app_name = 'workspaces'

urlpatterns = [
    # Workspace Dashboard
    path('', views.dashboard, name='dashboard'),
    
    # Workspace Settings
    path('settings/', views.settings, name='settings'),
    path('settings/members/', views.member_list, name='members'),
    path('settings/members/invite/', views.member_invite, name='member_invite'),
    path('settings/members/<uuid:member_id>/edit/', views.member_edit, name='member_edit'),
    path('settings/members/<uuid:member_id>/remove/', views.member_remove, name='member_remove'),
    path('settings/connections/', views.connection_list, name='connections'),
    path('settings/connections/add/', views.connection_add, name='connection_add'),
    path('settings/connections/<uuid:connection_id>/edit/', views.connection_edit, name='connection_edit'),
    path('settings/connections/<uuid:connection_id>/test/', views.connection_test, name='connection_test'),
    
    # CRM - Leads
    path('leads/', include('crm.urls_leads')),
    
    # CRM - Contacts
    path('contacts/', include('crm.urls_contacts')),
    
    # Listings
    path('listings/', include('listings.urls')),
    
    # Analytics
    path('analytics/', views.analytics, name='analytics'),
]
