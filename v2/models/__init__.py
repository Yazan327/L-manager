"""
Database Models for L-Manager v2

Includes:
- User: Authentication and authorization
- Listing: Property listings
- Lead: Incoming leads from all sources
- Customer: Customer/prospect management (CRM)
- Platform: Integration platforms (PropertyFinder, Bayut, Website)
- PlatformListing: Listing status per platform
- WebhookLog: Incoming webhook logs
- SyncLog: Sync history
"""
from .user import User
from .listing import Listing
from .lead import Lead, LeadSource, LeadStatus
from .customer import Customer, CustomerInteraction
from .platform import Platform, PlatformListing, PlatformType
from .sync import SyncLog, WebhookLog, CacheEntry

__all__ = [
    'User',
    'Listing',
    'Lead', 'LeadSource', 'LeadStatus',
    'Customer', 'CustomerInteraction',
    'Platform', 'PlatformListing', 'PlatformType',
    'SyncLog', 'WebhookLog', 'CacheEntry'
]
