# PropertyFinder Listings Helper

This is a Python application for managing property listings via the PropertyFinder API.

## Project Structure
- `src/` - Main source code
- `src/api/` - API client and authentication
- `src/models/` - Data models for listings
- `src/utils/` - Utility functions
- `src/dashboard/` - Flask web dashboard with user authentication
- `src/database/` - SQLAlchemy models for local storage
- `.env` - Environment configuration (API credentials)
- `documentation/propertyfinder-api.json` - **PropertyFinder Enterprise API OpenAPI 3.1.0 specification** (official documentation)

## PropertyFinder API Documentation
The `documentation/propertyfinder-api.json` file contains the complete PropertyFinder Enterprise API specification including:
- **Authentication**: OAuth 2.0 with API Key/Secret → JWT token exchange
- **Endpoints**: Auth, Users, Listings, Locations, Leads, Credits, Compliances, Statistics, Webhooks
- **Base URL**: `https://atlas.propertyfinder.com/v1`
- **Rate Limits**: 60 req/min for auth, 650 req/min for other endpoints
- **Token Expiry**: 30 minutes (no refresh token)

### Key API Endpoints (from openapi.json):
- `POST /v1/auth/token` - Get JWT access token
- `GET /v1/listings` - List listings with pagination
- `POST /v1/listings` - Create listing (draft)
- `PUT /v1/listings/{id}` - Update listing
- `POST /v1/listings/{id}/publish` - Publish listing
- `POST /v1/listings/{id}/unpublish` - Unpublish listing
- `GET /v1/users` - List users/agents
- `GET /v1/locations` - Search locations
- `GET /v1/credits/balance` - Get credits balance
- `GET /v1/leads` - Get leads
- `GET /v1/compliances/{permitNumber}/{licenseNumber}` - DLD/ADREC compliance

### Region-Specific Rules (UAE):
- Dubai: DLD compliance required (permit validation)
- Abu Dhabi: ADREC compliance required
- Listings must be associated with Public Profile and valid Location

## Setup
1. Copy `.env.example` to `.env`
2. Fill in your PropertyFinder API credentials
3. Install dependencies: `pip install -r requirements.txt`
4. Run the application: `python src/main.py`

## Features
- Create single listings
- Bulk listing creation from CSV/JSON
- Update existing listings
- Delete listings
- List all listings
- Web dashboard with user management
- Role-based access control (admin, manager, agent, viewer)

## Dashboard Login
Default credentials: `admin@listings.local` / `admin123`
