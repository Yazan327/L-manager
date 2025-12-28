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

# Get the src directory (parent of dashboard)
SRC_DIR = Path(__file__).parent.parent
ROOT_DIR = SRC_DIR.parent

# Add src directory to path
sys.path.insert(0, str(SRC_DIR))

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, g
from werkzeug.utils import secure_filename

from api import PropertyFinderClient, PropertyFinderAPIError, Config
from models import PropertyListing, PropertyType, OfferingType, Location, Price
from utils import BulkListingManager
from database import db, LocalListing, PFSession, User, ListingFolder

# Setup paths for templates and static files
TEMPLATE_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'
UPLOAD_FOLDER = ROOT_DIR / 'uploads'
DATABASE_PATH = ROOT_DIR / 'data' / 'listings.db'

# Ensure data directory exists
DATABASE_PATH.parent.mkdir(exist_ok=True)

app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Database Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db.init_app(app)

# Create tables and run migrations
with app.app_context():
    # First create all new tables (including listing_folders)
    db.create_all()
    
    # Migration: Add folder_id column to listings table if it doesn't exist
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        
        # Check if listings table exists and if folder_id column is missing
        if 'listings' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('listings')]
            
            if 'folder_id' not in columns:
                # Add folder_id column (without foreign key constraint for simpler migration)
                with db.engine.connect() as conn:
                    dialect = db.engine.dialect.name
                    conn.execute(text('ALTER TABLE listings ADD COLUMN folder_id INTEGER'))
                    conn.commit()
                print("✓ Migration: Added folder_id column to listings table")
    except Exception as e:
        # Column might already exist or other non-critical error
        print(f"Migration note: {e}")
    
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
        print(f"✓ Created default admin user: {admin_email}")


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
# In-memory cache for PropertyFinder data
_pf_cache = {
    'listings': [],
    'users': [],
    'leads': [],
    'credits': None,
    'last_updated': None,
    'cache_duration': 300  # 5 minutes in seconds
}

def get_cached_pf_data(force_refresh=False):
    """Get PropertyFinder data with caching"""
    global _pf_cache
    
    # Check if cache is valid
    if not force_refresh and _pf_cache['last_updated']:
        age = (datetime.now() - _pf_cache['last_updated']).total_seconds()
        if age < _pf_cache['cache_duration']:
            return _pf_cache
    
    # Fetch fresh data
    try:
        client = get_client()
        
        # Fetch all listings (paginated)
        all_listings = []
        page = 1
        while True:
            result = client.get_listings(page=page, per_page=50)
            listings = result.get('results', [])
            if not listings:
                break
            all_listings.extend(listings)
            
            pagination = result.get('pagination', {})
            if page >= pagination.get('totalPages', 1):
                break
            page += 1
            if page > 50:  # Support up to 2500 listings
                break
        
        _pf_cache['listings'] = all_listings
        
        # Fetch users
        try:
            users_result = client.get_users(per_page=50)
            _pf_cache['users'] = users_result.get('data', [])
        except:
            pass
        
        # Fetch leads (for per-listing lead counts)
        try:
            leads_result = client.get_leads(per_page=100)
            _pf_cache['leads'] = leads_result.get('data', [])
        except:
            _pf_cache['leads'] = []
        
        # Fetch credits
        try:
            _pf_cache['credits'] = client.get_credits()
        except:
            pass
        
        _pf_cache['last_updated'] = datetime.now()
        _pf_cache['error'] = None
        
    except PropertyFinderAPIError as e:
        _pf_cache['error'] = f"API Error: {e.message}"
    except Exception as e:
        _pf_cache['error'] = f"Error: {str(e)}"
    
    return _pf_cache

# Configuration
ALLOWED_EXTENSIONS = {'json', 'csv'}
UPLOAD_FOLDER.mkdir(exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max


def get_client():
    """Get PropertyFinder API client"""
    return PropertyFinderClient()


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
    return render_template('users.html', users=[u.to_dict() for u in users], roles=User.ROLES)


@app.route('/users/create', methods=['POST'])
@permission_required('manage_users')
def create_user():
    """Create a new user"""
    email = request.form.get('email', '').strip().lower()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'viewer')
    
    if not email or not name or not password:
        flash('All fields are required.', 'error')
        return redirect(url_for('users_page'))
    
    if User.query.filter_by(email=email).first():
        flash('A user with this email already exists.', 'error')
        return redirect(url_for('users_page'))
    
    if role not in User.ROLES:
        flash('Invalid role selected.', 'error')
        return redirect(url_for('users_page'))
    
    user = User(email=email, name=name, role=role)
    user.set_password(password)
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
    # Get local stats
    stats = {
        'total': LocalListing.query.count(),
        'published': LocalListing.query.filter_by(status='published').count(),
        'draft': LocalListing.query.filter_by(status='draft').count(),
    }
    recent = LocalListing.query.order_by(LocalListing.updated_at.desc()).limit(5).all()
    return render_template('index.html', stats=stats, recent_listings=[l.to_dict() for l in recent])


@app.route('/listings')
@login_required
def listings():
    """List all listings page - uses local database"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    status = request.args.get('status')
    sort_by = request.args.get('sort_by', 'updated_at')
    sort_order = request.args.get('sort_order', 'desc')
    search = request.args.get('search', '').strip()
    folder_id = request.args.get('folder_id', type=int)  # Folder filter
    
    query = LocalListing.query
    
    # Filter by folder
    if folder_id:
        query = query.filter_by(folder_id=folder_id)
    elif folder_id == 0 or request.args.get('folder_id') == '0':
        # Show uncategorized listings (no folder)
        query = query.filter(LocalListing.folder_id.is_(None))
    
    # Filter by status
    if status:
        query = query.filter_by(status=status)
    
    # Search filter
    if search:
        search_term = f'%{search}%'
        query = query.filter(
            db.or_(
                LocalListing.reference.ilike(search_term),
                LocalListing.title_en.ilike(search_term),
                LocalListing.location.ilike(search_term),
                LocalListing.city.ilike(search_term)
            )
        )
    
    # Sorting
    valid_sort_columns = {
        'updated_at': LocalListing.updated_at,
        'created_at': LocalListing.created_at,
        'price': LocalListing.price,
        'reference': LocalListing.reference,
        'title': LocalListing.title_en,
        'views': LocalListing.views,
        'leads': LocalListing.leads,
        'status': LocalListing.status,
        'bedrooms': LocalListing.bedrooms,
        'size': LocalListing.size
    }
    
    sort_column = valid_sort_columns.get(sort_by, LocalListing.updated_at)
    if sort_order == 'asc':
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())
    
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Get all folders for sidebar
    folders = ListingFolder.get_all_with_counts()
    current_folder = ListingFolder.query.get(folder_id) if folder_id else None
    uncategorized_count = LocalListing.query.filter(LocalListing.folder_id.is_(None)).count()
    
    return render_template('listings.html', 
                         listings=[l.to_dict() for l in pagination.items],
                         pagination={
                             'current_page': pagination.page,
                             'last_page': pagination.pages,
                             'per_page': per_page,
                             'total': pagination.total,
                             'has_prev': pagination.has_prev,
                             'has_next': pagination.has_next
                         },
                         folders=folders,
                         current_folder=current_folder.to_dict() if current_folder else None,
                         folder_id=folder_id,
                         uncategorized_count=uncategorized_count,
                         page=page,
                         per_page=per_page,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         search=search,
                         status=status or '')

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
    return render_template('bulk_upload.html', defaults={
        'agent_email': Config.DEFAULT_AGENT_EMAIL,
        'owner_email': Config.DEFAULT_OWNER_EMAIL
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
    
    cache = get_cached_pf_data()
    
    listings = cache['listings']
    leads = cache.get('leads', [])
    
    # Filter by user if specified
    if user_id:
        user_id = int(user_id)
        listings = [l for l in listings if 
                   l.get('publicProfile', {}).get('id') == user_id or
                   l.get('assignedTo', {}).get('id') == user_id]
        leads = [l for l in leads if 
                l.get('publicProfile', {}).get('id') == user_id]
    
    return jsonify({
        'success': cache.get('error') is None,
        'listings': listings,
        'users': cache['users'],
        'leads': leads,
        'error': cache.get('error'),
        'cached_at': cache['last_updated'].isoformat() if cache['last_updated'] else None
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


@app.route('/settings')
@permission_required('settings')
def settings():
    """Settings page"""
    return render_template('settings.html', config={
        'api_base_url': Config.API_BASE_URL,
        'has_api_key': bool(Config.API_KEY),
        'has_api_secret': bool(Config.API_SECRET),
        'has_legacy_token': bool(Config.API_TOKEN),
        'agency_id': Config.AGENCY_ID,
        'debug': Config.DEBUG,
        'bulk_batch_size': Config.BULK_BATCH_SIZE,
        'bulk_delay': Config.BULK_DELAY_SECONDS,
        'default_agent_email': Config.DEFAULT_AGENT_EMAIL,
        'default_owner_email': Config.DEFAULT_OWNER_EMAIL
    })


# ==================== FOLDER API ENDPOINTS ====================

@app.route('/api/folders', methods=['GET'])
@login_required
def api_get_folders():
    """API: Get all folders"""
    folders = ListingFolder.get_all_with_counts()
    uncategorized_count = LocalListing.query.filter(LocalListing.folder_id.is_(None)).count()
    return jsonify({
        'folders': folders,
        'uncategorized_count': uncategorized_count
    })


@app.route('/api/folders', methods=['POST'])
@permission_required('create')
def api_create_folder():
    """API: Create a new folder"""
    data = request.json
    
    if not data.get('name'):
        return jsonify({'error': 'Folder name is required'}), 400
    
    folder = ListingFolder(
        name=data['name'],
        color=data.get('color', 'indigo'),
        icon=data.get('icon', 'fa-folder'),
        description=data.get('description'),
        parent_id=data.get('parent_id')
    )
    db.session.add(folder)
    db.session.commit()
    
    return jsonify({'folder': folder.to_dict(), 'message': 'Folder created successfully'})


@app.route('/api/folders/<int:folder_id>', methods=['GET'])
@login_required
def api_get_folder(folder_id):
    """API: Get a single folder"""
    folder = ListingFolder.query.get_or_404(folder_id)
    return jsonify({'folder': folder.to_dict()})


@app.route('/api/folders/<int:folder_id>', methods=['PUT', 'PATCH'])
@permission_required('edit')
def api_update_folder(folder_id):
    """API: Update a folder"""
    folder = ListingFolder.query.get_or_404(folder_id)
    data = request.json
    
    if 'name' in data:
        folder.name = data['name']
    if 'color' in data:
        folder.color = data['color']
    if 'icon' in data:
        folder.icon = data['icon']
    if 'description' in data:
        folder.description = data['description']
    if 'parent_id' in data:
        folder.parent_id = data['parent_id']
    
    db.session.commit()
    return jsonify({'folder': folder.to_dict(), 'message': 'Folder updated successfully'})


@app.route('/api/folders/<int:folder_id>', methods=['DELETE'])
@permission_required('delete')
def api_delete_folder(folder_id):
    """API: Delete a folder (moves listings to uncategorized)"""
    folder = ListingFolder.query.get_or_404(folder_id)
    
    # Move all listings in this folder to uncategorized
    LocalListing.query.filter_by(folder_id=folder_id).update({'folder_id': None})
    
    db.session.delete(folder)
    db.session.commit()
    
    return jsonify({'message': 'Folder deleted successfully'})


@app.route('/api/listings/move-to-folder', methods=['POST'])
@permission_required('edit')
def api_move_listings_to_folder():
    """API: Move listings to a folder"""
    data = request.json
    listing_ids = data.get('listing_ids', [])
    folder_id = data.get('folder_id')  # None means uncategorized
    
    if not listing_ids:
        return jsonify({'error': 'No listings specified'}), 400
    
    # Verify folder exists if specified
    if folder_id is not None:
        folder = ListingFolder.query.get(folder_id)
        if not folder:
            return jsonify({'error': 'Folder not found'}), 404
    
    # Update listings
    updated = LocalListing.query.filter(LocalListing.id.in_(listing_ids)).update(
        {'folder_id': folder_id},
        synchronize_session=False
    )
    db.session.commit()
    
    return jsonify({
        'message': f'Moved {updated} listings',
        'moved_count': updated
    })


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
    result = client.publish_listing(listing_id)
    return jsonify({'success': True, 'data': result})


@app.route('/api/listings/<listing_id>/unpublish', methods=['POST'])
@api_error_handler
def api_unpublish_listing(listing_id):
    """API: Unpublish a listing"""
    client = get_client()
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
    """Handle listing creation form submission"""
    data = build_listing_from_form(request.form)
    
    client = get_client()
    result = client.create_listing(data)
    
    flash('Listing created successfully!', 'success')
    listing_id = result.get('id') or result.get('data', {}).get('id')
    if listing_id:
        return redirect(url_for('view_listing', listing_id=listing_id))
    return redirect(url_for('listings'))


@app.route('/listings/<listing_id>/update', methods=['POST'])
@api_error_handler
def update_listing_form(listing_id):
    """Handle listing update form submission"""
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


if __name__ == '__main__':
    print("=" * 50)
    print("PropertyFinder Dashboard")
    print("=" * 50)
    
    if not Config.validate():
        print("\n⚠ Warning: API credentials not configured in .env")
        print("  Some features may not work until configured")
    
    print(f"\nStarting server at http://localhost:5000")
    print("Press Ctrl+C to stop\n")
    
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000)
