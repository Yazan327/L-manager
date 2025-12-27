"""
Customer Model

CRM functionality: Customer management and interaction tracking.
"""
from datetime import datetime
from typing import Optional, List
import json
import enum
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.orm import relationship

from v2.core.database import Base


class CustomerType(str, enum.Enum):
    """Customer type"""
    BUYER = "buyer"
    SELLER = "seller"
    TENANT = "tenant"
    LANDLORD = "landlord"
    INVESTOR = "investor"


class CustomerStatus(str, enum.Enum):
    """Customer status"""
    PROSPECT = "prospect"
    ACTIVE = "active"
    INACTIVE = "inactive"
    VIP = "vip"


class Customer(Base):
    """Customer / prospect for CRM"""
    __tablename__ = 'customers'
    
    id = Column(Integer, primary_key=True, index=True)
    
    # ==================== IDENTITY ====================
    name = Column(String(200), nullable=False)
    email = Column(String(120), unique=True, index=True)
    phone = Column(String(50), index=True)
    whatsapp = Column(String(50))
    
    # ==================== DETAILS ====================
    customer_type = Column(String(20), default='buyer')  # CustomerType
    status = Column(String(20), default='prospect')  # CustomerStatus
    nationality = Column(String(50))
    language = Column(String(20), default='en')
    
    # Company (for investors/corporates)
    company_name = Column(String(200))
    company_position = Column(String(100))
    
    # ==================== PREFERENCES ====================
    interested_in = Column(String(20))  # sale, rent
    preferred_property_types = Column(String(200))  # JSON array
    preferred_locations = Column(Text)  # JSON array
    min_budget = Column(Float)
    max_budget = Column(Float)
    min_bedrooms = Column(Integer)
    max_bedrooms = Column(Integer)
    
    # ==================== FINANCIAL ====================
    is_pre_approved = Column(Boolean, default=False)
    pre_approval_amount = Column(Float)
    financing_source = Column(String(100))
    
    # ==================== ASSIGNMENT ====================
    assigned_agent_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    
    # ==================== STATISTICS ====================
    total_leads = Column(Integer, default=0)
    total_viewings = Column(Integer, default=0)
    total_transactions = Column(Float, default=0)  # Total value of closed deals
    
    # ==================== METADATA ====================
    tags = Column(String(500))  # JSON array
    notes = Column(Text)
    source = Column(String(50))  # How they found us
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_contact = Column(DateTime)
    
    # ==================== RELATIONSHIPS ====================
    leads = relationship("Lead", back_populates="customer")
    interactions = relationship("CustomerInteraction", back_populates="customer", cascade="all, delete-orphan")
    assigned_agent = relationship("User", foreign_keys=[assigned_agent_id])
    
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
            
            # Identity
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'whatsapp': self.whatsapp,
            
            # Details
            'customer_type': self.customer_type,
            'status': self.status,
            'nationality': self.nationality,
            'language': self.language,
            'company_name': self.company_name,
            'company_position': self.company_position,
            
            # Preferences
            'interested_in': self.interested_in,
            'preferred_property_types': self.preferred_property_types,
            'preferred_locations': self.preferred_locations,
            'min_budget': self.min_budget,
            'max_budget': self.max_budget,
            'min_bedrooms': self.min_bedrooms,
            'max_bedrooms': self.max_bedrooms,
            
            # Financial
            'is_pre_approved': self.is_pre_approved,
            'pre_approval_amount': self.pre_approval_amount,
            'financing_source': self.financing_source,
            
            # Assignment
            'assigned_agent_id': self.assigned_agent_id,
            'assigned_agent_name': self.assigned_agent.name if self.assigned_agent else None,
            
            # Statistics
            'total_leads': self.total_leads,
            'total_viewings': self.total_viewings,
            'total_transactions': self.total_transactions,
            
            # Metadata
            'tags': self.get_tags(),
            'notes': self.notes,
            'source': self.source,
            
            # Timestamps
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_contact': self.last_contact.isoformat() if self.last_contact else None,
        }


class InteractionType(str, enum.Enum):
    """Interaction type"""
    CALL = "call"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    MEETING = "meeting"
    VIEWING = "viewing"
    NOTE = "note"


class CustomerInteraction(Base):
    """Track interactions with customers"""
    __tablename__ = 'customer_interactions'
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)
    
    # ==================== INTERACTION ====================
    interaction_type = Column(String(20), nullable=False)  # InteractionType
    direction = Column(String(10), default='outbound')  # inbound, outbound
    
    subject = Column(String(200))
    notes = Column(Text)
    outcome = Column(String(100))  # interested, not_interested, follow_up, etc.
    
    # For viewings
    listing_id = Column(Integer, ForeignKey('listings_v2.id'), nullable=True)
    
    # ==================== FOLLOW-UP ====================
    follow_up_date = Column(DateTime)
    follow_up_notes = Column(Text)
    
    # ==================== METADATA ====================
    created_by_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # ==================== RELATIONSHIPS ====================
    customer = relationship("Customer", back_populates="interactions")
    created_by = relationship("User", foreign_keys=[created_by_id])
    listing = relationship("Listing", foreign_keys=[listing_id])
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'interaction_type': self.interaction_type,
            'direction': self.direction,
            'subject': self.subject,
            'notes': self.notes,
            'outcome': self.outcome,
            'listing_id': self.listing_id,
            'listing_reference': self.listing.reference if self.listing else None,
            'follow_up_date': self.follow_up_date.isoformat() if self.follow_up_date else None,
            'follow_up_notes': self.follow_up_notes,
            'created_by_id': self.created_by_id,
            'created_by_name': self.created_by.name if self.created_by else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
