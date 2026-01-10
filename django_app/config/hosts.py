"""
Host configuration for django-hosts
Enables subdomain routing for admin portal
"""
from django_hosts import patterns, host
from django.conf import settings

host_patterns = patterns(
    '',
    # Admin portal subdomain: admin.l-manager.up.railway.app
    host(r'admin', 'config.urls_admin', name='admin'),
    
    # Main application: l-manager.up.railway.app or *.l-manager.up.railway.app (workspace subdomains)
    host(r'www', 'config.urls', name='www'),
    
    # Workspace subdomains: {workspace-slug}.l-manager.up.railway.app
    host(r'(?P<workspace_slug>[\w-]+)', 'config.urls_workspace', name='workspace'),
)
