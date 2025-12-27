"""
User Model

Dashboard users with role-based permissions.
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import relationship

from v2.core.database import Base
from v2.core.security import hash_password, verify_password


class User(Base):
    """Dashboard user with role-based permissions"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=True)
    
    # Role: admin, manager, agent, viewer
    role = Column(String(20), default='viewer')
    is_active = Column(Boolean, default=True)
    
    # PropertyFinder agent mapping
    pf_user_id = Column(Integer, nullable=True)
    pf_public_profile_id = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    
    # Relationships
    listings = relationship("Listing", back_populates="agent", foreign_keys="Listing.agent_id")
    assigned_leads = relationship("Lead", back_populates="assigned_to", foreign_keys="Lead.assigned_to_id")
    
    # Role permissions mapping
    ROLES = {
        'admin': {
            'name': 'Administrator',
            'permissions': ['view', 'create', 'edit', 'delete', 'publish', 'bulk_upload', 
                          'manage_users', 'settings', 'leads', 'customers', 'integrations']
        },
        'manager': {
            'name': 'Manager',
            'permissions': ['view', 'create', 'edit', 'delete', 'publish', 'bulk_upload', 
                          'leads', 'customers']
        },
        'agent': {
            'name': 'Agent',
            'permissions': ['view', 'create', 'edit', 'leads']
        },
        'viewer': {
            'name': 'Viewer',
            'permissions': ['view']
        }
    }
    
    def set_password(self, password: str):
        """Hash and set the user's password"""
        self.password_hash = hash_password(password)
    
    def check_password(self, password: str) -> bool:
        """Verify the user's password"""
        return verify_password(password, self.password_hash)
    
    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission"""
        role_perms = self.ROLES.get(self.role, {}).get('permissions', [])
        return permission in role_perms
    
    def get_permissions(self) -> List[str]:
        """Get all permissions for the user's role"""
        return self.ROLES.get(self.role, {}).get('permissions', [])
    
    def to_dict(self) -> dict:
        """Convert to dictionary (without password)"""
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'phone': self.phone,
            'role': self.role,
            'role_name': self.ROLES.get(self.role, {}).get('name', 'Unknown'),
            'permissions': self.get_permissions(),
            'is_active': self.is_active,
            'pf_user_id': self.pf_user_id,
            'pf_public_profile_id': self.pf_public_profile_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }
