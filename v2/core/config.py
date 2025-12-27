"""
Application Configuration

Uses Pydantic Settings for environment variable management.
Supports both development (SQLite) and production (PostgreSQL).
"""
import os
from pathlib import Path
from typing import Optional, List
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # ===========================================
    # APPLICATION
    # ===========================================
    APP_NAME: str = "L-Manager"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = Field(default=False, alias="DEBUG")
    SECRET_KEY: str = Field(default="change-me-in-production", alias="SECRET_KEY")
    
    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    
    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5000"]
    
    # ===========================================
    # DATABASE
    # ===========================================
    # Use SQLite for development, PostgreSQL for production
    DATABASE_URL: str = Field(
        default="sqlite:///./data/lmanager.db",
        alias="DATABASE_URL"
    )
    
    # PostgreSQL settings (when DATABASE_URL starts with postgresql://)
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    
    # ===========================================
    # REDIS (for Celery and caching)
    # ===========================================
    REDIS_URL: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    
    # ===========================================
    # JWT AUTHENTICATION
    # ===========================================
    JWT_SECRET_KEY: str = Field(default="jwt-secret-change-me", alias="JWT_SECRET_KEY")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    
    # ===========================================
    # PROPERTYFINDER API
    # ===========================================
    PF_API_BASE_URL: str = Field(
        default="https://atlas.propertyfinder.com/v1",
        alias="PF_API_BASE_URL"
    )
    PF_API_KEY: str = Field(default="", alias="PF_API_KEY")
    PF_API_SECRET: str = Field(default="", alias="PF_API_SECRET")
    
    # ===========================================
    # BAYUT API
    # ===========================================
    BAYUT_API_BASE_URL: str = Field(
        default="https://api.bayut.com/v1",
        alias="BAYUT_API_BASE_URL"
    )
    BAYUT_API_KEY: str = Field(default="", alias="BAYUT_API_KEY")
    BAYUT_API_SECRET: str = Field(default="", alias="BAYUT_API_SECRET")
    
    # ===========================================
    # CUSTOM WEBSITE API
    # ===========================================
    WEBSITE_API_BASE_URL: str = Field(default="", alias="WEBSITE_API_BASE_URL")
    WEBSITE_API_KEY: str = Field(default="", alias="WEBSITE_API_KEY")
    
    # ===========================================
    # ZAPIER / WEBHOOKS
    # ===========================================
    ZAPIER_WEBHOOK_SECRET: str = Field(default="", alias="ZAPIER_WEBHOOK_SECRET")
    
    # ===========================================
    # SCHEDULER
    # ===========================================
    SCHEDULER_ENABLED: bool = Field(default=True, alias="PF_SCHEDULER_ENABLED")
    SCHEDULER_INTERVAL_MINUTES: int = Field(default=30, alias="PF_SCHEDULER_INTERVAL_MINUTES")
    
    # ===========================================
    # ADMIN USER
    # ===========================================
    ADMIN_EMAIL: str = Field(default="admin@listings.local", alias="ADMIN_EMAIL")
    ADMIN_PASSWORD: str = Field(default="admin123", alias="ADMIN_PASSWORD")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"
    
    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite database"""
        return self.DATABASE_URL.startswith("sqlite")
    
    @property
    def is_postgres(self) -> bool:
        """Check if using PostgreSQL database"""
        return self.DATABASE_URL.startswith("postgresql")


# Global settings instance
settings = Settings()
