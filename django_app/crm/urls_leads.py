"""
CRM - Leads URLs
"""
from django.urls import path
from . import views_leads

app_name = 'leads'

urlpatterns = [
    path('', views_leads.lead_list, name='list'),
    path('create/', views_leads.lead_create, name='create'),
    path('<uuid:lead_id>/', views_leads.lead_detail, name='detail'),
    path('<uuid:lead_id>/edit/', views_leads.lead_edit, name='edit'),
    path('<uuid:lead_id>/delete/', views_leads.lead_delete, name='delete'),
    path('<uuid:lead_id>/assign/', views_leads.lead_assign, name='assign'),
    path('<uuid:lead_id>/comment/', views_leads.lead_add_comment, name='add_comment'),
    path('<uuid:lead_id>/status/', views_leads.lead_update_status, name='update_status'),
    
    # API endpoints for AJAX
    path('api/sync/', views_leads.api_sync_leads, name='api_sync'),
]
