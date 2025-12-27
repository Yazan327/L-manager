"""
Platform Model

Integration platforms: PropertyFinder, Bayut, Website.
Tracks listing status on each platform.
"""
from datetime import datetime
from typing import Optional
import enum
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, Enum, UniqueConstraint
from sqlalchemy.orm import relationship

from v2.core.database import Base


class PlatformType(str, enum.Enum):
    """Platform type"""
    PROPERTYFINDER = "propertyfinder"
    BAYUT = "bayut"
    WEBSITE = "website"


class Platform(Base):
    """Integration platform configuration"""
    __tablename__ = 'platforms'
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)  # PlatformType
    display_name = Column(String(100))
    
    # API Configuration
    api_base_url = Column(String(500))
    api_key = Column(String(500))
    api_secret = Column(String(500))
    
    # Status
    is_enabled = Column(Boolean, default=True)
    is_connected = Column(Boolean, default=False)
    last_sync = Column(DateTime)
    last_error = Column(Text)
    
    # Settings
    auto_publish = Column(Boolean, default=False)  # Auto-publish new listings
    sync_interval_minutes = Column(Integer, default=30)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    listings = relationship("PlatformListing", back_populates="platform", cascade="all, delete-orphan")
    
    def to_dict(self) -> dict:
        """Convert to dictionary (without secrets)"""
        return {
            'id': self.id,
            'name': self.name,
            'display_name': self.display_name,
            'api_base_url': self.api_base_url,
            'is_enabled': self.is_enabled,
            'is_connected': self.is_connected,
            'auto_publish': self.auto_publish,
            'sync_interval_minutes': self.sync_interval_minutes,
            'last_sync': self.last_sync.isoformat() if self.last_sync else None,
            'last_error': self.last_error,
            'listings_count': len(self.listings) if self.listings else 0
        }


class PlatformListingStatus(str, enum.Enum):
    """Listing status on a platform"""
    PENDING = "pending"  # Waiting to be published
    PUBLISHING = "publishing"  # Currently publishing
    PUBLISHED = "published"  # Successfully published
    FAILED = "failed"  # Failed to publish
    UNPUBLISHED = "unpublished"  # Removed from platform


class PlatformListing(Base):
    """Tracks a listing's status on each platform"""
    __tablename__ = 'platform_listings'
    
    id = Column(Integer, primary_key=True, index=True)
    
    listing_id = Column(Integer, ForeignKey('listings_v2.id'), nullable=False)
    platform_id = Column(Integer, ForeignKey('platforms.id'), nullable=False)
    
    # External reference
    external_id = Column(String(100))  # ID on the external platform
    external_url = Column(String(500))  # URL on the external platform
    
    # Status
    status = Column(String(20), default='pending')  # PlatformListingStatus
    last_error = Column(Text)
    
    # Sync tracking
    last_synced_at = Column(DateTime)
    last_published_at = Column(DateTime)
    
    # Analytics from platform
    views = Column(Integer, default=0)
    leads_count = Column(Integer, default=0)
    quality_score = Column(Integer)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Unique constraint: one entry per listing per platform
    __table_args__ = (
        UniqueConstraint('listing_id', 'platform_id', name='unique_listing_platform'),
    )
    
    # Relationships
    listing = relationship("Listing", back_populates="platform_listings")
    platform = relationship("Platform", back_populates="listings")
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'listing_id': self.listing_id,
            'listing_reference': self.listing.reference if self.listing else None,
            'platform_id': self.platform_id,
            'platform_name': self.platform.name if self.platform else None,
            'external_id': self.external_id,
            'external_url': self.external_url,
            'status': self.status,
            'last_error': self.last_error,
            'views': self.views,
            'leads_count': self.leads_count,
            'quality_score': self.quality_score,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'last_published_at': self.last_published_at.isoformat() if self.last_published_at else None,
        }
