"""
Database Configuration

Supports both SQLite (development) and PostgreSQL (production).
Uses SQLAlchemy 2.0+ async patterns.
"""
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

from .config import settings

# Create database directory if using SQLite
if settings.is_sqlite:
    db_path = Path(settings.DATABASE_URL.replace("sqlite:///", "").lstrip("./"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

# Engine configuration
if settings.is_sqlite:
    # SQLite configuration (for development)
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=settings.DEBUG
    )
else:
    # PostgreSQL configuration (for production)
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        echo=settings.DEBUG
    )

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


def get_db():
    """
    Dependency that provides a database session.
    
    Usage:
        @app.get("/items")
        def get_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize the database (create all tables)"""
    # Import all models to register them with Base
    from v2.models import (
        User, Listing, Lead, Customer, 
        Platform, PlatformListing, WebhookLog, SyncLog
    )
    
    Base.metadata.create_all(bind=engine)
    print(f"✓ Database initialized: {settings.DATABASE_URL}")
