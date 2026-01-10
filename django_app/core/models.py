"""
Core models - Custom User model and authentication
"""
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
import uuid


class UserManager(BaseUserManager):
    """Custom user manager for email-based authentication"""
    
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_system_admin', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """
    Custom user model with email as the primary identifier.
    Users can belong to multiple workspaces with different roles.
    """
    username = None  # Remove username field
    email = models.EmailField('email address', unique=True)
    
    # Profile fields
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    
    # System-level flags
    is_system_admin = models.BooleanField(
        default=False,
        help_text='Designates whether this user has access to the admin portal'
    )
    
    # Timestamps
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    
    # Settings
    timezone = models.CharField(max_length=50, default='UTC')
    language = models.CharField(max_length=10, default='en')
    
    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    
    class Meta:
        db_table = 'users'
        verbose_name = 'user'
        verbose_name_plural = 'users'
    
    def __str__(self):
        return self.email
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email
    
    def get_workspaces(self):
        """Get all workspaces this user is a member of"""
        from workspaces.models import Workspace
        return Workspace.objects.filter(members__user=self)
    
    def get_role_in_workspace(self, workspace):
        """Get user's role in a specific workspace"""
        from workspaces.models import WorkspaceMember
        try:
            membership = WorkspaceMember.objects.get(user=self, workspace=workspace)
            return membership.role
        except WorkspaceMember.DoesNotExist:
            return None
    
    def has_workspace_permission(self, workspace, permission):
        """Check if user has a specific permission in a workspace"""
        from workspaces.models import WorkspaceMember
        try:
            membership = WorkspaceMember.objects.get(user=self, workspace=workspace)
            return membership.has_permission(permission)
        except WorkspaceMember.DoesNotExist:
            return False


class UserSession(models.Model):
    """
    Track user sessions for security and analytics
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    
    session_key = models.CharField(max_length=40)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    created_at = models.DateTimeField(default=timezone.now)
    last_activity = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'user_sessions'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Session for {self.user.email}"


class AuditLog(models.Model):
    """
    Track all significant actions in the system for auditing
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Who performed the action
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    
    # What action was performed
    action = models.CharField(max_length=100)  # e.g., 'create', 'update', 'delete', 'login'
    entity_type = models.CharField(max_length=100)  # e.g., 'listing', 'lead', 'user'
    entity_id = models.CharField(max_length=100, blank=True)
    
    # Where (which workspace)
    workspace = models.ForeignKey(
        'workspaces.Workspace', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='audit_logs'
    )
    
    # Details
    description = models.TextField(blank=True)
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    
    # Request info
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    created_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['workspace', 'created_at']),
            models.Index(fields=['entity_type', 'entity_id']),
        ]
    
    def __str__(self):
        return f"{self.action} {self.entity_type} by {self.user}"


class SystemSetting(models.Model):
    """
    System-wide settings (key-value store)
    """
    key = models.CharField(max_length=100, primary_key=True)
    value = models.JSONField()
    description = models.TextField(blank=True)
    
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='updated_settings'
    )
    
    class Meta:
        db_table = 'system_settings'
    
    def __str__(self):
        return self.key


class FeatureFlag(models.Model):
    """
    Feature flags for gradual rollout and A/B testing
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    
    # Global toggle
    is_enabled = models.BooleanField(default=False)
    
    # Percentage rollout (0-100)
    rollout_percentage = models.IntegerField(default=100)
    
    # Specific workspaces (if not globally enabled)
    enabled_workspaces = models.ManyToManyField(
        'workspaces.Workspace',
        blank=True,
        related_name='enabled_features'
    )
    
    # Metadata
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'feature_flags'
    
    def __str__(self):
        return self.name
    
    def is_enabled_for_workspace(self, workspace):
        """Check if this feature is enabled for a specific workspace"""
        if self.is_enabled:
            return True
        return self.enabled_workspaces.filter(pk=workspace.pk).exists()
