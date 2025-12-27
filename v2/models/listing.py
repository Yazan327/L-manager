"""
Listing Model

Property listings with support for multi-platform publishing.
"""
from datetime import datetime
from typing import Optional, List
import json
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.orm import relationship
import enum

from v2.core.database import Base


class ListingStatus(str, enum.Enum):
    """Listing status"""
    DRAFT = "draft"
    PENDING = "pending"
    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    ARCHIVED = "archived"


class PropertyCategory(str, enum.Enum):
    """Property category"""
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"


class OfferingType(str, enum.Enum):
    """Offering type"""
    SALE = "sale"
    RENT = "rent"


class Listing(Base):
    """Property listing model"""
    __tablename__ = 'listings_v2'
    
    id = Column(Integer, primary_key=True, index=True)
    reference = Column(String(50), unique=True, nullable=False, index=True)
    
    # ==================== LOCATION ====================
    emirate = Column(String(50))
    city = Column(String(100))
    community = Column(String(200))
    sub_community = Column(String(200))
    building = Column(String(200))
    location_id = Column(Integer)  # PropertyFinder location ID
    
    # ==================== PROPERTY DETAILS ====================
    category = Column(String(20), default='residential')  # residential, commercial
    offering_type = Column(String(20), default='sale')  # sale, rent
    property_type = Column(String(10))  # AP, VH, TH, PH, OF, etc.
    
    # Specifications
    bedrooms = Column(String(10))
    bathrooms = Column(String(10))
    size = Column(Float)
    plot_size = Column(Float)
    furnishing = Column(String(20))  # furnished, unfurnished, semi-furnished
    completion_status = Column(String(20))  # ready, off_plan
    
    # Unit details
    floor_number = Column(String(20))
    unit_number = Column(String(50))
    parking_spaces = Column(Integer, default=0)
    
    # ==================== PRICE ====================
    price = Column(Float, nullable=False)
    price_on_application = Column(Boolean, default=False)
    rent_frequency = Column(String(20))  # yearly, monthly, weekly, daily
    service_charge = Column(Float)
    
    # ==================== DESCRIPTION ====================
    title_en = Column(String(200))
    title_ar = Column(String(200))
    description_en = Column(Text)
    description_ar = Column(Text)
    
    # ==================== MEDIA ====================
    images = Column(Text)  # JSON array of image URLs
    main_image = Column(String(500))
    video_url = Column(String(500))
    virtual_tour_url = Column(String(500))
    floor_plan_url = Column(String(500))
    
    # ==================== AMENITIES ====================
    amenities = Column(Text)  # JSON array of amenity codes
    
    # ==================== COMPLIANCE ====================
    permit_number = Column(String(100))  # DLD/ADREC permit
    rera_number = Column(String(100))
    developer = Column(String(200))
    project_name = Column(String(200))
    
    # ==================== ASSIGNMENT ====================
    agent_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    owner_name = Column(String(200))
    owner_phone = Column(String(50))
    owner_email = Column(String(120))
    
    # ==================== ANALYTICS ====================
    views = Column(Integer, default=0)
    leads_count = Column(Integer, default=0)
    quality_score = Column(Integer, default=0)
    
    # ==================== STATUS & METADATA ====================
    status = Column(String(20), default='draft')
    available_from = Column(DateTime)
    featured = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = Column(DateTime)
    
    # ==================== RELATIONSHIPS ====================
    agent = relationship("User", back_populates="listings", foreign_keys=[agent_id])
    platform_listings = relationship("PlatformListing", back_populates="listing", cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="listing")
    
    def get_images(self) -> List[str]:
        """Get images as list"""
        if not self.images:
            return []
        try:
            return json.loads(self.images)
        except:
            return []
    
    def set_images(self, images: List[str]):
        """Set images from list"""
        self.images = json.dumps(images)
    
    def get_amenities(self) -> List[str]:
        """Get amenities as list"""
        if not self.amenities:
            return []
        try:
            return json.loads(self.amenities)
        except:
            return []
    
    def set_amenities(self, amenities: List[str]):
        """Set amenities from list"""
        self.amenities = json.dumps(amenities)
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'reference': self.reference,
            
            # Location
            'emirate': self.emirate,
            'city': self.city,
            'community': self.community,
            'sub_community': self.sub_community,
            'building': self.building,
            'location_id': self.location_id,
            
            # Property
            'category': self.category,
            'offering_type': self.offering_type,
            'property_type': self.property_type,
            'bedrooms': self.bedrooms,
            'bathrooms': self.bathrooms,
            'size': self.size,
            'plot_size': self.plot_size,
            'furnishing': self.furnishing,
            'completion_status': self.completion_status,
            'floor_number': self.floor_number,
            'unit_number': self.unit_number,
            'parking_spaces': self.parking_spaces,
            
            # Price
            'price': self.price,
            'price_on_application': self.price_on_application,
            'rent_frequency': self.rent_frequency,
            'service_charge': self.service_charge,
            
            # Description
            'title_en': self.title_en,
            'title_ar': self.title_ar,
            'description_en': self.description_en,
            'description_ar': self.description_ar,
            
            # Media
            'images': self.get_images(),
            'main_image': self.main_image,
            'video_url': self.video_url,
            'virtual_tour_url': self.virtual_tour_url,
            'floor_plan_url': self.floor_plan_url,
            
            # Amenities
            'amenities': self.get_amenities(),
            
            # Compliance
            'permit_number': self.permit_number,
            'rera_number': self.rera_number,
            'developer': self.developer,
            'project_name': self.project_name,
            
            # Assignment
            'agent_id': self.agent_id,
            'agent_name': self.agent.name if self.agent else None,
            'owner_name': self.owner_name,
            'owner_phone': self.owner_phone,
            'owner_email': self.owner_email,
            
            # Analytics
            'views': self.views,
            'leads_count': self.leads_count,
            'quality_score': self.quality_score,
            
            # Status
            'status': self.status,
            'available_from': self.available_from.isoformat() if self.available_from else None,
            'featured': self.featured,
            
            # Timestamps
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            
            # Platform status
            'platforms': {
                pl.platform.name: {
                    'status': pl.status,
                    'external_id': pl.external_id,
                    'url': pl.external_url
                }
                for pl in self.platform_listings
            } if self.platform_listings else {}
        }
