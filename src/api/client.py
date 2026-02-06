"""
PropertyFinder Enterprise API Client

Based on the official PropertyFinder Enterprise API (OpenAPI 3.1.0)
Documentation: https://atlas.propertyfinder.com/v1

Authentication Flow:
1. Exchange API Key + API Secret for JWT token (POST /v1/auth/token)
2. Use JWT token in Authorization header for all subsequent requests
3. Token expires in 30 minutes (no refresh token - request new one)

Rate Limits:
- Auth endpoint: 60 requests/minute
- Other endpoints: 650 requests/minute
"""
import json
import time
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from .config import Config


class PropertyFinderAPIError(Exception):
    """Custom exception for PropertyFinder API errors"""
    def __init__(self, message: str, status_code: int = None, response: dict = None):
        self.message = message
        self.status_code = status_code
        self.response = response
        super().__init__(self.message)


class PropertyFinderClient:
    """
    PropertyFinder Enterprise API Client
    
    Handles OAuth authentication and all API requests to PropertyFinder Enterprise API.
    Base URL: https://atlas.propertyfinder.com/v1
    """
    
    def __init__(self, api_key: str = None, api_secret: str = None, base_url: str = None):
        """
        Initialize the PropertyFinder Enterprise API client
        
        Args:
            api_key: API Key from PF Expert (uses env if not provided)
            api_secret: API Secret from PF Expert (uses env if not provided)
            base_url: API base URL (uses env if not provided)
        """
        self.base_url = (base_url or Config.API_BASE_URL).rstrip('/')
        self.api_key = api_key or Config.API_KEY
        self.api_secret = api_secret or Config.API_SECRET
        
        # Token cache
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        
        # Session setup
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
    
    # ==================== AUTHENTICATION ====================
    
    def _get_access_token(self, force_refresh: bool = False) -> str:
        """
        Get a valid access token, refreshing if necessary
        
        The Enterprise API uses API Key + API Secret to get a JWT token.
        Token expires in 30 minutes (1800 seconds).
        
        Args:
            force_refresh: Force getting a new token even if current one is valid
            
        Returns:
            Valid JWT access token
        """
        # Check if we have a valid cached token
        if not force_refresh and self._access_token and self._token_expires_at:
            # Check if token is still valid (with buffer)
            buffer = timedelta(seconds=Config.TOKEN_EXPIRY_BUFFER)
            if datetime.now() < (self._token_expires_at - buffer):
                return self._access_token
        
        # Request new token
        if not self.api_key or not self.api_secret:
            raise PropertyFinderAPIError(
                "API Key and API Secret are required. "
                "Get them from PF Expert → Settings → API Keys → Type: 'API Integration'"
            )
        
        token_url = f"{self.base_url}/auth/token"
        
        if Config.DEBUG:
            print(f"[DEBUG] Requesting new access token from {token_url}")
        
        try:
            response = requests.post(
                token_url,
                json={
                    'apiKey': self.api_key,
                    'apiSecret': self.api_secret
                },
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                timeout=Config.REQUEST_TIMEOUT
            )
            
            if Config.DEBUG:
                print(f"[DEBUG] Token response status: {response.status_code}")
            
            if not response.ok:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('message', f'HTTP {response.status_code}')
                except:
                    error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                
                raise PropertyFinderAPIError(
                    f"Authentication failed: {error_msg}",
                    status_code=response.status_code
                )
            
            token_data = response.json()
            
            # Cache the token
            self._access_token = token_data.get('accessToken')
            expires_in = token_data.get('expiresIn', 1800)  # Default 30 minutes
            self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            if Config.DEBUG:
                print(f"[DEBUG] New token obtained, expires in {expires_in} seconds")
            
            return self._access_token
            
        except requests.RequestException as e:
            raise PropertyFinderAPIError(f"Failed to authenticate: {str(e)}")
    
    def _ensure_authenticated(self):
        """Ensure session has a valid Authorization header"""
        token = self._get_access_token()
        self.session.headers['Authorization'] = f'Bearer {token}'
    
    # ==================== REQUEST HANDLER ====================
    
    def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: dict = None, 
        params: dict = None,
        retries: int = None,
        skip_auth: bool = False
    ) -> Dict[str, Any]:
        """
        Make an API request with retry logic and automatic token refresh
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            endpoint: API endpoint (without base URL)
            data: Request body data
            params: Query parameters
            retries: Number of retries (uses config default)
            skip_auth: Skip authentication (for auth endpoints)
            
        Returns:
            API response as dictionary
        """
        # Ensure we have valid auth
        if not skip_auth:
            self._ensure_authenticated()
        
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        retries = retries or Config.MAX_RETRIES
        
        for attempt in range(retries + 1):
            try:
                if Config.DEBUG:
                    print(f"[DEBUG] {method} {url}")
                    if data:
                        print(f"[DEBUG] Data: {json.dumps(data, indent=2)[:500]}...")
                
                response = self.session.request(
                    method=method,
                    url=url,
                    json=data,
                    params=params,
                    timeout=Config.REQUEST_TIMEOUT
                )
                
                if Config.DEBUG:
                    print(f"[DEBUG] Response Status: {response.status_code}")
                
                # Handle authentication errors (token expired)
                if response.status_code == 401 and not skip_auth:
                    if attempt < retries:
                        if Config.DEBUG:
                            print("[DEBUG] Token expired, refreshing...")
                        self._access_token = None  # Force refresh
                        self._ensure_authenticated()
                        continue
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    if attempt < retries:
                        print(f"Rate limited. Waiting {retry_after} seconds...")
                        time.sleep(retry_after)
                        continue
                
                # Parse response
                try:
                    response_data = response.json()
                except json.JSONDecodeError:
                    raw_text = (response.text or '').strip()
                    if len(raw_text) > 500:
                        raw_text = raw_text[:500] + '...'
                    response_data = {'raw': raw_text}

                # Capture request/correlation ID if present
                request_id = response.headers.get('x-request-id') or response.headers.get('x-correlation-id')
                if request_id and isinstance(response_data, dict):
                    response_data['_request_id'] = request_id
                
                # Check for errors
                if not response.ok:
                    error_msg = None
                    if isinstance(response_data, dict):
                        error_msg = response_data.get('message') or response_data.get('error') or response_data.get('raw')
                    if not error_msg:
                        error_msg = f'HTTP {response.status_code}'
                    if 'errors' in response_data:
                        # Extract validation errors
                        errors = response_data.get('errors', [])
                        if errors:
                            error_details = '; '.join([
                                f"{e.get('field', 'unknown')}: {e.get('message', e.get('reason', 'unknown error'))}"
                                for e in errors
                            ])
                            error_msg = f"{error_msg} - {error_details}"
                    
                    raise PropertyFinderAPIError(
                        message=error_msg,
                        status_code=response.status_code,
                        response=response_data
                    )
                
                return response_data
                
            except requests.RequestException as e:
                if attempt < retries:
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"Request failed. Retrying in {wait_time}s... ({attempt + 1}/{retries})")
                    time.sleep(wait_time)
                else:
                    raise PropertyFinderAPIError(f"Request failed after {retries} retries: {str(e)}")
    
    # ==================== CONNECTION TEST ====================
    
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the API connection and authentication
        
        Returns:
            Connection test result with token info
        """
        try:
            # Try to get a token
            token = self._get_access_token(force_refresh=True)
            
            # Try to fetch users (simplest endpoint)
            try:
                users = self.get_users(page=1, per_page=1)
                user_count = users.get('pagination', {}).get('total', 0)
            except:
                user_count = 'unknown'
            
            return {
                'success': True,
                'message': 'Successfully connected to PropertyFinder Enterprise API',
                'base_url': self.base_url,
                'token_expires_at': self._token_expires_at.isoformat() if self._token_expires_at else None,
                'user_count': user_count
            }
        except PropertyFinderAPIError as e:
            return {
                'success': False,
                'message': str(e.message),
                'status_code': e.status_code,
                'base_url': self.base_url
            }
    
    # ==================== USER OPERATIONS ====================
    
    def get_users(self, page: int = 1, per_page: int = 15, **filters) -> Dict[str, Any]:
        """
        Get users in the organization
        
        Required before creating listings to get publicProfile.id
        
        Args:
            page: Page number
            per_page: Items per page (max 50)
            **filters: Additional filters
            
        Returns:
            List of users with publicProfile info
        """
        params = {'page': page, 'perPage': per_page, **filters}
        return self._make_request('GET', '/users', params=params)
    
    def get_user(self, user_id: int) -> Dict[str, Any]:
        """Get a single user by ID"""
        return self._make_request('GET', f'/users/{user_id}')
    
    # ==================== LISTING OPERATIONS ====================
    
    def get_listings(self, page: int = 1, per_page: int = 15, **filters) -> Dict[str, Any]:
        """
        Get all listings with optional filtering
        
        Args:
            page: Page number
            per_page: Items per page (max 50)
            **filters: Additional filters
                - filter[state]: draft, live, takendown, etc.
                - filter[publicProfileId]: Filter by agent
                
        Returns:
            List of listings with pagination info
        """
        params = {'page': page, 'perPage': per_page, **filters}
        return self._make_request('GET', '/listings', params=params)
    
    def get_listing(self, listing_id: str) -> Dict[str, Any]:
        """
        Get a single listing by ID
        
        Args:
            listing_id: The listing ID
            
        Returns:
            Listing details
        """
        return self._make_request('GET', f'/listings/{listing_id}')
    
    def create_listing(self, listing_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new listing in DRAFT mode
        
        The listing flow is:
        1. Create listing (draft) with POST /listings
        2. Publish listing with POST /listings/{id}/publish
        
        Required fields:
        - type: Property type (apartment, villa, etc.)
        - category: residential or commercial
        - price.type: yearly, sale, monthly, etc.
        - price.amounts: {yearly: 50000} or {sale: 1000000}
        - location.id: Location ID from /locations
        - title.en or title.ar: Listing title
        - assignedTo.id: Public profile ID from /users
        
        Args:
            listing_data: Listing data dictionary
            
        Returns:
            Created listing response with listingId
        """
        return self._make_request('POST', '/listings', data=listing_data)
    
    def update_listing(self, listing_id: str, listing_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing listing
        
        Args:
            listing_id: The listing ID to update
            listing_data: Updated listing data
            
        Returns:
            Updated listing response
        """
        return self._make_request('PUT', f'/listings/{listing_id}', data=listing_data)
    
    def delete_listing(self, listing_id: str) -> Dict[str, Any]:
        """
        Delete a listing
        
        Args:
            listing_id: The listing ID to delete
            
        Returns:
            Deletion response
        """
        return self._make_request('DELETE', f'/listings/{listing_id}')
    
    def get_listing_state(self, listing_id: str) -> Dict[str, Any]:
        """
        Get the current state of a listing
        
        States: draft, live, live_pending_deletion, takendown, takendown_pending_deletion
        
        Args:
            listing_id: The listing ID
            
        Returns:
            Listing state info
        """
        return self._make_request('GET', f'/listings/{listing_id}/state')
    
    # ==================== PUBLISH OPERATIONS ====================
    
    def get_publish_prices(self, listing_id: str) -> Dict[str, Any]:
        """
        Get the publishing price for a listing
        
        Call this before publishing to see the cost.
        
        Args:
            listing_id: The listing ID
            
        Returns:
            Publishing price info
        """
        return self._make_request('GET', f'/listings/{listing_id}/publish/prices')
    
    def publish_listing(self, listing_id: str, product_name: str = None) -> Dict[str, Any]:
        """
        Publish a draft listing
        
        Note: Publishing is ASYNCHRONOUS. A 200 response means the request was received,
        not that the listing was published. Use webhooks or poll /listings/{id}/state.
        
        Args:
            listing_id: The listing ID to publish
            product_name: Optional product name from publish/prices
            
        Returns:
            Publish request response
        """
        data = {}
        if product_name:
            data['productName'] = product_name
        return self._make_request('POST', f'/listings/{listing_id}/publish', data=data if data else None)
    
    def unpublish_listing(self, listing_id: str) -> Dict[str, Any]:
        """
        Unpublish (takedown) a live listing
        
        Args:
            listing_id: The listing ID to unpublish
            
        Returns:
            Unpublish response
        """
        return self._make_request('POST', f'/listings/{listing_id}/unpublish')
    
    # ==================== LOCATION OPERATIONS ====================
    
    def get_locations(self, search: str = None, page: int = 1, per_page: int = 15, **filters) -> Dict[str, Any]:
        """
        Search locations in PropertyFinder's location tree
        
        Every listing must be associated with a valid location.
        
        Args:
            search: Search query (e.g., "Marina", "Downtown")
            page: Page number
            per_page: Items per page
            **filters: Additional filters (filter[parent]=50 for sub-locations)
            
        Returns:
            List of locations with coordinates and IDs
        """
        params = {'page': page, 'perPage': per_page, **filters}
        if search:
            params['search'] = search
        return self._make_request('GET', '/locations', params=params)
    
    # ==================== COMPLIANCE (DLD/RERA) ====================
    
    def get_compliance(self, permit_number: str, license_number: str, permit_type: str = 'rera') -> Dict[str, Any]:
        """
        Verify DLD/RERA permit details
        
        Required for Dubai listings - must have valid RERA permit.
        
        Args:
            permit_number: The permit number
            license_number: Company license number
            permit_type: rera, dtcm, or adrec
            
        Returns:
            Official permit details from DLD
        """
        params = {'permitType': permit_type}
        return self._make_request('GET', f'/compliances/{permit_number}/{license_number}', params=params)
    
    # ==================== CREDITS ====================
    
    def get_credits(self) -> Dict[str, Any]:
        """
        Get available credits/listings quota
        
        Returns:
            Credit balance and usage info
        """
        return self._make_request('GET', '/credits')
    
    # ==================== STATISTICS ====================
    
    def get_statistics(self, **filters) -> Dict[str, Any]:
        """
        Get listing statistics
        
        Args:
            **filters: Filters for statistics
            
        Returns:
            Statistics data
        """
        return self._make_request('GET', '/stats', params=filters)
    
    # ==================== LEADS ====================
    
    def get_leads(self, page: int = 1, per_page: int = 15, **filters) -> Dict[str, Any]:
        """
        Get leads/inquiries
        
        Args:
            page: Page number
            per_page: Items per page
            **filters: Additional filters
            
        Returns:
            List of leads
        """
        params = {'page': page, 'perPage': per_page, **filters}
        return self._make_request('GET', '/leads', params=params)
    
    # ==================== BULK OPERATIONS ====================
    
    def bulk_create_listings(
        self, 
        listings: List[Dict[str, Any]], 
        auto_publish: bool = False,
        progress_callback = None
    ) -> Dict[str, Any]:
        """
        Create multiple listings in bulk
        
        Args:
            listings: List of listing data dictionaries
            auto_publish: Automatically publish after creating
            progress_callback: Optional callback(current, total, listing_id, success, error)
            
        Returns:
            Bulk operation results
        """
        results = {
            'total': len(listings),
            'success': 0,
            'failed': 0,
            'created': [],
            'errors': []
        }
        
        for i, listing_data in enumerate(listings):
            try:
                # Create listing
                response = self.create_listing(listing_data)
                listing_id = response.get('id')
                
                # Auto-publish if requested
                if auto_publish and listing_id:
                    try:
                        self.publish_listing(listing_id)
                    except PropertyFinderAPIError as pub_error:
                        # Created but failed to publish
                        results['created'].append({
                            'listing_id': listing_id,
                            'status': 'draft',
                            'publish_error': str(pub_error.message)
                        })
                        results['success'] += 1
                        if progress_callback:
                            progress_callback(i + 1, len(listings), listing_id, True, str(pub_error.message))
                        continue
                
                results['created'].append({
                    'listing_id': listing_id,
                    'status': 'published' if auto_publish else 'draft'
                })
                results['success'] += 1
                
                if progress_callback:
                    progress_callback(i + 1, len(listings), listing_id, True, None)
                
                # Rate limiting delay
                if i < len(listings) - 1:
                    time.sleep(Config.BULK_DELAY_SECONDS)
                    
            except PropertyFinderAPIError as e:
                results['failed'] += 1
                results['errors'].append({
                    'index': i,
                    'reference': listing_data.get('reference', f'row_{i}'),
                    'error': str(e.message),
                    'status_code': e.status_code
                })
                
                if progress_callback:
                    progress_callback(i + 1, len(listings), None, False, str(e.message))
        
        return results
