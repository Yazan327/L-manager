"""
PropertyFinder Listing Data Models
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from enum import Enum


class PropertyType(Enum):
    """Property types available in PropertyFinder"""
    APARTMENT = "AP"
    VILLA = "VH"
    TOWNHOUSE = "TH"
    PENTHOUSE = "PH"
    COMPOUND = "CO"
    DUPLEX = "DU"
    FULL_FLOOR = "FF"
    HALF_FLOOR = "HF"
    WHOLE_BUILDING = "WB"
    BULK_UNITS = "BU"
    BUNGALOW = "BG"
    HOTEL_APARTMENT = "HA"
    LOFT = "LP"
    OFFICE = "OF"
    RETAIL = "RE"
    WAREHOUSE = "WH"
    SHOP = "SH"
    LAND = "LA"
    LABOUR_CAMP = "LC"
    COMMERCIAL_BUILDING = "CB"
    COMMERCIAL_VILLA = "CV"
    COMMERCIAL_FLOOR = "CF"
    INDUSTRIAL_LAND = "IL"
    MIXED_USE_LAND = "ML"
    SHOWROOM = "SR"
    COMMERCIAL_PLOT = "CP"
    RESIDENTIAL_PLOT = "RP"
    RESIDENTIAL_FLOOR = "RF"
    RESIDENTIAL_BUILDING = "RB"


class OfferingType(Enum):
    """Listing offering types"""
    SALE = "sale"
    RENT = "rent"


class CompletionStatus(Enum):
    """Property completion status"""
    READY = "ready"
    OFF_PLAN = "off_plan"
    COMPLETED = "completed"


class FurnishingStatus(Enum):
    """Furnishing status"""
    FURNISHED = "furnished"
    UNFURNISHED = "unfurnished"
    PARTLY_FURNISHED = "partly_furnished"


class ListingStatus(Enum):
    """Listing publication status"""
    DRAFT = "draft"
    LIVE = "live"
    UNPUBLISHED = "unpublished"
    EXPIRED = "expired"
    DELETED = "deleted"


class RentFrequency(Enum):
    """Rental payment frequency"""
    YEARLY = "yearly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"


@dataclass
class Location:
    """Location/Address information"""
    city: str
    community: str
    sub_community: Optional[str] = None
    building: Optional[str] = None
    street: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Price:
    """Price information"""
    amount: float
    currency: str = "AED"
    frequency: Optional[RentFrequency] = None  # For rentals
    
    def to_dict(self) -> Dict[str, Any]:
        data = {
            'amount': self.amount,
            'currency': self.currency
        }
        if self.frequency:
            data['frequency'] = self.frequency.value
        return data


@dataclass
class Agent:
    """Agent information"""
    id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass 
class PropertyListing:
    """
    Complete Property Listing Model
    
    This represents a property listing with all available fields
    for the PropertyFinder API.
    """
    # Required fields
    title: str
    title_arabic: Optional[str] = None
    description: str = ""
    description_arabic: Optional[str] = None
    property_type: PropertyType = PropertyType.APARTMENT
    offering_type: OfferingType = OfferingType.SALE
    price: Price = None
    location: Location = None
    
    # Property details
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    size: Optional[float] = None  # in sqft
    plot_size: Optional[float] = None  # in sqft
    
    # Additional details
    reference_number: Optional[str] = None
    permit_number: Optional[str] = None  # RERA/DLD permit
    completion_status: Optional[CompletionStatus] = None
    furnishing: Optional[FurnishingStatus] = None
    parking_spaces: Optional[int] = None
    year_built: Optional[int] = None
    
    # Media
    images: List[str] = field(default_factory=list)
    video_url: Optional[str] = None
    virtual_tour_url: Optional[str] = None
    
    # Amenities and features
    amenities: List[str] = field(default_factory=list)
    private_amenities: List[str] = field(default_factory=list)
    
    # Agent assignment
    agent: Optional[Agent] = None
    agent_id: Optional[str] = None
    
    # Status
    status: ListingStatus = ListingStatus.DRAFT
    featured: bool = False
    
    # Rental specific
    rent_frequency: Optional[RentFrequency] = None
    cheques: Optional[int] = None  # Number of cheques for rent
    
    # Meta
    external_reference: Optional[str] = None  # Your internal reference
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert listing to API-compatible dictionary"""
        data = {
            'title': self.title,
            'description': self.description,
            'property_type': self.property_type.value if isinstance(self.property_type, PropertyType) else self.property_type,
            'offering_type': self.offering_type.value if isinstance(self.offering_type, OfferingType) else self.offering_type,
        }
        
        # Add optional string fields
        if self.title_arabic:
            data['title_ar'] = self.title_arabic
        if self.description_arabic:
            data['description_ar'] = self.description_arabic
        if self.reference_number:
            data['reference_number'] = self.reference_number
        if self.permit_number:
            data['permit_number'] = self.permit_number
        if self.video_url:
            data['video_url'] = self.video_url
        if self.virtual_tour_url:
            data['virtual_tour_url'] = self.virtual_tour_url
        if self.external_reference:
            data['external_reference'] = self.external_reference
        
        # Add price
        if self.price:
            data['price'] = self.price.to_dict() if hasattr(self.price, 'to_dict') else self.price
        
        # Add location
        if self.location:
            data['location'] = self.location.to_dict() if hasattr(self.location, 'to_dict') else self.location
        
        # Add numeric fields
        if self.bedrooms is not None:
            data['bedrooms'] = self.bedrooms
        if self.bathrooms is not None:
            data['bathrooms'] = self.bathrooms
        if self.size is not None:
            data['size'] = self.size
        if self.plot_size is not None:
            data['plot_size'] = self.plot_size
        if self.parking_spaces is not None:
            data['parking'] = self.parking_spaces
        if self.year_built is not None:
            data['year_built'] = self.year_built
        if self.cheques is not None:
            data['cheques'] = self.cheques
        
        # Add enum fields
        if self.completion_status:
            data['completion_status'] = self.completion_status.value if isinstance(self.completion_status, CompletionStatus) else self.completion_status
        if self.furnishing:
            data['furnishing'] = self.furnishing.value if isinstance(self.furnishing, FurnishingStatus) else self.furnishing
        if self.rent_frequency:
            data['rent_frequency'] = self.rent_frequency.value if isinstance(self.rent_frequency, RentFrequency) else self.rent_frequency
        
        # Add lists
        if self.images:
            data['images'] = self.images
        if self.amenities:
            data['amenities'] = self.amenities
        if self.private_amenities:
            data['private_amenities'] = self.private_amenities
        
        # Add agent
        if self.agent_id:
            data['agent_id'] = self.agent_id
        elif self.agent:
            data['agent'] = self.agent.to_dict() if hasattr(self.agent, 'to_dict') else self.agent
        
        # Add boolean fields
        data['featured'] = self.featured
        
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PropertyListing':
        """Create a PropertyListing from a dictionary"""
        # Handle property_type
        prop_type = data.get('property_type')
        if isinstance(prop_type, str):
            try:
                prop_type = PropertyType(prop_type)
            except ValueError:
                prop_type = PropertyType.APARTMENT
        
        # Handle offering_type
        offer_type = data.get('offering_type')
        if isinstance(offer_type, str):
            try:
                offer_type = OfferingType(offer_type)
            except ValueError:
                offer_type = OfferingType.SALE
        
        # Handle price
        price_data = data.get('price')
        price = None
        if price_data:
            if isinstance(price_data, dict):
                freq = price_data.get('frequency')
                if freq:
                    try:
                        freq = RentFrequency(freq)
                    except ValueError:
                        freq = None
                price = Price(
                    amount=price_data.get('amount', 0),
                    currency=price_data.get('currency', 'AED'),
                    frequency=freq
                )
            elif isinstance(price_data, (int, float)):
                price = Price(amount=price_data)
        
        # Handle location
        loc_data = data.get('location')
        location = None
        if loc_data and isinstance(loc_data, dict):
            location = Location(
                city=loc_data.get('city', ''),
                community=loc_data.get('community', ''),
                sub_community=loc_data.get('sub_community'),
                building=loc_data.get('building'),
                street=loc_data.get('street'),
                latitude=loc_data.get('latitude'),
                longitude=loc_data.get('longitude')
            )
        
        return cls(
            title=data.get('title', ''),
            title_arabic=data.get('title_ar'),
            description=data.get('description', ''),
            description_arabic=data.get('description_ar'),
            property_type=prop_type,
            offering_type=offer_type,
            price=price,
            location=location,
            bedrooms=data.get('bedrooms'),
            bathrooms=data.get('bathrooms'),
            size=data.get('size'),
            plot_size=data.get('plot_size'),
            reference_number=data.get('reference_number'),
            permit_number=data.get('permit_number'),
            completion_status=data.get('completion_status'),
            furnishing=data.get('furnishing'),
            parking_spaces=data.get('parking'),
            year_built=data.get('year_built'),
            images=data.get('images', []),
            video_url=data.get('video_url'),
            virtual_tour_url=data.get('virtual_tour_url'),
            amenities=data.get('amenities', []),
            private_amenities=data.get('private_amenities', []),
            agent_id=data.get('agent_id'),
            featured=data.get('featured', False),
            rent_frequency=data.get('rent_frequency'),
            cheques=data.get('cheques'),
            external_reference=data.get('external_reference')
        )
