"""
Workspace models - Multi-tenant workspace management
"""
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
import uuid


class WorkspaceRole(models.Model):
    """
    Predefined roles for workspace members with associated permissions
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50)  # e.g., 'admin', 'manager', 'agent', 'viewer'
    display_name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    
    # Permission flags (Bitrix24-style)
    can_manage_workspace = models.BooleanField(default=False)
    can_manage_users = models.BooleanField(default=False)
    can_manage_roles = models.BooleanField(default=False)
    can_manage_connections = models.BooleanField(default=False)
    
    # CRM permissions
    can_view_all_leads = models.BooleanField(default=False)
    can_create_leads = models.BooleanField(default=False)
    can_edit_leads = models.BooleanField(default=False)
    can_delete_leads = models.BooleanField(default=False)
    can_assign_leads = models.BooleanField(default=False)
    
    # Listing permissions
    can_view_all_listings = models.BooleanField(default=False)
    can_create_listings = models.BooleanField(default=False)
    can_edit_listings = models.BooleanField(default=False)
    can_delete_listings = models.BooleanField(default=False)
    can_publish_listings = models.BooleanField(default=False)
    
    # Analytics permissions
    can_view_analytics = models.BooleanField(default=False)
    can_export_data = models.BooleanField(default=False)
    
    # Is this a system-defined role or custom?
    is_system_role = models.BooleanField(default=False)
    
    # Hierarchy level (lower = more permissions)
    hierarchy_level = models.IntegerField(default=100)
    
    class Meta:
        db_table = 'workspace_roles'
        ordering = ['hierarchy_level', 'name']
    
    def __str__(self):
        return self.display_name


class Workspace(models.Model):
    """
    A workspace is a tenant in the multi-tenant system.
    Each workspace has its own users, listings, leads, and settings.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Basic info
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to='workspace_logos/', null=True, blank=True)
    
    # Owner (the user who created the workspace)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='owned_workspaces'
    )
    
    # Status
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    
    # Subscription/Plan (for future monetization)
    plan = models.CharField(max_length=50, default='free')  # free, starter, professional, enterprise
    plan_expires_at = models.DateTimeField(null=True, blank=True)
    
    # Limits
    max_users = models.IntegerField(default=5)
    max_listings = models.IntegerField(default=100)
    max_leads = models.IntegerField(default=1000)
    
    # Settings (JSON)
    settings = models.JSONField(default=dict, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'workspaces'
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)
    
    @property
    def member_count(self):
        return self.members.count()
    
    @property
    def listing_count(self):
        return self.listings.count()
    
    @property
    def lead_count(self):
        return self.leads.count()
    
    def get_active_members(self):
        return self.members.filter(is_active=True, user__is_active=True)
    
    def get_admins(self):
        return self.members.filter(role__can_manage_workspace=True)


class WorkspaceMember(models.Model):
    """
    Membership linking users to workspaces with roles
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='members'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workspace_memberships'
    )
    role = models.ForeignKey(
        WorkspaceRole,
        on_delete=models.PROTECT,
        related_name='members'
    )
    
    # Status
    is_active = models.BooleanField(default=True)
    
    # Invitation tracking
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_invitations'
    )
    invited_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(default=timezone.now)
    
    # Custom permissions (override role permissions)
    custom_permissions = models.JSONField(default=dict, blank=True)
    
    class Meta:
        db_table = 'workspace_members'
        unique_together = ['workspace', 'user']
        ordering = ['joined_at']
    
    def __str__(self):
        return f"{self.user.email} in {self.workspace.name}"
    
    def has_permission(self, permission):
        """Check if member has a specific permission"""
        # Check custom permissions first
        if permission in self.custom_permissions:
            return self.custom_permissions[permission]
        # Fall back to role permissions
        return getattr(self.role, permission, False)


class WorkspaceInvitation(models.Model):
    """
    Pending invitations to join a workspace
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='invitations'
    )
    email = models.EmailField()
    role = models.ForeignKey(
        WorkspaceRole,
        on_delete=models.PROTECT
    )
    
    # Invitation details
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    token = models.CharField(max_length=100, unique=True)
    message = models.TextField(blank=True)
    
    # Status
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('expired', 'Expired'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'workspace_invitations'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Invitation for {self.email} to {self.workspace.name}"
    
    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


class WorkspaceConnection(models.Model):
    """
    External API connections for a workspace (e.g., PropertyFinder, Bayut)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='connections'
    )
    
    # Connection type
    PROVIDER_CHOICES = [
        ('propertyfinder', 'PropertyFinder'),
        ('bayut', 'Bayut'),
        ('dubizzle', 'Dubizzle'),
        ('custom', 'Custom API'),
    ]
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    name = models.CharField(max_length=100)  # User-friendly name
    
    # Credentials (encrypted in production)
    api_key = models.CharField(max_length=500, blank=True)
    api_secret = models.CharField(max_length=500, blank=True)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    
    # Additional settings
    base_url = models.URLField(blank=True)
    settings = models.JSONField(default=dict, blank=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'workspace_connections'
        unique_together = ['workspace', 'provider', 'name']
    
    def __str__(self):
        return f"{self.name} ({self.provider}) - {self.workspace.name}"
    
    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return True
        return timezone.now() > self.token_expires_at


class WorkspaceWebhook(models.Model):
    """
    Webhooks configured for a workspace
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='webhooks'
    )
    
    name = models.CharField(max_length=100)
    url = models.URLField()
    secret = models.CharField(max_length=200, blank=True)
    
    # Events to trigger
    EVENT_CHOICES = [
        ('lead.created', 'Lead Created'),
        ('lead.updated', 'Lead Updated'),
        ('lead.assigned', 'Lead Assigned'),
        ('listing.created', 'Listing Created'),
        ('listing.published', 'Listing Published'),
        ('listing.unpublished', 'Listing Unpublished'),
    ]
    events = models.JSONField(default=list)  # List of event names
    
    # Status
    is_active = models.BooleanField(default=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    failure_count = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'workspace_webhooks'
    
    def __str__(self):
        return f"{self.name} - {self.workspace.name}"
