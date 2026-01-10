"""
Listings app admin configuration
"""
from django.contrib import admin
from .models import Listing, ListingFolder, ListingImage, ListingVideo, ListingHistory, LoopConfig


@admin.register(ListingFolder)
class ListingFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'workspace', 'order')
    list_filter = ('workspace',)
    search_fields = ('name',)


class ListingImageInline(admin.TabularInline):
    model = ListingImage
    extra = 0


class ListingVideoInline(admin.TabularInline):
    model = ListingVideo
    extra = 0


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ('reference_number', 'title', 'workspace', 'property_type', 'offering_type', 'price', 'status')
    list_filter = ('status', 'property_type', 'offering_type', 'workspace')
    search_fields = ('reference_number', 'title', 'location_name', 'community')
    inlines = [ListingImageInline, ListingVideoInline]
    date_hierarchy = 'created_at'


@admin.register(ListingImage)
class ListingImageAdmin(admin.ModelAdmin):
    list_display = ('listing', 'image_type', 'is_primary', 'order')
    list_filter = ('image_type', 'is_primary')


@admin.register(ListingVideo)
class ListingVideoAdmin(admin.ModelAdmin):
    list_display = ('listing', 'video_type', 'title')
    list_filter = ('video_type',)


@admin.register(ListingHistory)
class ListingHistoryAdmin(admin.ModelAdmin):
    list_display = ('listing', 'action', 'user', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('listing__reference_number',)
    date_hierarchy = 'created_at'


@admin.register(LoopConfig)
class LoopConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'workspace', 'action', 'interval_hours', 'is_active', 'last_run_at')
    list_filter = ('is_active', 'action')
    search_fields = ('name',)
