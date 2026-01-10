"""
CRM - Contacts URLs
"""
from django.urls import path
from . import views_contacts

app_name = 'contacts'

urlpatterns = [
    path('', views_contacts.contact_list, name='list'),
    path('create/', views_contacts.contact_create, name='create'),
    path('<uuid:contact_id>/', views_contacts.contact_detail, name='detail'),
    path('<uuid:contact_id>/edit/', views_contacts.contact_edit, name='edit'),
    path('<uuid:contact_id>/delete/', views_contacts.contact_delete, name='delete'),
]
