#!/usr/bin/env python3
"""
Example: How to use PropertyFinder Listings Helper as a library

This script demonstrates various ways to interact with the PropertyFinder API
using this helper library.
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from api import PropertyFinderClient, PropertyFinderAPIError, Config
from models import (
    PropertyListing, PropertyType, OfferingType, 
    Location, Price, CompletionStatus, FurnishingStatus
)
from utils import BulkListingManager


def example_create_single_listing():
    """Example: Create a single listing programmatically"""
    print("\n=== Creating Single Listing ===\n")
    
    # Initialize client (uses credentials from .env)
    client = PropertyFinderClient()
    
    # Create a listing object
    listing = PropertyListing(
        title="Stunning 3BR Apartment with Sea View",
        title_arabic="شقة رائعة 3 غرف نوم مع إطلالة بحرية",
        description="Luxurious 3 bedroom apartment in prime location with breathtaking sea views. Modern finishes, spacious layout, and premium amenities.",
        description_arabic="شقة فاخرة 3 غرف نوم في موقع متميز مع إطلالات خلابة على البحر",
        property_type=PropertyType.APARTMENT,
        offering_type=OfferingType.SALE,
        price=Price(amount=3500000, currency="AED"),
        location=Location(
            city="Dubai",
            community="Dubai Marina",
            sub_community="Marina Gate",
            building="Marina Gate 2",
            latitude=25.0806,
            longitude=55.1402
        ),
        bedrooms=3,
        bathrooms=4,
        size=2100,
        reference_number="MY-REF-001",
        permit_number="RERA-12345",
        completion_status=CompletionStatus.READY,
        furnishing=FurnishingStatus.FURNISHED,
        parking_spaces=2,
        amenities=["gym", "pool", "concierge", "security", "balcony"],
        images=[
            "https://example.com/image1.jpg",
            "https://example.com/image2.jpg",
            "https://example.com/image3.jpg"
        ],
        featured=True
    )
    
    # Convert to API format and create
    try:
        result = client.create_listing(listing.to_dict())
        print(f"✓ Listing created successfully!")
        print(f"  Listing ID: {result.get('id')}")
        return result
    except PropertyFinderAPIError as e:
        print(f"✗ Error creating listing: {e.message}")
        return None


def example_bulk_create_from_json():
    """Example: Bulk create listings from JSON file"""
    print("\n=== Bulk Create from JSON ===\n")
    
    client = PropertyFinderClient()
    manager = BulkListingManager(client)
    
    def progress(current, total, status):
        print(f"  Progress: {current}/{total} - {status}")
    
    try:
        result = manager.create_listings_from_json(
            'samples/sample_listings.json',
            progress_callback=progress,
            publish=False  # Don't publish, just create as drafts
        )
        
        print(f"\n✓ Bulk operation completed!")
        print(f"  Total: {result.total}")
        print(f"  Successful: {result.successful}")
        print(f"  Failed: {result.failed}")
        
        if result.errors:
            print("\n  Errors:")
            for error in result.errors:
                print(f"    - {error['reference']}: {error['error']}")
        
        return result
    except Exception as e:
        print(f"✗ Error: {e}")
        return None


def example_bulk_create_from_list():
    """Example: Bulk create listings from a Python list"""
    print("\n=== Bulk Create from List ===\n")
    
    # Define listings as dictionaries
    listings = [
        {
            "title": "Modern Studio in JLT",
            "description": "Compact studio apartment with city views",
            "property_type": "AP",
            "offering_type": "rent",
            "price": {"amount": 45000, "currency": "AED", "frequency": "yearly"},
            "location": {"city": "Dubai", "community": "Jumeirah Lake Towers"},
            "bedrooms": 0,
            "bathrooms": 1,
            "size": 450,
            "reference_number": "PROG-001"
        },
        {
            "title": "Cozy 1BR in Business Bay",
            "description": "Well-maintained 1 bedroom with canal view",
            "property_type": "AP",
            "offering_type": "rent",
            "price": {"amount": 65000, "currency": "AED", "frequency": "yearly"},
            "location": {"city": "Dubai", "community": "Business Bay"},
            "bedrooms": 1,
            "bathrooms": 1,
            "size": 750,
            "reference_number": "PROG-002"
        }
    ]
    
    client = PropertyFinderClient()
    manager = BulkListingManager(client)
    
    try:
        result = manager.create_listings_from_list(listings, publish=False)
        print(f"✓ Created {result.successful} of {result.total} listings")
        return result
    except Exception as e:
        print(f"✗ Error: {e}")
        return None


def example_list_and_update():
    """Example: List listings and update one"""
    print("\n=== List and Update ===\n")
    
    client = PropertyFinderClient()
    
    try:
        # Get first page of listings
        listings = client.get_listings(page=1, per_page=10)
        print(f"Found {len(listings.get('data', []))} listings")
        
        # Update first listing if exists
        data = listings.get('data', [])
        if data:
            listing_id = data[0].get('id')
            print(f"\nUpdating listing {listing_id}...")
            
            # Update price
            result = client.patch_listing(listing_id, {
                "price": {"amount": 3000000}
            })
            print(f"✓ Listing updated!")
        
    except PropertyFinderAPIError as e:
        print(f"✗ Error: {e.message}")


def example_get_reference_data():
    """Example: Get reference data (property types, locations, etc.)"""
    print("\n=== Reference Data ===\n")
    
    client = PropertyFinderClient()
    
    try:
        # Get property types
        print("Property Types:")
        types = client.get_property_types()
        for t in types.get('data', [])[:5]:
            print(f"  - {t.get('name')}: {t.get('code')}")
        
        # Search locations
        print("\nLocations in Dubai Marina:")
        locations = client.get_locations(query="Dubai Marina")
        for loc in locations.get('data', [])[:5]:
            print(f"  - {loc.get('name')}")
        
        # Get amenities
        print("\nAvailable Amenities:")
        amenities = client.get_amenities()
        for a in amenities.get('data', [])[:5]:
            print(f"  - {a.get('name')}")
            
    except PropertyFinderAPIError as e:
        print(f"✗ Error: {e.message}")


def main():
    """Run examples"""
    print("=" * 50)
    print("PropertyFinder Listings Helper - Examples")
    print("=" * 50)
    
    # Check configuration
    if not Config.validate():
        print("\n⚠ Please configure your API credentials in .env file")
        print("  Copy .env.example to .env and fill in your credentials")
        return
    
    print(f"\nAPI Base URL: {Config.API_BASE_URL}")
    print(f"Debug Mode: {Config.DEBUG}")
    
    # Uncomment examples you want to run:
    
    # example_create_single_listing()
    # example_bulk_create_from_json()
    # example_bulk_create_from_list()
    # example_list_and_update()
    # example_get_reference_data()
    
    print("\n✓ Uncomment the examples in main() to run them")
    print("  Make sure to configure your .env file first!")


if __name__ == '__main__':
    main()
