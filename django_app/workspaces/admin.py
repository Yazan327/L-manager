"""
Workspaces app admin configuration
"""
from django.contrib import admin
from .models import (
    Workspace, WorkspaceMember, WorkspaceRole, 
    WorkspaceInvitation, WorkspaceConnection, WorkspaceWebhook
)


@admin.register(WorkspaceRole)
class WorkspaceRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'display_name', 'hierarchy_level', 'is_system_role')
    list_filter = ('is_system_role',)


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'owner', 'plan', 'is_active', 'created_at')
    list_filter = ('is_active', 'plan', 'created_at')
    search_fields = ('name', 'slug', 'owner__email')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(WorkspaceMember)
class WorkspaceMemberAdmin(admin.ModelAdmin):
    list_display = ('user', 'workspace', 'role', 'is_active', 'joined_at')
    list_filter = ('is_active', 'role')
    search_fields = ('user__email', 'workspace__name')


@admin.register(WorkspaceInvitation)
class WorkspaceInvitationAdmin(admin.ModelAdmin):
    list_display = ('email', 'workspace', 'role', 'status', 'created_at', 'expires_at')
    list_filter = ('status', 'created_at')
    search_fields = ('email', 'workspace__name')


@admin.register(WorkspaceConnection)
class WorkspaceConnectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'workspace', 'provider', 'is_active', 'last_sync_at')
    list_filter = ('provider', 'is_active')
    search_fields = ('name', 'workspace__name')


@admin.register(WorkspaceWebhook)
class WorkspaceWebhookAdmin(admin.ModelAdmin):
    list_display = ('name', 'workspace', 'url', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'workspace__name')
