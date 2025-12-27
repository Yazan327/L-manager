"""
Lead Model

Incoming leads from all sources: PropertyFinder, Bayut, Website, Zapier/Social Media.
"""
from datetime import datetime
from typing import Optional, List
import json
import enum
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.orm import relationship

from v2.core.database import Base


class LeadSource(str, enum.Enum):
    """Lead source"""
    PROPERTYFINDER = "propertyfinder"
    BAYUT = "bayut"
    WEBSITE = "website"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    WHATSAPP = "whatsapp"
    PHONE = "phone"
    EMAIL = "email"
    REFERRAL = "referral"
    ZAPIER = "zapier"
    OTHER = "other"


class LeadStatus(str, enum.Enum):
    """Lead status in pipeline"""
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    VIEWING_SCHEDULED = "viewing_scheduled"
    VIEWING_DONE = "viewing_done"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"
    SPAM = "spam"


class LeadPriority(str, enum.Enum):
    """Lead priority"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Lead(Base):
    """Incoming lead from any source"""
    __tablename__ = 'leads'
    
    id = Column(Integer, primary_key=True, index=True)
    
    # ==================== SOURCE ====================
    source = Column(String(30), default='other', index=True)  # LeadSource
    source_id = Column(String(100))  # External ID from source (PF lead ID, etc.)
    source_url = Column(String(500))  # URL where lead originated
    
    # ==================== CONTACT INFO ====================
    name = Column(String(200), nullable=False)
    email = Column(String(120), index=True)
    phone = Column(String(50), index=True)
    whatsapp = Column(String(50))
    nationality = Column(String(50))
    
    # ==================== INQUIRY DETAILS ====================
    message = Column(Text)
    inquiry_type = Column(String(50))  # general, viewing, price, availability
    
    # ==================== PROPERTY INTEREST ====================
    listing_id = Column(Integer, ForeignKey('listings_v2.id'), nullable=True)
    listing_reference = Column(String(50))  # For quick reference
    
    # What they're looking for (if not specific listing)
    interested_in = Column(String(20))  # sale, rent
    property_types = Column(String(200))  # JSON array
    min_bedrooms = Column(Integer)
    max_bedrooms = Column(Integer)
    min_price = Column(Float)
    max_price = Column(Float)
    preferred_locations = Column(Text)  # JSON array
    
    # ==================== QUALIFICATION ====================
    status = Column(String(30), default='new', index=True)  # LeadStatus
    priority = Column(String(20), default='medium')  # LeadPriority
    score = Column(Integer, default=0)  # Lead quality score (0-100)
    
    # Finance
    budget = Column(Float)
    is_pre_approved = Column(Boolean, default=False)
    financing_needed = Column(Boolean, default=False)
    
    # Timeline
    move_in_date = Column(DateTime)
    urgency = Column(String(20))  # immediate, 1_month, 3_months, 6_months, flexible
    
    # ==================== ASSIGNMENT ====================
    assigned_to_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    
    # ==================== FOLLOW-UP ====================
    last_contact = Column(DateTime)
    next_follow_up = Column(DateTime)
    follow_up_notes = Column(Text)
    
    # ==================== CONVERSION ====================
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
    converted_at = Column(DateTime)
    lost_reason = Column(String(200))
    
    # ==================== METADATA ====================
    tags = Column(String(500))  # JSON array
    notes = Column(Text)
    raw_data = Column(Text)  # Original webhook/API data (JSON)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # ==================== RELATIONSHIPS ====================
    listing = relationship("Listing", back_populates="leads")
    assigned_to = relationship("User", back_populates="assigned_leads", foreign_keys=[assigned_to_id])
    customer = relationship("Customer", back_populates="leads")
    
    def get_tags(self) -> List[str]:
        """Get tags as list"""
        if not self.tags:
            return []
        try:
            return json.loads(self.tags)
        except:
            return []
    
    def set_tags(self, tags: List[str]):
        """Set tags from list"""
        self.tags = json.dumps(tags)
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            
            # Source
            'source': self.source,
            'source_id': self.source_id,
            'source_url': self.source_url,
            
            # Contact
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'whatsapp': self.whatsapp,
            'nationality': self.nationality,
            
            # Inquiry
            'message': self.message,
            'inquiry_type': self.inquiry_type,
            
            # Property
            'listing_id': self.listing_id,
            'listing_reference': self.listing_reference,
            'listing_title': self.listing.title_en if self.listing else None,
            
            # Preferences
            'interested_in': self.interested_in,
            'property_types': self.property_types,
            'min_bedrooms': self.min_bedrooms,
            'max_bedrooms': self.max_bedrooms,
            'min_price': self.min_price,
            'max_price': self.max_price,
            'preferred_locations': self.preferred_locations,
            
            # Qualification
            'status': self.status,
            'priority': self.priority,
            'score': self.score,
            'budget': self.budget,
            'is_pre_approved': self.is_pre_approved,
            'financing_needed': self.financing_needed,
            'move_in_date': self.move_in_date.isoformat() if self.move_in_date else None,
            'urgency': self.urgency,
            
            # Assignment
            'assigned_to_id': self.assigned_to_id,
            'assigned_to_name': self.assigned_to.name if self.assigned_to else None,
            
            # Follow-up
            'last_contact': self.last_contact.isoformat() if self.last_contact else None,
            'next_follow_up': self.next_follow_up.isoformat() if self.next_follow_up else None,
            'follow_up_notes': self.follow_up_notes,
            
            # Conversion
            'customer_id': self.customer_id,
            'converted_at': self.converted_at.isoformat() if self.converted_at else None,
            'lost_reason': self.lost_reason,
            
            # Metadata
            'tags': self.get_tags(),
            'notes': self.notes,
            
            # Timestamps
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
