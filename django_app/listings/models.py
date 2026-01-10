"""
Listings models - Property listings management
"""
from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class ListingFolder(models.Model):
    """
    Folders for organizing listings within a workspace
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    workspace = models.ForeignKey(
        'workspaces.Workspace',
        on_delete=models.CASCADE,
        related_name='listing_folders'
    )
    
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=20, default='#3B82F6')  # Hex color
    icon = models.CharField(max_length=50, blank=True)
    
    # Hierarchy
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children'
    )
    
    order = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'listing_folders'
        ordering = ['order', 'name']
        unique_together = ['workspace', 'name', 'parent']
    
    def __str__(self):
        return self.name


class Listing(models.Model):
    """
    A property listing that can be published to PropertyFinder and other platforms
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Workspace scope
    workspace = models.ForeignKey(
        'workspaces.Workspace',
        on_delete=models.CASCADE,
        related_name='listings'
    )
    
    # Organization
    folder = models.ForeignKey(
        ListingFolder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='listings'
    )
    
    # External references
    external_id = models.CharField(max_length=100, blank=True)  # PropertyFinder listing ID
    reference_number = models.CharField(max_length=100)  # Internal reference
    
    # Property type
    PROPERTY_TYPE_CHOICES = [
        ('apartment', 'Apartment'),
        ('villa', 'Villa'),
        ('townhouse', 'Townhouse'),
        ('penthouse', 'Penthouse'),
        ('duplex', 'Duplex'),
        ('studio', 'Studio'),
        ('office', 'Office'),
        ('shop', 'Shop'),
        ('warehouse', 'Warehouse'),
        ('land', 'Land'),
        ('building', 'Building'),
        ('hotel_apartment', 'Hotel Apartment'),
        ('other', 'Other'),
    ]
    property_type = models.CharField(max_length=50, choices=PROPERTY_TYPE_CHOICES)
    
    # Offering type
    OFFERING_TYPE_CHOICES = [
        ('sale', 'For Sale'),
        ('rent', 'For Rent'),
    ]
    offering_type = models.CharField(max_length=10, choices=OFFERING_TYPE_CHOICES)
    
    # Basic details
    title = models.CharField(max_length=200)
    title_ar = models.CharField(max_length=200, blank=True)  # Arabic title
    description = models.TextField()
    description_ar = models.TextField(blank=True)  # Arabic description
    
    # Location
    location_id = models.CharField(max_length=100, blank=True)  # PropertyFinder location ID
    location_name = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    community = models.CharField(max_length=200, blank=True)
    sub_community = models.CharField(max_length=200, blank=True)
    building_name = models.CharField(max_length=200, blank=True)
    
    # Coordinates
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    
    # Pricing
    price = models.DecimalField(max_digits=15, decimal_places=2)
    price_currency = models.CharField(max_length=3, default='AED')
    price_period = models.CharField(max_length=20, blank=True)  # For rent: yearly, monthly
    
    # Property specifications
    bedrooms = models.IntegerField(null=True, blank=True)
    bathrooms = models.IntegerField(null=True, blank=True)
    size_sqft = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    plot_size_sqft = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
    # Additional details
    furnished = models.CharField(max_length=20, blank=True)  # furnished, unfurnished, semi-furnished
    parking_spaces = models.IntegerField(null=True, blank=True)
    floor_number = models.IntegerField(null=True, blank=True)
    total_floors = models.IntegerField(null=True, blank=True)
    year_built = models.IntegerField(null=True, blank=True)
    
    # Amenities (stored as JSON array)
    amenities = models.JSONField(default=list, blank=True)
    
    # Completion status (for off-plan)
    COMPLETION_STATUS_CHOICES = [
        ('ready', 'Ready'),
        ('off_plan', 'Off Plan'),
        ('under_construction', 'Under Construction'),
    ]
    completion_status = models.CharField(max_length=20, choices=COMPLETION_STATUS_CHOICES, default='ready')
    handover_date = models.DateField(null=True, blank=True)
    
    # Ownership & Agent
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='listings'
    )
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='agent_listings'
    )
    
    # Compliance (UAE specific)
    permit_number = models.CharField(max_length=100, blank=True)  # DLD/ADREC permit
    rera_number = models.CharField(max_length=100, blank=True)
    broker_orn = models.CharField(max_length=100, blank=True)
    agent_brn = models.CharField(max_length=100, blank=True)
    
    # Publication status
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending', 'Pending Review'),
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('expired', 'Expired'),
        ('sold', 'Sold'),
        ('rented', 'Rented'),
        ('archived', 'Archived'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # Publication tracking
    published_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_published_to = models.JSONField(default=dict, blank=True)  # Platform: timestamp
    
    # Statistics
    views_count = models.IntegerField(default=0)
    leads_count = models.IntegerField(default=0)
    favorites_count = models.IntegerField(default=0)
    
    # Featured/Premium
    is_featured = models.BooleanField(default=False)
    featured_until = models.DateTimeField(null=True, blank=True)
    
    # Metadata
    metadata = models.JSONField(default=dict, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'listings'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workspace', 'status']),
            models.Index(fields=['workspace', 'property_type']),
            models.Index(fields=['workspace', 'offering_type']),
            models.Index(fields=['reference_number']),
            models.Index(fields=['external_id']),
        ]
        unique_together = ['workspace', 'reference_number']
    
    def __str__(self):
        return f"{self.reference_number} - {self.title}"
    
    @property
    def is_published(self):
        return self.status == 'active' and self.published_at is not None


class ListingImage(models.Model):
    """
    Images associated with a listing
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name='images'
    )
    
    # Image storage
    image = models.ImageField(upload_to='listing_images/')
    url = models.URLField(blank=True)  # External URL if not stored locally
    
    # Metadata
    title = models.CharField(max_length=200, blank=True)
    alt_text = models.CharField(max_length=200, blank=True)
    
    # Ordering
    order = models.IntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    
    # Image type
    IMAGE_TYPE_CHOICES = [
        ('photo', 'Photo'),
        ('floor_plan', 'Floor Plan'),
        ('video_thumbnail', 'Video Thumbnail'),
    ]
    image_type = models.CharField(max_length=20, choices=IMAGE_TYPE_CHOICES, default='photo')
    
    created_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'listing_images'
        ordering = ['order', 'created_at']
    
    def __str__(self):
        return f"Image for {self.listing.reference_number}"


class ListingVideo(models.Model):
    """
    Videos associated with a listing
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name='videos'
    )
    
    url = models.URLField()  # YouTube, Vimeo, etc.
    title = models.CharField(max_length=200, blank=True)
    
    # Type
    VIDEO_TYPE_CHOICES = [
        ('walkthrough', 'Walkthrough'),
        ('aerial', 'Aerial'),
        ('promotional', 'Promotional'),
    ]
    video_type = models.CharField(max_length=20, choices=VIDEO_TYPE_CHOICES, default='walkthrough')
    
    order = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'listing_videos'
        ordering = ['order']
    
    def __str__(self):
        return f"Video for {self.listing.reference_number}"


class ListingHistory(models.Model):
    """
    Track changes to listings for audit purposes
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name='history'
    )
    
    # Who made the change
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    
    # What changed
    action = models.CharField(max_length=50)  # created, updated, published, unpublished, etc.
    field_name = models.CharField(max_length=100, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    
    created_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'listing_history'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.action} on {self.listing.reference_number}"


class LoopConfig(models.Model):
    """
    Configuration for listing loops (auto-refresh schedules)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    workspace = models.ForeignKey(
        'workspaces.Workspace',
        on_delete=models.CASCADE,
        related_name='loop_configs'
    )
    
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    
    # Target listings (filter criteria)
    filter_criteria = models.JSONField(default=dict)  # e.g., {"status": "active", "property_type": "apartment"}
    
    # Schedule
    is_active = models.BooleanField(default=True)
    interval_hours = models.IntegerField(default=24)  # How often to refresh
    
    # Actions
    ACTION_CHOICES = [
        ('refresh', 'Refresh Listing'),
        ('republish', 'Republish'),
        ('boost', 'Boost'),
    ]
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, default='refresh')
    
    # Execution tracking
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'loop_configs'
    
    def __str__(self):
        return self.name
