"""
PropertyFinder Enterprise API Configuration

Based on the official PropertyFinder Enterprise API (OpenAPI 3.1.0)
Base URL: https://atlas.propertyfinder.com/v1
Authentication: OAuth 2.0 with API Key + API Secret → JWT token
"""
import os
from pathlib import Path
from dotenv import load_dotenv


def _clean_env(value: str) -> str:
    """Trim whitespace and surrounding quotes from env values."""
    if value is None:
        return ''
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    return value.strip()

# Load environment variables
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)


class Config:
    """Configuration class for PropertyFinder Enterprise API"""
    
    # Enterprise API Base URL (atlas.propertyfinder.com)
    API_BASE_URL = _clean_env(os.getenv('PF_API_BASE_URL', 'https://atlas.propertyfinder.com/v1'))
    
    # Enterprise API Authentication
    # Get these from PF Expert → Settings → API Keys → Type: "API Integration"
    API_KEY = _clean_env(os.getenv('PF_API_KEY', ''))
    API_SECRET = _clean_env(os.getenv('PF_API_SECRET', ''))
    
    # Legacy auth (for backward compatibility, prefer API_KEY/API_SECRET)
    API_TOKEN = _clean_env(os.getenv('PF_API_TOKEN', ''))
    CLIENT_ID = _clean_env(os.getenv('PF_CLIENT_ID', ''))
    CLIENT_SECRET = _clean_env(os.getenv('PF_CLIENT_SECRET', ''))
    
    # Company Information
    AGENCY_ID = _clean_env(os.getenv('PF_AGENCY_ID', ''))
    BROKER_ID = _clean_env(os.getenv('PF_BROKER_ID', ''))
    
    # Request Settings
    REQUEST_TIMEOUT = int(_clean_env(os.getenv('PF_REQUEST_TIMEOUT', '30')))
    MAX_RETRIES = int(_clean_env(os.getenv('PF_MAX_RETRIES', '3')))
    DEBUG = _clean_env(os.getenv('PF_DEBUG', 'false')).lower() == 'true'
    USER_AGENT = _clean_env(os.getenv(
        'PF_USER_AGENT',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ))
    ACCEPT_LANGUAGE = _clean_env(os.getenv('PF_ACCEPT_LANGUAGE', 'en-US,en;q=0.9'))
    SKIP_MEDIA = _clean_env(os.getenv('PF_SKIP_MEDIA', 'false')).lower() == 'true'
    HTTP_PROXY = _clean_env(os.getenv('PF_HTTP_PROXY', ''))
    HTTPS_PROXY = _clean_env(os.getenv('PF_HTTPS_PROXY', ''))
    WEBHOOK_SECRET = _clean_env(os.getenv('PF_WEBHOOK_SECRET', ''))
    WEBHOOK_URL = _clean_env(os.getenv('PF_WEBHOOK_URL', ''))
    
    # Bulk Operations
    BULK_BATCH_SIZE = int(_clean_env(os.getenv('PF_BULK_BATCH_SIZE', '50')))
    BULK_DELAY_SECONDS = float(_clean_env(os.getenv('PF_BULK_DELAY_SECONDS', '1')))

    # Media warnings
    MAX_IMAGES_WARN = int(_clean_env(os.getenv('PF_MAX_IMAGES_WARN', '15')))
    
    # Default Values for Bulk Upload
    DEFAULT_AGENT_EMAIL = _clean_env(os.getenv('PF_DEFAULT_AGENT_EMAIL', ''))
    DEFAULT_OWNER_EMAIL = _clean_env(os.getenv('PF_DEFAULT_OWNER_EMAIL', ''))
    
    # Scheduler Settings
    SCHEDULER_ENABLED = _clean_env(os.getenv('PF_SCHEDULER_ENABLED', 'true')).lower() == 'true'
    SCHEDULER_INTERVAL_MINUTES = int(_clean_env(os.getenv('PF_SCHEDULER_INTERVAL_MINUTES', '30')))
    
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
