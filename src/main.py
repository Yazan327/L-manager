#!/usr/bin/env python3
"""
PropertyFinder Listings Helper - Command Line Interface

Usage:
    python main.py create --file listings.json
    python main.py create --file listings.csv --publish
    python main.py list
    python main.py get <listing_id>
    python main.py delete <listing_id>
"""
import argparse
import json
import sys
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from api import PropertyFinderClient, PropertyFinderAPIError, Config
from models import PropertyListing, PropertyType, OfferingType, Location, Price
from utils import BulkListingManager


def print_json(data, indent=2):
    """Pretty print JSON data"""
    print(json.dumps(data, indent=indent, ensure_ascii=False, default=str))


def progress_callback(current: int, total: int, status: str):
    """Progress callback for bulk operations"""
    percentage = (current / total * 100) if total > 0 else 0
    bar_length = 30
    filled = int(bar_length * current / total) if total > 0 else 0
    bar = '█' * filled + '░' * (bar_length - filled)
    print(f"\r[{bar}] {percentage:.1f}% ({current}/{total}) - {status}", end='', flush=True)
    if current >= total:
        print()


def cmd_create_single(args, client: PropertyFinderClient):
    """Create a single listing interactively"""
    print("Creating a new listing...")
    print("-" * 40)
    
    # Collect basic information
    title = input("Title: ").strip()
    description = input("Description: ").strip()
    
    # Property type
    print("\nProperty Types: AP=Apartment, VH=Villa, TH=Townhouse, PH=Penthouse, OF=Office, LA=Land")
    prop_type = input("Property Type (default: AP): ").strip().upper() or "AP"
    
    # Offering type
    offer_type = input("Offering Type (sale/rent, default: sale): ").strip().lower() or "sale"
    
    # Price
    price_amount = float(input("Price (AED): ").strip() or "0")
    
    # Location
    city = input("City: ").strip()
    community = input("Community/Area: ").strip()
    
    # Details
    bedrooms = input("Bedrooms (optional): ").strip()
    bathrooms = input("Bathrooms (optional): ").strip()
    size = input("Size in sqft (optional): ").strip()
    
    # Reference
    reference = input("Reference Number (optional): ").strip()
    
    # Build listing
    listing = PropertyListing(
        title=title,
        description=description,
        property_type=PropertyType(prop_type) if prop_type else PropertyType.APARTMENT,
        offering_type=OfferingType(offer_type),
        price=Price(amount=price_amount),
        location=Location(city=city, community=community),
        bedrooms=int(bedrooms) if bedrooms else None,
        bathrooms=int(bathrooms) if bathrooms else None,
        size=float(size) if size else None,
        reference_number=reference or None
    )
    
    print("\nCreating listing...")
    try:
        result = client.create_listing(listing.to_dict())
        print("\n✓ Listing created successfully!")
        print_json(result)
    except PropertyFinderAPIError as e:
        print(f"\n✗ Error: {e.message}")
        if e.response:
            print_json(e.response)
        sys.exit(1)


def cmd_create_bulk(args, client: PropertyFinderClient):
    """Create listings from file (JSON or CSV)"""
    file_path = Path(args.file)
    
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)
    
    manager = BulkListingManager(client)
    
    print(f"Processing file: {file_path}")
    print("-" * 40)
    
    # Determine file type and process
    if file_path.suffix.lower() == '.json':
        result = manager.create_listings_from_json(
            str(file_path),
            progress_callback=progress_callback,
            publish=args.publish
        )
    elif file_path.suffix.lower() == '.csv':
        result = manager.create_listings_from_csv(
            str(file_path),
            progress_callback=progress_callback,
            publish=args.publish
        )
    else:
        print(f"Error: Unsupported file type: {file_path.suffix}")
        print("Supported formats: .json, .csv")
        sys.exit(1)
    
    # Print results
    print("\n" + "=" * 40)
    print("BULK CREATION RESULTS")
    print("=" * 40)
    print(f"Total:      {result.total}")
    print(f"Successful: {result.successful}")
    print(f"Failed:     {result.failed}")
    
    if result.errors:
        print("\nFailed listings:")
        for error in result.errors:
            print(f"  - {error['reference']}: {error['error']}")
    
    # Export results if requested
    if args.output:
        manager.export_results_to_file(result, args.output)


def cmd_list_listings(args, client: PropertyFinderClient):
    """List all listings"""
    print("Fetching listings...")
    
    try:
        result = client.get_listings(
            page=args.page,
            per_page=args.limit
        )
        print_json(result)
    except PropertyFinderAPIError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


def cmd_get_listing(args, client: PropertyFinderClient):
    """Get a single listing"""
    try:
        result = client.get_listing(args.listing_id)
        print_json(result)
    except PropertyFinderAPIError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


def cmd_update_listing(args, client: PropertyFinderClient):
    """Update a listing"""
    # Parse update data from JSON string or file
    if args.data:
        update_data = json.loads(args.data)
    elif args.file:
        with open(args.file, 'r') as f:
            update_data = json.load(f)
    else:
        print("Error: Provide --data or --file for update")
        sys.exit(1)
    
    try:
        result = client.update_listing(args.listing_id, update_data)
        print("✓ Listing updated successfully!")
        print_json(result)
    except PropertyFinderAPIError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


def cmd_delete_listing(args, client: PropertyFinderClient):
    """Delete a listing"""
    confirm = input(f"Are you sure you want to delete listing {args.listing_id}? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Cancelled.")
        return
    
    try:
        result = client.delete_listing(args.listing_id)
        print("✓ Listing deleted successfully!")
    except PropertyFinderAPIError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


def cmd_publish(args, client: PropertyFinderClient):
    """Publish a listing"""
    try:
        result = client.publish_listing(args.listing_id)
        print("✓ Listing published successfully!")
        print_json(result)
    except PropertyFinderAPIError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


def cmd_unpublish(args, client: PropertyFinderClient):
    """Unpublish a listing"""
    try:
        result = client.unpublish_listing(args.listing_id)
        print("✓ Listing unpublished successfully!")
        print_json(result)
    except PropertyFinderAPIError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


def cmd_reference_data(args, client: PropertyFinderClient):
    """Get reference data (property types, locations, etc.)"""
    try:
        if args.type == 'property-types':
            result = client.get_property_types()
        elif args.type == 'locations':
            result = client.get_locations(args.query)
        elif args.type == 'amenities':
            result = client.get_amenities()
        elif args.type == 'agents':
            result = client.get_agents()
        else:
            print(f"Unknown reference type: {args.type}")
            sys.exit(1)
        
        print_json(result)
    except PropertyFinderAPIError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


def cmd_account(args, client: PropertyFinderClient):
    """Get account information"""
    try:
        result = client.get_account()
        print_json(result)
    except PropertyFinderAPIError as e:
        print(f"Error: {e.message}")
        sys.exit(1)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='PropertyFinder Listings Helper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create listings from JSON file
  python main.py create --file listings.json
  
  # Create listings from CSV and publish them
  python main.py create --file listings.csv --publish
  
  # Create a single listing interactively
  python main.py create-single
  
  # List all listings
  python main.py list
  
  # Get a specific listing
  python main.py get LISTING_ID
  
  # Delete a listing
  python main.py delete LISTING_ID
  
  # Get reference data
  python main.py reference property-types
  python main.py reference locations --query "Dubai Marina"
        """
    )
    
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--token', help='API token (overrides .env)')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Create command (bulk)
    create_parser = subparsers.add_parser('create', help='Create listings from file')
    create_parser.add_argument('--file', '-f', required=True, help='JSON or CSV file path')
    create_parser.add_argument('--publish', '-p', action='store_true', help='Publish listings after creation')
    create_parser.add_argument('--output', '-o', help='Output file for results')
    
    # Create single command
    single_parser = subparsers.add_parser('create-single', help='Create a single listing interactively')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List all listings')
    list_parser.add_argument('--page', type=int, default=1, help='Page number')
    list_parser.add_argument('--limit', type=int, default=25, help='Items per page')
    
    # Get command
    get_parser = subparsers.add_parser('get', help='Get a listing by ID')
    get_parser.add_argument('listing_id', help='Listing ID')
    
    # Update command
    update_parser = subparsers.add_parser('update', help='Update a listing')
    update_parser.add_argument('listing_id', help='Listing ID')
    update_parser.add_argument('--data', '-d', help='JSON data to update')
    update_parser.add_argument('--file', '-f', help='JSON file with update data')
    
    # Delete command
    delete_parser = subparsers.add_parser('delete', help='Delete a listing')
    delete_parser.add_argument('listing_id', help='Listing ID')
    
    # Publish command
    publish_parser = subparsers.add_parser('publish', help='Publish a listing')
    publish_parser.add_argument('listing_id', help='Listing ID')
    
    # Unpublish command
    unpublish_parser = subparsers.add_parser('unpublish', help='Unpublish a listing')
    unpublish_parser.add_argument('listing_id', help='Listing ID')
    
    # Reference data command
    ref_parser = subparsers.add_parser('reference', help='Get reference data')
    ref_parser.add_argument('type', choices=['property-types', 'locations', 'amenities', 'agents'])
    ref_parser.add_argument('--query', '-q', help='Search query (for locations)')
    
    # Account command
    account_parser = subparsers.add_parser('account', help='Get account information')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(0)
    
    # Enable debug if requested
    if args.debug:
        Config.DEBUG = True
    
    # Validate configuration
    if not Config.validate():
        print("\nPlease configure your API credentials in the .env file")
        print("See .env.example for required fields")
        sys.exit(1)
    
    # Create client
    client = PropertyFinderClient(api_token=args.token if hasattr(args, 'token') and args.token else None)
    
    # Route to appropriate command
    commands = {
        'create': cmd_create_bulk,
        'create-single': cmd_create_single,
        'list': cmd_list_listings,
        'get': cmd_get_listing,
        'update': cmd_update_listing,
        'delete': cmd_delete_listing,
        'publish': cmd_publish,
        'unpublish': cmd_unpublish,
        'reference': cmd_reference_data,
        'account': cmd_account
    }
    
    if args.command in commands:
        commands[args.command](args, client)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
