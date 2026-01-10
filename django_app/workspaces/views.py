"""
Workspace views - Dashboard, settings, and member management
"""
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from django.utils import timezone
from django.http import JsonResponse
import uuid

from .models import Workspace, WorkspaceMember, WorkspaceInvitation, WorkspaceConnection, WorkspaceRole
from core.models import User


def workspace_required(view_func):
    """Decorator to load workspace and check membership"""
    @wraps(view_func)
    @login_required
    def _wrapped_view(request, workspace_slug, *args, **kwargs):
        workspace = get_object_or_404(Workspace, slug=workspace_slug, is_active=True)
        
        # Check membership
        try:
            membership = WorkspaceMember.objects.select_related('role').get(
                workspace=workspace,
                user=request.user,
                is_active=True
            )
        except WorkspaceMember.DoesNotExist:
            messages.error(request, 'You are not a member of this workspace')
            return redirect('core:workspace_list')
        
        # Add to request for easy access
        request.workspace = workspace
        request.membership = membership
        
        return view_func(request, workspace_slug, *args, **kwargs)
    return _wrapped_view


def permission_required(permission):
    """Decorator to check workspace permission"""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not hasattr(request, 'membership'):
                messages.error(request, 'Permission denied')
                return redirect('core:workspace_list')
            
            if not request.membership.has_permission(permission):
                messages.error(request, 'You do not have permission to perform this action')
                return redirect('workspaces:dashboard', workspace_slug=request.workspace.slug)
            
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


@workspace_required
def dashboard(request, workspace_slug):
    """Workspace dashboard with overview"""
    from crm.models import Lead
    from listings.models import Listing
    
    # Stats
    stats = {
        'total_leads': Lead.objects.filter(workspace=request.workspace).count(),
        'open_leads': Lead.objects.filter(workspace=request.workspace, status__in=['new', 'contacted', 'qualified']).count(),
        'total_listings': Listing.objects.filter(workspace=request.workspace).count(),
        'active_listings': Listing.objects.filter(workspace=request.workspace, status='active').count(),
        'members': request.workspace.members.filter(is_active=True).count(),
    }
    
    # Recent leads
    recent_leads = Lead.objects.filter(
        workspace=request.workspace
    ).select_related('assigned_to').order_by('-received_at')[:5]
    
    # Recent listings
    recent_listings = Listing.objects.filter(
        workspace=request.workspace
    ).order_by('-created_at')[:5]
    
    return render(request, 'workspaces/dashboard.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'stats': stats,
        'recent_leads': recent_leads,
        'recent_listings': recent_listings,
    })


@workspace_required
@permission_required('can_manage_workspace')
def settings(request, workspace_slug):
    """Workspace settings"""
    if request.method == 'POST':
        request.workspace.name = request.POST.get('name', request.workspace.name).strip()
        request.workspace.description = request.POST.get('description', '').strip()
        
        if 'logo' in request.FILES:
            request.workspace.logo = request.FILES['logo']
        
        request.workspace.save()
        messages.success(request, 'Workspace settings updated')
        return redirect('workspaces:settings', workspace_slug=workspace_slug)
    
    return render(request, 'workspaces/settings.html', {
        'workspace': request.workspace,
        'membership': request.membership,
    })


@workspace_required
@permission_required('can_manage_users')
def member_list(request, workspace_slug):
    """List workspace members"""
    members = WorkspaceMember.objects.filter(
        workspace=request.workspace
    ).select_related('user', 'role', 'invited_by').order_by('joined_at')
    
    pending_invitations = WorkspaceInvitation.objects.filter(
        workspace=request.workspace,
        status='pending'
    ).select_related('role', 'invited_by')
    
    roles = WorkspaceRole.objects.all()
    
    return render(request, 'workspaces/members.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'members': members,
        'pending_invitations': pending_invitations,
        'roles': roles,
    })


@workspace_required
@permission_required('can_manage_users')
def member_invite(request, workspace_slug):
    """Invite a new member"""
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        role_id = request.POST.get('role_id')
        message = request.POST.get('message', '').strip()
        
        role = get_object_or_404(WorkspaceRole, id=role_id)
        
        # Check if already a member
        if WorkspaceMember.objects.filter(
            workspace=request.workspace,
            user__email=email
        ).exists():
            messages.error(request, 'This user is already a member')
        # Check for pending invitation
        elif WorkspaceInvitation.objects.filter(
            workspace=request.workspace,
            email=email,
            status='pending'
        ).exists():
            messages.error(request, 'An invitation is already pending for this email')
        else:
            # Create invitation
            invitation = WorkspaceInvitation.objects.create(
                workspace=request.workspace,
                email=email,
                role=role,
                invited_by=request.user,
                token=str(uuid.uuid4()),
                message=message,
                expires_at=timezone.now() + timezone.timedelta(days=7)
            )
            
            # TODO: Send invitation email
            
            messages.success(request, f'Invitation sent to {email}')
        
        return redirect('workspaces:members', workspace_slug=workspace_slug)
    
    roles = WorkspaceRole.objects.all()
    
    return render(request, 'workspaces/member_invite.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'roles': roles,
    })


@workspace_required
@permission_required('can_manage_users')
def member_edit(request, workspace_slug, member_id):
    """Edit member role"""
    member = get_object_or_404(
        WorkspaceMember,
        id=member_id,
        workspace=request.workspace
    )
    
    if request.method == 'POST':
        role_id = request.POST.get('role_id')
        role = get_object_or_404(WorkspaceRole, id=role_id)
        
        member.role = role
        member.save()
        
        messages.success(request, f'Updated role for {member.user.email}')
        return redirect('workspaces:members', workspace_slug=workspace_slug)
    
    roles = WorkspaceRole.objects.all()
    
    return render(request, 'workspaces/member_edit.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'member': member,
        'roles': roles,
    })


@workspace_required
@permission_required('can_manage_users')
def member_remove(request, workspace_slug, member_id):
    """Remove member from workspace"""
    member = get_object_or_404(
        WorkspaceMember,
        id=member_id,
        workspace=request.workspace
    )
    
    # Can't remove workspace owner
    if member.user == request.workspace.owner:
        messages.error(request, 'Cannot remove workspace owner')
        return redirect('workspaces:members', workspace_slug=workspace_slug)
    
    if request.method == 'POST':
        email = member.user.email
        member.delete()
        messages.success(request, f'Removed {email} from workspace')
        return redirect('workspaces:members', workspace_slug=workspace_slug)
    
    return render(request, 'workspaces/member_remove_confirm.html', {
        'workspace': request.workspace,
        'member': member,
    })


@workspace_required
@permission_required('can_manage_connections')
def connection_list(request, workspace_slug):
    """List API connections"""
    connections = WorkspaceConnection.objects.filter(
        workspace=request.workspace
    ).order_by('-created_at')
    
    return render(request, 'workspaces/connections.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'connections': connections,
    })


@workspace_required
@permission_required('can_manage_connections')
def connection_add(request, workspace_slug):
    """Add new API connection"""
    if request.method == 'POST':
        provider = request.POST.get('provider')
        name = request.POST.get('name', '').strip()
        api_key = request.POST.get('api_key', '').strip()
        api_secret = request.POST.get('api_secret', '').strip()
        
        connection = WorkspaceConnection.objects.create(
            workspace=request.workspace,
            provider=provider,
            name=name,
            api_key=api_key,
            api_secret=api_secret,
        )
        
        messages.success(request, f'Connection "{name}" added')
        return redirect('workspaces:connections', workspace_slug=workspace_slug)
    
    return render(request, 'workspaces/connection_form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'is_create': True,
        'provider_choices': WorkspaceConnection.PROVIDER_CHOICES,
    })


@workspace_required
@permission_required('can_manage_connections')
def connection_edit(request, workspace_slug, connection_id):
    """Edit API connection"""
    connection = get_object_or_404(
        WorkspaceConnection,
        id=connection_id,
        workspace=request.workspace
    )
    
    if request.method == 'POST':
        connection.name = request.POST.get('name', connection.name).strip()
        connection.api_key = request.POST.get('api_key', connection.api_key).strip()
        connection.api_secret = request.POST.get('api_secret', connection.api_secret).strip()
        connection.is_active = request.POST.get('is_active') == 'on'
        connection.save()
        
        messages.success(request, 'Connection updated')
        return redirect('workspaces:connections', workspace_slug=workspace_slug)
    
    return render(request, 'workspaces/connection_form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'connection': connection,
        'is_create': False,
    })


@workspace_required
@permission_required('can_manage_connections')
def connection_test(request, workspace_slug, connection_id):
    """Test API connection"""
    connection = get_object_or_404(
        WorkspaceConnection,
        id=connection_id,
        workspace=request.workspace
    )
    
    # TODO: Implement actual API test based on provider
    result = {
        'success': True,
        'message': 'Connection test successful',
    }
    
    return JsonResponse(result)


@workspace_required
@permission_required('can_view_analytics')
def analytics(request, workspace_slug):
    """Workspace analytics dashboard"""
    from crm.models import Lead
    from listings.models import Listing
    from django.db.models.functions import TruncDate
    
    # Lead stats by status
    lead_by_status = Lead.objects.filter(
        workspace=request.workspace
    ).values('status').annotate(count=Count('id'))
    
    # Listing stats by status
    listing_by_status = Listing.objects.filter(
        workspace=request.workspace
    ).values('status').annotate(count=Count('id'))
    
    # Leads over time (last 30 days)
    thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
    leads_over_time = Lead.objects.filter(
        workspace=request.workspace,
        received_at__gte=thirty_days_ago
    ).annotate(
        date=TruncDate('received_at')
    ).values('date').annotate(count=Count('id')).order_by('date')
    
    return render(request, 'workspaces/analytics.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'lead_by_status': list(lead_by_status),
        'listing_by_status': list(listing_by_status),
        'leads_over_time': list(leads_over_time),
    })
