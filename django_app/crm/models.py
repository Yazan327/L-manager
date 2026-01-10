"""
CRM models - Leads, Contacts, and related entities
"""
from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class Lead(models.Model):
    """
    A lead represents a potential customer inquiry from PropertyFinder or other sources
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Workspace scope
    workspace = models.ForeignKey(
        'workspaces.Workspace',
        on_delete=models.CASCADE,
        related_name='leads'
    )
    
    # External reference (from PropertyFinder API)
    external_id = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=50, default='propertyfinder')  # propertyfinder, bayut, website, manual
    
    # Contact information
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    country_code = models.CharField(max_length=10, blank=True)
    
    # Lead details
    message = models.TextField(blank=True)
    
    # Property interest
    listing = models.ForeignKey(
        'listings.Listing',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='leads'
    )
    property_reference = models.CharField(max_length=100, blank=True)
    
    # Status workflow
    STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('qualified', 'Qualified'),
        ('viewing', 'Viewing Scheduled'),
        ('negotiation', 'In Negotiation'),
        ('won', 'Won'),
        ('lost', 'Lost'),
        ('archived', 'Archived'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    
    # Priority
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    
    # Assignment
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_leads'
    )
    
    # Financial
    budget_min = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    budget_currency = models.CharField(max_length=3, default='AED')
    
    # Preferences
    preferred_locations = models.JSONField(default=list, blank=True)
    property_type_preference = models.CharField(max_length=100, blank=True)
    bedrooms_min = models.IntegerField(null=True, blank=True)
    bedrooms_max = models.IntegerField(null=True, blank=True)
    
    # Tracking
    source_url = models.URLField(blank=True)
    utm_source = models.CharField(max_length=100, blank=True)
    utm_medium = models.CharField(max_length=100, blank=True)
    utm_campaign = models.CharField(max_length=100, blank=True)
    
    # Timestamps
    received_at = models.DateTimeField(default=timezone.now)  # When lead was received
    first_contact_at = models.DateTimeField(null=True, blank=True)
    last_contact_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Metadata
    metadata = models.JSONField(default=dict, blank=True)  # Store additional API data
    
    class Meta:
        db_table = 'leads'
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['workspace', 'status']),
            models.Index(fields=['workspace', 'assigned_to']),
            models.Index(fields=['workspace', 'received_at']),
            models.Index(fields=['external_id']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.status}"
    
    @property
    def is_open(self):
        return self.status not in ['won', 'lost', 'archived']
    
    @property
    def response_time(self):
        if self.first_contact_at and self.received_at:
            return self.first_contact_at - self.received_at
        return None


class LeadComment(models.Model):
    """
    Comments/notes on a lead
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='lead_comments'
    )
    
    content = models.TextField()
    
    # For activity tracking
    COMMENT_TYPE_CHOICES = [
        ('note', 'Note'),
        ('call', 'Phone Call'),
        ('email', 'Email'),
        ('meeting', 'Meeting'),
        ('viewing', 'Property Viewing'),
        ('status_change', 'Status Change'),
    ]
    comment_type = models.CharField(max_length=20, choices=COMMENT_TYPE_CHOICES, default='note')
    
    # Mentions
    mentioned_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='lead_mentions'
    )
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'lead_comments'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Comment on {self.lead.name} by {self.author}"


class LeadTask(models.Model):
    """
    Tasks/follow-ups associated with a lead
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='tasks')
    
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    
    # Assignment
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='lead_tasks'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_lead_tasks'
    )
    
    # Scheduling
    due_date = models.DateTimeField(null=True, blank=True)
    reminder_at = models.DateTimeField(null=True, blank=True)
    
    # Status
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Priority
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'lead_tasks'
        ordering = ['due_date', '-created_at']
    
    def __str__(self):
        return self.title
    
    @property
    def is_overdue(self):
        if self.due_date and self.status == 'pending':
            return timezone.now() > self.due_date
        return False


class Contact(models.Model):
    """
    A contact is a person (potential or existing customer) stored in the CRM
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    workspace = models.ForeignKey(
        'workspaces.Workspace',
        on_delete=models.CASCADE,
        related_name='contacts'
    )
    
    # Basic info
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    secondary_phone = models.CharField(max_length=50, blank=True)
    
    # Company info
    company = models.CharField(max_length=200, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    
    # Address
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    
    # Classification
    CONTACT_TYPE_CHOICES = [
        ('buyer', 'Buyer'),
        ('seller', 'Seller'),
        ('tenant', 'Tenant'),
        ('landlord', 'Landlord'),
        ('investor', 'Investor'),
        ('agent', 'Agent'),
        ('other', 'Other'),
    ]
    contact_type = models.CharField(max_length=20, choices=CONTACT_TYPE_CHOICES, default='buyer')
    
    # Status
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('blocked', 'Blocked'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    
    # Ownership
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_contacts'
    )
    
    # Social
    linkedin_url = models.URLField(blank=True)
    
    # Notes
    notes = models.TextField(blank=True)
    
    # Tags
    tags = models.JSONField(default=list, blank=True)
    
    # Source tracking
    source = models.CharField(max_length=100, blank=True)  # How the contact was acquired
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'contacts'
        ordering = ['first_name', 'last_name']
        indexes = [
            models.Index(fields=['workspace', 'email']),
            models.Index(fields=['workspace', 'phone']),
        ]
    
    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_leads(self):
        """Get all leads associated with this contact's email or phone"""
        from django.db.models import Q
        return Lead.objects.filter(
            workspace=self.workspace
        ).filter(
            Q(email=self.email) | Q(phone=self.phone)
        )


class Customer(models.Model):
    """
    A customer represents a converted contact who has made a transaction
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    workspace = models.ForeignKey(
        'workspaces.Workspace',
        on_delete=models.CASCADE,
        related_name='customers'
    )
    
    contact = models.OneToOneField(
        Contact,
        on_delete=models.CASCADE,
        related_name='customer_profile'
    )
    
    # Customer info
    customer_number = models.CharField(max_length=50, blank=True)  # Internal reference
    
    # Classification
    CUSTOMER_TYPE_CHOICES = [
        ('individual', 'Individual'),
        ('company', 'Company'),
    ]
    customer_type = models.CharField(max_length=20, choices=CUSTOMER_TYPE_CHOICES, default='individual')
    
    # Financial
    lifetime_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='AED')
    
    # Status
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('vip', 'VIP'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    
    # Timestamps
    first_transaction_at = models.DateTimeField(null=True, blank=True)
    last_transaction_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'customers'
        ordering = ['-created_at']
    
    def __str__(self):
        return str(self.contact)
