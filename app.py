#!/usr/bin/env python3
"""
PropertyFinder Dashboard - Web UI for managing listings
"""
import os
import sys
import json
from pathlib import Path
from functools import wraps
from datetime import datetime, timedelta

# Get the src directory
ROOT_DIR = Path(__file__).parent
SRC_DIR = ROOT_DIR / 'src'

# Add src directory to path
sys.path.insert(0, str(SRC_DIR))

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, g
from werkzeug.utils import secure_filename

from api import PropertyFinderClient, PropertyFinderAPIError, Config
from models import PropertyListing, PropertyType, OfferingType, Location, Price
from utils import BulkListingManager
from database import db, LocalListing, PFSession, User, PFCache, AppSettings, ListingFolder
from images import ImageProcessor

# Setup paths for templates and static files
TEMPLATE_DIR = SRC_DIR / 'dashboard' / 'templates'
STATIC_DIR = SRC_DIR / 'dashboard' / 'static'

# Production settings
IS_PRODUCTION = os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('PRODUCTION', 'false').lower() == 'true'

# Public URL for external access (needed for PropertyFinder media URLs)
# Set APP_PUBLIC_URL in Railway environment variables to your domain
APP_PUBLIC_URL = os.environ.get('APP_PUBLIC_URL') or os.environ.get('RAILWAY_PUBLIC_DOMAIN')
if APP_PUBLIC_URL and not APP_PUBLIC_URL.startswith('http'):
    APP_PUBLIC_URL = f'https://{APP_PUBLIC_URL}'
print(f"[STARTUP] APP_PUBLIC_URL: {APP_PUBLIC_URL or 'NOT SET - local images will NOT work with PropertyFinder!'}")

# Storage Configuration - Use Railway Volume in production
RAILWAY_VOLUME_PATH = Path('/data')
if IS_PRODUCTION and RAILWAY_VOLUME_PATH.exists():
    # Use Railway Volume for persistent storage
    UPLOAD_FOLDER = RAILWAY_VOLUME_PATH / 'uploads'
    LISTING_IMAGES_FOLDER = RAILWAY_VOLUME_PATH / 'uploads' / 'listings'
    print(f"[STARTUP] Using Railway Volume at: {RAILWAY_VOLUME_PATH}")
else:
    # Local development storage
    UPLOAD_FOLDER = ROOT_DIR / 'uploads'
    LISTING_IMAGES_FOLDER = ROOT_DIR / 'uploads' / 'listings'
    print(f"[STARTUP] Using local storage at: {UPLOAD_FOLDER}")

# Ensure upload directories exist
try:
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    LISTING_IMAGES_FOLDER.mkdir(parents=True, exist_ok=True)
    (UPLOAD_FOLDER / 'logos').mkdir(parents=True, exist_ok=True)
    (UPLOAD_FOLDER / 'processed').mkdir(parents=True, exist_ok=True)
    print(f"[STARTUP] Upload directories created/verified")
except Exception as e:
    print(f"[STARTUP] Warning: Could not create upload directories: {e}")

DATABASE_PATH = ROOT_DIR / 'data' / 'listings.db'

# Database Configuration - Use PostgreSQL in production if DATABASE_URL is set
DATABASE_URL = os.environ.get('DATABASE_URL')

print(f"[STARTUP] Production mode: {IS_PRODUCTION}")
print(f"[STARTUP] DATABASE_URL set: {bool(DATABASE_URL)}")

# Ensure data directory exists (only for SQLite)
if not DATABASE_URL:
    print(f"[STARTUP] Using SQLite at: {DATABASE_PATH}")
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"[STARTUP] Data directory created/verified")
    except Exception as e:
        print(f"[STARTUP] Warning: Could not create data directory: {e}")

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Add a simple ping endpoint before any database setup
@app.route('/ping')
def ping():
    return 'pong', 200

@app.route('/favicon.ico')
def favicon():
    return '', 204  # No content

if DATABASE_URL:
    print(f"[STARTUP] Using PostgreSQL database")
    # Railway PostgreSQL fix: replace postgres:// with postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_PATH}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

# Initialize database
db.init_app(app)

# Create tables and default admin user
with app.app_context():
    from sqlalchemy import text, inspect
    
    # Run migrations BEFORE create_all - add missing columns to existing tables
    print("[MIGRATION] Checking for required migrations...")
    
    try:
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()
        print(f"[MIGRATION] Existing tables: {existing_tables}")
    except Exception as e:
        print(f"[MIGRATION] Error inspecting tables: {e}")
        existing_tables = []
    
    # Migration: Add folder_id column to listings table if it doesn't exist
    if 'listings' in existing_tables:
        try:
            columns = [col['name'] for col in inspector.get_columns('listings')]
            print(f"[MIGRATION] Listings table columns: {columns}")
            
            if 'folder_id' not in columns:
                print("[MIGRATION] Adding folder_id column to listings table...")
                try:
                    with db.engine.connect() as conn:
                        # Use PostgreSQL-compatible syntax with IF NOT EXISTS workaround
                        # PostgreSQL doesn't support IF NOT EXISTS for ADD COLUMN, so we catch the error
                        conn.execute(text('ALTER TABLE listings ADD COLUMN folder_id INTEGER NULL'))
                        conn.commit()
                    print("[MIGRATION] ✓ Added folder_id column to listings table")
                except Exception as alter_error:
                    error_str = str(alter_error).lower()
                    if 'already exists' in error_str or 'duplicate column' in error_str:
                        print("[MIGRATION] folder_id column already exists (caught duplicate error)")
                    else:
                        print(f"[MIGRATION] ERROR adding column: {alter_error}")
                        raise
            else:
                print("[MIGRATION] folder_id column already exists")
        except Exception as e:
            print(f"[MIGRATION] Error checking/adding folder_id: {e}")
            # Try to add it anyway with raw SQL that handles duplicates
            try:
                with db.engine.connect() as conn:
                    # For PostgreSQL, use DO block to handle IF NOT EXISTS
                    conn.execute(text("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns 
                                WHERE table_name = 'listings' AND column_name = 'folder_id'
                            ) THEN
                                ALTER TABLE listings ADD COLUMN folder_id INTEGER NULL;
                            END IF;
                        END $$;
                    """))
                    conn.commit()
                print("[MIGRATION] ✓ Added folder_id column using PostgreSQL DO block")
            except Exception as do_error:
                print(f"[MIGRATION] DO block also failed: {do_error}")
    else:
        print("[MIGRATION] listings table does not exist yet, will be created by create_all()")
    
    try:
        print("[STARTUP] Creating database tables...")
        db.create_all()
        print("[STARTUP] Database tables created successfully")
        
        # Initialize default settings
        AppSettings.init_defaults()
        
        # Set defaults from .env if not already set in DB
        if not AppSettings.get('default_agent_email'):
            AppSettings.set('default_agent_email', Config.DEFAULT_AGENT_EMAIL)
        if not AppSettings.get('default_owner_email'):
            AppSettings.set('default_owner_email', Config.DEFAULT_OWNER_EMAIL)
        
        # Create default admin user if no users exist
        if User.query.count() == 0:
            admin_email = os.environ.get('ADMIN_EMAIL', 'admin@listings.local')
            admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
            admin = User(
                email=admin_email,
                name='Administrator',
                role='admin',
                is_active=True
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            db.session.commit()
            print(f"[STARTUP] Created default admin user: {admin_email}")
        
        print("[STARTUP] Database initialization complete")
        
        # Skip auto-sync in production to avoid slow startup
        # Users can manually sync from the dashboard
        if not IS_PRODUCTION:
            first_run = AppSettings.get('first_run_completed') != 'true'
            if first_run and Config.validate():
                print("\n🔄 First run detected - syncing PropertyFinder data...")
                try:
                    client = PropertyFinderClient()
                    
                    # Fetch listings
                    all_listings = []
                    page = 1
                    while True:
                        result = client.get_listings(page=page, per_page=50)
                        listings = result.get('results', [])
                        if not listings:
                            break
                        all_listings.extend(listings)
                        if page >= result.get('pagination', {}).get('totalPages', 1):
                            break
                        page += 1
                        if page > 50:  # Support up to 2500 listings
                            break
                    PFCache.set_cache('listings', all_listings)
                    print(f"   ✓ Synced {len(all_listings)} listings")
                    
                    # Fetch users
                    try:
                        users_result = client.get_users(per_page=50)
                        users = users_result.get('data', [])
                        PFCache.set_cache('users', users)
                        print(f"   ✓ Synced {len(users)} users")
                    except:
                        pass
                    
                    # Fetch leads
                    try:
                        leads_result = client.get_leads(per_page=100)
                        leads = leads_result.get('results', [])
                        PFCache.set_cache('leads', leads)
                        print(f"   ✓ Synced {len(leads)} leads")
                    except:
                        pass
                    
                    AppSettings.set('first_run_completed', 'true')
                    AppSettings.set('last_sync_at', datetime.now().isoformat())
                    print("   ✓ First run sync complete!\n")
                except Exception as e:
                    print(f"   ⚠ First run sync failed: {e}\n")
        else:
            print("✓ Production mode: Skipping auto-sync on startup")
    except Exception as e:
        print(f"⚠ Database initialization error: {e}")
        # Don't crash - let the app start anyway


# ==================== GLOBAL ERROR HANDLER ====================

@app.errorhandler(Exception)
def handle_exception(e):
    """Log all unhandled exceptions"""
    import traceback
    print(f"[ERROR] Unhandled exception: {e}")
    traceback.print_exc()
    return jsonify({'error': str(e)}), 500


# ==================== AUTHENTICATION ====================

def get_current_user():
    """Get the currently logged-in user"""
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None


def login_required(f):
    """Decorator to require login for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login', next=request.url))
        
        user = User.query.get(session['user_id'])
        if not user or not user.is_active:
            session.clear()
            flash('Your session has expired. Please log in again.', 'warning')
            return redirect(url_for('login'))
        
        g.user = user
        return f(*args, **kwargs)
    return decorated_function


def permission_required(permission):
    """Decorator to require a specific permission"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in to access this page.', 'warning')
                return redirect(url_for('login', next=request.url))
            
            user = User.query.get(session['user_id'])
            if not user or not user.is_active:
                session.clear()
                flash('Your session has expired. Please log in again.', 'warning')
                return redirect(url_for('login'))
            
            if not user.has_permission(permission):
                flash(f'You do not have permission to access this feature.', 'error')
                return redirect(url_for('index'))
            
            g.user = user
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.before_request
def load_user():
    """Load user before each request"""
    g.user = None
    if 'user_id' in session:
        g.user = User.query.get(session['user_id'])


@app.context_processor
def inject_user():
    """Make user available in all templates"""
    return dict(current_user=g.user)


# ==================== CACHE ====================
# In-memory cache for PropertyFinder data (backed by DB for persistence)
_pf_cache = {
    'listings': None,  # None = not loaded yet, [] = empty
    'users': None,
    'leads': None,
    'locations': None,
    'credits': None,
    'last_updated': None,
    'cache_duration': 1800,  # 30 minutes in seconds (was 5 min)
    'db_loaded': False,  # Track if we've loaded from DB
}

def get_cached_listings():
    """Get cached listings - lazy load from DB"""
    global _pf_cache
    if _pf_cache['listings'] is None:
        # Load from DB cache
        db_data = PFCache.get_cache('listings')
        _pf_cache['listings'] = db_data if db_data else []
        _pf_cache['last_updated'] = PFCache.get_last_update('listings')
        _pf_cache['db_loaded'] = True
        print(f"[Cache] Loaded {len(_pf_cache['listings'])} listings from DB cache")
    return _pf_cache['listings']

def get_cached_users():
    """Get cached users - lazy load from DB"""
    global _pf_cache
    if _pf_cache['users'] is None:
        _pf_cache['users'] = PFCache.get_cache('users') or []
    return _pf_cache['users']

def get_cached_leads():
    """Get cached leads - lazy load from DB"""
    global _pf_cache
    if _pf_cache['leads'] is None:
        _pf_cache['leads'] = PFCache.get_cache('leads') or []
    return _pf_cache['leads']

def get_cached_locations():
    """Get cached locations - lazy load from DB"""
    global _pf_cache
    if _pf_cache['locations'] is None:
        cached = PFCache.get_cache('locations')
        _pf_cache['locations'] = cached if isinstance(cached, dict) else {}
    return _pf_cache['locations']

def build_location_map(listings, force_refresh=False):
    """Build a map of location IDs to names - uses cache only, no API calls for performance"""
    global _pf_cache
    
    # Get existing location cache from DB
    location_map = get_cached_locations()
    
    # If we have any cache, just return it - don't make slow API calls
    if location_map and not force_refresh:
        return location_map
    
    # Only fetch locations if explicitly refreshing or cache is empty
    if not force_refresh:
        return location_map
    
    # Get all unique location IDs from listings that are missing
    missing_ids = set()
    for l in listings:
        loc_id = l.get('location', {}).get('id')
        if loc_id and str(loc_id) not in location_map:
            missing_ids.add(loc_id)
    
    if not missing_ids:
        return location_map
    
    print(f"[Locations] Fetching {len(missing_ids)} missing location(s)...")
    
    # Fetch location names from API - limit to 3 quick searches only
    try:
        client = get_client()
        # Only search the main emirates to avoid too many API calls
        search_terms = ['Dubai', 'Abu Dhabi', 'Sharjah']
        
        for term in search_terms[:3]:  # Limit to 3 searches max
            if not missing_ids:
                break
            try:
                result = client.get_locations(search=term, per_page=100)
                for loc in result.get('data', []):
                    loc_id = str(loc.get('id'))
                    if loc_id not in location_map:
                        tree = loc.get('tree', [])
                        if tree:
                            names = [t.get('name', '') for t in tree]
                            location_map[loc_id] = ' > '.join(names)
                        else:
                            location_map[loc_id] = loc.get('name', f'Location {loc_id}')
                        missing_ids.discard(int(loc_id))
            except Exception:
                pass
        
        # Save to cache
        _pf_cache['locations'] = location_map
        PFCache.set_cache('locations', location_map)
        
    except Exception as e:
        print(f"Error building location map: {e}")
    
    return location_map

def load_cache_from_db():
    """Load cached data from database on first access - LAZY loading"""
    global _pf_cache
    # Only load last_updated, not the actual data
    if _pf_cache['last_updated'] is None:
        _pf_cache['last_updated'] = PFCache.get_last_update()

def get_cached_pf_data(force_refresh=False, quick_load=False):
    """Get PropertyFinder data with caching (DB-backed)
    
    Args:
        force_refresh: If True, fetch fresh data from API
        quick_load: If True, only fetch first page of listings (faster)
    """
    global _pf_cache
    
    # Load timestamp from DB on first access
    load_cache_from_db()
    
    # Check if cache is valid (don't refresh if we have data and it's recent)
    if not force_refresh and _pf_cache['last_updated']:
        age = (datetime.now() - _pf_cache['last_updated']).total_seconds()
        # Use lazy loading - only load listings when needed
        cached_listings = get_cached_listings()
        if age < _pf_cache['cache_duration'] and cached_listings:
            # Load the rest lazily for return
            return {
                'listings': cached_listings,
                'users': get_cached_users(),
                'leads': get_cached_leads(),
                'credits': _pf_cache['credits'],
                'last_updated': _pf_cache['last_updated'],
                'error': None
            }
    
    # If we have data from DB but it's older, return it immediately (background refresh later)
    has_cached_data = len(get_cached_listings()) > 0
    
    # Fetch fresh data
    try:
        client = get_client()
        
        # Fetch listings (paginated) - increased limits for large portfolios
        all_listings = []
        page = 1
        max_pages = 10 if quick_load else 50  # Quick: 500 listings, Full: 2500 listings max
        
        while page <= max_pages:
            result = client.get_listings(page=page, per_page=50)
            listings = result.get('results', [])
            if not listings:
                break
            all_listings.extend(listings)
            
            pagination = result.get('pagination', {})
            if page >= pagination.get('totalPages', 1):
                break
            page += 1
        
        _pf_cache['listings'] = all_listings
        PFCache.set_cache('listings', all_listings)
        
        # Fetch users (single page only)
        try:
            users_result = client.get_users(per_page=50)
            _pf_cache['users'] = users_result.get('data', [])
            PFCache.set_cache('users', _pf_cache['users'])
        except:
            pass
        
        # Fetch leads - limit to 2 pages max for performance
        if not quick_load:
            try:
                all_leads = []
                leads_page = 1
                max_leads_pages = 2  # 100 leads max
                
                while leads_page <= max_leads_pages:
                    leads_result = client.get_leads(page=leads_page, per_page=50)
                    leads = leads_result.get('data', [])
                    if not leads:
                        break
                    all_leads.extend(leads)
                    
                    leads_pagination = leads_result.get('pagination', {})
                    if leads_page >= leads_pagination.get('totalPages', 1):
                        break
                    leads_page += 1
                
                _pf_cache['leads'] = all_leads
                PFCache.set_cache('leads', all_leads)
                
                # Sync leads to CRM (in background ideally)
                sync_pf_leads_to_db(all_leads)
            except:
                _pf_cache['leads'] = []
        
        # Skip credits fetch for performance (fetch on-demand if needed)
        
        _pf_cache['last_updated'] = datetime.now()
        _pf_cache['error'] = None
        AppSettings.set('last_sync_at', datetime.now().isoformat())
        
    except PropertyFinderAPIError as e:
        _pf_cache['error'] = f"API Error: {e.message}"
        if has_cached_data:
            return _pf_cache
    except Exception as e:
        _pf_cache['error'] = f"Error: {str(e)}"
        if has_cached_data:
            return _pf_cache
    
    return _pf_cache


def sync_pf_leads_to_db(pf_leads):
    """Sync PropertyFinder leads to CRM database"""
    from database import Lead
    from dateutil import parser as date_parser
    
    imported = 0
    updated = 0
    for pf_lead in pf_leads:
        try:
            pf_id = str(pf_lead.get('id', ''))
            if not pf_id:
                continue
            
            # Check if already exists
            existing = Lead.query.filter_by(source='propertyfinder', source_id=pf_id).first()
            
            # Extract contact info from sender - new structure has contacts array
            sender = pf_lead.get('sender', {})
            contacts = sender.get('contacts', [])
            
            # Extract phone/email from contacts array
            phone = ''
            email = ''
            whatsapp = ''
            for contact in contacts:
                if contact.get('type') == 'phone':
                    phone = contact.get('value', '')
                    # If channel is whatsapp, this is also the whatsapp number
                    if pf_lead.get('channel') == 'whatsapp':
                        whatsapp = phone
                elif contact.get('type') == 'email':
                    email = contact.get('value', '')
            
            # Get agent info from publicProfile
            public_profile = pf_lead.get('publicProfile', {})
            
            # Parse received date
            received_at = None
            if pf_lead.get('createdAt'):
                try:
                    received_at = date_parser.parse(pf_lead.get('createdAt'))
                except:
                    pass
            
            # Get listing info
            listing_info = pf_lead.get('listing', {})
            listing_id = str(listing_info.get('id', ''))
            listing_ref = listing_info.get('reference', listing_id)
            
            if existing:
                # Update existing lead with new PF status if changed
                if existing.pf_status != pf_lead.get('status'):
                    existing.pf_status = pf_lead.get('status', '')
                    existing.response_link = pf_lead.get('responseLink', '')
                    updated += 1
                continue
            
            # Create new lead
            lead = Lead(
                source='propertyfinder',
                source_id=pf_id,
                channel=pf_lead.get('channel', ''),
                name=sender.get('name', 'Unknown'),
                email=email,
                phone=phone,
                whatsapp=whatsapp,
                message=pf_lead.get('message', ''),
                listing_reference=listing_ref,
                pf_listing_id=listing_id,
                response_link=pf_lead.get('responseLink', ''),
                status='new',
                pf_status=pf_lead.get('status', ''),
                priority='medium',
                pf_agent_id=str(public_profile.get('id', '')),
                pf_agent_name=public_profile.get('name', ''),
                received_at=received_at
            )
            db.session.add(lead)
            imported += 1
        except Exception as e:
            print(f"Error importing lead {pf_lead.get('id')}: {e}")
            continue
    
    if imported > 0 or updated > 0:
        try:
            db.session.commit()
            if imported > 0:
                print(f"✓ Synced {imported} new leads to database")
            if updated > 0:
                print(f"✓ Updated {updated} existing leads")
        except Exception as e:
            db.session.rollback()
            print(f"Error committing leads: {e}")

# Configuration
ALLOWED_EXTENSIONS = {'json', 'csv'}
UPLOAD_FOLDER.mkdir(exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max for image processing


# Global error handler for unhandled exceptions
@app.errorhandler(Exception)
def handle_exception(e):
    """Handle unhandled exceptions"""
    import traceback
    print(f"[ERROR] Unhandled exception: {type(e).__name__}: {e}")
    traceback.print_exc()
    
    # Check if it's an API request
    if request.path.startswith('/api/'):
        return jsonify({
            'error': f'{type(e).__name__}: {str(e)}',
            'success': False
        }), 500
    
    # For non-API requests, show error page or redirect
    flash(f'An error occurred: {str(e)}', 'error')
    return redirect(url_for('index'))


def get_client():
    """Get PropertyFinder API client"""
    return PropertyFinderClient()


def generate_reference_id():
    """Generate a unique reference ID for listings"""
    import uuid
    import time
    # Format: REF-YYYYMMDD-XXXXX (e.g., REF-20251227-A3B4C)
    date_part = time.strftime('%Y%m%d')
    unique_part = uuid.uuid4().hex[:5].upper()
    return f"REF-{date_part}-{unique_part}"


def convert_google_drive_url(url):
    """
    Convert Google Drive share links to direct CDN URLs.
    
    Supports formats:
    - https://drive.google.com/file/d/FILE_ID/view?usp=sharing
    - https://drive.google.com/open?id=FILE_ID
    - https://drive.google.com/uc?id=FILE_ID
    
    Returns CDN URL: https://lh3.googleusercontent.com/d/FILE_ID
    This format is more reliable for external services like PropertyFinder.
    """
    import re
    
    if not url:
        return url
    
    url = url.strip()
    
    # Already a CDN URL
    if 'lh3.googleusercontent.com' in url:
        return url
    
    # Not a Google Drive URL
    if 'drive.google.com' not in url:
        return url
    
    # Pattern 1: /file/d/FILE_ID/
    match = re.search(r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        return f"https://lh3.googleusercontent.com/d/{file_id}"
    
    # Pattern 2: ?id=FILE_ID or &id=FILE_ID
    match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        return f"https://lh3.googleusercontent.com/d/{file_id}"
    
    # Could not extract ID, return as-is
    return url


def process_image_urls(images_input):
    """
    Process image URLs from form input.
    Handles:
    - JSON array (new format from image manager)
    - Newline-separated text (legacy)
    - Pipe-separated text (legacy)
    
    Converts Google Drive links to direct URLs.
    Returns JSON string of URLs (new format).
    """
    if not images_input:
        return '[]'
    
    urls = []
    
    # Try JSON first (new format)
    try:
        parsed = json.loads(images_input)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    url = item.strip()
                elif isinstance(item, dict):
                    url = item.get('url', '')
                else:
                    continue
                
                if url and url.lower() != 'none':
                    # Convert Google Drive links
                    if 'drive.google.com' in url:
                        url = convert_google_drive_url(url)
                    urls.append(url)
            return json.dumps(urls)
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Fall back to text format (legacy)
    for line in images_input.replace('|', '\n').split('\n'):
        url = line.strip()
        if url and url.lower() != 'none' and (url.startswith('http://') or url.startswith('https://') or url.startswith('/')):
            # Convert Google Drive links
            if 'drive.google.com' in url:
                url = convert_google_drive_url(url)
            urls.append(url)
    
    return json.dumps(urls)


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def api_error_handler(f):
    """Decorator to handle API errors"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except PropertyFinderAPIError as e:
            if request.is_json or request.headers.get('Accept') == 'application/json':
                return jsonify({'error': e.message, 'status_code': e.status_code}), e.status_code or 500
            flash(f'API Error: {e.message}', 'error')
            return redirect(request.referrer or url_for('index'))
        except Exception as e:
            if request.is_json or request.headers.get('Accept') == 'application/json':
                return jsonify({'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'error')
            return redirect(request.referrer or url_for('index'))
    return decorated_function


def transform_api_listing_to_local(api_listing):
    """
    Transform PropertyFinder API listing response to local database field format.
    This allows the listing_form.html template to work with API data.
    """
    # Extract price info
    price = None
    price_type = None
    if api_listing.get('price'):
        price_obj = api_listing['price']
        price_type = price_obj.get('type', 'sale')
        amounts = price_obj.get('amounts', {})
        # Get the appropriate price based on type
        price = amounts.get(price_type) or amounts.get('sale') or amounts.get('yearly')
    
    # Extract location info
    location = ''
    if api_listing.get('location'):
        loc = api_listing['location']
        if isinstance(loc, dict):
            location = loc.get('fullName', {}).get('en', '') if isinstance(loc.get('fullName'), dict) else str(loc.get('id', ''))
    
    # Extract images
    images = []
    if api_listing.get('media', {}).get('images'):
        for img in api_listing['media']['images']:
            if isinstance(img, dict):
                url = img.get('medium', {}).get('url') or img.get('original', {}).get('url')
                if url:
                    images.append(url)
    
    return {
        'id': api_listing.get('id'),
        'reference': api_listing.get('reference', ''),
        'emirate': api_listing.get('uaeEmirate', ''),
        'city': '',  # Not directly available in API response
        'location': location,
        'category': api_listing.get('category', ''),
        'offering_type': price_type if price_type in ['sale', 'rent'] else ('rent' if price_type in ['yearly', 'monthly', 'weekly', 'daily'] else 'sale'),
        'property_type': api_listing.get('type', ''),
        'bedrooms': api_listing.get('bedrooms', ''),
        'bathrooms': api_listing.get('bathrooms', ''),
        'size': api_listing.get('size'),
        'furnishing_type': api_listing.get('furnishingType', ''),
        'project_status': api_listing.get('projectStatus', ''),
        'parking_slots': api_listing.get('parkingSlots'),
        'floor_number': api_listing.get('floorNumber', ''),
        'price': price,
        'downpayment': api_listing.get('price', {}).get('downPayment', 0) if api_listing.get('price') else 0,
        'rent_frequency': price_type if price_type in ['yearly', 'monthly', 'weekly', 'daily'] else '',
        'title_en': api_listing.get('title', {}).get('en', '') if isinstance(api_listing.get('title'), dict) else '',
        'title_ar': api_listing.get('title', {}).get('ar', '') if isinstance(api_listing.get('title'), dict) else '',
        'description_en': api_listing.get('description', {}).get('en', '') if isinstance(api_listing.get('description'), dict) else '',
        'description_ar': api_listing.get('description', {}).get('ar', '') if isinstance(api_listing.get('description'), dict) else '',
        'images': images,
        'video_tour': api_listing.get('media', {}).get('videos', {}).get('default', ''),
        'video_360': api_listing.get('media', {}).get('videos', {}).get('view360', ''),
        'amenities': api_listing.get('amenities', []),
        'assigned_agent': api_listing.get('assignedTo', {}).get('name', '') if api_listing.get('assignedTo') else '',
        'developer': api_listing.get('developer', ''),
        'status': 'live' if api_listing.get('portals', {}).get('propertyfinder', {}).get('isLive') else 'draft',
        'pf_listing_id': api_listing.get('id'),
        'created_at': api_listing.get('createdAt'),
    }


# ==================== AUTH PAGES ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if g.user:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been deactivated. Contact an administrator.', 'error')
                return render_template('login.html')
            
            # Log in the user
            session.clear()
            session['user_id'] = user.id
            if remember:
                session.permanent = True
            
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            flash(f'Welcome back, {user.name}!', 'success')
            
            # Redirect to next page or index
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password.', 'error')
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout the user"""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/users')
@permission_required('manage_users')
def users_page():
    """User management page"""
    users = User.query.order_by(User.created_at.desc()).all()
    pf_users = PFCache.get_cache('users') or []
    return render_template('users.html', 
                           users=[u.to_dict() for u in users], 
                           roles=User.ROLES,
                           all_permissions=User.ALL_PERMISSIONS,
                           pf_users=pf_users)


@app.route('/users/create', methods=['POST'])
@permission_required('manage_users')
def create_user():
    """Create a new user"""
    email = request.form.get('email', '').strip().lower()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'viewer')
    pf_agent_id = request.form.get('pf_agent_id', '').strip() or None
    pf_agent_name = request.form.get('pf_agent_name', '').strip() or None
    use_custom_permissions = request.form.get('use_custom_permissions') == 'on'
    
    if not email or not name or not password:
        flash('All fields are required.', 'error')
        return redirect(url_for('users_page'))
    
    if User.query.filter_by(email=email).first():
        flash('A user with this email already exists.', 'error')
        return redirect(url_for('users_page'))
    
    if role not in User.ROLES:
        flash('Invalid role selected.', 'error')
        return redirect(url_for('users_page'))
    
    user = User(email=email, name=name, role=role, pf_agent_id=pf_agent_id, pf_agent_name=pf_agent_name)
    user.set_password(password)
    
    # Set custom permissions if enabled
    if use_custom_permissions:
        custom_perms = request.form.getlist('custom_permissions')
        user.set_custom_permissions(custom_perms)
    
    db.session.add(user)
    db.session.commit()
    
    flash(f'User "{name}" created successfully.', 'success')
    return redirect(url_for('users_page'))


@app.route('/users/<int:user_id>/edit', methods=['POST'])
@permission_required('manage_users')
def edit_user(user_id):
    """Edit a user"""
    user = User.query.get_or_404(user_id)
    
    # Prevent editing the last admin
    if user.role == 'admin' and User.query.filter_by(role='admin', is_active=True).count() == 1:
        if request.form.get('role') != 'admin' or request.form.get('is_active') == 'false':
            flash('Cannot demote or deactivate the last admin.', 'error')
            return redirect(url_for('users_page'))
    
    user.name = request.form.get('name', user.name).strip()
    user.role = request.form.get('role', user.role)
    user.is_active = request.form.get('is_active') != 'false'
    user.pf_agent_id = request.form.get('pf_agent_id', '').strip() or None
    user.pf_agent_name = request.form.get('pf_agent_name', '').strip() or None
    
    # Handle custom permissions
    use_custom_permissions = request.form.get('use_custom_permissions') == 'on'
    if use_custom_permissions:
        custom_perms = request.form.getlist('custom_permissions')
        user.set_custom_permissions(custom_perms)
    else:
        user.set_custom_permissions(None)  # Clear custom permissions, use role defaults
    
    # Update password if provided
    new_password = request.form.get('password', '').strip()
    if new_password:
        user.set_password(new_password)
    
    db.session.commit()
    flash(f'User "{user.name}" updated successfully.', 'success')
    return redirect(url_for('users_page'))


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@permission_required('manage_users')
def delete_user(user_id):
    """Delete a user"""
    user = User.query.get_or_404(user_id)
    
    # Prevent self-deletion
    if user.id == g.user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('users_page'))
    
    # Prevent deleting the last admin
    if user.role == 'admin' and User.query.filter_by(role='admin', is_active=True).count() == 1:
        flash('Cannot delete the last admin.', 'error')
        return redirect(url_for('users_page'))
    
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User "{user.name}" deleted.', 'success')
    return redirect(url_for('users_page'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """User profile page"""
    if request.method == 'POST':
        g.user.name = request.form.get('name', g.user.name).strip()
        
        # Update password if provided
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        
        if new_password:
            if not g.user.check_password(current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('profile'))
            g.user.set_password(new_password)
            flash('Password updated successfully.', 'success')
        
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile'))
    
    return render_template('profile.html')


# ==================== PAGES ====================

@app.route('/')
@login_required
def index():
    """Dashboard home page"""
    try:
        # Get local stats
        stats = {
            'total': LocalListing.query.count(),
            'published': LocalListing.query.filter_by(status='published').count(),
            'draft': LocalListing.query.filter_by(status='draft').count(),
        }
        recent = LocalListing.query.order_by(LocalListing.updated_at.desc()).limit(5).all()
        return render_template('index.html', stats=stats, recent_listings=[l.to_dict() for l in recent])
    except Exception as e:
        print(f"[ERROR] Index route error: {e}")
        import traceback
        traceback.print_exc()
        raise


@app.route('/listings')
@login_required
def listings():
    """List all listings page - uses local database"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    status = request.args.get('status')
    
    query = LocalListing.query
    if status:
        query = query.filter_by(status=status)
    
    query = query.order_by(LocalListing.updated_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('listings.html', 
                         listings=[l.to_dict() for l in pagination.items],
                         pagination={
                             'current_page': pagination.page,
                             'last_page': pagination.pages,
                             'per_page': per_page,
                             'total': pagination.total
                         },
                         page=page,
                         per_page=per_page)

@app.route('/listings/new')
@permission_required('create')
def new_listing():
    """New listing form page"""
    property_types = [
        {'code': pt.value, 'name': pt.name.replace('_', ' ').title()} 
        for pt in PropertyType
    ]
    return render_template('listing_form.html', 
                         listing=None, 
                         property_types=property_types,
                         edit_mode=False)


@app.route('/listings/<listing_id>')
@login_required
@api_error_handler
def view_listing(listing_id):
    """View single listing page - checks local DB first, then PropertyFinder API"""
    # Try local database first (for integer IDs)
    try:
        local_id = int(listing_id)
        local_listing = LocalListing.query.get(local_id)
        if local_listing:
            return render_template('listing_detail.html', listing=local_listing.to_dict())
    except (ValueError, TypeError):
        pass  # Not an integer ID, try API
    
    # Try PropertyFinder API
    client = get_client()
    listing = client.get_listing(listing_id)
    return render_template('listing_detail.html', listing=listing.get('data', listing))


@app.route('/listings/<listing_id>/edit')
@permission_required('edit')
@api_error_handler
def edit_listing(listing_id):
    """Edit listing form page - checks local DB first, then PropertyFinder API"""
    property_types = [
        {'code': pt.value, 'name': pt.name.replace('_', ' ').title()} 
        for pt in PropertyType
    ]
    
    # Try local database first (for integer IDs)
    try:
        local_id = int(listing_id)
        local_listing = LocalListing.query.get(local_id)
        if local_listing:
            return render_template('listing_form.html', 
                                 listing=local_listing.to_dict(),
                                 property_types=property_types,
                                 edit_mode=True)
    except (ValueError, TypeError):
        pass  # Not an integer ID, try API
    
    # Try PropertyFinder API
    client = get_client()
    listing = client.get_listing(listing_id)
    # Transform API response to local field names for form compatibility
    api_listing = listing.get('data', listing)
    transformed = transform_api_listing_to_local(api_listing)
    return render_template('listing_form.html', 
                         listing=transformed,
                         property_types=property_types,
                         edit_mode=True)


@app.route('/bulk')
@permission_required('bulk_upload')
def bulk_upload():
    """Bulk upload page"""
    # Get settings for defaults
    app_settings = AppSettings.get_all()
    return render_template('bulk_upload.html', defaults={
        'agent_email': Config.DEFAULT_AGENT_EMAIL,
        'owner_email': Config.DEFAULT_OWNER_EMAIL,
        'agent_id': app_settings.get('default_pf_agent_id', '')
    })


@app.route('/insights')
@login_required
def insights():
    """Insights and analytics page - loads without API calls, data fetched on demand"""
    # Get local listings only (no API call)
    local_listings = LocalListing.query.all()
    local_data = [listing.to_dict() for listing in local_listings]
    
    # Return empty PF data - user will load on demand
    return render_template('insights.html', 
                         pf_listings=[],
                         local_listings=local_data,
                         users=[],
                         leads=[],
                         credits=None,
                         error_message=None,
                         cache_age=None,
                         data_loaded=False)


@app.route('/api/pf/refresh', methods=['POST'])
def api_refresh_pf_data():
    """API: Force refresh PropertyFinder data cache"""
    cache = get_cached_pf_data(force_refresh=True)
    return jsonify({
        'success': cache.get('error') is None,
        'listings_count': len(cache['listings']),
        'users_count': len(cache['users']),
        'error': cache.get('error'),
        'cached_at': cache['last_updated'].isoformat() if cache['last_updated'] else None
    })


@app.route('/api/pf/insights', methods=['GET'])
def api_pf_insights():
    """API: Get all PropertyFinder data for insights page (on-demand loading)"""
    user_id = request.args.get('user_id')
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    
    # FAST PATH: Always try cache first (DB-backed, survives restarts)
    cached_listings = get_cached_listings()  # Loads from DB if not in memory
    cached_users = get_cached_users()
    cached_leads = get_cached_leads()
    
    # If we have cached data and not forcing refresh, return immediately (no API calls)
    if cached_listings and not force_refresh:
        print(f"[Insights] Returning {len(cached_listings)} cached listings (no API call)")
        cache = {
            'listings': cached_listings,
            'users': cached_users,
            'leads': cached_leads,
            'last_updated': _pf_cache['last_updated'],
            'from_cache': True
        }
    elif force_refresh:
        # User explicitly requested refresh - fetch from API
        print(f"[Insights] Force refresh requested, fetching from API...")
        cache = get_cached_pf_data(force_refresh=True, quick_load=False)
    else:
        # No cache at all - do quick initial load
        print(f"[Insights] No cache found, doing quick API load...")
        cache = get_cached_pf_data(force_refresh=True, quick_load=True)
    
    listings = cache.get('listings', [])
    leads = cache.get('leads', [])
    
    # Get cached location map (no API calls - just use what we have)
    location_map = get_cached_locations()
    
    # Filter by user if specified
    if user_id:
        user_id = int(user_id)
        listings = [l for l in listings if 
                   l.get('publicProfile', {}).get('id') == user_id or
                   l.get('assignedTo', {}).get('id') == user_id]
        leads = [l for l in leads if 
                l.get('publicProfile', {}).get('id') == user_id]
    
    return jsonify({
        'success': cache.get('error') is None or len(listings) > 0,
        'listings': listings,
        'users': cache.get('users', []),
        'leads': leads,
        'locations': location_map,
        'error': cache.get('error') if not listings else None,
        'cached_at': cache['last_updated'].isoformat() if cache.get('last_updated') else None,
        'from_cache': not force_refresh
    })


@app.route('/api/pf/locations/refresh', methods=['POST'])
def api_refresh_locations():
    """API: Refresh location cache from API (on-demand)"""
    listings = get_cached_listings()
    location_map = build_location_map(listings, force_refresh=True)
    return jsonify({
        'success': True,
        'count': len(location_map),
        'locations': location_map
    })


@app.route('/api/pf/users', methods=['GET'])
def api_pf_users():
    """API: Get PropertyFinder users (lightweight, for agent dropdown)"""
    try:
        client = get_client()
        users_result = client.get_users(per_page=50)
        users = users_result.get('data', [])
        return jsonify({
            'success': True,
            'users': users
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'users': [],
            'error': str(e)
        })


@app.route('/api/pf/listings', methods=['GET'])
def api_pf_listings():
    """API: Get cached PropertyFinder listings"""
    cache = get_cached_pf_data()
    return jsonify({
        'listings': cache['listings'],
        'count': len(cache['listings']),
        'cached_at': cache['last_updated'].isoformat() if cache['last_updated'] else None
    })


@app.route('/settings')
@permission_required('settings')
def settings():
    """Settings page"""
    # Get PF users for default agent dropdown
    pf_users = PFCache.get_cache('users') or []
    app_settings = AppSettings.get_all()
    
    return render_template('settings.html', 
        config={
            'api_base_url': Config.API_BASE_URL,
            'has_api_key': bool(Config.API_KEY),
            'has_api_secret': bool(Config.API_SECRET),
            'has_legacy_token': bool(Config.API_TOKEN),
            'agency_id': Config.AGENCY_ID,
            'debug': Config.DEBUG,
            'bulk_batch_size': Config.BULK_BATCH_SIZE,
            'bulk_delay': Config.BULK_DELAY_SECONDS,
            'default_agent_email': app_settings.get('default_agent_email', Config.DEFAULT_AGENT_EMAIL),
            'default_owner_email': app_settings.get('default_owner_email', Config.DEFAULT_OWNER_EMAIL),
        },
        app_settings=app_settings,
        pf_users=pf_users
    )


@app.route('/api/settings', methods=['GET'])
@login_required
def api_get_settings():
    """API: Get all app settings"""
    settings = AppSettings.get_all()
    last_sync = PFCache.get_last_update()
    return jsonify({
        'success': True,
        'settings': settings,
        'last_sync': last_sync.isoformat() if last_sync else None
    })


@app.route('/api/settings', methods=['POST'])
@permission_required('settings')
def api_update_settings():
    """API: Update app settings"""
    data = request.get_json()
    
    allowed_keys = ['sync_interval_minutes', 'auto_sync_enabled', 'default_agent_email', 
                    'default_owner_email', 'default_pf_agent_id']
    
    for key in allowed_keys:
        if key in data:
            AppSettings.set(key, data[key])
    
    return jsonify({'success': True, 'settings': AppSettings.get_all()})


@app.route('/api/sync', methods=['POST'])
@login_required
def api_manual_sync():
    """API: Trigger manual sync of PropertyFinder data"""
    try:
        client = get_client()
        
        # Fetch listings
        all_listings = []
        page = 1
        while True:
            result = client.get_listings(page=page, per_page=50)
            listings = result.get('results', [])
            if not listings:
                break
            all_listings.extend(listings)
            if page >= result.get('pagination', {}).get('totalPages', 1):
                break
            page += 1
            if page > 50:  # Support up to 2500 listings
                break
        PFCache.set_cache('listings', all_listings)
        
        # Fetch users
        users = []
        try:
            users_result = client.get_users(per_page=50)
            users = users_result.get('data', [])
            PFCache.set_cache('users', users)
        except:
            pass
        
        # Fetch leads
        leads = []
        try:
            leads_result = client.get_leads(per_page=100)
            leads = leads_result.get('results', [])
            PFCache.set_cache('leads', leads)
        except:
            pass
        
        AppSettings.set('last_sync_at', datetime.now().isoformat())
        
        return jsonify({
            'success': True,
            'listings_count': len(all_listings),
            'users_count': len(users),
            'leads_count': len(leads),
            'synced_at': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== API ENDPOINTS ====================

@app.route('/api/listings', methods=['GET'])
@api_error_handler
def api_get_listings():
    """API: Get all listings"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    
    client = get_client()
    result = client.get_listings(page=page, per_page=per_page)
    return jsonify(result)


@app.route('/api/listings', methods=['POST'])
@api_error_handler
def api_create_listing():
    """API: Create a new listing"""
    data = request.get_json()
    
    client = get_client()
    result = client.create_listing(data)
    
    return jsonify({'success': True, 'data': result}), 201


@app.route('/api/listings/<listing_id>', methods=['GET'])
@api_error_handler
def api_get_listing(listing_id):
    """API: Get a single listing"""
    client = get_client()
    result = client.get_listing(listing_id)
    return jsonify(result)


@app.route('/api/listings/<listing_id>', methods=['PUT', 'PATCH'])
@api_error_handler
def api_update_listing(listing_id):
    """API: Update a listing"""
    data = request.get_json()
    
    client = get_client()
    if request.method == 'PUT':
        result = client.update_listing(listing_id, data)
    else:
        result = client.patch_listing(listing_id, data)
    
    return jsonify({'success': True, 'data': result})


@app.route('/api/listings/<listing_id>', methods=['DELETE'])
@api_error_handler
def api_delete_listing(listing_id):
    """API: Delete a listing"""
    client = get_client()
    result = client.delete_listing(listing_id)
    return jsonify({'success': True, 'message': 'Listing deleted'})


@app.route('/api/listings/<listing_id>/publish', methods=['POST'])
@api_error_handler
def api_publish_listing(listing_id):
    """API: Publish a listing"""
    client = get_client()
    
    # Check if this is a local listing ID (integer)
    try:
        local_id = int(listing_id)
        local_listing = LocalListing.query.get(local_id)
        if local_listing:
            if local_listing.pf_listing_id:
                # Check if PF listing still exists
                try:
                    pf_listing = client.get_listing(local_listing.pf_listing_id)
                    if not pf_listing or not pf_listing.get('id'):
                        # PF listing doesn't exist anymore, need to create new one
                        local_listing.pf_listing_id = None
                        db.session.commit()
                except:
                    # PF listing not found, clear the ID
                    local_listing.pf_listing_id = None
                    db.session.commit()
            
            if not local_listing.pf_listing_id:
                # Need to create on PF first
                listing_data = local_listing.to_pf_format()
                
                # Validate required fields
                missing = []
                if not listing_data.get('title'):
                    missing.append('Title')
                if not listing_data.get('description'):
                    missing.append('Description')
                if not listing_data.get('price'):
                    missing.append('Price')
                if not listing_data.get('location'):
                    missing.append('Location')
                if not listing_data.get('assignedTo'):
                    missing.append('Assigned Agent')
                if not listing_data.get('bedrooms'):
                    missing.append('Bedrooms')
                if not listing_data.get('bathrooms'):
                    missing.append('Bathrooms')
                
                if missing:
                    error_msg = f"Cannot publish. Missing required fields: {', '.join(missing)}"
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'success': False, 'error': error_msg}), 400
                    flash(error_msg, 'error')
                    return redirect(url_for('edit_listing', listing_id=listing_id))
                
                # Create on PF
                try:
                    result = client.create_listing(listing_data)
                    pf_id = result.get('id')
                    if pf_id:
                        local_listing.pf_listing_id = pf_id
                        db.session.commit()
                    else:
                        error_msg = f"Failed to create listing on PropertyFinder: {result}"
                        if request.is_json or request.headers.get('Accept') == 'application/json':
                            return jsonify({'success': False, 'error': error_msg}), 400
                        flash(error_msg, 'error')
                        return redirect(url_for('view_listing', listing_id=listing_id))
                except Exception as e:
                    error_msg = f"Failed to create listing on PropertyFinder: {str(e)}"
                    if request.is_json or request.headers.get('Accept') == 'application/json':
                        return jsonify({'success': False, 'error': error_msg}), 400
                    flash(error_msg, 'error')
                    return redirect(url_for('view_listing', listing_id=listing_id))
            
            # Now publish
            try:
                result = client.publish_listing(local_listing.pf_listing_id)
                local_listing.status = 'pending'
                db.session.commit()
                
                if request.is_json or request.headers.get('Accept') == 'application/json':
                    return jsonify({'success': True, 'data': result, 'pf_listing_id': local_listing.pf_listing_id})
                flash(f'Publish request submitted for listing {local_listing.pf_listing_id}', 'success')
                return redirect(url_for('view_listing', listing_id=listing_id))
            except PropertyFinderAPIError as e:
                error_msg = f"PropertyFinder rejected publish request: {e.message}"
                if request.is_json or request.headers.get('Accept') == 'application/json':
                    return jsonify({'success': False, 'error': error_msg}), e.status_code or 400
                flash(error_msg, 'error')
                return redirect(url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        pass
    
    # Direct PF listing ID
    result = client.publish_listing(listing_id)
    return jsonify({'success': True, 'data': result})


@app.route('/api/listings/<listing_id>/unpublish', methods=['POST'])
@api_error_handler
def api_unpublish_listing(listing_id):
    """API: Unpublish a listing"""
    client = get_client()
    
    # Check if this is a local listing ID (integer)
    try:
        local_id = int(listing_id)
        local_listing = LocalListing.query.get(local_id)
        if local_listing and local_listing.pf_listing_id:
            # Use the PF listing ID instead
            result = client.unpublish_listing(local_listing.pf_listing_id)
            local_listing.status = 'draft'
            db.session.commit()
            return jsonify({'success': True, 'data': result})
    except (ValueError, TypeError):
        pass
    
    result = client.unpublish_listing(listing_id)
    return jsonify({'success': True, 'data': result})


@app.route('/api/bulk/upload', methods=['POST'])
@api_error_handler
def api_bulk_upload():
    """API: Bulk upload listings from file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Use JSON or CSV'}), 400
    
    filename = secure_filename(file.filename)
    filepath = Path(app.config['UPLOAD_FOLDER']) / filename
    file.save(str(filepath))
    
    publish = request.form.get('publish', 'false').lower() == 'true'
    
    client = get_client()
    manager = BulkListingManager(client)
    
    try:
        if filename.endswith('.json'):
            result = manager.create_listings_from_json(str(filepath), publish=publish)
        else:
            result = manager.create_listings_from_csv(str(filepath), publish=publish)
        
        # Clean up uploaded file
        filepath.unlink()
        
        return jsonify({
            'success': True,
            'total': result.total,
            'successful': result.successful,
            'failed': result.failed,
            'results': result.results,
            'errors': result.errors
        })
    except Exception as e:
        filepath.unlink(missing_ok=True)
        raise


@app.route('/api/bulk/create', methods=['POST'])
@api_error_handler
def api_bulk_create():
    """API: Bulk create listings from JSON array"""
    data = request.get_json()
    listings = data.get('listings', [])
    publish = data.get('publish', False)
    
    if not listings:
        return jsonify({'error': 'No listings provided'}), 400
    
    client = get_client()
    manager = BulkListingManager(client)
    result = manager.create_listings_from_list(listings, publish=publish)
    
    return jsonify({
        'success': True,
        'total': result.total,
        'successful': result.successful,
        'failed': result.failed,
        'results': result.results,
        'errors': result.errors
    })


@app.route('/api/reference/<ref_type>', methods=['GET'])
@api_error_handler
def api_reference_data(ref_type):
    """API: Get reference data"""
    client = get_client()
    
    if ref_type == 'property-types':
        result = client.get_property_types()
    elif ref_type == 'locations':
        query = request.args.get('q', '')
        result = client.get_locations(query)
    elif ref_type == 'amenities':
        result = client.get_amenities()
    elif ref_type == 'agents':
        result = client.get_agents()
    else:
        return jsonify({'error': 'Unknown reference type'}), 400
    
    return jsonify(result)


@app.route('/api/account', methods=['GET'])
@api_error_handler
def api_account():
    """API: Get account info"""
    client = get_client()
    result = client.get_account()
    return jsonify(result)


@app.route('/api/config', methods=['GET'])
def api_get_config():
    """API: Get current configuration status (without secrets)"""
    has_key = bool(Config.API_KEY)
    has_secret = bool(Config.API_SECRET)
    
    # Show partial key/secret for verification
    key_preview = Config.API_KEY[:8] + '••••••' if Config.API_KEY else '••••••••'
    secret_preview = Config.API_SECRET[:8] + '••••••' if Config.API_SECRET else '••••••••'
    
    return jsonify({
        'has_api_key': has_key,
        'has_api_secret': has_secret,
        'api_key_preview': key_preview,
        'api_secret_preview': secret_preview,
        'api_base_url': Config.API_BASE_URL
    })


@app.route('/api/test-connection', methods=['GET', 'POST'])
def api_test_connection():
    """API: Test the Enterprise API connection"""
    try:
        client = get_client()
        result = client.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e),
            'base_url': Config.API_BASE_URL
        })


@app.route('/api/users', methods=['GET'])
@api_error_handler
def api_get_users():
    """API: Get users (agents) from PF"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('perPage', 15, type=int)
    
    client = get_client()
    result = client.get_users(page=page, per_page=per_page)
    return jsonify(result)


@app.route('/api/locations', methods=['GET'])
@api_error_handler
def api_get_locations():
    """API: Search locations"""
    search = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    
    client = get_client()
    result = client.get_locations(search=search, page=page)
    return jsonify(result)


@app.route('/api/credits', methods=['GET'])
@api_error_handler
def api_get_credits():
    """API: Get credits info"""
    client = get_client()
    result = client.get_credits()
    return jsonify(result)


# ==================== FORM HANDLERS ====================

@app.route('/listings/create', methods=['POST'])
@api_error_handler
def create_listing_form():
    """Handle listing creation form submission - saves locally first"""
    form = request.form
    
    # Auto-generate reference if not provided
    reference = form.get('reference')
    if not reference or not reference.strip():
        reference = generate_reference_id()
    
    # Create local listing first
    local_listing = LocalListing(
        emirate=form.get('emirate'),
        city=form.get('city'),
        category=form.get('category'),
        offering_type=form.get('offering_type'),
        property_type=form.get('property_type'),
        location=form.get('location'),
        location_id=form.get('location_id') if form.get('location_id') else None,
        assigned_agent=form.get('assigned_agent'),
        reference=reference,
        bedrooms=form.get('bedrooms'),
        bathrooms=form.get('bathrooms'),
        size=float(form.get('size')) if form.get('size') else None,
        parking_slots=int(form.get('parking_slots')) if form.get('parking_slots') else None,
        furnishing_type=form.get('furnishing_type'),
        project_status=form.get('project_status'),
        floor_number=form.get('floor_number'),
        unit_number=form.get('unit_number'),
        price=float(form.get('price')) if form.get('price') else None,
        downpayment=float(form.get('downpayment')) if form.get('downpayment') else None,
        rent_frequency=form.get('rent_frequency'),
        title_en=form.get('title_en'),
        title_ar=form.get('title_ar'),
        description_en=form.get('description_en'),
        description_ar=form.get('description_ar'),
        video_tour=convert_google_drive_url(form.get('video_tour')) if form.get('video_tour') else None,
        video_360=convert_google_drive_url(form.get('video_360')) if form.get('video_360') else None,
        permit_number=form.get('permit_number'),
        owner_name=form.get('owner_name'),
        developer=form.get('developer'),
        status='draft'
    )
    
    # Handle images (auto-convert Google Drive links)
    images = form.get('images', '')
    if images:
        local_listing.images = process_image_urls(images)
    
    # Handle amenities
    amenities = form.getlist('amenities')
    local_listing.amenities = ','.join(amenities) if amenities else ''
    
    db.session.add(local_listing)
    db.session.commit()
    
    flash('Listing saved as draft!', 'success')
    return redirect(url_for('view_listing', listing_id=local_listing.id))


@app.route('/listings/<listing_id>/update', methods=['POST'])
@api_error_handler
def update_listing_form(listing_id):
    """Handle listing update form submission"""
    
    # Check if this is a local listing first
    try:
        local_id = int(listing_id)
        local_listing = LocalListing.query.get(local_id)
        if local_listing:
            # Update local listing
            form = request.form
            
            local_listing.emirate = form.get('emirate')
            local_listing.city = form.get('city')
            local_listing.category = form.get('category')
            local_listing.offering_type = form.get('offering_type')
            local_listing.property_type = form.get('property_type')
            local_listing.location = form.get('location')
            local_listing.location_id = form.get('location_id') if form.get('location_id') else None
            local_listing.assigned_agent = form.get('assigned_agent')
            local_listing.reference = form.get('reference')
            
            # Specifications
            local_listing.bedrooms = form.get('bedrooms')
            local_listing.bathrooms = form.get('bathrooms')
            local_listing.size = float(form.get('size')) if form.get('size') else None
            local_listing.parking_slots = int(form.get('parking_slots')) if form.get('parking_slots') else None
            local_listing.furnishing_type = form.get('furnishing_type')
            local_listing.project_status = form.get('project_status')
            local_listing.floor_number = form.get('floor_number')
            local_listing.unit_number = form.get('unit_number')
            
            # Price
            local_listing.price = float(form.get('price')) if form.get('price') else None
            local_listing.downpayment = float(form.get('downpayment')) if form.get('downpayment') else None
            local_listing.rent_frequency = form.get('rent_frequency')
            
            # Content
            local_listing.title_en = form.get('title_en')
            local_listing.title_ar = form.get('title_ar')
            local_listing.description_en = form.get('description_en')
            local_listing.description_ar = form.get('description_ar')
            
            # Media (auto-convert Google Drive links)
            images = form.get('images', '')
            if images:
                local_listing.images = process_image_urls(images)
            local_listing.video_tour = convert_google_drive_url(form.get('video_tour')) if form.get('video_tour') else None
            local_listing.video_360 = convert_google_drive_url(form.get('video_360')) if form.get('video_360') else None
            
            # Amenities
            amenities = form.getlist('amenities')
            local_listing.amenities = ','.join(amenities) if amenities else ''
            
            # Compliance
            local_listing.permit_number = form.get('permit_number')
            
            # Other
            local_listing.owner_name = form.get('owner_name')
            local_listing.developer = form.get('developer')
            
            db.session.commit()
            
            flash('Listing updated successfully!', 'success')
            return redirect(url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        pass  # Not an integer ID, continue with PF listing
    
    # PropertyFinder listing
    data = build_listing_from_form(request.form)
    
    client = get_client()
    result = client.update_listing(listing_id, data)
    
    flash('Listing updated successfully!', 'success')
    return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/delete', methods=['POST'])
@api_error_handler
def delete_listing_form(listing_id):
    """Handle listing deletion"""
    client = get_client()
    client.delete_listing(listing_id)
    
    flash('Listing deleted successfully!', 'success')
    return redirect(url_for('listings'))


@app.route('/listings/<listing_id>/duplicate', methods=['POST'])
@login_required
def duplicate_listing(listing_id):
    """Duplicate an existing listing with a new reference ID"""
    try:
        local_id = int(listing_id)
        original = LocalListing.query.get(local_id)
        
        if not original:
            flash('Listing not found', 'error')
            return redirect(url_for('listings'))
        
        # Create a new listing with copied data
        new_listing = LocalListing(
            # Property basics
            reference=generate_reference_id(),  # New reference
            category=original.category,
            offering_type=original.offering_type,
            property_type=original.property_type,
            
            # Location
            emirate=original.emirate,
            city=original.city,
            location=original.location,
            location_id=original.location_id,
            
            # Property details
            bedrooms=original.bedrooms,
            bathrooms=original.bathrooms,
            size=original.size,
            furnishing_type=original.furnishing_type,
            project_status=original.project_status,
            parking_slots=original.parking_slots,
            floor_number=original.floor_number,
            unit_number=original.unit_number,
            
            # Price
            price=original.price,
            downpayment=original.downpayment,
            rent_frequency=original.rent_frequency,
            
            # Content
            title_en=original.title_en,
            title_ar=original.title_ar,
            description_en=original.description_en,
            description_ar=original.description_ar,
            
            # Media
            images=original.images,
            video_tour=original.video_tour,
            video_360=original.video_360,
            
            # Amenities
            amenities=original.amenities,
            
            # Assignment
            assigned_agent=original.assigned_agent,
            owner_id=original.owner_id,
            owner_name=original.owner_name,
            
            # Other
            developer=original.developer,
            permit_number=None,  # Clear permit - needs new one
            available_from=original.available_from,
            
            # Status
            status='draft',  # Always start as draft
            pf_listing_id=None  # Not synced yet
        )
        
        db.session.add(new_listing)
        db.session.commit()
        
        flash(f'Listing duplicated successfully! New reference: {new_listing.reference}', 'success')
        return redirect(url_for('edit_listing', listing_id=new_listing.id))
        
    except (ValueError, TypeError):
        flash('Can only duplicate local listings', 'error')
        return redirect(url_for('view_listing', listing_id=listing_id))
    except Exception as e:
        flash(f'Error duplicating listing: {str(e)}', 'error')
        return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/send-to-pf', methods=['POST'])
@login_required
def send_to_pf_draft(listing_id):
    """Send listing to PropertyFinder as draft (without publishing)"""
    try:
        local_id = int(listing_id)
        local_listing = LocalListing.query.get(local_id)
        
        if not local_listing:
            flash('Listing not found', 'error')
            return redirect(url_for('listings'))
        
        # Check if already on PF
        if local_listing.pf_listing_id:
            flash(f'Listing already on PropertyFinder (ID: {local_listing.pf_listing_id})', 'info')
            return redirect(url_for('view_listing', listing_id=listing_id))
        
        client = get_client()
        
        # Build listing data
        listing_data = local_listing.to_pf_format()
        
        # Check for required fields
        missing_fields = []
        if not listing_data.get('title'):
            missing_fields.append('Title (English or Arabic)')
        if not listing_data.get('description'):
            missing_fields.append('Description (English or Arabic)')
        if not listing_data.get('price'):
            missing_fields.append('Price')
        if not listing_data.get('location'):
            missing_fields.append('Location ID (use location search)')
        if not listing_data.get('assignedTo'):
            missing_fields.append('Assigned Agent')
        if not listing_data.get('uaeEmirate'):
            missing_fields.append('Emirate')
        if not listing_data.get('bedrooms'):
            missing_fields.append('Bedrooms')
        if not listing_data.get('bathrooms'):
            missing_fields.append('Bathrooms')
        
        if missing_fields:
            flash(f'Cannot send to PF. Missing required fields: {", ".join(missing_fields)}', 'error')
            return redirect(url_for('edit_listing', listing_id=listing_id))
        
        # Create on PropertyFinder as draft
        try:
            result = client.create_listing(listing_data)
            pf_listing_id = result.get('id')
            
            if not pf_listing_id:
                error_msg = result.get('error') or result.get('message') or str(result)
                flash(f'PropertyFinder rejected the listing: {error_msg}', 'error')
                return redirect(url_for('view_listing', listing_id=listing_id))
            
            local_listing.pf_listing_id = str(pf_listing_id)
            local_listing.status = 'pf_draft'  # On PF as draft
            db.session.commit()
            
            flash(f'Listing sent to PropertyFinder as draft! PF ID: {pf_listing_id}', 'success')
            return redirect(url_for('view_listing', listing_id=listing_id))
            
        except PropertyFinderAPIError as e:
            flash(f'Failed to create on PropertyFinder: {e.message}', 'error')
            return redirect(url_for('view_listing', listing_id=listing_id))
        except Exception as e:
            flash(f'Failed to create on PropertyFinder: {str(e)}', 'error')
            return redirect(url_for('view_listing', listing_id=listing_id))
            
    except (ValueError, TypeError):
        flash('Can only send local listings to PF draft', 'error')
        return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/publish', methods=['POST'])
@api_error_handler
def publish_listing_form(listing_id):
    """Handle listing publish from web form"""
    # Check if this is a local listing
    try:
        local_id = int(listing_id)
        local_listing = LocalListing.query.get(local_id)
        if local_listing:
            client = get_client()
            
            # Check if already synced to PropertyFinder
            if local_listing.pf_listing_id:
                # Verify PF listing still exists
                try:
                    pf_listing = client.get_listing(local_listing.pf_listing_id)
                    if not pf_listing or not pf_listing.get('id'):
                        # PF listing doesn't exist anymore
                        local_listing.pf_listing_id = None
                        db.session.commit()
                        flash('PropertyFinder listing no longer exists. Creating new one...', 'warning')
                except:
                    # PF listing not found
                    local_listing.pf_listing_id = None
                    db.session.commit()
                    flash('PropertyFinder listing not found. Creating new one...', 'warning')
            
            # If no PF listing ID, need to create first
            if not local_listing.pf_listing_id:
                # Build listing data from local listing
                listing_data = local_listing.to_pf_format()
                
                # Check for required fields
                missing_fields = []
                if not listing_data.get('title'):
                    missing_fields.append('Title (English or Arabic)')
                if not listing_data.get('description'):
                    missing_fields.append('Description (English or Arabic)')
                if not listing_data.get('price'):
                    missing_fields.append('Price')
                if not listing_data.get('location'):
                    missing_fields.append('Location ID (use location search)')
                if not listing_data.get('assignedTo'):
                    missing_fields.append('Assigned Agent')
                if not listing_data.get('uaeEmirate'):
                    missing_fields.append('Emirate')
                if not listing_data.get('bedrooms'):
                    missing_fields.append('Bedrooms')
                if not listing_data.get('bathrooms'):
                    missing_fields.append('Bathrooms')
                
                if missing_fields:
                    flash(f'Cannot publish. Missing required fields: {", ".join(missing_fields)}', 'error')
                    return redirect(url_for('edit_listing', listing_id=listing_id))
                
                # Create on PropertyFinder
                try:
                    result = client.create_listing(listing_data)
                    pf_listing_id = result.get('id')
                    
                    if not pf_listing_id:
                        error_msg = result.get('error') or result.get('message') or str(result)
                        flash(f'PropertyFinder rejected the listing: {error_msg}', 'error')
                        return redirect(url_for('view_listing', listing_id=listing_id))
                    
                    local_listing.pf_listing_id = str(pf_listing_id)
                    local_listing.status = 'draft'
                    db.session.commit()
                    flash(f'Listing created on PropertyFinder (ID: {pf_listing_id})', 'success')
                    
                except PropertyFinderAPIError as e:
                    flash(f'Failed to create on PropertyFinder: {e.message}', 'error')
                    return redirect(url_for('view_listing', listing_id=listing_id))
                except Exception as e:
                    flash(f'Failed to create on PropertyFinder: {str(e)}', 'error')
                    return redirect(url_for('view_listing', listing_id=listing_id))
            
            # Now publish on PropertyFinder
            try:
                publish_result = client.publish_listing(local_listing.pf_listing_id)
                local_listing.status = 'live'  # PF publishes instantly
                db.session.commit()
                flash(f'Listing published successfully! PF ID: {local_listing.pf_listing_id}', 'success')
                return redirect(url_for('view_listing', listing_id=listing_id))
                
            except PropertyFinderAPIError as e:
                flash(f'PropertyFinder rejected publish: {e.message}', 'error')
                return redirect(url_for('view_listing', listing_id=listing_id))
            except Exception as e:
                flash(f'Failed to publish: {str(e)}', 'error')
                return redirect(url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        pass  # Not an integer ID, continue with PF listing
    
    # PropertyFinder listing
    client = get_client()
    result = client.publish_listing(listing_id)
    
    flash('Listing published successfully!', 'success')
    return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/unpublish', methods=['POST'])
@api_error_handler
def unpublish_listing_form(listing_id):
    """Handle listing unpublish from web form"""
    # Check if this is a local listing
    try:
        local_id = int(listing_id)
        local_listing = LocalListing.query.get(local_id)
        if local_listing:
            if not local_listing.pf_listing_id:
                flash('This listing is not published on PropertyFinder', 'warning')
                return redirect(url_for('view_listing', listing_id=listing_id))
            
            # Unpublish on PropertyFinder
            client = get_client()
            result = client.unpublish_listing(local_listing.pf_listing_id)
            
            # Update local status
            local_listing.status = 'draft'
            db.session.commit()
            
            flash('Listing unpublished successfully!', 'success')
            return redirect(url_for('view_listing', listing_id=listing_id))
    except (ValueError, TypeError):
        pass  # Not an integer ID, continue with PF listing
    
    # PropertyFinder listing
    client = get_client()
    result = client.unpublish_listing(listing_id)
    
    flash('Listing unpublished successfully!', 'success')
    return redirect(url_for('view_listing', listing_id=listing_id))


def build_listing_from_form(form):
    """Build listing dict from form data"""
    data = {
        'title': form.get('title'),
        'description': form.get('description'),
        'property_type': form.get('property_type'),
        'offering_type': form.get('offering_type'),
    }
    
    # Price
    price_amount = form.get('price')
    if price_amount:
        data['price'] = {
            'amount': float(price_amount),
            'currency': form.get('currency', 'AED')
        }
        if form.get('rent_frequency'):
            data['price']['frequency'] = form.get('rent_frequency')
    
    # Location
    location = {}
    for field in ['city', 'community', 'sub_community', 'building', 'street']:
        if form.get(field):
            location[field] = form.get(field)
    if location:
        data['location'] = location
    
    # Numeric fields
    for field in ['bedrooms', 'bathrooms', 'parking']:
        if form.get(field):
            data[field] = int(form.get(field))
    
    for field in ['size', 'plot_size']:
        if form.get(field):
            data[field] = float(form.get(field))
    
    # String fields
    for field in ['reference_number', 'permit_number', 'completion_status', 'furnishing']:
        if form.get(field):
            data[field] = form.get(field)
    
    # Amenities (comma-separated)
    if form.get('amenities'):
        data['amenities'] = [a.strip() for a in form.get('amenities').split(',')]
    
    # Images (newline-separated URLs)
    if form.get('images'):
        data['images'] = [img.strip() for img in form.get('images').split('\n') if img.strip()]
    
    # Boolean
    data['featured'] = form.get('featured') == 'on'
    
    return data


# ==================== LOCAL DATABASE API ====================

@app.route('/api/local/listings', methods=['GET'])
def api_local_get_listings():
    """Get all local listings"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    status = request.args.get('status')
    
    query = LocalListing.query
    if status:
        query = query.filter_by(status=status)
    
    query = query.order_by(LocalListing.updated_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    return jsonify({
        'data': [l.to_dict() for l in pagination.items],
        'meta': {
            'current_page': pagination.page,
            'last_page': pagination.pages,
            'per_page': per_page,
            'total': pagination.total
        }
    })


@app.route('/api/local/listings', methods=['POST'])
def api_local_create_listing():
    """Create a local listing"""
    data = request.get_json()
    
    # Check if reference already exists
    existing = LocalListing.query.filter_by(reference=data.get('reference')).first()
    if existing:
        return jsonify({'error': 'Reference already exists'}), 400
    
    listing = LocalListing.from_dict(data)
    db.session.add(listing)
    db.session.commit()
    
    return jsonify({'success': True, 'data': listing.to_dict()}), 201


@app.route('/api/local/listings/<int:listing_id>', methods=['GET'])
def api_local_get_listing(listing_id):
    """Get a single local listing"""
    listing = LocalListing.query.get_or_404(listing_id)
    return jsonify({'data': listing.to_dict()})


@app.route('/api/local/listings/<int:listing_id>', methods=['PUT'])
def api_local_update_listing(listing_id):
    """Update a local listing"""
    listing = LocalListing.query.get_or_404(listing_id)
    data = request.get_json()
    
    # Update fields
    for key, value in data.items():
        if hasattr(listing, key) and key not in ['id', 'created_at']:
            if key == 'images' and isinstance(value, list):
                value = '|'.join(value)
            elif key == 'amenities' and isinstance(value, list):
                value = ','.join(value)
            setattr(listing, key, value)
    
    listing.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True, 'data': listing.to_dict()})


@app.route('/api/local/listings/<int:listing_id>', methods=['DELETE'])
def api_local_delete_listing(listing_id):
    """Delete a local listing"""
    listing = LocalListing.query.get_or_404(listing_id)
    db.session.delete(listing)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Listing deleted'})


@app.route('/api/local/listings/bulk', methods=['POST'])
def api_local_bulk_create():
    """Bulk create local listings"""
    data = request.get_json()
    listings_data = data.get('listings', [])
    
    created = []
    errors = []
    
    for idx, item in enumerate(listings_data):
        try:
            # Check for duplicate reference
            if LocalListing.query.filter_by(reference=item.get('reference')).first():
                errors.append({'index': idx, 'error': 'Reference already exists', 'reference': item.get('reference')})
                continue
            
            listing = LocalListing.from_dict(item)
            db.session.add(listing)
            db.session.flush()
            created.append(listing.to_dict())
        except Exception as e:
            errors.append({'index': idx, 'error': str(e), 'reference': item.get('reference')})
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'created': len(created),
        'errors': len(errors),
        'error_details': errors
    })


@app.route('/api/local/stats', methods=['GET'])
def api_local_stats():
    """Get local listings statistics"""
    total = LocalListing.query.count()
    published = LocalListing.query.filter_by(status='published').count()
    draft = LocalListing.query.filter_by(status='draft').count()
    
    for_sale = LocalListing.query.filter_by(offering_type='sale').count()
    for_rent = LocalListing.query.filter_by(offering_type='rent').count()
    
    return jsonify({
        'total': total,
        'published': published,
        'draft': draft,
        'for_sale': for_sale,
        'for_rent': for_rent
    })


# ==================== PF AUTHENTICATION ====================

@app.route('/auth')
@login_required
@permission_required('settings')
def auth_page():
    """PropertyFinder authentication page"""
    pf_session = PFSession.query.first()
    return render_template('auth.html', session=pf_session)


@app.route('/api/auth/save-session', methods=['POST'])
def save_pf_session():
    """Save PropertyFinder session cookies from browser"""
    data = request.get_json()
    
    # Get or create session
    pf_session = PFSession.query.first()
    if not pf_session:
        pf_session = PFSession()
        db.session.add(pf_session)
    
    pf_session.cookies = json.dumps(data.get('cookies', {}))
    pf_session.user_agent = data.get('userAgent')
    pf_session.logged_in = data.get('loggedIn', False)
    pf_session.email = data.get('email')
    pf_session.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Session saved'})


@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    """Check authentication status"""
    pf_session = PFSession.query.first()
    
    if pf_session and pf_session.logged_in:
        return jsonify({
            'authenticated': True,
            'email': pf_session.email,
            'updated_at': pf_session.updated_at.isoformat() if pf_session.updated_at else None
        })
    
    return jsonify({'authenticated': False})


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Clear saved session"""
    PFSession.query.delete()
    db.session.commit()
    return jsonify({'success': True})


# ==================== CRM: LEADS ====================

from database import Lead, Customer

@app.route('/leads')
@login_required
def leads_page():
    """Leads management page"""
    return render_template('leads.html')


@app.route('/api/leads', methods=['GET'])
@login_required
def api_get_leads():
    """Get all leads"""
    leads = Lead.query.order_by(Lead.created_at.desc()).all()
    return jsonify({'leads': [l.to_dict() for l in leads]})


@app.route('/api/leads', methods=['POST'])
@login_required
def api_create_lead():
    """Create a new lead"""
    data = request.get_json()
    
    lead = Lead(
        name=data.get('name'),
        email=data.get('email'),
        phone=data.get('phone'),
        whatsapp=data.get('whatsapp'),
        source=data.get('source', 'other'),
        message=data.get('message'),
        listing_reference=data.get('listing_reference'),
        priority=data.get('priority', 'medium'),
        status='new'
    )
    
    db.session.add(lead)
    db.session.commit()
    
    return jsonify({'success': True, 'lead': lead.to_dict()})


@app.route('/api/leads/<int:lead_id>', methods=['GET'])
@login_required
def api_get_lead(lead_id):
    """Get a single lead"""
    lead = Lead.query.get_or_404(lead_id)
    return jsonify({'lead': lead.to_dict()})


@app.route('/api/leads/<int:lead_id>', methods=['PATCH'])
@login_required
def api_update_lead(lead_id):
    """Update a lead"""
    lead = Lead.query.get_or_404(lead_id)
    data = request.get_json()
    
    for field in ['name', 'email', 'phone', 'whatsapp', 'source', 'message', 
                  'listing_reference', 'status', 'priority', 'notes', 'assigned_to_id']:
        if field in data:
            setattr(lead, field, data[field])
    
    if 'status' in data and data['status'] == 'contacted':
        lead.last_contact = datetime.utcnow()
    
    db.session.commit()
    return jsonify({'success': True, 'lead': lead.to_dict()})


@app.route('/api/leads/<int:lead_id>', methods=['DELETE'])
@login_required
def api_delete_lead(lead_id):
    """Delete a lead"""
    lead = Lead.query.get_or_404(lead_id)
    db.session.delete(lead)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/leads/bulk-delete', methods=['POST'])
@login_required
def api_bulk_delete_leads():
    """Delete multiple leads at once"""
    data = request.get_json()
    lead_ids = data.get('ids', [])
    
    if not lead_ids:
        return jsonify({'success': False, 'error': 'No leads selected'}), 400
    
    deleted = 0
    for lead_id in lead_ids:
        lead = Lead.query.get(lead_id)
        if lead:
            db.session.delete(lead)
            deleted += 1
    
    db.session.commit()
    return jsonify({'success': True, 'deleted': deleted})


@app.route('/api/leads/refresh-agents', methods=['POST'])
@login_required
def api_refresh_lead_agents():
    """Refresh agent names for all PF leads based on listing's assignedTo"""
    # Get PF users and listings
    pf_users = PFCache.get_cache('users') or []
    pf_listings = PFCache.get_cache('listings') or []
    
    user_map = {u.get('publicProfile', {}).get('id'): u for u in pf_users}
    listing_map = {l.get('id'): l for l in pf_listings}
    for l in pf_listings:
        if l.get('reference'):
            listing_map[l.get('reference')] = l
    
    # Update all PF leads
    leads = Lead.query.filter_by(source='propertyfinder').all()
    updated = 0
    
    for lead in leads:
        listing_id = lead.pf_listing_id or lead.listing_reference
        pf_agent_id = None
        pf_agent_name = None
        
        # Get agent from listing's assignedTo
        if listing_id and listing_id in listing_map:
            pf_listing = listing_map[listing_id]
            assigned_to = pf_listing.get('assignedTo', {})
            if assigned_to and assigned_to.get('id'):
                pf_agent_id = str(assigned_to.get('id'))
        
        # Fallback to existing pf_agent_id
        if not pf_agent_id and lead.pf_agent_id:
            pf_agent_id = lead.pf_agent_id
        
        # Map agent ID to name
        if pf_agent_id:
            agent_id_int = int(pf_agent_id) if pf_agent_id.isdigit() else None
            if agent_id_int and agent_id_int in user_map:
                user = user_map[agent_id_int]
                pf_agent_name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
        
        # Update if changed
        if pf_agent_id != lead.pf_agent_id or pf_agent_name != lead.pf_agent_name:
            lead.pf_agent_id = pf_agent_id
            lead.pf_agent_name = pf_agent_name
            updated += 1
    
    db.session.commit()
    return jsonify({'success': True, 'updated': updated, 'total': len(leads)})


@app.route('/api/leads/sync-pf', methods=['POST'])
@login_required
def api_sync_leads_from_pf():
    """Sync leads from PropertyFinder"""
    from dateutil import parser as date_parser
    
    try:
        client = get_client()
        # Fetch all leads with pagination
        all_pf_leads = []
        page = 1
        while True:
            result = client.get_leads(page=page, per_page=50)
            leads_data = result.get('data', [])
            if not leads_data:
                break
            all_pf_leads.extend(leads_data)
            pagination = result.get('pagination', {})
            if page >= pagination.get('totalPages', 1):
                break
            page += 1
            if page > 10:  # Safety limit
                break
        
        # Get PF users to map agent names
        pf_users = PFCache.get_cache('users') or []
        user_map = {u.get('publicProfile', {}).get('id'): u for u in pf_users}
        
        # Get PF listings to map listing owners (assignedTo)
        pf_listings = PFCache.get_cache('listings') or []
        listing_map = {l.get('id'): l for l in pf_listings}
        # Also map by reference
        for l in pf_listings:
            if l.get('reference'):
                listing_map[l.get('reference')] = l
        
        imported = 0
        skipped = 0
        for pf_lead in all_pf_leads:
            # Check if already exists
            source_id = str(pf_lead.get('id'))
            existing = Lead.query.filter_by(source='propertyfinder', source_id=source_id).first()
            if existing:
                skipped += 1
                continue
            
            # Extract contact info - PF API uses 'sender' not 'contact'
            sender = pf_lead.get('sender', {})
            listing = pf_lead.get('listing', {})
            public_profile = pf_lead.get('publicProfile', {})
            
            # Get phone/email from contacts array
            contacts = sender.get('contacts', [])
            phone = None
            email = None
            for c in contacts:
                if c.get('type') == 'phone':
                    phone = c.get('value')
                elif c.get('type') == 'email':
                    email = c.get('value')
            
            # Parse received date from PF
            received_at = None
            if pf_lead.get('createdAt'):
                try:
                    received_at = date_parser.parse(pf_lead.get('createdAt'))
                except:
                    pass
            
            # Get agent info - use listing's assignedTo (the listing owner/agent)
            listing_id = listing.get('id') or listing.get('reference')
            pf_agent_id = None
            pf_agent_name = None
            
            # First try to get agent from listing's assignedTo
            if listing_id and listing_id in listing_map:
                pf_listing = listing_map[listing_id]
                assigned_to = pf_listing.get('assignedTo', {})
                if assigned_to and assigned_to.get('id'):
                    pf_agent_id = str(assigned_to.get('id'))
            
            # Fallback to publicProfile if no listing agent
            if not pf_agent_id and public_profile.get('id'):
                pf_agent_id = str(public_profile.get('id'))
            
            # Map agent ID to name
            if pf_agent_id:
                agent_id_int = int(pf_agent_id) if pf_agent_id.isdigit() else None
                if agent_id_int and agent_id_int in user_map:
                    user = user_map[agent_id_int]
                    pf_agent_name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
            
            lead = Lead(
                source='propertyfinder',
                source_id=source_id,
                channel=pf_lead.get('channel'),  # whatsapp, email, call
                name=sender.get('name', 'Unknown'),
                email=email,
                phone=phone,
                whatsapp=phone if pf_lead.get('channel') == 'whatsapp' else None,
                message=pf_lead.get('message'),
                pf_listing_id=str(listing.get('id')) if listing.get('id') else None,
                listing_reference=listing.get('reference'),
                response_link=pf_lead.get('responseLink'),
                status='new',
                pf_status=pf_lead.get('status'),  # sent, delivered, read, replied
                priority='medium',
                pf_agent_id=pf_agent_id,
                pf_agent_name=pf_agent_name,
                received_at=received_at
            )
            db.session.add(lead)
            imported += 1
        
        db.session.commit()
        return jsonify({
            'success': True, 
            'imported': imported, 
            'skipped': skipped,
            'total': len(all_pf_leads)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== WEBHOOKS ====================

@app.route('/webhooks/zapier', methods=['POST'])
def webhook_zapier():
    """
    Receive leads from Zapier
    
    Expected payload:
    {
        "name": "John Doe",
        "email": "john@example.com",
        "phone": "+971501234567",
        "message": "Interested in property",
        "source": "facebook",  // facebook, instagram, website, etc.
        "listing_reference": "ABC-123"  // optional
    }
    """
    # Verify webhook secret if configured
    secret = request.headers.get('X-Webhook-Secret')
    expected_secret = os.environ.get('ZAPIER_WEBHOOK_SECRET')
    if expected_secret and secret != expected_secret:
        return jsonify({'error': 'Invalid webhook secret'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    # Create lead
    lead = Lead(
        source=data.get('source', 'zapier'),
        name=data.get('name', 'Unknown'),
        email=data.get('email'),
        phone=data.get('phone'),
        whatsapp=data.get('whatsapp') or data.get('phone'),
        message=data.get('message'),
        listing_reference=data.get('listing_reference'),
        status='new',
        priority=data.get('priority', 'medium')
    )
    
    db.session.add(lead)
    db.session.commit()
    
    return jsonify({'success': True, 'lead_id': lead.id})


@app.route('/webhooks/propertyfinder', methods=['POST'])
def webhook_propertyfinder():
    """Receive lead notifications from PropertyFinder webhook"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    # Extract lead info from PF webhook
    contact = data.get('contact', {})
    listing = data.get('listing', {})
    
    lead = Lead(
        source='propertyfinder',
        source_id=str(data.get('id')),
        name=contact.get('name', 'Unknown'),
        email=contact.get('email'),
        phone=contact.get('phone'),
        message=data.get('message'),
        pf_listing_id=str(listing.get('id')) if listing.get('id') else None,
        listing_reference=listing.get('reference'),
        status='new',
        priority='medium'
    )
    
    db.session.add(lead)
    db.session.commit()
    
    return jsonify({'success': True, 'lead_id': lead.id})


# ==================== IMAGE EDITOR ENDPOINTS ====================

# Ensure logos directory exists
LOGOS_DIR = UPLOAD_FOLDER / 'logos'
PROCESSED_IMAGES_DIR = UPLOAD_FOLDER / 'processed'

@app.route('/image-editor')
@login_required
def image_editor():
    """Image editor page"""
    # Get all image settings
    settings = {
        'image_default_ratio': AppSettings.get('image_default_ratio', ''),
        'image_default_size': AppSettings.get('image_default_size', 'full_hd'),
        'image_quality': AppSettings.get('image_quality', '90'),
        'image_format': AppSettings.get('image_format', 'JPEG'),
        'image_qr_enabled': AppSettings.get('image_qr_enabled', 'false'),
        'image_qr_data': AppSettings.get('image_default_qr_data', ''),
        'image_qr_position': AppSettings.get('image_qr_position', 'bottom_right'),
        'image_qr_size_percent': AppSettings.get('image_qr_size_percent', '12'),
        'image_qr_color': AppSettings.get('image_qr_color', '#000000'),
        'image_logo_enabled': AppSettings.get('image_logo_enabled', 'false'),
        'image_logo_position': AppSettings.get('image_logo_position', 'bottom_left'),
        'image_logo_size': AppSettings.get('image_logo_size', '15'),
        'image_logo_opacity': AppSettings.get('image_logo_opacity', '80'),
        'image_default_logo': AppSettings.get('image_default_logo', ''),
    }
    return render_template('image_editor.html', settings=settings)


@app.route('/api/images/settings', methods=['GET'])
@login_required
def api_get_image_settings():
    """Get image processing settings"""
    settings = {
        'default_logo': AppSettings.get('image_default_logo'),
        'default_qr_data': AppSettings.get('image_default_qr_data'),
        'default_ratio': AppSettings.get('image_default_ratio', '16:9'),
        'qr_position': AppSettings.get('image_qr_position', 'bottom-right'),
        'qr_size_percent': int(AppSettings.get('image_qr_size_percent', '15')),
        'logo_position': AppSettings.get('image_logo_position', 'bottom-left'),
        'logo_opacity': int(AppSettings.get('image_logo_opacity', '80'))
    }
    return jsonify(settings)


@app.route('/api/images/settings', methods=['POST'])
@permission_required('settings')
def api_save_image_settings():
    """Save image processing settings"""
    data = request.json
    
    if 'default_qr_data' in data:
        AppSettings.set('image_default_qr_data', data['default_qr_data'])
    if 'default_ratio' in data:
        AppSettings.set('image_default_ratio', data['default_ratio'])
    if 'qr_position' in data:
        AppSettings.set('image_qr_position', data['qr_position'])
    if 'qr_size_percent' in data:
        AppSettings.set('image_qr_size_percent', str(data['qr_size_percent']))
    if 'logo_position' in data:
        AppSettings.set('image_logo_position', data['logo_position'])
    if 'logo_opacity' in data:
        AppSettings.set('image_logo_opacity', str(data['logo_opacity']))
    
    return jsonify({'success': True, 'message': 'Settings saved'})


@app.route('/api/images/upload-logo', methods=['POST'])
@permission_required('settings')
def api_upload_logo():
    """Upload default logo"""
    if 'logo' not in request.files:
        return jsonify({'error': 'No logo file provided'}), 400
    
    file = request.files['logo']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Ensure logos directory exists
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Save logo with secure filename
    filename = secure_filename(file.filename)
    # Use timestamp to avoid caching issues
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'png'
    logo_filename = f'default_logo_{timestamp}.{ext}'
    logo_path = LOGOS_DIR / logo_filename
    
    file.save(str(logo_path))
    
    # Save path in settings
    relative_path = f'uploads/logos/{logo_filename}'
    AppSettings.set('image_default_logo', relative_path)
    
    return jsonify({
        'success': True,
        'logo_path': relative_path,
        'message': 'Logo uploaded successfully'
    })


@app.route('/api/images/process-single', methods=['POST'])
@login_required
def api_process_single_image():
    """Process a single image from base64 data"""
    import base64
    from io import BytesIO
    
    temp_logo_path = None
    
    try:
        data = request.json
        if not data or 'image' not in data:
            return jsonify({'error': 'No image provided'}), 400
        
        # Parse base64 image
        image_data_url = data['image']
        if ',' in image_data_url:
            header, encoded = image_data_url.split(',', 1)
        else:
            encoded = image_data_url
        
        try:
            image_bytes = base64.b64decode(encoded)
        except Exception as decode_err:
            print(f"[ImageProcessor] Base64 decode error: {decode_err}")
            return jsonify({'error': f'Invalid image data: {decode_err}'}), 400
        
        print(f"[ImageProcessor] Processing image, size: {len(image_bytes)} bytes")
        
        # Get processing options with safe defaults
        target_ratio = data.get('ratio', '') or None
        qr_data = data.get('qr_data') or None
        qr_position = (data.get('qr_position', 'bottom_right') or 'bottom_right').replace('-', '_')
        qr_size = int(data.get('qr_size_percent', 12) or 12)
        qr_color = data.get('qr_color', '#000000') or '#000000'
        logo_data = data.get('logo_data')
        logo_position = (data.get('logo_position', 'bottom_left') or 'bottom_left').replace('-', '_')
        logo_size = int(data.get('logo_size_percent', 10) or 10)
        logo_opacity = float(data.get('logo_opacity', 0.9) or 0.9)
        output_format = data.get('format', 'JPEG') or 'JPEG'
        quality = int(data.get('quality', 90) or 90)
        size_preset = data.get('size', 'original') or 'original'
        
        print(f"[ImageProcessor] Options: ratio={target_ratio}, qr={bool(qr_data)}, format={output_format}")
        
        # Handle logo from base64 if provided
        logo_source = None
        if logo_data and logo_data.startswith('data:'):
            try:
                if ',' in logo_data:
                    _, logo_encoded = logo_data.split(',', 1)
                else:
                    logo_encoded = logo_data
                logo_bytes = base64.b64decode(logo_encoded)
                # Save to temp file
                import tempfile
                fd, temp_logo_path = tempfile.mkstemp(suffix='.png')
                with os.fdopen(fd, 'wb') as f:
                    f.write(logo_bytes)
                logo_source = temp_logo_path
                print(f"[ImageProcessor] Logo saved to temp file: {temp_logo_path}")
            except Exception as logo_err:
                print(f"[ImageProcessor] Logo decode error: {logo_err}")
        elif not logo_data:
            # Check for default logo
            logo_setting = AppSettings.get('image_default_logo')
            if logo_setting:
                potential_path = str(ROOT_DIR / logo_setting)
                if Path(potential_path).exists():
                    logo_source = potential_path
                    print(f"[ImageProcessor] Using default logo: {logo_source}")
        
        # Create processor and process image
        processor = ImageProcessor()
        
        processed_bytes, metadata = processor.process_image(
            image_source=image_bytes,
            ratio=target_ratio,
            size=size_preset,
            qr_data=qr_data,
            qr_position=qr_position,
            qr_size_percent=qr_size,
            qr_color=qr_color,
            logo_source=logo_source,
            logo_position=logo_position,
            logo_size_percent=logo_size,
            logo_opacity=logo_opacity,
            output_format=output_format,
            quality=quality
        )
        
        print(f"[ImageProcessor] Processed successfully: {metadata.get('final_size')}")
        
        # Convert to data URI
        mime_types = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}
        mime_type = mime_types.get(output_format.upper(), 'image/jpeg')
        output_base64 = base64.b64encode(processed_bytes).decode('utf-8')
        data_uri = f"data:{mime_type};base64,{output_base64}"
        
        return jsonify({
            'success': True,
            'image': data_uri,
            'metadata': {
                'original_size': list(metadata['original_size']),
                'final_size': list(metadata['final_size']),
                'file_size': metadata['file_size'],
                'format': output_format
            }
        })
                
    except Exception as e:
        import traceback
        print(f"[ImageProcessor] ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({'error': f'{type(e).__name__}: {str(e)}'}), 500
    
    finally:
        # Clean up temp logo file
        if temp_logo_path and os.path.exists(temp_logo_path):
            try:
                os.unlink(temp_logo_path)
            except:
                pass


@app.route('/api/settings/images', methods=['GET'])
@login_required
def api_get_settings_images():
    """Get image settings (alternate endpoint)"""
    return api_get_image_settings()


@app.route('/api/settings/images', methods=['POST'])
@permission_required('settings')
def api_save_settings_images():
    """Save image settings"""
    data = request.json
    
    settings_map = {
        'ratio': 'image_default_ratio',
        'size': 'image_default_size',
        'quality': 'image_quality',
        'format': 'image_format',
        'qrEnabled': 'image_qr_enabled',
        'qrData': 'image_default_qr_data',
        'qrPosition': 'image_qr_position',
        'qrSize': 'image_qr_size_percent',
        'qrColor': 'image_qr_color',
        'logoEnabled': 'image_logo_enabled',
        'logoPosition': 'image_logo_position',
        'logoSize': 'image_logo_size',
        'logoOpacity': 'image_logo_opacity',
    }
    
    for js_key, db_key in settings_map.items():
        if js_key in data:
            value = data[js_key]
            # Convert booleans to strings
            if isinstance(value, bool):
                value = 'true' if value else 'false'
            AppSettings.set(db_key, str(value))
    
    # Handle logo data if provided as base64
    if data.get('logoData') and data['logoData'].startswith('data:'):
        import base64
        try:
            logo_data_url = data['logoData']
            if ',' in logo_data_url:
                _, encoded = logo_data_url.split(',', 1)
            else:
                encoded = logo_data_url
            logo_bytes = base64.b64decode(encoded)
            
            # Save logo
            LOGOS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            logo_filename = f'default_logo_{timestamp}.png'
            logo_path = LOGOS_DIR / logo_filename
            with open(logo_path, 'wb') as f:
                f.write(logo_bytes)
            
            AppSettings.set('image_default_logo', f'uploads/logos/{logo_filename}')
        except Exception as e:
            print(f"Error saving logo: {e}")
    
    return jsonify({'success': True, 'message': 'Settings saved successfully'})


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serve uploaded files"""
    from flask import send_from_directory
    return send_from_directory(str(UPLOAD_FOLDER), filename)


# ==================== IMAGE PROCESSING WITH SAVED SETTINGS ====================

@app.route('/api/images/process-with-settings', methods=['POST'])
@login_required
def api_process_image_with_settings():
    """Process an image using saved settings and save to disk
    
    Accepts either:
    - 'image': base64 data URL
    - 'url': URL to download (server downloads to bypass CORS)
    """
    import base64
    import uuid
    
    temp_logo_path = None
    
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        listing_id = data.get('listing_id')
        image_bytes = None
        
        # Option 1: URL - server downloads it (bypasses CORS)
        if 'url' in data and data['url']:
            url = data['url']
            print(f"[ProcessWithSettings] Downloading from URL: {url[:100]}...")
            
            # Skip if it's already a local processed image
            if url.startswith('/uploads/'):
                return jsonify({
                    'success': True,
                    'url': url,
                    'skipped': True,
                    'message': 'Already a local processed image'
                })
            
            try:
                import requests as http_requests
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                resp = http_requests.get(url, headers=headers, timeout=30, stream=True)
                resp.raise_for_status()
                image_bytes = resp.content
                print(f"[ProcessWithSettings] Downloaded {len(image_bytes)} bytes")
            except Exception as dl_err:
                print(f"[ProcessWithSettings] Download failed: {dl_err}")
                return jsonify({'error': f'Failed to download image: {dl_err}'}), 400
        
        # Option 2: Base64 image data
        elif 'image' in data and data['image']:
            image_data_url = data['image']
            if ',' in image_data_url:
                header, encoded = image_data_url.split(',', 1)
            else:
                encoded = image_data_url
            
            try:
                image_bytes = base64.b64decode(encoded)
            except Exception as decode_err:
                return jsonify({'error': f'Invalid image data: {decode_err}'}), 400
        else:
            return jsonify({'error': 'No image or url provided'}), 400
        
        # Load saved settings
        settings = {
            'ratio': AppSettings.get('image_default_ratio') or None,
            'size': AppSettings.get('image_default_size') or 'full_hd',
            'quality': int(AppSettings.get('image_quality') or 90),
            'format': AppSettings.get('image_format') or 'JPEG',
            'qr_enabled': AppSettings.get('image_qr_enabled') == 'true',
            'qr_data': AppSettings.get('image_default_qr_data') or '',
            'qr_position': AppSettings.get('image_qr_position') or 'bottom_right',
            'qr_size': int(AppSettings.get('image_qr_size_percent') or 12),
            'qr_color': AppSettings.get('image_qr_color') or '#000000',
            'logo_enabled': AppSettings.get('image_logo_enabled') == 'true',
            'logo_path': AppSettings.get('image_default_logo'),
            'logo_position': AppSettings.get('image_logo_position') or 'bottom_left',
            'logo_size': int(AppSettings.get('image_logo_size') or 10),
            'logo_opacity': float(AppSettings.get('image_logo_opacity') or 0.9),
        }
        
        print(f"[ProcessWithSettings] Using settings: ratio={settings['ratio']}, qr={settings['qr_enabled']}, logo={settings['logo_enabled']}")
        
        # Prepare QR data
        qr_data = settings['qr_data'] if settings['qr_enabled'] else None
        
        # Prepare logo
        logo_source = None
        if settings['logo_enabled'] and settings['logo_path']:
            potential_path = str(ROOT_DIR / settings['logo_path'])
            if Path(potential_path).exists():
                logo_source = potential_path
        
        # Create processor and process image
        processor = ImageProcessor()
        
        processed_bytes, metadata = processor.process_image(
            image_source=image_bytes,
            ratio=settings['ratio'],
            size=settings['size'],
            qr_data=qr_data,
            qr_position=settings['qr_position'].replace('-', '_'),
            qr_size_percent=settings['qr_size'],
            qr_color=settings['qr_color'],
            logo_source=logo_source,
            logo_position=settings['logo_position'].replace('-', '_'),
            logo_size_percent=settings['logo_size'],
            logo_opacity=settings['logo_opacity'],
            output_format=settings['format'],
            quality=settings['quality']
        )
        
        # Determine file extension
        ext = settings['format'].lower()
        if ext == 'jpeg':
            ext = 'jpg'
        
        # Save processed image to disk
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        filename = f'processed_{timestamp}_{unique_id}.{ext}'
        
        if listing_id:
            save_dir = LISTING_IMAGES_FOLDER / str(listing_id)
            relative_path = f'listings/{listing_id}/{filename}'
        else:
            save_dir = UPLOAD_FOLDER / 'processed'
            relative_path = f'processed/{filename}'
        
        save_dir.mkdir(parents=True, exist_ok=True)
        filepath = save_dir / filename
        
        with open(filepath, 'wb') as f:
            f.write(processed_bytes)
        
        url = f'/uploads/{relative_path}'
        
        print(f"[ProcessWithSettings] Saved: {relative_path} ({len(processed_bytes)} bytes)")
        
        return jsonify({
            'success': True,
            'url': url,
            'metadata': {
                'original_size': list(metadata['original_size']),
                'final_size': list(metadata['final_size']),
                'file_size': len(processed_bytes),
                'format': settings['format']
            }
        })
        
    except Exception as e:
        import traceback
        print(f"[ProcessWithSettings] ERROR: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== IMAGE UPLOAD ENDPOINT ====================

@app.route('/api/images/upload', methods=['POST'])
@login_required
def api_upload_image():
    """Upload a single image file"""
    import uuid
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Check file type
    allowed_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_extensions:
        return jsonify({'error': f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'}), 400
    
    # No file size limit - Flask MAX_CONTENT_LENGTH handles overall request size (50MB)
    
    try:
        # Get listing_id if provided (for organizing files)
        listing_id = request.form.get('listing_id')
        
        # Generate unique filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        filename = f'img_{timestamp}_{unique_id}.{ext}'
        
        # Determine save path
        if listing_id:
            save_dir = LISTING_IMAGES_FOLDER / str(listing_id)
            relative_path = f'listings/{listing_id}/{filename}'
        else:
            save_dir = UPLOAD_FOLDER / 'temp'
            relative_path = f'temp/{filename}'
        
        save_dir.mkdir(parents=True, exist_ok=True)
        filepath = save_dir / filename
        
        # Save file
        file.save(str(filepath))
        
        # Generate URL
        url = f'/uploads/{relative_path}'
        
        print(f"[ImageUpload] Saved: {relative_path} ({size} bytes)")
        
        return jsonify({
            'success': True,
            'id': unique_id,
            'url': url,
            'filename': filename,
            'size': size
        })
        
    except Exception as e:
        import traceback
        print(f"[ImageUpload] Error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== LISTING IMAGES ENDPOINTS ====================

@app.route('/api/listings/summary', methods=['GET'])
@login_required
def api_listings_summary():
    """Get summary of all listings for dropdown selection"""
    try:
        listings = LocalListing.query.order_by(LocalListing.reference).all()
        
        result = []
        for l in listings:
            # Count images
            image_count = 0
            if l.images:
                try:
                    imgs = json.loads(l.images) if isinstance(l.images, str) else l.images
                    image_count = len(imgs) if isinstance(imgs, list) else 0
                except:
                    pass
            
            result.append({
                'id': l.id,
                'reference': l.reference or f'ID-{l.id}',
                'title': l.title_en or 'Untitled',
                'title_en': l.title_en or 'Untitled',
                'city': l.city,
                'property_type': l.property_type,
                'offering_type': l.offering_type,
                'status': l.status or 'draft',
                'image_count': image_count
            })
        
        return jsonify({'listings': result})
    except Exception as e:
        import traceback
        print(f"[ERROR] api_listings_summary: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/api/listings/<int:listing_id>/images', methods=['GET'])
@login_required
def api_get_listing_images(listing_id):
    """Get images for a listing"""
    listing = LocalListing.query.get_or_404(listing_id)
    
    images = []
    if listing.images:
        try:
            images = json.loads(listing.images) if isinstance(listing.images, str) else listing.images
        except:
            images = []
    
    return jsonify({
        'listing_id': listing_id,
        'reference': listing.reference,
        'images': images,
        'count': len(images)
    })


@app.route('/api/listings/<int:listing_id>/images', methods=['POST'])
@permission_required('edit')
def api_assign_images_to_listing(listing_id):
    """Assign processed images to a listing"""
    import base64
    import uuid
    
    listing = LocalListing.query.get_or_404(listing_id)
    data = request.json
    
    if not data or 'images' not in data:
        return jsonify({'error': 'No images provided'}), 400
    
    images_data = data['images']  # List of base64 data URIs
    mode = data.get('mode', 'append')  # 'append' or 'replace'
    
    # Get existing images
    existing_images = []
    if mode == 'append' and listing.images:
        try:
            existing_images = json.loads(listing.images) if isinstance(listing.images, str) else listing.images
            if not isinstance(existing_images, list):
                existing_images = []
        except:
            existing_images = []
    
    # Create listing images directory
    listing_dir = LISTING_IMAGES_FOLDER / str(listing_id)
    listing_dir.mkdir(parents=True, exist_ok=True)
    
    # Save new images
    new_image_paths = []
    for i, img_data in enumerate(images_data):
        try:
            # Parse base64 data
            if ',' in img_data:
                header, encoded = img_data.split(',', 1)
                # Determine format from header
                if 'png' in header.lower():
                    ext = 'png'
                elif 'webp' in header.lower():
                    ext = 'webp'
                else:
                    ext = 'jpg'
            else:
                encoded = img_data
                ext = 'jpg'
            
            image_bytes = base64.b64decode(encoded)
            
            # Generate unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            unique_id = str(uuid.uuid4())[:8]
            filename = f'img_{timestamp}_{unique_id}.{ext}'
            filepath = listing_dir / filename
            
            # Save image
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            
            # Store relative path for database
            relative_path = f'listings/{listing_id}/{filename}'
            new_image_paths.append(relative_path)
            
            print(f"[ListingImages] Saved image: {relative_path} ({len(image_bytes)} bytes)")
            
        except Exception as e:
            print(f"[ListingImages] Error saving image {i}: {e}")
            continue
    
    # Combine with existing images
    all_images = existing_images + new_image_paths
    
    # Update listing
    listing.images = json.dumps(all_images)
    listing.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'listing_id': listing_id,
        'images_added': len(new_image_paths),
        'total_images': len(all_images),
        'images': all_images,
        'message': f'Added {len(new_image_paths)} images to listing'
    })


@app.route('/api/listings/<int:listing_id>/images', methods=['DELETE'])
@permission_required('edit')
def api_delete_listing_images(listing_id):
    """Delete images from a listing"""
    listing = LocalListing.query.get_or_404(listing_id)
    data = request.json
    
    images_to_delete = data.get('images', [])  # List of image paths to delete
    delete_all = data.get('delete_all', False)
    
    # Get existing images
    existing_images = []
    if listing.images:
        try:
            existing_images = json.loads(listing.images) if isinstance(listing.images, str) else listing.images
        except:
            existing_images = []
    
    if delete_all:
        images_to_delete = existing_images.copy()
    
    # Delete files and update list
    deleted_count = 0
    for img_path in images_to_delete:
        if img_path in existing_images:
            existing_images.remove(img_path)
            # Try to delete the actual file
            try:
                full_path = UPLOAD_FOLDER / img_path
                if full_path.exists():
                    full_path.unlink()
                    deleted_count += 1
            except Exception as e:
                print(f"[ListingImages] Error deleting file {img_path}: {e}")
    
    # Update listing
    listing.images = json.dumps(existing_images)
    listing.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'deleted_count': deleted_count,
        'remaining_images': len(existing_images),
        'images': existing_images
    })


@app.route('/api/listings/search', methods=['GET'])
@login_required
def api_search_listings():
    """Search listings for assignment dropdown"""
    query = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 20)), 50)
    
    # Build query
    listings_query = LocalListing.query
    
    if query:
        search = f'%{query}%'
        listings_query = listings_query.filter(
            db.or_(
                LocalListing.reference.ilike(search),
                LocalListing.title_en.ilike(search),
                LocalListing.location.ilike(search),
                LocalListing.property_type.ilike(search)
            )
        )
    
    listings = listings_query.order_by(LocalListing.updated_at.desc()).limit(limit).all()
    
    results = []
    for listing in listings:
        # Count existing images
        image_count = 0
        if listing.images:
            try:
                images = json.loads(listing.images) if isinstance(listing.images, str) else listing.images
                image_count = len(images) if isinstance(images, list) else 0
            except:
                pass
        
        results.append({
            'id': listing.id,
            'reference': listing.reference,
            'title': listing.title_en or f'{listing.property_type} in {listing.location}',
            'location': listing.location,
            'property_type': listing.property_type,
            'price': listing.price,
            'image_count': image_count,
            'status': listing.status
        })
    
    return jsonify({
        'results': results,
        'count': len(results)
    })


# ==================== HEALTH CHECK ====================

@app.route('/health')
def health_check():
    """Health check endpoint for Railway"""
    try:
        # Check database connection
        db.session.execute(db.text('SELECT 1'))
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'environment': 'production' if IS_PRODUCTION else 'development'
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500


if __name__ == '__main__':
    print("=" * 50)
    print("PropertyFinder Dashboard")
    print("=" * 50)
    
    if not Config.validate():
        print("\n⚠ Warning: API credentials not configured in .env")
        print("  Some features may not work until configured")
    
    port = int(os.environ.get('PORT', 5000))
    debug = not IS_PRODUCTION and Config.DEBUG
    
    print(f"\nEnvironment: {'Production' if IS_PRODUCTION else 'Development'}")
    print(f"Starting server at http://localhost:{port}")
    print("Press Ctrl+C to stop\n")
    
    app.run(debug=debug, host='0.0.0.0', port=port)

# This runs when gunicorn imports the module
print("[STARTUP] App module fully loaded and ready to serve requests")
