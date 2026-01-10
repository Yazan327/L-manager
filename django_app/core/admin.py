"""
Core app admin configuration
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, UserSession, AuditLog, SystemSetting, FeatureFlag


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'is_system_admin', 'is_staff', 'is_active')
    list_filter = ('is_system_admin', 'is_staff', 'is_active', 'created_at')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone', 'avatar')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'is_system_admin', 'groups', 'user_permissions')}),
        ('Settings', {'fields': ('timezone', 'language')}),
        ('Important dates', {'fields': ('last_login', 'created_at')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'is_system_admin'),
        }),
    )
    
    readonly_fields = ('created_at',)


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'ip_address', 'is_active', 'created_at', 'last_activity')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__email', 'ip_address')
    readonly_fields = ('id', 'created_at')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'entity_type', 'user', 'workspace', 'created_at')
    list_filter = ('action', 'entity_type', 'created_at')
    search_fields = ('user__email', 'entity_id', 'description')
    readonly_fields = ('id', 'created_at')


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'updated_at', 'updated_by')
    search_fields = ('key', 'description')


@admin.register(FeatureFlag)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_enabled', 'rollout_percentage', 'updated_at')
    list_filter = ('is_enabled',)
    search_fields = ('name', 'description')
