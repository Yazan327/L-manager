"""
Database Models for Local Listings Storage
"""
import re
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def convert_google_drive_url(url):
    """Convert Google Drive share URL to direct CDN URL.
    
    Supports formats:
    - https://drive.google.com/file/d/FILE_ID/view
    - https://drive.google.com/open?id=FILE_ID
    - https://drive.google.com/uc?id=FILE_ID
    
    Returns CDN URL: https://lh3.googleusercontent.com/d/FILE_ID
    This format is more reliable for external services like PropertyFinder.
    """
    if not url:
        return url
    
    url = url.strip()
    
    # Already a CDN URL
    if 'lh3.googleusercontent.com' in url:
        return url
    
    # Not a Google Drive URL
    if 'drive.google.com' not in url:
        return url
    
    # Extract file ID from various Google Drive URL formats
    file_id = None
    
    # Format: /file/d/FILE_ID/view or /file/d/FILE_ID
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
    
    # Format: /open?id=FILE_ID or /uc?id=FILE_ID
    if not file_id:
        match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
        if match:
            file_id = match.group(1)
    
    if file_id:
        return f'https://lh3.googleusercontent.com/d/{file_id}'
    
    # Could not extract file ID, return original
    return url


# ==================== USER & AUTHENTICATION ====================

class User(db.Model):
    """Dashboard user with granular section-based permissions"""
    __tablename__ = 'users'
    __table_args__ = (
        db.Index('idx_users_role', 'role'),
        db.Index('idx_users_is_active', 'is_active'),
        db.Index('idx_users_pf_agent_id', 'pf_agent_id'),
        db.Index('idx_users_created_at', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100), nullable=False, index=True)
    role = db.Column(db.String(20), default='user')  # admin, user (admin has all permissions)
    preferred_language = db.Column(db.String(5), nullable=False, default='en')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Section-based permissions stored as JSON
    # Format: {"listings": {"view": true, "create": true, "edit": false, ...}, "leads": {...}, ...}
    section_permissions = db.Column(db.Text, nullable=True, default='{}')
    
    # Locked PF Agent ID - user can only see/manage listings for this agent
    pf_agent_id = db.Column(db.String(100), nullable=True)
    pf_agent_name = db.Column(db.String(100), nullable=True)
    
    # Section definitions with their available actions
    SECTIONS = {
        'dashboard': {
            'name': 'Dashboard',
            'icon': 'fa-home',
            'description': 'Main dashboard and overview',
            'actions': ['view']
        },
        'listings': {
            'name': 'Listings',
            'icon': 'fa-building',
            'description': 'Property listings management',
            'actions': ['view', 'create', 'edit', 'delete', 'publish', 'bulk_upload']
        },
        'leads': {
            'name': 'Leads',
            'icon': 'fa-phone',
            'description': 'Lead management and CRM',
            'actions': ['view', 'create', 'edit', 'delete', 'assign']
        },
        'insights': {
            'name': 'Insights',
            'icon': 'fa-chart-line',
            'description': 'Analytics and statistics',
            'actions': ['view']
        },
        'tasks': {
            'name': 'Tasks',
            'icon': 'fa-tasks',
            'description': 'Task boards and project management',
            'actions': ['view', 'create', 'edit', 'delete']
        },
        'contacts': {
            'name': 'Contacts',
            'icon': 'fa-address-book',
            'description': 'Contact management',
            'actions': ['view', 'create', 'edit', 'delete']
        },
        'users': {
            'name': 'Users',
            'icon': 'fa-users',
            'description': 'User management',
            'actions': ['view', 'create', 'edit', 'delete']
        },
        'settings': {
            'name': 'Settings',
            'icon': 'fa-cog',
            'description': 'Application settings',
            'actions': ['view', 'edit']
        }
    }
    
    # Action descriptions
    ACTION_LABELS = {
        'view': 'View',
        'create': 'Create',
        'edit': 'Edit',
        'delete': 'Delete',
        'publish': 'Publish/Unpublish',
        'bulk_upload': 'Bulk Upload',
        'assign': 'Assign to Others'
    }
    
    # Legacy role definitions (for backward compatibility)
    ROLES = {
        'admin': {
            'name': 'Administrator',
            'permissions': []  # Admin has all permissions by default
        },
        'user': {
            'name': 'User',
            'permissions': []  # Custom permissions only
        }
    }
    
    # Legacy permission list (for backward compatibility)
    ALL_PERMISSIONS = [
        'view', 'create', 'edit', 'delete', 'publish', 
        'bulk_upload', 'manage_leads', 'manage_users', 'settings', 'manage_loops'
    ]
    
    def set_password(self, password):
        """Hash and set the user's password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify the user's password"""
        return check_password_hash(self.password_hash, password)
    
    def get_section_permissions(self):
        """Get section permissions as a dictionary"""
        import json
        if self.section_permissions:
            try:
                return json.loads(self.section_permissions)
            except:
                return {}
        return {}
    
    def set_section_permissions(self, permissions_dict):
        """Set section permissions from a dictionary"""
        import json
        if permissions_dict:
            self.section_permissions = json.dumps(permissions_dict)
        else:
            self.section_permissions = '{}'
    
    def has_section_access(self, section):
        """Check if user has any access to a section"""
        if self.role == 'admin':
            return True
        perms = self.get_section_permissions()
        section_perms = perms.get(section, {})
        return any(section_perms.values())
    
    def has_section_permission(self, section, action):
        """Check if user has a specific permission in a section"""
        if self.role == 'admin':
            return True
        perms = self.get_section_permissions()
        section_perms = perms.get(section, {})
        return section_perms.get(action, False)
    
    def get_accessible_sections(self):
        """Get list of sections the user can access"""
        if self.role == 'admin':
            return list(self.SECTIONS.keys())
        accessible = []
        perms = self.get_section_permissions()
        for section in self.SECTIONS.keys():
            section_perms = perms.get(section, {})
            if any(section_perms.values()):
                accessible.append(section)
        return accessible
    
    # Legacy methods for backward compatibility
    def set_custom_permissions(self, permissions_list):
        """Set custom permissions from a list (legacy)"""
        import json
        if permissions_list:
            # Convert old format to new section-based format
            perms = {}
            for perm in permissions_list:
                if perm == 'view':
                    perms.setdefault('listings', {})['view'] = True
                    perms.setdefault('dashboard', {})['view'] = True
                elif perm == 'create':
                    perms.setdefault('listings', {})['create'] = True
                elif perm == 'edit':
                    perms.setdefault('listings', {})['edit'] = True
                elif perm == 'delete':
                    perms.setdefault('listings', {})['delete'] = True
                elif perm == 'publish':
                    perms.setdefault('listings', {})['publish'] = True
                elif perm == 'bulk_upload':
                    perms.setdefault('listings', {})['bulk_upload'] = True
                elif perm == 'manage_leads':
                    perms.setdefault('leads', {})['view'] = True
                    perms.setdefault('leads', {})['create'] = True
                    perms.setdefault('leads', {})['edit'] = True
                elif perm == 'manage_users':
                    perms.setdefault('users', {})['view'] = True
                    perms.setdefault('users', {})['create'] = True
                    perms.setdefault('users', {})['edit'] = True
                    perms.setdefault('users', {})['delete'] = True
                elif perm == 'settings':
                    perms.setdefault('settings', {})['view'] = True
                    perms.setdefault('settings', {})['edit'] = True
                elif perm == 'manage_loops':
                    perms.setdefault('listings', {})['view'] = True
            self.set_section_permissions(perms)
        else:
            self.section_permissions = '{}'
    
    def get_custom_permissions(self):
        """Get custom permissions as a list (legacy)"""
        return None  # Deprecated
    
    def has_permission(self, permission):
        """Check if user has a specific permission (legacy compatibility)"""
        if self.role == 'admin':
            return True
        # Map old permissions to new section-based
        mapping = {
            'view': ('listings', 'view'),
            'create': ('listings', 'create'),
            'edit': ('listings', 'edit'),
            'delete': ('listings', 'delete'),
            'publish': ('listings', 'publish'),
            'bulk_upload': ('listings', 'bulk_upload'),
            'manage_leads': ('leads', 'view'),
            'manage_users': ('users', 'view'),
            'settings': ('settings', 'view'),
            'manage_loops': ('listings', 'view'),
        }
        if permission in mapping:
            section, action = mapping[permission]
            return self.has_section_permission(section, action)
        return False
    
    def get_permissions(self):
        """Get all permissions for the user (legacy format)"""
        if self.role == 'admin':
            return self.ALL_PERMISSIONS
        perms = []
        section_perms = self.get_section_permissions()
        if section_perms.get('listings', {}).get('view'):
            perms.append('view')
        if section_perms.get('listings', {}).get('create'):
            perms.append('create')
        if section_perms.get('listings', {}).get('edit'):
            perms.append('edit')
        if section_perms.get('listings', {}).get('delete'):
            perms.append('delete')
        if section_perms.get('listings', {}).get('publish'):
            perms.append('publish')
        if section_perms.get('listings', {}).get('bulk_upload'):
            perms.append('bulk_upload')
        if section_perms.get('leads', {}).get('view'):
            perms.append('manage_leads')
        if section_perms.get('users', {}).get('view'):
            perms.append('manage_users')
        if section_perms.get('settings', {}).get('view'):
            perms.append('settings')
        if section_perms.get('listings', {}).get('view'):
            perms.append('manage_loops')
        return perms
    
    def to_dict(self):
        """Convert to dictionary (without password)"""
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'role': self.role,
            'role_name': self.ROLES.get(self.role, {}).get('name', 'User'),
            'preferred_language': self.preferred_language or 'en',
            'permissions': self.get_permissions(),
            'section_permissions': self.get_section_permissions(),
            'accessible_sections': self.get_accessible_sections(),
            'has_custom_permissions': self.section_permissions not in [None, '{}', ''],
            'pf_agent_id': self.pf_agent_id,
            'pf_agent_name': self.pf_agent_name,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None
        }


# ==================== WORKSPACES ====================

class Workspace(db.Model):
    """Workspace - isolated environment with its own API connections and data"""
    __tablename__ = 'workspaces'
    __table_args__ = (
        db.Index('idx_workspaces_slug', 'slug'),
        db.Index('idx_workspaces_is_active', 'is_active'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    logo_url = db.Column(db.String(500), nullable=True)
    color = db.Column(db.String(20), default='indigo')  # Theme color
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Relationships
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    members = db.relationship('WorkspaceMember', back_populates='workspace', cascade='all, delete-orphan')
    connections = db.relationship('WorkspaceConnection', back_populates='workspace', cascade='all, delete-orphan')
    api_credentials = db.relationship('WorkspaceApiCredential', back_populates='workspace', cascade='all, delete-orphan')
    
    # Available colors
    COLORS = ['indigo', 'blue', 'green', 'yellow', 'red', 'purple', 'pink', 'gray', 'orange', 'teal', 'cyan', 'emerald']
    
    def get_connection(self, provider):
        """Get connection for a specific provider"""
        for conn in self.connections:
            if conn.provider == provider and conn.is_active:
                return conn
        return None
    
    def get_member(self, user_id):
        """Get member by user ID"""
        for member in self.members:
            if member.user_id == user_id:
                return member
        return None
    
    def is_owner(self, user_id):
        """Check if user is owner"""
        member = self.get_member(user_id)
        return member and member.role == 'owner'
    
    def is_admin(self, user_id):
        """Check if user is admin or owner"""
        member = self.get_member(user_id)
        return member and member.role in ('owner', 'admin')
    
    def to_dict(self, include_members=False, include_connections=False):
        """Convert to dictionary"""
        data = {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'logo_url': self.logo_url,
            'color': self.color,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'member_count': len(self.members),
            'connection_count': len([c for c in self.connections if c.is_active])
        }
        if include_members:
            data['members'] = [m.to_dict() for m in self.members]
        if include_connections:
            data['connections'] = [c.to_dict(include_secrets=False) for c in self.connections]
        return data
    
    @staticmethod
    def generate_slug(name):
        """Generate a URL-safe slug from name"""
        import re
        slug = name.lower().strip()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[-\s]+', '-', slug)
        return slug


class WorkspaceMember(db.Model):
    """Workspace membership - links users to workspaces with roles"""
    __tablename__ = 'workspace_members'
    __table_args__ = (
        db.UniqueConstraint('workspace_id', 'user_id', name='uq_workspace_user'),
        db.Index('idx_workspace_members_user', 'user_id'),
        db.Index('idx_workspace_members_workspace', 'workspace_id'),
        db.Index('idx_workspace_members_team_leader', 'team_leader_user_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(20), default='member')  # owner, admin, team_leader, member, viewer
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    invited_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    team_leader_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Relationships
    workspace = db.relationship('Workspace', back_populates='members')
    user = db.relationship('User', foreign_keys=[user_id])
    invited_by = db.relationship('User', foreign_keys=[invited_by_id])
    team_leader = db.relationship('User', foreign_keys=[team_leader_user_id])
    
    # Role definitions
    ROLES = {
        'owner': {'name': 'Owner', 'description': 'Full control, can delete workspace'},
        'admin': {'name': 'Admin', 'description': 'Manage members and connections'},
        'team_leader': {'name': 'Team Leader', 'description': 'Can view team records and manage own records'},
        'member': {'name': 'Member', 'description': 'Full access to workspace data'},
        'viewer': {'name': 'Viewer', 'description': 'Read-only access'}
    }
    
    def can_manage_members(self):
        return self.role in ('owner', 'admin')
    
    def can_manage_connections(self):
        return self.role in ('owner', 'admin')
    
    def can_edit_data(self):
        return self.role in ('owner', 'admin', 'team_leader', 'member')
    
    def can_view_data(self):
        return True
    
    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'user_id': self.user_id,
            'user_name': self.user.name if self.user else None,
            'user_email': self.user.email if self.user else None,
            'role': self.role,
            'role_name': self.ROLES.get(self.role, {}).get('name', 'Member'),
            'team_leader_user_id': self.team_leader_user_id,
            'team_leader_name': self.team_leader.name if self.team_leader else None,
            'joined_at': self.joined_at.isoformat() if self.joined_at else None,
            'can_manage_members': self.can_manage_members(),
            'can_manage_connections': self.can_manage_connections(),
            'can_edit_data': self.can_edit_data()
        }


class WorkspaceConnection(db.Model):
    """API connection credentials for a workspace"""
    __tablename__ = 'workspace_connections'
    __table_args__ = (
        db.UniqueConstraint('workspace_id', 'provider', name='uq_workspace_provider'),
        db.Index('idx_workspace_connections_workspace', 'workspace_id'),
        db.Index('idx_workspace_connections_provider', 'provider'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False)
    provider = db.Column(db.String(50), nullable=False)  # propertyfinder, bayut, dubizzle, etc.
    name = db.Column(db.String(100), nullable=True)  # Display name
    is_active = db.Column(db.Boolean, default=True)
    
    # Encrypted credentials (stored as JSON)
    credentials = db.Column(db.Text, nullable=True)  # JSON: {api_key, api_secret, ...}
    
    # Connection status
    last_connected_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    connection_status = db.Column(db.String(20), default='pending')  # pending, connected, error
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Relationships
    workspace = db.relationship('Workspace', back_populates='connections')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    
    # Provider definitions
    PROVIDERS = {
        'propertyfinder': {
            'name': 'PropertyFinder',
            'icon': 'fa-building',
            'color': 'blue',
            'fields': [
                {'key': 'api_key', 'label': 'API Key', 'type': 'text', 'required': True},
                {'key': 'api_secret', 'label': 'API Secret', 'type': 'password', 'required': True},
            ],
            'base_url': 'https://atlas.propertyfinder.com/v1'
        },
        'bayut': {
            'name': 'Bayut',
            'icon': 'fa-home',
            'color': 'red',
            'fields': [
                {'key': 'api_key', 'label': 'API Key', 'type': 'text', 'required': True},
                {'key': 'api_secret', 'label': 'API Secret', 'type': 'password', 'required': True},
            ],
            'base_url': 'https://api.bayut.com/v1'
        },
        'dubizzle': {
            'name': 'Dubizzle',
            'icon': 'fa-map-marker-alt',
            'color': 'orange',
            'fields': [
                {'key': 'api_key', 'label': 'API Key', 'type': 'text', 'required': True},
                {'key': 'api_secret', 'label': 'API Secret', 'type': 'password', 'required': True},
            ],
            'base_url': 'https://api.dubizzle.com/v1'
        },
        'google_drive': {
            'name': 'Google Drive',
            'icon': 'fab fa-google-drive',
            'color': 'green',
            'fields': [
                {'key': 'folder_id', 'label': 'Folder ID', 'type': 'text', 'required': True},
                {'key': 'service_account_json', 'label': 'Service Account JSON', 'type': 'textarea', 'required': False},
            ],
            'base_url': None
        }
    }
    
    def get_credentials(self):
        """Get credentials as dictionary"""
        import json
        if self.credentials:
            try:
                return json.loads(self.credentials)
            except:
                return {}
        return {}
    
    def set_credentials(self, creds_dict):
        """Set credentials from dictionary"""
        import json
        if creds_dict:
            self.credentials = json.dumps(creds_dict)
        else:
            self.credentials = '{}'
    
    def get_credential(self, key):
        """Get a specific credential value"""
        return self.get_credentials().get(key)
    
    def to_dict(self, include_secrets=False):
        """Convert to dictionary"""
        provider_info = self.PROVIDERS.get(self.provider, {})
        data = {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'provider': self.provider,
            'provider_name': provider_info.get('name', self.provider),
            'provider_icon': provider_info.get('icon', 'fa-plug'),
            'provider_color': provider_info.get('color', 'gray'),
            'name': self.name or provider_info.get('name', self.provider),
            'is_active': self.is_active,
            'connection_status': self.connection_status,
            'last_connected_at': self.last_connected_at.isoformat() if self.last_connected_at else None,
            'last_error': self.last_error,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
        if include_secrets:
            data['credentials'] = self.get_credentials()
        else:
            # Show masked credentials
            creds = self.get_credentials()
            masked = {}
            for key, value in creds.items():
                if value and len(str(value)) > 4:
                    masked[key] = str(value)[:4] + '****'
                else:
                    masked[key] = '****' if value else ''
            data['credentials_masked'] = masked
        return data


class WorkspaceApiCredential(db.Model):
    """Credential pair used by workspace-scoped external Open API clients."""
    __tablename__ = 'workspace_api_credentials'
    __table_args__ = (
        db.UniqueConstraint('key_id', name='uq_workspace_api_credentials_key_id'),
        db.Index('idx_workspace_api_credentials_workspace', 'workspace_id'),
        db.Index('idx_workspace_api_credentials_active', 'is_active'),
        db.Index('idx_workspace_api_credentials_revoked_at', 'revoked_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    key_id = db.Column(db.String(64), nullable=False, unique=True)
    secret_hash = db.Column(db.String(255), nullable=False)
    scopes_json = db.Column(db.Text, nullable=True, default='["listings:create"]')
    is_active = db.Column(db.Boolean, default=True)
    rate_limit_per_min = db.Column(db.Integer, default=60)

    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    revoked_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    last_used_at = db.Column(db.DateTime, nullable=True)
    last_used_ip = db.Column(db.String(50), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    workspace = db.relationship('Workspace', back_populates='api_credentials')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    revoked_by = db.relationship('User', foreign_keys=[revoked_by_id])

    def set_secret(self, raw_secret):
        self.secret_hash = generate_password_hash(raw_secret)

    def verify_secret(self, raw_secret):
        if not raw_secret or not self.secret_hash:
            return False
        return check_password_hash(self.secret_hash, raw_secret)

    def get_scopes(self):
        import json
        if not self.scopes_json:
            return ['listings:create']
        try:
            parsed = json.loads(self.scopes_json)
            if isinstance(parsed, list) and parsed:
                return [str(s).strip() for s in parsed if str(s).strip()]
        except Exception:
            pass
        return ['listings:create']

    def set_scopes(self, scopes):
        import json
        normalized = []
        for scope in scopes or []:
            text = str(scope or '').strip()
            if text and text not in normalized:
                normalized.append(text)
        if not normalized:
            normalized = ['listings:create']
        self.scopes_json = json.dumps(normalized)

    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= datetime.utcnow())

    def is_revoked(self):
        return bool(self.revoked_at)

    def is_usable(self):
        return self.is_active and not self.is_revoked() and not self.is_expired()

    def to_dict(self, include_secret_hash=False):
        data = {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'name': self.name,
            'key_id': self.key_id,
            'is_active': self.is_active,
            'scopes': self.get_scopes(),
            'rate_limit_per_min': self.rate_limit_per_min or 60,
            'created_by_id': self.created_by_id,
            'created_by_name': self.created_by.name if self.created_by else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'last_used_ip': self.last_used_ip,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'revoked_by_id': self.revoked_by_id,
            'status': 'active' if self.is_usable() else ('revoked' if self.is_revoked() else ('expired' if self.is_expired() else 'inactive'))
        }
        if include_secret_hash:
            data['secret_hash'] = self.secret_hash
        return data


class WorkspaceInvite(db.Model):
    """Workspace invite tokens for onboarding users."""
    __tablename__ = 'workspace_invites'
    __table_args__ = (
        db.Index('idx_workspace_invites_workspace', 'workspace_id'),
        db.Index('idx_workspace_invites_email', 'email'),
        db.Index('idx_workspace_invites_token', 'token_hash'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='member')
    token_hash = db.Column(db.String(128), nullable=False)
    invited_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    accepted_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    workspace = db.relationship('Workspace', foreign_keys=[workspace_id])
    invited_by = db.relationship('User', foreign_keys=[invited_by_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'email': self.email,
            'role': self.role,
            'invited_by_id': self.invited_by_id,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'accepted_at': self.accepted_at.isoformat() if self.accepted_at else None,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class PasswordResetToken(db.Model):
    """Password reset tokens (link-only)."""
    __tablename__ = 'password_reset_tokens'
    __table_args__ = (
        db.Index('idx_password_reset_user', 'user_id'),
        db.Index('idx_password_reset_token', 'token_hash'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token_hash = db.Column(db.String(128), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', foreign_keys=[user_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'created_by_id': self.created_by_id,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'used_at': self.used_at.isoformat() if self.used_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ==================== BITRIX24-STYLE PERMISSION SYSTEM ====================

class SystemRole(db.Model):
    """System-level roles (Portal/Tenant level) - Bitrix24 style
    These roles apply across all workspaces and define global capabilities.
    """
    __tablename__ = 'system_roles'
    __table_args__ = (
        db.Index('idx_system_roles_code', 'code'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)  # SYSTEM_ADMIN, GLOBAL_WORKSPACE_MANAGER, USER
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_system = db.Column(db.Boolean, default=False)  # Built-in role, cannot be deleted
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Capabilities stored as JSON: {"manage_workspaces": true, "view_all_workspaces": true, ...}
    capabilities = db.Column(db.Text, default='{}')
    
    # System role codes
    SYSTEM_ADMIN = 'SYSTEM_ADMIN'
    GLOBAL_WORKSPACE_MANAGER = 'GLOBAL_WORKSPACE_MANAGER'
    USER = 'USER'
    
    # Default system roles with capabilities
    DEFAULT_ROLES = {
        'SYSTEM_ADMIN': {
            'name': 'System Administrator',
            'description': 'Full system access including all configurations',
            'capabilities': {
                'manage_system': True,
                'manage_workspaces': True,
                'view_all_workspaces': True,
                'manage_users': True,
                'manage_system_roles': True,
                'manage_feature_flags': True,
                'view_audit_logs': True,
                'manage_global_settings': True,
            }
        },
        'GLOBAL_WORKSPACE_MANAGER': {
            'name': 'Global Workspace Manager',
            'description': 'Can manage all workspaces without accessing private content',
            'capabilities': {
                'manage_workspaces': True,
                'view_all_workspaces': True,
                'assign_workspace_admins': True,
                'configure_workspace_features': True,
                'view_workspace_stats': True,
            }
        },
        'USER': {
            'name': 'Regular User',
            'description': 'Standard user with workspace-based permissions only',
            'capabilities': {}
        }
    }
    
    def get_capabilities(self):
        """Get capabilities as dictionary"""
        import json
        if self.capabilities:
            try:
                return json.loads(self.capabilities)
            except:
                return {}
        return {}
    
    def set_capabilities(self, caps_dict):
        """Set capabilities from dictionary"""
        import json
        self.capabilities = json.dumps(caps_dict) if caps_dict else '{}'
    
    def has_capability(self, capability):
        """Check if role has a specific capability"""
        return self.get_capabilities().get(capability, False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'description': self.description,
            'is_system': self.is_system,
            'capabilities': self.get_capabilities(),
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class UserSystemRole(db.Model):
    """Links users to system-level roles"""
    __tablename__ = 'user_system_roles'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'system_role_id', name='uq_user_system_role'),
        db.Index('idx_user_system_roles_user', 'user_id'),
        db.Index('idx_user_system_roles_role', 'system_role_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    system_role_id = db.Column(db.Integer, db.ForeignKey('system_roles.id', ondelete='CASCADE'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('system_roles_assoc', lazy='dynamic'))
    system_role = db.relationship('SystemRole')
    assigned_by = db.relationship('User', foreign_keys=[assigned_by_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'system_role_id': self.system_role_id,
            'role_code': self.system_role.code if self.system_role else None,
            'role_name': self.system_role.name if self.system_role else None,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None
        }


class WorkspaceRole(db.Model):
    """Workspace-level roles - can be default or custom per workspace
    Bitrix24-style: WORKSPACE_ADMIN, MODERATOR, MEMBER, EXTERNAL
    """
    __tablename__ = 'workspace_roles'
    __table_args__ = (
        db.Index('idx_workspace_roles_workspace', 'workspace_id'),
        db.Index('idx_workspace_roles_code', 'code'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=True)  # NULL = global template
    code = db.Column(db.String(50), nullable=False)  # WORKSPACE_ADMIN, MODERATOR, MEMBER, EXTERNAL
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_default = db.Column(db.Boolean, default=False)  # Is this the default role for new members?
    is_system = db.Column(db.Boolean, default=False)  # Built-in role template
    priority = db.Column(db.Integer, default=0)  # Higher = more privileged
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Permission buckets - JSON: {"manage_members": "admin_only", "edit_data": "all_members", ...}
    # Values: "admin_only", "admin_moderator", "all_members", "authorized", "external", "deny"
    permission_buckets = db.Column(db.Text, default='{}')
    
    # Role codes
    WORKSPACE_ADMIN = 'WORKSPACE_ADMIN'
    MODERATOR = 'MODERATOR'
    MEMBER = 'MEMBER'
    EXTERNAL = 'EXTERNAL'
    
    # Permission bucket values
    BUCKET_ADMIN_ONLY = 'admin_only'
    BUCKET_ADMIN_MODERATOR = 'admin_moderator'
    BUCKET_ALL_MEMBERS = 'all_members'
    BUCKET_AUTHORIZED = 'authorized'
    BUCKET_EXTERNAL = 'external'
    BUCKET_DENY = 'deny'
    
    # Default workspace roles
    DEFAULT_ROLES = {
        'WORKSPACE_ADMIN': {
            'name': 'Workspace Admin',
            'description': 'Full control of the workspace',
            'priority': 100,
            'buckets': {
                'manage_members': 'admin_only',
                'manage_roles': 'admin_only',
                'manage_connections': 'admin_only',
                'manage_settings': 'admin_only',
                'delete_workspace': 'admin_only',
                'view_data': 'all_members',
                'create_data': 'all_members',
                'edit_data': 'all_members',
                'delete_data': 'admin_moderator',
            }
        },
        'MODERATOR': {
            'name': 'Moderator',
            'description': 'Can moderate content and assist with management',
            'priority': 50,
            'buckets': {
                'manage_members': 'admin_moderator',
                'manage_roles': 'deny',
                'manage_connections': 'deny',
                'manage_settings': 'deny',
                'delete_workspace': 'deny',
                'view_data': 'all_members',
                'create_data': 'all_members',
                'edit_data': 'all_members',
                'delete_data': 'admin_moderator',
            }
        },
        'MEMBER': {
            'name': 'Member',
            'description': 'Standard workspace member with full data access',
            'priority': 10,
            'buckets': {
                'manage_members': 'deny',
                'manage_roles': 'deny',
                'manage_connections': 'deny',
                'manage_settings': 'deny',
                'delete_workspace': 'deny',
                'view_data': 'all_members',
                'create_data': 'all_members',
                'edit_data': 'all_members',
                'delete_data': 'deny',
            }
        },
        'EXTERNAL': {
            'name': 'External/Guest',
            'description': 'Limited access for external collaborators',
            'priority': 1,
            'buckets': {
                'manage_members': 'deny',
                'manage_roles': 'deny',
                'manage_connections': 'deny',
                'manage_settings': 'deny',
                'delete_workspace': 'deny',
                'view_data': 'external',
                'create_data': 'deny',
                'edit_data': 'deny',
                'delete_data': 'deny',
            }
        }
    }
    
    def get_permission_buckets(self):
        """Get permission buckets as dictionary"""
        import json
        if self.permission_buckets:
            try:
                return json.loads(self.permission_buckets)
            except:
                return {}
        return {}
    
    def set_permission_buckets(self, buckets_dict):
        """Set permission buckets from dictionary"""
        import json
        self.permission_buckets = json.dumps(buckets_dict) if buckets_dict else '{}'
    
    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'code': self.code,
            'name': self.name,
            'description': self.description,
            'is_default': self.is_default,
            'is_system': self.is_system,
            'priority': self.priority,
            'permission_buckets': self.get_permission_buckets(),
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class ModulePermission(db.Model):
    """Module-level RBAC - defines what each workspace role can do in each module
    Modules: listings, leads, tasks, contacts, insights, etc.
    """
    __tablename__ = 'module_permissions'
    __table_args__ = (
        db.UniqueConstraint('workspace_role_id', 'module', name='uq_role_module'),
        db.Index('idx_module_permissions_role', 'workspace_role_id'),
        db.Index('idx_module_permissions_module', 'module'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_role_id = db.Column(db.Integer, db.ForeignKey('workspace_roles.id', ondelete='CASCADE'), nullable=False)
    module = db.Column(db.String(50), nullable=False)  # listings, leads, tasks, contacts, insights
    
    # Capabilities for this module - JSON: {"read": true, "create": true, "edit": "own", ...}
    # Values: true, false, "own", "team", "subteam", "workspace"
    capabilities = db.Column(db.Text, default='{}')
    
    # Merge strategy for multiple roles
    merge_strategy = db.Column(db.String(20), default='union')  # union, most_permissive, least_permissive
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Scope levels
    SCOPE_NONE = False
    SCOPE_ALL = True
    SCOPE_OWN = 'own'
    SCOPE_TEAM = 'team'
    SCOPE_SUBTEAM = 'subteam'
    SCOPE_WORKSPACE = 'workspace'
    
    # Available modules (matches User.SECTIONS)
    MODULES = ['dashboard', 'listings', 'leads', 'insights', 'tasks', 'contacts', 'users', 'settings', 'loops']
    
    # Relationship
    workspace_role = db.relationship('WorkspaceRole', backref=db.backref('module_permissions', lazy='dynamic'))
    
    def get_capabilities(self):
        """Get capabilities as dictionary"""
        import json
        if self.capabilities:
            try:
                return json.loads(self.capabilities)
            except:
                return {}
        return {}
    
    def set_capabilities(self, caps_dict):
        """Set capabilities from dictionary"""
        import json
        self.capabilities = json.dumps(caps_dict) if caps_dict else '{}'
    
    def to_dict(self):
        return {
            'id': self.id,
            'workspace_role_id': self.workspace_role_id,
            'module': self.module,
            'capabilities': self.get_capabilities(),
            'merge_strategy': self.merge_strategy,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class WorkspaceUserPermissionOverride(db.Model):
    """Per-user workspace permission overrides with allow/deny semantics."""
    __tablename__ = 'workspace_user_permission_overrides'
    __table_args__ = (
        db.UniqueConstraint('workspace_id', 'user_id', 'module', 'action', name='uq_ws_user_module_action_override'),
        db.Index('idx_ws_user_perm_override_workspace', 'workspace_id'),
        db.Index('idx_ws_user_perm_override_user', 'user_id'),
        db.Index('idx_ws_user_perm_override_module', 'module'),
        db.Index('idx_ws_user_perm_override_action', 'action'),
        db.Index('idx_ws_user_perm_override_effect', 'effect'),
    )

    EFFECT_ALLOW = 'allow'
    EFFECT_DENY = 'deny'
    ALLOWED_EFFECTS = [EFFECT_ALLOW, EFFECT_DENY]

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    module = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    effect = db.Column(db.String(10), nullable=False, default=EFFECT_ALLOW)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    workspace = db.relationship('Workspace', foreign_keys=[workspace_id])
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('workspace_permission_overrides', lazy='dynamic'))
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    updated_by = db.relationship('User', foreign_keys=[updated_by_id])

    def to_dict(self):
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'user_id': self.user_id,
            'module': self.module,
            'action': self.action,
            'effect': self.effect,
            'created_by_id': self.created_by_id,
            'updated_by_id': self.updated_by_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def get_user_overrides(cls, workspace_id, user_id, module=None):
        query = cls.query.filter_by(workspace_id=workspace_id, user_id=user_id)
        if module:
            query = query.filter_by(module=module)
        return query.all()

    @classmethod
    def replace_user_overrides(cls, workspace_id, user_id, override_rows, actor_user_id=None):
        """Replace all overrides for a user in a workspace atomically.

        override_rows format:
        [
            {'module': 'listings', 'action': 'edit', 'effect': 'deny'},
            ...
        ]
        """
        from sqlalchemy import and_

        cls.query.filter(and_(cls.workspace_id == workspace_id, cls.user_id == user_id)).delete()

        normalized_rows = []
        for row in override_rows or []:
            if not isinstance(row, dict):
                continue
            module = str(row.get('module') or '').strip().lower()
            action = str(row.get('action') or '').strip().lower()
            effect = str(row.get('effect') or '').strip().lower()
            if not module or not action or effect not in cls.ALLOWED_EFFECTS:
                continue
            normalized_rows.append({
                'module': module,
                'action': action,
                'effect': effect
            })

        for row in normalized_rows:
            db.session.add(cls(
                workspace_id=workspace_id,
                user_id=user_id,
                module=row['module'],
                action=row['action'],
                effect=row['effect'],
                created_by_id=actor_user_id,
                updated_by_id=actor_user_id
            ))

        return normalized_rows


class ObjectACL(db.Model):
    """Object-level ACL - per-object permission overrides with inheritance
    Can be applied to listings, folders, leads, tasks, etc.
    """
    __tablename__ = 'object_acls'
    __table_args__ = (
        db.Index('idx_object_acl_object', 'object_type', 'object_id'),
        db.Index('idx_object_acl_principal', 'principal_type', 'principal_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Object being protected
    object_type = db.Column(db.String(50), nullable=False)  # listing, folder, lead, task, contact
    object_id = db.Column(db.Integer, nullable=False)
    
    # Principal (who has access)
    principal_type = db.Column(db.String(20), nullable=False)  # user, workspace_role, team
    principal_id = db.Column(db.Integer, nullable=False)
    
    # Permissions - JSON: {"read": true, "edit": true, "delete": false, "share": true, "admin": false}
    permissions = db.Column(db.Text, default='{}')
    
    # Inheritance
    inherit_from_parent = db.Column(db.Boolean, default=True)
    propagate_to_children = db.Column(db.Boolean, default=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Permission levels
    PERM_READ = 'read'
    PERM_CREATE = 'create'
    PERM_EDIT = 'edit'
    PERM_DELETE = 'delete'
    PERM_SHARE = 'share'
    PERM_ADMIN = 'admin'
    
    ALL_PERMISSIONS = [PERM_READ, PERM_CREATE, PERM_EDIT, PERM_DELETE, PERM_SHARE, PERM_ADMIN]
    
    def get_permissions(self):
        """Get permissions as dictionary"""
        import json
        if self.permissions:
            try:
                return json.loads(self.permissions)
            except:
                return {}
        return {}
    
    def set_permissions(self, perms_dict):
        """Set permissions from dictionary"""
        import json
        self.permissions = json.dumps(perms_dict) if perms_dict else '{}'
    
    def has_permission(self, perm):
        """Check if this ACL grants a specific permission"""
        return self.get_permissions().get(perm, False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'object_type': self.object_type,
            'object_id': self.object_id,
            'principal_type': self.principal_type,
            'principal_id': self.principal_id,
            'permissions': self.get_permissions(),
            'inherit_from_parent': self.inherit_from_parent,
            'propagate_to_children': self.propagate_to_children,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class FeatureFlag(db.Model):
    """Feature flags for controlling permission enforcement and features
    Supports global, workspace, and module scopes
    """
    __tablename__ = 'feature_flags'
    __table_args__ = (
        db.UniqueConstraint('code', 'scope', 'scope_id', name='uq_feature_flag'),
        db.Index('idx_feature_flags_code', 'code'),
        db.Index('idx_feature_flags_scope', 'scope', 'scope_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), nullable=False)  # permission_enforcement, audit_mode, new_feature_x
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    
    # Scope: global, workspace, module
    scope = db.Column(db.String(20), default='global')
    scope_id = db.Column(db.Integer, nullable=True)  # workspace_id or null for global
    
    # Value
    is_enabled = db.Column(db.Boolean, default=False)
    value = db.Column(db.Text, nullable=True)  # Optional JSON value for complex flags
    
    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Common feature flag codes
    PERMISSION_ENFORCEMENT = 'permission_enforcement'
    AUDIT_MODE = 'audit_mode'
    WORKSPACE_ISOLATION = 'workspace_isolation'
    OBJECT_ACL = 'object_acl'
    
    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'description': self.description,
            'scope': self.scope,
            'scope_id': self.scope_id,
            'is_enabled': self.is_enabled,
            'value': self.value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AuditLog(db.Model):
    """Audit log for tracking permission checks and changes
    Used for security monitoring and compliance
    """
    __tablename__ = 'audit_logs'
    __table_args__ = (
        db.Index('idx_audit_logs_user', 'user_id'),
        db.Index('idx_audit_logs_action', 'action'),
        db.Index('idx_audit_logs_resource', 'resource_type', 'resource_id'),
        db.Index('idx_audit_logs_created_at', 'created_at'),
        db.Index('idx_audit_logs_workspace', 'workspace_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Actor
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    user_email = db.Column(db.String(120), nullable=True)  # Denormalized for history
    
    # Action
    action = db.Column(db.String(100), nullable=False)  # permission_check, role_change, acl_update, etc.
    action_result = db.Column(db.String(20), nullable=True)  # allowed, denied, error
    
    # Resource
    resource_type = db.Column(db.String(50), nullable=True)  # workspace, listing, lead, etc.
    resource_id = db.Column(db.Integer, nullable=True)
    workspace_id = db.Column(db.Integer, nullable=True)
    
    # Details - JSON
    details = db.Column(db.Text, nullable=True)
    
    # Request context
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    
    # Timestamp
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Action types
    ACTION_PERMISSION_CHECK = 'permission_check'
    ACTION_PERMISSION_DENIED = 'permission_denied'
    ACTION_ROLE_ASSIGNED = 'role_assigned'
    ACTION_ROLE_REMOVED = 'role_removed'
    ACTION_ACL_CREATED = 'acl_created'
    ACTION_ACL_UPDATED = 'acl_updated'
    ACTION_ACL_DELETED = 'acl_deleted'
    ACTION_WORKSPACE_CREATED = 'workspace_created'
    ACTION_WORKSPACE_DELETED = 'workspace_deleted'
    ACTION_MEMBER_ADDED = 'member_added'
    ACTION_MEMBER_REMOVED = 'member_removed'
    
    def get_details(self):
        """Get details as dictionary"""
        import json
        if self.details:
            try:
                return json.loads(self.details)
            except:
                return {}
        return {}
    
    def set_details(self, details_dict):
        """Set details from dictionary"""
        import json
        self.details = json.dumps(details_dict) if details_dict else None
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_email': self.user_email,
            'action': self.action,
            'action_result': self.action_result,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'workspace_id': self.workspace_id,
            'details': self.get_details(),
            'ip_address': self.ip_address,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ==================== LISTING FOLDERS ====================

class ListingFolder(db.Model):
    """Folders/Groups for organizing listings"""
    __tablename__ = 'listing_folders'
    __table_args__ = (
        db.Index('idx_folders_parent_id', 'parent_id'),
        db.Index('idx_folders_name', 'name'),
        db.Index('idx_folders_workspace_id', 'workspace_id'),
        db.Index('idx_folders_owner_user_id', 'owner_user_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=True, index=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='indigo')  # CSS color class
    icon = db.Column(db.String(50), default='fa-folder')  # FontAwesome icon
    description = db.Column(db.Text, nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('listing_folders.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    workspace = db.relationship('Workspace', foreign_keys=[workspace_id])
    owner = db.relationship('User', foreign_keys=[owner_user_id])
    parent = db.relationship('ListingFolder', remote_side=[id], backref='subfolders')
    listings = db.relationship('LocalListing', backref='folder', lazy='dynamic')
    
    # Available colors for folder styling
    COLORS = ['indigo', 'blue', 'green', 'yellow', 'red', 'purple', 'pink', 'gray', 'orange', 'teal']
    
    # Available icons
    ICONS = [
        'fa-folder', 'fa-building', 'fa-home', 'fa-city', 'fa-star', 
        'fa-heart', 'fa-fire', 'fa-bolt', 'fa-gem', 'fa-crown',
        'fa-tag', 'fa-bookmark', 'fa-flag', 'fa-bell', 'fa-clock'
    ]
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'owner_user_id': self.owner_user_id,
            'name': self.name,
            'color': self.color,
            'icon': self.icon,
            'description': self.description,
            'parent_id': self.parent_id,
            'listing_count': self.listings.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def get_all_with_counts(cls, workspace_id=None, owner_user_id=None):
        """Get all folders with listing counts (workspace-aware)."""
        query = cls.query.order_by(cls.name)
        if workspace_id:
            query = query.filter_by(workspace_id=workspace_id)
        if owner_user_id is not None:
            query = query.filter_by(owner_user_id=owner_user_id)
        folders = query.all()
        results = []
        for folder in folders:
            data = folder.to_dict()
            listing_query = LocalListing.query.filter_by(folder_id=folder.id)
            if workspace_id:
                listing_query = listing_query.filter_by(workspace_id=workspace_id)
            data['listing_count'] = listing_query.count()
            results.append(data)
        return results


# ==================== LISTINGS ====================

class LocalListing(db.Model):
    """Local listing storage model"""
    __tablename__ = 'listings'
    __table_args__ = (
        db.Index('idx_listings_workspace_id', 'workspace_id'),
        db.Index('idx_listings_folder_id', 'folder_id'),
        db.Index('idx_listings_status', 'status'),
        db.Index('idx_listings_offering_type', 'offering_type'),
        db.Index('idx_listings_property_type', 'property_type'),
        db.Index('idx_listings_emirate', 'emirate'),
        db.Index('idx_listings_city', 'city'),
        db.Index('idx_listings_location_id', 'location_id'),
        db.Index('idx_listings_price', 'price'),
        db.Index('idx_listings_bedrooms', 'bedrooms'),
        db.Index('idx_listings_assigned_agent', 'assigned_agent'),
        db.Index('idx_listings_assigned_to_id', 'assigned_to_id'),
        db.Index('idx_listings_pf_listing_id', 'pf_listing_id'),
        db.Index('idx_listings_created_at', 'created_at'),
        db.Index('idx_listings_updated_at', 'updated_at'),
        # Composite indexes for common queries
        db.Index('idx_listings_status_offering', 'status', 'offering_type'),
        db.Index('idx_listings_emirate_city', 'emirate', 'city'),
        db.Index('idx_listings_type_beds_price', 'property_type', 'bedrooms', 'price'),
        db.Index('idx_listings_workspace_status', 'workspace_id', 'status'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=True, index=True)
    reference = db.Column(db.String(50), unique=True, nullable=False, index=True)
    
    # Folder/Group assignment
    folder_id = db.Column(db.Integer, db.ForeignKey('listing_folders.id'), nullable=True)
    
    # Core Details
    emirate = db.Column(db.String(50))
    city = db.Column(db.String(100))
    location = db.Column(db.String(200))  # Location text for display
    location_id = db.Column(db.Integer)   # PropertyFinder location ID
    category = db.Column(db.String(20))  # residential, commercial
    offering_type = db.Column(db.String(20))  # sale, rent
    property_type = db.Column(db.String(50))  # apartment, villa, townhouse, etc.
    
    # Specifications
    bedrooms = db.Column(db.String(10))
    bathrooms = db.Column(db.String(10))
    size = db.Column(db.Float)
    furnishing_type = db.Column(db.String(20))
    project_status = db.Column(db.String(20))
    parking_slots = db.Column(db.Integer)
    floor_number = db.Column(db.String(20))
    unit_number = db.Column(db.String(50))
    
    # Price
    price = db.Column(db.Float)
    downpayment = db.Column(db.Float)
    rent_frequency = db.Column(db.String(20))
    
    # Description
    title_en = db.Column(db.String(100))
    title_ar = db.Column(db.String(100))
    description_en = db.Column(db.Text)
    description_ar = db.Column(db.Text)
    
    # Media
    images = db.Column(db.Text)  # JSON array of URLs
    video_tour = db.Column(db.String(500))
    video_360 = db.Column(db.String(500))
    original_images = db.Column(db.Text)  # JSON array of original image paths
    
    # Amenities
    amenities = db.Column(db.Text)  # Comma-separated list
    
    # Assignment
    assigned_agent = db.Column(db.String(100))
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    owner_id = db.Column(db.String(100))
    owner_name = db.Column(db.String(100))

    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id])
    
    # Additional
    developer = db.Column(db.String(100))
    permit_number = db.Column(db.String(50))
    available_from = db.Column(db.String(50))
    
    # Analytics
    views = db.Column(db.Integer, default=0)
    leads = db.Column(db.Integer, default=0)
    
    # Status & Metadata
    status = db.Column(db.String(20), default='draft')  # draft, published, pending
    pf_listing_id = db.Column(db.String(50))  # PropertyFinder ID if synced
    synced_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def get_images(self):
        """Public method to get parsed images list"""
        return self._parse_images()
    
    def _parse_images(self):
        """Parse images from various storage formats and return URLs"""
        import json
        
        if not self.images:
            return []
        
        images = []
        
        # Try JSON format first (new format from image editor)
        try:
            parsed = json.loads(self.images)
            if isinstance(parsed, list):
                images = parsed
        except (json.JSONDecodeError, TypeError):
            # Fall back to pipe-separated format (legacy)
            images = self.images.split('|') if self.images else []
        
        # Convert relative paths to URLs and filter out invalid entries
        result = []
        for img in images:
            if not img:  # Skip None, empty strings, etc.
                continue
                
            url = None
            
            if isinstance(img, str):
                img = img.strip()
                if not img or img.lower() == 'none':  # Skip empty or "None" strings
                    continue
                    
                # If it's a relative path (e.g., "listings/123/img.jpg"), prefix with /uploads/
                if img.startswith('listings/') or img.startswith('uploads/'):
                    if not img.startswith('/'):
                        url = '/uploads/' + img.lstrip('uploads/')
                    else:
                        url = img
                elif img.startswith('http'):
                    # Already a full URL
                    url = img
                elif img.startswith('/'):
                    # Already an absolute path
                    url = img
                elif img.startswith('temp/'):
                    url = '/uploads/' + img
                else:
                    # Assume it's a relative path, prefix with /uploads/
                    url = '/uploads/' + img
                    
            elif isinstance(img, dict):
                # Handle PropertyFinder format: {original: {url: "..."}}
                url = img.get('url') or (img.get('original', {}).get('url') if img.get('original') else None)
            
            # Only add valid URLs
            if url and url.lower() != 'none' and len(url) > 1:
                result.append(url)
        
        return result

    def _parse_original_images(self):
        """Parse original_images list and return URLs"""
        import json
        
        if not self.original_images:
            return []
        
        images = []
        try:
            parsed = json.loads(self.original_images)
            if isinstance(parsed, list):
                images = parsed
        except (json.JSONDecodeError, TypeError):
            images = self.original_images.split('|') if self.original_images else []
        
        result = []
        for img in images:
            if not img:
                continue
            if isinstance(img, str):
                img = img.strip()
                if not img or img.lower() == 'none':
                    continue
                if img.startswith('http'):
                    result.append(img)
                elif img.startswith('/'):
                    result.append(img)
                else:
                    result.append('/uploads/' + img.lstrip('/'))
        return result
    
    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': self.id,
            'reference': self.reference,
            'emirate': self.emirate,
            'city': self.city,
            'location': self.location,
            'location_id': self.location_id,
            'category': self.category,
            'offering_type': self.offering_type,
            'property_type': self.property_type,
            'bedrooms': self.bedrooms,
            'bathrooms': self.bathrooms,
            'size': self.size,
            'furnishing_type': self.furnishing_type,
            'project_status': self.project_status,
            'parking_slots': self.parking_slots,
            'floor_number': self.floor_number,
            'unit_number': self.unit_number,
            'price': self.price,
            'downpayment': self.downpayment,
            'rent_frequency': self.rent_frequency,
            'title_en': self.title_en,
            'title_ar': self.title_ar,
            'description_en': self.description_en,
            'description_ar': self.description_ar,
            'images': self._parse_images(),
            'video_tour': self.video_tour,
            'video_360': self.video_360,
            'amenities': self.amenities.split(',') if self.amenities else [],
            'assigned_agent': self.assigned_agent,
            'assigned_to_id': self.assigned_to_id,
            'assigned_to_name': self.assigned_to.name if self.assigned_to else None,
            'owner_id': self.owner_id,
            'owner_name': self.owner_name,
            'developer': self.developer,
            'permit_number': self.permit_number,
            'available_from': self.available_from,
            'views': self.views or 0,
            'leads': self.leads or 0,
            'status': self.status,
            'pf_listing_id': self.pf_listing_id,
            'folder_id': self.folder_id,
            'folder': self.folder.to_dict() if self.folder else None,
            'synced_at': self.synced_at.isoformat() if self.synced_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'original_images': self._parse_original_images(),
        }
    
    @classmethod
    def from_dict(cls, data):
        """Create from dictionary - handles both local format and PropertyFinder API format"""
        
        # Handle PropertyFinder API format (camelCase, nested objects)
        # Extract values from nested structures
        
        # Images - can be string, list, or PF format [{original: {url}}]
        images = data.get('images', [])
        if isinstance(images, list):
            if len(images) > 0 and isinstance(images[0], dict):
                # PF format: [{original: {url: '...'}}]
                images = '|'.join([img.get('original', {}).get('url', '') for img in images if img.get('original', {}).get('url')])
            else:
                images = '|'.join([str(img) for img in images if img])

        original_images = data.get('original_images')
        if isinstance(original_images, list):
            original_images = '|'.join([str(img) for img in original_images if img])
        
        # Handle media object (PF format)
        media = data.get('media', {})
        if media:
            if not images and media.get('images'):
                img_list = media.get('images', [])
                images = '|'.join([img.get('original', {}).get('url', '') for img in img_list if img.get('original', {}).get('url')])
        
        # Videos from media object
        videos = media.get('videos', {}) if media else {}
        video_tour = data.get('video_tour') or videos.get('default', '')
        video_360 = data.get('video_360') or videos.get('view360', '')
        
        # Handle amenities list
        amenities = data.get('amenities', [])
        if isinstance(amenities, list):
            amenities = ','.join([str(a) for a in amenities])
        
        # Handle title/description (PF uses nested objects)
        title = data.get('title', {})
        title_en = data.get('title_en') or (title.get('en') if isinstance(title, dict) else None)
        title_ar = data.get('title_ar') or (title.get('ar') if isinstance(title, dict) else None)
        
        description = data.get('description', {})
        description_en = data.get('description_en') or (description.get('en') if isinstance(description, dict) else None)
        description_ar = data.get('description_ar') or (description.get('ar') if isinstance(description, dict) else None)
        
        # Handle price (PF uses nested price.type, price.amounts)
        price_obj = data.get('price', {})
        price = data.get('price') if not isinstance(data.get('price'), dict) else None
        price_type = None
        downpayment = data.get('downpayment')
        
        if isinstance(price_obj, dict):
            price_type = price_obj.get('type')
            amounts = price_obj.get('amounts', {})
            # Get price from amounts based on type
            if price_type and amounts:
                price = amounts.get(price_type) or amounts.get('sale') or amounts.get('yearly') or 0
            downpayment = downpayment or price_obj.get('downpayment')
        
        # Determine offering type from price type
        offering_type = data.get('offering_type')
        if not offering_type and price_type:
            offering_type = 'sale' if price_type == 'sale' else 'rent'
        rent_frequency = data.get('rent_frequency') or (price_type if price_type != 'sale' else None)
        
        # Handle location (PF uses location.id)
        location = data.get('location')
        location_id = None
        if isinstance(location, dict):
            location_id = location.get('id')
            location = data.get('_locationText', '')  # Fallback text if provided
        
        # Handle assignedTo (PF uses assignedTo.id)
        assigned_to = data.get('assignedTo', {})
        assigned_agent = data.get('assigned_agent')
        if isinstance(assigned_to, dict) and assigned_to.get('id'):
            assigned_agent = str(assigned_to.get('id'))
        
        # Handle compliance (PF format)
        compliance = data.get('compliance', {})
        permit_number = data.get('permit_number')
        if isinstance(compliance, dict) and compliance.get('listingAdvertisementNumber'):
            permit_number = compliance.get('listingAdvertisementNumber')
        
        return cls(
            reference=data.get('reference'),
            emirate=data.get('emirate') or data.get('uaeEmirate'),
            city=data.get('city'),
            location=location if isinstance(location, str) else '',
            location_id=location_id,
            category=data.get('category'),
            offering_type=offering_type,
            property_type=data.get('property_type') or data.get('type'),
            bedrooms=str(data.get('bedrooms', '')),
            bathrooms=str(data.get('bathrooms', '')),
            size=float(data.get('size', 0)) if data.get('size') else None,
            furnishing_type=data.get('furnishing_type') or data.get('furnishingType'),
            project_status=data.get('project_status') or data.get('projectStatus'),
            parking_slots=int(data.get('parking_slots') or data.get('parkingSlots') or 0) if (data.get('parking_slots') or data.get('parkingSlots')) else None,
            floor_number=data.get('floor_number') or data.get('floorNumber'),
            unit_number=data.get('unit_number') or data.get('unitNumber'),
            price=float(price) if price else None,
            downpayment=float(downpayment) if downpayment else None,
            rent_frequency=rent_frequency,
            title_en=title_en,
            title_ar=title_ar,
            description_en=description_en,
            description_ar=description_ar,
            images=images,
            original_images=original_images,
            video_tour=video_tour,
            video_360=video_360,
            amenities=amenities,
            assigned_agent=assigned_agent,
            assigned_to_id=data.get('assigned_to_id'),
            owner_id=data.get('owner_id'),
            owner_name=data.get('owner_name') or data.get('ownerName'),
            developer=data.get('developer'),
            permit_number=permit_number,
            available_from=data.get('available_from') or data.get('availableFrom'),
            status=data.get('status', 'draft'),
        )
    
    def to_pf_format(self):
        """
        Convert local listing to PropertyFinder API format
        
        Required by PF API:
        - uaeEmirate: dubai, abu_dhabi, northern_emirates
        - type: Property type (apartment, villa, etc.)
        - category: residential or commercial
        - price.type: yearly, sale, monthly, etc.
        - price.amounts: {yearly: 50000} or {sale: 1000000}
        - location.id: Location ID from /locations API
        - title.en or title.ar: Listing title
        - assignedTo.id: Public profile ID from /users API
        - bedrooms: string (studio, 1-30)
        - bathrooms: string (none, 1-20)
        """
        import uuid
        import time
        
        # Auto-generate reference if missing
        reference = self.reference
        if not reference:
            date_part = time.strftime('%Y%m%d')
            unique_part = uuid.uuid4().hex[:5].upper()
            reference = f"REF-{date_part}-{unique_part}"
            # Save it back to the model
            self.reference = reference
        
        # Property type - already stored in API format
        prop_type = self.property_type.lower() if self.property_type else 'apartment'
        
        pf_data = {
            'reference': reference,
            'category': self.category or 'residential',
            'type': prop_type,
        }
        
        # UAE Emirate
        if self.emirate:
            emirate = self.emirate.lower()
            if emirate in ['dubai', 'abu_dhabi', 'northern_emirates']:
                pf_data['uaeEmirate'] = emirate
            elif 'abu' in emirate or 'dhabi' in emirate:
                pf_data['uaeEmirate'] = 'abu_dhabi'
            elif 'dubai' in emirate:
                pf_data['uaeEmirate'] = 'dubai'
            else:
                pf_data['uaeEmirate'] = 'northern_emirates'
        
        # Location - use location_id if available
        if self.location_id:
            pf_data['location'] = {'id': int(self.location_id)}
        
        # Assigned Agent and Created By (both required by PF API)
        if self.assigned_agent:
            try:
                agent_id = int(self.assigned_agent)
                pf_data['assignedTo'] = {'id': agent_id}
                pf_data['createdBy'] = {'id': agent_id}  # Required by PF API
            except (ValueError, TypeError):
                pass
        
        # Title
        if self.title_en or self.title_ar:
            pf_data['title'] = {}
            if self.title_en:
                pf_data['title']['en'] = self.title_en[:100]  # Max 100 chars
            if self.title_ar:
                pf_data['title']['ar'] = self.title_ar[:100]
        
        # Description
        if self.description_en or self.description_ar:
            pf_data['description'] = {}
            if self.description_en:
                pf_data['description']['en'] = self.description_en[:5000]
            if self.description_ar:
                pf_data['description']['ar'] = self.description_ar[:5000]
        
        # Price structure (API format)
        if self.price:
            price_type = 'sale' if self.offering_type == 'sale' else (self.rent_frequency or 'yearly')
            pf_data['price'] = {
                'type': price_type,
                'amounts': {price_type: int(self.price)}
            }
            if self.downpayment and price_type == 'sale':
                pf_data['price']['downpayment'] = int(self.downpayment)
        
        # Specifications - bedrooms/bathrooms must be strings
        if self.bedrooms:
            beds = str(self.bedrooms).lower().strip()
            if beds == '0':
                beds = 'studio'
            pf_data['bedrooms'] = beds
        
        if self.bathrooms:
            baths = str(self.bathrooms).lower().strip()
            if baths == '0':
                baths = 'none'
            pf_data['bathrooms'] = baths
        
        # Size - number in sqft
        if self.size:
            pf_data['size'] = float(self.size)
        
        # Furnishing type
        if self.furnishing_type:
            furn = self.furnishing_type.lower()
            if furn in ['furnished', 'semi-furnished', 'unfurnished']:
                pf_data['furnishingType'] = furn
        
        # Project status
        if self.project_status:
            status = self.project_status.lower()
            if status in ['completed', 'off_plan', 'completed_primary', 'off_plan_primary']:
                pf_data['projectStatus'] = status
        
        # Other specs
        if self.parking_slots:
            pf_data['parkingSlots'] = int(self.parking_slots)
        if self.floor_number:
            pf_data['floorNumber'] = str(self.floor_number)
        if self.unit_number:
            pf_data['unitNumber'] = str(self.unit_number)
        if self.developer:
            pf_data['developer'] = self.developer
        if self.available_from:
            pf_data['availableFrom'] = self.available_from
        
        # Amenities - must be valid API values
        if self.amenities:
            amenities_list = self.amenities.split(',') if isinstance(self.amenities, str) else self.amenities
            valid_amenities = [
                'central-ac', 'built-in-wardrobes', 'kitchen-appliances', 'security',
                'concierge', 'private-gym', 'shared-gym', 'private-jacuzzi', 'shared-spa',
                'covered-parking', 'maids-room', 'barbecue-area', 'shared-pool',
                'childrens-pool', 'private-garden', 'private-pool', 'view-of-water',
                'walk-in-closet', 'lobby-in-building', 'electricity', 'waters',
                'sanitation', 'no-services', 'fixed-phone', 'fibre-optics',
                'flood-drainage', 'balcony', 'networked', 'view-of-landmark',
                'dining-in-building', 'conference-room', 'study', 'maid-service',
                'childrens-play-area', 'pets-allowed', 'vastu-compliant'
            ]
            pf_data['amenities'] = [a.strip() for a in amenities_list if a.strip() in valid_amenities]
        
        # Media - Images in API format
        # PropertyFinder requires publicly accessible URLs
        if self.images:
            import os
            
            # Get public URL base (Railway or custom domain)
            public_url = os.environ.get('APP_PUBLIC_URL') or os.environ.get('RAILWAY_PUBLIC_DOMAIN')
            if public_url and not public_url.startswith('http'):
                public_url = f'https://{public_url}'
            
            # Parse images from JSON or pipe-separated format
            if isinstance(self.images, str):
                try:
                    import json as json_module
                    parsed = json_module.loads(self.images)
                    images_list = parsed if isinstance(parsed, list) else [parsed]
                except:
                    images_list = self.images.split('|')
            else:
                images_list = self.images
            
            processed_urls = []
            for img in images_list:
                if not img:
                    continue
                    
                url = img.strip() if isinstance(img, str) else str(img)
                if not url or url.lower() == 'none':
                    continue
                
                # Convert local paths to public URLs
                if url.startswith('/uploads/') and public_url:
                    url = f'{public_url}{url}'
                elif url.startswith('uploads/') and public_url:
                    url = f'{public_url}/{url}'
                elif url.startswith('listings/') and public_url:
                    url = f'{public_url}/uploads/{url}'
                
                # Convert Google Drive URLs
                if url.startswith('http'):
                    url = convert_google_drive_url(url)
                    processed_urls.append(url)
                elif public_url and '/uploads/' in url:
                    # Local path converted to public URL
                    processed_urls.append(url)
            
            if processed_urls:
                pf_data['media'] = {
                    'images': [{'original': {'url': url}} for url in processed_urls]
                }
        
        # Media - Videos (auto-convert Google Drive URLs)
        if self.video_tour or self.video_360:
            if 'media' not in pf_data:
                pf_data['media'] = {}
            pf_data['media']['videos'] = {}
            if self.video_tour:
                pf_data['media']['videos']['default'] = convert_google_drive_url(self.video_tour)
            if self.video_360:
                pf_data['media']['videos']['view360'] = convert_google_drive_url(self.video_360)
        
        # Compliance (RERA/ADREC permit)
        if self.permit_number:
            emirate = pf_data.get('uaeEmirate', 'dubai')
            compliance_type = 'adrec' if emirate == 'abu_dhabi' else 'rera'
            pf_data['compliance'] = {
                'type': compliance_type,
                'listingAdvertisementNumber': self.permit_number
            }
        
        return pf_data


class PFSession(db.Model):
    """Store PropertyFinder browser session"""
    __tablename__ = 'pf_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    cookies = db.Column(db.Text)  # JSON serialized cookies
    user_agent = db.Column(db.String(500))
    logged_in = db.Column(db.Boolean, default=False)
    email = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PFCache(db.Model):
    """Cache PropertyFinder API data in database for fast access"""
    __tablename__ = 'pf_cache'
    __table_args__ = (
        db.UniqueConstraint('workspace_id', 'cache_type', name='uq_pf_cache_workspace_type'),
        db.Index('idx_pf_cache_type', 'cache_type'),
        db.Index('idx_pf_cache_workspace', 'workspace_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    cache_type = db.Column(db.String(50), nullable=False)  # 'listings', 'users', 'leads'
    data = db.Column(db.Text)  # JSON serialized data
    count = db.Column(db.Integer, default=0)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=True, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @classmethod
    def _resolve_workspace_id(cls, workspace_id):
        if workspace_id is not None:
            return workspace_id
        try:
            from flask import has_request_context, g, session
            if has_request_context():
                ws = getattr(g, 'workspace', None)
                if ws:
                    return ws.id
                if session.get('active_workspace_id'):
                    return session.get('active_workspace_id')
                if session.get('current_workspace_id'):
                    return session.get('current_workspace_id')
        except Exception:
            pass
        return None

    @classmethod
    def get_cache(cls, cache_type, workspace_id=None):
        """Get cached data by type (workspace-aware)"""
        import json
        workspace_id = cls._resolve_workspace_id(workspace_id)
        cache = cls.query.filter_by(cache_type=cache_type, workspace_id=workspace_id).first()
        if cache and cache.data:
            try:
                return json.loads(cache.data)
            except:
                return []
        return []
    
    @classmethod
    def set_cache(cls, cache_type, data, workspace_id=None):
        """Set cache data by type (workspace-aware)"""
        import json
        workspace_id = cls._resolve_workspace_id(workspace_id)
        cache = cls.query.filter_by(cache_type=cache_type, workspace_id=workspace_id).first()
        if not cache:
            cache = cls(cache_type=cache_type, workspace_id=workspace_id)
            db.session.add(cache)
        
        cache.data = json.dumps(data, default=str)
        cache.count = len(data) if isinstance(data, list) else 1
        cache.updated_at = datetime.utcnow()
        db.session.commit()
        return cache
    
    @classmethod
    def get_last_update(cls, cache_type=None, workspace_id=None):
        """Get the last update time (workspace-aware)"""
        workspace_id = cls._resolve_workspace_id(workspace_id)
        if cache_type:
            cache = cls.query.filter_by(cache_type=cache_type, workspace_id=workspace_id).first()
            return cache.updated_at if cache else None
        else:
            # Get the most recent update time across all cache types (for 'listings')
            cache = cls.query.filter_by(cache_type='listings', workspace_id=workspace_id).first()
            return cache.updated_at if cache else None
    
    @classmethod
    def get_all_cached_data(cls, workspace_id=None):
        """Get all cached data as a dictionary (workspace-aware)"""
        return {
            'listings': cls.get_cache('listings', workspace_id=workspace_id),
            'users': cls.get_cache('users', workspace_id=workspace_id),
            'leads': cls.get_cache('leads', workspace_id=workspace_id),
            'last_updated': cls.get_last_update(workspace_id=workspace_id)
        }


# ==================== CRM: LEADS ====================

class Lead(db.Model):
    """Incoming leads from all sources: PropertyFinder, Bayut, Zapier, etc."""
    __tablename__ = 'crm_leads'
    __table_args__ = (
        db.Index('idx_leads_workspace_id', 'workspace_id'),
        db.Index('idx_leads_status', 'status'),
        db.Index('idx_leads_source', 'source'),
        db.Index('idx_leads_priority', 'priority'),
        db.Index('idx_leads_lead_type', 'lead_type'),
        db.Index('idx_leads_assigned_to_id', 'assigned_to_id'),
        db.Index('idx_leads_pf_agent_id', 'pf_agent_id'),
        db.Index('idx_leads_customer_id', 'customer_id'),
        db.Index('idx_leads_received_at', 'received_at'),
        db.Index('idx_leads_created_at', 'created_at'),
        db.Index('idx_leads_next_follow_up', 'next_follow_up'),
        db.Index('idx_leads_source_id', 'source_id'),
        db.Index('idx_leads_pf_listing_id', 'pf_listing_id'),
        # Composite indexes for common filters
        db.Index('idx_leads_status_source', 'status', 'source'),
        db.Index('idx_leads_status_assigned', 'status', 'assigned_to_id'),
        db.Index('idx_leads_agent_status', 'pf_agent_id', 'status'),
        db.Index('idx_leads_workspace_status', 'workspace_id', 'status'),
        db.Index('idx_leads_workspace_tags', 'workspace_id', 'tags'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=True, index=True)
    
    # Source
    source = db.Column(db.String(30), default='other')  # propertyfinder, bayut, website, facebook, instagram, zapier, phone, email
    source_id = db.Column(db.String(100))  # External ID from source
    channel = db.Column(db.String(30))  # whatsapp, email, call, etc.
    
    # Contact Info
    name = db.Column(db.String(200), nullable=False, index=True)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))
    whatsapp = db.Column(db.String(50))
    
    # Inquiry
    message = db.Column(db.Text)
    listing_reference = db.Column(db.String(50))  # Related listing
    pf_listing_id = db.Column(db.String(50))  # PropertyFinder listing ID
    response_link = db.Column(db.String(500))  # PropertyFinder response link
    
    # Status: new, contacted, qualified, viewing, negotiation, won, lost, spam
    status = db.Column(db.String(30), default='new')
    pf_status = db.Column(db.String(30))  # Original PF status: sent, delivered, read, replied
    priority = db.Column(db.String(20), default='medium')  # low, medium, high, urgent
    lead_type = db.Column(db.String(20), default='for_sale')  # for_sale, for_rent
    
    # Assignment - from PropertyFinder (publicProfile)
    pf_agent_id = db.Column(db.String(50))  # PropertyFinder public profile ID
    pf_agent_name = db.Column(db.String(100))  # Agent name from PF
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Follow-up
    last_contact = db.Column(db.DateTime)
    next_follow_up = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    tags = db.Column(db.Text)  # comma-separated workspace tag IDs
    
    # Conversion
    customer_id = db.Column(db.Integer, db.ForeignKey('crm_customers.id'), nullable=True)
    converted_at = db.Column(db.DateTime)
    
    # Timestamps
    received_at = db.Column(db.DateTime)  # Actual date received in PF/source
    created_at = db.Column(db.DateTime, default=datetime.utcnow)  # When added to our system
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id])
    customer = db.relationship('Customer', back_populates='leads')
    
    def to_dict(self):
        return {
            'id': self.id,
            'source': self.source,
            'source_id': self.source_id,
            'channel': self.channel,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'whatsapp': self.whatsapp,
            'message': self.message,
            'listing_reference': self.listing_reference,
            'pf_listing_id': self.pf_listing_id,
            'response_link': self.response_link,
            'status': self.status,
            'pf_status': self.pf_status,
            'priority': self.priority,
            'lead_type': getattr(self, 'lead_type', None) or 'for_sale',
            'pf_agent_id': self.pf_agent_id,
            'pf_agent_name': self.pf_agent_name,
            'assigned_to_id': self.assigned_to_id,
            'assigned_to_name': self.assigned_to.name if self.assigned_to else None,
            'last_contact': self.last_contact.isoformat() if self.last_contact else None,
            'next_follow_up': self.next_follow_up.isoformat() if self.next_follow_up else None,
            'notes': self.notes,
            'tags': self.get_tags(),
            'customer_id': self.customer_id,
            'converted_at': self.converted_at.isoformat() if self.converted_at else None,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def get_tags(self):
        """Return normalized list of tag ids."""
        if not self.tags:
            return []
        values = []
        for raw in str(self.tags).split(','):
            tag = raw.strip().lower()
            if tag and tag not in values:
                values.append(tag)
        return values

    def set_tags(self, tag_ids):
        """Store normalized tag ids in comma-separated form."""
        if not tag_ids:
            self.tags = None
            return
        normalized = []
        for raw in tag_ids:
            tag = str(raw or '').strip().lower()
            if tag and tag not in normalized:
                normalized.append(tag)
        self.tags = ','.join(normalized) if normalized else None


class LeadReminder(db.Model):
    """Lead reminder records for events/meetings/actions."""
    __tablename__ = 'lead_reminders'
    __table_args__ = (
        db.Index('idx_lead_reminders_workspace_due', 'workspace_id', 'due_at'),
        db.Index('idx_lead_reminders_lead_due', 'lead_id', 'due_at'),
        db.Index('idx_lead_reminders_assignee_status_due', 'assigned_to_id', 'status', 'due_at'),
        db.Index('idx_lead_reminders_workspace_status', 'workspace_id', 'status'),
    )

    TYPE_EVENT = 'event'
    TYPE_MEETING = 'meeting'
    TYPE_ACTION = 'action'
    TYPES = (TYPE_EVENT, TYPE_MEETING, TYPE_ACTION)

    STATUS_PENDING = 'pending'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELLED = 'cancelled'
    STATUSES = (STATUS_PENDING, STATUS_COMPLETED, STATUS_CANCELLED)

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('crm_leads.id', ondelete='CASCADE'), nullable=False, index=True)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    type = db.Column(db.String(20), nullable=False, default=TYPE_ACTION)
    title = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text)
    due_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    completed_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    workspace = db.relationship('Workspace', foreign_keys=[workspace_id])
    lead = db.relationship('Lead', backref=db.backref('reminders', lazy='dynamic', cascade='all, delete-orphan'))
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    def to_dict(self):
        now = datetime.utcnow()
        is_overdue = bool(
            self.status == self.STATUS_PENDING and
            self.due_at is not None and
            self.due_at < now
        )
        return {
            'id': self.id,
            'workspace_id': self.workspace_id,
            'lead_id': self.lead_id,
            'assigned_to_id': self.assigned_to_id,
            'assigned_to_name': self.assigned_to.name if self.assigned_to else None,
            'created_by_id': self.created_by_id,
            'created_by_name': self.created_by.name if self.created_by else None,
            'type': self.type,
            'title': self.title,
            'notes': self.notes,
            'due_at': self.due_at.isoformat() if self.due_at else None,
            'status': self.status,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'cancelled_at': self.cancelled_at.isoformat() if self.cancelled_at else None,
            'is_overdue': is_overdue,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class LeadComment(db.Model):
    """Comments/notes on leads with timestamps and user attribution"""
    __tablename__ = 'lead_comments'
    __table_args__ = (
        db.Index('idx_lead_comments_lead_id', 'lead_id'),
        db.Index('idx_lead_comments_user_id', 'user_id'),
        db.Index('idx_lead_comments_created_at', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('crm_leads.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    lead = db.relationship('Lead', backref=db.backref('comments', lazy='dynamic', cascade='all, delete-orphan'))
    user = db.relationship('User')
    
    def to_dict(self):
        return {
            'id': self.id,
            'lead_id': self.lead_id,
            'user_id': self.user_id,
            'user_name': self.user.name if self.user else 'System',
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ==================== CONTACTS ====================

class Contact(db.Model):
    """Saved contacts with phone numbers and country codes"""
    __tablename__ = 'contacts'
    __table_args__ = (
        db.Index('idx_contacts_workspace_id', 'workspace_id'),
        db.Index('idx_contacts_name', 'name'),
        db.Index('idx_contacts_phone', 'phone'),
        db.Index('idx_contacts_email', 'email'),
        db.Index('idx_contacts_lead_id', 'lead_id'),
        db.Index('idx_contacts_created_by_id', 'created_by_id'),
        db.Index('idx_contacts_created_at', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=True, index=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50), nullable=False)  # Full phone with country code
    country_code = db.Column(db.String(10), default='+971')  # UAE default
    email = db.Column(db.String(120))
    company = db.Column(db.String(200))
    notes = db.Column(db.Text)
    tags = db.Column(db.String(500))  # comma-separated
    
    # Linked to lead (optional)
    lead_id = db.Column(db.Integer, db.ForeignKey('crm_leads.id'), nullable=True)
    
    # Created by
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    lead = db.relationship('Lead', backref=db.backref('contacts', lazy='dynamic'))
    created_by = db.relationship('User')
    
    # Common country codes
    COUNTRY_CODES = [
        ('+971', 'UAE'),
        ('+966', 'Saudi Arabia'),
        ('+973', 'Bahrain'),
        ('+974', 'Qatar'),
        ('+965', 'Kuwait'),
        ('+968', 'Oman'),
        ('+20', 'Egypt'),
        ('+91', 'India'),
        ('+92', 'Pakistan'),
        ('+63', 'Philippines'),
        ('+44', 'UK'),
        ('+1', 'USA/Canada'),
        ('+86', 'China'),
        ('+7', 'Russia'),
        ('+33', 'France'),
        ('+49', 'Germany'),
    ]
    
    def get_full_phone(self):
        """Get phone with country code"""
        if self.phone.startswith('+'):
            return self.phone
        return f"{self.country_code}{self.phone.lstrip('0')}"
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone,
            'country_code': self.country_code,
            'full_phone': self.get_full_phone(),
            'email': self.email,
            'company': self.company,
            'notes': self.notes,
            'tags': self.tags.split(',') if self.tags else [],
            'lead_id': self.lead_id,
            'created_by_id': self.created_by_id,
            'created_by_name': self.created_by.name if self.created_by else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


# ==================== CRM: CUSTOMERS ====================

class Customer(db.Model):
    """Customer/prospect for CRM"""
    __tablename__ = 'crm_customers'
    __table_args__ = (
        db.Index('idx_customers_name', 'name'),
        db.Index('idx_customers_phone', 'phone'),
        db.Index('idx_customers_customer_type', 'customer_type'),
        db.Index('idx_customers_status', 'status'),
        db.Index('idx_customers_assigned_agent_id', 'assigned_agent_id'),
        db.Index('idx_customers_created_at', 'created_at'),
        db.Index('idx_customers_last_contact', 'last_contact'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Identity
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True)
    phone = db.Column(db.String(50))
    whatsapp = db.Column(db.String(50))
    nationality = db.Column(db.String(50))
    
    # Type: buyer, seller, tenant, landlord, investor
    customer_type = db.Column(db.String(20), default='buyer')
    status = db.Column(db.String(20), default='prospect')  # prospect, active, inactive, vip
    
    # Preferences
    interested_in = db.Column(db.String(20))  # sale, rent
    min_budget = db.Column(db.Float)
    max_budget = db.Column(db.Float)
    preferred_locations = db.Column(db.Text)  # JSON array
    
    # Assignment
    assigned_agent_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Stats
    total_leads = db.Column(db.Integer, default=0)
    total_viewings = db.Column(db.Integer, default=0)
    
    # Notes
    notes = db.Column(db.Text)
    tags = db.Column(db.String(500))  # comma-separated
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_contact = db.Column(db.DateTime)
    
    # Relationships
    leads = db.relationship('Lead', back_populates='customer')
    assigned_agent = db.relationship('User', foreign_keys=[assigned_agent_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'whatsapp': self.whatsapp,
            'nationality': self.nationality,
            'customer_type': self.customer_type,
            'status': self.status,
            'interested_in': self.interested_in,
            'min_budget': self.min_budget,
            'max_budget': self.max_budget,
            'preferred_locations': self.preferred_locations,
            'assigned_agent_id': self.assigned_agent_id,
            'assigned_agent_name': self.assigned_agent.name if self.assigned_agent else None,
            'total_leads': self.total_leads,
            'total_viewings': self.total_viewings,
            'notes': self.notes,
            'tags': self.tags.split(',') if self.tags else [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_contact': self.last_contact.isoformat() if self.last_contact else None,
        }


# ==================== APP SETTINGS ====================

class AppSettings(db.Model):
    """Application settings stored in database"""
    __tablename__ = 'app_settings'
    __table_args__ = (
        db.UniqueConstraint('workspace_id', 'key', name='uq_app_settings_workspace_key'),
        db.Index('idx_app_settings_workspace', 'workspace_id'),
        db.Index('idx_app_settings_key', 'key'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), nullable=False)
    value = db.Column(db.Text)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=True, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Default settings
    DEFAULTS = {
        'sync_interval_minutes': '30',
        'auto_sync_enabled': 'true',
        'workspace_timezone': 'Asia/Dubai',
        'default_agent_email': '',
        'default_owner_email': '',
        'default_insights_agent_id': '',  # PF user ID to show by default in insights
        'last_sync_at': '',
        'first_run_completed': 'false',
        # Lead CRM settings - JSON arrays
        'lead_statuses': '[{"id":"new","label":"New","color":"blue"},{"id":"contacted","label":"Contacted","color":"yellow"},{"id":"qualified","label":"Qualified","color":"green"},{"id":"viewing","label":"Viewing","color":"purple"},{"id":"negotiation","label":"Negotiation","color":"orange"},{"id":"won","label":"Won","color":"emerald"},{"id":"lost","label":"Lost","color":"red"},{"id":"spam","label":"Spam","color":"gray"}]',
        'lead_sources': '[{"id":"propertyfinder","label":"PropertyFinder","color":"red"},{"id":"bayut","label":"Bayut","color":"blue"},{"id":"website","label":"Website","color":"purple"},{"id":"facebook","label":"Facebook","color":"indigo"},{"id":"instagram","label":"Instagram","color":"pink"},{"id":"whatsapp","label":"WhatsApp","color":"green"},{"id":"phone","label":"Phone","color":"gray"},{"id":"email","label":"Email","color":"cyan"},{"id":"referral","label":"Referral","color":"amber"},{"id":"zapier","label":"Zapier","color":"orange"},{"id":"other","label":"Other","color":"gray"}]',
        'lead_tags': '[]',
        # Image processing settings
        'image_default_ratio': 'landscape_16_9',
        'image_default_size': 'full_hd',
        'image_max_dimension': '1920',
        'image_quality': '90',
        'image_format': 'JPEG',
        'image_qr_enabled': 'true',
        'image_qr_data': '',              # Default QR data (URL, etc.)
        'image_qr_position': 'bottom_right',
        'image_qr_size_percent': '12',
        'image_qr_color': '#000000',
        'image_qr_opacity': '1.0',
        'image_logo_enabled': 'false',
        'image_logo_data': '',            # Base64 encoded logo
        'image_logo_position': 'bottom_left',
        'image_logo_size_percent': '10',
        'image_logo_opacity': '0.9',
    }
    
    @classmethod
    def _resolve_workspace_id(cls, workspace_id):
        if workspace_id is not None:
            return workspace_id
        try:
            from flask import has_request_context, g, session
            if has_request_context():
                ws = getattr(g, 'workspace', None)
                if ws:
                    return ws.id
                if session.get('active_workspace_id'):
                    return session.get('active_workspace_id')
                if session.get('current_workspace_id'):
                    return session.get('current_workspace_id')
        except Exception:
            pass
        return None

    @classmethod
    def get(cls, key, default=None, workspace_id=None):
        """Get a setting value (workspace-aware)"""
        workspace_id = cls._resolve_workspace_id(workspace_id)
        if workspace_id is not None:
            setting = cls.query.filter_by(key=key, workspace_id=workspace_id).first()
            if setting:
                return setting.value
        setting = cls.query.filter_by(key=key, workspace_id=None).first()
        if setting:
            return setting.value
        return default if default is not None else cls.DEFAULTS.get(key, '')
    
    @classmethod
    def set(cls, key, value, workspace_id=None):
        """Set a setting value (workspace-aware)"""
        workspace_id = cls._resolve_workspace_id(workspace_id)
        setting = cls.query.filter_by(key=key, workspace_id=workspace_id).first()
        if not setting:
            setting = cls(key=key, workspace_id=workspace_id)
            db.session.add(setting)
        setting.value = str(value) if value is not None else ''
        db.session.commit()
        return setting
    
    @classmethod
    def get_all(cls, workspace_id=None):
        """Get all settings as dictionary (workspace-aware)"""
        workspace_id = cls._resolve_workspace_id(workspace_id)
        settings = {}
        for key, default in cls.DEFAULTS.items():
            settings[key] = cls.get(key, default, workspace_id=workspace_id)
        return settings
    
    @classmethod
    def init_defaults(cls, workspace_id=None):
        """Initialize default settings if not exist (workspace-aware)."""
        for key, default in cls.DEFAULTS.items():
            if not cls.query.filter_by(key=key, workspace_id=workspace_id).first():
                cls.set(key, default, workspace_id=workspace_id)


# ==================== LISTING LOOP SYSTEM ====================

class LoopConfig(db.Model):
    """Configuration for a listing loop (auto-duplicate/republish)"""
    __tablename__ = 'loop_configs'
    __table_args__ = (
        db.Index('idx_loop_configs_workspace_id', 'workspace_id'),
        db.Index('idx_loop_configs_owner_user_id', 'owner_user_id'),
        db.Index('idx_loop_configs_is_active', 'is_active'),
        db.Index('idx_loop_configs_next_run', 'next_run_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=True, index=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    
    # Loop type: 'duplicate' = create copy & publish, 'delete_republish' = delete from PF & republish
    loop_type = db.Column(db.String(20), default='duplicate')
    
    # Timing
    interval_hours = db.Column(db.Float, default=1.0)  # Hours between each action
    interval_unit = db.Column(db.String(16), default='hours')  # hours, minutes, seconds
    schedule_mode = db.Column(db.String(32), default='interval')  # interval, windowed_interval, daily_times
    schedule_window_start = db.Column(db.String(5), nullable=True)  # HH:MM
    schedule_window_end = db.Column(db.String(5), nullable=True)    # HH:MM
    schedule_exact_times = db.Column(db.Text, nullable=True)        # JSON array of HH:MM

    # Duplicate handling
    keep_duplicates = db.Column(db.Boolean, default=True)  # Keep in "Duplicated" folder
    max_duplicates = db.Column(db.Integer, default=0)  # 0 = unlimited
    
    # Status
    is_active = db.Column(db.Boolean, default=False)
    is_paused = db.Column(db.Boolean, default=False)
    
    # Execution tracking
    current_index = db.Column(db.Integer, default=0)  # Current position in listing sequence
    consecutive_failures = db.Column(db.Integer, default=0)  # For auto-stop logic
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_run_at = db.Column(db.DateTime, nullable=True)
    next_run_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    listings = db.relationship('LoopListing', backref='loop_config', lazy='dynamic', cascade='all, delete-orphan')
    duplicates = db.relationship('DuplicatedListing', backref='loop_config', lazy='dynamic')
    owner = db.relationship('User', foreign_keys=[owner_user_id])

    SCHEDULE_INTERVAL = 'interval'
    SCHEDULE_WINDOWED_INTERVAL = 'windowed_interval'
    SCHEDULE_DAILY_TIMES = 'daily_times'
    SCHEDULE_MODES = [SCHEDULE_INTERVAL, SCHEDULE_WINDOWED_INTERVAL, SCHEDULE_DAILY_TIMES]
    INTERVAL_UNITS = ['hours', 'minutes', 'seconds']

    def get_interval_unit(self):
        unit = (self.interval_unit or 'hours').strip().lower()
        return unit if unit in self.INTERVAL_UNITS else 'hours'

    def get_interval_value(self):
        try:
            hours = float(self.interval_hours or 1.0)
        except (TypeError, ValueError):
            hours = 1.0
        if hours <= 0:
            hours = 1.0
        unit = self.get_interval_unit()
        if unit == 'minutes':
            value = hours * 60.0
        elif unit == 'seconds':
            value = hours * 3600.0
        else:
            value = hours
        if abs(value - round(value)) < 1e-9:
            return int(round(value))
        return value

    def get_schedule_exact_times(self):
        """Return exact times schedule as a list of HH:MM strings."""
        import json
        raw = self.schedule_exact_times
        if not raw:
            return []
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return []
        if not isinstance(value, list):
            return []
        return [str(v) for v in value if isinstance(v, str)]

    def set_schedule_exact_times(self, values):
        """Persist exact times schedule from a list of HH:MM strings."""
        import json
        if not values:
            self.schedule_exact_times = None
            return
        self.schedule_exact_times = json.dumps(list(values))
    
    def to_dict(self):
        return {
            'id': self.id,
            'owner_user_id': self.owner_user_id,
            'owner_name': self.owner.name if self.owner else None,
            'name': self.name,
            'loop_type': self.loop_type,
            'interval_hours': self.interval_hours,
            'interval_unit': self.get_interval_unit(),
            'interval_value': self.get_interval_value(),
            'schedule_mode': self.schedule_mode or self.SCHEDULE_INTERVAL,
            'schedule_window_start': self.schedule_window_start,
            'schedule_window_end': self.schedule_window_end,
            'schedule_exact_times': self.get_schedule_exact_times(),
            'keep_duplicates': self.keep_duplicates,
            'max_duplicates': self.max_duplicates,
            'is_active': self.is_active,
            'is_paused': self.is_paused,
            'current_index': self.current_index,
            'consecutive_failures': self.consecutive_failures,
            'listing_count': self.listings.count(),
            'duplicate_count': self.duplicates.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_run_at': self.last_run_at.isoformat() if self.last_run_at else None,
            'next_run_at': self.next_run_at.isoformat() if self.next_run_at else None,
        }
    
    def get_next_listing(self):
        """Get the next listing in the sequence"""
        listings = self.listings.order_by(LoopListing.order_index).all()
        if not listings:
            return None
        
        # Wrap around if at end
        index = self.current_index % len(listings)
        return listings[index]
    
    def advance_index(self):
        """Move to next listing in sequence"""
        count = self.listings.count()
        if count > 0:
            self.current_index = (self.current_index + 1) % count
        db.session.commit()


class LoopListing(db.Model):
    """A listing assigned to a loop"""
    __tablename__ = 'loop_listings'
    __table_args__ = (
        db.Index('idx_loop_listings_loop_config_id', 'loop_config_id'),
        db.Index('idx_loop_listings_listing_id', 'listing_id'),
        db.Index('idx_loop_listings_order_index', 'order_index'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    loop_config_id = db.Column(db.Integer, db.ForeignKey('loop_configs.id'), nullable=False)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    
    # Order in the sequence (for multi-listing loops)
    order_index = db.Column(db.Integer, default=0)
    
    # Tracking
    last_processed_at = db.Column(db.DateTime, nullable=True)
    times_processed = db.Column(db.Integer, default=0)
    consecutive_failures = db.Column(db.Integer, default=0)
    
    # Relationship to the actual listing
    listing = db.relationship('LocalListing', backref='loop_assignments')
    
    def to_dict(self):
        return {
            'id': self.id,
            'loop_config_id': self.loop_config_id,
            'listing_id': self.listing_id,
            'order_index': self.order_index,
            'last_processed_at': self.last_processed_at.isoformat() if self.last_processed_at else None,
            'times_processed': self.times_processed,
            'listing': {
                'id': self.listing.id,
                'reference': self.listing.reference,
                'title': self.listing.title_en,
                'status': self.listing.status,
            } if self.listing else None
        }


class DuplicatedListing(db.Model):
    """Track duplicated listings created by loops"""
    __tablename__ = 'duplicated_listings'
    __table_args__ = (
        db.Index('idx_duplicated_original', 'original_listing_id'),
        db.Index('idx_duplicated_duplicate', 'duplicate_listing_id'),
        db.Index('idx_duplicated_loop', 'loop_config_id'),
        db.Index('idx_duplicated_status', 'status'),
        db.Index('idx_duplicated_pf_id', 'pf_listing_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Reference to original listing
    original_listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    
    # The duplicate listing created (stored in our DB)
    duplicate_listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=True)
    
    # PropertyFinder listing ID for the duplicate
    pf_listing_id = db.Column(db.String(100), nullable=True)
    
    # Which loop created this
    loop_config_id = db.Column(db.Integer, db.ForeignKey('loop_configs.id'), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    published_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)  # When deleted from PF
    
    # Status
    status = db.Column(db.String(20), default='created')  # created, published, deleted
    
    # Relationships
    original_listing = db.relationship('LocalListing', foreign_keys=[original_listing_id], backref='duplicates_created')
    duplicate_listing = db.relationship('LocalListing', foreign_keys=[duplicate_listing_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'original_listing_id': self.original_listing_id,
            'duplicate_listing_id': self.duplicate_listing_id,
            'pf_listing_id': self.pf_listing_id,
            'loop_config_id': self.loop_config_id,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'original': {
                'reference': self.original_listing.reference,
                'title': self.original_listing.title_en,
            } if self.original_listing else None
        }


class LoopExecutionLog(db.Model):
    """Log of loop executions for debugging and monitoring"""
    __tablename__ = 'loop_execution_logs'
    __table_args__ = (
        db.Index('idx_loop_exec_loop_config', 'loop_config_id'),
        db.Index('idx_loop_exec_listing', 'listing_id'),
        db.Index('idx_loop_exec_action', 'action'),
        db.Index('idx_loop_exec_success', 'success'),
        db.Index('idx_loop_exec_executed_at', 'executed_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    loop_config_id = db.Column(db.Integer, db.ForeignKey('loop_configs.id'), nullable=False)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=True)
    
    # Execution details
    action = db.Column(db.String(50))  # 'duplicate', 'delete_republish', 'cleanup', 'error'
    success = db.Column(db.Boolean, default=False)
    message = db.Column(db.Text, nullable=True)
    pf_listing_id = db.Column(db.String(100), nullable=True)
    
    # Timestamps
    executed_at = db.Column(db.DateTime, default=datetime.utcnow)
    duration_ms = db.Column(db.Integer, nullable=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'loop_config_id': self.loop_config_id,
            'listing_id': self.listing_id,
            'action': self.action,
            'success': self.success,
            'message': self.message,
            'pf_listing_id': self.pf_listing_id,
            'executed_at': self.executed_at.isoformat() if self.executed_at else None,
            'duration_ms': self.duration_ms
        }


# ===== TASK MANAGEMENT (Trello-like) =====

# Board member permissions
BOARD_PERMISSIONS = {
    'owner': {
        'name': 'Owner',
        'can_view': True,
        'can_edit': True,
        'can_create_tasks': True,
        'can_delete_tasks': True,
        'can_manage_members': True,
        'can_edit_board': True,
        'can_delete_board': True
    },
    'admin': {
        'name': 'Admin',
        'can_view': True,
        'can_edit': True,
        'can_create_tasks': True,
        'can_delete_tasks': True,
        'can_manage_members': True,
        'can_edit_board': True,
        'can_delete_board': False
    },
    'editor': {
        'name': 'Editor',
        'can_view': True,
        'can_edit': True,
        'can_create_tasks': True,
        'can_delete_tasks': False,
        'can_manage_members': False,
        'can_edit_board': False,
        'can_delete_board': False
    },
    'member': {
        'name': 'Member',
        'can_view': True,
        'can_edit': True,  # Can edit tasks assigned to them
        'can_create_tasks': True,
        'can_delete_tasks': False,
        'can_manage_members': False,
        'can_edit_board': False,
        'can_delete_board': False
    },
    'viewer': {
        'name': 'Viewer',
        'can_view': True,
        'can_edit': False,
        'can_create_tasks': False,
        'can_delete_tasks': False,
        'can_manage_members': False,
        'can_edit_board': False,
        'can_delete_board': False
    }
}


class BoardMember(db.Model):
    """Board membership with role-based permissions"""
    __tablename__ = 'board_members'
    __table_args__ = (
        db.UniqueConstraint('board_id', 'user_id', name='unique_board_member'),
        db.Index('idx_board_members_board_id', 'board_id'),
        db.Index('idx_board_members_user_id', 'user_id'),
        db.Index('idx_board_members_role', 'role'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('task_boards.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(20), default='member')  # owner, admin, editor, member, viewer
    
    # Notification preferences
    notify_on_assign = db.Column(db.Boolean, default=True)
    notify_on_comment = db.Column(db.Boolean, default=True)
    notify_on_due = db.Column(db.Boolean, default=True)
    
    # Timestamps
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    invited_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref='board_memberships')
    invited_by = db.relationship('User', foreign_keys=[invited_by_id])
    
    def has_permission(self, permission):
        """Check if member has a specific permission"""
        role_perms = BOARD_PERMISSIONS.get(self.role, {})
        return role_perms.get(permission, False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'board_id': self.board_id,
            'user_id': self.user_id,
            'user': self.user.to_dict() if self.user else None,
            'role': self.role,
            'role_name': BOARD_PERMISSIONS.get(self.role, {}).get('name', 'Unknown'),
            'permissions': BOARD_PERMISSIONS.get(self.role, {}),
            'notify_on_assign': self.notify_on_assign,
            'notify_on_comment': self.notify_on_comment,
            'notify_on_due': self.notify_on_due,
            'joined_at': self.joined_at.isoformat() if self.joined_at else None,
            'invited_by_id': self.invited_by_id
        }


# Association table for Task <-> User (multiple assignees)
task_assignee_association = db.Table('task_assignees',
    db.Column('task_id', db.Integer, db.ForeignKey('tasks.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('assigned_at', db.DateTime, default=datetime.utcnow),
    db.Column('assigned_by_id', db.Integer, db.ForeignKey('users.id'), nullable=True)
)


class TaskBoard(db.Model):
    """Task boards for organizing tasks (like Trello boards)"""
    __tablename__ = 'task_boards'
    __table_args__ = (
        db.Index('idx_task_boards_workspace_id', 'workspace_id'),
        db.Index('idx_task_boards_created_by_id', 'created_by_id'),
        db.Index('idx_task_boards_is_archived', 'is_archived'),
        db.Index('idx_task_boards_is_private', 'is_private'),
        db.Index('idx_task_boards_created_at', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspaces.id'), nullable=True, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(20), default='#3b82f6')  # Board color
    icon = db.Column(db.String(50), default='clipboard')  # Icon name
    
    # Columns configuration stored as JSON
    # Format: [{"id": "uuid", "name": "To Do", "color": "#gray"}, ...]
    columns_config = db.Column(db.Text, default='[]')
    
    # Board settings
    is_archived = db.Column(db.Boolean, default=False)
    is_favorite = db.Column(db.Boolean, default=False)
    is_private = db.Column(db.Boolean, default=True)  # If false, all users can view
    
    # Ownership
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    tasks = db.relationship('Task', backref='board', lazy='dynamic', cascade='all, delete-orphan')
    members = db.relationship('BoardMember', backref='board', lazy='dynamic', cascade='all, delete-orphan')
    creator = db.relationship('User', foreign_keys=[created_by_id], backref='created_boards')
    
    def get_columns(self):
        """Parse columns_config JSON"""
        import json
        try:
            return json.loads(self.columns_config or '[]')
        except:
            return []
    
    def set_columns(self, columns):
        """Set columns_config from list"""
        import json
        self.columns_config = json.dumps(columns)
    
    def get_member(self, user_id):
        """Get membership for a user"""
        return self.members.filter_by(user_id=user_id).first()
    
    def is_member(self, user_id):
        """Check if user is a member of this board"""
        return self.get_member(user_id) is not None
    
    def get_user_role(self, user_id):
        """Get user's role on this board"""
        if self.created_by_id == user_id:
            return 'owner'
        member = self.get_member(user_id)
        return member.role if member else None
    
    def user_can(self, user_id, permission):
        """Check if user has specific permission on this board"""
        # Creator always has full access
        if self.created_by_id == user_id:
            return True
        # Check membership
        member = self.get_member(user_id)
        if member:
            return member.has_permission(permission)
        # Public boards allow viewing
        if not self.is_private and permission == 'can_view':
            return True
        return False
    
    def get_all_members_with_creator(self):
        """Get all members including the creator"""
        members_list = []
        # Add creator as owner
        if self.creator:
            members_list.append({
                'user_id': self.created_by_id,
                'user': self.creator.to_dict(),
                'role': 'owner',
                'role_name': 'Owner',
                'is_creator': True
            })
        # Add other members
        for member in self.members.all():
            if member.user_id != self.created_by_id:
                member_dict = member.to_dict()
                member_dict['is_creator'] = False
                members_list.append(member_dict)
        return members_list
    
    def to_dict(self, include_tasks=False, include_members=False):
        data = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'color': self.color,
            'icon': self.icon,
            'columns': self.get_columns(),
            'is_archived': self.is_archived,
            'is_favorite': self.is_favorite,
            'is_private': getattr(self, 'is_private', True),
            'created_by_id': self.created_by_id,
            'created_by': self.creator.to_dict() if self.creator else None,
            'creator': self.creator.to_dict() if self.creator else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'task_count': self.tasks.count() if self.tasks else 0,
            'member_count': self.members.count() + 1 if self.members else 1  # +1 for creator
        }
        if include_tasks:
            data['tasks'] = [t.to_dict() for t in self.tasks.all()]
        if include_members:
            data['members'] = self.get_all_members_with_creator()
        return data


class TaskLabel(db.Model):
    """Labels for tasks (like Trello labels)"""
    __tablename__ = 'task_labels'
    __table_args__ = (
        db.Index('idx_task_labels_board_id', 'board_id'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='#6b7280')  # Tailwind gray-500
    board_id = db.Column(db.Integer, db.ForeignKey('task_boards.id'), nullable=True)  # Board-specific or global
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'color': self.color,
            'board_id': self.board_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# Association table for Task <-> TaskLabel many-to-many
task_label_association = db.Table('task_label_association',
    db.Column('task_id', db.Integer, db.ForeignKey('tasks.id'), primary_key=True),
    db.Column('label_id', db.Integer, db.ForeignKey('task_labels.id'), primary_key=True)
)


class Task(db.Model):
    """Individual tasks within a board"""
    __tablename__ = 'tasks'
    __table_args__ = (
        db.Index('idx_tasks_board_id', 'board_id'),
        db.Index('idx_tasks_column_id', 'column_id'),
        db.Index('idx_tasks_assignee_id', 'assignee_id'),
        db.Index('idx_tasks_created_by_id', 'created_by_id'),
        db.Index('idx_tasks_priority', 'priority'),
        db.Index('idx_tasks_due_date', 'due_date'),
        db.Index('idx_tasks_is_completed', 'is_completed'),
        db.Index('idx_tasks_position', 'position'),
        db.Index('idx_tasks_created_at', 'created_at'),
        db.Index('idx_tasks_board_column', 'board_id', 'column_id'),
        db.Index('idx_tasks_board_position', 'board_id', 'position'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=True)
    
    # Board and column
    board_id = db.Column(db.Integer, db.ForeignKey('task_boards.id'), nullable=False)
    column_id = db.Column(db.String(100), nullable=False)  # References columns_config id
    position = db.Column(db.Integer, default=0)  # Order within column
    
    # Task details
    priority = db.Column(db.String(20), default='medium')  # low, medium, high, urgent
    due_date = db.Column(db.DateTime, nullable=True)
    start_date = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    is_completed = db.Column(db.Boolean, default=False)
    
    # Assignment - keep single assignee for backward compatibility, but also support multiple
    assignee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Additional data
    cover_color = db.Column(db.String(20), nullable=True)  # Card cover color
    cover_image = db.Column(db.String(500), nullable=True)  # Card cover image URL
    checklist = db.Column(db.Text, default='[]')  # JSON: [{"id": "", "text": "", "checked": false}]
    attachments = db.Column(db.Text, default='[]')  # JSON: [{"id": "", "name": "", "url": "", "type": ""}]
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    labels = db.relationship('TaskLabel', secondary=task_label_association, backref='tasks')
    comments = db.relationship('TaskComment', backref='task', lazy='dynamic', cascade='all, delete-orphan')
    assignees = db.relationship(
        'User', 
        secondary='task_assignees',
        primaryjoin='Task.id == foreign(task_assignees.c.task_id)',
        secondaryjoin='User.id == foreign(task_assignees.c.user_id)',
        backref='assigned_tasks',
        lazy='dynamic'
    )
    assignee = db.relationship('User', foreign_keys=[assignee_id], backref='primary_tasks')
    creator = db.relationship('User', foreign_keys=[created_by_id], backref='created_tasks')
    
    def get_checklist(self):
        import json
        try:
            return json.loads(self.checklist or '[]')
        except:
            return []
    
    def set_checklist(self, items):
        import json
        self.checklist = json.dumps(items)
    
    def get_attachments(self):
        import json
        try:
            return json.loads(self.attachments or '[]')
        except:
            return []
    
    def set_attachments(self, items):
        import json
        self.attachments = json.dumps(items)
    
    def get_all_assignees(self):
        """Get all assignees including primary assignee"""
        assignee_list = []
        if self.assignee:
            assignee_list.append(self.assignee.to_dict())
        for user in self.assignees:
            if user.id != self.assignee_id:
                assignee_list.append(user.to_dict())
        return assignee_list
    
    def is_assigned_to(self, user_id):
        """Check if task is assigned to a specific user"""
        if self.assignee_id == user_id:
            return True
        return any(u.id == user_id for u in self.assignees)
    
    def to_dict(self):
        # Get all assignee IDs
        assignee_ids = []
        if self.assignee_id:
            assignee_ids.append(self.assignee_id)
        for user in self.assignees:
            if user.id not in assignee_ids:
                assignee_ids.append(user.id)
        
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'board_id': self.board_id,
            'column_id': self.column_id,
            'position': self.position,
            'priority': self.priority,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'is_completed': self.is_completed,
            'assignee_id': self.assignee_id,
            'assignee': self.assignee.to_dict() if self.assignee else None,
            'assignee_ids': assignee_ids,
            'assignees': self.get_all_assignees(),
            'created_by_id': self.created_by_id,
            'creator': self.creator.to_dict() if self.creator else None,
            'cover_color': self.cover_color,
            'cover_image': self.cover_image,
            'checklist': self.get_checklist(),
            'attachments': self.get_attachments(),
            'labels': [l.to_dict() for l in self.labels],
            'comment_count': self.comments.count() if self.comments else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class TaskComment(db.Model):
    """Comments on tasks"""
    __tablename__ = 'task_comments'
    __table_args__ = (
        db.Index('idx_task_comments_task_id', 'task_id'),
        db.Index('idx_task_comments_user_id', 'user_id'),
        db.Index('idx_task_comments_created_at', 'created_at'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'user_id': self.user_id,
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
