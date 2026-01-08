"""
Database Models for Local Listings Storage
"""
import re
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def convert_google_drive_url(url):
    """Convert Google Drive share URL to direct CDN URL.
    
    Supports formats:
    - https://drive.google.com/file/d/FILE_ID/view
    - https://drive.google.com/open?id=FILE_ID
    - https://drive.google.com/uc?id=FILE_ID
    
    Returns CDN URL: https://lh3.googleusercontent.com/d/FILE_ID
    This format is more reliable for external services like PropertyFinder.
    """
    if not url:
        return url
    
    url = url.strip()
    
    # Already a CDN URL
    if 'lh3.googleusercontent.com' in url:
        return url
    
    # Not a Google Drive URL
    if 'drive.google.com' not in url:
        return url
    
    # Extract file ID from various Google Drive URL formats
    file_id = None
    
    # Format: /file/d/FILE_ID/view or /file/d/FILE_ID
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
    
    # Format: /open?id=FILE_ID or /uc?id=FILE_ID
    if not file_id:
        match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
        if match:
            file_id = match.group(1)
    
    if file_id:
        return f'https://lh3.googleusercontent.com/d/{file_id}'
    
    # Could not extract file ID, return original
    return url


# ==================== USER & AUTHENTICATION ====================

class User(db.Model):
    """Dashboard user with role-based permissions"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='viewer')  # admin, manager, agent, viewer
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Custom permissions (JSON array of permission strings, overrides role if set)
    custom_permissions = db.Column(db.Text, nullable=True)  # JSON array like '["view","create"]'
    
    # Locked PF Agent ID - user can only see/manage listings for this agent
    pf_agent_id = db.Column(db.String(100), nullable=True)  # PropertyFinder public profile ID
    pf_agent_name = db.Column(db.String(100), nullable=True)  # Display name for the agent
    
    # All available permissions
    ALL_PERMISSIONS = [
        'view',           # View listings and insights
        'create',         # Create new listings
        'edit',           # Edit existing listings
        'delete',         # Delete listings
        'publish',        # Publish/unpublish listings
        'bulk_upload',    # Bulk upload listings
        'manage_leads',   # View and manage leads
        'manage_users',   # Manage users (admin only typically)
        'settings'        # Access app settings
    ]
    
    # Role-based permissions (used as defaults)
    ROLES = {
        'admin': {
            'name': 'Administrator',
            'permissions': ['view', 'create', 'edit', 'delete', 'publish', 'bulk_upload', 'manage_leads', 'manage_users', 'settings']
        },
        'manager': {
            'name': 'Manager',
            'permissions': ['view', 'create', 'edit', 'delete', 'publish', 'bulk_upload', 'manage_leads']
        },
        'agent': {
            'name': 'Agent',
            'permissions': ['view', 'create', 'edit', 'manage_leads']
        },
        'viewer': {
            'name': 'Viewer',
            'permissions': ['view']
        }
    }
    
    def set_password(self, password):
        """Hash and set the user's password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify the user's password"""
        return check_password_hash(self.password_hash, password)
    
    def set_custom_permissions(self, permissions_list):
        """Set custom permissions from a list"""
        import json
        if permissions_list:
            self.custom_permissions = json.dumps(permissions_list)
        else:
            self.custom_permissions = None
    
    def get_custom_permissions(self):
        """Get custom permissions as a list"""
        import json
        if self.custom_permissions:
            try:
                return json.loads(self.custom_permissions)
            except:
                return None
        return None
    
    def has_permission(self, permission):
        """Check if user has a specific permission (custom permissions override role)"""
        custom = self.get_custom_permissions()
        if custom is not None:
            return permission in custom
        role_perms = self.ROLES.get(self.role, {}).get('permissions', [])
        return permission in role_perms
    
    def get_permissions(self):
        """Get all permissions for the user (custom or role-based)"""
        custom = self.get_custom_permissions()
        if custom is not None:
            return custom
        return self.ROLES.get(self.role, {}).get('permissions', [])
    
    def to_dict(self):
        """Convert to dictionary (without password)"""
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'role': self.role,
            'role_name': self.ROLES.get(self.role, {}).get('name', 'Unknown'),
            'permissions': self.get_permissions(),
            'custom_permissions': self.get_custom_permissions(),
            'has_custom_permissions': self.custom_permissions is not None,
            'pf_agent_id': self.pf_agent_id,
            'pf_agent_name': self.pf_agent_name,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


# ==================== LISTING FOLDERS ====================

class ListingFolder(db.Model):
    """Folders/Groups for organizing listings"""
    __tablename__ = 'listing_folders'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='indigo')  # CSS color class
    icon = db.Column(db.String(50), default='fa-folder')  # FontAwesome icon
    description = db.Column(db.Text, nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('listing_folders.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    parent = db.relationship('ListingFolder', remote_side=[id], backref='subfolders')
    listings = db.relationship('LocalListing', backref='folder', lazy='dynamic')
    
    # Available colors for folder styling
    COLORS = ['indigo', 'blue', 'green', 'yellow', 'red', 'purple', 'pink', 'gray', 'orange', 'teal']
    
    # Available icons
    ICONS = [
        'fa-folder', 'fa-building', 'fa-home', 'fa-city', 'fa-star', 
        'fa-heart', 'fa-fire', 'fa-bolt', 'fa-gem', 'fa-crown',
        'fa-tag', 'fa-bookmark', 'fa-flag', 'fa-bell', 'fa-clock'
    ]
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'name': self.name,
            'color': self.color,
            'icon': self.icon,
            'description': self.description,
            'parent_id': self.parent_id,
            'listing_count': self.listings.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def get_all_with_counts(cls):
        """Get all folders with listing counts"""
        folders = cls.query.order_by(cls.name).all()
        return [f.to_dict() for f in folders]


# ==================== LISTINGS ====================

class LocalListing(db.Model):
    """Local listing storage model"""
    __tablename__ = 'listings'
    
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    
    # Folder/Group assignment
    folder_id = db.Column(db.Integer, db.ForeignKey('listing_folders.id'), nullable=True)
    
    # Core Details
    emirate = db.Column(db.String(50))
    city = db.Column(db.String(100))
    location = db.Column(db.String(200))  # Location text for display
    location_id = db.Column(db.Integer)   # PropertyFinder location ID
    category = db.Column(db.String(20))  # residential, commercial
    offering_type = db.Column(db.String(20))  # sale, rent
    property_type = db.Column(db.String(50))  # apartment, villa, townhouse, etc.
    
    # Specifications
    bedrooms = db.Column(db.String(10))
    bathrooms = db.Column(db.String(10))
    size = db.Column(db.Float)
    furnishing_type = db.Column(db.String(20))
    project_status = db.Column(db.String(20))
    parking_slots = db.Column(db.Integer)
    floor_number = db.Column(db.String(20))
    unit_number = db.Column(db.String(50))
    
    # Price
    price = db.Column(db.Float)
    downpayment = db.Column(db.Float)
    rent_frequency = db.Column(db.String(20))
    
    # Description
    title_en = db.Column(db.String(100))
    title_ar = db.Column(db.String(100))
    description_en = db.Column(db.Text)
    description_ar = db.Column(db.Text)
    
    # Media
    images = db.Column(db.Text)  # JSON array of URLs
    video_tour = db.Column(db.String(500))
    video_360 = db.Column(db.String(500))
    
    # Amenities
    amenities = db.Column(db.Text)  # Comma-separated list
    
    # Assignment
    assigned_agent = db.Column(db.String(100))
    owner_id = db.Column(db.String(100))
    owner_name = db.Column(db.String(100))
    
    # Additional
    developer = db.Column(db.String(100))
    permit_number = db.Column(db.String(50))
    available_from = db.Column(db.String(50))
    
    # Analytics
    views = db.Column(db.Integer, default=0)
    leads = db.Column(db.Integer, default=0)
    
    # Status & Metadata
    status = db.Column(db.String(20), default='draft')  # draft, published, pending
    pf_listing_id = db.Column(db.String(50))  # PropertyFinder ID if synced
    synced_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def get_images(self):
        """Public method to get parsed images list"""
        return self._parse_images()
    
    def _parse_images(self):
        """Parse images from various storage formats and return URLs"""
        import json
        
        if not self.images:
            return []
        
        images = []
        
        # Try JSON format first (new format from image editor)
        try:
            parsed = json.loads(self.images)
            if isinstance(parsed, list):
                images = parsed
        except (json.JSONDecodeError, TypeError):
            # Fall back to pipe-separated format (legacy)
            images = self.images.split('|') if self.images else []
        
        # Convert relative paths to URLs and filter out invalid entries
        result = []
        for img in images:
            if not img:  # Skip None, empty strings, etc.
                continue
                
            url = None
            
            if isinstance(img, str):
                img = img.strip()
                if not img or img.lower() == 'none':  # Skip empty or "None" strings
                    continue
                    
                # If it's a relative path (e.g., "listings/123/img.jpg"), prefix with /uploads/
                if img.startswith('listings/') or img.startswith('uploads/'):
                    if not img.startswith('/'):
                        url = '/uploads/' + img.lstrip('uploads/')
                    else:
                        url = img
                elif img.startswith('http'):
                    # Already a full URL
                    url = img
                elif img.startswith('/'):
                    # Already an absolute path
                    url = img
                elif img.startswith('temp/'):
                    url = '/uploads/' + img
                else:
                    # Assume it's a relative path, prefix with /uploads/
                    url = '/uploads/' + img
                    
            elif isinstance(img, dict):
                # Handle PropertyFinder format: {original: {url: "..."}}
                url = img.get('url') or (img.get('original', {}).get('url') if img.get('original') else None)
            
            # Only add valid URLs
            if url and url.lower() != 'none' and len(url) > 1:
                result.append(url)
        
        return result
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'reference': self.reference,
            'emirate': self.emirate,
            'city': self.city,
            'location': self.location,
            'location_id': self.location_id,
            'category': self.category,
            'offering_type': self.offering_type,
            'property_type': self.property_type,
            'bedrooms': self.bedrooms,
            'bathrooms': self.bathrooms,
            'size': self.size,
            'furnishing_type': self.furnishing_type,
            'project_status': self.project_status,
            'parking_slots': self.parking_slots,
            'floor_number': self.floor_number,
            'unit_number': self.unit_number,
            'price': self.price,
            'downpayment': self.downpayment,
            'rent_frequency': self.rent_frequency,
            'title_en': self.title_en,
            'title_ar': self.title_ar,
            'description_en': self.description_en,
            'description_ar': self.description_ar,
            'images': self._parse_images(),
            'video_tour': self.video_tour,
            'video_360': self.video_360,
            'amenities': self.amenities.split(',') if self.amenities else [],
            'assigned_agent': self.assigned_agent,
            'owner_id': self.owner_id,
            'owner_name': self.owner_name,
            'developer': self.developer,
            'permit_number': self.permit_number,
            'available_from': self.available_from,
            'views': self.views or 0,
            'leads': self.leads or 0,
            'status': self.status,
            'pf_listing_id': self.pf_listing_id,
            'folder_id': self.folder_id,
            'folder': self.folder.to_dict() if self.folder else None,
            'synced_at': self.synced_at.isoformat() if self.synced_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
    
    @classmethod
    def from_dict(cls, data):
        """Create from dictionary - handles both local format and PropertyFinder API format"""
        
        # Handle PropertyFinder API format (camelCase, nested objects)
        # Extract values from nested structures
        
        # Images - can be string, list, or PF format [{original: {url}}]
        images = data.get('images', [])
        if isinstance(images, list):
            if len(images) > 0 and isinstance(images[0], dict):
                # PF format: [{original: {url: '...'}}]
                images = '|'.join([img.get('original', {}).get('url', '') for img in images if img.get('original', {}).get('url')])
            else:
                images = '|'.join([str(img) for img in images if img])
        
        # Handle media object (PF format)
        media = data.get('media', {})
        if media:
            if not images and media.get('images'):
                img_list = media.get('images', [])
                images = '|'.join([img.get('original', {}).get('url', '') for img in img_list if img.get('original', {}).get('url')])
        
        # Videos from media object
        videos = media.get('videos', {}) if media else {}
        video_tour = data.get('video_tour') or videos.get('default', '')
        video_360 = data.get('video_360') or videos.get('view360', '')
        
        # Handle amenities list
        amenities = data.get('amenities', [])
        if isinstance(amenities, list):
            amenities = ','.join([str(a) for a in amenities])
        
        # Handle title/description (PF uses nested objects)
        title = data.get('title', {})
        title_en = data.get('title_en') or (title.get('en') if isinstance(title, dict) else None)
        title_ar = data.get('title_ar') or (title.get('ar') if isinstance(title, dict) else None)
        
        description = data.get('description', {})
        description_en = data.get('description_en') or (description.get('en') if isinstance(description, dict) else None)
        description_ar = data.get('description_ar') or (description.get('ar') if isinstance(description, dict) else None)
        
        # Handle price (PF uses nested price.type, price.amounts)
        price_obj = data.get('price', {})
        price = data.get('price') if not isinstance(data.get('price'), dict) else None
        price_type = None
        downpayment = data.get('downpayment')
        
        if isinstance(price_obj, dict):
            price_type = price_obj.get('type')
            amounts = price_obj.get('amounts', {})
            # Get price from amounts based on type
            if price_type and amounts:
                price = amounts.get(price_type) or amounts.get('sale') or amounts.get('yearly') or 0
            downpayment = downpayment or price_obj.get('downpayment')
        
        # Determine offering type from price type
        offering_type = data.get('offering_type')
        if not offering_type and price_type:
            offering_type = 'sale' if price_type == 'sale' else 'rent'
        rent_frequency = data.get('rent_frequency') or (price_type if price_type != 'sale' else None)
        
        # Handle location (PF uses location.id)
        location = data.get('location')
        location_id = None
        if isinstance(location, dict):
            location_id = location.get('id')
            location = data.get('_locationText', '')  # Fallback text if provided
        
        # Handle assignedTo (PF uses assignedTo.id)
        assigned_to = data.get('assignedTo', {})
        assigned_agent = data.get('assigned_agent')
        if isinstance(assigned_to, dict) and assigned_to.get('id'):
            assigned_agent = str(assigned_to.get('id'))
        
        # Handle compliance (PF format)
        compliance = data.get('compliance', {})
        permit_number = data.get('permit_number')
        if isinstance(compliance, dict) and compliance.get('listingAdvertisementNumber'):
            permit_number = compliance.get('listingAdvertisementNumber')
        
        return cls(
            reference=data.get('reference'),
            emirate=data.get('emirate') or data.get('uaeEmirate'),
            city=data.get('city'),
            location=location if isinstance(location, str) else '',
            location_id=location_id,
            category=data.get('category'),
            offering_type=offering_type,
            property_type=data.get('property_type') or data.get('type'),
            bedrooms=str(data.get('bedrooms', '')),
            bathrooms=str(data.get('bathrooms', '')),
            size=float(data.get('size', 0)) if data.get('size') else None,
            furnishing_type=data.get('furnishing_type') or data.get('furnishingType'),
            project_status=data.get('project_status') or data.get('projectStatus'),
            parking_slots=int(data.get('parking_slots') or data.get('parkingSlots') or 0) if (data.get('parking_slots') or data.get('parkingSlots')) else None,
            floor_number=data.get('floor_number') or data.get('floorNumber'),
            unit_number=data.get('unit_number') or data.get('unitNumber'),
            price=float(price) if price else None,
            downpayment=float(downpayment) if downpayment else None,
            rent_frequency=rent_frequency,
            title_en=title_en,
            title_ar=title_ar,
            description_en=description_en,
            description_ar=description_ar,
            images=images,
            video_tour=video_tour,
            video_360=video_360,
            amenities=amenities,
            assigned_agent=assigned_agent,
            owner_id=data.get('owner_id'),
            owner_name=data.get('owner_name') or data.get('ownerName'),
            developer=data.get('developer'),
            permit_number=permit_number,
            available_from=data.get('available_from') or data.get('availableFrom'),
            status=data.get('status', 'draft'),
        )
    
    def to_pf_format(self):
        """
        Convert local listing to PropertyFinder API format
        
        Required by PF API:
        - uaeEmirate: dubai, abu_dhabi, northern_emirates
        - type: Property type (apartment, villa, etc.)
        - category: residential or commercial
        - price.type: yearly, sale, monthly, etc.
        - price.amounts: {yearly: 50000} or {sale: 1000000}
        - location.id: Location ID from /locations API
        - title.en or title.ar: Listing title
        - assignedTo.id: Public profile ID from /users API
        - bedrooms: string (studio, 1-30)
        - bathrooms: string (none, 1-20)
        """
        import uuid
        import time
        
        # Auto-generate reference if missing
        reference = self.reference
        if not reference:
            date_part = time.strftime('%Y%m%d')
            unique_part = uuid.uuid4().hex[:5].upper()
            reference = f"REF-{date_part}-{unique_part}"
            # Save it back to the model
            self.reference = reference
        
        # Property type - already stored in API format
        prop_type = self.property_type.lower() if self.property_type else 'apartment'
        
        pf_data = {
            'reference': reference,
            'category': self.category or 'residential',
            'type': prop_type,
        }
        
        # UAE Emirate
        if self.emirate:
            emirate = self.emirate.lower()
            if emirate in ['dubai', 'abu_dhabi', 'northern_emirates']:
                pf_data['uaeEmirate'] = emirate
            elif 'abu' in emirate or 'dhabi' in emirate:
                pf_data['uaeEmirate'] = 'abu_dhabi'
            elif 'dubai' in emirate:
                pf_data['uaeEmirate'] = 'dubai'
            else:
                pf_data['uaeEmirate'] = 'northern_emirates'
        
        # Location - use location_id if available
        if self.location_id:
            pf_data['location'] = {'id': int(self.location_id)}
        
        # Assigned Agent and Created By (both required by PF API)
        if self.assigned_agent:
            try:
                agent_id = int(self.assigned_agent)
                pf_data['assignedTo'] = {'id': agent_id}
                pf_data['createdBy'] = {'id': agent_id}  # Required by PF API
            except (ValueError, TypeError):
                pass
        
        # Title
        if self.title_en or self.title_ar:
            pf_data['title'] = {}
            if self.title_en:
                pf_data['title']['en'] = self.title_en[:100]  # Max 100 chars
            if self.title_ar:
                pf_data['title']['ar'] = self.title_ar[:100]
        
        # Description
        if self.description_en or self.description_ar:
            pf_data['description'] = {}
            if self.description_en:
                pf_data['description']['en'] = self.description_en[:5000]
            if self.description_ar:
                pf_data['description']['ar'] = self.description_ar[:5000]
        
        # Price structure (API format)
        if self.price:
            price_type = 'sale' if self.offering_type == 'sale' else (self.rent_frequency or 'yearly')
            pf_data['price'] = {
                'type': price_type,
                'amounts': {price_type: int(self.price)}
            }
            if self.downpayment and price_type == 'sale':
                pf_data['price']['downpayment'] = int(self.downpayment)
        
        # Specifications - bedrooms/bathrooms must be strings
        if self.bedrooms:
            beds = str(self.bedrooms).lower().strip()
            if beds == '0':
                beds = 'studio'
            pf_data['bedrooms'] = beds
        
        if self.bathrooms:
            baths = str(self.bathrooms).lower().strip()
            if baths == '0':
                baths = 'none'
            pf_data['bathrooms'] = baths
        
        # Size - number in sqft
        if self.size:
            pf_data['size'] = float(self.size)
        
        # Furnishing type
        if self.furnishing_type:
            furn = self.furnishing_type.lower()
            if furn in ['furnished', 'semi-furnished', 'unfurnished']:
                pf_data['furnishingType'] = furn
        
        # Project status
        if self.project_status:
            status = self.project_status.lower()
            if status in ['completed', 'off_plan', 'completed_primary', 'off_plan_primary']:
                pf_data['projectStatus'] = status
        
        # Other specs
        if self.parking_slots:
            pf_data['parkingSlots'] = int(self.parking_slots)
        if self.floor_number:
            pf_data['floorNumber'] = str(self.floor_number)
        if self.unit_number:
            pf_data['unitNumber'] = str(self.unit_number)
        if self.developer:
            pf_data['developer'] = self.developer
        if self.available_from:
            pf_data['availableFrom'] = self.available_from
        
        # Amenities - must be valid API values
        if self.amenities:
            amenities_list = self.amenities.split(',') if isinstance(self.amenities, str) else self.amenities
            valid_amenities = [
                'central-ac', 'built-in-wardrobes', 'kitchen-appliances', 'security',
                'concierge', 'private-gym', 'shared-gym', 'private-jacuzzi', 'shared-spa',
                'covered-parking', 'maids-room', 'barbecue-area', 'shared-pool',
                'childrens-pool', 'private-garden', 'private-pool', 'view-of-water',
                'walk-in-closet', 'lobby-in-building', 'electricity', 'waters',
                'sanitation', 'no-services', 'fixed-phone', 'fibre-optics',
                'flood-drainage', 'balcony', 'networked', 'view-of-landmark',
                'dining-in-building', 'conference-room', 'study', 'maid-service',
                'childrens-play-area', 'pets-allowed', 'vastu-compliant'
            ]
            pf_data['amenities'] = [a.strip() for a in amenities_list if a.strip() in valid_amenities]
        
        # Media - Images in API format
        # PropertyFinder requires publicly accessible URLs
        if self.images:
            import os
            
            # Get public URL base (Railway or custom domain)
            public_url = os.environ.get('APP_PUBLIC_URL') or os.environ.get('RAILWAY_PUBLIC_DOMAIN')
            if public_url and not public_url.startswith('http'):
                public_url = f'https://{public_url}'
            
            # Parse images from JSON or pipe-separated format
            if isinstance(self.images, str):
                try:
                    import json as json_module
                    parsed = json_module.loads(self.images)
                    images_list = parsed if isinstance(parsed, list) else [parsed]
                except:
                    images_list = self.images.split('|')
            else:
                images_list = self.images
            
            processed_urls = []
            for img in images_list:
                if not img:
                    continue
                    
                url = img.strip() if isinstance(img, str) else str(img)
                if not url or url.lower() == 'none':
                    continue
                
                # Convert local paths to public URLs
                if url.startswith('/uploads/') and public_url:
                    url = f'{public_url}{url}'
                elif url.startswith('uploads/') and public_url:
                    url = f'{public_url}/{url}'
                elif url.startswith('listings/') and public_url:
                    url = f'{public_url}/uploads/{url}'
                
                # Convert Google Drive URLs
                if url.startswith('http'):
                    url = convert_google_drive_url(url)
                    processed_urls.append(url)
                elif public_url and '/uploads/' in url:
                    # Local path converted to public URL
                    processed_urls.append(url)
            
            if processed_urls:
                pf_data['media'] = {
                    'images': [{'original': {'url': url}} for url in processed_urls]
                }
        
        # Media - Videos (auto-convert Google Drive URLs)
        if self.video_tour or self.video_360:
            if 'media' not in pf_data:
                pf_data['media'] = {}
            pf_data['media']['videos'] = {}
            if self.video_tour:
                pf_data['media']['videos']['default'] = convert_google_drive_url(self.video_tour)
            if self.video_360:
                pf_data['media']['videos']['view360'] = convert_google_drive_url(self.video_360)
        
        # Compliance (RERA/ADREC permit)
        if self.permit_number:
            emirate = pf_data.get('uaeEmirate', 'dubai')
            compliance_type = 'adrec' if emirate == 'abu_dhabi' else 'rera'
            pf_data['compliance'] = {
                'type': compliance_type,
                'listingAdvertisementNumber': self.permit_number
            }
        
        return pf_data


class PFSession(db.Model):
    """Store PropertyFinder browser session"""
    __tablename__ = 'pf_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    cookies = db.Column(db.Text)  # JSON serialized cookies
    user_agent = db.Column(db.String(500))
    logged_in = db.Column(db.Boolean, default=False)
    email = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PFCache(db.Model):
    """Cache PropertyFinder API data in database for fast access"""
    __tablename__ = 'pf_cache'
    
    id = db.Column(db.Integer, primary_key=True)
    cache_type = db.Column(db.String(50), nullable=False)  # 'listings', 'users', 'leads'
    data = db.Column(db.Text)  # JSON serialized data
    count = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @classmethod
    def get_cache(cls, cache_type):
        """Get cached data by type"""
        import json
        cache = cls.query.filter_by(cache_type=cache_type).first()
        if cache and cache.data:
            try:
                return json.loads(cache.data)
            except:
                return []
        return []
    
    @classmethod
    def set_cache(cls, cache_type, data):
        """Set cache data by type"""
        import json
        cache = cls.query.filter_by(cache_type=cache_type).first()
        if not cache:
            cache = cls(cache_type=cache_type)
            db.session.add(cache)
        
        cache.data = json.dumps(data, default=str)
        cache.count = len(data) if isinstance(data, list) else 1
        cache.updated_at = datetime.utcnow()
        db.session.commit()
        return cache
    
    @classmethod
    def get_last_update(cls, cache_type=None):
        """Get the last update time"""
        if cache_type:
            cache = cls.query.filter_by(cache_type=cache_type).first()
            return cache.updated_at if cache else None
        else:
            # Get the most recent update time across all cache types (for 'listings')
            cache = cls.query.filter_by(cache_type='listings').first()
            return cache.updated_at if cache else None
    
    @classmethod
    def get_all_cached_data(cls):
        """Get all cached data as a dictionary"""
        return {
            'listings': cls.get_cache('listings'),
            'users': cls.get_cache('users'),
            'leads': cls.get_cache('leads'),
            'last_updated': cls.get_last_update()
        }


# ==================== CRM: LEADS ====================

class Lead(db.Model):
    """Incoming leads from all sources: PropertyFinder, Bayut, Zapier, etc."""
    __tablename__ = 'crm_leads'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Source
    source = db.Column(db.String(30), default='other')  # propertyfinder, bayut, website, facebook, instagram, zapier, phone, email
    source_id = db.Column(db.String(100))  # External ID from source
    channel = db.Column(db.String(30))  # whatsapp, email, call, etc.
    
    # Contact Info
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))
    whatsapp = db.Column(db.String(50))
    
    # Inquiry
    message = db.Column(db.Text)
    listing_reference = db.Column(db.String(50))  # Related listing
    pf_listing_id = db.Column(db.String(50))  # PropertyFinder listing ID
    response_link = db.Column(db.String(500))  # PropertyFinder response link
    
    # Status: new, contacted, qualified, viewing, negotiation, won, lost, spam
    status = db.Column(db.String(30), default='new')
    pf_status = db.Column(db.String(30))  # Original PF status: sent, delivered, read, replied
    priority = db.Column(db.String(20), default='medium')  # low, medium, high, urgent
    lead_type = db.Column(db.String(20), default='for_sale')  # for_sale, for_rent
    
    # Assignment - from PropertyFinder (publicProfile)
    pf_agent_id = db.Column(db.String(50))  # PropertyFinder public profile ID
    pf_agent_name = db.Column(db.String(100))  # Agent name from PF
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Follow-up
    last_contact = db.Column(db.DateTime)
    next_follow_up = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    
    # Conversion
    customer_id = db.Column(db.Integer, db.ForeignKey('crm_customers.id'), nullable=True)
    converted_at = db.Column(db.DateTime)
    
    # Timestamps
    received_at = db.Column(db.DateTime)  # Actual date received in PF/source
    created_at = db.Column(db.DateTime, default=datetime.utcnow)  # When added to our system
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id])
    customer = db.relationship('Customer', back_populates='leads')
    
    def to_dict(self):
        return {
            'id': self.id,
            'source': self.source,
            'source_id': self.source_id,
            'channel': self.channel,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'whatsapp': self.whatsapp,
            'message': self.message,
            'listing_reference': self.listing_reference,
            'pf_listing_id': self.pf_listing_id,
            'response_link': self.response_link,
            'status': self.status,
            'pf_status': self.pf_status,
            'priority': self.priority,
            'lead_type': getattr(self, 'lead_type', None) or 'for_sale',
            'pf_agent_id': self.pf_agent_id,
            'pf_agent_name': self.pf_agent_name,
            'assigned_to_id': self.assigned_to_id,
            'assigned_to_name': self.assigned_to.name if self.assigned_to else None,
            'last_contact': self.last_contact.isoformat() if self.last_contact else None,
            'next_follow_up': self.next_follow_up.isoformat() if self.next_follow_up else None,
            'notes': self.notes,
            'customer_id': self.customer_id,
            'converted_at': self.converted_at.isoformat() if self.converted_at else None,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class LeadComment(db.Model):
    """Comments/notes on leads with timestamps and user attribution"""
    __tablename__ = 'lead_comments'
    
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('crm_leads.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    lead = db.relationship('Lead', backref=db.backref('comments', lazy='dynamic', cascade='all, delete-orphan'))
    user = db.relationship('User')
    
    def to_dict(self):
        return {
            'id': self.id,
            'lead_id': self.lead_id,
            'user_id': self.user_id,
            'user_name': self.user.name if self.user else 'System',
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ==================== CONTACTS ====================

class Contact(db.Model):
    """Saved contacts with phone numbers and country codes"""
    __tablename__ = 'contacts'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), nullable=False)  # Full phone with country code
    country_code = db.Column(db.String(10), default='+971')  # UAE default
    email = db.Column(db.String(120))
    company = db.Column(db.String(200))
    notes = db.Column(db.Text)
    tags = db.Column(db.String(500))  # comma-separated
    
    # Linked to lead (optional)
    lead_id = db.Column(db.Integer, db.ForeignKey('crm_leads.id'), nullable=True)
    
    # Created by
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    lead = db.relationship('Lead', backref=db.backref('contacts', lazy='dynamic'))
    created_by = db.relationship('User')
    
    # Common country codes
    COUNTRY_CODES = [
        ('+971', 'UAE'),
        ('+966', 'Saudi Arabia'),
        ('+973', 'Bahrain'),
        ('+974', 'Qatar'),
        ('+965', 'Kuwait'),
        ('+968', 'Oman'),
        ('+20', 'Egypt'),
        ('+91', 'India'),
        ('+92', 'Pakistan'),
        ('+63', 'Philippines'),
        ('+44', 'UK'),
        ('+1', 'USA/Canada'),
        ('+86', 'China'),
        ('+7', 'Russia'),
        ('+33', 'France'),
        ('+49', 'Germany'),
    ]
    
    def get_full_phone(self):
        """Get phone with country code"""
        if self.phone.startswith('+'):
            return self.phone
        return f"{self.country_code}{self.phone.lstrip('0')}"
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone,
            'country_code': self.country_code,
            'full_phone': self.get_full_phone(),
            'email': self.email,
            'company': self.company,
            'notes': self.notes,
            'tags': self.tags.split(',') if self.tags else [],
            'lead_id': self.lead_id,
            'created_by_id': self.created_by_id,
            'created_by_name': self.created_by.name if self.created_by else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


# ==================== CRM: CUSTOMERS ====================

class Customer(db.Model):
    """Customer/prospect for CRM"""
    __tablename__ = 'crm_customers'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Identity
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True)
    phone = db.Column(db.String(50))
    whatsapp = db.Column(db.String(50))
    nationality = db.Column(db.String(50))
    
    # Type: buyer, seller, tenant, landlord, investor
    customer_type = db.Column(db.String(20), default='buyer')
    status = db.Column(db.String(20), default='prospect')  # prospect, active, inactive, vip
    
    # Preferences
    interested_in = db.Column(db.String(20))  # sale, rent
    min_budget = db.Column(db.Float)
    max_budget = db.Column(db.Float)
    preferred_locations = db.Column(db.Text)  # JSON array
    
    # Assignment
    assigned_agent_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Stats
    total_leads = db.Column(db.Integer, default=0)
    total_viewings = db.Column(db.Integer, default=0)
    
    # Notes
    notes = db.Column(db.Text)
    tags = db.Column(db.String(500))  # comma-separated
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_contact = db.Column(db.DateTime)
    
    # Relationships
    leads = db.relationship('Lead', back_populates='customer')
    assigned_agent = db.relationship('User', foreign_keys=[assigned_agent_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'whatsapp': self.whatsapp,
            'nationality': self.nationality,
            'customer_type': self.customer_type,
            'status': self.status,
            'interested_in': self.interested_in,
            'min_budget': self.min_budget,
            'max_budget': self.max_budget,
            'preferred_locations': self.preferred_locations,
            'assigned_agent_id': self.assigned_agent_id,
            'assigned_agent_name': self.assigned_agent.name if self.assigned_agent else None,
            'total_leads': self.total_leads,
            'total_viewings': self.total_viewings,
            'notes': self.notes,
            'tags': self.tags.split(',') if self.tags else [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_contact': self.last_contact.isoformat() if self.last_contact else None,
        }


# ==================== APP SETTINGS ====================

class AppSettings(db.Model):
    """Application settings stored in database"""
    __tablename__ = 'app_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Default settings
    DEFAULTS = {
        'sync_interval_minutes': '30',
        'auto_sync_enabled': 'true',
        'default_agent_email': '',
        'default_owner_email': '',
        'default_insights_agent_id': '',  # PF user ID to show by default in insights
        'last_sync_at': '',
        'first_run_completed': 'false',
        # Lead CRM settings - JSON arrays
        'lead_statuses': '[{"id":"new","label":"New","color":"blue"},{"id":"contacted","label":"Contacted","color":"yellow"},{"id":"qualified","label":"Qualified","color":"green"},{"id":"viewing","label":"Viewing","color":"purple"},{"id":"negotiation","label":"Negotiation","color":"orange"},{"id":"won","label":"Won","color":"emerald"},{"id":"lost","label":"Lost","color":"red"},{"id":"spam","label":"Spam","color":"gray"}]',
        'lead_sources': '[{"id":"propertyfinder","label":"PropertyFinder","color":"red"},{"id":"bayut","label":"Bayut","color":"blue"},{"id":"website","label":"Website","color":"purple"},{"id":"facebook","label":"Facebook","color":"indigo"},{"id":"instagram","label":"Instagram","color":"pink"},{"id":"whatsapp","label":"WhatsApp","color":"green"},{"id":"phone","label":"Phone","color":"gray"},{"id":"email","label":"Email","color":"cyan"},{"id":"referral","label":"Referral","color":"amber"},{"id":"zapier","label":"Zapier","color":"orange"},{"id":"other","label":"Other","color":"gray"}]',
        # Image processing settings
        'image_default_ratio': 'landscape_16_9',
        'image_default_size': 'full_hd',
        'image_max_dimension': '1920',
        'image_quality': '90',
        'image_format': 'JPEG',
        'image_qr_enabled': 'true',
        'image_qr_data': '',              # Default QR data (URL, etc.)
        'image_qr_position': 'bottom_right',
        'image_qr_size_percent': '12',
        'image_qr_color': '#000000',
        'image_qr_opacity': '1.0',
        'image_logo_enabled': 'false',
        'image_logo_data': '',            # Base64 encoded logo
        'image_logo_position': 'bottom_left',
        'image_logo_size_percent': '10',
        'image_logo_opacity': '0.9',
    }
    
    @classmethod
    def get(cls, key, default=None):
        """Get a setting value"""
        setting = cls.query.filter_by(key=key).first()
        if setting:
            return setting.value
        return default if default is not None else cls.DEFAULTS.get(key, '')
    
    @classmethod
    def set(cls, key, value):
        """Set a setting value"""
        setting = cls.query.filter_by(key=key).first()
        if not setting:
            setting = cls(key=key)
            db.session.add(setting)
        setting.value = str(value) if value is not None else ''
        db.session.commit()
        return setting
    
    @classmethod
    def get_all(cls):
        """Get all settings as dictionary"""
        settings = {}
        for key, default in cls.DEFAULTS.items():
            settings[key] = cls.get(key, default)
        return settings
    
    @classmethod
    def init_defaults(cls):
        """Initialize default settings if not exist"""
        for key, default in cls.DEFAULTS.items():
            if not cls.query.filter_by(key=key).first():
                cls.set(key, default)


# ==================== LISTING LOOP SYSTEM ====================

class LoopConfig(db.Model):
    """Configuration for a listing loop (auto-duplicate/republish)"""
    __tablename__ = 'loop_configs'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    
    # Loop type: 'duplicate' = create copy & publish, 'delete_republish' = delete from PF & republish
    loop_type = db.Column(db.String(20), default='duplicate')
    
    # Timing
    interval_hours = db.Column(db.Float, default=1.0)  # Hours between each action
    
    # Duplicate handling
    keep_duplicates = db.Column(db.Boolean, default=True)  # Keep in "Duplicated" folder
    max_duplicates = db.Column(db.Integer, default=0)  # 0 = unlimited
    
    # Status
    is_active = db.Column(db.Boolean, default=False)
    is_paused = db.Column(db.Boolean, default=False)
    
    # Execution tracking
    current_index = db.Column(db.Integer, default=0)  # Current position in listing sequence
    consecutive_failures = db.Column(db.Integer, default=0)  # For auto-stop logic
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_run_at = db.Column(db.DateTime, nullable=True)
    next_run_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    listings = db.relationship('LoopListing', backref='loop_config', lazy='dynamic', cascade='all, delete-orphan')
    duplicates = db.relationship('DuplicatedListing', backref='loop_config', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'loop_type': self.loop_type,
            'interval_hours': self.interval_hours,
            'keep_duplicates': self.keep_duplicates,
            'max_duplicates': self.max_duplicates,
            'is_active': self.is_active,
            'is_paused': self.is_paused,
            'current_index': self.current_index,
            'consecutive_failures': self.consecutive_failures,
            'listing_count': self.listings.count(),
            'duplicate_count': self.duplicates.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_run_at': self.last_run_at.isoformat() if self.last_run_at else None,
            'next_run_at': self.next_run_at.isoformat() if self.next_run_at else None,
        }
    
    def get_next_listing(self):
        """Get the next listing in the sequence"""
        listings = self.listings.order_by(LoopListing.order_index).all()
        if not listings:
            return None
        
        # Wrap around if at end
        index = self.current_index % len(listings)
        return listings[index]
    
    def advance_index(self):
        """Move to next listing in sequence"""
        count = self.listings.count()
        if count > 0:
            self.current_index = (self.current_index + 1) % count
        db.session.commit()


class LoopListing(db.Model):
    """A listing assigned to a loop"""
    __tablename__ = 'loop_listings'
    
    id = db.Column(db.Integer, primary_key=True)
    loop_config_id = db.Column(db.Integer, db.ForeignKey('loop_configs.id'), nullable=False)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    
    # Order in the sequence (for multi-listing loops)
    order_index = db.Column(db.Integer, default=0)
    
    # Tracking
    last_processed_at = db.Column(db.DateTime, nullable=True)
    times_processed = db.Column(db.Integer, default=0)
    consecutive_failures = db.Column(db.Integer, default=0)
    
    # Relationship to the actual listing
    listing = db.relationship('LocalListing', backref='loop_assignments')
    
    def to_dict(self):
        return {
            'id': self.id,
            'loop_config_id': self.loop_config_id,
            'listing_id': self.listing_id,
            'order_index': self.order_index,
            'last_processed_at': self.last_processed_at.isoformat() if self.last_processed_at else None,
            'times_processed': self.times_processed,
            'listing': {
                'id': self.listing.id,
                'reference': self.listing.reference,
                'title': self.listing.title_en,
                'status': self.listing.status,
            } if self.listing else None
        }


class DuplicatedListing(db.Model):
    """Track duplicated listings created by loops"""
    __tablename__ = 'duplicated_listings'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Reference to original listing
    original_listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    
    # The duplicate listing created (stored in our DB)
    duplicate_listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=True)
    
    # PropertyFinder listing ID for the duplicate
    pf_listing_id = db.Column(db.String(100), nullable=True)
    
    # Which loop created this
    loop_config_id = db.Column(db.Integer, db.ForeignKey('loop_configs.id'), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    published_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)  # When deleted from PF
    
    # Status
    status = db.Column(db.String(20), default='created')  # created, published, deleted
    
    # Relationships
    original_listing = db.relationship('LocalListing', foreign_keys=[original_listing_id], backref='duplicates_created')
    duplicate_listing = db.relationship('LocalListing', foreign_keys=[duplicate_listing_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'original_listing_id': self.original_listing_id,
            'duplicate_listing_id': self.duplicate_listing_id,
            'pf_listing_id': self.pf_listing_id,
            'loop_config_id': self.loop_config_id,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'original': {
                'reference': self.original_listing.reference,
                'title': self.original_listing.title_en,
            } if self.original_listing else None
        }


class LoopExecutionLog(db.Model):
    """Log of loop executions for debugging and monitoring"""
    __tablename__ = 'loop_execution_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    loop_config_id = db.Column(db.Integer, db.ForeignKey('loop_configs.id'), nullable=False)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=True)
    
    # Execution details
    action = db.Column(db.String(50))  # 'duplicate', 'delete_republish', 'cleanup', 'error'
    success = db.Column(db.Boolean, default=False)
    message = db.Column(db.Text, nullable=True)
    pf_listing_id = db.Column(db.String(100), nullable=True)
    
    # Timestamps
    executed_at = db.Column(db.DateTime, default=datetime.utcnow)
    duration_ms = db.Column(db.Integer, nullable=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'loop_config_id': self.loop_config_id,
            'listing_id': self.listing_id,
            'action': self.action,
            'success': self.success,
            'message': self.message,
            'pf_listing_id': self.pf_listing_id,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'duration_ms': self.duration_ms
        }
