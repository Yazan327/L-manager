"""
PropertyFinder Enterprise API Configuration

Based on the official PropertyFinder Enterprise API (OpenAPI 3.1.0)
Base URL: https://atlas.propertyfinder.com/v1
Authentication: OAuth 2.0 with API Key + API Secret → JWT token
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)


class Config:
    """Configuration class for PropertyFinder Enterprise API"""
    
    # Enterprise API Base URL (atlas.propertyfinder.com)
    API_BASE_URL = os.getenv('PF_API_BASE_URL', 'https://atlas.propertyfinder.com/v1')
    
    # Enterprise API Authentication
    # Get these from PF Expert → Settings → API Keys → Type: "API Integration"
    API_KEY = os.getenv('PF_API_KEY', '')
    API_SECRET = os.getenv('PF_API_SECRET', '')
    
    # Legacy auth (for backward compatibility, prefer API_KEY/API_SECRET)
    API_TOKEN = os.getenv('PF_API_TOKEN', '')
    CLIENT_ID = os.getenv('PF_CLIENT_ID', '')
    CLIENT_SECRET = os.getenv('PF_CLIENT_SECRET', '')
    
    # Company Information
    AGENCY_ID = os.getenv('PF_AGENCY_ID', '')
    BROKER_ID = os.getenv('PF_BROKER_ID', '')
    
    # Request Settings
    REQUEST_TIMEOUT = int(os.getenv('PF_REQUEST_TIMEOUT', '30'))
    MAX_RETRIES = int(os.getenv('PF_MAX_RETRIES', '3'))
    DEBUG = os.getenv('PF_DEBUG', 'false').lower() == 'true'
    USER_AGENT = os.getenv(
        'PF_USER_AGENT',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    )
    ACCEPT_LANGUAGE = os.getenv('PF_ACCEPT_LANGUAGE', 'en-US,en;q=0.9')
    SKIP_MEDIA = os.getenv('PF_SKIP_MEDIA', 'false').lower() == 'true'
    
    # Bulk Operations
    BULK_BATCH_SIZE = int(os.getenv('PF_BULK_BATCH_SIZE', '50'))
    BULK_DELAY_SECONDS = float(os.getenv('PF_BULK_DELAY_SECONDS', '1'))

    # Media warnings
    MAX_IMAGES_WARN = int(os.getenv('PF_MAX_IMAGES_WARN', '15'))
    
    # Default Values for Bulk Upload
    DEFAULT_AGENT_EMAIL = os.getenv('PF_DEFAULT_AGENT_EMAIL', '')
    DEFAULT_OWNER_EMAIL = os.getenv('PF_DEFAULT_OWNER_EMAIL', '')
    
    # Scheduler Settings
    SCHEDULER_ENABLED = os.getenv('PF_SCHEDULER_ENABLED', 'true').lower() == 'true'
    SCHEDULER_INTERVAL_MINUTES = int(os.getenv('PF_SCHEDULER_INTERVAL_MINUTES', '30'))
    
    # Token cache settings (JWT token expires in 30 minutes)
    TOKEN_EXPIRY_BUFFER = 60  # Request new token 60 seconds before expiry
    
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration"""
        # Check for Enterprise API credentials
        if cls.API_KEY and cls.API_SECRET:
            return True
        
        # Check for legacy credentials
        if cls.API_TOKEN:
            print("Warning: Using legacy API_TOKEN. Consider switching to API_KEY/API_SECRET.")
            return True
            
        print("Missing required configuration: PF_API_KEY and PF_API_SECRET")
        print("Get these from PF Expert → Settings → API Keys → Type: 'API Integration'")
        return False
    
    @classmethod
    def has_enterprise_credentials(cls) -> bool:
        """Check if Enterprise API credentials are configured"""
        return bool(cls.API_KEY and cls.API_SECRET)
