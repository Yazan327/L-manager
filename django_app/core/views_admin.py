"""
Admin Portal views - System administration interface
"""
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.utils import timezone
from django.http import HttpResponseForbidden

from .models import User, AuditLog, SystemSetting, FeatureFlag
from workspaces.models import Workspace, WorkspaceMember, WorkspaceConnection, WorkspaceRole


def admin_required(view_func):
    """Decorator to require admin portal authentication"""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        # Check if user is authenticated via admin session
        if not request.session.get('admin_authenticated'):
            return redirect('admin_portal:login')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def login_view(request):
    """Admin portal login (separate from workspace login)"""
    if request.session.get('admin_authenticated'):
        return redirect('admin_portal:dashboard')
    
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        
        # Check against system admin credentials from settings
        if email == settings.SYSTEM_ADMIN_EMAIL and password == settings.SYSTEM_ADMIN_PASSWORD:
            request.session['admin_authenticated'] = True
            request.session['admin_email'] = email
            request.session.set_expiry(3600 * 4)  # 4 hour session
            return redirect('admin_portal:dashboard')
        
        # Also check if user is a system admin in database
        user = authenticate(request, username=email, password=password)
        if user and user.is_system_admin:
            request.session['admin_authenticated'] = True
            request.session['admin_email'] = email
            request.session['admin_user_id'] = user.id
            request.session.set_expiry(3600 * 4)
            return redirect('admin_portal:dashboard')
        
        messages.error(request, 'Invalid admin credentials')
    
    return render(request, 'admin_portal/login.html')


def logout_view(request):
    """Admin portal logout"""
    request.session.pop('admin_authenticated', None)
    request.session.pop('admin_email', None)
    request.session.pop('admin_user_id', None)
    messages.success(request, 'Logged out of admin portal')
    return redirect('admin_portal:login')


@admin_required
def dashboard(request):
    """Admin portal dashboard with system overview"""
    # Statistics
    stats = {
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
        'total_workspaces': Workspace.objects.count(),
        'active_workspaces': Workspace.objects.filter(is_active=True).count(),
    }
    
    # Recent activity
    recent_audit_logs = AuditLog.objects.select_related('user', 'workspace')[:10]
    
    # Recent workspaces
    recent_workspaces = Workspace.objects.annotate(
        member_count=Count('members')
    ).order_by('-created_at')[:5]
    
    return render(request, 'admin_portal/dashboard.html', {
        'stats': stats,
        'recent_audit_logs': recent_audit_logs,
        'recent_workspaces': recent_workspaces,
    })


@admin_required
def workspace_list(request):
    """List all workspaces"""
    query = request.GET.get('q', '')
    status = request.GET.get('status', '')
    
    workspaces = Workspace.objects.annotate(
        member_count=Count('members'),
        listing_count=Count('listings'),
        lead_count=Count('leads'),
    ).select_related('owner')
    
    if query:
        workspaces = workspaces.filter(
            Q(name__icontains=query) | Q(slug__icontains=query)
        )
    
    if status == 'active':
        workspaces = workspaces.filter(is_active=True)
    elif status == 'inactive':
        workspaces = workspaces.filter(is_active=False)
    
    workspaces = workspaces.order_by('-created_at')
    
    paginator = Paginator(workspaces, 20)
    page = request.GET.get('page', 1)
    workspaces = paginator.get_page(page)
    
    return render(request, 'admin_portal/workspaces.html', {
        'workspaces': workspaces,
        'query': query,
        'status': status,
    })


@admin_required
def workspace_create(request):
    """Create a new workspace"""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = request.POST.get('slug', '').strip()
        owner_email = request.POST.get('owner_email', '').strip().lower()
        plan = request.POST.get('plan', 'free')
        
        # Validation
        owner = User.objects.filter(email=owner_email).first()
        if not owner:
            messages.error(request, 'Owner email not found')
        elif Workspace.objects.filter(slug=slug).exists():
            messages.error(request, 'A workspace with this slug already exists')
        else:
            workspace = Workspace.objects.create(
                name=name,
                slug=slug,
                owner=owner,
                plan=plan
            )
            
            # Get or create admin role and add owner
            admin_role, _ = WorkspaceRole.objects.get_or_create(
                name='admin',
                defaults={'display_name': 'Administrator', 'is_system_role': True}
            )
            WorkspaceMember.objects.create(
                workspace=workspace,
                user=owner,
                role=admin_role
            )
            
            messages.success(request, f'Workspace "{name}" created')
            return redirect('admin_portal:workspace_detail', workspace_id=workspace.id)
    
    return render(request, 'admin_portal/workspace_form.html', {
        'is_create': True
    })


@admin_required
def workspace_detail(request, workspace_id):
    """View workspace details"""
    workspace = get_object_or_404(
        Workspace.objects.annotate(
            member_count=Count('members'),
            listing_count=Count('listings'),
            lead_count=Count('leads'),
        ),
        id=workspace_id
    )
    
    members = WorkspaceMember.objects.filter(
        workspace=workspace
    ).select_related('user', 'role')
    
    connections = WorkspaceConnection.objects.filter(workspace=workspace)
    
    return render(request, 'admin_portal/workspace_detail.html', {
        'workspace': workspace,
        'members': members,
        'connections': connections,
    })


@admin_required
def workspace_edit(request, workspace_id):
    """Edit workspace"""
    workspace = get_object_or_404(Workspace, id=workspace_id)
    
    if request.method == 'POST':
        workspace.name = request.POST.get('name', workspace.name).strip()
        workspace.description = request.POST.get('description', '').strip()
        workspace.plan = request.POST.get('plan', workspace.plan)
        workspace.is_active = request.POST.get('is_active') == 'on'
        workspace.max_users = int(request.POST.get('max_users', 5))
        workspace.max_listings = int(request.POST.get('max_listings', 100))
        workspace.max_leads = int(request.POST.get('max_leads', 1000))
        workspace.save()
        
        messages.success(request, 'Workspace updated')
        return redirect('admin_portal:workspace_detail', workspace_id=workspace.id)
    
    return render(request, 'admin_portal/workspace_form.html', {
        'workspace': workspace,
        'is_create': False
    })


@admin_required
def workspace_delete(request, workspace_id):
    """Delete workspace"""
    workspace = get_object_or_404(Workspace, id=workspace_id)
    
    if request.method == 'POST':
        name = workspace.name
        workspace.delete()
        messages.success(request, f'Workspace "{name}" deleted')
        return redirect('admin_portal:workspaces')
    
    return render(request, 'admin_portal/workspace_delete_confirm.html', {
        'workspace': workspace
    })


@admin_required
def user_list(request):
    """List all users"""
    query = request.GET.get('q', '')
    
    users = User.objects.annotate(
        workspace_count=Count('workspace_memberships')
    )
    
    if query:
        users = users.filter(
            Q(email__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query)
        )
    
    users = users.order_by('-created_at')
    
    paginator = Paginator(users, 20)
    page = request.GET.get('page', 1)
    users = paginator.get_page(page)
    
    return render(request, 'admin_portal/users.html', {
        'users': users,
        'query': query,
    })


@admin_required
def user_create(request):
    """Create a new user"""
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        is_system_admin = request.POST.get('is_system_admin') == 'on'
        
        if User.objects.filter(email=email).exists():
            messages.error(request, 'A user with this email already exists')
        else:
            user = User.objects.create_user(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_system_admin=is_system_admin
            )
            messages.success(request, f'User "{email}" created')
            return redirect('admin_portal:user_detail', user_id=user.id)
    
    return render(request, 'admin_portal/user_form.html', {
        'is_create': True
    })


@admin_required
def user_detail(request, user_id):
    """View user details"""
    user = get_object_or_404(User, id=user_id)
    
    memberships = WorkspaceMember.objects.filter(
        user=user
    ).select_related('workspace', 'role')
    
    audit_logs = AuditLog.objects.filter(user=user)[:20]
    
    return render(request, 'admin_portal/user_detail.html', {
        'user_obj': user,
        'memberships': memberships,
        'audit_logs': audit_logs,
    })


@admin_required
def user_edit(request, user_id):
    """Edit user"""
    user = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        user.first_name = request.POST.get('first_name', '').strip()
        user.last_name = request.POST.get('last_name', '').strip()
        user.is_active = request.POST.get('is_active') == 'on'
        user.is_system_admin = request.POST.get('is_system_admin') == 'on'
        
        new_password = request.POST.get('password', '').strip()
        if new_password:
            user.set_password(new_password)
        
        user.save()
        messages.success(request, 'User updated')
        return redirect('admin_portal:user_detail', user_id=user.id)
    
    return render(request, 'admin_portal/user_form.html', {
        'user_obj': user,
        'is_create': False
    })


@admin_required
def system_settings(request):
    """System-wide settings"""
    settings_list = SystemSetting.objects.all()
    
    if request.method == 'POST':
        key = request.POST.get('key', '').strip()
        value = request.POST.get('value', '')
        description = request.POST.get('description', '').strip()
        
        if key:
            import json
            try:
                value_json = json.loads(value)
            except json.JSONDecodeError:
                value_json = value
            
            SystemSetting.objects.update_or_create(
                key=key,
                defaults={
                    'value': value_json,
                    'description': description,
                }
            )
            messages.success(request, f'Setting "{key}" saved')
    
    return render(request, 'admin_portal/settings.html', {
        'settings_list': settings_list,
    })


@admin_required
def feature_flags(request):
    """Feature flag management"""
    flags = FeatureFlag.objects.all()
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        is_enabled = request.POST.get('is_enabled') == 'on'
        rollout = int(request.POST.get('rollout_percentage', 100))
        
        if name:
            FeatureFlag.objects.update_or_create(
                name=name,
                defaults={
                    'is_enabled': is_enabled,
                    'rollout_percentage': rollout,
                }
            )
            messages.success(request, f'Feature flag "{name}" updated')
    
    return render(request, 'admin_portal/feature_flags.html', {
        'flags': flags,
    })


@admin_required
def audit_logs(request):
    """View audit logs"""
    logs = AuditLog.objects.select_related('user', 'workspace').order_by('-created_at')
    
    # Filters
    action = request.GET.get('action', '')
    entity_type = request.GET.get('entity_type', '')
    
    if action:
        logs = logs.filter(action=action)
    if entity_type:
        logs = logs.filter(entity_type=entity_type)
    
    paginator = Paginator(logs, 50)
    page = request.GET.get('page', 1)
    logs = paginator.get_page(page)
    
    return render(request, 'admin_portal/audit_logs.html', {
        'logs': logs,
        'action': action,
        'entity_type': entity_type,
    })


@admin_required
def connection_list(request):
    """List all API connections across workspaces"""
    connections = WorkspaceConnection.objects.select_related('workspace').order_by('-created_at')
    
    return render(request, 'admin_portal/connections.html', {
        'connections': connections,
    })


@admin_required
def system_health(request):
    """System health and statistics"""
    from django.db import connection
    
    # Database check
    db_status = 'healthy'
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception as e:
        db_status = f'error: {str(e)}'
    
    health = {
        'database': db_status,
        'timestamp': timezone.now(),
    }
    
    return render(request, 'admin_portal/health.html', {
        'health': health,
    })
