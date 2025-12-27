"""
PropertyFinder Listings Dashboard - Web UI
A Flask-based dashboard for managing property listings
"""
import os
import sys
import json
from pathlib import Path
from functools import wraps

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from werkzeug.utils import secure_filename

from api import PropertyFinderClient, PropertyFinderAPIError, Config
from models import PropertyListing, PropertyType, OfferingType
from utils import BulkListingManager

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.urandom(24)

# Configure upload folder
UPLOAD_FOLDER = Path(__file__).parent / 'uploads'
UPLOAD_FOLDER.mkdir(exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

ALLOWED_EXTENSIONS = {'json', 'csv'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_client():
    """Get PropertyFinder API client"""
    return PropertyFinderClient()


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
            return redirect(url_for('dashboard'))
        except Exception as e:
            if request.is_json or request.headers.get('Accept') == 'application/json':
                return jsonify({'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'error')
            return redirect(url_for('dashboard'))
    return decorated_function


# ==================== ROUTES ====================

@app.route('/')
def dashboard():
    """Main dashboard page"""
    return render_template('dashboard.html')


@app.route('/listings')
@api_error_handler
def listings():
    """Listings page with table view"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    
    client = get_client()
    result = client.get_listings(page=page, per_page=per_page)
    
    return render_template('listings.html', 
                         listings=result.get('data', []),
                         pagination=result.get('meta', {}),
                         page=page,
                         per_page=per_page)


@app.route('/listings/<listing_id>')
@api_error_handler
def view_listing(listing_id):
    """View single listing details"""
    client = get_client()
    listing = client.get_listing(listing_id)
    return render_template('listing_detail.html', listing=listing.get('data', listing))


@app.route('/listings/create', methods=['GET', 'POST'])
@api_error_handler
def create_listing():
    """Create new listing form"""
    if request.method == 'POST':
        client = get_client()
        
        # Build listing data from form
        listing_data = {
            'title': request.form.get('title'),
            'description': request.form.get('description'),
            'property_type': request.form.get('property_type'),
            'offering_type': request.form.get('offering_type'),
            'price': {
                'amount': float(request.form.get('price', 0)),
                'currency': request.form.get('currency', 'AED')
            },
            'location': {
                'city': request.form.get('city'),
                'community': request.form.get('community'),
                'sub_community': request.form.get('sub_community'),
                'building': request.form.get('building')
            },
            'bedrooms': int(request.form.get('bedrooms', 0)) if request.form.get('bedrooms') else None,
            'bathrooms': int(request.form.get('bathrooms', 0)) if request.form.get('bathrooms') else None,
            'size': float(request.form.get('size', 0)) if request.form.get('size') else None,
            'reference_number': request.form.get('reference_number'),
            'permit_number': request.form.get('permit_number')
        }
        
        # Add rent frequency for rentals
        if request.form.get('offering_type') == 'rent':
            listing_data['price']['frequency'] = request.form.get('rent_frequency', 'yearly')
        
        result = client.create_listing(listing_data)
        flash('Listing created successfully!', 'success')
        return redirect(url_for('listings'))
    
    # GET request - show form
    property_types = [
        ('AP', 'Apartment'), ('VH', 'Villa'), ('TH', 'Townhouse'),
        ('PH', 'Penthouse'), ('OF', 'Office'), ('RE', 'Retail'),
        ('WH', 'Warehouse'), ('LA', 'Land'), ('DU', 'Duplex'),
        ('FF', 'Full Floor'), ('WB', 'Whole Building')
    ]
    return render_template('create_listing.html', property_types=property_types)


@app.route('/listings/<listing_id>/edit', methods=['GET', 'POST'])
@api_error_handler
def edit_listing(listing_id):
    """Edit listing form"""
    client = get_client()
    
    if request.method == 'POST':
        # Build update data from form
        update_data = {}
        
        if request.form.get('title'):
            update_data['title'] = request.form.get('title')
        if request.form.get('description'):
            update_data['description'] = request.form.get('description')
        if request.form.get('price'):
            update_data['price'] = {
                'amount': float(request.form.get('price')),
                'currency': request.form.get('currency', 'AED')
            }
        if request.form.get('bedrooms'):
            update_data['bedrooms'] = int(request.form.get('bedrooms'))
        if request.form.get('bathrooms'):
            update_data['bathrooms'] = int(request.form.get('bathrooms'))
        if request.form.get('size'):
            update_data['size'] = float(request.form.get('size'))
        
        client.update_listing(listing_id, update_data)
        flash('Listing updated successfully!', 'success')
        return redirect(url_for('view_listing', listing_id=listing_id))
    
    # GET request - show form with current data
    listing = client.get_listing(listing_id)
    property_types = [
        ('AP', 'Apartment'), ('VH', 'Villa'), ('TH', 'Townhouse'),
        ('PH', 'Penthouse'), ('OF', 'Office'), ('RE', 'Retail'),
        ('WH', 'Warehouse'), ('LA', 'Land'), ('DU', 'Duplex'),
        ('FF', 'Full Floor'), ('WB', 'Whole Building')
    ]
    return render_template('edit_listing.html', 
                         listing=listing.get('data', listing),
                         property_types=property_types)


@app.route('/listings/<listing_id>/delete', methods=['POST'])
@api_error_handler
def delete_listing(listing_id):
    """Delete a listing"""
    client = get_client()
    client.delete_listing(listing_id)
    flash('Listing deleted successfully!', 'success')
    return redirect(url_for('listings'))


@app.route('/listings/<listing_id>/publish', methods=['POST'])
@api_error_handler
def publish_listing(listing_id):
    """Publish a listing"""
    client = get_client()
    client.publish_listing(listing_id)
    flash('Listing published successfully!', 'success')
    return redirect(url_for('view_listing', listing_id=listing_id))


@app.route('/listings/<listing_id>/unpublish', methods=['POST'])
@api_error_handler
def unpublish_listing(listing_id):
    """Unpublish a listing"""
    client = get_client()
    client.unpublish_listing(listing_id)
    flash('Listing unpublished successfully!', 'success')
    return redirect(url_for('view_listing', listing_id=listing_id))


# ==================== BULK OPERATIONS ====================

@app.route('/bulk')
def bulk_upload():
    """Bulk upload page"""
    return render_template('bulk_upload.html')


@app.route('/bulk/upload', methods=['POST'])
@api_error_handler
def bulk_upload_file():
    """Handle bulk file upload"""
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('bulk_upload'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('bulk_upload'))
    
    if not allowed_file(file.filename):
        flash('Invalid file type. Please upload JSON or CSV file.', 'error')
        return redirect(url_for('bulk_upload'))
    
    # Save file
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # Process file
    client = get_client()
    manager = BulkListingManager(client)
    publish = request.form.get('publish') == 'on'
    
    try:
        if filename.endswith('.json'):
            result = manager.create_listings_from_json(filepath, publish=publish)
        else:
            result = manager.create_listings_from_csv(filepath, publish=publish)
        
        # Clean up uploaded file
        os.remove(filepath)
        
        # Store results in session for display
        session['bulk_result'] = result.to_dict()
        
        return redirect(url_for('bulk_results'))
        
    except Exception as e:
        os.remove(filepath)
        flash(f'Error processing file: {str(e)}', 'error')
        return redirect(url_for('bulk_upload'))


@app.route('/bulk/results')
def bulk_results():
    """Display bulk operation results"""
    result = session.pop('bulk_result', None)
    if not result:
        return redirect(url_for('bulk_upload'))
    return render_template('bulk_results.html', result=result)


# ==================== API ENDPOINTS ====================

@app.route('/api/listings')
@api_error_handler
def api_listings():
    """API endpoint for listings (for AJAX)"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    
    client = get_client()
    result = client.get_listings(page=page, per_page=per_page)
    return jsonify(result)


@app.route('/api/listings', methods=['POST'])
@api_error_handler
def api_create_listing():
    """API endpoint to create listing"""
    client = get_client()
    data = request.get_json()
    result = client.create_listing(data)
    return jsonify(result), 201


@app.route('/api/listings/<listing_id>', methods=['PUT', 'PATCH'])
@api_error_handler
def api_update_listing(listing_id):
    """API endpoint to update listing"""
    client = get_client()
    data = request.get_json()
    
    if request.method == 'PUT':
        result = client.update_listing(listing_id, data)
    else:
        result = client.patch_listing(listing_id, data)
    
    return jsonify(result)


@app.route('/api/listings/<listing_id>', methods=['DELETE'])
@api_error_handler
def api_delete_listing(listing_id):
    """API endpoint to delete listing"""
    client = get_client()
    result = client.delete_listing(listing_id)
    return jsonify(result)


@app.route('/api/reference/<ref_type>')
@api_error_handler
def api_reference(ref_type):
    """Get reference data"""
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


@app.route('/api/account')
@api_error_handler
def api_account():
    """Get account info"""
    client = get_client()
    result = client.get_account()
    return jsonify(result)


# ==================== SETTINGS ====================

@app.route('/settings')
def settings():
    """Settings page"""
    return render_template('settings.html', config={
        'api_base_url': Config.API_BASE_URL,
        'has_token': bool(Config.API_TOKEN),
        'agency_id': Config.AGENCY_ID,
        'debug': Config.DEBUG,
        'bulk_batch_size': Config.BULK_BATCH_SIZE,
        'bulk_delay': Config.BULK_DELAY_SECONDS
    })


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error='Page not found', code=404), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', error='Server error', code=500), 500


if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("PropertyFinder Listings Dashboard")
    print("=" * 50)
    
    if not Config.validate():
        print("\n⚠ Warning: API credentials not configured")
        print("  Please edit .env file with your credentials")
    
    print(f"\n🚀 Starting server at http://localhost:5000")
    print("   Press Ctrl+C to stop\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
