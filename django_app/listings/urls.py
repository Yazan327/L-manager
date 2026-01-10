"""
Listings URLs
"""
from django.urls import path
from . import views

app_name = 'listings'

urlpatterns = [
    path('', views.listing_list, name='list'),
    path('create/', views.listing_create, name='create'),
    path('<uuid:listing_id>/', views.listing_detail, name='detail'),
    path('<uuid:listing_id>/edit/', views.listing_edit, name='edit'),
    path('<uuid:listing_id>/delete/', views.listing_delete, name='delete'),
    path('<uuid:listing_id>/publish/', views.listing_publish, name='publish'),
    path('<uuid:listing_id>/unpublish/', views.listing_unpublish, name='unpublish'),
    path('<uuid:listing_id>/images/', views.listing_images, name='images'),
    
    # Folders
    path('folders/', views.folder_list, name='folders'),
    path('folders/create/', views.folder_create, name='folder_create'),
    path('folders/<uuid:folder_id>/edit/', views.folder_edit, name='folder_edit'),
    path('folders/<uuid:folder_id>/delete/', views.folder_delete, name='folder_delete'),
    
    # Bulk operations
    path('bulk/', views.bulk_upload, name='bulk_upload'),
    
    # Loop configs
    path('loops/', views.loop_list, name='loops'),
    path('loops/create/', views.loop_create, name='loop_create'),
    path('loops/<uuid:loop_id>/edit/', views.loop_edit, name='loop_edit'),
    
    # API
    path('api/sync/', views.api_sync_listings, name='api_sync'),
]
