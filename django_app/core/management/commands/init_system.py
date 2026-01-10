"""
Initialize the system with default roles and admin user
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from core.models import User
from workspaces.models import WorkspaceRole


class Command(BaseCommand):
    help = 'Initialize system with default roles and admin user'

    def handle(self, *args, **options):
        self.stdout.write('Initializing system...')
        
        # Create default roles
        self.create_default_roles()
        
        # Create system admin user
        self.create_admin_user()
        
        self.stdout.write(self.style.SUCCESS('System initialized successfully!'))

    def create_default_roles(self):
        """Create default workspace roles"""
        roles = [
            {
                'name': 'admin',
                'display_name': 'Administrator',
                'description': 'Full access to all workspace features',
                'hierarchy_level': 1,
                'is_system_role': True,
                'can_manage_workspace': True,
                'can_manage_users': True,
                'can_manage_roles': True,
                'can_manage_connections': True,
                'can_view_all_leads': True,
                'can_create_leads': True,
                'can_edit_leads': True,
                'can_delete_leads': True,
                'can_assign_leads': True,
                'can_view_all_listings': True,
                'can_create_listings': True,
                'can_edit_listings': True,
                'can_delete_listings': True,
                'can_publish_listings': True,
                'can_view_analytics': True,
                'can_export_data': True,
            },
            {
                'name': 'manager',
                'display_name': 'Manager',
                'description': 'Can manage team members and oversee all data',
                'hierarchy_level': 10,
                'is_system_role': True,
                'can_manage_workspace': False,
                'can_manage_users': True,
                'can_manage_roles': False,
                'can_manage_connections': False,
                'can_view_all_leads': True,
                'can_create_leads': True,
                'can_edit_leads': True,
                'can_delete_leads': True,
                'can_assign_leads': True,
                'can_view_all_listings': True,
                'can_create_listings': True,
                'can_edit_listings': True,
                'can_delete_listings': True,
                'can_publish_listings': True,
                'can_view_analytics': True,
                'can_export_data': True,
            },
            {
                'name': 'agent',
                'display_name': 'Agent',
                'description': 'Standard agent with access to own leads and listings',
                'hierarchy_level': 50,
                'is_system_role': True,
                'can_manage_workspace': False,
                'can_manage_users': False,
                'can_manage_roles': False,
                'can_manage_connections': False,
                'can_view_all_leads': False,
                'can_create_leads': True,
                'can_edit_leads': True,
                'can_delete_leads': False,
                'can_assign_leads': False,
                'can_view_all_listings': False,
                'can_create_listings': True,
                'can_edit_listings': True,
                'can_delete_listings': False,
                'can_publish_listings': True,
                'can_view_analytics': False,
                'can_export_data': False,
            },
            {
                'name': 'viewer',
                'display_name': 'Viewer',
                'description': 'Read-only access to workspace data',
                'hierarchy_level': 100,
                'is_system_role': True,
                'can_manage_workspace': False,
                'can_manage_users': False,
                'can_manage_roles': False,
                'can_manage_connections': False,
                'can_view_all_leads': True,
                'can_create_leads': False,
                'can_edit_leads': False,
                'can_delete_leads': False,
                'can_assign_leads': False,
                'can_view_all_listings': True,
                'can_create_listings': False,
                'can_edit_listings': False,
                'can_delete_listings': False,
                'can_publish_listings': False,
                'can_view_analytics': True,
                'can_export_data': False,
            },
        ]
        
        for role_data in roles:
            role, created = WorkspaceRole.objects.update_or_create(
                name=role_data['name'],
                defaults=role_data
            )
            status = 'Created' if created else 'Updated'
            self.stdout.write(f'  {status} role: {role.display_name}')

    def create_admin_user(self):
        """Create system admin user from environment settings"""
        email = getattr(settings, 'SYSTEM_ADMIN_EMAIL', None)
        password = getattr(settings, 'SYSTEM_ADMIN_PASSWORD', None)
        
        if not email or not password:
            self.stdout.write(self.style.WARNING(
                '  Skipping admin user creation: SYSTEM_ADMIN_EMAIL or SYSTEM_ADMIN_PASSWORD not set'
            ))
            return
        
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'is_system_admin': True,
                'is_staff': True,
                'is_superuser': True,
                'first_name': 'System',
                'last_name': 'Admin',
            }
        )
        
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(f'  Created system admin user: {email}')
        else:
            self.stdout.write(f'  System admin user already exists: {email}')
