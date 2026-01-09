"""
Permission Service - Bitrix24-style layered authorization system

This module implements a multi-level permission system:
1. Portal/Tenant Level - System roles (SYSTEM_ADMIN, GLOBAL_WORKSPACE_MANAGER, USER)
2. Workspace Level - Workspace roles with permission buckets
3. Module Level - RBAC per module with scope control
4. Object Level - Per-object ACL with inheritance

Authorization evaluation algorithm:
1. Check if user is SYSTEM_ADMIN → allow all
2. Check system-level capabilities for the action
3. Check workspace membership and role
4. Evaluate permission buckets for workspace-level actions
5. Check module permissions for module-specific actions
6. Check object ACL for object-specific access
7. Merge permissions according to strategy (union/most_permissive)
"""

import json
from datetime import datetime
from functools import wraps
from flask import g, request, abort
from typing import Optional, Dict, List, Any, Union


class PermissionService:
    """
    Centralized permission checking service.
    Implements Bitrix24-style layered authorization.
    """
    
    def __init__(self, db_session=None):
        self.db = db_session
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes
    
    # ==================== SYSTEM LEVEL ====================
    
    def is_system_admin(self, user) -> bool:
        """Check if user has SYSTEM_ADMIN role"""
        if not user:
            return False
        # Legacy compatibility: admin role = system admin
        if getattr(user, 'role', None) == 'admin':
            return True
        # Check new system roles
        return self._has_system_role(user, 'SYSTEM_ADMIN')
    
    def is_global_workspace_manager(self, user) -> bool:
        """Check if user has GLOBAL_WORKSPACE_MANAGER role"""
        if not user:
            return False
        if self.is_system_admin(user):
            return True
        return self._has_system_role(user, 'GLOBAL_WORKSPACE_MANAGER')
    
    def _has_system_role(self, user, role_code: str) -> bool:
        """Check if user has a specific system role"""
        from src.database.models import UserSystemRole, SystemRole
        
        cache_key = f"sys_role:{user.id}:{role_code}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if datetime.utcnow().timestamp() - cached['time'] < self._cache_ttl:
                return cached['value']
        
        result = UserSystemRole.query.join(SystemRole).filter(
            UserSystemRole.user_id == user.id,
            SystemRole.code == role_code
        ).first() is not None
        
        self._cache[cache_key] = {'value': result, 'time': datetime.utcnow().timestamp()}
        return result
    
    def get_user_system_capabilities(self, user) -> Dict[str, bool]:
        """Get all system-level capabilities for a user"""
        from src.database.models import UserSystemRole, SystemRole
        
        if not user:
            return {}
        
        # Legacy admin has all capabilities
        if getattr(user, 'role', None) == 'admin':
            return SystemRole.DEFAULT_ROLES.get('SYSTEM_ADMIN', {}).get('capabilities', {})
        
        capabilities = {}
        assignments = UserSystemRole.query.filter_by(user_id=user.id).all()
        
        for assignment in assignments:
            if assignment.system_role:
                role_caps = assignment.system_role.get_capabilities()
                for cap, value in role_caps.items():
                    if value:
                        capabilities[cap] = True
        
        return capabilities
    
    def has_system_capability(self, user, capability: str) -> bool:
        """Check if user has a specific system capability"""
        caps = self.get_user_system_capabilities(user)
        return caps.get(capability, False)
    
    # ==================== WORKSPACE LEVEL ====================
    
    def get_workspace_role(self, user, workspace_id: int) -> Optional[str]:
        """Get user's role in a workspace"""
        from src.database.models import WorkspaceMember
        
        if not user:
            return None
        
        member = WorkspaceMember.query.filter_by(
            user_id=user.id,
            workspace_id=workspace_id
        ).first()
        
        return member.role if member else None
    
    def is_workspace_admin(self, user, workspace_id: int) -> bool:
        """Check if user is admin/owner of workspace"""
        if self.is_system_admin(user):
            return True
        
        role = self.get_workspace_role(user, workspace_id)
        return role in ('owner', 'admin', 'WORKSPACE_ADMIN')
    
    def is_workspace_moderator(self, user, workspace_id: int) -> bool:
        """Check if user is moderator or higher in workspace"""
        if self.is_workspace_admin(user, workspace_id):
            return True
        
        role = self.get_workspace_role(user, workspace_id)
        return role in ('moderator', 'MODERATOR')
    
    def is_workspace_member(self, user, workspace_id: int) -> bool:
        """Check if user is a member of workspace (any role)"""
        if self.is_system_admin(user):
            return True
        
        return self.get_workspace_role(user, workspace_id) is not None
    
    def get_workspace_permission_bucket(self, user, workspace_id: int, action: str) -> str:
        """Get permission bucket level for an action in workspace"""
        from src.database.models import WorkspaceRole, WorkspaceMember
        
        if self.is_system_admin(user):
            return WorkspaceRole.BUCKET_ADMIN_ONLY  # Effectively has all access
        
        member = WorkspaceMember.query.filter_by(
            user_id=user.id,
            workspace_id=workspace_id
        ).first()
        
        if not member:
            return WorkspaceRole.BUCKET_DENY
        
        # Get workspace role configuration
        ws_role = WorkspaceRole.query.filter(
            ((WorkspaceRole.workspace_id == workspace_id) | (WorkspaceRole.workspace_id.is_(None))),
            WorkspaceRole.code == member.role.upper()
        ).order_by(WorkspaceRole.workspace_id.desc().nullslast()).first()
        
        if ws_role:
            buckets = ws_role.get_permission_buckets()
            return buckets.get(action, WorkspaceRole.BUCKET_DENY)
        
        # Fallback to legacy role mapping
        role_map = {
            'owner': WorkspaceRole.BUCKET_ADMIN_ONLY,
            'admin': WorkspaceRole.BUCKET_ADMIN_ONLY,
            'moderator': WorkspaceRole.BUCKET_ADMIN_MODERATOR,
            'member': WorkspaceRole.BUCKET_ALL_MEMBERS,
            'viewer': WorkspaceRole.BUCKET_AUTHORIZED,
            'external': WorkspaceRole.BUCKET_EXTERNAL,
        }
        return role_map.get(member.role, WorkspaceRole.BUCKET_DENY)
    
    def check_workspace_action(self, user, workspace_id: int, action: str) -> bool:
        """Check if user can perform action in workspace based on permission buckets"""
        from src.database.models import WorkspaceRole
        
        if self.is_system_admin(user):
            return True
        
        bucket = self.get_workspace_permission_bucket(user, workspace_id, action)
        role = self.get_workspace_role(user, workspace_id)
        
        if bucket == WorkspaceRole.BUCKET_DENY:
            return False
        elif bucket == WorkspaceRole.BUCKET_ADMIN_ONLY:
            return role in ('owner', 'admin', 'WORKSPACE_ADMIN')
        elif bucket == WorkspaceRole.BUCKET_ADMIN_MODERATOR:
            return role in ('owner', 'admin', 'moderator', 'WORKSPACE_ADMIN', 'MODERATOR')
        elif bucket == WorkspaceRole.BUCKET_ALL_MEMBERS:
            return role in ('owner', 'admin', 'moderator', 'member', 'WORKSPACE_ADMIN', 'MODERATOR', 'MEMBER')
        elif bucket == WorkspaceRole.BUCKET_AUTHORIZED:
            return role is not None
        elif bucket == WorkspaceRole.BUCKET_EXTERNAL:
            return True  # Even external can access
        
        return False
    
    # ==================== MODULE LEVEL ====================
    
    def get_module_capabilities(self, user, workspace_id: int, module: str) -> Dict[str, Any]:
        """Get user's capabilities for a specific module in a workspace"""
        from src.database.models import ModulePermission, WorkspaceRole, WorkspaceMember
        
        if self.is_system_admin(user):
            # System admin has all capabilities
            return {
                'read': True,
                'create': True,
                'edit': True,
                'delete': True,
                'publish': True,
                'assign': True,
                'bulk': True,
                'scope': 'workspace'
            }
        
        # Get workspace role
        member = WorkspaceMember.query.filter_by(
            user_id=user.id,
            workspace_id=workspace_id
        ).first()
        
        if not member:
            return {}
        
        # Get workspace role definition
        ws_role = WorkspaceRole.query.filter(
            ((WorkspaceRole.workspace_id == workspace_id) | (WorkspaceRole.workspace_id.is_(None))),
            WorkspaceRole.code == member.role.upper()
        ).order_by(WorkspaceRole.workspace_id.desc().nullslast()).first()
        
        if not ws_role:
            # Fallback to section-based permissions (legacy)
            return self._get_legacy_module_permissions(user, module)
        
        # Get module permissions for this role
        mod_perm = ModulePermission.query.filter_by(
            workspace_role_id=ws_role.id,
            module=module
        ).first()
        
        if mod_perm:
            return mod_perm.get_capabilities()
        
        # Fallback to default based on role
        return self._get_default_module_capabilities(member.role, module)
    
    def _get_legacy_module_permissions(self, user, module: str) -> Dict[str, Any]:
        """Get module permissions from legacy section_permissions"""
        if not user:
            return {}
        
        section_perms = user.get_section_permissions()
        module_perms = section_perms.get(module, {})
        
        return {
            'read': module_perms.get('view', False),
            'create': module_perms.get('create', False),
            'edit': module_perms.get('edit', False),
            'delete': module_perms.get('delete', False),
            'publish': module_perms.get('publish', False),
            'assign': module_perms.get('assign', False),
            'bulk': module_perms.get('bulk_upload', False),
            'scope': 'own'  # Legacy: own scope only unless admin
        }
    
    def _get_default_module_capabilities(self, role: str, module: str) -> Dict[str, Any]:
        """Get default module capabilities based on workspace role"""
        role_lower = role.lower()
        
        if role_lower in ('owner', 'admin'):
            return {
                'read': True, 'create': True, 'edit': True, 
                'delete': True, 'publish': True, 'assign': True, 
                'bulk': True, 'scope': 'workspace'
            }
        elif role_lower == 'moderator':
            return {
                'read': True, 'create': True, 'edit': True, 
                'delete': True, 'publish': True, 'assign': False, 
                'bulk': True, 'scope': 'workspace'
            }
        elif role_lower == 'member':
            return {
                'read': True, 'create': True, 'edit': True, 
                'delete': False, 'publish': False, 'assign': False, 
                'bulk': False, 'scope': 'own'
            }
        elif role_lower in ('viewer', 'external'):
            return {
                'read': True, 'create': False, 'edit': False, 
                'delete': False, 'publish': False, 'assign': False, 
                'bulk': False, 'scope': 'workspace'
            }
        
        return {}
    
    def check_module_access(self, user, workspace_id: int, module: str, action: str) -> bool:
        """Check if user can perform action in module"""
        caps = self.get_module_capabilities(user, workspace_id, module)
        return caps.get(action, False) == True
    
    def check_module_scope(self, user, workspace_id: int, module: str, object_owner_id: int) -> bool:
        """Check if user's scope allows access to object owned by object_owner_id"""
        from src.database.models import ModulePermission
        
        if self.is_system_admin(user):
            return True
        
        caps = self.get_module_capabilities(user, workspace_id, module)
        scope = caps.get('scope', ModulePermission.SCOPE_OWN)
        
        if scope == 'workspace' or scope == True:
            return True
        elif scope == 'own':
            return user.id == object_owner_id
        elif scope == 'team':
            # TODO: Implement team scope check
            return user.id == object_owner_id
        
        return False
    
    # ==================== OBJECT LEVEL ====================
    
    def get_object_permissions(self, user, object_type: str, object_id: int) -> Dict[str, bool]:
        """Get user's permissions for a specific object"""
        from src.database.models import ObjectACL, WorkspaceMember
        
        if self.is_system_admin(user):
            return {p: True for p in ObjectACL.ALL_PERMISSIONS}
        
        if not user:
            return {}
        
        # Check direct user ACL
        acls = ObjectACL.query.filter(
            ObjectACL.object_type == object_type,
            ObjectACL.object_id == object_id,
            ((ObjectACL.principal_type == 'user') & (ObjectACL.principal_id == user.id)) |
            (ObjectACL.principal_type == 'workspace_role')
        ).all()
        
        permissions = {}
        
        for acl in acls:
            if acl.principal_type == 'user' and acl.principal_id == user.id:
                # Direct user permission
                for perm, value in acl.get_permissions().items():
                    if value:
                        permissions[perm] = True
            elif acl.principal_type == 'workspace_role':
                # Check if user has this workspace role
                # Get workspace from object (would need object lookup)
                pass
        
        return permissions
    
    def check_object_access(self, user, object_type: str, object_id: int, permission: str) -> bool:
        """Check if user has specific permission on object"""
        perms = self.get_object_permissions(user, object_type, object_id)
        return perms.get(permission, False)
    
    # ==================== MAIN CHECK ACCESS ====================
    
    def check_access(
        self, 
        user, 
        action: str, 
        resource_type: str = None,
        resource_id: int = None,
        workspace_id: int = None,
        module: str = None,
        audit: bool = True
    ) -> bool:
        """
        Main authorization check - evaluates all permission layers.
        
        Algorithm:
        1. Check system admin → allow all
        2. Check system capabilities
        3. Check workspace membership
        4. Check workspace permission buckets
        5. Check module permissions
        6. Check object ACL
        
        Args:
            user: User object
            action: Action to check (read, create, edit, delete, etc.)
            resource_type: Type of resource (workspace, listing, lead, etc.)
            resource_id: ID of specific resource
            workspace_id: Workspace context
            module: Module context (listings, leads, etc.)
            audit: Whether to log this check
        
        Returns:
            bool: Whether access is allowed
        """
        from src.database.models import FeatureFlag
        
        # Check if permission enforcement is enabled
        if not self._is_enforcement_enabled(workspace_id):
            # Audit mode or disabled - allow but log
            if audit and self._is_audit_mode_enabled(workspace_id):
                self._log_audit(user, action, resource_type, resource_id, workspace_id, 'audit_only')
            return True
        
        result = False
        
        try:
            # 1. System admin bypass
            if self.is_system_admin(user):
                result = True
                return result
            
            # 2. System capabilities
            if resource_type == 'system' or action.startswith('manage_'):
                if self.has_system_capability(user, action):
                    result = True
                    return result
            
            # 3. Workspace membership check
            if workspace_id:
                if not self.is_workspace_member(user, workspace_id):
                    # Check if global workspace manager
                    if self.is_global_workspace_manager(user):
                        # Can view workspace but not private content
                        if action in ('view_workspace', 'manage_workspace', 'assign_admin'):
                            result = True
                            return result
                    result = False
                    return result
                
                # 4. Workspace permission buckets
                workspace_action = self._map_to_workspace_action(action)
                if workspace_action:
                    if not self.check_workspace_action(user, workspace_id, workspace_action):
                        result = False
                        return result
            
            # 5. Module permissions
            if module:
                if not self.check_module_access(user, workspace_id, module, action):
                    result = False
                    return result
            
            # 6. Object ACL
            if resource_type and resource_id:
                if not self.check_object_access(user, resource_type, resource_id, action):
                    # Check module-level fallback
                    if module:
                        result = self.check_module_access(user, workspace_id, module, action)
                    else:
                        result = False
                    return result
            
            # Default: allow for members with module access
            result = True
            return result
            
        finally:
            if audit:
                self._log_audit(
                    user, action, resource_type, resource_id, workspace_id,
                    'allowed' if result else 'denied'
                )
    
    def _map_to_workspace_action(self, action: str) -> Optional[str]:
        """Map action to workspace permission bucket"""
        mapping = {
            'create': 'create_data',
            'edit': 'edit_data',
            'delete': 'delete_data',
            'view': 'view_data',
            'read': 'view_data',
            'manage_members': 'manage_members',
            'manage_roles': 'manage_roles',
            'manage_connections': 'manage_connections',
            'manage_settings': 'manage_settings',
        }
        return mapping.get(action)
    
    def _is_enforcement_enabled(self, workspace_id: int = None) -> bool:
        """Check if permission enforcement is enabled"""
        from src.database.models import FeatureFlag
        
        # Check workspace-specific flag first
        if workspace_id:
            flag = FeatureFlag.query.filter_by(
                code=FeatureFlag.PERMISSION_ENFORCEMENT,
                scope='workspace',
                scope_id=workspace_id
            ).first()
            if flag:
                return flag.is_enabled
        
        # Check global flag
        flag = FeatureFlag.query.filter_by(
            code=FeatureFlag.PERMISSION_ENFORCEMENT,
            scope='global'
        ).first()
        
        # Default: disabled (backward compatible)
        return flag.is_enabled if flag else False
    
    def _is_audit_mode_enabled(self, workspace_id: int = None) -> bool:
        """Check if audit mode is enabled (log without blocking)"""
        from src.database.models import FeatureFlag
        
        if workspace_id:
            flag = FeatureFlag.query.filter_by(
                code=FeatureFlag.AUDIT_MODE,
                scope='workspace',
                scope_id=workspace_id
            ).first()
            if flag:
                return flag.is_enabled
        
        flag = FeatureFlag.query.filter_by(
            code=FeatureFlag.AUDIT_MODE,
            scope='global'
        ).first()
        
        return flag.is_enabled if flag else False
    
    def _log_audit(
        self, 
        user, 
        action: str, 
        resource_type: str, 
        resource_id: int, 
        workspace_id: int,
        result: str
    ):
        """Log permission check to audit log"""
        from src.database.models import AuditLog, db
        
        try:
            log = AuditLog(
                user_id=user.id if user else None,
                user_email=user.email if user else None,
                action=AuditLog.ACTION_PERMISSION_CHECK if result == 'allowed' else AuditLog.ACTION_PERMISSION_DENIED,
                action_result=result,
                resource_type=resource_type,
                resource_id=resource_id,
                workspace_id=workspace_id,
                ip_address=request.remote_addr if request else None,
                user_agent=request.user_agent.string if request and request.user_agent else None
            )
            log.set_details({'action_requested': action})
            db.session.add(log)
            db.session.commit()
        except Exception:
            # Don't fail on audit log errors
            pass
    
    # ==================== LIST EFFECTIVE PERMISSIONS ====================
    
    def list_effective_permissions(
        self, 
        user, 
        workspace_id: int = None,
        module: str = None,
        resource_type: str = None,
        resource_id: int = None
    ) -> Dict[str, Any]:
        """
        List all effective permissions for a user.
        
        Returns:
            {
                'system_role': 'SYSTEM_ADMIN' | 'GLOBAL_WORKSPACE_MANAGER' | 'USER',
                'system_capabilities': {...},
                'workspace_role': 'WORKSPACE_ADMIN' | 'MODERATOR' | ...,
                'workspace_permissions': {...},
                'module_capabilities': {...},
                'object_permissions': {...},
                'effective': {...}  # Merged final permissions
            }
        """
        from src.database.models import UserSystemRole, SystemRole
        
        result = {
            'system_role': None,
            'system_capabilities': {},
            'workspace_role': None,
            'workspace_permissions': {},
            'module_capabilities': {},
            'object_permissions': {},
            'effective': {}
        }
        
        if not user:
            return result
        
        # System role
        if self.is_system_admin(user):
            result['system_role'] = 'SYSTEM_ADMIN'
        elif self.is_global_workspace_manager(user):
            result['system_role'] = 'GLOBAL_WORKSPACE_MANAGER'
        else:
            result['system_role'] = 'USER'
        
        result['system_capabilities'] = self.get_user_system_capabilities(user)
        
        # Workspace permissions
        if workspace_id:
            result['workspace_role'] = self.get_workspace_role(user, workspace_id)
            
            # Get all permission buckets
            from src.database.models import WorkspaceRole
            for action in ['manage_members', 'manage_roles', 'manage_connections', 
                          'manage_settings', 'view_data', 'create_data', 'edit_data', 'delete_data']:
                bucket = self.get_workspace_permission_bucket(user, workspace_id, action)
                result['workspace_permissions'][action] = {
                    'bucket': bucket,
                    'allowed': self.check_workspace_action(user, workspace_id, action)
                }
            
            # Module capabilities
            if module:
                result['module_capabilities'] = self.get_module_capabilities(user, workspace_id, module)
        
        # Object permissions
        if resource_type and resource_id:
            result['object_permissions'] = self.get_object_permissions(user, resource_type, resource_id)
        
        # Compute effective permissions
        result['effective'] = self._compute_effective_permissions(result)
        
        return result
    
    def _compute_effective_permissions(self, perm_data: Dict) -> Dict[str, bool]:
        """Merge all permission layers into effective permissions"""
        effective = {}
        
        # Start with system capabilities
        if perm_data.get('system_role') == 'SYSTEM_ADMIN':
            return {
                'full_access': True,
                'read': True, 'create': True, 'edit': True, 'delete': True,
                'publish': True, 'assign': True, 'manage': True
            }
        
        # Add workspace permissions
        for action, info in perm_data.get('workspace_permissions', {}).items():
            effective[action] = info.get('allowed', False)
        
        # Add module capabilities
        for cap, value in perm_data.get('module_capabilities', {}).items():
            if value:
                effective[cap] = True
        
        # Add object permissions (override)
        for perm, value in perm_data.get('object_permissions', {}).items():
            effective[perm] = value
        
        return effective
    
    # ==================== CACHE MANAGEMENT ====================
    
    def clear_cache(self, user_id: int = None):
        """Clear permission cache"""
        if user_id:
            keys_to_remove = [k for k in self._cache.keys() if f":{user_id}:" in k]
            for key in keys_to_remove:
                del self._cache[key]
        else:
            self._cache.clear()


# ==================== SINGLETON INSTANCE ====================

_permission_service = None

def get_permission_service() -> PermissionService:
    """Get the singleton permission service instance"""
    global _permission_service
    if _permission_service is None:
        _permission_service = PermissionService()
    return _permission_service


# ==================== HELPER FUNCTIONS ====================

def check_access(
    user,
    action: str,
    resource_type: str = None,
    resource_id: int = None,
    workspace_id: int = None,
    module: str = None
) -> bool:
    """Convenience function for permission checking"""
    return get_permission_service().check_access(
        user, action, resource_type, resource_id, workspace_id, module
    )


def list_effective_permissions(
    user,
    workspace_id: int = None,
    module: str = None,
    resource_type: str = None,
    resource_id: int = None
) -> Dict[str, Any]:
    """Convenience function for listing permissions"""
    return get_permission_service().list_effective_permissions(
        user, workspace_id, module, resource_type, resource_id
    )


# ==================== DECORATORS ====================

def require_permission(action: str, module: str = None, get_workspace_id=None, get_resource=None):
    """
    Decorator to require permission for a route.
    
    Usage:
        @require_permission('edit', module='listings')
        def edit_listing(listing_id):
            ...
        
        @require_permission('manage_members', get_workspace_id=lambda: request.view_args.get('workspace_id'))
        def manage_workspace(workspace_id):
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from flask import g, abort, request
            
            user = getattr(g, 'user', None)
            workspace_id = None
            resource_type = None
            resource_id = None
            
            # Get workspace ID
            if get_workspace_id:
                workspace_id = get_workspace_id()
            elif 'workspace_id' in kwargs:
                workspace_id = kwargs['workspace_id']
            
            # Get resource info
            if get_resource:
                resource_type, resource_id = get_resource()
            
            # Check permission
            service = get_permission_service()
            if not service.check_access(user, action, resource_type, resource_id, workspace_id, module):
                abort(403)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_system_role(role_code: str):
    """
    Decorator to require a system role.
    
    Usage:
        @require_system_role('SYSTEM_ADMIN')
        def admin_only():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from flask import g, abort
            
            user = getattr(g, 'user', None)
            service = get_permission_service()
            
            if role_code == 'SYSTEM_ADMIN' and not service.is_system_admin(user):
                abort(403)
            elif role_code == 'GLOBAL_WORKSPACE_MANAGER' and not service.is_global_workspace_manager(user):
                abort(403)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_workspace_role(min_role: str = 'member'):
    """
    Decorator to require minimum workspace role.
    
    Usage:
        @require_workspace_role('admin')
        def workspace_admin_action(workspace_id):
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from flask import g, abort
            
            user = getattr(g, 'user', None)
            workspace_id = kwargs.get('workspace_id')
            
            if not workspace_id:
                abort(400)
            
            service = get_permission_service()
            
            if min_role in ('owner', 'admin', 'WORKSPACE_ADMIN'):
                if not service.is_workspace_admin(user, workspace_id):
                    abort(403)
            elif min_role in ('moderator', 'MODERATOR'):
                if not service.is_workspace_moderator(user, workspace_id):
                    abort(403)
            else:
                if not service.is_workspace_member(user, workspace_id):
                    abort(403)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
