# PropertyFinder Listings Helper

A Python application for managing property listings via the PropertyFinder API. Supports single listing creation, bulk uploads from CSV/JSON, and full CRUD operations.

## Features

- ✅ **Single Listing Creation** - Create listings interactively or programmatically
- ✅ **Bulk Listing Creation** - Import from JSON or CSV files
- ✅ **Full CRUD Operations** - Create, Read, Update, Delete listings
- ✅ **Publish/Unpublish** - Control listing visibility
- ✅ **Progress Tracking** - Real-time progress for bulk operations
- ✅ **Error Handling** - Comprehensive error reporting with retry logic
- ✅ **Rate Limiting** - Automatic handling of API rate limits

## Project Structure

```
listings_m/
├── .env                    # Your API credentials (create from .env.example)
├── .env.example           # Environment template
├── requirements.txt       # Python dependencies
├── samples/               # Sample data files
│   ├── sample_listings.json
│   └── sample_listings.csv
└── src/
    ├── main.py            # CLI entry point
    ├── api/
    │   ├── client.py      # PropertyFinder API client
    │   └── config.py      # Configuration management
    ├── models/
    │   └── listing.py     # Data models for listings
    └── utils/
        └── bulk_operations.py  # Bulk import/export utilities
```

## Quick Start

### 1. Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
# Edit .env with your PropertyFinder API credentials
```

### 2. Configure API Credentials

Edit `.env` file with your credentials:

```env
PF_API_TOKEN=your_api_token_here
PF_AGENCY_ID=your_agency_id
```

### 3. Usage

#### Create Listings from JSON File

```bash
cd src
python main.py create --file ../samples/sample_listings.json
```

#### Create Listings from CSV File

```bash
python main.py create --file ../samples/sample_listings.csv
```

#### Create and Publish Listings

```bash
python main.py create --file listings.json --publish
```

#### Export Results

```bash
python main.py create --file listings.json --output results.json
```

#### List All Listings

```bash
python main.py list
python main.py list --page 2 --limit 50
```

#### Get Single Listing

```bash
python main.py get LISTING_ID
```

#### Update a Listing

```bash
python main.py update LISTING_ID --data '{"price": {"amount": 3000000}}'
```

#### Delete a Listing

```bash
python main.py delete LISTING_ID
```

#### Publish/Unpublish

```bash
python main.py publish LISTING_ID
python main.py unpublish LISTING_ID
```

#### Get Reference Data

```bash
python main.py reference property-types
python main.py reference locations --query "Dubai Marina"
python main.py reference amenities
python main.py reference agents
```

## Using as a Library

```python
from src import (
    PropertyFinderClient,
    BulkListingManager,
    PropertyListing,
    PropertyType,
    OfferingType,
    Location,
    Price
)

# Initialize client
client = PropertyFinderClient()

# Create a single listing
listing = PropertyListing(
    title="Luxury 2BR Apartment",
    description="Beautiful apartment with sea views",
    property_type=PropertyType.APARTMENT,
    offering_type=OfferingType.SALE,
    price=Price(amount=2500000),
    location=Location(city="Dubai", community="Dubai Marina"),
    bedrooms=2,
    bathrooms=3,
    size=1450
)

result = client.create_listing(listing.to_dict())
print(f"Created listing: {result['id']}")

# Bulk create from file
manager = BulkListingManager(client)
result = manager.create_listings_from_json('listings.json', publish=True)
print(f"Created {result.successful} of {result.total} listings")
```

## JSON File Format

```json
[
  {
    "title": "Luxury 2BR Apartment",
    "description": "Beautiful apartment with sea views",
    "property_type": "AP",
    "offering_type": "sale",
    "price": {
      "amount": 2500000,
      "currency": "AED"
    },
    "location": {
      "city": "Dubai",
      "community": "Dubai Marina"
    },
    "bedrooms": 2,
    "bathrooms": 3,
    "size": 1450,
    "reference_number": "REF-001",
    "amenities": ["gym", "pool", "security"]
  }
]
```

## CSV File Format

```csv
title,description,property_type,offering_type,price,currency,city,community,bedrooms,bathrooms,size,reference_number,amenities
"Luxury 2BR","Sea views",AP,sale,2500000,AED,Dubai,Dubai Marina,2,3,1450,REF-001,"gym,pool"
```

## Property Types

| Code | Type |
|------|------|
| AP | Apartment |
| VH | Villa |
| TH | Townhouse |
| PH | Penthouse |
| OF | Office |
| RE | Retail |
| WH | Warehouse |
| LA | Land |

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `PF_API_BASE_URL` | API base URL | No (default: https://app.propertyfinder.ae/api/v1) |
| `PF_API_TOKEN` | Your API token | **Yes** |
| `PF_CLIENT_ID` | OAuth client ID | No |
| `PF_CLIENT_SECRET` | OAuth client secret | No |
| `PF_AGENCY_ID` | Your agency ID | No |
| `PF_BROKER_ID` | Broker ID | No |
| `PF_REQUEST_TIMEOUT` | Request timeout in seconds | No (default: 30) |
| `PF_MAX_RETRIES` | Max retry attempts | No (default: 3) |
| `PF_DEBUG` | Enable debug logging | No (default: false) |
| `PF_BULK_BATCH_SIZE` | Bulk operation batch size | No (default: 50) |
| `PF_BULK_DELAY_SECONDS` | Delay between bulk requests | No (default: 1) |

## Error Handling

The application handles various error scenarios:

- **Authentication errors**: Check your API token in `.env`
- **Rate limiting**: Automatic retry with backoff
- **Network errors**: Automatic retry with exponential backoff
- **Validation errors**: Detailed error messages in bulk results

## Debug Mode

Enable debug mode to see detailed request/response logs:

```bash
python main.py --debug create --file listings.json
```

Or set in `.env`:

```env
PF_DEBUG=true
```

## License

MIT License
