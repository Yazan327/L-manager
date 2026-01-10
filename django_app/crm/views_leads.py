"""
CRM - Leads views
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone

from workspaces.views import workspace_required, permission_required
from workspaces.models import WorkspaceMember
from .models import Lead, LeadComment
from core.models import User


@workspace_required
def lead_list(request, workspace_slug):
    """List leads with filtering and pagination"""
    leads = Lead.objects.filter(
        workspace=request.workspace
    ).select_related('assigned_to', 'listing')
    
    # Filters
    status = request.GET.get('status', '')
    priority = request.GET.get('priority', '')
    assigned = request.GET.get('assigned', '')
    search = request.GET.get('search', '')
    
    if status:
        leads = leads.filter(status=status)
    if priority:
        leads = leads.filter(priority=priority)
    if assigned == 'me':
        leads = leads.filter(assigned_to=request.user)
    elif assigned == 'unassigned':
        leads = leads.filter(assigned_to__isnull=True)
    elif assigned:
        leads = leads.filter(assigned_to_id=assigned)
    
    if search:
        leads = leads.filter(
            Q(name__icontains=search) |
            Q(email__icontains=search) |
            Q(phone__icontains=search) |
            Q(message__icontains=search)
        )
    
    leads = leads.order_by('-received_at')
    
    # Pagination
    paginator = Paginator(leads, 25)
    page = request.GET.get('page', 1)
    leads = paginator.get_page(page)
    
    # For filter dropdowns
    members = WorkspaceMember.objects.filter(
        workspace=request.workspace,
        is_active=True
    ).select_related('user')
    
    return render(request, 'crm/leads/list.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'leads': leads,
        'members': members,
        'status_choices': Lead.STATUS_CHOICES,
        'priority_choices': Lead.PRIORITY_CHOICES,
        'current_filters': {
            'status': status,
            'priority': priority,
            'assigned': assigned,
            'search': search,
        }
    })


@workspace_required
@permission_required('can_create_leads')
def lead_create(request, workspace_slug):
    """Create a new lead"""
    if request.method == 'POST':
        lead = Lead.objects.create(
            workspace=request.workspace,
            name=request.POST.get('name', '').strip(),
            email=request.POST.get('email', '').strip(),
            phone=request.POST.get('phone', '').strip(),
            message=request.POST.get('message', '').strip(),
            source='manual',
            status=request.POST.get('status', 'new'),
            priority=request.POST.get('priority', 'medium'),
        )
        
        assigned_to_id = request.POST.get('assigned_to')
        if assigned_to_id:
            lead.assigned_to_id = assigned_to_id
            lead.save()
        
        messages.success(request, f'Lead "{lead.name}" created')
        return redirect('workspaces:leads:detail', workspace_slug=workspace_slug, lead_id=lead.id)
    
    members = WorkspaceMember.objects.filter(
        workspace=request.workspace,
        is_active=True
    ).select_related('user')
    
    return render(request, 'crm/leads/form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'members': members,
        'status_choices': Lead.STATUS_CHOICES,
        'priority_choices': Lead.PRIORITY_CHOICES,
        'is_create': True,
    })


@workspace_required
def lead_detail(request, workspace_slug, lead_id):
    """View lead details"""
    lead = get_object_or_404(
        Lead.objects.select_related('assigned_to', 'listing'),
        id=lead_id,
        workspace=request.workspace
    )
    
    comments = LeadComment.objects.filter(
        lead=lead
    ).select_related('author').order_by('-created_at')
    
    # Members for assignment dropdown
    members = WorkspaceMember.objects.filter(
        workspace=request.workspace,
        is_active=True
    ).select_related('user')
    
    return render(request, 'crm/leads/detail.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'lead': lead,
        'comments': comments,
        'members': members,
        'status_choices': Lead.STATUS_CHOICES,
        'priority_choices': Lead.PRIORITY_CHOICES,
    })


@workspace_required
@permission_required('can_edit_leads')
def lead_edit(request, workspace_slug, lead_id):
    """Edit lead"""
    lead = get_object_or_404(Lead, id=lead_id, workspace=request.workspace)
    
    if request.method == 'POST':
        lead.name = request.POST.get('name', lead.name).strip()
        lead.email = request.POST.get('email', '').strip()
        lead.phone = request.POST.get('phone', '').strip()
        lead.message = request.POST.get('message', '').strip()
        lead.status = request.POST.get('status', lead.status)
        lead.priority = request.POST.get('priority', lead.priority)
        
        assigned_to_id = request.POST.get('assigned_to')
        if assigned_to_id:
            lead.assigned_to_id = assigned_to_id
        else:
            lead.assigned_to = None
        
        lead.save()
        
        messages.success(request, 'Lead updated')
        return redirect('workspaces:leads:detail', workspace_slug=workspace_slug, lead_id=lead.id)
    
    members = WorkspaceMember.objects.filter(
        workspace=request.workspace,
        is_active=True
    ).select_related('user')
    
    return render(request, 'crm/leads/form.html', {
        'workspace': request.workspace,
        'membership': request.membership,
        'lead': lead,
        'members': members,
        'status_choices': Lead.STATUS_CHOICES,
        'priority_choices': Lead.PRIORITY_CHOICES,
        'is_create': False,
    })


@workspace_required
@permission_required('can_delete_leads')
def lead_delete(request, workspace_slug, lead_id):
    """Delete lead"""
    lead = get_object_or_404(Lead, id=lead_id, workspace=request.workspace)
    
    if request.method == 'POST':
        name = lead.name
        lead.delete()
        messages.success(request, f'Lead "{name}" deleted')
        return redirect('workspaces:leads:list', workspace_slug=workspace_slug)
    
    return render(request, 'crm/leads/delete_confirm.html', {
        'workspace': request.workspace,
        'lead': lead,
    })


@workspace_required
@permission_required('can_assign_leads')
@require_POST
def lead_assign(request, workspace_slug, lead_id):
    """Assign lead to a user"""
    lead = get_object_or_404(Lead, id=lead_id, workspace=request.workspace)
    
    assigned_to_id = request.POST.get('assigned_to')
    
    if assigned_to_id:
        user = get_object_or_404(User, id=assigned_to_id)
        lead.assigned_to = user
        
        # Add system comment
        LeadComment.objects.create(
            lead=lead,
            author=request.user,
            content=f'Assigned to {user.full_name}',
            comment_type='status_change'
        )
    else:
        lead.assigned_to = None
    
    lead.save()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    
    messages.success(request, 'Lead assigned')
    return redirect('workspaces:leads:detail', workspace_slug=workspace_slug, lead_id=lead.id)


@workspace_required
@require_POST
def lead_add_comment(request, workspace_slug, lead_id):
    """Add comment to lead"""
    lead = get_object_or_404(Lead, id=lead_id, workspace=request.workspace)
    
    content = request.POST.get('content', '').strip()
    comment_type = request.POST.get('comment_type', 'note')
    
    if content:
        LeadComment.objects.create(
            lead=lead,
            author=request.user,
            content=content,
            comment_type=comment_type
        )
        
        # Update last contact time
        if comment_type in ['call', 'email', 'meeting']:
            lead.last_contact_at = timezone.now()
            if not lead.first_contact_at:
                lead.first_contact_at = timezone.now()
            lead.save()
        
        messages.success(request, 'Comment added')
    
    return redirect('workspaces:leads:detail', workspace_slug=workspace_slug, lead_id=lead.id)


@workspace_required
@permission_required('can_edit_leads')
@require_POST
def lead_update_status(request, workspace_slug, lead_id):
    """Update lead status"""
    lead = get_object_or_404(Lead, id=lead_id, workspace=request.workspace)
    
    old_status = lead.status
    new_status = request.POST.get('status')
    
    if new_status and new_status != old_status:
        lead.status = new_status
        
        # Set closed_at for final statuses
        if new_status in ['won', 'lost', 'archived']:
            lead.closed_at = timezone.now()
        else:
            lead.closed_at = None
        
        lead.save()
        
        # Add system comment
        LeadComment.objects.create(
            lead=lead,
            author=request.user,
            content=f'Status changed from {old_status} to {new_status}',
            comment_type='status_change'
        )
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'status': lead.status})
    
    messages.success(request, 'Status updated')
    return redirect('workspaces:leads:detail', workspace_slug=workspace_slug, lead_id=lead.id)


@workspace_required
@permission_required('can_manage_connections')
@require_POST
def api_sync_leads(request, workspace_slug):
    """Sync leads from PropertyFinder API"""
    # TODO: Implement PropertyFinder API sync
    # This would fetch leads from the configured connection
    
    return JsonResponse({
        'success': True,
        'message': 'Lead sync initiated',
        'synced_count': 0
    })
