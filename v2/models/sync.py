"""
Sync and Logging Models

Track sync operations, webhooks, and caching.
"""
from datetime import datetime
from typing import Optional
import json
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey

from v2.core.database import Base


class SyncLog(Base):
    """Log sync operations with platforms"""
    __tablename__ = 'sync_logs'
    
    id = Column(Integer, primary_key=True, index=True)
    
    # What was synced
    sync_type = Column(String(50), nullable=False)  # listings, leads, stats, full
    platform = Column(String(50))  # propertyfinder, bayut, website, all
    direction = Column(String(20), default='pull')  # pull, push
    
    # Results
    status = Column(String(20), default='running')  # running, success, failed, partial
    items_processed = Column(Integer, default=0)
    items_created = Column(Integer, default=0)
    items_updated = Column(Integer, default=0)
    items_failed = Column(Integer, default=0)
    
    # Errors
    error_message = Column(Text)
    error_details = Column(Text)  # JSON array of individual errors
    
    # Timing
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    duration_seconds = Column(Float)
    
    # Trigger
    triggered_by = Column(String(50))  # scheduler, manual, webhook
    triggered_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    
    def complete(self, status: str = 'success', error: str = None):
        """Mark sync as complete"""
        self.status = status
        self.completed_at = datetime.utcnow()
        self.duration_seconds = (self.completed_at - self.started_at).total_seconds()
        if error:
            self.error_message = error
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'sync_type': self.sync_type,
            'platform': self.platform,
            'direction': self.direction,
            'status': self.status,
            'items_processed': self.items_processed,
            'items_created': self.items_created,
            'items_updated': self.items_updated,
            'items_failed': self.items_failed,
            'error_message': self.error_message,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'duration_seconds': self.duration_seconds,
            'triggered_by': self.triggered_by,
        }


class WebhookLog(Base):
    """Log incoming webhooks from external services"""
    __tablename__ = 'webhook_logs'
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Source
    source = Column(String(50), nullable=False)  # zapier, propertyfinder, bayut, facebook, etc.
    webhook_type = Column(String(50))  # lead, listing_update, etc.
    
    # Request details
    method = Column(String(10))
    path = Column(String(500))
    headers = Column(Text)  # JSON
    body = Column(Text)  # Raw body
    ip_address = Column(String(50))
    
    # Processing
    status = Column(String(20), default='received')  # received, processing, processed, failed
    processed_at = Column(DateTime)
    error_message = Column(Text)
    
    # Result
    result_type = Column(String(50))  # lead_created, listing_updated, etc.
    result_id = Column(Integer)  # ID of created/updated record
    
    # Timestamps
    received_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'source': self.source,
            'webhook_type': self.webhook_type,
            'method': self.method,
            'path': self.path,
            'status': self.status,
            'error_message': self.error_message,
            'result_type': self.result_type,
            'result_id': self.result_id,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'processed_at': self.processed_at.isoformat() if self.processed_at else None,
        }


class CacheEntry(Base):
    """Cache data from external APIs"""
    __tablename__ = 'cache_entries'
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Cache key
    cache_type = Column(String(50), nullable=False, index=True)  # pf_listings, pf_users, bayut_listings, etc.
    cache_key = Column(String(200), default='default')  # Additional key for filtering
    
    # Data
    data = Column(Text)  # JSON serialized data
    count = Column(Integer, default=0)
    
    # Metadata
    source = Column(String(50))  # propertyfinder, bayut, etc.
    error_message = Column(Text)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime)
    
    @classmethod
    def get(cls, db, cache_type: str, cache_key: str = 'default'):
        """Get cached data"""
        entry = db.query(cls).filter(
            cls.cache_type == cache_type,
            cls.cache_key == cache_key
        ).first()
        
        if entry and entry.data:
            try:
                return json.loads(entry.data)
            except:
                return None
        return None
    
    @classmethod
    def set(cls, db, cache_type: str, data, cache_key: str = 'default', ttl_minutes: int = 30):
        """Set cached data"""
        entry = db.query(cls).filter(
            cls.cache_type == cache_type,
            cls.cache_key == cache_key
        ).first()
        
        if not entry:
            entry = cls(cache_type=cache_type, cache_key=cache_key)
            db.add(entry)
        
        entry.data = json.dumps(data, default=str)
        entry.count = len(data) if isinstance(data, list) else 1
        entry.updated_at = datetime.utcnow()
        entry.expires_at = datetime.utcnow() + datetime.timedelta(minutes=ttl_minutes) if ttl_minutes else None
        
        db.commit()
        return entry
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'id': self.id,
            'cache_type': self.cache_type,
            'cache_key': self.cache_key,
            'count': self.count,
            'source': self.source,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
        }
