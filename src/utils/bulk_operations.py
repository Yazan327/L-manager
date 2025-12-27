"""
Bulk Operations for PropertyFinder Listings
Supports CSV and JSON file imports for bulk listing creation
"""
import csv
import json
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Callable
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.client import PropertyFinderClient, PropertyFinderAPIError
from api.config import Config
from models.listing import PropertyListing


@dataclass
class BulkResult:
    """Result of a bulk operation"""
    total: int = 0
    successful: int = 0
    failed: int = 0
    results: List[Dict[str, Any]] = None
    errors: List[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.results is None:
            self.results = []
        if self.errors is None:
            self.errors = []
    
    def add_success(self, reference: str, listing_id: str, data: dict = None):
        """Add a successful result"""
        self.successful += 1
        self.results.append({
            'reference': reference,
            'listing_id': listing_id,
            'status': 'success',
            'data': data
        })
    
    def add_failure(self, reference: str, error: str, data: dict = None):
        """Add a failed result"""
        self.failed += 1
        self.errors.append({
            'reference': reference,
            'error': error,
            'status': 'failed',
            'data': data
        })
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'total': self.total,
            'successful': self.successful,
            'failed': self.failed,
            'success_rate': f"{(self.successful / self.total * 100):.1f}%" if self.total > 0 else "0%",
            'results': self.results,
            'errors': self.errors
        }
    
    def __str__(self):
        return f"BulkResult(total={self.total}, successful={self.successful}, failed={self.failed})"


class BulkListingManager:
    """
    Manages bulk listing operations for PropertyFinder
    
    Supports:
    - CSV file import
    - JSON file import
    - Batch processing with rate limiting
    - Progress callbacks
    - Error handling and reporting
    """
    
    def __init__(self, client: PropertyFinderClient = None):
        """
        Initialize bulk manager
        
        Args:
            client: PropertyFinder API client (creates new one if not provided)
        """
        self.client = client or PropertyFinderClient()
        self.batch_size = Config.BULK_BATCH_SIZE
        self.delay_seconds = Config.BULK_DELAY_SECONDS
    
    def create_listings_from_json(
        self, 
        file_path: str,
        progress_callback: Callable[[int, int, str], None] = None,
        publish: bool = False
    ) -> BulkResult:
        """
        Create listings from a JSON file
        
        Args:
            file_path: Path to JSON file containing listings array
            progress_callback: Optional callback(current, total, status)
            publish: Whether to publish listings after creation
            
        Returns:
            BulkResult with operation results
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Handle both array and object with 'listings' key
        if isinstance(data, dict):
            listings_data = data.get('listings', data.get('data', []))
        else:
            listings_data = data
        
        return self._process_listings(listings_data, progress_callback, publish)
    
    def create_listings_from_csv(
        self,
        file_path: str,
        progress_callback: Callable[[int, int, str], None] = None,
        publish: bool = False,
        delimiter: str = ','
    ) -> BulkResult:
        """
        Create listings from a CSV file
        
        Args:
            file_path: Path to CSV file
            progress_callback: Optional callback(current, total, status)
            publish: Whether to publish listings after creation
            delimiter: CSV delimiter character
            
        Returns:
            BulkResult with operation results
        """
        listings_data = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                listing_data = self._csv_row_to_listing(row)
                listings_data.append(listing_data)
        
        return self._process_listings(listings_data, progress_callback, publish)
    
    def create_listings_from_list(
        self,
        listings: List[Dict[str, Any]],
        progress_callback: Callable[[int, int, str], None] = None,
        publish: bool = False
    ) -> BulkResult:
        """
        Create listings from a list of dictionaries
        
        Args:
            listings: List of listing dictionaries
            progress_callback: Optional callback(current, total, status)
            publish: Whether to publish listings after creation
            
        Returns:
            BulkResult with operation results
        """
        return self._process_listings(listings, progress_callback, publish)
    
    def _process_listings(
        self,
        listings_data: List[Dict[str, Any]],
        progress_callback: Callable[[int, int, str], None] = None,
        publish: bool = False
    ) -> BulkResult:
        """
        Process a list of listings
        
        Args:
            listings_data: List of listing dictionaries
            progress_callback: Optional progress callback
            publish: Whether to publish after creation
            
        Returns:
            BulkResult with operation results
        """
        result = BulkResult(total=len(listings_data))
        
        for i, listing_data in enumerate(listings_data):
            try:
                # Get reference for tracking
                reference = listing_data.get('reference_number') or \
                           listing_data.get('external_reference') or \
                           f"listing_{i+1}"
                
                # Report progress
                if progress_callback:
                    progress_callback(i + 1, result.total, f"Creating: {reference}")
                
                # Convert to PropertyListing if needed
                if isinstance(listing_data, PropertyListing):
                    listing_dict = listing_data.to_dict()
                else:
                    listing = PropertyListing.from_dict(listing_data)
                    listing_dict = listing.to_dict()
                
                # Create listing
                response = self.client.create_listing(listing_dict)
                listing_id = response.get('id') or response.get('data', {}).get('id')
                
                # Publish if requested
                if publish and listing_id:
                    try:
                        self.client.publish_listing(listing_id)
                    except PropertyFinderAPIError as e:
                        print(f"Warning: Created but failed to publish {reference}: {e.message}")
                
                result.add_success(reference, listing_id, response)
                
            except PropertyFinderAPIError as e:
                result.add_failure(
                    reference=listing_data.get('reference_number', f"listing_{i+1}"),
                    error=f"API Error ({e.status_code}): {e.message}",
                    data=listing_data
                )
            except Exception as e:
                result.add_failure(
                    reference=listing_data.get('reference_number', f"listing_{i+1}"),
                    error=str(e),
                    data=listing_data
                )
            
            # Rate limiting delay between requests
            if i < len(listings_data) - 1:
                time.sleep(self.delay_seconds)
        
        if progress_callback:
            progress_callback(result.total, result.total, "Complete")
        
        return result
    
    def _csv_row_to_listing(self, row: Dict[str, str]) -> Dict[str, Any]:
        """
        Convert a CSV row to listing dictionary
        
        Handles type conversion and field mapping
        """
        listing = {}
        
        # Direct string mappings
        string_fields = [
            'title', 'title_ar', 'description', 'description_ar',
            'reference_number', 'permit_number', 'video_url', 
            'virtual_tour_url', 'external_reference', 'property_type',
            'offering_type', 'completion_status', 'furnishing',
            'rent_frequency'
        ]
        for field in string_fields:
            if field in row and row[field]:
                listing[field] = row[field]
        
        # Integer fields
        int_fields = ['bedrooms', 'bathrooms', 'parking', 'year_built', 'cheques']
        for field in int_fields:
            if field in row and row[field]:
                try:
                    listing[field] = int(row[field])
                except ValueError:
                    pass
        
        # Float fields
        float_fields = ['size', 'plot_size']
        for field in float_fields:
            if field in row and row[field]:
                try:
                    listing[field] = float(row[field])
                except ValueError:
                    pass
        
        # Price handling
        if 'price' in row and row['price']:
            try:
                listing['price'] = {
                    'amount': float(row['price']),
                    'currency': row.get('currency', 'AED')
                }
                if 'rent_frequency' in row and row['rent_frequency']:
                    listing['price']['frequency'] = row['rent_frequency']
            except ValueError:
                pass
        
        # Location handling
        location_fields = ['city', 'community', 'sub_community', 'building', 'street']
        location = {}
        for field in location_fields:
            if field in row and row[field]:
                location[field] = row[field]
        
        # Handle latitude/longitude
        if 'latitude' in row and 'longitude' in row:
            try:
                location['latitude'] = float(row['latitude'])
                location['longitude'] = float(row['longitude'])
            except ValueError:
                pass
        
        if location:
            listing['location'] = location
        
        # Images (comma-separated URLs)
        if 'images' in row and row['images']:
            listing['images'] = [img.strip() for img in row['images'].split(',')]
        
        # Amenities (comma-separated)
        if 'amenities' in row and row['amenities']:
            listing['amenities'] = [a.strip() for a in row['amenities'].split(',')]
        
        # Boolean fields
        if 'featured' in row:
            listing['featured'] = row['featured'].lower() in ('true', '1', 'yes')
        
        # Agent ID
        if 'agent_id' in row and row['agent_id']:
            listing['agent_id'] = row['agent_id']
        
        return listing
    
    def update_listings_bulk(
        self,
        updates: List[Dict[str, Any]],
        progress_callback: Callable[[int, int, str], None] = None
    ) -> BulkResult:
        """
        Bulk update existing listings
        
        Args:
            updates: List of dicts with 'id' and fields to update
            progress_callback: Optional progress callback
            
        Returns:
            BulkResult with operation results
        """
        result = BulkResult(total=len(updates))
        
        for i, update in enumerate(updates):
            listing_id = update.get('id')
            if not listing_id:
                result.add_failure(
                    reference=f"update_{i+1}",
                    error="Missing listing ID",
                    data=update
                )
                continue
            
            try:
                if progress_callback:
                    progress_callback(i + 1, result.total, f"Updating: {listing_id}")
                
                # Remove id from update data
                update_data = {k: v for k, v in update.items() if k != 'id'}
                
                response = self.client.update_listing(listing_id, update_data)
                result.add_success(listing_id, listing_id, response)
                
            except PropertyFinderAPIError as e:
                result.add_failure(listing_id, f"API Error ({e.status_code}): {e.message}", update)
            except Exception as e:
                result.add_failure(listing_id, str(e), update)
            
            if i < len(updates) - 1:
                time.sleep(self.delay_seconds)
        
        return result
    
    def delete_listings_bulk(
        self,
        listing_ids: List[str],
        progress_callback: Callable[[int, int, str], None] = None
    ) -> BulkResult:
        """
        Bulk delete listings
        
        Args:
            listing_ids: List of listing IDs to delete
            progress_callback: Optional progress callback
            
        Returns:
            BulkResult with operation results
        """
        result = BulkResult(total=len(listing_ids))
        
        for i, listing_id in enumerate(listing_ids):
            try:
                if progress_callback:
                    progress_callback(i + 1, result.total, f"Deleting: {listing_id}")
                
                response = self.client.delete_listing(listing_id)
                result.add_success(listing_id, listing_id, response)
                
            except PropertyFinderAPIError as e:
                result.add_failure(listing_id, f"API Error ({e.status_code}): {e.message}")
            except Exception as e:
                result.add_failure(listing_id, str(e))
            
            if i < len(listing_ids) - 1:
                time.sleep(self.delay_seconds)
        
        return result
    
    def export_results_to_file(self, result: BulkResult, output_path: str):
        """
        Export bulk operation results to a JSON file
        
        Args:
            result: BulkResult object
            output_path: Path for output file
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"Results exported to: {output_path}")
